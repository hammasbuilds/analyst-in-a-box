"""Exhaustive UI audit: every control on every page, real input, checked output.

    uv run --with playwright python scripts/ui_audit.py [--quick]

Starts its own server on a free port in 8800-8899 with a fresh temporary data folder (sample data
built fresh, PINs read from that folder's pins.txt), opens headless Edge (Playwright's bundled
Chromium if Edge is missing) and, at 1440x900 dark, 1440x900 light and 390x844, for every page:

  * enumerates every button, link, chip, tab, select, checkbox, input, textarea, file input, drop
    zone and clickable row from the DOM (nothing hand-picked) and acts on each with a realistic value;
    controls that appear after an action (a drawer, a new form) are enumerated and exercised too;
  * waits for the requests to finish and the spinners to stop, then records console and page errors,
    failed requests (a 4xx counts as an intended refusal only when the page shows the reason),
    text that overflows or is clipped by its container or the viewport, chart labels cut mid-word,
    NaN / undefined / null / [object Object] in visible text, a result left on screen next to an
    error, and spinners that never stop.

Then it runs the scripted flows that need a sequence (sign in as staff, manager and owner; raise,
approve, reject, cancel, execute; the four-eyes refusal; audit Verify and CSV; bad input on the Data
page). It prints one table row per action, the denominator (controls found, exercised, and why any
were not), and exits non-zero on any problem. Only the server process it started is stopped.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PASTE = ("invoice,sku,description,qty,price,date,customer,country\n"
         "A1001,MUG1,Blue mug,2,4.50,2024-03-01,C01,United Kingdom\n"
         "A1001,TEA2,Green tea tin,1,6.00,2024-03-01,C01,United Kingdom\n"
         "A1002,MUG1,Blue mug,5,4.50,2024-03-02,C02,Ireland")
PAGES = [("", "Dashboard", "#t-out .sqlview, #t-out .note"), ("ask", "Ask", "#q"),
         ("forecast", "Forecasts", "#f-chart svg, #f-out .note"), ("alerts", "Fraud & anomalies", "#a-list .alert, #a-list .empty"),
         ("risk", "Customer risk", "tr[data-c]"), ("workflows", "Workflows", "#n-go"),
         ("about", "About", ".guide-sec"), ("data", "Data", "#drop")]
CONFIGS = [("1440 dark", 1440, 900, "dark"), ("1440 light", 1440, 900, "light"), ("390 phone", 390, 844, "dark")]
# repeated row controls are sampled: every alert would otherwise be ticketed one by one
GROUP_CAP = {"[data-ticket]": 2, "[data-dismiss]": 2, "[data-t]": 4, "tr[data-c]": 25, "[data-do]": 6}

ENUM_JS = r"""
(scope) => {
  const root = document.querySelector('.drawer') || document.querySelector(scope);
  if (!root) return [];
  const SEL = 'button, a[href], select, input, textarea, [role=button], [tabindex="0"], tr.click, tr[data-c], [data-t], summary';
  const vis = (e) => { if (e.type === 'file') return true; const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    const closed = e.closest('details:not([open])'); if (closed && !(e.matches('summary') && e.parentElement === closed)) return false;
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !e.closest('[hidden]') && (!e.checkVisibility || e.checkVisibility()); };
  const DATA = ['q','act','st','n','c','t','do','jump','ticket','dismiss','map','r','close'];
  const seen = {};
  const name = (e) => (/^(input|textarea|select)$/i.test(e.tagName) ? (e.getAttribute('aria-label') || e.getAttribute('placeholder') || '')
    : (e.innerText || e.getAttribute('aria-label') || '')).trim().replace(/\s+/g, ' ').slice(0, 48);
  return [...root.querySelectorAll(SEL)].filter(vis).filter((e) => !e.disabled).map((e) => {
    const tag = e.tagName.toLowerCase(), type = (e.getAttribute('type') || '').toLowerCase();
    let sel = null, group = null;
    if (e.id) sel = '#' + CSS.escape(e.id);
    else for (const d of DATA) if (e.hasAttribute('data-' + d)) {
      const v = e.getAttribute('data-' + d);
      sel = `${tag}[data-${d}="${CSS.escape(v)}"]`; group = (tag === 'tr' && d === 'c') ? 'tr[data-c]' : `[data-${d}]`; break; }
    if (!sel && tag === 'a') sel = `a[href="${CSS.escape(e.getAttribute('href'))}"]`;
    const text = name(e);
    if (!sel) sel = tag + (type ? `[type="${type}"]` : '');
    const k = sel + '|' + text; seen[k] = (seen[k] || 0) + 1;
    return { sel, text, idx: seen[k] - 1, tag, type, group: group || sel, inDrawer: !!e.closest('.drawer'),
             href: e.getAttribute('href') || '', target: e.getAttribute('target') || '', download: e.hasAttribute('download') };
  });
}
"""

FIND_JS = r"""
([sel, text, idx, mark, scope]) => {
  const root = document.querySelector('.drawer') || document.querySelector(scope) || document;
  const norm = (e) => (/^(input|textarea|select)$/i.test(e.tagName) ? (e.getAttribute('aria-label') || e.getAttribute('placeholder') || '')
    : (e.innerText || e.getAttribute('aria-label') || '')).trim().replace(/\s+/g, ' ').slice(0, 48);
  const all = [...root.querySelectorAll(sel)].filter((e) => norm(e) === text);
  const e = all[idx]; if (!e) return false;
  document.querySelectorAll('[data-audit]').forEach((x) => x.removeAttribute('data-audit'));
  e.setAttribute('data-audit', mark); return true;
}
"""

CHECK_JS = r"""
() => {
  const out = [];
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0' && (!e.checkVisibility || e.checkVisibility()); };
  const W = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > W + 1) out.push(`page scrolls sideways: ${document.documentElement.scrollWidth}px wide at ${W}px`);
  const text = (e) => [...e.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
  const isScroll = (e) => { const s = getComputedStyle(e); return /(auto|scroll)/.test(s.overflowX); };
  const isClip = (e) => { const s = getComputedStyle(e); return /(hidden|clip)/.test(s.overflowX); };
  const BOX = '.card, .kpi, .pane, .alert, .drawer, .hero, .note, .toast, main, aside, .chip, .btn, .badge';
  for (const e of document.querySelectorAll('main *, aside *, .toast')) {
    if (e.closest('svg') || !vis(e)) continue;
    const t = text(e); if (!t) continue;
    const r = e.getBoundingClientRect();
    if (r.right <= 0 || r.bottom <= -5000) continue;  // a visually hidden label for screen readers
    // own text clipped by its own overflow, without an ellipsis
    // measure the text itself (a ripple or a decoration can widen scrollWidth without hiding any text)
    const tr = (() => { const rg = document.createRange(); let R = -1e9, L = 1e9;
      for (const n of e.childNodes) if (n.nodeType === 3 && n.textContent.trim()) { rg.selectNodeContents(n); const b = rg.getBoundingClientRect(); R = Math.max(R, b.right); L = Math.min(L, b.left); }
      return { right: R, left: L }; })();
    // a word split over two lines ("smoot" / "h"); breaks at a hyphen or a path separator are allowed
    for (const n of e.childNodes) if (n.nodeType === 3) { const re = /[^\s\-\/\\]{2,}/g; let m;
      while ((m = re.exec(n.textContent))) { const rg = document.createRange(); rg.setStart(n, m.index); rg.setEnd(n, m.index + m[0].length);
        const ls = new Set([...rg.getClientRects()].filter((q) => q.width > 0).map((q) => Math.round(q.top)));
        if (ls.size > 1) { out.push(`word broken across lines: "${m[0].slice(0, 30)}"`); break; } } }
    if (isClip(e) && (tr.right > r.right + 1 || tr.left < r.left - 1) && getComputedStyle(e).textOverflow !== 'ellipsis')
      out.push(`text clipped: "${t.slice(0, 40)}" (${e.tagName.toLowerCase()}.${e.className})`);
    if (getComputedStyle(e).textOverflow === 'ellipsis' && e.scrollWidth > e.clientWidth + 1 && !e.closest('[title], [aria-label]'))
      out.push(`text cut with an ellipsis and no tooltip: "${t.slice(0, 40)}"`);
    let a = e.parentElement;
    while (a && !(isScroll(a) || isClip(a) || a.matches(BOX))) a = a.parentElement;
    if (!a || isScroll(a)) continue;
    const ar = a.getBoundingClientRect();
    if (r.right > ar.right + 2 || r.left < ar.left - 2) out.push(`text overflows its ${a.tagName.toLowerCase()}.${String(a.className).split(' ')[0]}: "${t.slice(0, 40)}" (${Math.round(r.right - ar.right)}px)`);
    if (r.right > W + 1) out.push(`text past the viewport: "${t.slice(0, 40)}"`);
  }
  // chart labels: the drawn text must be the label, its start with an ellipsis, or a date without the century
  for (const tx of document.querySelectorAll('main svg text')) {
    const ti = tx.querySelector('title'); if (!ti) continue;
    const full = ti.textContent, shown = [...tx.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('');
    const ok = shown === full || (shown.endsWith('…') && full.startsWith(shown.slice(0, -1).trimEnd())) || (/^\d{4}-/.test(full) && full.slice(2).startsWith(shown));
    if (!ok) out.push(`chart label cut mid-word: "${shown}" for "${full}"`);
  }
  // svg text past its own chart box
  for (const svg of document.querySelectorAll('main svg.chart')) {
    if (!vis(svg)) continue; const sr = svg.getBoundingClientRect();
    for (const tx of svg.querySelectorAll('text')) { const r = tx.getBoundingClientRect(); if (r.width && (r.right > sr.right + 2 || r.left < sr.left - 2)) { out.push(`chart text outside its chart: "${tx.textContent.slice(0, 30)}"`); break; } }
  }
  // NaN / undefined / null in what a person reads (SQL text and inputs excluded)
  const main = document.querySelector('main'), side = document.querySelector('aside');
  for (const box of [main, side]) {
    const w = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
    let n; while ((n = w.nextNode())) {
      const p = n.parentElement; if (!p || p.closest('textarea, .sqlview, pre, code, .mono, script, style, title') || !vis(p)) continue;
      const m = n.textContent.match(/\b(NaN|undefined|null|Infinity)\b|\[object Object\]/); if (m) out.push(`"${m[0]}" shown: "${n.textContent.trim().slice(0, 50)}"`);
    }
  }
  for (const k of document.querySelectorAll('main .kpi .v, main h1, main h2')) if (vis(k) && !k.textContent.trim() && !k.querySelector('svg')) out.push(`empty ${k.tagName.toLowerCase()} ${k.className}`);
  for (const b of document.querySelectorAll('main button, aside button, main a, aside a')) if (vis(b) && !b.textContent.trim() && !b.getAttribute('aria-label') && !b.title) out.push('control with no name');
  // a result left next to an error
  for (const note of document.querySelectorAll('main .note.bad')) {
    if (!vis(note)) continue; const c = note.closest('[id]:not(#app)'); if (!c) continue;
    if (c.querySelector('table, svg.chart') && !c.querySelector('.stale-label')) out.push(`stale result next to an error in #${c.id}: "${note.textContent.trim().slice(0, 50)}"`);
  }
  return out;
}
"""

BUSY_JS = "() => (window.__inflight || 0) + [...document.querySelectorAll('.spin, .skel, .is-busy')].filter((e) => e.getBoundingClientRect().width > 0).length"
BUSY_DETAIL_JS = "() => [`${window.__inflight || 0} requests`, ...[...document.querySelectorAll('.spin, .skel, .is-busy')].filter((e) => e.getBoundingClientRect().width > 0).map((e) => e.className + ' in ' + (e.closest('[id]') || {}).id)].slice(0, 4).join(', ')"
INFLIGHT_INIT = """(() => { window.__inflight = 0; const f = window.fetch.bind(window);
  window.fetch = (...a) => { window.__inflight++; const p = f(...a); p.finally(() => window.__inflight--).catch(() => {}); return p; };
  const o = XMLHttpRequest.prototype.send; XMLHttpRequest.prototype.send = function (...a) { window.__inflight++;
    this.addEventListener('loadend', () => window.__inflight--); return o.apply(this, a); }; })();"""
VISIBLE_ERROR_JS = """() => { const v = (e) => e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).opacity !== '0';
  const t = document.querySelector('.toast.bad.show'); const n = [...document.querySelectorAll('main .note.bad')].filter(v);
  return (t ? t.textContent : '') + ' ' + n.map((x) => x.textContent).join(' '); }"""


@dataclass
class Row:
    config: str
    page: str
    control: str
    action: str
    result: str
    problems: list[str] = field(default_factory=list)


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


def _launch(p):
    try:
        return p.chromium.launch(channel="msedge")
    except Exception:
        root = Path.home() / "AppData/Local/ms-playwright"
        found = sorted(root.glob("chromium-*/chrome-win64/chrome.exe")) if root.exists() else []
        return p.chromium.launch(executable_path=str(found[-1]) if found else None)


class Auditor:
    def __init__(self, page: Page, base: str, pins: dict[str, str], files: dict[str, Path], config: str):
        self.pg, self.base, self.pins, self.files, self.config = page, base, pins, files, config
        self.rows: list[Row] = []
        self.found: dict[str, int] = {}
        self.exercised: dict[str, int] = {}
        self.skipped: dict[str, list[str]] = {}
        self.inflight = 0
        self.events: list[str] = []
        self.bad_responses: list[tuple[int, str]] = []
        page.on("request", lambda r: self._inc(1))
        page.on("requestfinished", lambda r: self._inc(-1))
        page.on("requestfailed", lambda r: (self._inc(-1), self.events.append(f"request failed: {r.url} {r.failure}")))
        page.on("response", lambda r: self.bad_responses.append((r.status, r.url)) if r.status >= 400 else None)
        page.on("console", lambda m: self.events.append("console: " + m.text)
                if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.events.append(f"page error: {e}"))
        page.on("dialog", lambda d: d.dismiss())
        self.pending_pin: tuple[str, str] | None = None

    def _inc(self, d: int) -> None:
        self.inflight = max(0, self.inflight + d)

    # ------------------------------------------------------------------ waiting and checking
    def settle(self, timeout: float = 120.0) -> list[str]:
        """Wait until no request is in flight for 400 ms and no spinner is visible."""
        end = time.time() + timeout
        quiet_since = None
        while time.time() < end:
            busy = self.pg.evaluate(BUSY_JS) > 0
            if busy:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since > 0.4:
                return []
            self.pg.wait_for_timeout(100)
        return [f"still busy after {timeout:.0f}s: {self.pg.evaluate(BUSY_DETAIL_JS)}"]

    def check(self, before_events: int, before_resp: int) -> tuple[list[str], str]:
        probs = list(self.pg.evaluate(CHECK_JS))
        probs += self.events[before_events:]
        result = ""
        for status, url in self.bad_responses[before_resp:]:
            path = url.replace(self.base, "")
            if status >= 500:
                probs.append(f"server error {status} {path}")
                continue
            shown = self.pg.evaluate(VISIBLE_ERROR_JS).strip()
            if shown:
                result = f"refused {status}, shown: {shown[:70]}"
            else:
                probs.append(f"{status} {path} with no message on the page")
        return probs, result

    # ------------------------------------------------------------------ one control
    def value_for(self, c: dict) -> str | None:
        ident = c["sel"].lstrip("#").replace("\\", "")
        actor = self.pg.evaluate("() => (document.querySelector('#actor') || {}).value || ''")
        values = {
            "t-q": "revenue by country", "q": "Top 10 products by revenue in 2011",
            "sql-box": "SELECT country, COUNT(*) AS customers FROM customers GROUP BY country ORDER BY 2 DESC",
            "s-loc": str(self.files["sqlite"]), "s-name": "Shop copy", "p-name": "pasted_sales", "p-text": PASTE,
            "n-title": "Refund for damaged goods", "n-cust": "15362", "n-order": "489437", "n-amt": "250",
            "n-reason": "Box arrived crushed; photo on file", "t-c": "Checked the photos", "cl-new": "1500",
            "pin": self.pins.get(actor, ""), "pin-old": self.pins.get(self.signed_in() or "", ""),
        }
        if ident == "pin-new":
            who = self.signed_in() or ""
            new = "NEWPIN-" + str(int(time.time()) % 100000)
            self.pending_pin = (who, new)
            return new
        if ident in values:
            return values[ident]
        return "100" if c["type"] == "number" else "test value"

    def signed_in(self) -> str | None:
        label = self.pg.evaluate("() => (document.querySelector('#who-toggle') || {getAttribute: () => ''}).getAttribute('aria-label') || ''")
        m = re.match(r"Signed in as (.+); account", label)
        return m.group(1) if m else None

    def act(self, page_name: str, scope: str, route: str, c: dict) -> None:
        pg = self.pg
        label = f"{c['tag']}{'[' + c['type'] + ']' if c['type'] else ''} {c['sel']} \"{c['text']}\""
        if not pg.evaluate(FIND_JS, [c["sel"], c["text"], c["idx"], "x", scope]):
            self.skipped.setdefault("gone before its turn (re-rendered by an earlier action)", []).append(label)
            return
        el = pg.locator("[data-audit=x]")
        pg.evaluate("() => { const t = document.querySelector('.toast'); if (t) t.classList.remove('show', 'bad'); }")
        e0, r0 = len(self.events), len(self.bad_responses)
        hash0 = pg.evaluate("location.hash")
        action, result, probs = "", "", []
        try:
            if c["target"] == "_blank":
                href = c["href"]
                action, result = "check link", ("external link " + href) if href.startswith("https://") else "bad link"
                if not href.startswith("https://"):
                    probs.append(f"external link is not https: {href}")
            elif c["download"] or c["href"].startswith("/api/"):
                with pg.expect_download(timeout=30000) as dl:
                    el.click()
                path = Path(tempfile.mkdtemp()) / "dl"
                dl.value.save_as(str(path))
                n = len(path.read_text(encoding="utf-8-sig").splitlines())
                action, result = "click (download)", f"downloaded {dl.value.suggested_filename}, {n} lines"
                if n < 2:
                    probs.append("download has no data rows")
            elif c["tag"] == "select":
                opts = el.evaluate("(s) => [...s.options].map((o) => o.value)")
                cur = el.input_value()
                want = {"actor": "Bilal Ahmed", "f-h": "13", "a-sv": "high"}.get(c["sel"].lstrip("#"))
                if want is None:
                    want = next((o for o in opts if o != cur and o), cur) if not c["sel"].startswith("select[data-map") else cur
                el.select_option(want)
                action, result = f"choose {want!r}", f"{len(opts)} options"
            elif c["type"] == "file":
                el.set_input_files(str(self.files["csv"]))
                action = "choose file bakery_sales.csv"
            elif c["tag"] in ("input", "textarea") and c["type"] not in ("checkbox", "radio", "submit", "button"):
                v = self.value_for(c) or ""
                el.fill(v)
                if c["sel"] in ("#t-q", "#q"):
                    el.press("Enter")
                    action = f"type {v[:30]!r} + Enter"
                else:
                    action = f"fill {('*' * len(v)) if 'pin' in c['sel'] else v[:30]!r}"
            elif c["type"] in ("checkbox", "radio"):
                el.check()
                action = "check"
            elif c["sel"] == "#drop":
                pg.evaluate("""async (text) => { const dt = new DataTransfer(); dt.items.add(new File([text], 'dropped_sales.csv', {type: 'text/csv'}));
                    const d = document.querySelector('#drop'); for (const t of ['dragenter', 'dragover', 'drop']) d.dispatchEvent(new DragEvent(t, {dataTransfer: dt, bubbles: true, cancelable: true})); }""",
                            self.files["csv"].read_text(encoding="utf-8"))
                action = "drop a CSV file"
            else:
                el.scroll_into_view_if_needed(timeout=5000)
                el.click(timeout=10000)
                action = "click"
            probs += self.settle()
            if self.pending_pin and pg.evaluate("() => (document.querySelector('.toast.show') || {}).textContent || ''").startswith("PIN changed"):
                self.pins[self.pending_pin[0]] = self.pending_pin[1]
                self.pending_pin = None
            p2, r2 = self.check(e0, r0)
            probs += p2
            result = result or r2
            h = pg.evaluate("location.hash")
            if h != hash0:
                result = result or f"navigated to {h or '#/'}"
            if not result:
                toast = pg.evaluate("() => { const t = document.querySelector('.toast.show'); return t ? t.textContent : ''; }")
                result = ("toast: " + toast[:60]) if toast else "ok"
        except Exception as ex:  # a control that cannot be operated is a finding
            probs.append(f"could not operate: {str(ex).splitlines()[0][:120]}")
        self.rows.append(Row(self.config, page_name, label, action, result, probs))
        if os.environ.get("AUDIT_PROGRESS"):
            print(f"[{self.config}] {page_name}: {label[:60]} -> {action} / {result[:50]} {'; '.join(probs)[:200]}", flush=True)
        self.exercised[page_name] = self.exercised.get(page_name, 0) + 1
        # back to the page under audit if the control navigated away
        if pg.evaluate("location.hash") != hash0 and c["target"] != "_blank":
            self.goto(route)

    def goto(self, route: str) -> None:
        ready = next(r for (rt, _, r) in PAGES if rt == route) if route != "__side" else "#nav a"
        self.pg.goto(f"{self.base}/#/{'' if route == '__side' else route}")
        self.pg.wait_for_selector(ready, timeout=180000)
        self.settle()

    # ------------------------------------------------------------------ one page
    def audit_page(self, route: str, name: str, scope: str = "#app") -> None:
        self.goto(route)
        e0, r0 = len(self.events), len(self.bad_responses)
        probs, _ = self.check(e0, r0)
        self.rows.append(Row(self.config, name, "(page)", "load", "loaded", probs))
        done: set[str] = set()
        per_group: dict[str, int] = {}
        found_keys: set[str] = set()
        for _ in range(600):
            ctrls = self.pg.evaluate(ENUM_JS, scope)
            for c in ctrls:
                found_keys.add(f"{c['sel']}|{c['text']}|{c['idx']}")
            todo = []
            for c in ctrls:
                key = f"{c['sel']}|{c['text']}|{c['idx']}"
                if key in done:
                    continue
                cap = GROUP_CAP.get(c["group"])
                if cap is not None and per_group.get(c["group"], 0) >= cap:
                    done.add(key)
                    self.skipped.setdefault(f"sampled: {c['group']} is a repeated row control (tickets, dismissals, customers), {cap} exercised per page", []).append(f"{name}: {c['text']}")
                    continue
                todo.append(c)
            if not todo:
                if self.pg.query_selector(".drawer"):  # housekeeping: nothing left inside, close it
                    self.pg.locator(".drawer [data-close]").first.click()
                    self.settle()
                    continue
                break
            # close / cancel / sign-out last, so the controls they would hide get their turn first
            todo.sort(key=lambda c: (2 if (c["sel"].endswith('[data-close=""]') or c["text"] in ("Close", "Cancel", "Sign out"))
                                     else 1 if c["tag"] in ("summary", "select") else 0))
            c = todo[0]
            done.add(f"{c['sel']}|{c['text']}|{c['idx']}")
            per_group[c["group"]] = per_group.get(c["group"], 0) + 1
            self.act(name, scope, route, c)
        self.found[name] = self.found.get(name, 0) + len(found_keys)
        gone = found_keys - done
        if gone:
            self.skipped.setdefault("seen once, then removed by an earlier action on the same page (a filter or re-render)", []).extend(f"{name}: {k.split('|')[1][:30]}" for k in gone)


def scripted(a: Auditor, base: str) -> None:
    """Flows that need an order: roles, four-eyes, the full ticket life cycle, bad input on Data."""
    pg = a.pg
    rows = a.rows

    def fresh(route: str, ready: str) -> None:
        pg.goto(f"{base}/#/{route}")
        pg.reload()
        pg.wait_for_selector(ready, timeout=180000)
        a.settle()

    def step(control: str, action: str, fn, expect_refusal: str | None = None) -> None:
        pg.evaluate("() => { const t = document.querySelector('.toast'); if (t) t.classList.remove('show', 'bad'); }")
        e0, r0 = len(a.events), len(a.bad_responses)
        probs: list[str] = []
        try:
            fn()
            probs += a.settle()
        except Exception as ex:
            probs.append(f"could not operate: {str(ex).splitlines()[0][:120]}")
        p2, res = a.check(e0, r0)
        probs += p2
        shown = pg.evaluate(VISIBLE_ERROR_JS).strip()
        if expect_refusal is not None:
            if not shown or expect_refusal.lower() not in shown.lower():
                probs.append(f"expected a refusal mentioning {expect_refusal!r}, page shows {shown[:80]!r}")
            res = f"refused as intended: {shown[:70]}"
        else:
            toast = pg.evaluate("() => (document.querySelector('.toast.show') || {}).textContent || ''")
            res = res or (("toast: " + toast[:60]) if toast else "ok")
        rows.append(Row(a.config, "Scripted", control, action, res, probs))

    def sign_in(name: str) -> None:
        pg.evaluate("() => fetch('/api/logout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})")
        fresh("workflows", "#n-go")
        if pg.query_selector("#out-btn"):
            if pg.is_visible("#who-toggle"):
                pg.click("#who-toggle")
            pg.click("#out-btn")
            a.settle()
        if pg.is_visible("#who-toggle"):
            pg.click("#who-toggle")
        pg.select_option("#actor", name)
        pg.fill("#pin", a.pins[name])
        pg.click("#login button[type=submit]")
        pg.wait_for_selector("#out-btn", state="attached")
        a.settle()

    def ticket(kind: str, title: str, amount: str, cust: str = "17450", order: str = "524181") -> str:
        fresh("workflows", "#n-go")
        pg.select_option("#n-kind", kind)
        pg.fill("#n-title", title)
        pg.fill("#n-cust", cust)
        pg.fill("#n-order", order)
        pg.fill("#n-amt", amount)
        pg.fill("#n-reason", "audit flow")
        pg.click("#n-go")
        pg.wait_for_selector(".toast.show")
        m = re.search(r"#(\d+)", pg.inner_text(".toast.show"))
        a.settle()
        return m.group(1) if m else ""

    def open_ticket(tid: str) -> None:
        fresh("workflows", "#n-go")
        if pg.query_selector(".drawer"):
            pg.click(".drawer [data-close]")
        pg.click(".tabs button[data-st='']")
        a.settle()
        pg.click(f"[data-t='{tid}']")
        pg.wait_for_selector(".drawer")
        a.settle()

    def do(tid: str, what: str, comment: str = "ok") -> None:
        open_ticket(tid)
        pg.fill("#t-c", comment)
        pg.click(f"[data-do={what}]")

    def no_execute() -> None:
        if pg.query_selector("[data-do=execute]"):
            raise AssertionError("Execute is offered while the owner gate is still open")

    def no_stale() -> None:
        if pg.query_selector("#ask-out table, #ask-out svg.chart"):
            raise AssertionError("the previous result is still on screen under the refusal")

    ids: dict[str, str] = {}
    step("sign in", "Amna Khan (staff) + PIN", lambda: sign_in("Amna Khan"))
    step("wrong PIN", "Bilal Ahmed + wrong PIN", lambda: (
        pg.click("#who-toggle") if pg.is_visible("#who-toggle") else None, pg.click("#out-btn"), a.settle(),
        pg.click("#who-toggle") if pg.is_visible("#who-toggle") else None,
        pg.select_option("#actor", "Bilal Ahmed"), pg.fill("#pin", "000000"), pg.click("#login button[type=submit]")),
        expect_refusal="PIN")
    step("sign in", "Amna Khan again", lambda: sign_in("Amna Khan"))
    step("raise", "refund 250 (two managers)", lambda: ids.__setitem__("r250", ticket("refund", "Refund 250", "250")))
    step("raise", "refund 80 (one manager)", lambda: ids.__setitem__("r80", ticket("refund", "Refund 80", "80", order="567423")))
    step("raise", "refund 60 to cancel", lambda: ids.__setitem__("r60", ticket("refund", "Refund 60", "60", order="567381")))
    step("raise", "refund with no amount", lambda: (fresh("workflows", "#n-go"),
         pg.select_option("#n-kind", "refund"), pg.fill("#n-amt", ""), pg.click("#n-go")), expect_refusal="amount")
    step("four-eyes", "Amna approves her own ticket", lambda: do(ids["r250"], "approve"), expect_refusal="")
    step("cancel", "Amna cancels her own refund 60", lambda: do(ids["r60"], "cancel"))
    step("sign in", "Bilal Ahmed (manager)", lambda: sign_in("Bilal Ahmed"))
    step("approve", "Bilal approves refund 250 (gate 1)", lambda: do(ids["r250"], "approve", "photo matches"))
    step("four-eyes", "Bilal approves refund 250 again (gate 2)", lambda: do(ids["r250"], "approve"), expect_refusal="")
    step("reject", "Bilal rejects refund 80", lambda: do(ids["r80"], "reject", "no evidence"))
    step("cancel", "Bilal cancels Amna's ticket", lambda: do(ids["r250"], "cancel"), expect_refusal="")
    step("sign in", "Sana Malik (manager)", lambda: sign_in("Sana Malik"))
    step("approve", "Sana approves refund 250 (gate 2)", lambda: do(ids["r250"], "approve", "second look"))
    step("execute", "Sana executes refund 250", lambda: do(ids["r250"], "execute"))
    step("raise", "credit limit 9,000 for 17450 (needs owner)", lambda: ids.__setitem__("cl", ticket("credit_limit_change", "Raise limit", "9000", order="")))
    step("four-eyes", "Sana approves her own credit limit", lambda: do(ids["cl"], "approve"), expect_refusal="")
    step("sign in", "Bilal Ahmed (manager)", lambda: sign_in("Bilal Ahmed"))
    step("approve", "Bilal approves the credit limit (gate 1)", lambda: do(ids["cl"], "approve"))
    step("execute", "Bilal executes before the owner gate", lambda: (open_ticket(ids["cl"]), no_execute()))
    step("approve", "Bilal tries the owner gate", lambda: do(ids["cl"], "approve"), expect_refusal="")
    step("sign in", "Omar Siddiqui (owner)", lambda: sign_in("Omar Siddiqui"))
    step("approve", "Omar approves the owner gate", lambda: do(ids["cl"], "approve", "owner ok"))
    step("execute", "Omar executes the credit limit", lambda: do(ids["cl"], "execute"))
    step("verify", "audit Verify", lambda: (fresh("workflows", "#verify"), pg.click("#verify"),
         pg.wait_for_selector(".toast.show:has-text('Audit intact')")))

    def audit_csv() -> None:
        with pg.expect_download() as dl:
            pg.click("a[href='/api/audit/export']")
        p = Path(tempfile.mkdtemp()) / "audit.csv"
        dl.value.save_as(str(p))
        rows_ = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig"))))
        if "hash" not in rows_[0] or len(rows_) < 10:
            raise AssertionError(f"audit CSV has {len(rows_) - 1} rows, header {rows_[0]}")
    step("audit CSV", "Download CSV", audit_csv)
    # Data page: bad input gets a clear message
    step("sign in", "Bilal Ahmed for the Data page", lambda: sign_in("Bilal Ahmed"))

    def data(fn) -> None:
        fresh("data", "#drop")
        fn()

    def connect(kind: str, loc: str) -> None:
        pg.click("summary:has-text('Connect SQLite')")
        pg.select_option("#s-kind", kind)
        pg.fill("#s-loc", loc)
        pg.click("[data-act=connect]")
    step("connect", "SQLite path that does not exist", lambda: data(lambda: connect("sqlite", r"C:\nope\missing.sqlite3")), expect_refusal="")
    step("connect", "PostgreSQL URL that is not a URL", lambda: data(lambda: connect("postgres", "not a url")), expect_refusal="")
    step("connect", "empty path", lambda: data(lambda: connect("sqlite", "")), expect_refusal="path of a SQLite file")
    step("upload", "a .exe file", lambda: data(lambda: pg.set_input_files("#file", str(a.files["exe"]))), expect_refusal="not accepted")
    step("upload", "an empty CSV", lambda: data(lambda: pg.set_input_files("#file", str(a.files["empty"]))), expect_refusal="")
    step("paste", "Import with nothing pasted", lambda: data(lambda: (pg.fill("#p-text", ""), pg.click("#p-go"))), expect_refusal="Paste")
    step("paste", "header only", lambda: data(lambda: (pg.fill("#p-text", "a,b,c"), pg.click("#p-go"))), expect_refusal="")
    step("ask", "DELETE in the SQL box", lambda: (fresh("ask", "#q"), pg.fill("#q", "how many customers do we have"),
         pg.click("#go"), pg.wait_for_selector("#sql-box"), pg.fill("#sql-box", "DELETE FROM orders"), pg.click("#run-sql"),
        pg.wait_for_selector("#ask-refused"), no_stale()), expect_refusal="SELECT")
    step("ask", "nonsense question", lambda: (fresh("ask", "#q"), pg.fill("#q", "zzqx flibber"), pg.click("#go")), expect_refusal="")


def main(argv: list[str]) -> int:
    quick = "--quick" in argv
    work = Path(tempfile.mkdtemp(prefix="aib-audit-"))
    home = work / "data"
    home.mkdir()
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, ANALYST_HOME=str(home), PYTHONIOENCODING="utf-8")
    env.pop("ANALYST_LLM", None)
    proc = subprocess.Popen(
        [sys.executable, "-c", "from analyst_in_a_box.launcher import main; main()", "--no-browser", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rows: list[Row] = []
    found: dict[str, int] = {}
    exercised: dict[str, int] = {}
    skipped: dict[str, list[str]] = {}
    try:
        for _ in range(300):
            try:
                urllib.request.urlopen(base + "/api/state", timeout=2).read()
                break
            except Exception:
                time.sleep(1)
        else:
            raise SystemExit("server did not start")
        pins = _pins(home / "pins.txt")
        files = {"csv": work / "bakery_sales.csv", "exe": work / "tool.exe", "empty": work / "empty.csv",
                 "sqlite": home / "sample_business.sqlite3"}
        lines = ["invoice,sku,description,qty,price,date,customer,country"] + [
            f"B{2000 + n // 2},SD0{n % 6},Loaf {n % 6},{1 + n % 5},{2 + n % 7}.50,2024-04-{1 + n % 28:02d},K{n % 9:02d},Ireland" for n in range(40)]
        files["csv"].write_text("\n".join(lines) + "\n", encoding="utf-8")
        files["exe"].write_bytes(b"MZ\x90\x00not really a program")
        files["empty"].write_text("", encoding="utf-8")
        configs = CONFIGS[:1] if quick else CONFIGS
        if os.environ.get("AUDIT_CONFIGS"):  # e.g. "390 phone" to re-check one layout
            configs = [c for c in CONFIGS if c[0] in os.environ["AUDIT_CONFIGS"].split(",")]
        only = [x for x in os.environ.get("AUDIT_PAGES", "").split(",") if x]
        with sync_playwright() as p:
            browser = _launch(p)
            for cname, w, h, scheme in configs:
                ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, accept_downloads=True)
                ctx.add_init_script(INFLIGHT_INIT)
                ctx.add_init_script(f"try{{localStorage.setItem('aib-theme','{scheme}')}}catch(e){{}}")
                ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
                pg = ctx.new_page()
                a = Auditor(pg, base, pins, files, cname)
                # sidebar once per configuration (it is the same on every page), then every page's own controls
                if not only or "Sidebar" in only:
                    a.audit_page("", "Sidebar", "aside")
                # the sidebar pass flips the theme and signs out; restore both, then audit the pages as a manager
                if pg.url == "about:blank":
                    pg.goto(base + "/#/about")
                pg.evaluate(f"""async (pin) => {{ try {{ localStorage.setItem('aib-theme', '{scheme}'); }} catch (e) {{}}
                    document.documentElement.dataset.theme = '{scheme}';
                    await fetch('/api/login', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{name: 'Bilal Ahmed', pin}})}}); }}""",
                            a.pins["Bilal Ahmed"])
                pg.reload()
                for route, name, _ in PAGES:
                    if only and name not in only:
                        continue
                    a.audit_page(route, name)
                    # the Data page can switch the active source; put the sample back for the next pass
                    if route == "data":
                        pg.evaluate("""async () => { const s = await (await fetch('/api/sources')).json();
                            const m = s.find((x) => x.kind === 'sample'); if (m) await fetch(`/api/sources/${m.id}/activate`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'}); }""")
                if cname == configs[0][0] and not only:
                    scripted(a, base)
                rows += a.rows
                for k, v in a.found.items():
                    found[f"{cname} / {k}"] = v
                for k, v in a.exercised.items():
                    exercised[f"{cname} / {k}"] = v
                for k, v in a.skipped.items():
                    skipped.setdefault(k, []).extend(f"{cname}: {x}" for x in v)
                ctx.close()
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)

    # ------------------------------------------------------------------ report
    def cut(s: str, n: int) -> str:
        s = s.replace("\n", " ")
        return s if len(s) <= n else s[: n - 1] + "…"
    print(f"{'config':10} | {'page':17} | {'control':46} | {'action':30} | {'result':44} | problems")
    print("-" * 180)
    for r in rows:
        print(f"{r.config:10} | {r.page:17} | {cut(r.control, 46):46} | {cut(r.action, 30):30} | {cut(r.result, 44):44} | {'; '.join(r.problems) or '-'}")
    total_found = sum(found.values())
    total_ex = sum(exercised.values())
    print("\nDenominator (distinct controls seen on the page, including ones that appeared after an action):")
    for k in found:
        print(f"  {k:32} found {found[k]:4}  exercised {exercised.get(k, 0):4}")
    print(f"  {'all':32} found {total_found:4}  exercised {total_ex:4}  scripted steps {sum(1 for r in rows if r.page == 'Scripted')}")
    for why, items in skipped.items():
        print(f"  not exercised, {why}: {len(items)}")
    bad = [r for r in rows if r.problems]
    print(f"\nactions {len(rows)}, with problems {len(bad)}")
    for r in bad:
        print(f"  PROBLEM {r.config} / {r.page} / {r.control}: {'; '.join(r.problems)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
