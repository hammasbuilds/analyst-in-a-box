"""Ask your data: a question in English (or simple Roman Urdu) becomes SQL that is shown, validated
and run read-only.

Two routes. With a language model configured (llm.py) the model writes the SQL from the schema.
Without one, a deterministic parser composes SQL from a metric, a dimension, filters and a ranking.
Either way the SQL goes through sqlsafe.validate and the read-only connection; the model is never
trusted, and when it fails the parser answers instead.
"""

from __future__ import annotations

import re
from typing import Any

from . import llm, sources, sqlsafe

# ---- Roman Urdu ------------------------------------------------------------------------------
# Phrases first (longest match), then single words. "" drops a filler word. This is a small
# glossary for business questions, not a translator; anything outside it passes through unchanged.

URDU_PHRASES = [
    ("sab se zyada", "top"), ("sabse zyada", "top"), ("sab se ziada", "top"),
    ("sabse ziada", "top"), ("sab se acha", "top"), ("sab se achi", "top"),
    ("sab se kam", "lowest"), ("sabse kam", "lowest"), ("ke hisab se", "by"),
    ("kitne log", "how many customer"), ("kis mulk", "which country"), ("kon sa", "which"),
    ("kaun sa", "which"), ("kaun si", "which"), ("kon si", "which"),
]
URDU_WORDS = {
    "kitne": "how many", "kitni": "how many", "kitna": "how many", "kitnay": "how many",
    "kul": "total", "ausat": "average", "bikri": "sales", "farokht": "sales", "bikne": "sales",
    "bikti": "sales", "bikta": "sales", "aamdani": "revenue", "amdani": "revenue",
    "mahine": "month", "mahina": "month", "mahinay": "month", "maheena": "month",
    "saal": "year", "sal": "year", "hafte": "week", "hafta": "week", "gahak": "customer",
    "grahak": "customer", "khareedar": "customer", "kharidar": "customer", "gahakon": "customer",
    "mulk": "country", "mulkon": "country", "wapsi": "refund", "wapis": "refund",
    "waapsi": "refund", "cheez": "product", "cheezein": "product", "cheezen": "product",
    "maal": "product", "kis": "which", "kaun": "which", "kon": "which", "pichle": "last",
    "pichla": "last", "har": "by", "dikhao": "", "dikhaye": "", "batao": "", "bataiye": "",
    "btao": "", "dikha": "", "ka": "", "ki": "", "ke": "", "se": "", "mein": "", "me": "",
    "hai": "", "hain": "", "kya": "", "wali": "", "wale": "", "wala": "", "zyada": "top",
    "ziada": "top", "kam": "lowest", "din": "day", "roz": "day", "order": "order",
}


def normalise(question: str) -> tuple[str, str]:
    """Lower-case, expand Roman Urdu. Returns (text, language) with language 'en' or 'roman-ur'."""
    q = " " + re.sub(r"[?!.,;:]+", " ", question.lower()) + " "
    hit = False
    for src, dst in URDU_PHRASES:
        if f" {src} " in q:
            q, hit = q.replace(f" {src} ", f" {dst} "), True
    out = []
    for tok in q.split():
        if tok in URDU_WORDS and tok not in ("order",):
            hit = True
            if URDU_WORDS[tok]:
                out.append(URDU_WORDS[tok])
        else:
            out.append(tok)
    return " ".join(out), ("roman-ur" if hit else "en")


# ---- the canonical (retail) parser -----------------------------------------------------------

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
COUNTRY_ALIASES = {"uk": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
                   "u.k.": "United Kingdom", "us": "USA", "america": "USA", "eire": "EIRE"}
TIME_DIMS = {"day": "substr(o.order_date,1,10)", "week": "strftime('%Y-W%W', o.order_date)",
             "month": "substr(o.order_date,1,7)", "quarter": "substr(o.order_date,1,4) || '-Q' || "
             "((CAST(substr(o.order_date,6,2) AS INTEGER)+2)/3)",
             "year": "substr(o.order_date,1,4)",
             "weekday": "CASE strftime('%w', o.order_date) WHEN '0' THEN '7 Sun' WHEN '1' THEN "
             "'1 Mon' WHEN '2' THEN '2 Tue' WHEN '3' THEN '3 Wed' WHEN '4' THEN '4 Thu' "
             "WHEN '5' THEN '5 Fri' ELSE '6 Sat' END"}
