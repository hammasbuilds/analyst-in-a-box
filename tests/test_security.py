"""Attacks, each kept as a regression test: SQL bypasses, impersonation (four-eyes), races,
over-refunds, hostile uploads, CSV formula injection, CSRF and audit tampering."""

import hashlib
import io
import json
import sqlite3
import threading
import time
import zipfile

import pytest
from conftest import PIN

from analyst_in_a_box import appdb, auth, exporting, sources, sqlsafe, workflows


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def biz(tmp_path):
    p = tmp_path / "b.sqlite3"
    c = sqlite3.connect(p)
    c.execute("create table orders(id int, total real, note text, blobby blob)")
    c.executemany("insert into orders values(?,?,?,?)",
                  [(i, i * 1.5, f"n{i}", b"\x00\xff") for i in range(3000)])
    c.commit()
    c.close()
    return {"kind": "sqlite", "location": str(p)}


def count(src):
    c = sqlite3.connect(src["location"])
    try:
        return c.execute("select count(*) from orders").fetchone()[0]
    finally:
        c.close()


# ---- the read-only guarantee ------------------------------------------------------------------
ATTACKS = [
    "SELECT 1; DROP TABLE orders",
    "SELECT 1 /* ; */; DELETE FROM orders",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT * FROM orders; PRAGMA writable_schema=1",
    "PRAGMA table_info(orders)",
    "PRAGMA query_only=OFF",
    "SELECT load_extension('x')",
    "SELECT LOAD_EXTENSION('x')",
    'SELECT "load_extension"(\'x\')',
    "WITH x AS (SELECT 1) DELETE FROM orders",
    "WITH x AS (SELECT 1) INSERT INTO orders SELECT 1,1,'a',x'00'",
    "WITH x AS (SELECT 1) UPDATE orders SET total=0",
    "REPLACE INTO orders VALUES(1,1,'a',x'00')",
    "VACUUM",
    "VACUUM INTO 'copy.db'",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM main.sqlite_master",
    "SELECT * FROM [sqlite_master]",
    "SELECT * FROM `sqlite_schema`",
    "SELECT * FROM sqlite_temp_master",
    "SELECT * FROM pragma_table_info('orders')",
    "SELECT 1; DROP TABLE orders",  # Greek question mark, looks like a semicolon
    "SELECT 1； DROP TABLE orders",  # fullwidth semicolon
    "SELECT 1​; DROP TABLE orders",
    "SELECT 1 -- \n; DROP TABLE orders",
    "SELECT randomblob(1000000000)",
    "SELECT hex(zeroblob(900000000))",
    "CREATE TABLE x AS SELECT * FROM orders",
    "CREATE TEMP TABLE x(a)",
    "EXPLAIN SELECT 1",
    "SELECT '",
    "SELECT * FROM orders WHERE 1=1 /* unterminated",
    "SELECT " + "(" * 5000 + "1" + ")" * 5000,
    "SELECT 1" + " UNION SELECT 1" * 400,
    "SELECT " + "+".join(["1"] * 20000),
    "SELECT 1 FROM orders WHERE id IN (" + ",".join(str(i) for i in range(30000)) + ")",
]


@pytest.mark.parametrize("sql", ATTACKS, ids=[f"attack{i}" for i in range(len(ATTACKS))])
def test_every_bypass_is_refused_and_nothing_changes(biz, sql):
    before = count(biz)
    with pytest.raises(sqlsafe.QueryError):
        sources.query(biz, sql)
    assert count(biz) == before


def test_catalog_check_looks_past_a_schema_prefix():
    v = sqlsafe.validate("SELECT * FROM main.sqlite_master", max_rows=10)
    assert not v.ok and v.refused


def test_authoriser_alone_stops_writes_when_the_parser_is_fooled(biz):
    """Layer 3 on its own: statements that never went through validate()."""
    for sql in ("DELETE FROM orders", "ATTACH 'y.db' AS y", "PRAGMA writable_schema=1",
                "CREATE TABLE z(a)", "INSERT INTO orders VALUES(1,1,'a',x'00')", "DROP TABLE orders"):
        with pytest.raises(sqlsafe.QueryError):
            sqlsafe.run_sqlite(biz["location"], sql, max_rows=10, timeout_s=2)
    assert count(biz) == 3000


