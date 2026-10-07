import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from analyst_in_a_box import canon


@pytest.fixture(scope="session")
def sample_file(tmp_path_factory):
    """A 700-customer slice of the shipped sample, built once per test session."""
    path = tmp_path_factory.mktemp("sample") / "sample.sqlite3"
    rows = list(canon.read_sample_rows())
    keep = set(sorted({r["customer"] for r in rows})[:700])
    con = sqlite3.connect(path)
    canon.build_from_flat(con, (r for r in rows if r["customer"] in keep))
    con.close()
    return path


@pytest.fixture()
def home(tmp_path, monkeypatch, sample_file):
    monkeypatch.setenv("ANALYST_HOME", str(tmp_path))
    shutil.copy(sample_file, tmp_path / "sample_business.sqlite3")
    return tmp_path


@pytest.fixture()
def client(home):
    from analyst_in_a_box.app import create_app

    with TestClient(create_app(home / "analyst.sqlite3")) as c:
        yield c


@pytest.fixture()
def src(sample_file):
    return {"id": 1, "kind": "sample", "location": str(sample_file), "name": "t"}


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.delenv("ANALYST_LLM", raising=False)
