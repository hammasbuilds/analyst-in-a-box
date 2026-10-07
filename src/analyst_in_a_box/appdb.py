"""The app's own SQLite file: settings, data sources, tickets, approvals, the audit chain, alert
state, credit limits and the executed-actions ledger. Business data is never written here, and the
app never writes to a business database except to build the canonical tables from an upload.
"""

from __future__ import annotations

import hashlib
import json
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
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, question TEXT, sql TEXT, mode TEXT, rows INTEGER,
    ok INTEGER, note TEXT);
"""

_lock = threading.RLock()


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
        con.commit()
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
        return int(cur.lastrowid or 0)


def verify_audit(con: sqlite3.Connection) -> dict[str, Any]:
    prev = GENESIS
    n = 0
    for r in con.execute("SELECT * FROM audit ORDER BY id"):
        n += 1
        expect = _digest(prev, r["at"], r["actor"], r["action"], r["ticket_id"], r["detail"])
        if r["prev_hash"] != prev or r["hash"] != expect:
            return {"ok": False, "entries": n, "first_bad_id": r["id"]}
        prev = r["hash"]
    return {"ok": True, "entries": n, "first_bad_id": None, "head": prev}