def test_string_bomb_is_stopped_fast_and_small(biz):
    for sql in ("SELECT printf('%.*c', 900000000, 'x')", "SELECT length(printf('%1000000000d', 1))",
                "SELECT replace(printf('%.*c',100000000,'a'),'a','bbbbbbbbbb')"):
        t = time.time()
        try:
            res = sources.query(biz, sql)
            assert all(len(str(c)) < 6000 for row in res["rows"] for c in row)
        except sqlsafe.QueryError:
            pass
        assert time.time() - t < 3


def test_slow_query_times_out(biz, monkeypatch):
    monkeypatch.setattr("analyst_in_a_box.config.QUERY_TIMEOUT_S", 1.0)
    t = time.time()
    with pytest.raises(sqlsafe.QueryError, match="timed out"):
        sources.query(biz, "SELECT count(*) FROM orders a, orders b, orders c, orders d")
    assert time.time() - t < 5


def test_limit_cannot_be_escaped_and_odd_values_are_json_safe(biz):
    assert len(sources.query(biz, "SELECT * FROM orders LIMIT -1")["rows"]) == 1000
    assert len(sources.query(biz, "SELECT * FROM orders LIMIT 99999999")["rows"]) == 1000
    r = sources.query(biz, "SELECT blobby, 1e999 AS inf, -1e999 AS ninf FROM orders LIMIT 1")
    assert r["rows"][0] == ["<2 byte blob>", None, None]
    json.dumps(r, allow_nan=False)


def test_http_sql_endpoint_survives_odd_values(client):
    r = ok(client.post("/api/sql", json={"sql": "SELECT x'ff00' AS b, 1e999 AS i, 'é' AS u"}))
    assert r["rows"][0][0].startswith("<") and r["rows"][0][1] is None


def test_app_database_cannot_become_a_data_source(client, home):
    r = client.post("/api/sources", json={"kind": "sqlite", "location": str(home / "analyst.sqlite3")})
    assert r.status_code == 400 and "own database" in r.text


def test_table_names_with_quotes_do_not_break_the_schema_browser(tmp_path):
    p = tmp_path / "q.sqlite3"
    c = sqlite3.connect(p)
    c.execute('create table "we""ird"(a)')
    c.execute('insert into "we""ird" values(1)')
    c.commit()
    c.close()
    assert sources.schema({"kind": "sqlite", "location": str(p)})[0]["name"] == 'we"ird'


# ---- who is acting: sign-in and four-eyes -----------------------------------------------------


def raise_refund(client, who="Amna Khan", amount=500):
    oid, cid = ok(client.post("/api/sql", json={
        "sql": "SELECT order_id, customer_id FROM orders WHERE status='completed' "
               f"AND total > {amount + 10} LIMIT 1"}))["rows"][0]
    return ok(client.post("/api/tickets", json={
        "kind": "refund", "title": "r", "requested_by": who, "order_id": oid,
        "customer_id": cid, "amount": amount}))


