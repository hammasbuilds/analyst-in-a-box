"""Fraud and anomaly screening with a reason for every flag.

Robust statistics (median and MAD, the 0.6745 scaling) and the "say why in one sentence" alert
text come from the ideas in incident-copilot's metrics/anomaly module; the rules are original,
informed by the general shape of card-and-order fraud rules (amount, velocity, duplicates, refunds
against purchases, manual adjustments). No data from any private repo is used.

There are no fraud labels in the shipped data, so a flag is a prompt for a person to look, not a
finding. Each alert carries its rules, their weights and the numbers behind them.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from . import sources

MAD_SCALE = 0.6745
Z_ORDER, Z_REFUND, Z_QTY, Z_DAY = 3.5, 3.5, 6.0, 4.0
MANUAL_CODES = {"M", "ADJUST", "ADJUST2", "D", "BANK CHARGES", "CRUK", "TEST001"}
SEVERITY = [(50, "high"), (30, "medium"), (20, "low")]


def mad(values: list[float]) -> float:
    med = statistics.median(values)
    return statistics.median(abs(v - med) for v in values)


def robust_z(x: float, med: float, m: float) -> float:
    """Modified z-score. A zero MAD (more than half the values identical) gives 0, not infinity."""
    return MAD_SCALE * (x - med) / m if m > 0 else 0.0


def _sev(score: float) -> str | None:
    for cut, name in SEVERITY:
        if score >= cut:
            return name
    return None


def _gbp(x: float) -> str:
    return f"£{x:,.2f}"


def screen(src: dict[str, Any]) -> dict[str, Any]:
    """All alerts for a canonical SQLite source, highest score first."""
    orders = sources.rows_of(
        src, "SELECT order_id, customer_id, order_date, status, items, total FROM orders "
             "ORDER BY order_date")
    if not orders:
        return {"alerts": [], "summary": {"orders_screened": 0}}
    lines = sources.rows_of(
        src,
        "SELECT oi.order_id, oi.stock_code, p.description, p.is_product, oi.quantity, "
        "oi.unit_price, oi.line_total FROM order_items oi "
        "LEFT JOIN products p ON p.stock_code = oi.stock_code")
    by_order: dict[str, list] = defaultdict(list)
    prod_qty: dict[str, list[float]] = defaultdict(list)
    prod_price: dict[str, list[float]] = defaultdict(list)
    for ln in lines:
        by_order[ln["order_id"]].append(ln)
        if ln["is_product"] and ln["quantity"] > 0:
            prod_qty[ln["stock_code"]].append(float(ln["quantity"]))
            if ln["unit_price"] > 0:
                prod_price[ln["stock_code"]].append(float(ln["unit_price"]))
    qty_stats = {c: (statistics.median(v), mad(v)) for c, v in prod_qty.items() if len(v) >= 10}
    price_med = {c: statistics.median(v) for c, v in prod_price.items() if len(v) >= 5}

    done = [o for o in orders if o["status"] == "completed" and o["total"] > 0]
    canc = [o for o in orders if o["status"] == "cancelled"]
    logs = [math.log(o["total"]) for o in done]
    med_t, mad_t = statistics.median(logs), mad(logs)
    clogs = [math.log(-o["total"]) for o in canc if o["total"] < 0]
    med_c, mad_c = (statistics.median(clogs), mad(clogs)) if len(clogs) > 5 else (0.0, 0.0)

    hours = [int(o["order_date"][11:13]) for o in orders]
    hour_share = {h: hours.count(h) / len(hours) for h in set(hours)}
    first_date = datetime.fromisoformat(orders[0]["order_date"])

    hist: dict[str, list[float]] = defaultdict(list)  # a customer's own prior order totals
    spent: dict[str, float] = defaultdict(float)
    first_seen: dict[str, datetime] = {}
    recent: dict[str, list[tuple[datetime, float, str]]] = defaultdict(list)
    alerts: list[dict[str, Any]] = []

    for o in orders:
        cid, when = o["customer_id"], datetime.fromisoformat(o["order_date"])
        first_seen.setdefault(cid, when)
        reasons: list[dict[str, Any]] = []

        def add(code: str, weight: float, text: str, reasons: list = reasons) -> None:
            reasons.append({"code": code, "weight": round(weight, 1), "text": text})

        if o["status"] == "completed" and o["total"] > 0:
            t = o["total"]
            z = robust_z(math.log(t), med_t, mad_t)
            if z >= Z_ORDER:
                add("amount_outlier", min(50, 25 + (z - Z_ORDER) * 5),
                    f"Order total {_gbp(t)} is {z:.1f} robust deviations above the typical "
                    f"{_gbp(math.exp(med_t))} (median/MAD on the log scale).")
            prior = hist[cid]
            if len(prior) >= 6:
                pm, pmad = statistics.median(prior), mad(prior)
                zc = robust_z(t, pm, pmad)
                if zc >= 4 and t > 2 * pm:
                    add("customer_outlier", 15,
                        f"{_gbp(t)} is {t / pm:.1f}x this customer's usual order of {_gbp(pm)} "
                        f"over {len(prior)} earlier orders (z={zc:.1f}).")
            for ln in by_order[o["order_id"]]:
                code = ln["stock_code"]
                if ln["is_product"] and ln["quantity"] > 0 and code in qty_stats:
                    qm, qmad = qty_stats[code]
                    zq = robust_z(ln["quantity"], qm, qmad)
                    if zq >= Z_QTY and ln["quantity"] >= 20 * qm and ln["quantity"] >= 100:
                        add("quantity_spike", 12,
                            f"{ln['quantity']:,} x {(ln['description'] or code).strip()} on one "
                            f"line; the usual line is {qm:g} (z={zq:.1f}).")
                        break
            for ln in by_order[o["order_id"]]:
                code = ln["stock_code"]
                if ln["is_product"] and code in price_med and ln["unit_price"] > 0:
                    ratio = ln["unit_price"] / price_med[code]
                    if ratio <= 0.2 or ratio >= 5:
                        add("price_deviation", 15,
                            f"{(ln['description'] or code).strip()} priced at "
                            f"{_gbp(ln['unit_price'])}, {ratio:.2f}x its median {_gbp(price_med[code])}.")
                        break
            manual = [ln for ln in by_order[o["order_id"]]
                      if ln["stock_code"] in MANUAL_CODES and abs(ln["line_total"]) >= 100]
            if manual:
                ln = manual[0]
                add("manual_line", 20,
                    f"Manual or adjustment line '{(ln['description'] or ln['stock_code']).strip()}' "
                    f"for {_gbp(ln['line_total'])}.")
            for w2, t2, oid2 in recent[cid]:
                if t >= 20 and abs(t2 - t) < 0.005 and abs((when - w2).total_seconds()) <= 86400 \
                        and oid2 != o["order_id"]:
                    add("duplicate", 20,
                        f"Same customer, same total {_gbp(t)}, within 24 hours of order {oid2}.")
                    break
            share = hour_share[int(o["order_date"][11:13])]
            if share < 0.01:
                add("off_hours", 8,
                    f"Placed at {o['order_date'][11:16]}, an hour with only {share * 100:.2f}% "
                    "of all orders.")
            hist[cid].append(t)
            spent[cid] += t
            recent[cid].append((when, t, o["order_id"]))
            kind = "order"
        elif o["status"] == "cancelled" and o["total"] < 0:
            amt = -o["total"]
            z = robust_z(math.log(amt), med_c, mad_c) if mad_c else 0.0
            if z >= Z_REFUND:
                add("large_refund", min(50, 25 + (z - Z_REFUND) * 5),
                    f"Refund of {_gbp(amt)} is {z:.1f} robust deviations above the typical "
                    f"{_gbp(math.exp(med_c))} refund.")
            visible = (first_seen[cid] - first_date) > timedelta(days=90)
            if visible and amt > spent[cid] + 0.01:
                add("refund_exceeds_purchases", 30,
                    f"Refund of {_gbp(amt)} is larger than everything this customer had bought "
                    f"before it ({_gbp(spent[cid])}), and their first order is inside the data.")
            kind = "refund"
        else:
            continue
        if not reasons:
            continue
        score = min(100.0, sum(r["weight"] for r in reasons))
        sev = _sev(score)
        if sev:
            alerts.append({
                "key": f"{kind}:{o['order_id']}", "kind": kind, "order_id": o["order_id"],
                "customer_id": cid, "date": o["order_date"][:16], "amount": o["total"],
                "score": round(score, 1), "severity": sev, "reasons": reasons,
                "subject": f"{'Refund' if kind == 'refund' else 'Order'} {o['order_id']}",
            })

    alerts.extend(_day_spikes(orders))
    alerts.sort(key=lambda a: (-a["score"], a["date"]))
    counts = {s: sum(1 for a in alerts if a["severity"] == s) for _, s in SEVERITY}
    return {
        "alerts": alerts,
        "summary": {
            "orders_screened": len(orders), "completed": len(done), "cancelled": len(canc),
            "alerts": len(alerts), "by_severity": counts,
            "by_kind": {k: sum(1 for a in alerts if a["kind"] == k) for k in ("order", "refund", "day")},
            "note": "No fraud labels exist in this data; an alert is a prompt to look, not a finding.",
        },
    }


def _day_spikes(orders: list) -> list[dict[str, Any]]:
    """Daily revenue and order-count spikes against the median of the previous 28 trading days."""
    rev: dict[str, float] = defaultdict(float)
    cnt: dict[str, int] = defaultdict(int)
    for o in orders:
        if o["status"] == "completed":
            d = o["order_date"][:10]
            rev[d] += o["total"]
            cnt[d] += 1
    days = sorted(rev)
    out = []
    for metric, series in (("revenue", rev), ("orders", cnt)):
        for i, d in enumerate(days):
            window = [series[x] for x in days[max(0, i - 28):i]]
            if len(window) < 14:
                continue
            m, md = statistics.median(window), mad(window)
            z = robust_z(series[d], m, md)
            if abs(z) >= Z_DAY and series[d] > 0:
                up = z > 0
                if not up and series[d] > 0.5 * m:
                    continue
                score = min(60.0, 20 + (abs(z) - Z_DAY) * 4)
                val = _gbp(series[d]) if metric == "revenue" else f"{series[d]:,}"
                med_txt = _gbp(m) if metric == "revenue" else f"{m:g}"
                out.append({
                    "key": f"day:{d}:{metric}", "kind": "day", "order_id": None,
                    "customer_id": None, "date": d, "amount": series[d],
                    "score": round(score, 1), "severity": _sev(score) or "low",
                    "subject": f"{metric.title()} {'spike' if up else 'drop'} on {d}",
                    "reasons": [{"code": f"day_{metric}_{'spike' if up else 'drop'}",
                                 "weight": round(score, 1),
                                 "text": f"Daily {metric} {val} against a 28-trading-day median of "
                                         f"{med_txt} (z={z:+.1f}, median/MAD)."}],
                })
    return out
