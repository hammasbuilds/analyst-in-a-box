"""Sign-in by PIN, so "who is acting" is something the server knows rather than something the
browser claims.

Before this existed a drop-down chose the acting name and the request body repeated it, so one
person could raise a refund as Amna and approve it as Bilal (the four-eyes rule held only against
honest clients). Now every state-changing request carries a session cookie issued after a PIN was
checked, and the name on the ticket comes from the session, never from the body.

Limits, stated plainly: the people are the fixed list in workflows.USERS; PINs are short secrets
meant for one shared machine or a small office, not a replacement for single sign-on; anyone with
file access to the data folder can still read or edit the database directly.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import appdb, workflows

SESSION_HOURS = 12
PIN_LENGTH = 8
MIN_NEW_PIN = 6
MAX_FAILS = 5
LOCK_SECONDS = 60
_ITER = 200_000
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/I

_fails: dict[str, tuple[int, float]] = {}
_flock = threading.Lock()


class AuthError(Exception):
    """Not signed in, wrong PIN or locked out. Mapped to HTTP 401 / 429."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITER).hex()


def _new_pin() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(PIN_LENGTH))


def _store(con: sqlite3.Connection, name: str, pin: str) -> None:
    salt = secrets.token_hex(16)
    con.execute(
        "INSERT INTO credentials(name,salt,pin_hash,changed_at) VALUES(?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET salt=excluded.salt, pin_hash=excluded.pin_hash, "
        "changed_at=excluded.changed_at", (name, salt, _hash(pin, salt), appdb.now()))
    con.commit()
    con.execute("DELETE FROM sessions WHERE name=?", (name,))
    con.commit()


def ensure_credentials(con: sqlite3.Connection, pins_file: Path | None) -> dict[str, str]:
    """Give every person without a PIN a random one. The new PINs are written to `pins_file`
    (one line each) for the owner to hand out and delete; only salted hashes stay in the database."""
    have = {r[0] for r in con.execute("SELECT name FROM credentials")}
    fresh = {}
    for u in workflows.USERS:
        if u["name"] not in have:
            fresh[u["name"]] = _new_pin()
            _store(con, u["name"], fresh[u["name"]])
    if fresh and pins_file is not None:
        lines = [f"{n}\t{p}" for n, p in fresh.items()]
        header = ("# Analyst-in-a-Box sign-in PINs. Give each person their own line, then delete "
                  "this file. Lost a PIN? Run: analyst-in-a-box --reset-pin \"Name\"\n")
        with pins_file.open("a", encoding="utf-8") as f:
            f.write(header + "\n".join(lines) + "\n")
    return fresh


def reset_pin(con: sqlite3.Connection, name: str) -> str:
    workflows.user_role(name)
    pin = _new_pin()
    _store(con, name, pin)
    appdb.audit(con, "system", "auth.pin_reset", None, {"user": name})
    return pin


def _check_lock(name: str) -> None:
    with _flock:
        n, until = _fails.get(name, (0, 0.0))
        if n >= MAX_FAILS and time.monotonic() < until:
            raise AuthError(f"too many wrong PINs; try again in {int(until - time.monotonic()) + 1} s", 429)


def login(con: sqlite3.Connection, name: str, pin: str) -> str:
    """Check the PIN and return a new session token (kept only as a hash on the server)."""
    _check_lock(name)
    row = con.execute("SELECT salt, pin_hash FROM credentials WHERE name=?", (name,)).fetchone()
    # hash even for unknown names so the response time does not reveal who exists
    salt = row["salt"] if row else "00" * 16
    good = hmac.compare_digest(_hash(pin or "", salt), row["pin_hash"] if row else "x")
    if not row or not good:
        with _flock:
            n, until = _fails.get(name, (0, 0.0))
            if time.monotonic() >= until:
                n = 0  # the last burst of failures has aged out
            _fails[name] = (n + 1, time.monotonic() + LOCK_SECONDS)
        if row:
            appdb.audit(con, name, "auth.failed", None, {})
        raise AuthError("wrong name or PIN")
    with _flock:
        _fails.pop(name, None)
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    con.execute("DELETE FROM sessions WHERE expires_at < ?", (now.strftime("%Y-%m-%d %H:%M:%S"),))
    con.execute(
        "INSERT INTO sessions(token_hash,name,created_at,expires_at) VALUES(?,?,?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), name, appdb.now(),
         (now + timedelta(hours=SESSION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    appdb.audit(con, name, "auth.login", None, {})
    return token


def user_for(con: sqlite3.Connection, token: str | None) -> str | None:
    if not token:
        return None
    row = con.execute(
        "SELECT name, expires_at FROM sessions WHERE token_hash=?",
        (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row or row["expires_at"] < appdb.now():
        return None
    return row["name"]


def logout(con: sqlite3.Connection, token: str | None) -> None:
    if token:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        con.commit()


def change_pin(con: sqlite3.Connection, name: str, old: str, new: str) -> None:
    row = con.execute("SELECT salt, pin_hash FROM credentials WHERE name=?", (name,)).fetchone()
    if not row or not hmac.compare_digest(_hash(old or "", row["salt"]), row["pin_hash"]):
        raise AuthError("the current PIN is wrong", 403)
    if len(new or "") < MIN_NEW_PIN or new.strip() != new:
        raise workflows.WorkflowError(f"a new PIN needs at least {MIN_NEW_PIN} characters, no spaces at the ends")
    _store(con, name, new)
    appdb.audit(con, name, "auth.pin_changed", None, {})
