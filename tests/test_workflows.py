import json

import pytest

from analyst_in_a_box import appdb
from analyst_in_a_box import workflows as wf


@pytest.fixture()
def con(tmp_path):
    p = tmp_path / "a.sqlite3"
    appdb.init(p)
    c = appdb.connect(p)
    yield c
    c.close()


def room(order_id, customer_id):
    return 500.0 if order_id == "O1" else None


def refund(con, amount, by="Amna Khan"):
    return wf.create(con, kind="refund", title="r", requested_by=by, customer_id="c1",
                     order_id="O1", amount=amount, order_check=room)


def test_gates_by_amount():
    assert wf.gates_for("refund", 50, {}) == ["manager"]
    assert wf.gates_for("refund", 100, {}) == ["manager"]
    assert wf.gates_for("refund", 100.01, {}) == ["manager", "manager"]
    assert wf.gates_for("refund", 1000.01, {}) == ["manager", "owner"]
    assert wf.gates_for("credit_limit_change", None, {"old_limit": 1000, "new_limit": 900}) == ["manager"]
    assert wf.gates_for("credit_limit_change", None, {"old_limit": 1000, "new_limit": 1200}) == ["manager"]
    assert wf.gates_for("credit_limit_change", None, {"old_limit": 1000, "new_limit": 1300}) == ["manager", "owner"]
    assert wf.gates_for("credit_limit_change", None, {"old_limit": 1000, "new_limit": 1100,
                                                      "risk_decision": "DECLINE"}) == ["manager", "owner"]


def test_full_lifecycle_two_gates(con):
    t = refund(con, 300)
    assert t["gates"] == ["manager", "manager"] and t["status"] == "pending"
    t = wf.approve(con, t["id"], "Bilal Ahmed")
    assert t["status"] == "pending" and t["approvals_given"] == 1
    t = wf.approve(con, t["id"], "Sana Malik")
    assert t["status"] == "approved"
    t = wf.execute(con, t["id"], "Bilal Ahmed")
    assert t["status"] == "executed"
    assert con.execute("SELECT amount FROM ledger WHERE kind='refund'").fetchone()[0] == 300


def test_four_eyes_and_role_rules(con):
    t = refund(con, 300, by="Bilal Ahmed")
    with pytest.raises(wf.WorkflowError, match="raised"):
        wf.approve(con, t["id"], "Bilal Ahmed")
    wf.approve(con, t["id"], "Sana Malik")
    with pytest.raises(wf.WorkflowError, match="already"):
        wf.approve(con, t["id"], "Sana Malik")
    big = refund(con, 150, by="Sana Malik")  # 2 gates (<=1000)
    with pytest.raises(wf.WorkflowError, match="manager"):
        wf.approve(con, big["id"], "Amna Khan")  # staff cannot approve


def test_owner_gate_needs_owner(con):
    t = wf.create(con, kind="credit_limit_change", title="cl", requested_by="Amna Khan",
                  customer_id="c1", payload={"new_limit": 9000})
    assert t["gates"] == ["manager", "owner"]
    wf.approve(con, t["id"], "Bilal Ahmed")
    with pytest.raises(wf.WorkflowError, match="owner"):
        wf.approve(con, t["id"], "Sana Malik")
    t = wf.approve(con, t["id"], "Omar Siddiqui")
    t = wf.execute(con, t["id"], "Omar Siddiqui")
    assert con.execute("SELECT limit_gbp FROM credit_limits WHERE customer_id='c1'").fetchone()[0] == 9000


def test_illegal_transitions_raise(con):
    t = refund(con, 50)
    with pytest.raises(wf.TransitionError):
        wf.execute(con, t["id"], "Bilal Ahmed")  # not approved yet
    wf.approve(con, t["id"], "Bilal Ahmed")
    wf.execute(con, t["id"], "Bilal Ahmed")
    with pytest.raises(wf.TransitionError):
        wf.execute(con, t["id"], "Bilal Ahmed")  # no double execution
    with pytest.raises(wf.TransitionError):
        wf.cancel(con, t["id"], "Amna Khan")


def test_reject_needs_reason_and_manager(con):
    t = refund(con, 50)
    with pytest.raises(wf.WorkflowError):
        wf.reject(con, t["id"], "Bilal Ahmed", "")
    with pytest.raises(wf.WorkflowError):
        wf.reject(con, t["id"], "Amna Khan", "no")
    assert wf.reject(con, t["id"], "Bilal Ahmed", "duplicate request")["status"] == "rejected"


def test_cancel_only_by_requester_or_owner(con):
    t = refund(con, 50)
    with pytest.raises(wf.WorkflowError):
        wf.cancel(con, t["id"], "Sana Malik")
    assert wf.cancel(con, t["id"], "Amna Khan")["status"] == "cancelled"


def test_refund_cannot_exceed_order(con):
    with pytest.raises(wf.WorkflowError, match="left to refund"):
        refund(con, 600)
    t = refund(con, 400)
    wf.approve(con, t["id"], "Bilal Ahmed")
    wf.approve(con, t["id"], "Sana Malik")
    wf.execute(con, t["id"], "Bilal Ahmed")
    with pytest.raises(wf.WorkflowError, match="left to refund"):
        refund(con, 150)  # only 100 left
    with pytest.raises(wf.WorkflowError, match="does not exist"):
        wf.create(con, kind="refund", title="r", requested_by="Amna Khan", order_id="NOPE",
                  amount=5, order_check=room)


def test_validation_errors(con):
    for bad in [dict(kind="nope"), dict(kind="refund", amount=-1, order_id="O1"),
                dict(kind="credit_limit_change"), dict(kind="fraud_review")]:
        with pytest.raises(wf.WorkflowError):
            wf.create(con, title="x", requested_by="Amna Khan", order_check=room, **bad)
    with pytest.raises(wf.WorkflowError, match="unknown user"):
        wf.create(con, kind="fraud_review", title="x", requested_by="Mallory", order_id="O1")


def test_audit_chain_detects_tampering(con):
    t = refund(con, 50)
    wf.approve(con, t["id"], "Bilal Ahmed")
    wf.execute(con, t["id"], "Bilal Ahmed")
    assert appdb.verify_audit(con) == {**appdb.verify_audit(con), "ok": True, "entries": 4}
    con.execute("UPDATE audit SET detail=? WHERE id=2", (json.dumps({"gate": 1, "of": 99}),))
    con.commit()
    bad = appdb.verify_audit(con)
    assert not bad["ok"] and bad["first_bad_id"] == 2


def test_audit_chain_detects_deleted_row(con):
    t = refund(con, 50)
    wf.approve(con, t["id"], "Bilal Ahmed")
    con.execute("DELETE FROM audit WHERE id=2")
    con.commit()
    assert not appdb.verify_audit(con)["ok"]
