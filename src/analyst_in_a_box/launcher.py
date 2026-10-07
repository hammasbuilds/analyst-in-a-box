"""``uv run analyst-in-a-box``: start the server and open the browser."""

from __future__ import annotations

import argparse
import socket
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from . import appdb, auth, config
from .app import create_app


def free_port(preferred: int, host: str = "127.0.0.1") -> int:
    for port in [preferred, *range(preferred + 1, preferred + 40)]:
        with socket.socket() as s:
            if s.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError("no free port found")


def _open_when_ready(url: str) -> None:
    for _ in range(400):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1).read()
            break
        except OSError:
            time.sleep(0.2)
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="analyst-in-a-box", description="An AI back office for a small business.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    ap.add_argument("--db", help="app SQLite file (default: ~/.analyst-in-a-box/analyst.sqlite3)")
    ap.add_argument("--reset-pin", metavar="NAME", help="give NAME a new sign-in PIN, print it, and exit")
    a = ap.parse_args(argv)
    path = a.db or str(config.app_db_path())
    if a.reset_pin:
        appdb.init(path)
        con = appdb.connect(path)
        try:
            auth.ensure_credentials(con, None)
            print(f"New PIN for {a.reset_pin}: {auth.reset_pin(con, a.reset_pin)}")
        finally:
            con.close()
        return
    first_run = not config.sample_db_path().exists()
    if first_run:
        print("First run: building the sample business database (about 10 seconds)...")
    app = create_app(path, allowed_hosts=[a.host])  # builds the sample on first run
    if app.state.new_pins:
        print(f"Sign-in PINs for {len(app.state.new_pins)} people were written to "
              f"{app.state.pins_file}. Give each person their own line, then delete the file.")
    port = free_port(a.port, a.host)
    url = f"http://{a.host}:{port}"
    print(f"Analyst-in-a-Box on {url}  (data: {config.home()})  Ctrl+C to stop")
    if not a.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host=a.host, port=port, log_level="warning")
