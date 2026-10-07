"""The HTTP API and the single-page app."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    appdb,
    auth,
    canon,
    config,
    dashboard,
    exporting,
    forecasting,
    fraud,
    llm,
    nlq,
    risk,
    sources,
    sqlsafe,
    workflows,
)

STATIC = Path(__file__).parent / "static"


class _RevalidatingStatic(StaticFiles):
    """Static files are always revalidated (ETag / Last-Modified), so a reload shows the newest UI."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


_ASSET_SUFFIXES = {".css", ".js", ".svg"}
_build_cache: dict[tuple[tuple[str, int, int], ...], str] = {}


def _build_id() -> str:
    """Short content hash over the CSS, JS and SVG assets: it changes whenever any of them does."""
    files = [f for f in sorted(STATIC.rglob("*")) if f.is_file() and f.suffix in _ASSET_SUFFIXES]
    sig = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    if sig not in _build_cache:
        h = hashlib.sha256()
        for f in files:
            h.update(f.name.encode())
            h.update(f.read_bytes())
        _build_cache.clear()
        _build_cache[sig] = h.hexdigest()[:10]
    return _build_cache[sig]


_ASSET_URL = re.compile(r"""(?P<q>["'])(?P<u>/static/(?!fonts/)[^"'?]+)(?P=q)""")


def _versioned_index() -> str:
    """index.html with ?v=<build id> on every /static/ URL, so the browser fetches changed assets by a new URL."""
    v = _build_id()
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return _ASSET_URL.sub(lambda m: f"{m['q']}{m['u']}?v={v}{m['q']}", html)


COOKIE = "aib_session"
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1", "testserver"}
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
_ERRORS = (
    sources.SourceError, sqlsafe.QueryError, workflows.WorkflowError,
    forecasting.ForecastError, risk.RiskError,
)


class SourceIn(BaseModel):
    kind: str
    location: str
    name: str | None = None


class AskIn(BaseModel):
    question: str
    source_id: int | None = None
    use_llm: bool = True
    preview: bool = False  # the home-page example on load: not written to the question history


class SqlIn(BaseModel):
    sql: str
    source_id: int | None = None


class MapIn(BaseModel):
    table: str
    mapping: dict[str, str]


class SqlExportIn(BaseModel):
    sql: str
    source_id: int | None = None


class LoginIn(BaseModel):
    name: str
    pin: str


class PinIn(BaseModel):
    old_pin: str
    new_pin: str


class TicketIn(BaseModel):
    kind: str
    title: str
    requested_by: str = ""  # ignored: the signed-in user raises the ticket
    customer_id: str | None = None
    order_id: str | None = None
    amount: float | None = None
    reason: str = ""
    payload: dict[str, Any] = {}


class ActIn(BaseModel):
    actor: str = ""  # ignored; kept so older clients still validate
    comment: str = ""


class AlertTicketIn(BaseModel):
    requested_by: str = ""
    comment: str = ""


class DismissIn(BaseModel):
    actor: str = ""
    note: str = ""


