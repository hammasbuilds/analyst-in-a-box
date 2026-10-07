"""Hierarchical demand forecast: total -> category -> product, coherent, with a backtest.

Built on demand-forecast-platform (path dependency, not edited): Hierarchy and its reconciliation
methods, ETS, Croston, seasonal naive and the error measures. What is added here:

  * the data: weekly units per product from completed orders, trailing/leading partial weeks cut
  * the hierarchy: the top products of every category are leaves, the rest of the category is one
    "other" leaf, so the total is the real total
  * per-series model choice by demand pattern (Syntetos-Boylan: ADI and CV^2), made again inside
    every backtest fold, so the backtest sees only what the model would have seen
  * a rolling-origin backtest against seasonal naive for every node and every reconciliation method
  * non-negative leaves, then re-aggregation, so the result is both >= 0 and exactly coherent
"""

from __future__ import annotations

import statistics
import threading
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from forecast import (
    Hierarchy,
    croston,
    ets,
    naive,
    naive_seasonal,
    reconcile,
    smape,
)
from forecast.backtest import mae, naive_scale

from . import sources

PERIOD = 52
ADI_CUT, CV2_CUT = 1.32, 0.49
METHODS = ("bottom_up", "top_down", "mint_ols", "mint_wls", "weighted_blend")
_cache: dict[tuple, dict[str, Any]] = {}
_lock = threading.Lock()


class ForecastError(ValueError):
    pass


def classify(series: list[float]) -> dict[str, Any]:
    """Syntetos-Boylan demand pattern from the history."""
    nz = [v for v in series if v > 0]
    if len(nz) < 2:
        return {"pattern": "too sparse", "adi": None, "cv2": None}
    adi = len(series) / len(nz)
    mean = statistics.fmean(nz)
    cv2 = (statistics.pstdev(nz) / mean) ** 2 if mean else 0.0
    if adi < ADI_CUT:
        pattern = "smooth" if cv2 < CV2_CUT else "erratic"
    else:
        pattern = "intermittent" if cv2 < CV2_CUT else "lumpy"
    return {"pattern": pattern, "adi": round(adi, 2), "cv2": round(cv2, 2)}


def fit_forecast(history: list[float], horizon: int) -> tuple[str, list[float]]:
    """The model for one series, chosen from that series' own demand pattern: Croston for
    intermittent and lumpy demand, ETS (seasonal when two years of weeks exist) otherwise.

    A holdout-based "pick the best of several" was tried and made the backtest worse (a noisy
    8-week holdout picks noise), so the choice is by pattern only and the backtest is what says
    whether the result beat seasonal naive.
    """
    pattern = classify(history)["pattern"]
    if pattern in ("intermittent", "lumpy", "too sparse"):
        return "Croston", croston(history, horizon)
    seasonal = len(history) >= 2 * PERIOD
    return ("ETS (seasonal)" if seasonal else "ETS",
            ets(history, horizon, period=PERIOD if seasonal else 1))


def forecaster(history: list[float], horizon: int) -> list[float]:
    return fit_forecast(history, horizon)[1]


