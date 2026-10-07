"""Where things live. ANALYST_HOME moves everything (database, uploads, sample copy)."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Analyst-in-a-Box"


def home() -> Path:
    p = Path(os.environ.get("ANALYST_HOME") or Path.home() / ".analyst-in-a-box")
    p.mkdir(parents=True, exist_ok=True)
    return p


def app_db_path() -> Path:
    return home() / "analyst.sqlite3"


def sample_db_path() -> Path:
    return home() / "sample_business.sqlite3"


def uploads_db_path() -> Path:
    return home() / "uploads.sqlite3"


MAX_ROWS = 1000  # result cap for any question
QUERY_TIMEOUT_S = 8.0  # wall-clock cap for any question
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_UNZIPPED_BYTES = 400 * 1024 * 1024  # an .xlsx may not unpack to more than this
MAX_UPLOAD_ROWS = 1_000_000
MAX_UPLOAD_COLUMNS = 500
MAX_CELL_CHARS = 100_000
MAX_JSON_BODY = 2 * 1024 * 1024  # any non-upload request body
EXPORT_ROWS = 50_000
