"""The app's own SQLite file: settings, data sources, tickets, approvals, the audit chain, alert
state, credit limits and the executed-actions ledger. Business data is never written here, and the
app never writes to a business database except to build the canonical tables from an upload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, location TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, customer_id TEXT,
    order_id TEXT, amount REAL, reason TEXT, status TEXT NOT NULL, requested_by TEXT NOT NULL,
    gates TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL, approver TEXT NOT NULL,
    role TEXT NOT NULL, decision TEXT NOT NULL, comment TEXT, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    ticket_id INTEGER, detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alert_state (
    alert_key TEXT PRIMARY KEY, status TEXT NOT NULL, ticket_id INTEGER, note TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS credit_limits (
    customer_id TEXT PRIMARY KEY, limit_gbp REAL NOT NULL, set_at TEXT NOT NULL, ticket_id INTEGER);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, customer_id TEXT, order_id TEXT, amount REAL,
    ticket_id INTEGER, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credentials (
    name TEXT PRIMARY KEY, salt TEXT NOT NULL, pin_hash TEXT NOT NULL, changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, question TEXT, sql TEXT, mode TEXT, rows INTEGER,
    ok INTEGER, note TEXT);
"""

LOCK = threading.RLock()
_lock = LOCK


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init(path: str | Path) -> None:
    con = connect(path)
    try:
        con.executescript(SCHEMA)
        # one approval per person per ticket and one ledger row per executed ticket, enforced by
        # the database as well as by the code (an older file with duplicates just skips them)
        for ddl in (
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_approvals_once ON approvals(ticket_id, approver) "
            "WHERE decision='approve'",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_ledger_ticket ON ledger(ticket_id) "
            "WHERE ticket_id IS NOT NULL",
        ):
            try:
                con.execute(ddl)
            except sqlite3.IntegrityError:
                pass
        con.commit()
        adopt_anchor(con)
    finally:
        con.close()


def get_setting(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


# ---- the audit chain -------------------------------------------------------------------------

GENESIS = "0" * 64


def _digest(prev: str, at: str, actor: str, action: str, ticket_id: int | None, detail: str) -> str:
    raw = json.dumps([prev, at, actor, action, ticket_id, detail], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def audit(
    con: sqlite3.Connection, actor: str, action: str, ticket_id: int | None, detail: dict[str, Any]
) -> int:
    """Append one entry. Each entry's hash covers the previous hash, so editing or deleting an
    old row breaks every hash after it (see verify_audit)."""
    with _lock:
        row = con.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        at = now()
        d = json.dumps(detail, sort_keys=True, ensure_ascii=False)
        h = _digest(prev, at, actor, action, ticket_id, d)
        cur = con.execute(
            "INSERT INTO audit(at,actor,action,ticket_id,detail,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
            (at, actor, action, ticket_id, d, prev, h),
        )
        con.commit()
        new_id = int(cur.lastrowid or 0)
        n = con.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        _write_anchor(con, n, new_id, h)
        return new_id


# ---- the anchor: a signed copy of the chain head kept OUTSIDE the database --------------------
# A hash chain alone cannot see its own tail being cut off, or the whole chain being recomputed
# after an edit. The head (count, last id, last hash) is also written to a side file next to the
# database, signed with a key kept in a second file; verify_audit compares the two.


def _db_file(con: sqlite3.Connection) -> Path | None:
    row = con.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


def _key(db: Path) -> bytes:
    kp = db.with_name(db.name + ".audit-key")
    if not kp.exists():
        tmp = kp.with_suffix(".tmp")
        tmp.write_text(secrets.token_hex(32))
        os.replace(tmp, kp)
    return bytes.fromhex(kp.read_text().strip())


def _mac(db: Path, count: int, last_id: int, h: str) -> str:
    return hmac.new(_key(db), f"{count}|{last_id}|{h}".encode(), "sha256").hexdigest()


def _write_anchor(con: sqlite3.Connection, count: int, last_id: int, h: str) -> None:
    db = _db_file(con)
    if db is None:
        return
    body = {"count": count, "last_id": last_id, "hash": h, "mac": _mac(db, count, last_id, h)}
    ap = db.with_name(db.name + ".audit-head")
    tmp = ap.with_suffix(".tmp")
    tmp.write_text(json.dumps(body))
    os.replace(tmp, ap)


def adopt_anchor(con: sqlite3.Connection) -> None:
    """Start anchoring a database that has audit rows but no anchor yet (an older install), but
    only if its chain verifies right now; otherwise leave it absent so verify reports it."""
    db = _db_file(con)
    if db is None or db.with_name(db.name + ".audit-head").exists():
        return
    res = _walk_chain(con)
    if res["ok"] and res["entries"]:
        last = con.execute("SELECT id, hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        _write_anchor(con, res["entries"], last[0], last[1])


def _walk_chain(con: sqlite3.Connection) -> dict[str, Any]:
    prev = GENESIS
    n = 0
    for r in con.execute("SELECT * FROM audit ORDER BY id"):
        n += 1
        expect = _digest(prev, r["at"], r["actor"], r["action"], r["ticket_id"], r["detail"])
        if r["prev_hash"] != prev or r["hash"] != expect:
            return {"ok": False, "entries": n, "first_bad_id": r["id"], "head": None}
        prev = r["hash"]
    return {"ok": True, "entries": n, "first_bad_id": None, "head": prev}


def verify_audit(con: sqlite3.Connection) -> dict[str, Any]:
    """Walk the chain, then compare its head with the signed anchor kept outside the database.
    `anchor` is one of ok, missing, forged, shortened, rewritten, or n/a (in-memory database)."""
    res = _walk_chain(con)
    res["anchor"] = "n/a"
    res["problems"] = []
    if not res["ok"]:
        res["problems"].append(f"the chain breaks at entry {res['first_bad_id']}")
    db = _db_file(con)
    if db is None:
        return res
    ap = db.with_name(db.name + ".audit-head")
    if not ap.exists():
        res["anchor"] = "missing" if res["entries"] else "ok"
        if res["entries"]:
            res["problems"].append("the signed head file is missing, so a cut-off tail cannot be ruled out")
    else:
        try:
            a = json.loads(ap.read_text())
            valid = hmac.compare_digest(
                str(a["mac"]), _mac(db, int(a["count"]), int(a["last_id"]), str(a["hash"])))
        except (ValueError, KeyError, TypeError, OSError):
            valid = False
        if not valid:
            res["anchor"] = "forged"
            res["problems"].append("the signed head file does not verify")
        else:
            row = con.execute("SELECT hash FROM audit WHERE id=?", (a["last_id"],)).fetchone()
            upto = con.execute("SELECT COUNT(*) FROM audit WHERE id<=?", (a["last_id"],)).fetchone()[0]
            if row is None or upto != a["count"]:
                res["anchor"] = "shortened"
                res["problems"].append(
                    f"the signed head says {a['count']} entries up to id {a['last_id']}; "
                    f"the database has {upto}")
            elif row[0] != a["hash"]:
                res["anchor"] = "rewritten"
                res["problems"].append(f"entry {a['last_id']} no longer has the hash that was signed")
            else:
                res["anchor"] = "ok"
    res["ok"] = res["ok"] and res["anchor"] in ("ok", "n/a")
    return res
