"""Drive the real UI in headless Chromium, save screenshots, and fail on any console error.

    uv run --with playwright python scripts/ui_tour.py http://127.0.0.1:8791 docs/screenshots PINS_FILE

PINS_FILE is the pins.txt written next to the app database on first run (name<TAB>PIN per line).
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def _chrome() -> str | None:
    """Playwright's full Chromium if present (the headless shell may not be installed)."""
    root = Path.home() / "AppData/Local/ms-playwright"
    found = sorted(root.glob("chromium-*/chrome-win64/chrome.exe")) if root.exists() else []
    return str(found[-1]) if found else None


def _pins(path: str) -> dict[str, str]:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            name, pin = line.split("\t")
            out[name] = pin
    return out


def main(base: str, out: str, pins_file: str) -> int:
    pins = _pins(pins_file)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=_chrome())
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))

        def sign_in(name: str) -> None:
            if pg.query_selector("#out-btn"):
                pg.click("#out-btn")
            pg.wait_for_selector("#login")
            pg.select_option("#actor", value=name)
            pg.fill("#pin", pins[name])
            pg.click("#login button[type=submit]")
            pg.wait_for_selector("#out-btn")
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(1500)  # let the re-render after sign-in finish before navigating

        def shot(name: str, full: bool = True) -> None:
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(out_dir / f"{name}.png"), full_page=full)

        pg.goto(base + "/#/")
        pg.wait_for_selector(".kpi")
        shot("01-dashboard")
        pg.wait_for_selector("#login")
        shot("14-sign-in", full=False)
        sign_in("Amna Khan")

        pg.goto(base + "/#/data")
        pg.wait_for_selector("details.card")
        pg.click("details.card summary")
        shot("02-data")

        pg.goto(base + "/#/ask")
        pg.fill("#q", "Top 10 products by revenue in 2011")
        pg.click("#go")
        try:
            pg.wait_for_selector("#sql-box")
        except Exception:
            pg.screenshot(path="ui_tour_failure.png")
            raise
        shot("03-ask")
        pg.fill("#q", "har mahine ki bikri")
        pg.click("#go")
        pg.wait_for_selector(".badge.warn")
        shot("04-ask-roman-urdu")
        pg.fill("#sql-box", "DELETE FROM orders")
        pg.click("#run-sql")
        pg.wait_for_selector(".note.bad")
        shot("05-ask-refused-write", full=False)

        pg.goto(base + "/#/forecast")
        pg.wait_for_selector("#f-chart svg", timeout=90000)
        shot("06-forecast")

        pg.goto(base + "/#/alerts")
        pg.wait_for_selector(".alert", timeout=60000)
        shot("07-alerts")
        pg.click("[data-ticket] >> nth=0")
        pg.wait_for_timeout(800)

        pg.goto(base + "/#/risk")
        pg.wait_for_selector("tr[data-c]", timeout=60000)
        shot("08-risk")
        pg.click("tr[data-c] >> nth=0")
        pg.wait_for_selector(".drawer")
        shot("09-risk-customer", full=False)
        pg.click(".scrim")

        sign_in("Sana Malik")
        pg.goto(base + "/#/workflows")
        pg.wait_for_selector("[data-t]")
        shot("10-workflows")
        pg.click("[data-t] >> nth=0")
        pg.wait_for_selector(".drawer")
        pg.click("[data-do=approve]")
        pg.wait_for_timeout(600)
        shot("11-ticket-approved", full=False)
        pg.click("[data-close] >> nth=1")
        pg.click("#verify")
        pg.wait_for_selector(".toast.show")
        shot("15-audit-verified", full=False)

        pg.emulate_media(color_scheme="dark")
        pg.evaluate("document.documentElement.dataset.theme='dark'")
        pg.goto(base + "/#/")
        pg.wait_for_selector(".kpi")
        shot("12-dashboard-dark")
        pg.set_viewport_size({"width": 390, "height": 800})
        pg.goto(base + "/#/ask")
        pg.wait_for_selector("#q")
        shot("13-mobile-ask", full=False)
        b.close()
    # the refused DELETE in the Ask step is a deliberate 400
    unexpected = [e for e in errors if "400" not in e]
    print("unexpected console errors:", unexpected or "none", f"({len(errors) - len(unexpected)} expected 400)")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
