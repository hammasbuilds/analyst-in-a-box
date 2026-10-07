"""Offline end-to-end demo: no server, no model, no network. Uses a throwaway ANALYST_HOME.

    uv run python demo.py
"""

import os
import tempfile
from pathlib import Path

os.environ["ANALYST_HOME"] = tempfile.mkdtemp(prefix="aib-demo-")

from analyst_in_a_box import (  # noqa: E402
    appdb,
    config,
    forecasting,
    fraud,
    nlq,
    risk,
    sources,
    workflows,
)

appdb.init(config.app_db_path())
con = appdb.connect(config.app_db_path())
sources.ensure_builtin(con)
src = sources.get_source(con)
size = Path(src["location"]).stat().st_size / 1e6

n = lambda sql: sources.rows_of(src, sql)[0][0]  # noqa: E731
print("Input : UCI Online Retail II, seeded sample of 2,000 customers (CC BY 4.0)")
print(f"        {n('SELECT COUNT(*) FROM order_items'):,} order lines -> {n('SELECT COUNT(*) FROM orders'):,} orders, "
      f"{n('SELECT COUNT(*) FROM customers'):,} customers, {n('SELECT COUNT(*) FROM products'):,} products "
      f"({size:.0f} MB SQLite), {n('SELECT MIN(order_date) FROM orders')[:10]} to {n('SELECT MAX(order_date) FROM orders')[:10]}")
print()
print("Ask your data (no model)")
for q in ["Top 5 products by revenue in 2011", "har mahine ki bikri", "kitne customers hain", "Refunds by month"]:
    r = nlq.answer(src, q)
    first = r["rows"][0] if r["rows"] else None
    print(f"  {q!r:42} [{r['language']}] -> {len(r['rows'])} rows, first {first}")
bad = None
try:
    sources.query(src, "DELETE FROM orders")
except Exception as exc:
    bad = exc
print(f"  write attempt 'DELETE FROM orders' -> {bad}")
print()
f = forecasting.run(src, horizon=8, per_category=3)
b = f["backtest"]
print(f"Forecast ({f['horizon']} weeks, {f['method']}, {f['history_weeks']} weeks of history)")
print(f"  coherent before reconciling: {f['coherent_before_reconciliation']}; after: {f['coherent']}")
print(f"  series beating seasonal naive in the backtest: {b['nodes_beating_seasonal_naive']} of {b['nodes_judged']} "
      f"({b['folds']} origins)")
for lv in b["by_level"]:
    print(f"  {lv['level']:9} MAE seasonal naive {lv['seasonal_naive']:>9,.1f} | base {lv['base']:>9,.1f} | "
          f"bottom-up {lv['bottom_up']:>9,.1f} | mint_wls {lv['mint_wls']:>9,.1f}")
print()
fr = fraud.screen(src)
s = fr["summary"]
print(f"Fraud and anomalies: {s['orders_screened']:,} orders screened -> {s['alerts']} alerts "
      f"{s['by_severity']} {s['by_kind']}")
top = fr["alerts"][0]
print(f"  top: {top['subject']} score {top['score']}: {top['reasons'][0]['text']}")
print()
r = risk.build(src)
m = r["metrics"]
print(f"Customer risk ({r['outcome']})")
print(f"  {r['snapshot_customers']} customers, bad rate {r['base_bad_rate']:.1%}; Gini train {m['train']['gini']:.3f} "
      f"validation {m['validation']['gini']:.3f} test {m['test']['gini']:.3f} "
      f"(test: {m['test']['customers']} customers, {m['test']['bad']} bad)")
fa = r["fairness"]["country_group"]
print(f"  fairness, UK vs Other: disparate impact {fa['worst_disparate_impact']:.3f}, four-fifths flag {fa['four_fifths_rule_flag']}")
print(f"  scored now: {r['live']['customers']} customers, {r['live']['approved']} approve at cut-off {r['score_cutoff']}")
print()
t = workflows.create(con, kind="credit_limit_change", title="demo", requested_by="Amna Khan",
                     customer_id=next(iter(r["_customers"])), payload={"new_limit": 9000})
print(f"Workflow: credit limit to £9,000 needs gates {t['gates']}")
workflows.approve(con, t["id"], "Bilal Ahmed")
t = workflows.approve(con, t["id"], "Omar Siddiqui")
t = workflows.execute(con, t["id"], "Omar Siddiqui")
print(f"  status {t['status']}; audit entries {len(t['audit'])}; chain {appdb.verify_audit(con)['ok']}")
