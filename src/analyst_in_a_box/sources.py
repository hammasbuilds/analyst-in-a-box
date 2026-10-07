"""Data sources: the shipped sample, SQLite files, PostgreSQL URLs and uploaded CSV/Excel sheets."""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from . import appdb, canon, config, sqlsafe

BUILTIN_NAME = "Sample business: UK gift shop (UCI Online Retail II subset)"
UPLOAD_NAME = "My uploads"


class SourceError(ValueError):
    pass


def mask_url(url: str) -> str:
    return re.sub(r"(://[^:/@\s]+):[^@\s]+@", r"\1:***@", url)


def ensure_builtin(con: sqlite3.Connection) -> int:
    """Build the sample database on first run and register it. Returns its source id."""
    path = config.sample_db_path()
    if not path.exists():
        canon.build_sample(path)
    row = con.execute("SELECT id FROM sources WHERE kind='sample'").fetchone()
    if row:
        return int(row[0])
    cur = con.execute(
        "INSERT INTO sources(name,kind,location,created_at) VALUES(?,?,?,?)",
        (BUILTIN_NAME, "sample", str(path), appdb.now()),
    )
    con.commit()
    sid = int(cur.lastrowid or 0)
    if appdb.get_setting(con, "active_source") is None:
        appdb.set_setting(con, "active_source", str(sid))
    return sid


def list_sources(con: sqlite3.Connection) -> list[dict[str, Any]]:
    active = appdb.get_setting(con, "active_source")
    out = []
    for r in con.execute("SELECT * FROM sources ORDER BY id"):
        out.append(
            {
                "id": r["id"], "name": r["name"], "kind": r["kind"],
                "location": mask_url(r["location"]) if r["kind"] == "postgres" else r["location"],
                "created_at": r["created_at"], "active": str(r["id"]) == active,
            }
        )
    return out


def get_source(con: sqlite3.Connection, source_id: int | None = None) -> dict[str, Any]:
    if source_id is None:
        source_id = int(appdb.get_setting(con, "active_source") or 0)
    r = con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not r:
        raise SourceError("no such data source")
    return dict(r)


def _check_sqlite(path: str) -> None:
    p = Path(path)
    if not p.is_file():
        raise SourceError(f"file not found: {path}")
    try:
        c = sqlsafe.open_readonly(p)
        try:
            c.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        finally:
            c.close()
    except sqlite3.Error as exc:
        raise SourceError(f"not a SQLite database: {exc}") from exc


def add_source(con: sqlite3.Connection, kind: str, location: str, name: str | None) -> dict[str, Any]:
    location = (location or "").strip()
    if kind == "sqlite":
        _check_sqlite(location)
        mine = {config.app_db_path().resolve(), config.uploads_db_path().resolve()}
        if Path(location).resolve() in mine or Path(location).resolve() == Path(
                con.execute("PRAGMA database_list").fetchone()[2] or "-").resolve():
            raise SourceError(
                "that file is the app's own database (tickets, PINs, audit); it cannot be a data source")
        name = name or Path(location).name
    elif kind == "postgres":
        if not re.match(r"^postgres(ql)?://", location):
            raise SourceError("a PostgreSQL URL looks like postgresql://user:password@host:5432/db")
        try:
            sqlsafe.run_postgres(location, "SELECT 1", max_rows=1, timeout_s=5)
        except sqlsafe.QueryError as exc:
            raise SourceError(f"cannot connect: {exc}") from exc
        name = name or mask_url(location).split("@")[-1]
    else:
        raise SourceError("kind must be 'sqlite' or 'postgres'")
    cur = con.execute(
        "INSERT INTO sources(name,kind,location,created_at) VALUES(?,?,?,?)",
        (name, kind, location, appdb.now()),
    )
    con.commit()
    return get_source(con, int(cur.lastrowid or 0))


def activate(con: sqlite3.Connection, source_id: int) -> None:
    get_source(con, source_id)
    appdb.set_setting(con, "active_source", str(source_id))


def remove_source(con: sqlite3.Connection, source_id: int) -> None:
    src = get_source(con, source_id)
    if src["kind"] in ("sample", "upload"):
        raise SourceError("the sample and the uploads source cannot be removed")
    con.execute("DELETE FROM sources WHERE id=?", (source_id,))
    con.commit()
    if appdb.get_setting(con, "active_source") == str(source_id):
        appdb.set_setting(con, "active_source", str(ensure_builtin(con)))