def model_name(history: list[float]) -> str:
    return fit_forecast(history, 1)[0]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def load_weekly(src: dict[str, Any], per_category: int) -> dict[str, Any]:
    """Weekly units per product, zero-filled, plus the category of every product."""
    rng = sources.rows_of(
        src, "SELECT MIN(order_date) lo, MAX(order_date) hi FROM orders WHERE status='completed'"
    )[0]
    if not rng["lo"]:
        raise ForecastError("there are no completed orders to forecast from")
    lo, hi = date.fromisoformat(rng["lo"][:10]), date.fromisoformat(rng["hi"][:10])
    first = _monday(lo) if lo.weekday() == 0 else _monday(lo) + timedelta(days=7)
    last = _monday(hi) - timedelta(days=7) if hi.weekday() != 6 else _monday(hi)
    if last < first + timedelta(days=7 * (2 * 8)):
        raise ForecastError("need at least 16 complete weeks of history")
    rows = sources.rows_of(
        src,
        "SELECT p.stock_code code, p.description descr, p.category cat, "
        "date(o.order_date, '-6 days', 'weekday 1') wk, SUM(oi.quantity) units "
        "FROM order_items oi JOIN orders o ON o.order_id=oi.order_id "
        "JOIN products p ON p.stock_code=oi.stock_code "
        "WHERE o.status='completed' AND p.is_product=1 AND oi.quantity>0 "
        "GROUP BY 1,2,3,4",
    )
    weeks: list[str] = []
    d = first
    while d <= last:
        weeks.append(d.isoformat())
        d += timedelta(days=7)
    idx = {w: i for i, w in enumerate(weeks)}
    series: dict[str, list[float]] = {}
    meta: dict[str, tuple[str, str]] = {}
    for r in rows:
        if r["wk"] not in idx:
            continue
        s = series.setdefault(r["code"], [0.0] * len(weeks))
        s[idx[r["wk"]]] += float(r["units"])
        meta[r["code"]] = (r["cat"], r["descr"])
    by_cat: dict[str, list[str]] = defaultdict(list)
    for code in sorted(series, key=lambda c: -sum(series[c])):
        by_cat[meta[code][0]].append(code)
    leaves: dict[str, list[float]] = {}
    paths: list[tuple[str, str]] = []
    leaf_codes: dict[str, str | None] = {}
    for cat, codes in by_cat.items():
        for code in codes[:per_category]:
            name = f"{code} {meta[code][1].title()[:28]}".strip()
            leaves[f"{cat}/{name}"] = series[code]
            paths.append((cat, name))
            leaf_codes[f"{cat}/{name}"] = code
        rest = codes[per_category:]
        if rest:
            other = [sum(series[c][i] for c in rest) for i in range(len(weeks))]
            leaves[f"{cat}/other ({len(rest)} products)"] = other
            paths.append((cat, f"other ({len(rest)} products)"))
            leaf_codes[f"{cat}/other ({len(rest)} products)"] = None
    h = Hierarchy.from_paths(paths, root="All products")
    history = h.aggregate(leaves)
    return {"hierarchy": h, "history": history, "weeks": weeks, "leaf_codes": leaf_codes}


def _nonneg(h: Hierarchy, fc: dict[str, list[float]]) -> dict[str, list[float]]:
    leaves = {leaf: [max(0.0, v) for v in fc[leaf]] for leaf in h.leaves()}
    return h.aggregate(leaves)


def _base(h: Hierarchy, train: dict[str, list[float]], horizon: int) -> dict[str, list[float]]:
    return {n: [max(0.0, v) for v in forecaster(train[n], horizon)] for n in h.nodes}


def _bench(train: list[float], horizon: int) -> list[float]:
    return naive_seasonal(train, horizon, period=PERIOD) if len(train) >= PERIOD else naive(train, horizon)


