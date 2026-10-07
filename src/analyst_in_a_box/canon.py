"""The canonical business schema and the loader that builds it from flat order lines.

Five tables: customers, products, orders, order_items, payments. The shipped sample is built from
the UCI "Online Retail II" file (see scripts/prepare_sample.py). An uploaded sheet with one row
per order line can be mapped onto the same schema, so every module (dashboard, forecast, fraud,
risk) works on both.

Honest limits of the mapping from that file:
  * "payments" are derived: one row per invoice, kind 'payment' (positive) or 'refund' (a
    cancellation invoice, negative). The file has no payment method or payment date.
  * "category" is derived from the product description by keyword rules (CATEGORY_RULES); the file
    has no category column.
"""

from __future__ import annotations

import csv
import gzip
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

SAMPLE_CSV = Path(__file__).parent / "sample" / "online_retail_subset.csv.gz"

SCHEMA = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY, country TEXT, first_order_date TEXT, last_order_date TEXT);
CREATE TABLE products (
    stock_code TEXT PRIMARY KEY, description TEXT, category TEXT, is_product INTEGER,
    median_price REAL);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY, customer_id TEXT, order_date TEXT, status TEXT,
    items INTEGER, total REAL);
CREATE TABLE order_items (
    order_id TEXT, stock_code TEXT, quantity INTEGER, unit_price REAL, line_total REAL);
CREATE TABLE payments (
    payment_id INTEGER PRIMARY KEY, order_id TEXT, customer_id TEXT, paid_at TEXT,
    amount REAL, kind TEXT);
CREATE INDEX ix_orders_customer ON orders(customer_id);
CREATE INDEX ix_orders_date ON orders(order_date);
CREATE INDEX ix_items_order ON order_items(order_id);
CREATE INDEX ix_items_stock ON order_items(stock_code);
CREATE INDEX ix_pay_customer ON payments(customer_id);
"""

CANON_TABLES = ("customers", "products", "orders", "order_items", "payments")

# First match wins, so the seasonal rule goes first.
CATEGORY_RULES: list[tuple[str, str]] = [
    ("Christmas", r"CHRISTMAS|XMAS|ADVENT|SANTA|REINDEER|SNOWMAN|SNOWFLAKE|NATIVITY|STOCKING"),
    ("Candles & lighting", r"CANDLE|T-LIGHT|TEALIGHT|LIGHT|LANTERN|LAMP|HOLDER|INCENSE|FAIRY"),
    ("Kitchen & dining", r"MUG|CUP|TEA|PLATE|BOWL|JAR|CAKE|KITCHEN|SPOON|APRON|COFFEE|DINNER|"
                         r"BOTTLE|JUG|COASTER|EGG|BAKING|CUTLERY|PICNIC|LUNCH"),
    ("Bags & accessories", r"BAG|PURSE|WALLET|UMBRELLA|NECKLACE|BRACELET|EARRING|JEWEL|SCARF"),
    ("Cards & wrapping", r"CARD|WRAP|RIBBON|GIFT|TISSUE|PAPER|TAG|ENVELOPE"),
    ("Textiles & cushions", r"CUSHION|BLANKET|THROW|HOT WATER|QUILT|TOWEL|COVER|CURTAIN"),
    ("Toys & party", r"TOY|DOLL|GAME|PUZZLE|BALLOON|PARTY|BIRTHDAY|BUNTING|CHILDREN|KIDS|SKITTLES"),
    ("Garden & outdoor", r"GARDEN|PLANT|FLOWER|BIRD|HERB|WATERING|SEED|POT\b|BUTTERFLY"),
    ("Storage & organising", r"BOX|BASKET|TIN\b|STORAGE|DRAWER|CABINET|SHELF|RACK|TRAY|CRATE"),
    ("Stationery", r"NOTEBOOK|PEN\b|PENCIL|STATIONERY|STICKER|BOOK|DIARY|ERASER|RULER|CRAYON"),
    ("Home decor", r"FRAME|CLOCK|SIGN|MIRROR|DOORMAT|HOOK|HANGER|DECORATION|VASE|HEART|"
                   r"ORNAMENT|WALL|DOOR|PICTURE|SHABBY"),
]
_CATEGORY_RE = [(name, re.compile(rx)) for name, rx in CATEGORY_RULES]
_PRODUCT_CODE = re.compile(r"^\d{5}[A-Za-z]{0,3}$")


def categorise(description: str) -> str:
    d = (description or "").upper()
    for name, rx in _CATEGORY_RE:
        if rx.search(d):
            return name
    return "Other gifts"


def is_product_code(code: str) -> bool:
    return bool(_PRODUCT_CODE.match(code or ""))


_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                 "%d/%m/%Y %H:%M", "%d/%m/%Y", "%m/%d/%Y %H:%M", "%m/%d/%Y")


def parse_date(value: Any) -> str | None:
    """To 'YYYY-MM-DD HH:MM:SS'. Day-first is tried before month-first for slash dates."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _s(value: Any) -> str:
    """str() that keeps a numeric 0 (an id column of integers can legitimately hold 0)."""
    return "" if value is None else str(value).strip()


