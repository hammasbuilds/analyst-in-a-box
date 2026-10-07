"""Read-only by construction: parse-tree validation plus a read-only connection.

The validator is adapted from sql-analyst-agent (MIT, (c) 2026 Muhammad Hammas),
src/sqlanalyst/sql/validate.py at commit 6ab8f80, changed to cover SQLite as well as PostgreSQL.
The layers, weakest first:

  1. the prompt (only used with a language model)
  2. validate(): sqlglot parses the statement and rejects anything that is not one SELECT
  3. the connection: SQLite is opened with mode=ro, PRAGMA query_only=ON and an authoriser that
     refuses every action except reads; PostgreSQL runs in a READ ONLY transaction.

Layers 2 and 3 are independent; the tests attack each of them separately.
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlglot import exp, parse

_FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Grant, exp.Merge, exp.Command, exp.Pragma, exp.Attach,
)
_FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "lo_import", "lo_export",
    "dblink", "dblink_exec", "pg_terminate_backend", "pg_cancel_backend", "set_config",
    "pg_reload_conf", "query_to_xml", "load_extension", "readfile", "writefile", "edit",
    "randomblob", "zeroblob",
}
_FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
MAX_SQL_CHARS = 20_000
MAX_CELL_CHARS = 5_000  # longer text cells are cut in results
MAX_RESULT_CHARS = 4_000_000  # total text a result may carry before it is cut short


@dataclass
class Validation:
    ok: bool
    reason: str = ""
    refused: bool = False
    rewritten: str = ""
    tables_touched: list[str] = field(default_factory=list)


def _tables(tree: exp.Expression) -> set[str]:
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    found = set()
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if name and name not in cte_names:
            db = (table.db or "").lower()
            found.add(f"{db}.{name}" if db else name)
    return found


def validate(
    sql: str, *, max_rows: int, known_tables: set[str] | None = None, dialect: str = "sqlite"
) -> Validation:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return Validation(ok=False, reason="empty statement")
    if len(sql) > MAX_SQL_CHARS:
        return Validation(ok=False, reason=f"statement is longer than {MAX_SQL_CHARS:,} characters")
    try:
        statements = [s for s in parse(sql, read=dialect) if s is not None]
    except Exception as exc:  # ParseError, or TokenError for e.g. an unterminated string
        return Validation(ok=False, reason=f"could not parse as {dialect}: {exc}")
    if len(statements) != 1:
        return Validation(
            ok=False, refused=True, reason=f"expected exactly one statement, got {len(statements)}"
        )
    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.Union | exp.Subquery):
        return Validation(
            ok=False, refused=True, reason=f"only SELECT is permitted, got {type(tree).__name__}"
        )
    for node_type in _FORBIDDEN_NODES:
        if list(tree.find_all(node_type)):
            return Validation(
                ok=False, refused=True, reason=f"{node_type.__name__.upper()} is not permitted"
            )
    for fn in tree.find_all(exp.Anonymous):
        if (fn.name or "").lower() in _FORBIDDEN_FUNCTIONS:
            return Validation(
                ok=False, refused=True, reason=f"function {fn.name}() is not permitted"
            )
    referenced = _tables(tree)
    for name in referenced:
        schema = name.split(".")[0] if "." in name else ""
        base = name.split(".")[-1]
        if schema in _FORBIDDEN_SCHEMAS or base.startswith(("pg_", "sqlite_")):
            return Validation(
                ok=False, refused=True, reason=f"system catalog {name} is not permitted"
            )
    if known_tables is not None:
        unknown = {t.split(".")[-1] for t in referenced} - {t.lower() for t in known_tables}
        if unknown:
            return Validation(
                ok=False,
                reason=f"unknown table(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(known_tables))}",
            )
    existing = tree.args.get("limit")
    if existing is not None:
        try:
            if int(existing.expression.name) > max_rows:
                tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        except (AttributeError, ValueError):
            tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    else:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return Validation(
        ok=True, rewritten=tree.sql(dialect=dialect, pretty=True), tables_touched=sorted(referenced)
    )


# ---- execution -----------------------------------------------------------------------------

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


class QueryError(Exception):
    pass


def _authorizer(action: int, arg1: Any, arg2: Any, db: Any, trigger: Any) -> int:
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in _FORBIDDEN_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and str(arg1).lower().startswith("sqlite_"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def open_readonly(path: str | Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    return con


def _limit(con: sqlite3.Connection, name: str, value: int) -> None:
    attr = getattr(sqlite3, name, None)
    if attr is not None:
        con.setlimit(attr, value)


def _clean(v: Any, budget: list[int], max_cell: int) -> Any:
    """Make one result cell JSON-safe and bounded: no bytes, no NaN/Inf, no huge strings."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, bytes | bytearray | memoryview):
        return f"<{len(bytes(v)):,} byte blob>"
    if isinstance(v, str):
        if len(v) > max_cell:
            v = v[:max_cell] + f"... [{len(v) - max_cell:,} more characters]"
        budget[0] -= len(v)
    else:
        budget[0] -= 8
    return v


def run_sqlite(
    path: str | Path, sql: str, *, max_rows: int, timeout_s: float,
    max_cell: int = MAX_CELL_CHARS, max_chars: int = MAX_RESULT_CHARS,
) -> dict[str, Any]:
    """Run already-validated SQL on a read-only connection with an authoriser, a deadline and
    hard limits on string size (so printf('%.*c', 900000000, 'x') cannot eat the machine)."""
    con = open_readonly(path)
    try:
        _limit(con, "SQLITE_LIMIT_LENGTH", 1_000_000)
        _limit(con, "SQLITE_LIMIT_SQL_LENGTH", MAX_SQL_CHARS * 2)
        _limit(con, "SQLITE_LIMIT_EXPR_DEPTH", 200)
        _limit(con, "SQLITE_LIMIT_COMPOUND_SELECT", 20)
        _limit(con, "SQLITE_LIMIT_LIKE_PATTERN_LENGTH", 500)
        _limit(con, "SQLITE_LIMIT_FUNCTION_ARG", 16)
        _limit(con, "SQLITE_LIMIT_ATTACHED", 0)
        con.set_authorizer(_authorizer)
        deadline = time.monotonic() + timeout_s
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
        try:
            cur = con.execute(sql)
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.Error as exc:
            msg = "query timed out" if "interrupted" in str(exc) else str(exc)
            raise QueryError(msg) from exc
        cols = [d[0] for d in cur.description or []]
        truncated = len(rows) > max_rows
        budget, out = [max_chars], []
        for r in rows[:max_rows]:
            out.append([_clean(v, budget, max_cell) for v in r])
            if budget[0] < 0:
                truncated = True
                break
        return {"columns": cols, "rows": out, "truncated": truncated}
    finally:
        con.close()


def run_postgres(url: str, sql: str, *, max_rows: int, timeout_s: float) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:
        raise QueryError(
            "PostgreSQL support is optional: install it with `uv sync --extra postgres`"
        ) from exc
    try:
        with psycopg.connect(url, autocommit=False, connect_timeout=5) as con:
            con.read_only = True
            cur = con.cursor()
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
            cur.execute(sql)
            rows = cur.fetchmany(max_rows + 1)
            cols = [d.name for d in cur.description or []]
            budget = [MAX_RESULT_CHARS]
            return {
                "columns": cols,
                "rows": [[_clean(v, budget, MAX_CELL_CHARS) for v in r] for r in rows[:max_rows]],
                "truncated": len(rows) > max_rows,
            }
    except psycopg.Error as exc:
        raise QueryError(str(exc)) from exc
