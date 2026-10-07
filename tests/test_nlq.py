import pytest

from analyst_in_a_box import appdb, config, llm, nlq, sources


def ask(src, q, **kw):
    return nlq.answer(src, q, **kw)


def test_revenue_by_month_matches_direct_sql(src):
    r = ask(src, "Sales by month")
    assert r["ok"] and r["mode"] == "template" and r["chart"]["type"] == "line"
    direct = sources.rows_of(
        src, "SELECT substr(order_date,1,7) m, ROUND(SUM(total),2) v FROM orders "
             "WHERE status='completed' GROUP BY 1 ORDER BY 1")
    assert [tuple(x) for x in r["rows"]] == [(d["m"], d["v"]) for d in direct]


def test_top_n_and_filters(src):
    r = ask(src, "Top 3 customers in 2011")
    assert len(r["rows"]) == 3 and "'2011'" in r["sql"]
    r = ask(src, "revenue from Germany in 2011")
    assert "Germany" in r["sql"] and r["chart"]["type"] == "stat"


def test_counts_and_refunds(src):
    n = sources.rows_of(src, "SELECT COUNT(*) n FROM orders WHERE status='completed'")[0]["n"]
    assert ask(src, "How many orders")["rows"][0][0] == n
    assert ask(src, "Refunds by month")["ok"]


def test_roman_urdu(src):
    r = ask(src, "har mahine ki bikri")
    assert r["language"] == "roman-ur" and "GROUP BY" in r["sql"] and "order_date" in r["sql"]
    assert len(ask(src, "sab se zyada bikne wali 5 cheezein")["rows"]) == 5
    assert ask(src, "kitne customers hain")["rows"][0][0] > 0


def test_nonsense_is_not_guessed(src):
    r = ask(src, "hello world")
    assert not r["ok"] and r["examples"]


def test_generic_parser_on_foreign_table(home):
    appdb.init(config.app_db_path())
    con = appdb.connect(config.app_db_path())
    sources.import_upload(con, "shop.csv", b"region,units\nN,5\nN,7\nS,3\n")
    s = sources.get_source(con)
    r = ask(s, "total units by region")
    assert r["ok"] and {a: b for a, b in r["rows"]} == {"N": 12, "S": 3}
    assert ask(s, "how many rows in shop")["rows"][0][0] == 3


class FakeLLM:
    name, model = "fake", "m1"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, prompt, system="", max_tokens=600):
        return self.replies.pop(0)


def test_llm_sql_is_validated_and_repaired(src):
    fake = FakeLLM(["```sql\nSELECT nope FROM orders\n```", "```sql\nSELECT COUNT(*) AS n FROM orders\n```"])
    r = ask(src, "anything", client=fake)
    assert r["ok"] and r["mode"] == "llm" and "repaired" in r["explanation"]


def test_llm_write_is_refused_and_falls_back(src):
    r = ask(src, "sales by month", client=FakeLLM(["DROP TABLE orders", "DROP TABLE orders"]))
    assert r["ok"] and r["mode"] == "template" and "built-in parser" in r["note"]
    assert sources.rows_of(src, "SELECT COUNT(*) n FROM orders")[0]["n"] > 0


def test_llm_error_falls_back(src):
    class Down:
        name, model = "x", "y"

        def complete(self, *a, **k):
            raise llm.LLMError("down")

    assert ask(src, "sales by month", client=Down())["mode"] == "template"


def test_chart_suggestions():
    assert nlq.suggest_chart(["a"], []) is None
    assert nlq.suggest_chart(["m", "v"], [["2024-01", 1], ["2024-02", 2], ["2024-03", 3]])["type"] == "line"
    assert nlq.suggest_chart(["c", "v"], [["x", 1], ["y", 2]])["type"] == "bar"
    assert nlq.suggest_chart(["v"], [[5]])["type"] == "stat"


def test_extract_sql():
    assert nlq.extract_sql("Here:\n```sql\nSELECT 1\n```") == "SELECT 1"
    assert nlq.extract_sql("SELECT 2") == "SELECT 2"


def test_llm_off_by_default_and_key_missing_is_reported(monkeypatch):
    assert llm.get_client() is None
    monkeypatch.setenv("ANALYST_LLM", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.get_client() is None and "missing" in llm.status()["note"]
    pytest.importorskip("fastapi")


def test_relative_periods_filter_and_do_not_pick_the_dimension(src):
    r = ask(src, "top 5 products by revenue last quarter")
    assert r["ok"] and "'-Q'" in r["sql"] and len(r["rows"]) <= 5
    assert "quarter" in r["explanation"]
    # "is mahine" = this month; the thing to rank is the product, not the month
    r = ask(src, "is mahine sab se zyada bikne wali cheez")
    assert r["language"] == "roman-ur" and r["chart"]["x"] == "product"
    assert "SUBSTRING(MAX(order_date), 1, 7)" in r["sql"] or "substr(MAX(order_date), 1, 7)" in r["sql"]
