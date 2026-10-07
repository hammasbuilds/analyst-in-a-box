"""Customer / credit risk: a scorecard with points and reason codes, and a fairness panel.

Built on credit-risk-engine (path dependency, not edited): WoE/IV binning, the logistic fit, the
points scale, reason codes, calibration, Gini and the fairness audit.

What "bad" means here, stated plainly: the shipped data has no loan or payment defaults. The
outcome used is a refund event: in the 240 days after the cut-off date the customer's cancelled
orders total at least 5% of what they bought in that window (customers who did not buy in the
window are not exposed and are left out of the fit). Features are computed only from orders before the cut-off. It is a proxy
for customer risk, not a measurement of credit losses; with real payment data the same code takes
a real default flag.

Customers are split 80/15/5 into train/validation/test by a hash of their id (a customer is never
in two splits), and the Gini is reported on all three.
"""

from __future__ import annotations

import hashlib
import statistics
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from creditrisk import audit, calibration_table, fit_scorecard, gini
from creditrisk.scorecard import brier_score

from . import sources

OUTCOME_DAYS = 240
REFUND_SHARE = 0.05  # bad = refunds of at least this share of what they bought in the window
NUMERIC = ["recency_days", "orders", "spend", "avg_order", "tenure_days", "distinct_products",
           "refund_ratio"]
LABELS = {
    "recency_days": "days since last order", "orders": "number of orders",
    "spend": "total spend (£)", "avg_order": "average order (£)", "tenure_days": "days as a customer",
    "distinct_products": "different products bought", "refund_ratio": "refunded share of spend",
}
_cache: dict[tuple, dict[str, Any]] = {}
_lock = threading.Lock()


class RiskError(ValueError):
    pass


def split_of(customer_id: str) -> str:
    """Deterministic 80/15/5 split by customer id."""
    h = int(hashlib.sha256(customer_id.encode()).hexdigest(), 16) % 100
    return "train" if h < 80 else "validation" if h < 95 else "test"


def features_asof(src: dict[str, Any], asof: str) -> dict[str, dict[str, Any]]:
    """One feature row per customer with at least one completed order before `asof`."""
    base = sources.rows_of(
        src,
        "SELECT o.customer_id cid, COUNT(*) n, SUM(o.total) spend, MAX(o.order_date) last, "
        "MIN(o.order_date) first FROM orders o WHERE o.status='completed' AND o.order_date < ? "
        "GROUP BY o.customer_id", (asof,))
    refunds = {
        r["cid"]: r["v"] for r in sources.rows_of(
            src, "SELECT customer_id cid, -SUM(total) v FROM orders WHERE status='cancelled' "
                 "AND order_date < ? GROUP BY customer_id", (asof,))}
    prods = {
        r["cid"]: r["n"] for r in sources.rows_of(
            src, "SELECT o.customer_id cid, COUNT(DISTINCT oi.stock_code) n FROM order_items oi "
                 "JOIN orders o ON o.order_id=oi.order_id WHERE o.status='completed' "
                 "AND o.order_date < ? GROUP BY o.customer_id", (asof,))}
    country = {r["customer_id"]: r["country"]
               for r in sources.rows_of(src, "SELECT customer_id, country FROM customers")}
    t = datetime.fromisoformat(asof)
    out = {}
    for r in base:
        cid = r["cid"]
        spend = float(r["spend"] or 0)
        out[cid] = {
            "recency_days": (t - datetime.fromisoformat(r["last"])).days,
            "orders": int(r["n"]), "spend": round(spend, 2),
            "avg_order": round(spend / r["n"], 2) if r["n"] else 0.0,
            "tenure_days": (t - datetime.fromisoformat(r["first"])).days,
            "distinct_products": int(prods.get(cid, 0)),
            "refund_ratio": round(float(refunds.get(cid, 0.0)) / spend, 4) if spend > 0 else 0.0,
            "country_group": "United Kingdom" if country.get(cid) == "United Kingdom" else "Other",
            "country": country.get(cid, "Unknown"),
        }
    return out


