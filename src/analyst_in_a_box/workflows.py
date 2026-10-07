"""Tickets with approval gates and a tamper-evident audit trail.

The state-machine idea (legal transitions are listed, anything else raises) is the one used in
enterprise-ops-crew's tickets module; the gates and the hash-chained audit are specific to this
app. There is no authentication: the person using the app picks who they are acting as, and the
rules (four eyes, role of each approver, distinct approvers) are enforced against that name.

Nothing here touches the business data. Executing a ticket writes to the app's own ledger and
credit-limit table.
"""

from __future__ import annotations

import functools
import json
import math
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


def _serialised(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Run a state change under the app-wide lock. Without it two simultaneous requests can both
    read "no approvals yet" and both record one, so one manager could fill both gates of a
    two-manager refund, or one ticket could be executed twice."""
    @functools.wraps(fn)
    def wrapper(*a: Any, **k: Any) -> Any:
        with appdb.LOCK:
            return fn(*a, **k)
    return wrapper


def _finite(x: Any, what: str) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"{what} must be a number") from exc
    if not math.isfinite(v) or abs(v) > 1e12:
        raise WorkflowError(f"{what} must be a finite number below 1e12")
    return v


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


def _refunded_or_claimed(con: sqlite3.Connection, order_id: str, skip_ticket: int = 0) -> float:
    """Refunds already paid on an order plus the amount of refund tickets that could still pay."""
    paid = con.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='refund' AND order_id=?",
        (order_id,)).fetchone()[0]
    open_ = con.execute(
        "SELECT COALESCE(SUM(amount),0) FROM tickets WHERE kind='refund' AND order_id=? "
        "AND status IN ('pending','approved') AND id<>?", (order_id, skip_ticket)).fetchone()[0]
    return float(paid) + float(open_)


@_serialised
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
    if amount is not None:
        amount = _finite(amount, "the amount")
    if kind == "refund":
        if not order_id or amount is None or amount <= 0:
            raise WorkflowError("a refund needs an order id and a positive amount")
        if order_check is not None:
            room = order_check(order_id, customer_id)
            if room is None:
                raise WorkflowError(f"order {order_id} does not exist")
            already = _refunded_or_claimed(con, order_id)
            if amount > room - already + 0.005:
                raise WorkflowError(
                    f"refund {amount:,.2f} is more than order {order_id} has left to refund "
                    f"({max(room - already, 0):,.2f}, counting refunds already made and "
                    f"tickets still open)")
            payload["order_total"] = room
    if kind == "credit_limit_change":
        if not customer_id:
            raise WorkflowError("a credit limit change needs a customer id")
        new = _finite(payload.get("new_limit"), "payload.new_limit")
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


@_serialised
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


@_serialised
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


@_serialised
def cancel(con: sqlite3.Connection, tid: int, actor: str) -> dict[str, Any]:
    r = _row(con, tid)
    _transition(r, "cancelled")
    if actor != r["requested_by"] and user_role(actor) != "owner":
        raise WorkflowError("only the person who raised the ticket, or the owner, can cancel it")
    _set_status(con, tid, "cancelled")
    appdb.audit(con, actor, "ticket.cancelled", tid, {})
    return get(con, tid)


@_serialised
def execute(con: sqlite3.Connection, tid: int, actor: str) -> dict[str, Any]:
    r = _row(con, tid)
    _transition(r, "executed")
    if RANK[user_role(actor)] < RANK["manager"]:
        raise WorkflowError("only a manager or the owner can execute an approved ticket")
    now = appdb.now()
    payload = json.loads(r["payload"] or "{}")
    if r["kind"] == "refund":
        total = payload.get("order_total")
        if total is not None:
            paid = con.execute(
                "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='refund' AND order_id=?",
                (r["order_id"],)).fetchone()[0]
            if r["amount"] > float(total) - float(paid) + 0.005:
                raise WorkflowError(
                    f"order {r['order_id']} has only {max(float(total) - float(paid), 0):,.2f} left "
                    f"to refund; this ticket asks for {r['amount']:,.2f}. Cancel it.")
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


_STATUS_AFTER = {
    "ticket.created": "pending", "ticket.fully_approved": "approved", "ticket.rejected": "rejected",
    "ticket.cancelled": "cancelled", "ticket.executed": "executed",
}


def reconcile(con: sqlite3.Connection) -> dict[str, Any]:
    """Check the live tables against the audit trail. The hash chain protects the audit rows only;
    this catches someone editing the tickets, approvals, ledger or credit limits directly (for
    example UPDATE tickets SET status='approved') without leaving an audit entry."""
    problems: list[str] = []
    events: dict[int, list[sqlite3.Row]] = {}
    for a in con.execute("SELECT * FROM audit WHERE ticket_id IS NOT NULL ORDER BY id"):
        events.setdefault(a["ticket_id"], []).append(a)
    tickets = list(con.execute("SELECT * FROM tickets ORDER BY id"))
    for t in tickets:
        ev = events.get(t["id"], [])
        if not ev or ev[0]["action"] != "ticket.created":
            problems.append(f"ticket {t['id']} has no creation entry in the audit trail")
            continue
        created = json.loads(ev[0]["detail"])
        if ev[0]["actor"] != t["requested_by"]:
            problems.append(f"ticket {t['id']}: requested_by is {t['requested_by']} but the audit says {ev[0]['actor']}")
        if json.dumps(created.get("gates")) != t["gates"]:
            problems.append(f"ticket {t['id']}: approval gates differ from the audit trail")
        if created.get("amount") != t["amount"]:
            problems.append(f"ticket {t['id']}: amount {t['amount']} differs from the audit trail ({created.get('amount')})")
        status = "pending"
        for e in ev:
            status = _STATUS_AFTER.get(e["action"], status)
        if status != t["status"]:
            problems.append(f"ticket {t['id']} is {t['status']} but the audit trail says {status}")
        given = [e for e in ev if e["action"] == "ticket.approved"]
        rows = con.execute(
            "SELECT approver FROM approvals WHERE ticket_id=? AND decision='approve' ORDER BY id", (t["id"],)).fetchall()
        if [e["actor"] for e in given] != [r[0] for r in rows]:
            problems.append(f"ticket {t['id']}: the approvals table does not match the audited approvals")
    ids = {t["id"] for t in tickets}
    for tid in set(events) - ids:
        if any(e["action"] == "ticket.created" for e in events[tid]):
            problems.append(f"ticket {tid} is in the audit trail but missing from the tickets table")
    executed = {a["ticket_id"]: json.loads(a["detail"]) for a in con.execute(
        "SELECT * FROM audit WHERE action='ticket.executed'")}
    for r in con.execute("SELECT * FROM ledger WHERE ticket_id IS NOT NULL"):
        eff = executed.get(r["ticket_id"])
        if eff is None:
            problems.append(f"ledger row {r['id']} (ticket {r['ticket_id']}) has no audited execution")
        elif eff.get("amount") is not None and eff.get("amount") != r["amount"]:
            problems.append(f"ledger row {r['id']}: amount {r['amount']} differs from the audited {eff['amount']}")
    for r in con.execute("SELECT * FROM credit_limits WHERE ticket_id IS NOT NULL"):
        eff = executed.get(r["ticket_id"])
        if eff is None or eff.get("credit_limit") != r["limit_gbp"]:
            problems.append(f"credit limit of customer {r['customer_id']} does not match ticket {r['ticket_id']}'s audited execution")
    return {"tickets_checked": len(tickets), "problems": problems}
