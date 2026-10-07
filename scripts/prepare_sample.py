"""Cut the shipped sample from the UCI "Online Retail II" file.

Source: Chen, D. (2012), UCI Machine Learning Repository, CC BY 4.0. A UK online giftware wholesaler,
1 Dec 2009 to 9 Dec 2011. This script keeps every row of a seeded random sample of customers
(rows without a CustomerID are dropped, because a payment needs a customer) and writes a gzip CSV.

    python scripts/prepare_sample.py "D:/github/machine-learning/data/raw/online-retail-ii.csv"
"""

from __future__ import annotations

import csv
import gzip
import random
import sys
from pathlib import Path

N_CUSTOMERS = 2000
SEED = 20261007
OUT = Path(__file__).resolve().parents[1] / "src" / "analyst_in_a_box" / "sample" / "online_retail_subset.csv.gz"


def main(src: str) -> None:
    with open(src, encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["CustomerID"].strip()]
    customers = sorted({r["CustomerID"] for r in rows})
    keep = set(random.Random(SEED).sample(customers, N_CUSTOMERS))
    rows = [r for r in rows if r["CustomerID"] in keep]
    cols = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "CustomerID", "Country"]
    with gzip.open(OUT, "wt", encoding="utf-8", newline="", compresslevel=9) as out:
        w = csv.DictWriter(out, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r[c] for c in cols} for r in rows)
    print(f"{len(customers)} customers with an ID -> kept {len(keep)}; {len(rows)} rows -> {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main(sys.argv[1])