def dialect(src: dict[str, Any]) -> str:
    return "postgres" if src["kind"] == "postgres" else "sqlite"


def is_sqlite(src: dict[str, Any]) -> bool:
    return src["kind"] != "postgres"


# ---- schema browser --------------------------------------------------------------------------


def _q(name: str) -> str:
    """A SQLite identifier, quoted, with any double quote doubled."""
    return '"' + str(name).replace('"', '""') + '"'


def _cell(v: Any) -> Any:
    if isinstance(v, bytes | bytearray):
        return f"<{len(v):,} byte blob>"
    if isinstance(v, float) and v != v:
        return None
    return v[:200] if isinstance(v, str) else v


def schema(src: dict[str, Any], *, sample_rows: int = 3) -> list[dict[str, Any]]:
    if src["kind"] == "postgres":
        res = sqlsafe.run_postgres(
            src["location"],
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY table_name, ordinal_position",
            max_rows=5000, timeout_s=10,
        )
        tables: dict[str, list[dict[str, str]]] = {}
        for t, c, ty in res["rows"]:
            tables.setdefault(t, []).append({"name": c, "type": ty})
        return [{"name": t, "rows": None, "columns": cols, "sample": []} for t, cols in tables.items()]
    con = sqlsafe.open_readonly(src["location"])
    try:
        out = []
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in names:
            q = _q(t)
            cols = [{"name": r[1], "type": r[2] or "TEXT"} for r in con.execute(f"PRAGMA table_info({q})")]
            n = con.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
            sample = [[_cell(v) for v in r]
                      for r in con.execute(f"SELECT * FROM {q} LIMIT {int(sample_rows)}")]
            out.append({"name": t, "rows": n, "columns": cols, "sample": sample})
        return out
    finally:
        con.close()


def ddl_for_prompt(tables: list[dict[str, Any]]) -> str:
    lines = []
    for t in tables:
        cols = ", ".join(f'{c["name"]} {c["type"]}' for c in t["columns"])
        lines.append(f'CREATE TABLE {t["name"]} ({cols});')
        for row in t["sample"][:2]:
            lines.append(f"-- example row: {row}")
    return "\n".join(lines)


def table_names(src: dict[str, Any]) -> set[str]:
    return {t["name"] for t in schema(src, sample_rows=0)}


def is_canonical(src: dict[str, Any]) -> bool:
    if not is_sqlite(src):
        return False
    con = sqlsafe.open_readonly(src["location"])
    try:
        return canon.has_canonical(con)
    finally:
        con.close()


def query(src: dict[str, Any], sql: str, *, max_rows: int | None = None,
          max_chars: int | None = None) -> dict[str, Any]:
    """Validate then run one read-only statement. Raises QueryError with a reason."""
    max_rows = max_rows or config.MAX_ROWS
    v = sqlsafe.validate(
        sql, max_rows=max_rows, known_tables=table_names(src), dialect=dialect(src)
    )
    if not v.ok:
        raise sqlsafe.QueryError(("refused: " if v.refused else "") + v.reason)
    if src["kind"] == "postgres":
        res = sqlsafe.run_postgres(
            src["location"], v.rewritten, max_rows=max_rows, timeout_s=config.QUERY_TIMEOUT_S
        )
    else:
        res = sqlsafe.run_sqlite(
            src["location"], v.rewritten, max_rows=max_rows, timeout_s=config.QUERY_TIMEOUT_S,
            **({"max_chars": max_chars} if max_chars else {}),
        )
    res["sql"] = v.rewritten
    res["tables"] = v.tables_touched
    return res