def _date_range(src: dict[str, Any]) -> tuple[datetime, datetime]:
    r = sources.rows_of(src, "SELECT MIN(order_date) lo, MAX(order_date) hi FROM orders")[0]
    if not r["lo"]:
        raise RiskError("there are no orders to score")
    return datetime.fromisoformat(r["lo"]), datetime.fromisoformat(r["hi"])


def _stamp(src: dict[str, Any]) -> float:
    try:
        return Path(src["location"]).stat().st_mtime
    except OSError:
        return 0.0


def _reason_text(feature: str, bin_label: str, lost: int) -> str:
    return f"{LABELS.get(feature, feature)} in {bin_label} cost {lost} points against the best band"


def build(src: dict[str, Any], *, approve_rate: float = 0.8) -> dict[str, Any]:
    """Fit on the cut-off snapshot and score every customer as of the end of the data."""
    if not 0.3 <= approve_rate <= 0.99:
        raise RiskError("approve_rate must be between 0.30 and 0.99")
    if not sources.is_canonical(src):
        raise RiskError("risk scoring needs the canonical customers/orders tables")
    key = (src["location"], _stamp(src), round(approve_rate, 3))
    with _lock:
        if key in _cache:
            return _cache[key]
    lo, hi = _date_range(src)
    cut = hi - timedelta(days=OUTCOME_DAYS)
    if cut - lo < timedelta(days=270):
        raise RiskError("need at least 15 months of history: 9 to build features, 8 to observe outcomes")
    cut_s = cut.strftime("%Y-%m-%d %H:%M:%S")
    snap = features_asof(src, cut_s)
    win = sources.rows_of(
        src,
        "SELECT customer_id cid, SUM(CASE WHEN status='completed' THEN total ELSE 0 END) bought, "
        "SUM(CASE WHEN status='cancelled' THEN -total ELSE 0 END) refunded FROM orders "
        "WHERE order_date >= ? AND order_date <= ? GROUP BY customer_id",
        (cut_s, hi.strftime("%Y-%m-%d %H:%M:%S")))
    # Only customers who bought in the window are exposed to a refund; a customer who simply
    # left cannot go bad, and counting them as good would reward leaving.
    exposed = {r["cid"]: (r["bought"] or 0.0, r["refunded"] or 0.0) for r in win
               if (r["bought"] or 0.0) > 0}
    ids = sorted(c for c in snap if c in exposed)
    if len(ids) < 100:
        raise RiskError(f"only {len(ids)} customers bought both before and after the cut-off; need 100")
    snap = {c: snap[c] for c in ids}
    y = [1 if exposed[c][1] >= REFUND_SHARE * exposed[c][0] else 0 for c in ids]
    if sum(y) < 10 or sum(y) > len(y) - 10:
        raise RiskError("too few bad or good outcomes to fit a scorecard")
    cols = {f: [snap[c][f] for c in ids] for f in NUMERIC}
    splits = [split_of(c) for c in ids]

    def part(name: str) -> list[int]:
        return [i for i, s in enumerate(splits) if s == name]

    tr = part("train")
    card = fit_scorecard({f: [v[i] for i in tr] for f, v in cols.items()}, [y[i] for i in tr])

    def apps(idx: list[int]) -> list[dict]:
        return [{f: cols[f][i] for f in NUMERIC} for i in idx]

    metrics = {}
    for name in ("train", "validation", "test"):
        idx = part(name)
        probs = [card.probability(a) for a in apps(idx)]
        outs = [y[i] for i in idx]
        entry: dict[str, Any] = {"customers": len(idx), "bad": sum(outs),
                                 "bad_rate": round(sum(outs) / len(idx), 4) if idx else None}
        if idx and 0 < sum(outs) < len(outs):
            entry["gini"] = gini(probs, outs)
            entry["brier"] = brier_score(probs, outs)
        else:
            entry["gini"] = None
            entry["brier"] = None
        metrics[name] = entry
    val_idx = part("validation")
    calib = calibration_table([card.probability(a) for a in apps(val_idx)],
                              [y[i] for i in val_idx], n_bins=5) if val_idx else []

    train_scores = sorted(card.score(a) for a in apps(tr))
    cutoff = train_scores[int(len(train_scores) * (1 - approve_rate))]

    # fairness at the cut-off snapshot, every customer, decisions from the card's cut-off
    snap_scores = [card.score(a) for a in apps(list(range(len(ids))))]
    approved = [1 if s >= cutoff else 0 for s in snap_scores]
    fair = {}
    for label, grp in (
        ("country_group", [snap[c]["country_group"] for c in ids]),
        ("tenure_band", ["under 1 year" if snap[c]["tenure_days"] < 365 else "1 year or more"
                         for c in ids]),
    ):
        try:
            fair[label] = audit(grp, approved, y)
        except ValueError as exc:
            fair[label] = {"note": str(exc)}

    # live scoring: features as of the end of the data
    live = features_asof(src, (hi + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"))
    customers = {}
    for cid, f in live.items():
        app = {k: f[k] for k in NUMERIC}
        ex = card.explain(app, cutoff=cutoff)
        customers[cid] = {
            "customer_id": cid, "country": f["country"], "score": ex["score"],
            "pd": round(ex["probability_of_default"], 4), "decision": ex["decision"],
            "features": app, "points": ex["points"], "warnings": ex["warnings"],
            "reasons": [{**r, "text": _reason_text(r["feature"], r["bin"], r["points_lost"])}
                        for r in ex["reason_codes"]],
            "split": split_of(cid),
        }
    scores = [c["score"] for c in customers.values()]
    result = {
        "outcome": f"refunds of {REFUND_SHARE:.0%} or more of purchases in the {OUTCOME_DAYS} days after "
                   f"{cut_s[:10]}, among customers who bought in that window",
        "cutoff_date": cut_s[:10], "data_end": hi.strftime("%Y-%m-%d"),
        "snapshot_customers": len(ids), "snapshot_bad": sum(y),
        "base_bad_rate": round(sum(y) / len(y), 4),
        "score_cutoff": cutoff, "approve_rate": approve_rate,
        "metrics": metrics, "calibration": calib,
        "features": [{"feature": f, "label": LABELS[f], "iv": card.features[f].iv,
                      "strength": card.features[f].strength(),
                      "monotonic": card.features[f].is_monotonic()} for f in NUMERIC],
        "points_table": card.points_table(),
        "fairness": fair,
        "live": {"customers": len(customers), "score_min": min(scores), "score_max": max(scores),
                 "median_score": int(statistics.median(scores)),
                 "approved": sum(1 for c in customers.values() if c["decision"] == "APPROVE")},
        "notes": [
            "The outcome is a refund proxy, not a payment default: this data has none.",
            f"The test split is {metrics['test']['customers']} customers, so its Gini is a rough "
            "guide only; read the validation figure first.",
            "Country is not a feature; it is audited, so a gap shows up as a gap rather than being "
            "hidden by the model.",
        ],
    }
    result["_customers"] = customers
    with _lock:
        _cache[key] = result
    return result


def public(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def customer_list(result: dict[str, Any], *, order: str = "score", limit: int = 100,
                  decision: str | None = None) -> list[dict[str, Any]]:
    rows = list(result["_customers"].values())
    if decision:
        rows = [r for r in rows if r["decision"] == decision.upper()]
    rows.sort(key=lambda r: r["score"] if order == "score" else -r["features"]["spend"])
    return [{k: r[k] for k in ("customer_id", "country", "score", "pd", "decision", "split")}
            | {"spend": r["features"]["spend"], "top_reason": r["reasons"][0]["feature"]
               if r["reasons"] else None} for r in rows[:limit]]
