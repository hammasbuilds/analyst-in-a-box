"""Every endpoint, through HTTP."""

import io

import openpyxl


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_health_state_and_page(client):
    assert ok(client.get("/api/health")) == {"ok": True}
    s = ok(client.get("/api/state"))
    assert s["canonical"] and not s["llm"]["configured"] and len(s["users"]) == 4 and s["policy"]
    page = client.get("/")
    assert page.status_code == 200 and "Analyst-in-a-Box" in page.text
    for f in ("app.js", "charts.js", "style.css"):
        assert client.get(f"/static/{f}").status_code == 200


def test_sources_schema_upload_map(client, tmp_path):
    srcs = ok(client.get("/api/sources"))
    assert srcs[0]["kind"] == "sample" and srcs[0]["active"]
    sch = ok(client.get("/api/schema"))
    assert {t["name"] for t in sch["tables"]} == {"customers", "products", "orders", "order_items", "payments"}
    assert ok(client.get("/api/canonical-tables"))
    # connect a second SQLite file, switch, remove
    import shutil
    other = tmp_path / "other.sqlite3"
    shutil.copy(srcs[0]["location"], other)
    new = ok(client.post("/api/sources", json={"kind": "sqlite", "location": str(other), "name": "copy"}))
    assert ok(client.get("/api/state"))["source"]["id"] == new["id"]
    ok(client.post(f"/api/sources/{srcs[0]['id']}/activate"))
    ok(client.delete(f"/api/sources/{new['id']}"))
    assert client.delete(f"/api/sources/{srcs[0]['id']}").status_code == 400  # sample is permanent
    assert client.post("/api/sources", json={"kind": "sqlite", "location": "nope.db"}).status_code == 400
    assert client.post("/api/sources", json={"kind": "postgres", "location": "mysql://x"}).status_code == 400
    # upload csv then map it to the business tables
    lines = ["inv,sku,q,d,p,cust"] + [
        f"{i},1000{i % 3},{i % 4 + 1},2024-01-{i % 28 + 1:02d},2.5,c{i % 5}" for i in range(20)]
    up = ok(client.post("/api/upload", files={"file": ("lines.csv", "\n".join(lines))}))
    assert up["tables"][0]["rows"] == 20
    fields = ok(client.get("/api/sales-fields"))
    assert "customer" in fields
    m = ok(client.post(f"/api/sources/{up['source_id']}/map-sales", json={
        "table": "lines", "mapping": {"invoice": "inv", "stock_code": "sku", "quantity": "q",
                                      "date": "d", "price": "p", "customer": "cust"}}))
    assert m["orders"] == 20
    assert ok(client.get("/api/schema"))["canonical"]
    assert client.post("/api/upload", files={"file": ("x.pdf", b"%PDF")}).status_code == 400
    wb = openpyxl.Workbook()
    wb.active.append(["a", "b"])
    wb.active.append([1, 2])
    buf = io.BytesIO()
    wb.save(buf)
    assert ok(client.post("/api/upload", files={"file": ("w.xlsx", buf.getvalue())}))["tables"]


def test_ask_sql_history_examples(client):
    r = ok(client.post("/api/ask", json={"question": "revenue by country"}))
    assert r["ok"] and r["mode"] == "template" and r["rows"]
    bad = ok(client.post("/api/ask", json={"question": "zzz qqq"}))
    assert not bad["ok"]
    assert client.post("/api/sql", json={"sql": "DROP TABLE orders"}).status_code == 400
    assert client.post("/api/sql", json={"sql": "SELECT 1; SELECT 2"}).status_code == 400
    edited = ok(client.post("/api/sql", json={"sql": "SELECT COUNT(*) AS n FROM orders"}))
    assert edited["mode"] == "edited" and edited["rows"][0][0] > 0
    assert len(ok(client.get("/api/ask/history"))) >= 3
    assert ok(client.get("/api/ask/examples"))
    assert ok(client.post("/api/ask", json={"question": ""}))["ok"] is False
    # writes never happened
    assert ok(client.post("/api/sql", json={"sql": "SELECT COUNT(*) AS n FROM orders"}))["rows"][0][0] > 1000


def test_dashboard(client):
    d = ok(client.get("/api/dashboard"))
    assert d["canonical"] and len(d["cards"]) == 7 and d["charts"]["monthly_revenue"]
    rev = next(c for c in d["cards"] if c["key"] == "revenue")["value"]
    assert rev > 0


def test_forecast_endpoint(client):
    r = ok(client.get("/api/forecast?horizon=4&per_category=2"))
    assert r["coherent"] and r["backtest"]["by_level"]
    assert client.get("/api/forecast?horizon=99").status_code == 400
    assert client.get("/api/forecast?method=x").status_code == 400