TOP_WORDS = r"\b(top|best|highest|most|biggest|largest|leading|greatest)\b"
LOW_WORDS = r"\b(bottom|worst|lowest|least|smallest|fewest|weakest)\b"
DIM_WORDS = [
    ("weekday", r"\b(weekday|weekdays|day of (the )?week|days of (the )?week)\b"),
    ("quarter", r"\b(quarter|quarterly|quarters)\b"),
    ("month", r"\b(month|monthly|months|trend|over time)\b"),
    ("year", r"\b(year|yearly|annual|annually|years)\b"),
    ("week", r"\b(week|weekly|weeks)\b"),
    ("day", r"\b(day|daily|days)\b"),
    ("country", r"\b(country|countries|nation|region)\b"),
    ("category", r"\b(category|categories)\b"),
    ("product", r"\b(product|products|item|items|sku|skus)\b"),
    ("customer", r"\b(customer|customers|client|clients|buyer|buyers)\b"),
]

EXAMPLES = [
    "Sales by month", "Top 10 products by revenue", "Revenue by country",
    "How many customers do we have?", "Average order value by year",
    "Which category sells the most units?", "Refunds by month",
    "Top 5 customers in 2011", "Revenue in the last 30 days",
    "sab se zyada bikne wali 5 cheezein", "har mahine ki bikri", "kitne customers hain",
]


def _find_dim(q: str) -> str | None:
    m = re.search(r"\b(?:by|per|each|every|across|for each|group(?:ed)? by)\s+(?:the\s+)?(\w+(?: \w+)?)", q)
    if m:
        frag = m.group(1)
        for dim, rx in DIM_WORDS:
            if re.search(rx, frag):
                return dim
    # a ranking noun: "top 5 products", "which country"
    m = re.search(r"(?:top|bottom|best|worst|highest|lowest|biggest|which|most|least)\s+(?:\d+\s+)?"
                  r"(?:selling\s+|paying\s+|spending\s+)?(?P<noun>\w+)", q)
    if m:
        for dim, rx in DIM_WORDS:
            if re.search(rx, m.group("noun")):
                return dim
    hits = [dim for dim, rx in DIM_WORDS if re.search(rx, q)]
    if hits:
        # time words beat nouns ("sales by month for customers" is still by month)
        for dim in ("weekday", "quarter", "month", "year", "week", "day"):
            if dim in hits and dim != "day":
                return dim
        for dim in ("country", "category", "product", "customer"):
            if dim in hits:
                return dim
        return hits[0]
    return None


def _find_metric(q: str, dim: str | None) -> str:
    count_q = bool(re.search(r"\b(how many|number of|count|total number)\b", q))
    if re.search(r"\b(average order|aov|average basket|average sale|average value|avg order|mean order|average spend)\b", q):
        return "aov"
    if re.search(r"\b(refund|refunds|refunded|return|returns|returned|cancel|cancelled|cancellation|cancellations)\b", q):
        return "refund_count" if count_q else "refunds"
    if count_q and re.search(r"\b(customer|customers|client|clients|buyers)\b", q):
        return "customers"
    if count_q and re.search(r"\b(order|orders|invoice|invoices)\b", q):
        return "orders"
    if count_q and re.search(r"\b(product|products|item|items|sku|skus)\b", q):
        return "products_count"
    if re.search(r"\b(units|unit|quantity|quantities|pieces|volume|sold|selling units)\b", q):
        return "units"
    if re.search(r"\b(orders|invoices)\b", q) and not re.search(r"\b(revenue|sales)\b", q):
        return "orders"
    if dim == "customer" and re.search(r"\b(customers?)\b", q) and count_q:
        return "customers"
    return "revenue"


