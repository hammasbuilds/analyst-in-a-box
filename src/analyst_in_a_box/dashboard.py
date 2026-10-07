"""KPI cards and charts for the canonical schema. "Now" is the newest order in the data, so a
dataset that ends in 2011 still gets a meaningful last-30-days view."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from . import sources


def _pct(now: float, before: float) -> float | None:
    return round((now - before) / before * 100, 1) if before else None


def _weekly(src: dict[str, Any], hi: datetime, weeks: int = 13) -> dict[str, list[float]]:
    """Thirteen weekly points ending at the newest order, oldest first, for the KPI sparklines."""
    lo = (hi - timedelta(days=weeks * 7)).strftime("%Y-%m-%d %H:%M:%S")
    got = {r["b"]: r for r in sources.rows_of(
        src,
        "SELECT CAST((julianday(?) - julianday(order_date)) / 7 AS INTEGER) b, "
        "COALESCE(SUM(total),0) net, "
        "COALESCE(SUM(CASE WHEN status='completed' THEN total END),0) rev, "
        "COUNT(CASE WHEN status='completed' THEN 1 END) n, "
        "COALESCE(SUM(CASE WHEN status='cancelled' THEN -total END),0) ref, "
        "COUNT(DISTINCT CASE WHEN status='completed' THEN customer_id END) cust "
        "FROM orders WHERE order_date > ? GROUP BY b",
        (hi.strftime("%Y-%m-%d %H:%M:%S"), lo))}
    out: dict[str, list[float]] = {k: [] for k in ("revenue", "orders", "aov", "customers", "refund_rate")}
    for b in range(weeks - 1, -1, -1):
        r = got.get(b)
        rev, n = (r["rev"], r["n"]) if r else (0.0, 0)
        out["revenue"].append(round(r["net"], 2) if r else 0.0)
        out["orders"].append(n)
        out["aov"].append(round(rev / n, 2) if n else 0.0)
        out["customers"].append(r["cust"] if r else 0)
        out["refund_rate"].append(round(r["ref"] / rev * 100, 2) if r and rev else 0.0)
    return out


def build(src: dict[str, Any], *, open_alerts: int | None, tickets: dict[str, Any]) -> dict[str, Any]:
    if not sources.is_canonical(src):
        return {"canonical": False,
                "message": "This source is not in the orders/customers shape, so the KPI dashboard "
                           "is not available. Use Ask your data, or map an uploaded sheet of order "
                           "lines on the Data page."}
    hi = datetime.fromisoformat(
        sources.rows_of(src, "SELECT MAX(order_date) d FROM orders")[0]["d"])

    def window(days_from: int, days_to: int) -> dict[str, float]:
        a = (hi - timedelta(days=days_from)).strftime("%Y-%m-%d %H:%M:%S")
        b = (hi - timedelta(days=days_to)).strftime("%Y-%m-%d %H:%M:%S")
        r = sources.rows_of(
            src,
            "SELECT COALESCE(SUM(total),0) net, "
            "COALESCE(SUM(CASE WHEN status='completed' THEN total END),0) rev, "
            "COUNT(CASE WHEN status='completed' THEN 1 END) n, "
            "COALESCE(SUM(CASE WHEN status='cancelled' THEN -total END),0) ref, "
            "COUNT(DISTINCT CASE WHEN status='completed' THEN customer_id END) cust "
            "FROM orders WHERE order_date > ? AND order_date <= ?", (a, b))[0]
        return {"revenue": r["net"], "gross": r["rev"], "orders": r["n"], "refunds": r["ref"], "customers": r["cust"]}

    cur, prev = window(30, 0), window(60, 30)
    q90 = window(90, 0)
    aov = cur["gross"] / cur["orders"] if cur["orders"] else 0.0
    aov_prev = prev["gross"] / prev["orders"] if prev["orders"] else 0.0
    spark = _weekly(src, hi)
    cards = [
        {"key": "revenue", "label": "Net revenue, last 30 days", "value": round(cur["revenue"], 2),
         "unit": "gbp", "change_pct": _pct(cur["revenue"], prev["revenue"]),
         "spark": spark["revenue"]},
        {"key": "orders", "label": "Orders, last 30 days", "value": cur["orders"], "unit": "count",
         "change_pct": _pct(cur["orders"], prev["orders"]), "spark": spark["orders"]},
        {"key": "aov", "label": "Average completed-order value", "value": round(aov, 2), "unit": "gbp",
         "change_pct": _pct(aov, aov_prev), "spark": spark["aov"]},
        {"key": "customers", "label": "Active customers, 90 days", "value": q90["customers"],
         "unit": "count", "change_pct": None, "spark": spark["customers"]},
        {"key": "refund_rate", "label": "Refund rate, 90 days (of gross sales)",
         "value": round(q90["refunds"] / q90["gross"] * 100, 1) if q90["gross"] else 0.0,
         "unit": "pct", "change_pct": None, "spark": spark["refund_rate"]},
        {"key": "alerts", "label": "Open fraud and anomaly alerts", "value": open_alerts,
         "unit": "count", "change_pct": None},
        {"key": "tickets", "label": "Tickets awaiting approval", "value": tickets["awaiting_approval"],
         "unit": "count", "change_pct": None},
    ]
    monthly = [[r["m"], round(r["v"], 2)] for r in sources.rows_of(
        src, "SELECT substr(order_date,1,7) m, SUM(total) v FROM orders "
             "GROUP BY 1 ORDER BY 1")]
    # the newest month is partial; say so rather than let it look like a collapse
    last_month_partial = hi.day < 28
    countries = [[r["c"], round(r["v"], 2)] for r in sources.rows_of(
        src, "SELECT c.country c, SUM(o.total) v FROM orders o JOIN customers c "
             "ON c.customer_id=o.customer_id GROUP BY 1 "
             "ORDER BY 2 DESC LIMIT 8")]
    products = [[r["p"], round(r["v"], 2)] for r in sources.rows_of(
        src, "SELECT p.description p, SUM(oi.line_total) v FROM order_items oi "
             "JOIN orders o ON o.order_id=oi.order_id JOIN products p ON p.stock_code=oi.stock_code "
             "WHERE p.is_product=1 GROUP BY oi.stock_code ORDER BY 2 DESC LIMIT 8")]
    cats = [[r["c"], round(r["v"], 2)] for r in sources.rows_of(
        src, "SELECT p.category c, SUM(oi.line_total) v FROM order_items oi "
             "JOIN orders o ON o.order_id=oi.order_id JOIN products p ON p.stock_code=oi.stock_code "
             "WHERE p.is_product=1 GROUP BY 1 ORDER BY 2 DESC")]
    return {
        "canonical": True, "as_of": hi.strftime("%Y-%m-%d"), "cards": cards,
        "charts": {"monthly_revenue": monthly, "revenue_by_country": countries,
                   "top_products": products, "revenue_by_category": cats},
        "last_month_partial": last_month_partial,
        "totals": {r["k"]: r["v"] for r in sources.rows_of(
            src, "SELECT 'orders' k, COUNT(*) v FROM orders UNION ALL "
                 "SELECT 'customers', COUNT(*) FROM customers UNION ALL "
                 "SELECT 'products', COUNT(*) FROM products WHERE is_product=1")},
    }