def test_alerts_dismiss_ticket(client):
    a = ok(client.get("/api/alerts?limit=500"))
    assert a["summary"]["alerts"] > 0 and a["alerts"]
    assert ok(client.get("/api/alerts?severity=high&kind=order"))["matching"] >= 0
    order_alert = next(x for x in a["alerts"] if x["kind"] == "order")
    day_alert = next((x for x in a["alerts"] if x["kind"] == "day"), None)
    t = ok(client.post(f"/api/alerts/{order_alert['key']}/ticket", json={"requested_by": "Amna Khan"}))
    assert t["kind"] == "fraud_review" and t["order_id"] == order_alert["order_id"]
    again = ok(client.get("/api/alerts?status=ticketed"))
    assert any(x["key"] == order_alert["key"] for x in again["alerts"])
    other = next(x for x in a["alerts"] if x["key"] != order_alert["key"] and x["kind"] != "day")
    ok(client.post(f"/api/alerts/{other['key']}/dismiss", json={"actor": "Bilal Ahmed", "note": "fine"}))
    assert not any(x["key"] == other["key"] for x in ok(client.get("/api/alerts"))["alerts"])
    if day_alert:
        assert client.post(f"/api/alerts/{day_alert['key']}/ticket",
                           json={"requested_by": "Amna Khan"}).status_code == 400
    assert client.post("/api/alerts/nope:1/dismiss", json={"actor": "Bilal Ahmed"}).status_code == 400
    # executing the fraud review confirms the alert and writes a hold
    ok(client.post(f"/api/tickets/{t['id']}/approve", json={"actor": "Bilal Ahmed"}))
    ok(client.post(f"/api/tickets/{t['id']}/execute", json={"actor": "Bilal Ahmed"}))
    assert any(x["key"] == order_alert["key"] for x in ok(client.get("/api/alerts?status=confirmed"))["alerts"])
    assert ok(client.get("/api/ledger"))["ledger"][0]["kind"] == "hold"


def test_risk_endpoints(client):
    r = ok(client.get("/api/risk"))
    assert r["metrics"]["validation"]["gini"] is not None and "_customers" not in r
    lst = ok(client.get("/api/risk/customers?limit=3&decision=DECLINE"))
    assert len(lst) == 3
    c = ok(client.get(f"/api/risk/customer/{lst[0]['customer_id']}"))
    assert c["decision"] == "DECLINE" and c["points"] and c["credit_limit"] is None
    assert client.get("/api/risk/customer/nobody").status_code == 400
    assert client.get("/api/risk?approve_rate=0.1").status_code == 400


def test_ticket_lifecycle_over_http(client):
    order = ok(client.post("/api/sql", json={
        "sql": "SELECT order_id, customer_id, total FROM orders WHERE status='completed' AND total BETWEEN 60 AND 90 LIMIT 1"}))
    oid, cid, total = order["rows"][0]
    t = ok(client.post("/api/tickets", json={"kind": "refund", "title": "damaged", "requested_by": "Amna Khan",
                                             "customer_id": cid, "order_id": oid, "amount": 20}))
    tid = t["id"]
    assert t["gates"] == ["manager"]
    assert client.post(f"/api/tickets/{tid}/approve", json={"actor": "Amna Khan"}).status_code == 400
    assert client.post(f"/api/tickets/{tid}/execute", json={"actor": "Bilal Ahmed"}).status_code == 400
    assert ok(client.post(f"/api/tickets/{tid}/approve", json={"actor": "Bilal Ahmed", "comment": "ok"}))["status"] == "approved"
    assert ok(client.post(f"/api/tickets/{tid}/execute", json={"actor": "Sana Malik"}))["status"] == "executed"
    detail = ok(client.get(f"/api/tickets/{tid}"))
    assert [a["action"] for a in detail["audit"]] == [
        "ticket.created", "ticket.approved", "ticket.fully_approved", "ticket.executed"]
    assert ok(client.get("/api/tickets?status=executed"))["tickets"][0]["id"] == tid
    assert ok(client.get("/api/tickets"))["summary"]["by_status"]["executed"] == 1
    # too much for the order
    assert client.post("/api/tickets", json={"kind": "refund", "title": "x", "requested_by": "Amna Khan",
                                             "customer_id": cid, "order_id": oid, "amount": total}).status_code == 400
    # reject and cancel
    t2 = ok(client.post("/api/tickets", json={"kind": "fraud_review", "title": "f", "requested_by": "Amna Khan", "order_id": oid}))
    assert ok(client.post(f"/api/tickets/{t2['id']}/reject", json={"actor": "Bilal Ahmed", "comment": "no"}))["status"] == "rejected"
    t3 = ok(client.post("/api/tickets", json={"kind": "fraud_review", "title": "f", "requested_by": "Amna Khan", "order_id": oid}))
    assert ok(client.post(f"/api/tickets/{t3['id']}/cancel", json={"actor": "Amna Khan"}))["status"] == "cancelled"
    assert client.get("/api/tickets/9999").status_code == 400
    log = ok(client.get("/api/audit?limit=5"))
    assert len(log) == 5
    assert ok(client.get("/api/audit/verify"))["ok"]