def create_app(db_path: str | Path | None = None, allowed_hosts: list[str] | None = None) -> FastAPI:
    path = Path(db_path) if db_path else config.app_db_path()
    appdb.init(path)
    boot = appdb.connect(path)
    new_pins: dict[str, str] = {}
    try:
        sources.ensure_builtin(boot)
        new_pins = auth.ensure_credentials(boot, path.parent / "pins.txt")
    finally:
        boot.close()
    app = FastAPI(title="Analyst-in-a-Box", version="0.1.0")
    app.state.new_pins = new_pins
    app.state.pins_file = path.parent / "pins.txt"
    hosts = {h.lower() for h in LOOPBACK | set(allowed_hosts or [])}
    any_host = "*" in hosts
    fraud_cache: dict[tuple, dict[str, Any]] = {}

    def get_con() -> Iterator[sqlite3.Connection]:
        con = appdb.connect(path)
        try:
            yield con
        finally:
            con.close()

    Con = Depends(get_con)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Refuse foreign Host headers (DNS rebinding), cross-site writes (CSRF) and oversize bodies."""
        host = (request.headers.get("host") or "").lower()
        hostname = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        if not any_host and hostname not in hosts:
            return JSONResponse({"detail": f"host {host!r} is not allowed"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1].lower() != host:
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
            limit = (config.MAX_UPLOAD_BYTES + 1024 * 1024 if request.url.path == "/api/upload"
                     else config.MAX_JSON_BODY)
            try:
                declared = int(request.headers.get("content-length") or 0)
            except ValueError:
                declared = 0
            if declared > limit:
                return JSONResponse(
                    {"detail": f"request body is larger than {limit // 1024 // 1024} MB"},
                    status_code=413)
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    async def _auth_error(request: Request, exc: Exception) -> JSONResponse:
        status = exc.status if isinstance(exc, auth.AuthError) else 401
        return JSONResponse({"detail": str(exc)}, status_code=status)

    app.add_exception_handler(auth.AuthError, _auth_error)

    def me(request: Request, con: sqlite3.Connection = Con) -> str:
        """The signed-in person, from the session cookie. Never from the request body."""
        name = auth.user_for(con, request.cookies.get(COOKIE))
        if not name:
            raise auth.AuthError("sign in first (choose your name and enter your PIN)")
        return name

    Me = Depends(me)

    def need_manager(name: str) -> None:
        if workflows.RANK[workflows.user_role(name)] < workflows.RANK["manager"]:
            raise HTTPException(403, "changing data sources needs a manager or the owner")

    def same(claimed: str, name: str) -> None:
        """A body that names someone else is an impersonation attempt, not a typo."""
        if claimed and claimed != name:
            raise HTTPException(403, f"you are signed in as {name}; you cannot act as {claimed}")

    async def _bad_request(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    for err in _ERRORS:
        app.add_exception_handler(err, _bad_request)

    def active(con: sqlite3.Connection, source_id: int | None = None) -> dict[str, Any]:
        return sources.get_source(con, source_id)

    def screened(src: dict[str, Any]) -> dict[str, Any]:
        if not sources.is_canonical(src):
            raise sources.SourceError(
                "fraud screening needs the orders/order_items/customers tables; map an uploaded "
                "sheet of order lines on the Data page, or use the sample")
        key = (src["location"], forecasting._stamp(src))
        if key not in fraud_cache:
            fraud_cache.clear()
            fraud_cache[key] = fraud.screen(src)
        return fraud_cache[key]

    def merged_alerts(con: sqlite3.Connection, src: dict[str, Any]) -> list[dict[str, Any]]:
        state = {r["alert_key"]: dict(r) for r in con.execute("SELECT * FROM alert_state")}
        out = []
        for a in screened(src)["alerts"]:
            s = state.get(a["key"])
            out.append(a | {"status": s["status"] if s else "open",
                            "ticket_id": s["ticket_id"] if s else None,
                            "note": s["note"] if s else None})
        return out

    # ---- state ------------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.post("/api/login")
    def login(body: LoginIn, response: Response, con: sqlite3.Connection = Con) -> dict[str, Any]:
        token = auth.login(con, body.name, body.pin)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            max_age=auth.SESSION_HOURS * 3600, path="/")
        return {"name": body.name, "role": workflows.user_role(body.name)}

    @app.post("/api/logout")
    def logout(request: Request, response: Response, con: sqlite3.Connection = Con) -> dict[str, Any]:
        auth.logout(con, request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/me/pin")
    def change_pin(body: PinIn, name: str = Me, con: sqlite3.Connection = Con) -> dict[str, Any]:
        auth.change_pin(con, name, body.old_pin, body.new_pin)
        return {"ok": True, "note": "you were signed out everywhere; sign in with the new PIN"}

    @app.get("/api/state")
    def state(request: Request, con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con)
        who = auth.user_for(con, request.cookies.get(COOKIE))
        return {
            "me": {"name": who, "role": workflows.user_role(who)} if who else None,
            "pins_file": app.state.pins_file.exists(),
            "llm": llm.status(), "source": {k: src[k] for k in ("id", "name", "kind")},
            "canonical": sources.is_canonical(src), "users": workflows.USERS,
            "policy": workflows.POLICY, "max_rows": config.MAX_ROWS,
        }

    # ---- data -------------------------------------------------------------------------
    @app.get("/api/sources")
    def list_sources(con: sqlite3.Connection = Con) -> list[dict[str, Any]]:
        return sources.list_sources(con)

    @app.post("/api/sources")
    def add_source(body: SourceIn, name: str = Me, con: sqlite3.Connection = Con) -> dict[str, Any]:
        need_manager(name)
        s = sources.add_source(con, body.kind, body.location, body.name)
        sources.activate(con, s["id"])
        appdb.audit(con, name, "source.added", None, {"name": s["name"], "kind": s["kind"]})
        return {"id": s["id"], "name": s["name"], "kind": s["kind"]}

    @app.post("/api/sources/{source_id}/activate")
    def activate(source_id: int, name: str = Me, con: sqlite3.Connection = Con) -> dict[str, Any]:
        sources.activate(con, source_id)
        return {"ok": True}

    @app.delete("/api/sources/{source_id}")
    def remove(source_id: int, name: str = Me, con: sqlite3.Connection = Con) -> dict[str, Any]:
        need_manager(name)
        sources.remove_source(con, source_id)
        appdb.audit(con, name, "source.removed", None, {"id": source_id})
        return {"ok": True}

    @app.get("/api/schema")
    def schema(source_id: int | None = None, con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con, source_id)
        return {"source": src["name"], "kind": src["kind"], "tables": sources.schema(src),
                "canonical": sources.is_canonical(src)}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...), name: str = Me,
                     con: sqlite3.Connection = Con) -> dict[str, Any]:
        need_manager(name)
        chunks, size = [], 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > config.MAX_UPLOAD_BYTES:  # stop reading; do not hold a gigabyte in memory
                raise sources.SourceError(
                    f"file is larger than {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB")
            chunks.append(chunk)
        res = sources.import_upload(con, file.filename or "upload.csv", b"".join(chunks))
        appdb.audit(con, name, "source.uploaded", None, {
            "file": (file.filename or "")[:120], "tables": [t["table"] for t in res["tables"]]})
        return res

    @app.get("/api/sales-fields")
    def sales_fields() -> dict[str, str]:
        return sources.SALES_FIELDS

    @app.post("/api/sources/{source_id}/map-sales")
    def map_sales(source_id: int, body: MapIn, name: str = Me,
                  con: sqlite3.Connection = Con) -> dict[str, Any]:
        need_manager(name)
        src = active(con, source_id)
        counts = sources.map_sales(src, body.table, body.mapping)
        fraud_cache.clear()
        return counts

    # ---- ask --------------------------------------------------------------------------
    def log_query(con: sqlite3.Connection, res: dict[str, Any]) -> None:
        con.execute(
            "INSERT INTO query_log(at,question,sql,mode,rows,ok,note) VALUES(?,?,?,?,?,?,?)",
            (appdb.now(), res.get("question"), res.get("sql"), res.get("mode"),
             len(res.get("rows", [])), 1 if res.get("ok") else 0, res.get("error") or res.get("note")))
        con.commit()

    @app.post("/api/ask")
    def ask(body: AskIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con, body.source_id)
        t0 = time.perf_counter()
        res = nlq.answer(src, body.question, client=llm.get_client() if body.use_llm else None)
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        # only the fixed home-page examples may skip the history; every other question is logged
        if not (body.preview and body.question in nlq.DEMO_QUESTIONS):
            log_query(con, res)
        return res

    @app.post("/api/sql")
    def run_sql(body: SqlIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con, body.source_id)
        res = sources.query(src, body.sql)
        res["chart"] = nlq.suggest_chart(res["columns"], res["rows"])
        res |= {"ok": True, "mode": "edited", "question": None}
        log_query(con, res)
        return res

    @app.post("/api/export")
    def export_csv(body: SqlExportIn, con: sqlite3.Connection = Con) -> Response:
        """The result of one read-only query as a CSV that is safe to open in a spreadsheet."""
        src = active(con, body.source_id)
        res = sources.query(src, body.sql, max_rows=config.EXPORT_ROWS, max_chars=50_000_000)
        text = "\ufeff" + exporting.to_csv(res["columns"], res["rows"])
        return Response(text.encode("utf-8"), media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="analyst-export.csv"',
            "X-Rows": str(len(res["rows"])), "X-Truncated": str(res["truncated"]).lower()})

    @app.get("/api/ask/examples")
    def examples() -> list[str]:
        return nlq.EXAMPLES

    @app.get("/api/ask/history")
    def history(con: sqlite3.Connection = Con) -> list[dict[str, Any]]:
        return [dict(r) for r in con.execute(
            "SELECT at, question, sql, mode, rows, ok, note FROM query_log "
            "ORDER BY id DESC LIMIT 30")]

    # ---- dashboard --------------------------------------------------------------------
    @app.get("/api/dashboard")
    def dash(con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con)
        n_alerts = None
        if sources.is_canonical(src):
            n_alerts = sum(1 for a in merged_alerts(con, src) if a["status"] == "open")
        return dashboard.build(src, open_alerts=n_alerts, tickets=workflows.summary(con))

    # ---- forecast ---------------------------------------------------------------------
    @app.get("/api/forecast")
    def forecast(horizon: int = 8, per_category: int = 3, method: str = "mint_wls",
                 con: sqlite3.Connection = Con) -> dict[str, Any]:
        return forecasting.run(active(con), horizon=horizon, per_category=per_category,
                               method=method)

    # ---- fraud ------------------------------------------------------------------------
    @app.get("/api/alerts")
    def alerts(status: str | None = "open", severity: str | None = None, kind: str | None = None,
               limit: int = 100, con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con)
        rows = merged_alerts(con, src)
        total = screened(src)["summary"]
        if status:
            rows = [a for a in rows if a["status"] == status]
        if severity:
            rows = [a for a in rows if a["severity"] == severity]
        if kind:
            rows = [a for a in rows if a["kind"] == kind]
        return {"summary": total, "matching": len(rows), "alerts": rows[: max(1, min(limit, 500))]}

    @app.post("/api/alerts/{key:path}/dismiss")
    def dismiss(key: str, body: DismissIn, name: str = Me,
                con: sqlite3.Connection = Con) -> dict[str, Any]:
        same(body.actor, name)
        if not any(a["key"] == key for a in screened(active(con))["alerts"]):
            raise workflows.WorkflowError(f"no alert {key}")
        con.execute(
            "INSERT INTO alert_state(alert_key,status,ticket_id,note,at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(alert_key) DO UPDATE SET status=excluded.status, note=excluded.note, at=excluded.at",
            (key, "dismissed", None, body.note, appdb.now()))
        con.commit()
        appdb.audit(con, name, "alert.dismissed", None, {"alert": key, "note": body.note})
        return {"ok": True}

    @app.post("/api/alerts/{key:path}/ticket")
    def alert_ticket(key: str, body: AlertTicketIn, name: str = Me,
                     con: sqlite3.Connection = Con) -> dict[str, Any]:
        same(body.requested_by, name)
        src = active(con)
        alert = next((a for a in screened(src)["alerts"] if a["key"] == key), None)
        if alert is None:
            raise workflows.WorkflowError(f"no alert {key}")
        if alert["kind"] == "day":
            raise workflows.WorkflowError("a daily spike has no order to hold; investigate it first")
        why = "; ".join(r["text"] for r in alert["reasons"])
        t = workflows.create(
            con, kind="fraud_review", title=f"Review {alert['subject'].lower()}",
            requested_by=name, customer_id=alert["customer_id"],
            order_id=alert["order_id"], amount=abs(alert["amount"]),
            reason=(body.comment + " | " if body.comment else "") + why,
            payload={"alert_key": key, "score": alert["score"]})
        con.execute(
            "INSERT INTO alert_state(alert_key,status,ticket_id,note,at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(alert_key) DO UPDATE SET status=excluded.status, ticket_id=excluded.ticket_id, at=excluded.at",
            (key, "ticketed", t["id"], None, appdb.now()))
        con.commit()
        return t

    # ---- risk -------------------------------------------------------------------------
    @app.get("/api/risk")
    def risk_summary(approve_rate: float = 0.8, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return risk.public(risk.build(active(con), approve_rate=approve_rate))

    @app.get("/api/risk/customers")
    def risk_customers(decision: str | None = None, order: str = "score", limit: int = 100,
                       approve_rate: float = 0.8, con: sqlite3.Connection = Con) -> list[dict]:
        r = risk.build(active(con), approve_rate=approve_rate)
        return risk.customer_list(r, order=order, limit=max(1, min(limit, 1000)), decision=decision)

    @app.get("/api/risk/customer/{cid}")
    def risk_customer(cid: str, approve_rate: float = 0.8,
                      con: sqlite3.Connection = Con) -> dict[str, Any]:
        r = risk.build(active(con), approve_rate=approve_rate)
        c = r["_customers"].get(cid)
        if c is None:
            raise risk.RiskError(f"customer {cid} has no completed orders to score")
        lim = con.execute("SELECT limit_gbp, set_at FROM credit_limits WHERE customer_id=?",
                          (cid,)).fetchone()
        return c | {"score_cutoff": r["score_cutoff"],
                    "credit_limit": dict(lim) if lim else None}

    # ---- workflows --------------------------------------------------------------------
    def order_room(src: dict[str, Any]):
        def check(order_id: str, customer_id: str | None) -> float | None:
            if not sources.is_canonical(src):
                return None
            rows = sources.rows_of(
                src, "SELECT customer_id, total FROM orders WHERE order_id=? AND status='completed'",
                (order_id,))
            if not rows or (customer_id and rows[0]["customer_id"] != customer_id):
                return None
            return float(rows[0]["total"])
        return check

    @app.post("/api/tickets")
    def new_ticket(body: TicketIn, name: str = Me, con: sqlite3.Connection = Con) -> dict[str, Any]:
        same(body.requested_by, name)
        src = active(con)
        payload = dict(body.payload)
        if body.kind == "credit_limit_change" and body.customer_id and sources.is_canonical(src):
            c = risk.build(src)["_customers"].get(body.customer_id)
            payload["risk_decision"] = c["decision"] if c else None
            payload["risk_score"] = c["score"] if c else None
        return workflows.create(
            con, kind=body.kind, title=body.title, requested_by=name,
            customer_id=body.customer_id, order_id=body.order_id, amount=body.amount,
            reason=body.reason, payload=payload, order_check=order_room(src))

    @app.get("/api/tickets")
    def tickets(status: str | None = None, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return {"tickets": workflows.listing(con, status), "summary": workflows.summary(con)}

    @app.get("/api/tickets/{tid}")
    def ticket(tid: int, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return workflows.get(con, tid)

    @app.post("/api/tickets/{tid}/approve")
    def approve(tid: int, body: ActIn, name: str = Me,
               con: sqlite3.Connection = Con) -> dict[str, Any]:
        same(body.actor, name)
        return workflows.approve(con, tid, name, body.comment)

    @app.post("/api/tickets/{tid}/reject")
    def reject(tid: int, body: ActIn, name: str = Me,
              con: sqlite3.Connection = Con) -> dict[str, Any]:
        same(body.actor, name)
        return workflows.reject(con, tid, name, body.comment)

    @app.post("/api/tickets/{tid}/cancel")
    def cancel(tid: int, body: ActIn, name: str = Me,
              con: sqlite3.Connection = Con) -> dict[str, Any]:
        same(body.actor, name)
        return workflows.cancel(con, tid, name)

    @app.post("/api/tickets/{tid}/execute")
    def execute(tid: int, body: ActIn, name: str = Me,
               con: sqlite3.Connection = Con) -> dict[str, Any]:
        same(body.actor, name)
        return workflows.execute(con, tid, name)

    @app.get("/api/audit")
    def audit_log(limit: int = 100, con: sqlite3.Connection = Con) -> list[dict[str, Any]]:
        return [dict(r) for r in con.execute(
            "SELECT id, at, actor, action, ticket_id, detail, hash FROM audit "
            "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),))]

    @app.get("/api/audit/verify")
    def audit_verify(con: sqlite3.Connection = Con) -> dict[str, Any]:
        """The hash chain, the signed head kept outside the database, and a cross-check of the
        tickets, approvals, ledger and credit limits against what the trail says happened."""
        res = appdb.verify_audit(con)
        rec = workflows.reconcile(con)
        res["reconcile"] = rec
        res["problems"] = res["problems"] + rec["problems"]
        res["ok"] = res["ok"] and not rec["problems"]
        return res

    @app.get("/api/audit/export")
    def audit_export(con: sqlite3.Connection = Con) -> Response:
        rows = con.execute("SELECT id, at, actor, action, ticket_id, detail, prev_hash, hash "
                           "FROM audit ORDER BY id").fetchall()
        text = "\ufeff" + exporting.to_csv(
            ["id", "at", "actor", "action", "ticket_id", "detail", "prev_hash", "hash"],
            [list(r) for r in rows])
        return Response(text.encode("utf-8"), media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="audit-trail.csv"'})

    @app.get("/api/ledger")
    def ledger(con: sqlite3.Connection = Con) -> dict[str, Any]:
        return {
            "ledger": [dict(r) for r in con.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT 100")],
            "credit_limits": [dict(r) for r in con.execute(
                "SELECT * FROM credit_limits ORDER BY set_at DESC LIMIT 100")],
        }

    @app.get("/api/canonical-tables")
    def canonical_tables() -> list[str]:
        return list(canon.CANON_TABLES)

    # ---- the page ---------------------------------------------------------------------
    @app.get("/")
    def index() -> Response:
        return Response(_versioned_index(), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})

    app.mount("/static", _RevalidatingStatic(directory=STATIC), name="static")
    return app
