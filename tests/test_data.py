import io
import sqlite3

import pytest

from analyst_in_a_box import appdb, canon, config, sources


def test_categorise_and_product_codes():
    assert canon.categorise("WHITE HANGING HEART T-LIGHT HOLDER") == "Candles & lighting"
    assert canon.categorise("CHRISTMAS TREE") == "Christmas"
    assert canon.categorise("ZZZ") == "Other gifts"
    assert canon.is_product_code("85123A") and not canon.is_product_code("POST")


def test_sample_schema_and_invariants(src):
    assert sources.rows_of(src, "SELECT COUNT(*) n FROM orders")[0]["n"] > 1000
    bad = sources.rows_of(src, "SELECT COUNT(*) n FROM orders WHERE status='cancelled' AND total>0")
    assert bad[0]["n"] == 0
    pay = sources.rows_of(src, "SELECT ROUND(SUM(amount),2) a FROM payments")[0]["a"]
    tot = sources.rows_of(src, "SELECT ROUND(SUM(total),2) a FROM orders")[0]["a"]
    assert pay == tot
    assert sources.is_canonical(src)


def _row(inv, date, cust):
    return {"invoice": inv, "stock_code": "10001", "description": "A", "quantity": 2,
            "date": date, "price": 3.0, "customer": cust, "country": "UK"}


def test_loader_reports_skipped_rows(tmp_path):
    con = sqlite3.connect(tmp_path / "x.db")
    rows = [_row("1", "2011-01-02", "7.0"), _row("2", "garbage", "7"), _row("3", "2011-01-03", "")]
    counts = canon.build_from_flat(con, rows)
    assert counts["order_lines"] == 1 and counts["rows_skipped"] == 2
    assert con.execute("SELECT customer_id FROM customers").fetchone()[0] == "7"
    with pytest.raises(ValueError):
        canon.build_from_flat(con, rows[1:])


def _app_con(home):
    appdb.init(config.app_db_path())
    return appdb.connect(config.app_db_path())


def test_csv_upload_infers_types(home):
    con = _app_con(home)
    r = sources.import_upload(
        con, "My Sales.csv", b"Day,Qty,Price,Name\n2024-01-01,3,1.5,a\n2024-01-02,4,2,b\n")
    t = r["tables"][0]
    assert t["table"] == "my_sales"
    assert t["columns"] == {"day": "TEXT", "qty": "INTEGER", "price": "REAL", "name": "TEXT"}
    total = sqlite3.connect(config.uploads_db_path()).execute("SELECT SUM(qty) FROM my_sales")
    assert total.fetchone()[0] == 7
    assert appdb.get_setting(con, "active_source") == str(r["source_id"])


def test_excel_upload_one_table_per_sheet(home):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "Jan"
    wb.active.append(["item", "amount"])
    wb.active.append(["x", 10])
    ws = wb.create_sheet("Feb")
    ws.append(["item", "amount"])
    ws.append(["y", 20])
    buf = io.BytesIO()
    wb.save(buf)
    r = sources.import_upload(_app_con(home), "book.xlsx", buf.getvalue())
    assert {t["table"] for t in r["tables"]} == {"book_jan", "book_feb"}


def test_bad_uploads_are_refused(home):
    con = _app_con(home)
    with pytest.raises(sources.SourceError):
        sources.import_upload(con, "x.pdf", b"%PDF")
    with pytest.raises(sources.SourceError):
        sources.import_upload(con, "x.csv", b"only,a,header\n")


def test_map_sales_builds_canonical_tables(home):
    con = _app_con(home)
    lines = ["inv,sku,desc,q,d,p,cust"]
    for i in range(30):
        lines.append(f"{i},1000{i % 3},Item {i % 3},{i % 4 + 1},2024-01-{i % 28 + 1:02d},2.5,c{i % 5}")
    r = sources.import_upload(con, "lines.csv", "\n".join(lines).encode())
    s = sources.get_source(con, r["source_id"])
    counts = sources.map_sales(s, "lines", {"invoice": "inv", "stock_code": "sku", "description": "desc",
                                            "quantity": "q", "date": "d", "price": "p", "customer": "cust"})
    assert counts["orders"] == 30 and counts["customers"] == 5
    assert sources.is_canonical(s)
    with pytest.raises(sources.SourceError):
        sources.map_sales(s, "lines", {"invoice": "inv"})


def test_mask_url():
    assert sources.mask_url("postgresql://u:secret@h/db") == "postgresql://u:***@h/db"