def test_credit_limit_ticket_uses_server_side_risk_decision(client):
    declined = ok(client.get("/api/risk/customers?limit=1&decision=DECLINE"))[0]["customer_id"]
    # the caller cannot claim APPROVE: the server computes risk_decision itself
    t = ok(client.post("/api/tickets", json={
        "kind": "credit_limit_change", "title": "cl", "requested_by": "Amna Khan", "customer_id": declined,
        "payload": {"new_limit": 800, "risk_decision": "APPROVE"}}))
    assert t["payload"]["risk_decision"] == "DECLINE"
    assert t["gates"] == ["manager", "owner"]
    ok(client.post(f"/api/tickets/{t['id']}/approve", json={"actor": "Bilal Ahmed"}))
    ok(client.post(f"/api/tickets/{t['id']}/approve", json={"actor": "Omar Siddiqui"}))
    ok(client.post(f"/api/tickets/{t['id']}/execute", json={"actor": "Omar Siddiqui"}))
    assert ok(client.get("/api/ledger"))["credit_limits"][0]["limit_gbp"] == 800
    assert ok(client.get(f"/api/risk/customer/{declined}"))["credit_limit"]["limit_gbp"] == 800


def test_home_example_is_not_logged_but_every_other_question_is(client):
    before = len(ok(client.get("/api/ask/history")))
    q = "top 5 products by revenue last quarter"
    r = ok(client.post("/api/ask", json={"question": q, "preview": True}))
    assert r["ok"] and r["elapsed_ms"] >= 0
    assert len(ok(client.get("/api/ask/history"))) == before
    # the flag cannot be used to hide an arbitrary question
    ok(client.post("/api/ask", json={"question": "Top 3 customers in 2011", "preview": True}))
    assert len(ok(client.get("/api/ask/history"))) == before + 1


def test_dashboard_cards_carry_weekly_sparklines(client):
    cards = {c["key"]: c for c in ok(client.get("/api/dashboard"))["cards"]}
    for k in ("revenue", "orders", "aov", "customers", "refund_rate"):
        assert len(cards[k]["spark"]) == 13
    assert sum(cards["orders"]["spark"]) > 0


def test_cache_busting_and_revalidation(client):
    import re

    from analyst_in_a_box import app as appmod

    page = client.get("/")
    assert page.headers["cache-control"] == "no-cache"
    urls = re.findall(r'(?:src|href)="(/static/[^"]+)"', page.text)
    css = [u for u in urls if u.endswith(".css") or ".css?" in u]
    assert css and all("?v=" in u for u in urls if "/fonts/" not in u)
    assert "/static/fonts/manrope-latin.woff2" in page.text  # fonts stay unversioned (the CSS requests the same URL)
    asset = client.get(css[0])
    assert asset.status_code == 200 and asset.headers["cache-control"] == "no-cache"
    # the version follows the content: change a file, the id changes
    before = appmod._build_id()
    f = appmod.STATIC / "style.css"
    orig = f.read_bytes()
    try:
        f.write_bytes(orig + b"\n/* bump */")
        assert appmod._build_id() != before and appmod._build_id() in client.get("/").text
    finally:
        f.write_bytes(orig)
    assert appmod._build_id() == before


def test_paste_csv_matches_file_upload(client):
    text = "sku;qty;price\nA1;2;1.50\nB2;5;3.25\n"
    pasted = ok(client.post("/api/upload-text", json={"name": "My Sales!", "text": text}))
    assert pasted["tables"][0]["table"] == "my_sales" and pasted["tables"][0]["rows"] == 2
    filed = client.post("/api/upload", files={"file": ("my_sales2.csv", text.encode())}).json()
    assert filed["tables"][0]["columns"] == pasted["tables"][0]["columns"]
    assert ok(client.get("/api/state"))["source"]["kind"] == "upload"


def test_paste_csv_errors_are_clear(client):
    assert client.post("/api/upload-text", json={"name": "x", "text": "   \n"}).status_code == 400
    r = client.post("/api/upload-text", json={"name": "x", "text": "only,a,header\n"})
    assert r.status_code == 400 and r.json()["detail"]


def test_paste_requires_sign_in(plain_client):
    assert plain_client.post("/api/upload-text", json={"name": "x", "text": "a\n1\n"}).status_code == 401
