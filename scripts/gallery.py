"""Recreate every gallery screenshot from the real UI (headless Chromium, dark mode, 1440x900).

    uv run --with playwright --with pillow python scripts/gallery.py [OUT_DIR]

It starts its own server on a free port in 8800-8899 with a temporary data directory (sample database
built fresh, PINs read from that directory's pins.txt), types real inputs, presses the real buttons,
waits for the real output, saves docs/gallery/NN-section.png and stops only the process it started.
Where the page clears the form after a successful action, the shot is the form before (top) stacked on
the result after (bottom). The script fails on any unexpected console error.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
W, H = 1440, 900
BG = (11, 13, 26)


def _chrome() -> str | None:
    root = Path.home() / "AppData/Local/ms-playwright"
    found = sorted(root.glob("chromium-*/chrome-win64/chrome.exe")) if root.exists() else []
    return str(found[-1]) if found else None


def _free_port() -> int:
    for port in range(8800, 8900):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("no free port in 8800-8899")


def _pins(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            name, pin = line.split("\t")
            out[name] = pin
    return out


def _stack(top: Path, bottom: Path, dest: Path, top_label: str, bottom_label: str) -> None:
    a, b = Image.open(top).convert("RGB"), Image.open(bottom).convert("RGB")
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:
        font = ImageFont.load_default()
    bar = 36
    canvas = Image.new("RGB", (max(a.width, b.width), a.height + b.height + 2 * bar + 10), BG)
    d = ImageDraw.Draw(canvas)
    d.text((16, 8), top_label, fill=(160, 170, 255), font=font)
    canvas.paste(a, (0, bar))
    d.text((16, bar + a.height + 18), bottom_label, fill=(120, 230, 200), font=font)
    canvas.paste(b, (0, 2 * bar + a.height + 10))
    canvas.save(dest)


def _sales_csv(path: Path) -> None:
    """A small realistic order-lines sheet (a bakery) for the upload shot."""
    rows = [["invoice", "sku", "description", "qty", "price", "date", "customer", "country"]]
    items = [("SD01", "Sourdough loaf", 4.8), ("CR02", "Butter croissant", 2.2), ("BR03", "Brownie box", 9.5),
             ("CK04", "Lemon drizzle cake", 14.0), ("CO05", "Oat cookie", 1.6), ("BG06", "Bagel six-pack", 6.4)]
    for n in range(40):
        sku, desc, price = items[n % len(items)]
        rows.append([f"B{2000 + n // 2}", sku, desc, 1 + n % 5, price, f"2024-04-{1 + n % 28:02d} 10:{n % 60:02d}",
                     f"K{n % 9:02d}", ["United Kingdom", "Ireland", "France"][n % 3]])
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def main(out: str | None = None) -> int:
    out_dir = Path(out) if out else ROOT / "docs" / "gallery"
    out_dir.mkdir(parents=True, exist_ok=True)
    # a short path inside the repo, so no user name shows in the Sources panel; removed at the end
    work = ROOT / ".gallery-tmp"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    home = work / "data"
    home.mkdir()
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, ANALYST_HOME=str(home), PYTHONIOENCODING="utf-8")
    env.pop("ANALYST_LLM", None)
    proc = subprocess.Popen(
        [sys.executable, "-c", "from analyst_in_a_box.launcher import main; main()", "--no-browser", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors: list[str] = []
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(base + "/api/state", timeout=2).read()
                break
            except Exception:
                time.sleep(1)
        else:
            raise SystemExit("server did not start")
        pins = _pins(home / "pins.txt")
        tmp = work / "shots"
        tmp.mkdir()
        upload = work / "bakery_sales.csv"
        _sales_csv(upload)
        dl_dir = work / "downloads"
        dl_dir.mkdir()

        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=_chrome())
            ctx = b.new_context(viewport={"width": W, "height": H}, color_scheme="dark", accept_downloads=True)
            ctx.add_init_script("try{localStorage.setItem('aib-theme','dark')}catch(e){}")
            pg = ctx.new_page()
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errors.append(str(e)))

            def shot(name: str, full: bool = False, wait: int = 600) -> Path:
                pg.wait_for_timeout(wait)
                path = out_dir / f"{name}.png"
                if full:  # fixed sidebar: grow the viewport to the page height instead of full_page
                    h = min(int(pg.evaluate("document.documentElement.scrollHeight")), 2400)
                    pg.set_viewport_size({"width": W, "height": max(h, H)})
                    pg.wait_for_timeout(500)
                    pg.screenshot(path=str(path))
                    pg.set_viewport_size({"width": W, "height": H})
                else:
                    pg.screenshot(path=str(path))
                return path

            def scroll_to(sel: str, pad: int = 20) -> None:
                pg.evaluate("([s,p])=>{const e=document.querySelector(s);window.scrollTo(0,e.getBoundingClientRect().top+window.scrollY-p)}", [sel, pad])
                pg.wait_for_timeout(300)

            def scroll_card(text: str, pad: int = 120) -> None:
                pg.evaluate("([t,p])=>{const e=[...document.querySelectorAll('details.card')].find(d=>d.querySelector('summary').textContent.includes(t));window.scrollTo(0,e.getBoundingClientRect().top+window.scrollY-p)}", [text, pad])
                pg.wait_for_timeout(300)

            def csv_preview(src: Path, lines: int) -> Path:
                text = chr(10).join(src.read_text(encoding="utf-8-sig").splitlines()[:lines])
                pv = ctx.new_page()
                pv.set_viewport_size({"width": W, "height": 40 + 24 * lines})
                esc = text.replace("&", "&amp;").replace("<", "&lt;")
                pv.set_content(f"<body style='margin:0;background:#0b0d1a;color:#dfe3ff;font:15px/24px Consolas,monospace'><pre style='margin:16px;white-space:pre'>{esc}</pre></body>")
                path = tmp / "preview.png"
                pv.screenshot(path=str(path))
                pv.close()
                return path

            def pair(name: str, top_sel: str, action, top_label: str, bottom_label: str, wait: int = 700, after=None) -> None:
                t = tmp / "top.png"
                pg.locator(top_sel).first.screenshot(path=str(t))
                action()
                pg.wait_for_timeout(wait)
                if after:
                    after()
                    pg.wait_for_timeout(400)
                bpath = tmp / "bottom.png"
                pg.screenshot(path=str(bpath))
                _stack(t, bpath, out_dir / f"{name}.png", top_label, bottom_label)

            def sign_in(name: str, name_shot: str | None = None) -> None:
                if pg.query_selector(".scrim"):
                    pg.locator(".scrim").first.click(position={"x": 5, "y": 5})
                    pg.wait_for_selector(".scrim", state="detached")
                if pg.query_selector("#out-btn"):
                    pg.click("#out-btn")
                pg.wait_for_selector("#login")
                pg.select_option("#actor", value=name)
                pg.fill("#pin", pins[name])
                if name_shot:
                    shot(name_shot)
                pg.click("#login button[type=submit]")
                pg.wait_for_selector("#out-btn")
                pg.wait_for_load_state("networkidle")
                pg.wait_for_timeout(1500)

            def goto(route: str, ready: str, timeout: int = 60000) -> None:
                pg.goto(f"{base}/#/{route}")
                pg.wait_for_selector(ready, timeout=timeout)

            # ---------------------------------------------------------------- home / Try-it
            goto("", "#t-out .sqlview")
            pg.wait_for_selector(".kpi")
            shot("01-home", wait=1200)
            for i, n in enumerate(["02", "03", "04", "05"]):
                pg.click(f"#t-chips .chip >> nth={i}")
                pg.wait_for_selector("#t-out .skel", state="detached")
                pg.wait_for_selector("#t-out .sqlview")
                q = pg.input_value("#t-q")
                info = pg.inner_text("#t-out .timing")
                assert pg.query_selector("#t-out table"), q
                print(f"try-it {i + 1}: {q!r} -> {info}")
                scroll_to(".hero .pane", 24)
                shot(f"{n}-tryit-{i + 1}", wait=900)
                pg.evaluate("window.scrollTo(0,0)")
            pg.set_viewport_size({"width": 390, "height": 844})
            goto("", "#t-out .sqlview")
            shot("06-home-phone", wait=1200)
            pg.set_viewport_size({"width": W, "height": H})

            # ---------------------------------------------------------------- ask (sample data, read only)
            goto("ask", "#q")
            pg.fill("#q", "Top 10 products by revenue in 2011")
            pg.click("#go")
            pg.wait_for_selector("#sql-box")
            shot("10-ask-english", full=True, wait=1200)
            pg.fill("#q", "har mahine ki bikri")
            pg.click("#go")
            pg.wait_for_selector(".badge.warn")
            shot("11-ask-roman-urdu", full=True, wait=1200)
            # CSV export of this result (Ask page "Download CSV")
            with pg.expect_download() as dl:
                pg.click("#csv-sql")
            csv_path = dl_dir / "analyst-export.csv"
            dl.value.save_as(str(csv_path))
            pg.wait_for_selector(".toast.show")
            top = tmp / "ask.png"
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(top))
            _stack(top, csv_preview(csv_path, 8), out_dir / "23-export-query-csv.png",
                   "Input: Download CSV pressed under the Ask result (monthly revenue, 25 rows)",
                   "Output: the file that was saved, analyst-export.csv (first lines)")
            # a short result first, so the refusal and the edited SQL fit in one screen
            pg.evaluate("window.scrollTo(0,0)")
            pg.fill("#q", "how many customers do we have")
            pg.click("#go")
            pg.wait_for_selector("#sql-box")
            pg.fill("#sql-box", "DELETE FROM orders")
            pg.click("#run-sql")
            pg.wait_for_selector(".note.bad")
            pg.wait_for_selector(".toast.show", state="detached", timeout=15000)
            scroll_to("#ask-out", 60)
            shot("12-ask-refused-write", wait=800)
            csv_lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
            print("exported", len(csv_lines) - 1, "rows;", csv_lines[0])

            # ---------------------------------------------------------------- data: schema browser
            goto("data", "details.card")
            pg.locator("details.card summary", has_text="orders").first.click()
            pg.locator("details.card summary", has_text="order_items").first.click()
            pg.locator("details.card").first.scroll_into_view_if_needed()
            shot("07-data-schema", full=True, wait=800)

            # ---------------------------------------------------------------- forecasts
            goto("forecast", "#f-chart svg", 120000)
            pg.select_option("#f-h", "13")
            pg.click("#f-go")
            pg.wait_for_timeout(500)
            pg.wait_for_selector("#f-chart svg", timeout=120000)
            shot("13-forecast", full=True, wait=1500)

            # ---------------------------------------------------------------- fraud & anomalies
            goto("alerts", ".alert", 120000)
            pg.select_option("#a-sv", "high")
            pg.wait_for_timeout(2500)
            pg.wait_for_selector(".alert.high")
            shot("14-alerts-detail", wait=1000)

            # ---------------------------------------------------------------- customer risk
            goto("risk", "tr[data-c]", 120000)
            pg.locator("h2", has_text="Fairness panel").scroll_into_view_if_needed()
            shot("15-risk-fairness", wait=800)
            pg.evaluate("window.scrollTo(0,0)")
            pg.select_option("#r-dec", "DECLINE")
            pg.wait_for_timeout(1500)
            pg.click("tr[data-c] >> nth=0")
            pg.wait_for_selector(".drawer")
            shot("16-risk-customer", wait=800)
            pg.click(".scrim")

            # ---------------------------------------------------------------- workflows
            goto("workflows", "#n-go")
            sign_in("Amna Khan", "17-workflows-sign-in")
            goto("workflows", "#n-go")
            pg.select_option("#n-kind", "refund")
            pg.fill("#n-title", "Refund for damaged goods")
            pg.fill("#n-cust", "15362")
            pg.fill("#n-order", "489437")
            pg.fill("#n-amt", "250")
            pg.fill("#n-reason", "Customer reports the box arrived crushed; photo on file")
            pair("18-workflows-raise-refund", ".card:has(#n-go)", lambda: pg.click("#n-go"),
                 "Input: the New ticket form as filled in (signed in as Amna Khan, staff)",
                 "Output: ticket raised, and it needs two different managers (refund over 100)")
            sign_in("Bilal Ahmed")
            goto("workflows", "[data-t]")
            pg.click("[data-t] >> nth=0")
            pg.wait_for_selector(".drawer")
            pg.fill("#t-c", "Photo matches the packing slip")
            pg.click("[data-do=approve]")
            pg.wait_for_selector(".drawer .small b:text('Bilal Ahmed')")
            shot("19-workflows-first-approval", wait=900)
            sign_in("Sana Malik")
            goto("workflows", "[data-t]")
            pg.click("[data-t] >> nth=0")
            pg.wait_for_selector(".drawer")
            pg.fill("#t-c", "Second look done, approving")
            pg.click("[data-do=approve]")
            pg.wait_for_selector("[data-do=execute]")
            shot("20-workflows-second-approval", wait=900)
            pg.click("[data-do=execute]")
            pg.wait_for_selector(".drawer .badge.good")
            pg.click("[data-close] >> nth=1")
            pg.wait_for_timeout(600)
            pg.click("#verify")
            pg.wait_for_selector(".toast.show:has-text('Audit')")
            toast = pg.inner_text(".toast.show")
            print("verify:", toast)
            assert toast.startswith("Audit intact"), toast
            shot("21-workflows-audit-verify", full=True, wait=500)
            with pg.expect_download() as dl:
                pg.click("a[href='/api/audit/export']")
            audit_path = dl_dir / "audit.csv"
            dl.value.save_as(str(audit_path))
            pg.wait_for_timeout(400)
            top = tmp / "wf.png"
            pg.screenshot(path=str(top))
            _stack(top, csv_preview(audit_path, 9), out_dir / "24-export-audit-csv.png",
                   "Input: Download CSV pressed on the audit trail (Workflows page)",
                   "Output: the file that was saved, the hash-chained audit entries (first lines)")
            print("audit export:", len(audit_path.read_text(encoding="utf-8-sig").splitlines()) - 1, "rows")

            # ---------------------------------------------------------------- dashboard
            goto("", ".kpi")
            pg.wait_for_selector("#dash .card")
            shot("22-dashboard", full=True, wait=2500)

            # ---------------------------------------------------------------- about
            goto("about", ".guide-sec")
            shot("25-about", wait=800)

            # ---------------------------------------------------------------- data: upload and paste (last: they switch the active source)
            sign_in("Bilal Ahmed")
            goto("data", "#drop")
            pg.set_input_files("#file", str(upload))
            pg.wait_for_selector("details.card summary:has-text('bakery_sales')", timeout=60000)
            pg.locator("details.card summary", has_text="bakery_sales").first.click()
            scroll_card("bakery_sales")
            shot("08-data-upload-file", wait=900)
            pg.click("#p-ex")
            pg.fill("#p-name", "pasted_sales")
            pair("09-data-paste-csv", ".card:has(#p-go)", lambda: pg.click("#p-go"),
                 "Input: CSV text pasted into the box (the example rows)",
                 "Output: imported as a table, schema and sample rows listed",
                 after=lambda: (pg.locator("details.card summary", has_text="pasted_sales").first.click(),
                                scroll_card("pasted_sales", 140)))
            b.close()
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)
    unexpected = [e for e in errors if "400" not in e]
    print("unexpected console errors:", unexpected or "none", f"({len(errors) - len(unexpected)} expected 400)")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
