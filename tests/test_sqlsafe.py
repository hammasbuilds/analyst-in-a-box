import sqlite3

import pytest

from analyst_in_a_box import sqlsafe


def v(sql, **kw):
    return sqlsafe.validate(sql, max_rows=100, **kw)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE orders", "DELETE FROM orders", "UPDATE orders SET total=0",
        "INSERT INTO orders VALUES (1)", "SELECT 1; DROP TABLE orders",
        "WITH g AS (DELETE FROM orders RETURNING *) SELECT * FROM g",
        "PRAGMA writable_schema=1", "ATTACH DATABASE 'x.db' AS x", "CREATE TABLE t(a)",
        "SELECT load_extension('x')", "SELECT * FROM sqlite_master",
    ],
)
def test_writes_and_escapes_are_refused(sql):
    assert not v(sql).ok


def test_string_literal_containing_keywords_is_fine():
    assert v("SELECT 'delete from orders' AS s").ok


def test_limit_is_injected_and_clamped():
    assert "LIMIT 100" in v("SELECT a FROM t").rewritten
    assert "LIMIT 100" in v("SELECT a FROM t LIMIT 99999").rewritten
    assert "LIMIT 5" in v("SELECT a FROM t LIMIT 5").rewritten


def test_unknown_table_reports_available():
    r = v("SELECT * FROM nope", known_tables={"orders"})
    assert not r.ok and "orders" in r.reason


def test_unterminated_string_does_not_crash():
    assert not v("SELECT 'abc").ok


def test_connection_layer_refuses_writes_even_without_the_validator(sample_file):
    """Layer 3 on its own: skip validate() and send writes straight to the runner."""
    for sql in [
        "DELETE FROM orders", "DROP TABLE orders", "UPDATE orders SET total=0",
        "CREATE TABLE x(a)", "ATTACH DATABASE ':memory:' AS m", "PRAGMA query_only=OFF",
    ]:
        with pytest.raises(sqlsafe.QueryError):
            sqlsafe.run_sqlite(sample_file, sql, max_rows=10, timeout_s=5)
    con = sqlite3.connect(sample_file)
    assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] > 0


def test_runaway_query_times_out(sample_file):
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(sqlsafe.QueryError, match="timed out"):
        sqlsafe.run_sqlite(sample_file, sql, max_rows=10, timeout_s=0.5)


def test_row_cap_sets_truncated(sample_file):
    r = sqlsafe.run_sqlite(sample_file, "SELECT * FROM order_items", max_rows=7, timeout_s=5)
    assert len(r["rows"]) == 7 and r["truncated"]