def _cust(value: Any) -> str:
    s = _s(value)
    return s[:-2] if s.endswith(".0") else s


def build_from_flat(con: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Build the canonical tables from flat order lines.

    Keys per row: invoice, stock_code, description, quantity, date, price, customer, country.
    Returns counts, including how many rows were skipped and why, because a loader that drops
    rows silently reports success on bad input.
    """
    for t in (*CANON_TABLES,):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.executescript(SCHEMA)
    skipped: Counter[str] = Counter()
    items: list[tuple] = []
    order_meta: dict[str, dict[str, Any]] = {}
    desc_count: dict[str, Counter[str]] = defaultdict(Counter)
    prices: dict[str, list[float]] = defaultdict(list)
    cust_country: dict[str, Counter[str]] = defaultdict(Counter)
    n = 0
    for r in rows:
        n += 1
        inv = _s(r.get("invoice"))
        code = _s(r.get("stock_code"))
        cid = _cust(r.get("customer"))
        qty, price = _num(r.get("quantity")), _num(r.get("price"))
        when = parse_date(r.get("date"))
        if not inv or not code:
            skipped["no invoice or product code"] += 1
        elif not cid:
            skipped["no customer id"] += 1
        elif qty is None or price is None:
            skipped["quantity or price not a number"] += 1
        elif when is None:
            skipped["date not understood"] += 1
        elif price < 0:
            skipped["negative price (bad-debt adjustment)"] += 1
        else:
            qty_i = int(qty)
            items.append((inv, code, qty_i, price, round(qty_i * price, 4)))
            m = order_meta.setdefault(inv, {"cid": cid, "date": when, "n": 0, "total": 0.0})
            m["n"] += 1
            m["total"] += qty_i * price
            if when < m["date"]:
                m["date"] = when
            d = str(r.get("description") or "").strip()
            if d:
                desc_count[code][d] += 1
            if price > 0 and qty_i > 0:
                prices[code].append(price)
            cust_country[cid][str(r.get("country") or "Unknown").strip() or "Unknown"] += 1
    if not items:
        raise ValueError("no usable order lines: " + (str(dict(skipped)) if skipped else "empty"))

    con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)
    orders = []
    payments = []
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    for inv, m in order_meta.items():
        status = "cancelled" if inv.upper().startswith("C") or m["total"] < 0 else "completed"
        total = round(m["total"], 2)
        orders.append((inv, m["cid"], m["date"], status, m["n"], total))
        payments.append(
            (inv, m["cid"], m["date"], total, "refund" if status == "cancelled" else "payment")
        )
        first[m["cid"]] = min(first.get(m["cid"], m["date"]), m["date"])
        last[m["cid"]] = max(last.get(m["cid"], m["date"]), m["date"])
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)
    con.executemany(
        "INSERT INTO payments(order_id, customer_id, paid_at, amount, kind) VALUES (?,?,?,?,?)",
        payments,
    )
    con.executemany(
        "INSERT INTO customers VALUES (?,?,?,?)",
        [(c, cust_country[c].most_common(1)[0][0], first[c], last[c]) for c in cust_country],
    )
    prods = []
    for code, dc in desc_count.items():
        desc = dc.most_common(1)[0][0]
        real = is_product_code(code)
        prods.append((code, desc, categorise(desc) if real else "Fees & adjustments", int(real),
                      round(statistics.median(prices[code]), 4) if prices[code] else None))
    seen = {p[0] for p in prods}
    for code in {i[1] for i in items} - seen:
        prods.append((code, code, "Fees & adjustments", 0, None))
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?)", prods)
    con.commit()
    return {
        "rows_read": n, "order_lines": len(items), "orders": len(orders),
        "customers": len(cust_country), "products": len(prods),
        "rows_skipped": sum(skipped.values()), **{f"skipped: {k}": v for k, v in skipped.items()},
    }


def read_sample_rows() -> Iterable[dict[str, Any]]:
    with gzip.open(SAMPLE_CSV, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            yield {
                "invoice": r["Invoice"], "stock_code": r["StockCode"],
                "description": r["Description"], "quantity": r["Quantity"], "date": r["InvoiceDate"],
                "price": r["Price"], "customer": r["CustomerID"], "country": r["Country"],
            }


def build_sample(path: str | Path) -> dict[str, int]:
    p = Path(path)
    if p.exists():
        p.unlink()
    con = sqlite3.connect(p)
    try:
        return build_from_flat(con, read_sample_rows())
    finally:
        con.close()


def has_canonical(con: sqlite3.Connection) -> bool:
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(t in have for t in CANON_TABLES)