def run(
    src: dict[str, Any], *, horizon: int = 8, per_category: int = 3, method: str = "mint_wls",
    folds: int = 4,
) -> dict[str, Any]:
    if method not in METHODS:
        raise ForecastError(f"method must be one of {', '.join(METHODS)}")
    if not 1 <= horizon <= 26:
        raise ForecastError("horizon must be between 1 and 26 weeks")
    if not 1 <= per_category <= 6:
        raise ForecastError("products per category must be between 1 and 6")
    if not sources.is_canonical(src):
        raise ForecastError(
            "forecasting needs the orders / order_items / products tables; map an uploaded "
            "sheet of order lines on the Data page, or use the sample"
        )
    key = (src["location"], _stamp(src), horizon, per_category, method, folds)
    with _lock:
        if key in _cache:
            return _cache[key]
    data = load_weekly(src, per_category)
    h: Hierarchy = data["hierarchy"]
    hist: dict[str, list[float]] = data["history"]
    weeks: list[str] = data["weeks"]
    n = len(weeks)

    # --- final forecast ------------------------------------------------------------------
    base = _base(h, hist, horizon)
    rec = reconcile(h, base, method, history=hist)
    fc = _nonneg(h, rec)
    coherent = h.is_coherent(fc)
    last = date.fromisoformat(weeks[-1])
    future = [(last + timedelta(days=7 * (i + 1))).isoformat() for i in range(horizon)]

    # --- backtest: same folds for every node and method -----------------------------------
    initial = max(PERIOD + 8, n - horizon - (folds - 1) * 4)
    origins = [o for o in range(initial, n - horizon + 1, 4)][:folds]
    if not origins:
        raise ForecastError("not enough history for a backtest")
    names = ("base", *METHODS)
    err = {m: dict.fromkeys(h.nodes, 0.0) for m in names}
    bench_err = dict.fromkeys(h.nodes, 0.0)
    smape_acc = dict.fromkeys(h.nodes, 0.0)
    naive_err = dict.fromkeys(h.nodes, 0.0)
    for o in origins:
        train = {k: v[:o] for k, v in hist.items()}
        b = _base(h, train, horizon)
        fcs = {"base": b}
        for m in METHODS:
            fcs[m] = _nonneg(h, reconcile(h, b, m, history=train))
        for node in h.nodes:
            actual = hist[node][o : o + horizon]
            for m in names:
                err[m][node] += mae(actual, fcs[m][node])
            bench_err[node] += mae(actual, _bench(train[node], horizon))
            naive_err[node] += mae(actual, naive(train[node], horizon))
            smape_acc[node] += smape(actual, fcs[method][node])
    k = len(origins)
    levels = h.levels()
    level_names = ["total", "category", "product"][: len(levels)]

    def level_mae(m: str, nodes: list[str]) -> float:
        return round(statistics.fmean(err[m][x] / k for x in nodes), 3)

    by_level = []
    for lname, nodes in zip(level_names, levels, strict=False):
        row = {"level": lname, "nodes": len(nodes),
               "seasonal_naive": round(statistics.fmean(bench_err[x] / k for x in nodes), 3)}
        for m in names:
            row[m] = level_mae(m, nodes)
        by_level.append(row)

    out_nodes = []
    for name, node in h.nodes.items():
        series = hist[name]
        c = classify(series)
        model_mae = err[method][name] / k
        bench_mae = bench_err[name] / k
        rel = round(model_mae / bench_mae, 3) if bench_mae > 0 else None
        scale = naive_scale(series[: origins[-1]], period=1)
        depth = next(i for i, lv in enumerate(levels) if name in lv)
        out_nodes.append({
            "name": name, "parent": node.parent, "level": level_names[depth] if depth < 3 else str(depth),
            "model": model_name(series), "pattern": c["pattern"], "adi": c["adi"], "cv2": c["cv2"],
            "history": [round(v, 1) for v in series[-26:]],
            "forecast": [round(v, 1) for v in fc[name]],
            "base_forecast": [round(v, 1) for v in base[name]],
            "backtest": {
                "mae": round(model_mae, 3), "seasonal_naive_mae": round(bench_mae, 3),
                "relative_mae": rel, "beats_seasonal_naive": (rel < 1.0) if rel is not None else None,
                "mase": round(model_mae / scale, 3) if scale else None,
                "smape": round(smape_acc[name] / k, 3),
            },
        })
    judged = [n_["backtest"]["beats_seasonal_naive"] for n_ in out_nodes
              if n_["backtest"]["beats_seasonal_naive"] is not None]
    result = {
        "method": method, "horizon": horizon, "per_category": per_category,
        "weeks": weeks[-26:], "future_weeks": future, "history_weeks": n,
        "coherent": coherent, "coherent_before_reconciliation": h.is_coherent(base),
        "backtest": {
            "origins": [weeks[o] for o in origins], "folds": k, "horizon": horizon,
            "by_level": by_level,
            "nodes_beating_seasonal_naive": sum(judged), "nodes_judged": len(judged),
            "best_method_at_total": min(names, key=lambda m: err[m]["All products"]),
        },
        "nodes": out_nodes,
        "patterns": {p: sum(1 for x in out_nodes if x["pattern"] == p and x["level"] == "product")
                     for p in ("smooth", "erratic", "intermittent", "lumpy", "too sparse")},
    }
    with _lock:
        _cache[key] = result
    return result


def _stamp(src: dict[str, Any]) -> float:
    from pathlib import Path

    try:
        return Path(src["location"]).stat().st_mtime
    except OSError:
        return 0.0