def _filters(q: str, countries: list[str], categories: list[str]) -> tuple[list[str], list[str]]:
    where: list[str] = []
    notes: list[str] = []
    m = re.search(r"\blast (\d+) (day|days|week|weeks|month|months)\b", q)
    if m:
        n, unit = int(m.group(1)), m.group(2).rstrip("s")
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        where.append(f"o.order_date >= date((SELECT MAX(order_date) FROM orders), '-{days} day')")
        notes.append(f"last {n} {unit}s, counted back from the newest order in the data")
    elif re.search(r"\blast month\b", q):
        where.append("substr(o.order_date,1,7) = (SELECT substr(MAX(order_date),1,7) FROM orders)")
        notes.append("the most recent month in the data")
    elif re.search(r"\blast year\b", q):
        where.append("substr(o.order_date,1,4) = (SELECT substr(MAX(order_date),1,4) FROM orders)")
        notes.append("the most recent year in the data")
    ym = re.search(r"\b(19\d\d|20\d\d)\b", q)
    mn = next((i + 1 for i, name in enumerate(MONTHS) if re.search(rf"\b{name[:3]}[a-z]*\b", q)
               and re.search(rf"\b(in|of|during|for)?\s*{name}\b", q)), None)
    if mn:
        where.append(f"substr(o.order_date,6,2) = '{mn:02d}'")
        notes.append(MONTHS[mn - 1].title())
    if ym:
        where.append(f"substr(o.order_date,1,4) = '{ym.group(1)}'")
        notes.append(ym.group(1))
    padded = f" {q} "
    for alias, name in COUNTRY_ALIASES.items():
        if f" {alias} " in padded and name in countries:
            where.append(f"c.country = '{name}'")
            notes.append(name)
            break
    else:
        for name in sorted(countries, key=len, reverse=True):
            if f" {name.lower()} " in padded:
                where.append(f"c.country = '{name.replace(chr(39), chr(39) * 2)}'")
                notes.append(name)
                break
    for cat in categories:
        if cat.lower() in q or cat.lower().split(" &")[0] + " category" in q:
            where.append(f"p.category = '{cat}'")
            notes.append(f"category {cat}")
            break
    cm = re.search(r"\bcustomer\s+#?(\d{4,6})\b", q)
    if cm:
        where.append(f"o.customer_id = '{cm.group(1)}'")
        notes.append(f"customer {cm.group(1)}")
    return where, notes


def _quoted(question: str) -> str | None:
    m = re.search(r"[\"“']([^\"”']{3,60})[\"”']", question)
    return m.group(1) if m else None


