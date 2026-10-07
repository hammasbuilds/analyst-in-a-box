import math

import pytest

from analyst_in_a_box import forecasting, fraud, risk, sources


# ---- forecasting ---------------------------------------------------------------------------
def test_classify_patterns():
    assert forecasting.classify([5, 6, 5, 7, 6, 5, 6, 7] * 3)["pattern"] == "smooth"
    assert forecasting.classify([0, 0, 0, 9, 0, 0, 0, 0, 8, 0, 0, 0, 10, 0])["pattern"] == "intermittent"
    assert forecasting.classify([0, 0, 0, 0, 1])["pattern"] == "too sparse"


def test_intermittent_series_gets_croston():
    name, vals = forecasting.fit_forecast([0, 0, 4, 0, 0, 0, 5, 0, 0, 4, 0, 0] * 3, 4)
    assert name == "Croston" and len(vals) == 4 and all(v > 0 for v in vals)


def test_forecast_is_coherent_nonnegative_and_backtested(src):
    r = forecasting.run(src, horizon=4, per_category=2, method="mint_wls")
    assert r["coherent"] and not r["coherent_before_reconciliation"]
    nodes = {n["name"]: n for n in r["nodes"]}
    total = nodes["All products"]
    cats = [n for n in r["nodes"] if n["parent"] == "All products"]
    for i in range(4):
        assert math.isclose(sum(c["forecast"][i] for c in cats), total["forecast"][i], abs_tol=0.5 * len(cats))
    assert all(v >= 0 for n in r["nodes"] for v in n["forecast"])
    assert r["backtest"]["folds"] >= 1 and r["backtest"]["nodes_judged"] > 0
    assert {b["level"] for b in r["backtest"]["by_level"]} == {"total", "category", "product"}
    again = forecasting.run(src, horizon=4, per_category=2, method="mint_wls")
    assert again is r  # cached


def test_forecast_rejects_bad_settings(src):
    with pytest.raises(forecasting.ForecastError):
        forecasting.run(src, horizon=99)
    with pytest.raises(forecasting.ForecastError):
        forecasting.run(src, method="magic")


# ---- fraud ---------------------------------------------------------------------------------
def test_robust_z_and_mad():
    assert fraud.mad([1, 2, 3, 4, 100]) == 1
    assert fraud.robust_z(10, 5, 0) == 0.0  # zero MAD must not blow up
    assert fraud.robust_z(10, 5, 2) == pytest.approx(0.6745 * 2.5)


def test_every_alert_has_reasons_and_valid_score(src):
    res = fraud.screen(src)
    assert res["alerts"] and res["summary"]["alerts"] == len(res["alerts"])
    keys = [a["key"] for a in res["alerts"]]
    assert len(keys) == len(set(keys))
    for a in res["alerts"]:
        assert a["reasons"] and 20 <= a["score"] <= 100
        assert a["severity"] in ("high", "medium", "low")
    scores = [a["score"] for a in res["alerts"]]
    assert scores == sorted(scores, reverse=True)


def test_planted_outlier_is_flagged(src, tmp_path):
    """Plant a very large order in a copy of the data and check the amount rule fires."""
    import shutil
    import sqlite3

    copy = tmp_path / "planted.sqlite3"
    shutil.copy(src["location"], copy)
    con = sqlite3.connect(copy)
    cid = con.execute("SELECT customer_id FROM customers LIMIT 1").fetchone()[0]
    con.execute("INSERT INTO orders VALUES('PLANT1',?,'2011-06-01 10:00:00','completed',1,250000)", (cid,))
    con.execute("INSERT INTO order_items VALUES('PLANT1','M',1,250000,250000)")
    con.commit()
    con.close()
    res = fraud.screen({"kind": "sqlite", "location": str(copy)})
    a = next(x for x in res["alerts"] if x["order_id"] == "PLANT1")
    codes = {r["code"] for r in a["reasons"]}
    assert "amount_outlier" in codes and "manual_line" in codes and a["severity"] == "high"


# ---- risk ----------------------------------------------------------------------------------
def test_split_is_deterministic_and_roughly_80_15_5():
    ids = [str(i) for i in range(5000)]
    s = [risk.split_of(i) for i in ids]
    assert s == [risk.split_of(i) for i in ids]
    assert 0.77 < s.count("train") / 5000 < 0.83 and 0.12 < s.count("validation") / 5000 < 0.18


def test_scorecard_decisions_reasons_and_fairness(src):
    r = risk.build(src)
    assert r["metrics"]["train"]["gini"] > 0.2
    assert set(r["metrics"]) == {"train", "validation", "test"}
    assert r["fairness"]["country_group"]["groups"] and "worst_disparate_impact" in r["fairness"]["country_group"]
    c = next(iter(r["_customers"].values()))
    for c in r["_customers"].values():
        assert (c["decision"] == "APPROVE") == (c["score"] >= r["score_cutoff"])
    low = min(r["_customers"].values(), key=lambda c: c["score"])
    assert low["decision"] == "DECLINE" and low["reasons"]
    assert "_customers" not in risk.public(r)
    lst = risk.customer_list(r, limit=5, decision="decline")
    assert len(lst) <= 5 and all(x["decision"] == "DECLINE" for x in lst)


def test_risk_features_use_only_the_past(src):
    early = risk.features_asof(src, "2010-06-01 00:00:00")
    late = risk.features_asof(src, "2011-06-01 00:00:00")
    assert set(early) < set(late)
    for cid, f in early.items():
        assert f["orders"] <= late[cid]["orders"] and f["spend"] <= late[cid]["spend"] + 1e-6


def test_risk_validates_settings(src):
    with pytest.raises(risk.RiskError):
        risk.build(src, approve_rate=0.1)


def test_non_canonical_source_is_refused(home):
    from analyst_in_a_box import appdb, config
    appdb.init(config.app_db_path())
    con = appdb.connect(config.app_db_path())
    sources.import_upload(con, "a.csv", b"x,y\n1,2\n")
    s = sources.get_source(con)
    with pytest.raises(risk.RiskError):
        risk.build(s)
    with pytest.raises(forecasting.ForecastError):
        forecasting.run(s)