def rows_of(src: dict[str, Any], sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Internal analytics reads on a SQLite source (read-only connection, our own SQL)."""
    con = sqlsafe.open_readonly(src["location"])
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


# ---- uploads ---------------------------------------------------------------------------------


def _ident(name: str, taken: set[str], fallback: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name or "").strip()).strip("_").lower() or fallback
    if s[0].isdigit():
        s = f"c_{s}"
    base, i = s, 2
    while s in taken:
        s, i = f"{base}_{i}", i + 1
    taken.add(s)
    return s


_INT_RE = re.compile(r"[-+]?(0|[1-9]\d{0,18})")
_FLOAT_RE = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")
_I64 = 2**63 - 1


def _is_int(v: Any) -> bool:
    """A whole number that survives as an INTEGER: no leading zeros (postcodes, ids), within 64 bits."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return abs(v) <= _I64
    if isinstance(v, float):
        return v == v and abs(v) < 2**53 and v == int(v)
    s = str(v).strip().replace(",", "") if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", str(v).strip()) else str(v).strip()
    return bool(_INT_RE.fullmatch(s)) and abs(int(s)) <= _I64


def _is_float(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int | float):
        return v == v and abs(v) != float("inf")
    s = str(v).strip()
    s = s.replace(",", "") if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", s) else s
    if re.fullmatch(r"[-+]?0\d+", s.split(".")[0] if "." in s else s):  # 007, 00123.5
        return False
    if re.fullmatch(r"[-+]?\d{16,}", s):  # a long id, not a measurement: keep every digit
        return False
    return bool(_FLOAT_RE.fullmatch(s)) and abs(float(s)) != float("inf")


def _blank(v: Any) -> bool:
    return v in (None, "") or (isinstance(v, str) and v.strip().lower() == "nan") or (
        isinstance(v, float) and v != v)


def _infer(values: list[Any]) -> str:
    vals = [v for v in values if not _blank(v)]
    if not vals:
        return "TEXT"
    if all(isinstance(v, bool) for v in vals):
        return "INTEGER"
    if all(_is_int(v) for v in vals):
        return "INTEGER"
    if all(_is_float(v) for v in vals):
        return "REAL"
    return "TEXT"


def _coerce(v: Any, ty: str) -> Any:
    if _blank(v):
        return None
    if ty == "INTEGER":
        if isinstance(v, int | float):
            return int(v)
        return int(str(v).strip().replace(",", ""))
    if ty == "REAL":
        return float(str(v).replace(",", ""))
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M:%S") if getattr(v, "hour", None) is not None else str(v)
    return str(v)


def _read_csv(data: bytes) -> list[list[Any]]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dia = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dia = csv.excel
    rows: list[list[Any]] = []
    old = csv.field_size_limit(config.MAX_CELL_CHARS)
    try:
        for row in csv.reader(io.StringIO(text), dia):
            if any(c.strip() for c in row):
                rows.append(row)
                if len(rows) > config.MAX_UPLOAD_ROWS + 1:
                    raise SourceError(f"more than {config.MAX_UPLOAD_ROWS:,} rows; split the file")
    except csv.Error as exc:
        raise SourceError(
            f"cannot read the CSV: {exc} (a cell may be longer than "
            f"{config.MAX_CELL_CHARS:,} characters, or a quote is not closed)") from exc
    finally:
        csv.field_size_limit(old)
    return rows


def _check_zip(data: bytes) -> None:
    """Refuse a decompression bomb before openpyxl inflates it: a 40 MB workbook can hold gigabytes."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            total = sum(i.file_size for i in infos)
    except zipfile.BadZipFile as exc:
        raise SourceError("that is not a valid .xlsx file") from exc
    if len(infos) > 2000 or total > config.MAX_UNZIPPED_BYTES:
        raise SourceError(
            f"the workbook unpacks to {total // 1024 // 1024} MB; the limit is "
            f"{config.MAX_UNZIPPED_BYTES // 1024 // 1024} MB")


def _read_xlsx(data: bytes) -> dict[str, list[list[Any]]]:
    import openpyxl

    _check_zip(data)
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = {}
    try:
        if len(wb.worksheets) > 50:
            raise SourceError("more than 50 sheets in one workbook")
        for ws in wb.worksheets:
            rows: list[list[Any]] = []
            seen = 0
            for r in ws.iter_rows(values_only=True):
                seen += 1
                if seen > config.MAX_UPLOAD_ROWS * 2:
                    raise SourceError(f"sheet {ws.title!r} has more than {config.MAX_UPLOAD_ROWS:,} rows")
                if any(c not in (None, "") for c in r):
                    rows.append(list(r))
            if rows:
                out[ws.title] = rows
    finally:
        wb.close()
    return out


def _store(con: sqlite3.Connection, table: str, rows: list[list[Any]]) -> dict[str, Any]:
    if not rows:
        raise SourceError(f"{table}: no rows")
    header, body = rows[0], rows[1:]
    if not body:
        raise SourceError(f"{table}: a header and no data rows")
    if len(header) > config.MAX_UPLOAD_COLUMNS:
        raise SourceError(f"{table}: {len(header)} columns; the limit is {config.MAX_UPLOAD_COLUMNS}")
    taken: set[str] = set()
    cols = [_ident(h, taken, f"col{i + 1}") for i, h in enumerate(header)]
    width = len(cols)
    body = [(list(r) + [None] * width)[:width] for r in body]
    types = [_infer([r[i] for r in body]) for i in range(width)]
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute(f'CREATE TABLE "{table}" ({", ".join(f"{c} {t}" for c, t in zip(cols, types, strict=True))})')
    con.executemany(
        f'INSERT INTO "{table}" VALUES ({",".join("?" * width)})',
        [[_coerce(v, types[i]) for i, v in enumerate(r)] for r in body],
    )
    con.commit()
    return {"table": table, "rows": len(body), "columns": dict(zip(cols, types, strict=True))}


def import_upload(app_con: sqlite3.Connection, filename: str, data: bytes) -> dict[str, Any]:
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise SourceError(f"file is larger than {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    ext = Path(filename).suffix.lower()
    stem = Path(filename).stem
    if ext in (".csv", ".tsv", ".txt"):
        sheets = {stem: _read_csv(data)}
    elif ext in (".xlsx", ".xlsm"):
        try:
            sheets = _read_xlsx(data)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"cannot read the workbook: {exc}") from exc
    else:
        raise SourceError("upload a .csv, .tsv or .xlsx file")
    if not sheets:
        raise SourceError("the file has no data")
    path = config.uploads_db_path()
    con = sqlite3.connect(path)
    created = []
    try:
        for sheet, rows in sheets.items():
            label = stem if len(sheets) == 1 else f"{stem}_{sheet}"
            name = re.sub(r"[^0-9a-zA-Z]+", "_", label).strip("_").lower() or "sheet"
            if name[0].isdigit():
                name = f"t_{name}"
            if name in canon.CANON_TABLES:
                name += "_upload"
            try:
                created.append(_store(con, name, rows))
            except (sqlite3.Error, OverflowError, ValueError) as exc:
                con.rollback()
                raise SourceError(f"{name}: cannot store this sheet ({exc})") from exc
    finally:
        con.close()
    row = app_con.execute("SELECT id FROM sources WHERE kind='upload'").fetchone()
    if row:
        sid = int(row[0])
    else:
        cur = app_con.execute(
            "INSERT INTO sources(name,kind,location,created_at) VALUES(?,?,?,?)",
            (UPLOAD_NAME, "upload", str(path), appdb.now()),
        )
        app_con.commit()
        sid = int(cur.lastrowid or 0)
    appdb.set_setting(app_con, "active_source", str(sid))
    return {"source_id": sid, "tables": created}


SALES_FIELDS = {
    "invoice": "order / invoice number", "stock_code": "product code or name",
    "description": "product description (optional)", "quantity": "quantity",
    "date": "order date", "price": "unit price", "customer": "customer id or name",
    "country": "customer country (optional)",
}


def map_sales(src: dict[str, Any], table: str, mapping: dict[str, str]) -> dict[str, Any]:
    """Build the canonical tables (customers, products, orders, order_items, payments) inside an
    uploaded source from one flat table of order lines."""
    if src["kind"] != "upload":
        raise SourceError("column mapping works on uploaded sheets only")
    required = ["invoice", "stock_code", "quantity", "date", "price", "customer"]
    missing = [f for f in required if not mapping.get(f)]
    if missing:
        raise SourceError(f"map these fields: {', '.join(missing)}")
    con = sqlite3.connect(src["location"])
    con.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
        if not cols:
            raise SourceError(f"no such table: {table}")
        for f, c in mapping.items():
            if c and c not in cols:
                raise SourceError(f"{f}: column {c!r} is not in {table}")
        sel = ", ".join(f'"{mapping[f]}" AS {f}' if mapping.get(f) else f"NULL AS {f}"
                        for f in SALES_FIELDS)
        rows = [dict(r) for r in con.execute(f'SELECT {sel} FROM "{table}"')]
        if not mapping.get("description"):
            for r in rows:
                r["description"] = r["stock_code"]
        return canon.build_from_flat(con, rows)
    except ValueError as exc:
        raise SourceError(str(exc)) from exc
    finally:
        con.close()