def compose(
    question: str, countries: list[str], categories: list[str]
) -> dict[str, Any] | None:
    """Deterministic question -> SQL for the canonical schema, or None when nothing fits."""
    q, lang = normalise(question)
    dim = _find_dim(q)
    metric = _find_metric(q, dim)
    asc = bool(re.search(LOW_WORDS, q))
    ranked = bool(re.search(TOP_WORDS, q)) or asc
    nm = re.search(r"\b(?:top|bottom|best|worst|highest|lowest|biggest|first|least|most)\s+(\d{1,3})\b", q) \
        or re.search(r"\b(\d{1,3})\s+(?:best|top|worst|biggest|highest|lowest)?\s*(?:selling\s+)?"
                     r"(?:products?|customers?|countr(?:y|ies)|categor(?:y|ies)|months?|days?|items?|weeks?)\b", q)
    limit = int(nm.group(1)) if nm else None
    if ranked and limit is None:
        limit = 1 if re.search(r"\bwhich\b", q) else 10
    if dim in ("customer",) and metric in ("customers", "aov"):
        if metric == "customers":
            dim = None
    if dim is None and ranked:
        return None
    cue = r"\b(revenue|sales?|income|turnover|orders?|invoices?|customers?|clients?|units?|quantity|refunds?|returns?|cancel\w*|aov|spend|spent|products?|items?|money|worth)\b"
    if dim is None and not re.search(cue, q):
        return None
    where, notes = _filters(q, countries, categories)
    item_level = metric in ("units", "products_count") or dim in ("product", "category")
    if dim in ("product", "category", "customer") and metric == "aov":
        return None
    if dim == "product" and metric == "products_count":
        return None
    needle = _quoted(question)
    if needle:
        item_level = True
        where.append("p.description LIKE '%" + needle.upper().replace("'", "''") + "%'")
        notes.append(f"products matching '{needle}'")

    cancelled = metric in ("refunds", "refund_count")
    where.insert(0, "o.status = 'cancelled'" if cancelled else "o.status = 'completed'")
    if item_level and dim in ("product", "category") or needle:
        where.append("p.is_product = 1")

    if dim in TIME_DIMS:
        dexpr, dlabel, dgroup = TIME_DIMS[dim], dim, None
    elif dim == "country":
        dexpr, dlabel = "c.country", "country"
    elif dim == "category":
        dexpr, dlabel = "p.category", "category"
    elif dim == "product":
        dexpr, dlabel = "p.description", "product"
    elif dim == "customer":
        dexpr, dlabel = "o.customer_id", "customer_id"
    else:
        dexpr = dlabel = None
    dgroup = dexpr

    sign = "-" if cancelled else ""
    if item_level:
        mexpr = {
            "revenue": f"ROUND({sign}SUM(oi.line_total), 2)", "refunds": f"ROUND({sign}SUM(oi.line_total), 2)",
            "units": f"{sign}SUM(oi.quantity)", "orders": "COUNT(DISTINCT o.order_id)",
            "customers": "COUNT(DISTINCT o.customer_id)", "products_count": "COUNT(DISTINCT oi.stock_code)",
            "refund_count": "COUNT(DISTINCT o.order_id)", "aov": "",
        }[metric]
        base = ("FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
                "JOIN products p ON p.stock_code = oi.stock_code "
                "JOIN customers c ON c.customer_id = o.customer_id")
    else:
        mexpr = {
            "revenue": "ROUND(SUM(o.total), 2)", "refunds": "ROUND(-SUM(o.total), 2)",
            "orders": "COUNT(*)", "customers": "COUNT(DISTINCT o.customer_id)",
            "refund_count": "COUNT(*)", "aov": "ROUND(AVG(o.total), 2)",
        }.get(metric, "")
        base = "FROM orders o JOIN customers c ON c.customer_id = o.customer_id"
    if not mexpr:
        return None
    mlabel = {"revenue": "revenue", "refunds": "refunded", "units": "units", "orders": "orders",
              "customers": "customers", "products_count": "products", "refund_count": "refunds",
              "aov": "avg_order_value"}[metric]
    if metric == "units" and cancelled:
        mlabel = "units_returned"

    sel = f"{dexpr} AS {dlabel}, {mexpr} AS {mlabel}" if dexpr else f"{mexpr} AS {mlabel}"
    parts = [f"SELECT {sel}", base, "WHERE " + " AND ".join(where)]
    if dexpr:
        parts.append(f"GROUP BY {dgroup}")
        if dim in TIME_DIMS and not ranked:
            parts.append("ORDER BY 1")
            if limit:
                parts.append(f"LIMIT {limit}")
        else:
            parts.append(f"ORDER BY {mlabel} {'ASC' if asc else 'DESC'}")
            parts.append(f"LIMIT {limit or (20 if dim in ('product', 'customer') else 50)}")
    sql = "\n".join(parts)
    what = {"revenue": "revenue from completed orders", "refunds": "value of cancelled orders (refunds)",
            "units": "units sold", "orders": "completed orders", "customers": "distinct customers with completed orders",
            "products_count": "distinct products sold", "refund_count": "cancelled orders",
            "aov": "average completed-order value"}[metric]
    exp = what + (f" by {dlabel}" if dlabel else "")
    if ranked and dim:
        exp = f"{'lowest' if asc else 'top'} {limit} {dlabel}: {what}"
    if notes:
        exp += " (" + ", ".join(notes) + ")"
    return {"sql": sql, "explanation": exp, "language": lang}


# ---- generic parser for any other table -------------------------------------------------------

AGGS = [
    ("avg", r"\b(average|avg|mean)\b"), ("max", r"\b(max|maximum|highest|largest|biggest)\b"),
    ("min", r"\b(min|minimum|lowest|smallest)\b"), ("count", r"\b(how many|count|number of)\b"),
    ("sum", r"\b(sum|total|overall)\b"),
]


def _stem(w: str) -> str:
    return re.sub(r"(ies|es|s)$", "", w.lower()) if len(w) > 3 else w.lower()


