"""Tickets with approval gates and a tamper-evident audit trail.

The state-machine idea (legal transitions are listed, anything else raises) is the one used in
enterprise-ops-crew's tickets module; the gates and the hash-chained audit are specific to this
app. There is no authentication: the person using the app picks who they are acting as, and the
rules (four eyes, role of each approver, distinct approvers) are enforced against that name.

Nothing here touches the business data. Executing a ticket writes to the app's own ledger and
credit-limit table.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from . import appdb

USERS = [
    {"name": "Amna Khan", "role": "staff"},
    {"name": "Bilal Ahmed", "role": "manager"},
    {"name": "Sana Malik", "role": "manager"},
    {"name": "Omar Siddiqui", "role": "owner"},
]
RANK = {"staff": 0, "manager": 1, "owner": 2}
KINDS = ("refund", "credit_limit_change", "fraud_review")
ALLOWED = {
    "pending": {"approved", "rejected", "cancelled"},
    "approved": {"executed", "cancelled"},
    "rejected": set(),
    "executed": set(),
    "cancelled": set(),
}
POLICY = [
    "Refund up to £100: one manager approves.",
    "Refund over £100 up to £1,000: two different managers approve.",
    "Refund over £1,000: a manager, then the owner.",
    "Credit limit decrease: one manager approves.",
    "Credit limit increase of up to 25% and to no more than £5,000: one manager approves.",
    "Any larger increase, or any increase for a customer the scorecard declines: a manager, then the owner.",
    "Fraud review: one manager approves; executing it places a hold on the order.",
    "Nobody approves a ticket they raised, and nobody approves the same ticket twice.",
]


class WorkflowError(ValueError):
    pass


class TransitionError(WorkflowError):
    pass


def user_role(name: str) -> str:
    for u in USERS:
        if u["name"] == name:
            return u["role"]
    raise WorkflowError(f"unknown user {name!r}; choose one of {[u['name'] for u in USERS]}")


def gates_for(kind: str, amount: float | None, payload: dict[str, Any]) -> list[str]:
    if kind == "refund":
        a = amount or 0.0
        if a <= 100:
            return ["manager"]
        return ["manager", "manager"] if a <= 1000 else ["manager", "owner"]
    if kind == "credit_limit_change":
        old, new = float(payload.get("old_limit") or 0), float(payload.get("new_limit") or 0)
        if new <= old:
            return ["manager"]
        small = old > 0 and (new - old) / old <= 0.25 and new <= 5000
        declined = payload.get("risk_decision") == "DECLINE"
        return ["manager"] if small and not declined else ["manager", "owner"]
    if kind == "fraud_review":
        return ["manager"]
    raise WorkflowError(f"kind must be one of {', '.join(KINDS)}")


def _row(con: sqlite3.Connection, tid: int) -> sqlite3.Row:
    r = con.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not r:
        raise WorkflowError(f"no ticket {tid}")
    return r


def _view(con: sqlite3.Connection, r: sqlite3.Row) -> dict[str, Any]:
    gates = json.loads(r["gates"])
    approvals = [dict(a) for a in con.execute(
        "SELECT approver, role, decision, comment, at FROM approvals WHERE ticket_id=? ORDER BY id",
        (r["id"],))]
    given = [a for a in approvals if a["decision"] == "approve"]
    return {
        "id": r["id"], "kind": r["kind"], "title": r["title"], "customer_id": r["customer_id"],
        "order_id": r["order_id"], "amount": r["amount"], "reason": r["reason"],
        "status": r["status"], "requested_by": r["requested_by"], "gates": gates,
        "approvals": approvals, "approvals_needed": len(gates), "approvals_given": len(given),
        "next_gate": gates[len(given)] if r["status"] == "pending" and len(given) < len(gates) else None,
        "created_at": r["created_at"], "updated_at": r["updated_at"],
        "payload": json.loads(r["payload"] or "{}"),
    }


def get(con: sqlite3.Connection, tid: int) -> dict[str, Any]:
    v = _view(con, _row(con, tid))
    v["audit"] = [dict(a) | {"detail": json.loads(a["detail"])} for a in con.execute(
        "SELECT id, at, actor, action, detail, hash FROM audit WHERE ticket_id=? ORDER BY id", (tid,))]
    return v


def listing(con: sqlite3.Connection, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM tickets", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    q += " ORDER BY id DESC LIMIT ?"
    return [_view(con, r) for r in con.execute(q, (*args, limit))]


def _transition(r: sqlite3.Row, new: str) -> None:
    if new not in ALLOWED[r["status"]]:
        raise TransitionError(f"ticket {r['id']} is {r['status']}; it cannot become {new}")


def _set_status(con: sqlite3.Connection, tid: int, status: str) -> None:
    con.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, appdb.now(), tid))
    con.commit()


def create(
    con: sqlite3.Connection, *, kind: str, title: str, requested_by: str,
    customer_id: str | None = None, order_id: str | None = None, amount: float | None = None,
    reason: str = "", payload: dict[str, Any] | None = None,
    order_check: Callable[[str, str | None], float | None] | None = None,
) -> dict[str, Any]:
    payload = dict(payload or {})
    user_role(requested_by)
    if kind not in KINDS:
        raise WorkflowError(f"kind must be one of {', '.join(KINDS)}")
    if not title.strip():
        raise WorkflowError("a ticket needs a title")
    if kind == "refund":
        if not order_id or amount is None or amount <= 0:
            raise WorkflowError("a refund needs an order id and a positive amount")
        if order_check is not None:
            room = order_check(order_id, customer_id)
            if room is None:
                raise WorkflowError(f"order {order_id} does not exist")
            already = con.execute(
                "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='refund' AND order_id=?",
                (order_id,)).fetchone()[0]
            if amount > room - already + 0.005:
                raise WorkflowError(
                    f"refund {amount:,.2f} is more than order {order_id} has left to refund "
                    f"({max(room - already, 0):,.2f})")
    if kind == "credit_limit_change":
        if not customer_id:
            raise WorkflowError("a credit limit change needs a customer id")
        try:
            new = float(payload.get("new_limit"))
        except (TypeError, ValueError) as exc:
            raise WorkflowError("give the new limit in payload.new_limit") from exc
        if new < 0:
            raise WorkflowError("the new limit cannot be negative")
        row = con.execute("SELECT limit_gbp FROM credit_limits WHERE customer_id=?",
                          (customer_id,)).fetchone()
        payload["old_limit"] = float(row[0]) if row else 0.0
        payload["new_limit"] = new
        amount = new
    if kind == "fraud_review" and not (order_id or customer_id):
        raise WorkflowError("a fraud review needs an order or a customer")
    gates = gates_for(kind, amount, payload)
    now = appdb.now()
    cur = con.execute(
        "INSERT INTO tickets(kind,title,customer_id,order_id,amount,reason,status,requested_by,"
        "gates,created_at,updated_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, title.strip(), customer_id, order_id, amount, reason, "pending", requested_by,
         json.dumps(gates), now, now, json.dumps(payload)))
    con.commit()
    tid = int(cur.lastrowid or 0)
    appdb.audit(con, requested_by, "ticket.created", tid,
                {"kind": kind, "amount": amount, "gates": gates, "order_id": order_id,
                 "customer_id": customer_id})
    return get(con, tid)


def approve(con: sqlite3.Connection, tid: int, actor: str, comment: str = "") -> dict[str, Any]:
    r = _row(con, tid)
    _transition(r, "approved")
    role = user_role(actor)
    if actor == r["requested_by"]:
        raise WorkflowError("you raised this ticket; someone else must approve it")
    gates = json.loads(r["gates"])
    given = [a["approver"] for a in con.execute(
        "SELECT approver FROM approvals WHERE ticket_id=? AND decision='approve'", (tid,))]
    if actor in given:
        raise WorkflowError("you have already approved this ticket")
    need = gates[len(given)]
    if RANK[role] < RANK[need]:
        raise WorkflowError(f"the next approval needs a {need}; {actor} is {role}")
    con.execute("INSERT INTO approvals(ticket_id,approver,role,decision,comment,at) VALUES(?,?,?,?,?,?)",
                (tid, actor, role, "approve", comment, appdb.now()))
    con.commit()
    done = len(given) + 1 == len(gates)
    appdb.audit(con, actor, "ticket.approved", tid,
                {"gate": len(given) + 1, "of": len(gates), "role": role, "comment": comment})
    if done:
        _set_status(con, tid, "approved")
        appdb.audit(con, "system", "ticket.fully_approved", tid, {})
    else:
        con.execute("UPDATE tickets SET updated_at=? WHERE id=?", (appdb.now(), tid))
        con.commit()
    return get(con, tid)


def reject(con: sqlite3.Connection, tid: int, actor: str, comment: str) -> dict[str, Any]:
    r = _row(con, tid)
    _transition(r, "rejected")
    if RANK[user_role(actor)] < RANK["manager"]:
        raise WorkflowError("only a manager or the owner can reject a ticket")
    if not comment.strip():
        raise WorkflowError("say why the ticket is rejected")
    con.execute("INSERT INTO approvals(ticket_id,approver,role,decision,comment,at) VALUES(?,?,?,?,?,?)",
                (tid, actor, user_role(actor), "reject", comment, appdb.now()))
    con.commit()
    _set_status(con, tid, "rejected")
    appdb.audit(con, actor, "ticket.rejected", tid, {"comment": comment})
    return get(con, tid)


def cancel(con: sqlite3.Connection, tid: int, actor: str) -> dict[str, Any]:
    r = _row(con, tid)
    _transition(r, "cancelled")
    if actor != r["requested_by"] and user_role(actor) != "owner":
        raise WorkflowError("only the person who raised the ticket, or the owner, can cancel it")
    _set_status(con, tid, "cancelled")
    appdb.audit(con, actor, "ticket.cancelled", tid, {})
    return get(con, tid)


def execute(con: sqlite3.Connection, tid: int, actor: str) -> dict[str, Any]:
    r = _row(con, tid)
    _transition(r, "executed")
    if RANK[user_role(actor)] < RANK["manager"]:
        raise WorkflowError("only a manager or the owner can execute an approved ticket")
    now = appdb.now()
    payload = json.loads(r["payload"] or "{}")
    if r["kind"] == "refund":
        con.execute("INSERT INTO ledger(kind,customer_id,order_id,amount,ticket_id,at) VALUES(?,?,?,?,?,?)",
                    ("refund", r["customer_id"], r["order_id"], r["amount"], tid, now))
        effect = {"ledger": "refund", "amount": r["amount"], "order_id": r["order_id"]}
    elif r["kind"] == "credit_limit_change":
        con.execute(
            "INSERT INTO credit_limits(customer_id,limit_gbp,set_at,ticket_id) VALUES(?,?,?,?) "
            "ON CONFLICT(customer_id) DO UPDATE SET limit_gbp=excluded.limit_gbp, "
            "set_at=excluded.set_at, ticket_id=excluded.ticket_id",
            (r["customer_id"], payload["new_limit"], now, tid))
        effect = {"credit_limit": payload["new_limit"], "was": payload.get("old_limit")}
    else:
        con.execute("INSERT INTO ledger(kind,customer_id,order_id,amount,ticket_id,at) VALUES(?,?,?,?,?,?)",
                    ("hold", r["customer_id"], r["order_id"], None, tid, now))
        key = payload.get("alert_key")
        if key:
            con.execute(
                "INSERT INTO alert_state(alert_key,status,ticket_id,note,at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(alert_key) DO UPDATE SET status=excluded.status, at=excluded.at",
                (key, "confirmed", tid, "hold placed", now))
        effect = {"ledger": "hold", "order_id": r["order_id"], "customer_id": r["customer_id"]}
    con.commit()
    _set_status(con, tid, "executed")
    appdb.audit(con, actor, "ticket.executed", tid, effect)
    return get(con, tid)


def summary(con: sqlite3.Connection) -> dict[str, Any]:
    counts = {s: 0 for s in ALLOWED}
    for r in con.execute("SELECT status, COUNT(*) n FROM tickets GROUP BY status"):
        counts[r["status"]] = r["n"]
    return {"by_status": counts, "awaiting_approval": counts["pending"],
            "awaiting_execution": counts["approved"]}