def test_writes_need_a_session(plain_client):
    for method, url, body in (
        ("post", "/api/tickets", {"kind": "fraud_review", "title": "x", "customer_id": "1"}),
        ("post", "/api/tickets/1/approve", {}), ("post", "/api/tickets/1/execute", {}),
        ("post", "/api/alerts/x/dismiss", {}), ("delete", "/api/sources/2", None),
        ("post", "/api/sources/1/activate", None),
    ):
        r = getattr(plain_client, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 401, (url, r.text)
    assert plain_client.post("/api/upload", files={"file": ("a.csv", b"a\n1\n")}).status_code == 401
    # reading and asking stay open
    assert plain_client.get("/api/tickets").status_code == 200
    assert plain_client.post("/api/sql", json={"sql": "SELECT 1"}).status_code == 200


def test_wrong_pin_and_lockout(plain_client):
    auth._fails.clear()
    assert plain_client.post("/api/login", json={"name": "Bilal Ahmed", "pin": "nope"}).status_code == 401
    assert plain_client.post("/api/login", json={"name": "Nobody", "pin": PIN}).status_code == 401
    for _ in range(auth.MAX_FAILS):
        plain_client.post("/api/login", json={"name": "Bilal Ahmed", "pin": "nope"})
    locked = plain_client.post("/api/login", json={"name": "Bilal Ahmed", "pin": PIN})
    assert locked.status_code == 429
    auth._fails.clear()
    assert plain_client.post("/api/login", json={"name": "Bilal Ahmed", "pin": PIN}).status_code == 200


def test_cannot_act_as_someone_else_by_naming_them_in_the_body(plain_client):
    plain_client.sign_in("Amna Khan")
    t = raise_refund(plain_client)
    assert t["requested_by"] == "Amna Khan"
    # signed in as Amna but claiming to be Bilal: refused, not silently accepted
    r = plain_client.post(f"/api/tickets/{t['id']}/approve", json={"actor": "Bilal Ahmed"})
    assert r.status_code == 403 and "cannot act as" in r.text
    # and Amna cannot approve her own ticket under her own name
    assert plain_client.post(f"/api/tickets/{t['id']}/approve", json={}).status_code == 400
    # a ticket raised "as Bilal" while signed in as Amna is refused too
    r = plain_client.post("/api/tickets", json={"kind": "fraud_review", "title": "x", "customer_id": "1",
                                                "requested_by": "Bilal Ahmed"})
    assert r.status_code == 403


def test_one_session_cannot_fill_both_manager_gates(plain_client):
    plain_client.sign_in("Amna Khan")
    t = raise_refund(plain_client, amount=500)  # needs two managers
    assert t["gates"] == ["manager", "manager"]
    plain_client.sign_in("Bilal Ahmed")
    assert ok(plain_client.post(f"/api/tickets/{t['id']}/approve", json={}))["approvals_given"] == 1
    again = plain_client.post(f"/api/tickets/{t['id']}/approve", json={})
    assert again.status_code == 400 and "already approved" in again.text
    plain_client.post("/api/logout")
    assert plain_client.post(f"/api/tickets/{t['id']}/approve", json={}).status_code == 401
    plain_client.sign_in("Sana Malik")
    assert ok(plain_client.post(f"/api/tickets/{t['id']}/approve", json={}))["status"] == "approved"


def test_staff_cannot_change_data_sources(plain_client):
    plain_client.sign_in("Amna Khan")
    assert plain_client.post("/api/upload", files={"file": ("a.csv", b"a\n1\n")}).status_code == 403
    plain_client.sign_in("Bilal Ahmed")
    assert plain_client.post("/api/upload", files={"file": ("a.csv", b"a\n1\n")}).status_code == 200


def test_pin_change_and_reset(plain_client, home):
    plain_client.sign_in("Bilal Ahmed")
    assert plain_client.post("/api/me/pin", json={"old_pin": "wrong", "new_pin": "abcdefgh"}).status_code == 403
    assert plain_client.post("/api/me/pin", json={"old_pin": PIN, "new_pin": "abc"}).status_code == 400
    ok(plain_client.post("/api/me/pin", json={"old_pin": PIN, "new_pin": "a-new-pin-9"}))
    assert plain_client.post(
        "/api/tickets", json={"kind": "fraud_review", "title": "x", "customer_id": "1"}).status_code == 401
    assert plain_client.post("/api/login", json={"name": "Bilal Ahmed", "pin": PIN}).status_code == 401
    assert plain_client.post("/api/login", json={"name": "Bilal Ahmed", "pin": "a-new-pin-9"}).status_code == 200
    con = appdb.connect(home / "analyst.sqlite3")
    new = auth.reset_pin(con, "Bilal Ahmed")
    con.close()
    assert len(new) == auth.PIN_LENGTH
    assert plain_client.post("/api/login", json={"name": "Bilal Ahmed", "pin": new}).status_code == 200


def test_first_run_pins_are_random_hashed_and_written_once(tmp_path):
    from analyst_in_a_box.app import create_app

    db = tmp_path / "x" / "a.sqlite3"
    db.parent.mkdir()
    app = create_app(db)
    pins = app.state.new_pins
    assert set(pins) == {u["name"] for u in workflows.USERS} and len(set(pins.values())) == 4
    text = (db.parent / "pins.txt").read_text()
    assert all(f"{n}\t{p}" in text for n, p in pins.items())
    con = appdb.connect(db)
    stored = " ".join(r[0] for r in con.execute("SELECT pin_hash FROM credentials"))
    assert not any(p in stored for p in pins.values())
    con.close()
    assert create_app(db).state.new_pins == {}  # a second start does not regenerate them


def test_session_token_is_stored_hashed_and_cookie_is_httponly(plain_client, home):
    r = plain_client.post("/api/login", json={"name": "Bilal Ahmed", "pin": PIN})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    token = r.cookies.get("aib_session")
    con = appdb.connect(home / "analyst.sqlite3")
    assert con.execute("SELECT COUNT(*) FROM sessions WHERE token_hash=?", (token,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM sessions WHERE token_hash=?",
                       (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0] == 1
    con.close()


def test_cross_site_writes_and_foreign_hosts_are_refused(plain_client):
    plain_client.sign_in("Bilal Ahmed")
    body = {"kind": "fraud_review", "title": "x", "customer_id": "1"}
    r = plain_client.post("/api/tickets", json=body, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    r = plain_client.post("/api/tickets", json=body, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    r = plain_client.post("/api/upload", files={"file": ("a.csv", b"a\n1\n")},
                          headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert plain_client.get("/api/state", headers={"host": "evil.example"}).status_code == 403  # DNS rebinding
    same = plain_client.post("/api/tickets", json=body, headers={"origin": "http://testserver"})
    assert same.status_code == 200


def test_security_headers_and_oversize_bodies(plain_client):
    r = plain_client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    plain_client.sign_in("Bilal Ahmed")
    r = plain_client.post("/api/sql", content=b"{" + b" " * 3_000_000 + b"}",
                          headers={"content-type": "application/json"})
    assert r.status_code == 413


# ---- races and over-refunds -------------------------------------------------------------------


@pytest.fixture()
def db(home):
    appdb.init(home / "analyst.sqlite3")
    return home


def _hammer(home, fn, n):
    results = []

    def go():
        c = appdb.connect(home / "analyst.sqlite3")
        try:
            fn(c)
            results.append("ok")
        except workflows.WorkflowError:
            results.append("refused")
        finally:
            c.close()

    threads = [threading.Thread(target=go) for _ in range(n)]
    [x.start() for x in threads]
    [x.join() for x in threads]
    return results


def test_concurrent_approvals_by_one_person_count_once(db):
    home = db
    con = appdb.connect(home / "analyst.sqlite3")
    t = workflows.create(con, kind="refund", title="race", requested_by="Amna Khan", order_id="O", amount=500)
    results = _hammer(home, lambda c: workflows.approve(c, t["id"], "Bilal Ahmed"), 10)
    assert results.count("ok") == 1
    v = workflows.get(con, t["id"])
    assert v["approvals_given"] == 1 and v["status"] == "pending"


def test_concurrent_execution_pays_once(db):
    home = db
    con = appdb.connect(home / "analyst.sqlite3")
    t = workflows.create(con, kind="refund", title="r", requested_by="Amna Khan", order_id="O", amount=50)
    workflows.approve(con, t["id"], "Bilal Ahmed")
    results = _hammer(home, lambda c: workflows.execute(c, t["id"], "Sana Malik"), 8)
    assert results.count("ok") == 1
    assert con.execute("SELECT COUNT(*) FROM ledger WHERE ticket_id=?", (t["id"],)).fetchone()[0] == 1


def test_database_itself_refuses_a_duplicate_approval(db):
    home = db
    con = appdb.connect(home / "analyst.sqlite3")
    t = workflows.create(con, kind="refund", title="r", requested_by="Amna Khan", order_id="O", amount=500)
    con.execute("INSERT INTO approvals(ticket_id,approver,role,decision,at) VALUES(?,?,?,?,?)",
                (t["id"], "Bilal Ahmed", "manager", "approve", "now"))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO approvals(ticket_id,approver,role,decision,at) VALUES(?,?,?,?,?)",
                    (t["id"], "Bilal Ahmed", "manager", "approve", "now"))


def test_open_tickets_count_against_the_order_so_it_cannot_be_refunded_twice(client):
    oid, cid, total = ok(client.post("/api/sql", json={
        "sql": "SELECT order_id, customer_id, total FROM orders WHERE status='completed' "
               "AND total BETWEEN 60 AND 90 LIMIT 1"}))["rows"][0]
    first = ok(client.post("/api/tickets", json={"kind": "refund", "title": "a", "requested_by": "Amna Khan",
                                                 "order_id": oid, "customer_id": cid, "amount": total}))
    again = client.post("/api/tickets", json={"kind": "refund", "title": "b", "requested_by": "Amna Khan",
                                              "order_id": oid, "customer_id": cid, "amount": total})
    assert again.status_code == 400 and "left to refund" in again.text
    ok(client.post(f"/api/tickets/{first['id']}/cancel", json={"actor": "Amna Khan"}))
    ok(client.post("/api/tickets", json={"kind": "refund", "title": "c", "requested_by": "Amna Khan",
                                         "order_id": oid, "customer_id": cid, "amount": total}))


def test_execute_rechecks_the_order_if_the_ledger_changed_after_approval(db):
    home = db
    con = appdb.connect(home / "analyst.sqlite3")

    def room(o, c):
        return 100.0

    t = workflows.create(con, kind="refund", title="a", requested_by="Amna Khan", order_id="O",
                         amount=80, order_check=room)
    workflows.approve(con, t["id"], "Bilal Ahmed")
    con.execute("INSERT INTO ledger(kind,order_id,amount,ticket_id,at) VALUES('refund','O',60,NULL,'now')")
    con.commit()
    with pytest.raises(workflows.WorkflowError, match="left to refund"):
        workflows.execute(con, t["id"], "Bilal Ahmed")


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -float("inf"), 1e15])
def test_non_finite_amounts_are_refused(db, amount):
    home = db
    con = appdb.connect(home / "analyst.sqlite3")
    with pytest.raises(workflows.WorkflowError):
        workflows.create(con, kind="refund", title="x", requested_by="Amna Khan", order_id="O", amount=amount)
    with pytest.raises(workflows.WorkflowError):
        workflows.create(con, kind="credit_limit_change", title="x", requested_by="Amna Khan",
                         customer_id="c", payload={"new_limit": amount})


def test_nan_over_http(client):
    client.sign_in("Amna Khan")
    r = client.post("/api/tickets", content='{"kind":"refund","title":"x","requested_by":"Amna Khan",'
                    '"order_id":"zz","amount":NaN}', headers={"content-type": "application/json"})
    assert r.status_code in (400, 422)


# ---- uploads ----------------------------------------------------------------------------------


def upload(client, name, data):
    return client.post("/api/upload", files={"file": (name, data)})


def test_big_ids_and_leading_zeros_survive_an_upload(client):
    ok(upload(client, "ids.csv", "id,zip,n\n123456789012345678,00123,1\n2,00045,2.5\n"))
    rows = ok(client.post("/api/sql", json={"sql": "SELECT id, zip, n FROM ids ORDER BY id"}))["rows"]
    assert rows == [[2, "00045", 2.5], [123456789012345678, "00123", 1.0]]
    ok(upload(client, "huge.csv", "a\n99999999999999999999\n12\n"))
    assert ok(client.post("/api/sql", json={"sql": "SELECT a FROM huge ORDER BY a"}))["rows"][0][0] == "12"


def test_nan_strings_become_missing_not_text(client):
    ok(upload(client, "m.csv", "a\n1\nNaN\n3\n"))
    r = ok(client.post("/api/sql", json={"sql": "SELECT COUNT(a), SUM(a) FROM m"}))
    assert r["rows"][0] == [2, 4]


def test_oversized_cell_wide_sheet_and_garbage_are_clean_400s(client):
    assert upload(client, "big.csv", ("a,b\n" + "x" * 200_000 + ",1\n").encode()).status_code == 400
    wide = ",".join(f"c{i}" for i in range(600)) + "\n" + ",".join("1" for _ in range(600))
    assert upload(client, "wide.csv", wide.encode()).status_code == 400
    assert upload(client, "e.xlsx", b"not a zip").status_code == 400
    assert upload(client, "empty.csv", b"").status_code == 400
    assert upload(client, "../../evil.csv", b"a\n1\n").status_code == 200  # only the stem is used


def test_zip_bomb_workbook_is_refused_before_it_is_unpacked(client, monkeypatch):
    monkeypatch.setattr("analyst_in_a_box.config.MAX_UNZIPPED_BYTES", 1_000_000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", "0" * 20_000_000)
    assert len(buf.getvalue()) < 100_000
    r = upload(client, "bomb.xlsx", buf.getvalue())
    assert r.status_code == 400 and "unpacks to" in r.text


def test_upload_over_the_size_cap_is_refused(client, monkeypatch):
    monkeypatch.setattr("analyst_in_a_box.config.MAX_UPLOAD_BYTES", 2_000_000)
    r = upload(client, "a.csv", b"a\n" + b"1\n" * 1_500_000)
    assert r.status_code in (400, 413)


def test_formula_text_is_stored_as_text(client):
    ok(upload(client, "f.csv", "name,v\n=1+1,1\n@SUM(A1),2\n"))
    rows = ok(client.post("/api/sql", json={"sql": "SELECT name FROM f ORDER BY v"}))["rows"]
    assert rows == [["=1+1"], ["@SUM(A1)"]]


# ---- export -----------------------------------------------------------------------------------


def test_csv_export_neutralises_formulas_but_keeps_numbers():
    text = exporting.to_csv(["=bad", "n"], [["=HYPERLINK(\"http://evil\",\"x\")", -5],
                                            ["+1+1", 2.5], ["@SUM(1)", None], ["-2", 1],
                                            ["\t=1", 1], [" =1", 1], ["​=1", 1], ["fine", 1],
                                            ["اردو", 1]])
    lines = text.split("\r\n")
    assert lines[0].startswith("'=bad")
    assert "'=HYPERLINK" in lines[1] and lines[1].endswith(",-5")  # number stays a number
    assert lines[2].startswith("'+1+1") and lines[3].startswith("'@SUM")
    assert lines[4].startswith("'-2") and "'\t=1" in lines[5]
    assert lines[6].startswith("' =1") and lines[7].startswith("'​=1")
    assert lines[8].startswith("fine") and "اردو" in lines[9]


def test_export_endpoint_end_to_end(client):
    ok(upload(client, "evil.csv", "name,amt\n=cmd|' /C calc'!A0,-3\nok,4\n"))
    r = client.post("/api/export", json={"sql": "SELECT name, amt FROM evil ORDER BY amt"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith(b"\xef\xbb\xbf") and "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    assert "'=cmd|" in body and ",-3" in body and r.headers["x-rows"] == "2"
    assert client.post("/api/export", json={"sql": "DROP TABLE evil"}).status_code == 400


# ---- the audit trail --------------------------------------------------------------------------


@pytest.fixture()
def trail(client, home):
    t = ok(client.post("/api/tickets", json={"kind": "fraud_review", "title": "f", "requested_by": "Amna Khan",
                                             "customer_id": "7"}))
    ok(client.post(f"/api/tickets/{t['id']}/approve", json={"actor": "Bilal Ahmed"}))
    ok(client.post(f"/api/tickets/{t['id']}/execute", json={"actor": "Bilal Ahmed"}))
    assert ok(client.get("/api/audit/verify"))["ok"]
    return client, home / "analyst.sqlite3", t["id"]


def edit(path, sql, *args):
    c = sqlite3.connect(path)
    c.execute(sql, args)
    c.commit()
    c.close()


def test_editing_an_audit_row_is_detected(trail):
    client, db, _ = trail
    edit(db, "UPDATE audit SET actor='Mallory' WHERE id=(SELECT MIN(id) FROM audit WHERE action='ticket.created')")
    v = ok(client.get("/api/audit/verify"))
    assert not v["ok"] and v["first_bad_id"] is not None


def test_cutting_off_the_tail_is_detected(trail):
    client, db, _ = trail
    edit(db, "DELETE FROM audit WHERE id=(SELECT MAX(id) FROM audit)")
    v = ok(client.get("/api/audit/verify"))
    assert not v["ok"] and v["anchor"] == "shortened"


def test_deleting_the_whole_trail_is_detected(trail):
    client, db, _ = trail
    edit(db, "DELETE FROM audit")
    v = ok(client.get("/api/audit/verify"))
    assert not v["ok"] and v["anchor"] == "shortened"


def test_recomputing_the_whole_chain_after_an_edit_is_detected(trail):
    """The attacker edits a row and rebuilds every later hash so the chain is self-consistent."""
    client, db, _ = trail
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    prev = appdb.GENESIS
    for r in c.execute("SELECT * FROM audit ORDER BY id").fetchall():
        actor = "Mallory" if r["action"] == "ticket.created" else r["actor"]
        h = appdb._digest(prev, r["at"], actor, r["action"], r["ticket_id"], r["detail"])
        c.execute("UPDATE audit SET actor=?, prev_hash=?, hash=? WHERE id=?", (actor, prev, h, r["id"]))
        prev = h
    c.commit()
    c.close()
    v = ok(client.get("/api/audit/verify"))
    assert v["ok"] is False and v["anchor"] == "rewritten"


def test_forged_or_deleted_head_file_is_detected(trail):
    client, db, _ = trail
    head = db.with_name(db.name + ".audit-head")
    body = json.loads(head.read_text())
    body["count"] += 5
    head.write_text(json.dumps(body))
    assert ok(client.get("/api/audit/verify"))["anchor"] == "forged"
    head.unlink()
    v = ok(client.get("/api/audit/verify"))
    assert not v["ok"] and v["anchor"] == "missing"


def test_editing_the_tickets_table_directly_is_caught_by_the_cross_check(trail):
    client, db, tid = trail
    t = raise_refund(client, amount=300)
    edit(db, "UPDATE tickets SET status='approved' WHERE id=?", t["id"])  # skip both gates
    v = ok(client.get("/api/audit/verify"))
    assert not v["ok"] and any(f"ticket {t['id']} is approved but the audit" in p for p in v["problems"])
    edit(db, "UPDATE tickets SET status='pending' WHERE id=?", t["id"])
    edit(db, "UPDATE tickets SET amount=1 WHERE id=?", t["id"])
    assert any("amount" in p for p in ok(client.get("/api/audit/verify"))["problems"])
    edit(db, "UPDATE tickets SET amount=300 WHERE id=?", t["id"])
    assert ok(client.get("/api/audit/verify"))["ok"]


def test_a_forged_ledger_row_or_credit_limit_is_caught(trail):
    client, db, _ = trail
    edit(db, "INSERT INTO ledger(kind,order_id,amount,ticket_id,at) VALUES('refund','X',999,4242,'now')")
    assert any("ledger row" in p for p in ok(client.get("/api/audit/verify"))["problems"])
    edit(db, "INSERT INTO credit_limits(customer_id,limit_gbp,set_at,ticket_id) VALUES('c',9e9,'now',4242)")
    assert any("credit limit" in p for p in ok(client.get("/api/audit/verify"))["problems"])


def test_an_older_database_without_a_head_file_is_adopted_when_its_chain_is_sound(db):
    home = db
    db = home / "analyst.sqlite3"
    con = appdb.connect(db)
    appdb.audit(con, "Amna Khan", "ticket.created", None, {"x": 1})
    con.close()
    db.with_name(db.name + ".audit-head").unlink()
    appdb.init(db)
    con = appdb.connect(db)
    assert appdb.verify_audit(con)["anchor"] == "ok"
    con.close()


def test_audit_export_is_a_csv_with_hashes(trail):
    client, _, _ = trail
    r = client.get("/api/audit/export")
    assert r.status_code == 200 and "prev_hash" in r.text and "ticket.executed" in r.text


def test_login_events_are_in_the_trail(plain_client):
    plain_client.sign_in("Bilal Ahmed")
    actions = [a["action"] for a in plain_client.get("/api/audit?limit=5").json()]
    assert "auth.login" in actions