def _match_cols(q: str, tables: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    """(position, table, column) for every column or table whose name words appear in q."""
    out = []
    words = [(m.start(), _stem(m.group())) for m in re.finditer(r"[a-z0-9]+", q)]
    for t in tables:
        for c in t["columns"]:
            parts = [_stem(p) for p in c["name"].lower().split("_") if p]
            for i, (pos, _w) in enumerate(words):
                n = len(parts)
                if [x for _, x in words[i : i + n]] == parts:
                    out.append((pos, t["name"], c["name"]))
    return out


def generic(question: str, tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    q, lang = normalise(question)
    for t in tables:  # "show customers" / "list orders"
        if re.search(rf"\b(show|list|display|see|preview)\b.*\b{_stem(t['name'])}", q):
            return {"sql": f'SELECT * FROM "{t["name"]}" LIMIT 20',
                    "explanation": f"first rows of {t['name']}", "language": lang}
    agg = next((a for a, rx in AGGS if re.search(rx, q)), None)
    cols = sorted(_match_cols(q, tables))
    ranked = re.search(TOP_WORDS, q) or re.search(LOW_WORDS, q)
    if agg == "count" and not cols:
        for t in tables:
            if re.search(rf"\b{_stem(t['name'])}", q):
                return {"sql": f'SELECT COUNT(*) AS rows_in_{t["name"]} FROM "{t["name"]}"',
                        "explanation": f"number of rows in {t['name']}", "language": lang}
        return None
    if not cols:
        return None
    gm = re.search(r"\b(?:by|per|each|for each)\s+(\w+(?: \w+)?)", q)
    group_col = None
    if gm:
        frag = _match_cols(gm.group(1), tables)
        if frag:
            group_col = frag[0]
    first_table = cols[0][1]
    measure = next((c for c in cols if c[1] == first_table and (not group_col or c[1:] != group_col[1:])), None)
    if group_col and group_col[1] != first_table:
        measure = next((c for c in cols if c[1] == group_col[1] and c[1:] != group_col[1:]), None)
        first_table = group_col[1]
    if measure is None and agg != "count":
        return None
    tname = first_table
    tinfo = next(t for t in tables if t["name"] == tname)
    mtype = next((c["type"] for c in tinfo["columns"] if measure and c["name"] == measure[2]), "TEXT")
    numeric = mtype.upper() in ("INTEGER", "REAL", "NUMERIC", "FLOAT", "DOUBLE", "DECIMAL", "BIGINT")
    if agg is None:
        agg = "sum" if numeric and (group_col or ranked) else None
    if agg is None:
        return None
    if agg in ("sum", "avg", "max", "min") and not numeric:
        return None
    fn = {"sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN", "count": "COUNT"}[agg]
    mexpr = "COUNT(*)" if agg == "count" and measure is None else f'{fn}("{measure[2]}")'
    if agg in ("sum", "avg"):
        mexpr = f"ROUND({mexpr}, 2)"
    label = f"{agg}_{measure[2]}" if measure else "n"
    if group_col:
        order = "ASC" if re.search(LOW_WORDS, q) else "DESC"
        nm = re.search(r"\b(?:top|bottom|best|worst)\s+(\d{1,3})\b", q)
        lim = int(nm.group(1)) if nm else 20
        sql = (f'SELECT "{group_col[2]}", {mexpr} AS {label} FROM "{tname}" '
               f'GROUP BY "{group_col[2]}" ORDER BY {label} {order} LIMIT {lim}')
        exp = f"{agg} of {measure[2] if measure else 'rows'} by {group_col[2]} in {tname}"
    else:
        sql = f'SELECT {mexpr} AS {label} FROM "{tname}"'
        exp = f"{agg} of {measure[2] if measure else 'rows'} in {tname}"
    return {"sql": sql, "explanation": exp, "language": lang}


# ---- chart suggestion ------------------------------------------------------------------------

_TIMEISH = re.compile(r"^\d{4}(-\d{2}(-\d{2})?|-Q\d|-W\d{2})?$")


def suggest_chart(columns: list[str], rows: list[list[Any]]) -> dict[str, Any] | None:
    if not rows or not columns:
        return None
    numeric = [i for i in range(len(columns))
               if all(isinstance(r[i], int | float) or r[i] is None for r in rows)
               and any(r[i] is not None for r in rows)]
    if len(rows) == 1 and len(columns) <= 3 and numeric and len(numeric) == len(columns):
        return {"type": "stat", "x": None, "y": [columns[i] for i in numeric]}
    if len(columns) < 2 or not numeric:
        return None
    label_idx = next((i for i in range(len(columns)) if i not in numeric), None)
    if label_idx is None:
        return None
    ys = [columns[i] for i in numeric if i != label_idx][:2]
    if not ys:
        return None
    timeish = all(_TIMEISH.match(str(r[label_idx]) or "") for r in rows if r[label_idx] is not None)
    if timeish and len(rows) >= 3:
        return {"type": "line", "x": columns[label_idx], "y": ys}
    if len(rows) > 60:
        return None
    return {"type": "bar", "x": columns[label_idx], "y": ys[:1]}


# ---- orchestration ---------------------------------------------------------------------------

_SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)


def extract_sql(text: str) -> str:
    m = _SQL_BLOCK.search(text)
    sql = (m.group(1) if m else text).strip()
    i = re.search(r"\b(select|with)\b", sql, re.I)
    return sql[i.start():].strip() if i else sql


def _llm_sql(client: llm.Client, src: dict[str, Any], tables: list[dict[str, Any]], question: str,
             previous: tuple[str, str] | None = None) -> str:
    system = (
        f"You write ONE read-only {sources.dialect(src)} SELECT query that answers the user's "
        "question over the schema below. Use only these tables and columns. Return only the SQL in "
        "a ```sql block. No explanation. Never write to the database."
    )
    prompt = f"Schema:\n{sources.ddl_for_prompt(tables)}\n\nQuestion: {question}\n"
    if previous:
        prompt += f"\nYour previous query:\n{previous[0]}\nfailed with: {previous[1]}\nFix it.\n"
    return extract_sql(client.complete(prompt, system))


def answer(src: dict[str, Any], question: str, *, client: llm.Client | None = None) -> dict[str, Any]:
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "ask a question", "examples": EXAMPLES}
    tables = sources.schema(src, sample_rows=2)
    canonical = sources.is_canonical(src)
    _, lang = normalise(question)
    note = None

    def run(sql: str, mode: str, explanation: str, language: str) -> dict[str, Any]:
        res = sources.query(src, sql)
        return {
            "ok": True, "mode": mode, "question": question, "language": language,
            "sql": res["sql"], "explanation": explanation, "columns": res["columns"],
            "rows": res["rows"], "truncated": res["truncated"], "tables": res["tables"],
            "chart": suggest_chart(res["columns"], res["rows"]), "note": note,
        }

    if client is not None:
        try:
            sql = _llm_sql(client, src, tables, question)
            try:
                return run(sql, "llm", f"written by {client.name}:{client.model}", lang)
            except sqlsafe.QueryError as exc:
                sql2 = _llm_sql(client, src, tables, question, (sql, str(exc)))
                return run(sql2, "llm", f"written by {client.name}:{client.model} (repaired once)", lang)
        except (llm.LLMError, sqlsafe.QueryError) as exc:
            note = f"the model route failed ({exc}); answered with the built-in parser instead"

    plan = None
    if canonical:
        countries = [r[0] for r in sources.rows_of(src, "SELECT DISTINCT country FROM customers")]
        cats = [r[0] for r in sources.rows_of(src, "SELECT DISTINCT category FROM products")]
        plan = compose(question, countries, cats)
    if plan is None:
        plan = generic(question, tables)
    if plan is None:
        return {"ok": False, "question": question, "language": lang, "note": note,
                "error": "I could not turn that into a query without a language model. Try one of "
                         "the examples, or name a measure and a breakdown (for example 'revenue "
                         "by country').", "examples": EXAMPLES if canonical else []}
    try:
        return run(plan["sql"], "template", plan["explanation"], plan["language"])
    except sqlsafe.QueryError as exc:
        return {"ok": False, "question": question, "sql": plan["sql"], "language": lang,
                "error": str(exc), "note": note}
