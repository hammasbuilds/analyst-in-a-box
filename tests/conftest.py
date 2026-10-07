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


PIN = "TESTPIN-1"


class AsUser(TestClient):
    """A test client that signs in as whoever a request body names (`actor` / `requested_by`),
    so the existing tests can keep saying who acts. The server still ignores the body name and
    uses the session; tests of that live in test_security.py with the plain client."""

    default_user = "Bilal Ahmed"
    current: str | None = None
    auto = True

    def sign_in(self, name: str, pin: str = PIN):
        r = super().request("POST", "/api/login", json={"name": name, "pin": pin})
        if r.status_code == 200:
            self.current = name
        return r

    def request(self, method, url, **kw):  # noqa: D102
        if self.auto and method.upper() not in ("GET", "HEAD") and not str(url).endswith(("/login", "/logout")):
            body = kw.get("json") or {}
            who = body.get("actor") or body.get("requested_by") or self.current or self.default_user
            if who != self.current:
                self.sign_in(who)
        return super().request(method, url, **kw)


def _set_pins(path):
    from analyst_in_a_box import appdb, auth, workflows

    con = appdb.connect(path)
    try:
        for u in workflows.USERS:
            auth._store(con, u["name"], PIN)
    finally:
        con.close()


@pytest.fixture()
def plain_client(home):
    """No automatic sign-in: for testing the sign-in rules themselves."""
    from analyst_in_a_box.app import create_app

    app = create_app(home / "analyst.sqlite3")
    _set_pins(home / "analyst.sqlite3")
    with AsUser(app) as c:
        c.auto = False
        yield c


@pytest.fixture()
def client(home):
    from analyst_in_a_box.app import create_app

    app = create_app(home / "analyst.sqlite3")
    _set_pins(home / "analyst.sqlite3")
    with AsUser(app) as c:
        yield c


@pytest.fixture()
def src(sample_file):
    return {"id": 1, "kind": "sample", "location": str(sample_file), "name": "t"}


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.delenv("ANALYST_LLM", raising=False)


@pytest.fixture(autouse=True)
def _fast_pbkdf2(monkeypatch):
    """PIN hashing is deliberately slow in production; keep the suite quick."""
    monkeypatch.setattr("analyst_in_a_box.auth._ITER", 1000)
    from analyst_in_a_box import auth

    auth._fails.clear()
