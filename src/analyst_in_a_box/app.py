"""The HTTP API and the single-page app."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    appdb,
    canon,
    config,
    dashboard,
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


class SqlIn(BaseModel):
    sql: str
    source_id: int | None = None


class MapIn(BaseModel):
    table: str
    mapping: dict[str, str]


class TicketIn(BaseModel):
    kind: str
    title: str
    requested_by: str
    customer_id: str | None = None
    order_id: str | None = None
    amount: float | None = None
    reason: str = ""
    payload: dict[str, Any] = {}


class ActIn(BaseModel):
    actor: str
    comment: str = ""


class AlertTicketIn(BaseModel):
    requested_by: str
    comment: str = ""


class DismissIn(BaseModel):
    actor: str
    note: str = ""


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = Path(db_path) if db_path else config.app_db_path()
    appdb.init(path)
    boot = appdb.connect(path)
    try:
        sources.ensure_builtin(boot)
    finally:
        boot.close()
    app = FastAPI(title="Analyst-in-a-Box", version="0.1.0")
    fraud_cache: dict[tuple, dict[str, Any]] = {}

    def get_con() -> Iterator[sqlite3.Connection]:
        con = appdb.connect(path)
        try:
            yield con
        finally:
            con.close()

    Con = Depends(get_con)

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

    @app.get("/api/state")
    def state(con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con)
        return {
            "llm": llm.status(), "source": {k: src[k] for k in ("id", "name", "kind")},
            "canonical": sources.is_canonical(src), "users": workflows.USERS,
            "policy": workflows.POLICY, "max_rows": config.MAX_ROWS,
        }

    # ---- data -------------------------------------------------------------------------
    @app.get("/api/sources")
    def list_sources(con: sqlite3.Connection = Con) -> list[dict[str, Any]]:
        return sources.list_sources(con)

    @app.post("/api/sources")
    def add_source(body: SourceIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        s = sources.add_source(con, body.kind, body.location, body.name)
        sources.activate(con, s["id"])
        return {"id": s["id"], "name": s["name"], "kind": s["kind"]}

    @app.post("/api/sources/{source_id}/activate")
    def activate(source_id: int, con: sqlite3.Connection = Con) -> dict[str, Any]:
        sources.activate(con, source_id)
        return {"ok": True}

    @app.delete("/api/sources/{source_id}")
    def remove(source_id: int, con: sqlite3.Connection = Con) -> dict[str, Any]:
        sources.remove_source(con, source_id)
        return {"ok": True}

    @app.get("/api/schema")
    def schema(source_id: int | None = None, con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con, source_id)
        return {"source": src["name"], "kind": src["kind"], "tables": sources.schema(src),
                "canonical": sources.is_canonical(src)}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...), con: sqlite3.Connection = Con) -> dict[str, Any]:
        data = await file.read()
        return sources.import_upload(con, file.filename or "upload.csv", data)

    @app.get("/api/sales-fields")
    def sales_fields() -> dict[str, str]:
        return sources.SALES_FIELDS

    @app.post("/api/sources/{source_id}/map-sales")
    def map_sales(source_id: int, body: MapIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
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
        res = nlq.answer(src, body.question, client=llm.get_client() if body.use_llm else None)
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
    def dismiss(key: str, body: DismissIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        workflows.user_role(body.actor)
        if not any(a["key"] == key for a in screened(active(con))["alerts"]):
            raise workflows.WorkflowError(f"no alert {key}")
        con.execute(
            "INSERT INTO alert_state(alert_key,status,ticket_id,note,at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(alert_key) DO UPDATE SET status=excluded.status, note=excluded.note, at=excluded.at",
            (key, "dismissed", None, body.note, appdb.now()))
        con.commit()
        appdb.audit(con, body.actor, "alert.dismissed", None, {"alert": key, "note": body.note})
        return {"ok": True}

    @app.post("/api/alerts/{key:path}/ticket")
    def alert_ticket(key: str, body: AlertTicketIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con)
        alert = next((a for a in screened(src)["alerts"] if a["key"] == key), None)
        if alert is None:
            raise workflows.WorkflowError(f"no alert {key}")
        if alert["kind"] == "day":
            raise workflows.WorkflowError("a daily spike has no order to hold; investigate it first")
        why = "; ".join(r["text"] for r in alert["reasons"])
        t = workflows.create(
            con, kind="fraud_review", title=f"Review {alert['subject'].lower()}",
            requested_by=body.requested_by, customer_id=alert["customer_id"],
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
    def new_ticket(body: TicketIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        src = active(con)
        payload = dict(body.payload)
        if body.kind == "credit_limit_change" and body.customer_id and sources.is_canonical(src):
            c = risk.build(src)["_customers"].get(body.customer_id)
            payload["risk_decision"] = c["decision"] if c else None
            payload["risk_score"] = c["score"] if c else None
        return workflows.create(
            con, kind=body.kind, title=body.title, requested_by=body.requested_by,
            customer_id=body.customer_id, order_id=body.order_id, amount=body.amount,
            reason=body.reason, payload=payload, order_check=order_room(src))

    @app.get("/api/tickets")
    def tickets(status: str | None = None, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return {"tickets": workflows.listing(con, status), "summary": workflows.summary(con)}

    @app.get("/api/tickets/{tid}")
    def ticket(tid: int, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return workflows.get(con, tid)

    @app.post("/api/tickets/{tid}/approve")
    def approve(tid: int, body: ActIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return workflows.approve(con, tid, body.actor, body.comment)

    @app.post("/api/tickets/{tid}/reject")
    def reject(tid: int, body: ActIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return workflows.reject(con, tid, body.actor, body.comment)

    @app.post("/api/tickets/{tid}/cancel")
    def cancel(tid: int, body: ActIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return workflows.cancel(con, tid, body.actor)

    @app.post("/api/tickets/{tid}/execute")
    def execute(tid: int, body: ActIn, con: sqlite3.Connection = Con) -> dict[str, Any]:
        return workflows.execute(con, tid, body.actor)

    @app.get("/api/audit")
    def audit_log(limit: int = 100, con: sqlite3.Connection = Con) -> list[dict[str, Any]]:
        return [dict(r) for r in con.execute(
            "SELECT id, at, actor, action, ticket_id, detail, hash FROM audit "
            "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),))]

    @app.get("/api/audit/verify")
    def audit_verify(con: sqlite3.Connection = Con) -> dict[str, Any]:
        return appdb.verify_audit(con)

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
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
