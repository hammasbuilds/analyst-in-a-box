/* Analyst-in-a-Box: single-page app. No build step; talks to /api/*. */
// A page that was navigated away from can still be finishing a request; its leftover updates hit
// elements that no longer exist. Those land on a harmless sink instead of throwing.
const SINK = new Proxy(function () {}, { get: (_, k) => (k === Symbol.toPrimitive ? () => "" : SINK), set: () => true, apply: () => SINK });
const $ = (s, r = document) => r.querySelector(s) || SINK;
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = Charts.esc;
const S = { state: null, actor: null, route: "" };

const gbp = (v) => (v == null ? "-" : (v < 0 ? "-£" : "£") + Math.abs(v).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const gbp0 = (v) => (v == null ? "-" : (v < 0 ? "-£" : "£") + Math.abs(Math.round(v)).toLocaleString("en-GB"));
const num = (v, d = 0) => (v == null ? "-" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d }));
const pct = (v, d = 1) => (v == null ? "-" : (v * 100).toFixed(d) + "%");

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.body); }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) throw new Error((data && data.detail && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || r.statusText);
  return data;
}
const post = (p, body) => api(p, { method: "POST", body });
let toastT;
function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.classList.toggle("bad", !!bad); t.classList.remove("show"); void t.offsetWidth;
  t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), bad ? 6000 : 2800);
}
const busy = (el, on) => { if (el) { el.disabled = on; } };
const loading = (msg = "Working") => `<div class="empty"><span class="spin"></span> ${esc(msg)}…</div>`;
const dataHint = `<p class="small muted guide">No data of your own yet? Open <a href="#/data">Data</a> to drop a file, choose one, or paste CSV text, or press Load example there.</p>`;
const err = (e) => `<div class="note bad">${esc(e.message || e)}</div>${/Data page|needs the .*tables/.test(String(e.message || e)) ? dataHint : ""}`;
const head = (title, sub, extra = "") => `<div class="pagehead"><div><h1>${esc(title)}</h1><p>${sub}</p></div>${extra}</div>`;

const ICONS = {
  dashboard: "M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z",
  data: "M12 3C7 3 4 4.5 4 6.5v11C4 19.5 7 21 12 21s8-1.5 8-3.5v-11C20 4.5 17 3 12 3zM4 12c0 2 3 3.5 8 3.5s8-1.5 8-3.5",
  ask: "M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  forecast: "M3 17l6-6 4 4 8-9M15 6h6v6",
  alerts: "M12 3l10 18H2zM12 10v5M12 18v.5",
  risk: "M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5zM9 12l2 2 4-4",
  workflows: "M4 6h16M4 12h16M4 18h10M18 16l2 2 3-4",
  about: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01",
};
const NAV = [  // route, label, icon, short label for the phone tab grid
  ["", "Dashboard", "dashboard", "Home"], ["data", "Data", "data", "Data"], ["ask", "Ask your data", "ask", "Ask"],
  ["forecast", "Forecasts", "forecast", "Forecast"], ["alerts", "Fraud & anomalies", "alerts", "Fraud"],
  ["risk", "Customer risk", "risk", "Risk"], ["workflows", "Workflows", "workflows", "Workflows"], ["about", "About & guide", "about", "About"],
];

function renderNav(counts = {}) {
  $("#nav").innerHTML = NAV.map(([r, label, ic, short]) =>
    `<a href="#/${r}" data-r="${r}" class="${S.route === r ? "active" : ""}" aria-label="${label}"${S.route === r ? ' aria-current="page"' : ""}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICONS[ic]}"/></svg><span class="t">${label}</span><span class="ts" aria-hidden="true">${short}</span>${counts[r] ? `<span class="badge acc">${counts[r]}</span>` : ""}</a>`).join("");
}

/* ---------------------------------------------------------------- router */
const PAGES = {};
let routeSeq = 0;
async function route() {
  const r = (location.hash.replace(/^#\//, "").split("?")[0]) || "";
  S.route = PAGES[r] ? r : "";
  renderNav(S.counts);
  const seq = ++routeSeq, real = $("#app");
  // a page that finishes loading after the user has already moved on must not paint over the new one
  const app = new Proxy(real, {
    set(t, k, v) { if (seq === routeSeq) t[k] = v; return true; },
    get(t, k) { const v = t[k]; return typeof v === "function" ? v.bind(t) : v; },
  });
  app.innerHTML = loading();
  try { await PAGES[S.route](app); } catch (e) { app.innerHTML = head("Something went wrong", "") + err(e) + dataHint; }
}
window.addEventListener("hashchange", route);

async function refreshCounts() {
  try {
    const t = await api("/api/tickets?status=pending");
    S.counts = { workflows: t.tickets.length || "" };
  } catch (e) { S.counts = {}; }
  renderNav(S.counts);
}

/* ---------------------------------------------------------------- dashboard */
const DEMO = [
  "top 5 products by revenue last quarter", "monthly revenue in 2011",
  "is mahine sab se zyada bikne wali cheez", "revenue by country",
];
const SQL_KW = /('[^']*')|\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|LIMIT|JOIN|ON|AS|AND|OR|NOT|IN|IS|NULL|DESC|ASC|SUM|COUNT|AVG|MAX|MIN|ROUND|DISTINCT|CASE|WHEN|THEN|ELSE|END|CAST|INTEGER|SUBSTRING|LIKE|BETWEEN|HAVING)\b|(\b\d+(?:\.\d+)?\b)/gi;
function sqlHtml(sql) {
  let out = "", at = 0, m;
  SQL_KW.lastIndex = 0;
  while ((m = SQL_KW.exec(sql))) {
    out += esc(sql.slice(at, m.index));
    out += `<span class="${m[1] ? "s" : m[2] ? "k" : "n"}">${esc(m[0])}</span>`;
    at = m.index + m[0].length;
  }
  return out + esc(sql.slice(at));
}
const heroSkeleton = () => `<div class="strip"><span class="skel" style="width:120px;height:20px"></span><span class="skel" style="width:90px;height:20px"></span></div>
  <div class="skel" style="height:150px"></div><div class="skel" style="height:96px"></div><div class="skel" style="height:110px"></div>`;
function heroOutput(res, total) {
  if (!res.ok) {
    return `<div class="note bad">${esc(res.error || "No answer")}</div>${res.note ? `<p class="small muted">${esc(res.note)}</p>` : ""}
      ${res.sql ? `<div class="sqlview">${sqlHtml(res.sql)}</div>` : ""}
      ${res.examples && res.examples.length ? `<div class="chips">${res.examples.slice(0, 4).map((x) => `<button class="chip" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>` : ""}`;
  }
  const n = res.rows.length;
  return `<div class="strip"><span class="badge ${res.mode === "llm" ? "info" : "acc"}">${res.mode === "llm" ? "language model" : "built-in parser, no model"}</span>
      ${res.language === "roman-ur" ? '<span class="badge gold">Roman Urdu understood</span>' : ""}
      <span class="badge good" title="One SELECT, run on a read-only connection with an authoriser">read-only checked</span>
      <span class="badge timing" title="Time the database took, then the full round trip to this page">${n} row${n === 1 ? "" : "s"} &middot; SQL ${res.elapsed_ms} ms &middot; ${total} ms total</span></div>
    ${res.explanation ? `<div class="t-explain">${esc(res.explanation)}</div>` : ""}
    ${resultChart(res)}
    ${resultTable(res)}
    <details open><summary>The SQL that ran</summary><div class="sqlview">${sqlHtml(res.sql)}</div></details>`;
}
let heroSeq = 0;
async function runHero(q, preview = false) {
  if (!q.trim()) { toast("Type a question first", true); Motion.shake($("#t-q")); return; }
  const seq = ++heroSeq;
  $("#t-q").value = q;
  $("#t-go").disabled = true;
  $("#t-out").innerHTML = heroSkeleton();
  const t0 = performance.now();
  let html;
  try { html = heroOutput(await post("/api/ask", { question: q, preview }), Math.round(performance.now() - t0)); }
  catch (e) { html = err(e); }
  if (seq !== heroSeq) return;
  $("#t-out").innerHTML = html;
  $("#t-go").disabled = false;
}
const heroHtml = () => `<section class="hero page-in" aria-label="Try it"><svg class="hero-art" viewBox="0 0 380 200" aria-hidden="true"><defs><linearGradient id="ha" x1="0" x2="1"><stop offset="0" stop-color="#8b6dff"/><stop offset=".55" stop-color="#3fe0ff"/><stop offset="1" stop-color="#ffb454"/></linearGradient><linearGradient id="hb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff8a5b" stop-opacity=".35"/><stop offset="1" stop-color="#ff8a5b" stop-opacity="0"/></linearGradient></defs><path d="M0 170C40 150 60 160 90 130S140 90 175 105 235 70 270 50 330 30 380 8V200H0z" fill="url(#hb)"/><path d="M0 170C40 150 60 160 90 130S140 90 175 105 235 70 270 50 330 30 380 8" fill="none" stroke="url(#ha)" stroke-width="4" stroke-linecap="round" style="filter:drop-shadow(0 0 10px #ff8a5b)"/><circle cx="270" cy="50" r="7" fill="#fff" style="filter:drop-shadow(0 0 10px #ffb454)"/><circle cx="270" cy="50" r="16" fill="none" stroke="#ffb454" stroke-opacity=".5"/></svg>
    <span class="kicker"><i></i>Try it, live</span>
    <h1>Ask your numbers in plain English or Roman Urdu.</h1>
    <p class="sub">Type a business question. You get the SQL that answers it, the result table and a chart, produced right now by this app against its own data. Nothing can write to your data. &ldquo;Revenue&rdquo; means net revenue, completed sales minus cancellations; say &ldquo;gross revenue&rdquo; for the figure before cancellations.</p>
    <div class="hero-stats"><div><b>0</b><span>models needed</span></div><div><b>1</b><span>SELECT, read-only</span></div><div><b>2</b><span>languages</span></div></div>
    <div class="tryit">
      <div class="pane"><div class="tag"><b>1</b>Your question<span class="badge acc">no sign-in needed</span></div>
        <label class="sr" for="t-q" style="position:absolute;left:-9999px">Business question</label>
        <textarea id="t-q" rows="3" spellcheck="false" autocomplete="off">${esc(DEMO[0])}</textarea>
        <div class="go"><button class="btn" id="t-go">Run</button><span class="small muted">Enter runs it, Shift+Enter adds a line</span></div>
        <div class="chips" id="t-chips">${DEMO.map((x) => `<button class="chip" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>
        <p class="signnote">Asking is read-only, so it works without signing in. Signing in (bottom left, with your PIN) is only for changes such as tickets, approvals and uploads.</p></div>
      <div class="pane"><div class="tag"><b>2</b>The answer</div><div class="out" id="t-out">${heroSkeleton()}</div></div>
    </div></section>`;
const kpiSkeleton = () => `<div class="kpis">${'<div class="kpi"><div class="skel" style="height:12px;width:60%"></div><div class="skel" style="height:28px;width:45%;margin-top:10px"></div><div class="skel" style="height:34px;margin-top:10px"></div></div>'.repeat(5)}</div>`;

PAGES[""] = async (app) => {
  app.innerHTML = heroHtml() + `<div id="dash">${kpiSkeleton()}</div>`;
  $("#t-go").onclick = () => runHero($("#t-q").value);
  $("#t-q").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); runHero($("#t-q").value); } };
  app.onclick = (e) => { const c = e.target.closest("[data-q]"); if (c && !c.disabled) runHero(c.dataset.q); };
  runHero(DEMO[0], true);  // the page's own example, answered by the real backend on load
  const d = await api("/api/dashboard");
  const box = $("#dash");
  if (!d.canonical) { box.innerHTML = `<div class="note warn">${esc(d.message)}</div>${dataHint}`; return; }
  const fmt = { gbp: gbp0, count: (v) => (v == null ? "-" : num(v)), pct: (v) => v + "%" };
  const card = (c) => `<div class="kpi"><div class="l">${esc(c.label)}</div><div class="v">${fmt[c.unit](c.value)}</div>${c.change_pct == null ? '<div class="delta muted">&nbsp;</div>' : `<div class="delta ${c.change_pct >= 0 ? "up" : "down"}">${c.change_pct >= 0 ? "▲" : "▼"} ${Math.abs(c.change_pct)}% vs prior 30 days</div>`}${c.spark ? Charts.spark(c.spark) : ""}</div>`;
  box.innerHTML = `<p class="muted small" style="margin:0 0 10px">Business pulse as of <b>${esc(d.as_of)}</b>, the newest order in the data. ${num(d.totals.orders)} orders, ${num(d.totals.customers)} customers, ${num(d.totals.products)} products. Sparklines show the last 13 weeks.</p>`
    + `<div class="kpis">${d.cards.map(card).join("")}</div>`
    + `<div class="grid cols-2">
      <div class="card"><h2>Net revenue by month</h2>${Charts.columns({ rows: d.charts.monthly_revenue, fmt: (v) => "£" + Charts.compact(v), highlightLast: d.last_month_partial })}${d.last_month_partial ? '<p class="small muted">The newest month is partial, so its bar is faded.</p>' : ""}</div>
      <div class="card"><h2>Net revenue by country</h2>${Charts.bars({ rows: d.charts.revenue_by_country, fmt: (v) => "£" + Charts.compact(v), color: "var(--c2)" })}</div>
      <div class="card"><h2>Top products by net revenue</h2>${Charts.bars({ rows: d.charts.top_products, fmt: (v) => "£" + Charts.compact(v), color: "var(--c3)", labelW: 190 })}</div>
      <div class="card"><h2>Net revenue by category</h2>${Charts.bars({ rows: d.charts.revenue_by_category, fmt: (v) => "£" + Charts.compact(v), color: "var(--c1)", labelW: 150 })}<p class="small muted">Categories are derived from product descriptions by keyword rules. Net revenue is completed sales minus cancelled and refunded lines.</p></div>
    </div>`;
};

/* ---------------------------------------------------------------- data */
const PASTE_EXAMPLE = "invoice,sku,description,qty,price,date,customer,country\nA1001,MUG1,Blue mug,2,4.50,2024-03-01,C01,United Kingdom\nA1001,TEA2,Green tea tin,1,6.00,2024-03-01,C01,United Kingdom\nA1002,MUG1,Blue mug,5,4.50,2024-03-02,C02,Ireland";
PAGES.data = async (app) => {
  const [srcs, sch, fields] = await Promise.all([api("/api/sources"), api("/api/schema"), api("/api/sales-fields")]);
  const active = srcs.find((s) => s.active);
  const tableCard = (t) => `<details class="card" style="padding:12px 16px"><summary>${esc(t.name)} <span class="muted small">${t.rows == null ? "" : num(t.rows) + " rows · "}${t.columns.length} columns</span></summary>
    <div class="scroll" style="max-height:260px;margin-top:8px"><table class="nowrap"><thead><tr>${t.columns.map((c) => `<th>${esc(c.name)}<div class="muted" style="text-transform:none;font-weight:400">${esc(c.type)}</div></th>`).join("")}</tr></thead>
    <tbody>${t.sample.map((r) => `<tr>${r.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;
  app.innerHTML = head("Data", "Connect a database or upload sheets. Everything you ask is read-only.")
    + `<div class="grid cols-2">
      <div class="card"><h2>Sources</h2>
        <table class="sources"><colgroup><col><col style="width:9.5rem"></colgroup><tbody>${srcs.map((s) => `<tr><td><b>${esc(s.name)}</b><div class="small muted src-loc" title="${esc(s.location)}">${esc(s.kind)} · ${esc(s.location)}</div></td>
          <td class="num">${s.active ? '<span class="badge good">active</span>' : `<button class="btn ghost small" data-act="use" data-id="${s.id}">Use</button>`}
          ${["sqlite", "postgres"].includes(s.kind) ? `<button class="btn ghost small" data-act="rm" data-id="${s.id}">Remove</button>` : ""}</td></tr>`).join("")}</tbody></table>
        <details style="margin-top:12px"><summary>Connect SQLite or PostgreSQL</summary>
          <div class="stack" style="margin-top:8px">
            <label class="f">Kind<select id="s-kind"><option value="sqlite">SQLite file</option><option value="postgres">PostgreSQL URL</option></select></label>
            <label class="f">Path or URL<input type="text" id="s-loc" placeholder="C:\\data\\shop.sqlite3  or  postgresql://user:pass@host:5432/db"></label>
            <label class="f">Name (optional)<input type="text" id="s-name"></label>
            <button class="btn" data-act="connect">Connect</button>
            <p class="small muted">PostgreSQL needs the optional driver (<span class="mono">uv sync --extra postgres</span>). Connections are opened read-only. Questions and the schema browser work on both; the dashboard, forecasts, fraud screen and risk need the SQLite order tables.</p>
          </div></details></div>
      <div class="card"><h2>Add your own data</h2>
        <div class="drop" id="drop" tabindex="0" role="button" aria-label="Drop a file here or press Enter to choose one">
          <p><b>Drop a file here</b> or <label class="link" for="file">choose from your computer</label></p>
          <p class="small">Accepted: .csv, .tsv, .txt, .xlsx, .xlsm. Up to 40 MB, 1,000,000 rows and 500 columns per sheet.</p>
          <input type="file" id="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm" hidden>
          <div class="progress" id="up-prog" hidden><i></i></div></div>
        <details class="paste" id="paste-box" open><summary>Or paste CSV text</summary>
          <label class="f">Table name<input type="text" id="p-name" value="pasted_sales" maxlength="60"></label>
          <label class="f">Paste rows here (first line is the header; commas, semicolons, tabs or pipes all work)
            <textarea id="p-text" rows="6" spellcheck="false" placeholder="${esc(PASTE_EXAMPLE)}"></textarea></label>
          <div class="row"><button class="btn" id="p-go">Import pasted data</button><button class="btn ghost" id="p-ex" type="button">Load example</button>
            <span class="small muted" id="p-count"></span></div></details>
        <p class="small muted">What input looks like: one header row, then one row per record, for example <span class="mono">invoice,sku,qty,price</span>. Each sheet becomes a table in "My uploads"; column types are inferred, and it becomes the active source. Importing needs a manager sign-in.</p>
        <div id="up-msg"></div></div>
    </div>`
    + `<div class="card" style="margin-top:16px"><div class="row"><h2 style="margin:0">Schema of ${esc(sch.source)}</h2><span class="badge ${sch.canonical ? "good" : ""}">${sch.canonical ? "business tables present" : "generic tables"}</span></div>
      <div class="stack" style="margin-top:12px">${sch.tables.map(tableCard).join("") || '<div class="empty">No tables yet. Drop a file, choose one, or paste CSV text above.</div>'}</div></div>`
    + (active && active.kind === "upload" && sch.tables.length ? `<div class="card" style="margin-top:16px"><h2>Use an uploaded sheet as your sales data</h2>
      <p class="muted small">Map one sheet of order lines (one row per product on an order) and every module works on it: dashboard, forecasts, fraud, risk. It builds customers, products, orders, order_items and payments in the uploads database.</p>
      <div class="grid cols-3"><label class="f">Sheet<select id="m-table">${sch.tables.filter((t) => !["customers", "products", "orders", "order_items", "payments"].includes(t.name)).map((t) => `<option>${esc(t.name)}</option>`).join("")}</select></label>
      ${Object.entries(fields).map(([k, d]) => `<label class="f">${esc(d)}<select data-map="${k}"><option value=""></option></select></label>`).join("")}</div>
      <div class="row" style="margin-top:12px"><button class="btn" data-act="map">Build the business tables</button><span id="m-msg" class="small"></span></div></div>` : "");
  const fillMap = () => {
    const t = sch.tables.find((x) => x.name === (document.querySelector("#m-table") || {}).value); if (!t) return;
    const guess = { invoice: /invoice|order/i, stock_code: /stock|sku|code|product/i, description: /desc|name/i, quantity: /qty|quantity/i, date: /date/i, price: /price|unit/i, customer: /customer|client/i, country: /country/i };
    $$("[data-map]").forEach((sel) => {
      sel.innerHTML = '<option value=""></option>' + t.columns.map((c) => `<option>${esc(c.name)}</option>`).join("");
      const g = t.columns.find((c) => guess[sel.dataset.map].test(c.name)); if (g) sel.value = g.name;
    });
  };
  if (document.querySelector("#m-table")) { fillMap(); $("#m-table").onchange = fillMap; }
  const done = (r) => { toast(`Imported ${r.tables.map((t) => t.table + " (" + t.rows + " rows)").join(", ")}`); route(); };
  const MAX = 40 * 1024 * 1024, OK = /\.(csv|tsv|txt|xlsx|xlsm)$/i;
  const up = (file) => {
    if (!file) return;
    if (!OK.test(file.name)) { $("#up-msg").innerHTML = err("That file type is not accepted. Use .csv, .tsv, .txt, .xlsx or .xlsm."); return; }
    if (file.size > MAX) { $("#up-msg").innerHTML = err(`That file is ${(file.size / 1048576).toFixed(1)} MB; the limit is 40 MB. Split it and upload the parts.`); return; }
    const fd = new FormData(); fd.append("file", file);
    const prog = $("#up-prog"), bar = $("#up-prog i"); prog.hidden = false; bar.style.width = "0%";
    $("#up-msg").innerHTML = loading(`Uploading ${file.name}`);
    const x = new XMLHttpRequest(); x.open("POST", "/api/upload"); x.responseType = "json";
    x.upload.onprogress = (e) => { if (e.lengthComputable) bar.style.width = Math.round((e.loaded / e.total) * 100) + "%"; };
    x.onload = () => {
      prog.hidden = true; const d = x.response;
      if (x.status >= 200 && x.status < 300) done(d);
      else $("#up-msg").innerHTML = err((d && typeof d.detail === "string" && d.detail) || x.statusText || "upload failed");
    };
    x.onerror = () => { prog.hidden = true; $("#up-msg").innerHTML = err("the upload did not reach the app"); };
    x.send(fd);
  };
  $("#file").onchange = (e) => up(e.target.files[0]);
  const drop = $("#drop");
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#file").click(); } };
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => up(e.dataTransfer.files[0]));
  const pcount = () => { const t = $("#p-text").value; const n = t.split("\n").filter((l) => l.trim()).length; $("#p-count").textContent = n ? `${Math.max(0, n - 1)} data rows, ${(new Blob([t]).size / 1024).toFixed(1)} KB` : ""; };
  $("#p-text").oninput = pcount;
  $("#p-ex").onclick = () => { $("#p-text").value = PASTE_EXAMPLE; pcount(); };
  $("#p-go").onclick = async () => {
    if (!$("#p-text").value.trim()) { Motion.shake($("#p-text")); $("#up-msg").innerHTML = err("Paste a header row and at least one data row first, or press Load example."); return; }
    $("#up-msg").innerHTML = loading("Importing");
    try { done(await post("/api/upload-text", { name: $("#p-name").value, text: $("#p-text").value })); } catch (e) { $("#up-msg").innerHTML = err(e); }
  };
  app.onclick = async (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    try {
      if (b.dataset.act === "use") { await post(`/api/sources/${b.dataset.id}/activate`, {}); await boot(); route(); }
      if (b.dataset.act === "rm") { await api(`/api/sources/${b.dataset.id}`, { method: "DELETE" }); await boot(); route(); }
      if (b.dataset.act === "connect") { await post("/api/sources", { kind: $("#s-kind").value, location: $("#s-loc").value, name: $("#s-name").value || null }); toast("Connected"); await boot(); route(); }
      if (b.dataset.act === "map") {
        const mapping = {}; $$("[data-map]").forEach((s) => { if (s.value) mapping[s.dataset.map] = s.value; });
        $("#m-msg").innerHTML = '<span class="spin"></span>';
        const r = await post(`/api/sources/${active.id}/map-sales`, { table: $("#m-table").value, mapping });
        toast(`Built ${r.orders} orders for ${r.customers} customers (${r.rows_skipped} rows skipped)`); await boot(); route();
      }
    } catch (x) { toast(x.message, true); $("#m-msg").textContent = ""; }
  };
};

/* ---------------------------------------------------------------- ask */
function resultChart(res) {
  const c = res.chart; if (!c) return "";
  const ci = (n) => res.columns.indexOf(n);
  if (c.type === "stat") return `<div class="kpis">${c.y.map((n) => `<div class="kpi"><div class="l">${esc(n)}</div><div class="v">${fmtCell(n, res.rows[0][ci(n)])}</div></div>`).join("")}</div>`;
  const xi = ci(c.x);
  const money = /revenue|refund|value|spend|total|amount|price|sales/i.test(c.y[0]);
  const f = money ? (v) => "£" + Charts.compact(v) : Charts.compact;
  if (c.type === "line") return Charts.line({ labels: res.rows.map((r) => r[xi]), series: c.y.map((n) => ({ name: n, values: res.rows.map((r) => r[ci(n)]) })), fmt: f });
  // categories read best as horizontal bars with the full label; past BAR_CAP only the largest are drawn
  const all = res.rows.map((r) => [r[xi], r[ci(c.y[0])]]).filter((r) => typeof r[1] === "number");
  if (all.length <= BAR_CAP) return Charts.bars({ rows: all, fmt: f, labelW: 180 });
  const top = [...all].sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, BAR_CAP);
  return Charts.bars({ rows: top, fmt: f, labelW: 180 })
    + `<p class="small muted chart-cap">Chart shows the ${BAR_CAP} largest by ${esc(c.y[0])}; ${all.length - BAR_CAP} more in the table.</p>`;
}
const BAR_CAP = 12;
function fmtCell(name, v) {
  if (v == null) return "";
  if (typeof v === "number") return /revenue|refund|value|spend|total|amount|price|sales/i.test(name) ? gbp(v) : num(v, 2);
  return esc(v);
}
function resultTable(res) {
  const rows = res.rows.slice(0, 300);
  return `<div class="scroll"><table><thead><tr>${res.columns.map((c) => `<th class="${typeof (res.rows[0] || [])[res.columns.indexOf(c)] === "number" ? "num" : ""}">${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((v, i) => `<td class="${typeof v === "number" ? "num" : ""}">${fmtCell(res.columns[i], v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
  <p class="small muted">${res.rows.length} row${res.rows.length === 1 ? "" : "s"}${res.rows.length > 300 ? " (first 300 shown)" : ""}${res.truncated ? ` - capped at ${S.state.max_rows} rows` : ""}</p>`;
}
// A refused or failed run replaces the whole result: the previous chart and table must not stay
// under the refusal, or it reads as if the refused SQL returned them. The SQL stays editable.
function showRefusal(message, sql, extra = "") {
  const out = $("#ask-out");
  out.innerHTML = `<div class="card stack" id="ask-refused"><div class="row"><span class="badge bad">not run</span><span class="small muted">No rows: nothing from an earlier question is shown.</span></div>${err({ message })}${extra}
    ${sql != null ? `<label class="f">The SQL you ran<textarea id="sql-box" rows="${Math.min(14, String(sql).split("\n").length + 1)}" spellcheck="false">${esc(sql)}</textarea></label>
    <div class="row"><button class="btn ghost small" id="run-sql">Run edited SQL</button><button class="btn ghost small" id="copy-sql">Copy</button><span class="small muted">Only one SELECT is ever run.</span></div>` : ""}</div>`;
  wireSql(out);
}
function wireSql(out) {
  if (!document.querySelector("#sql-box")) return;
  $("#run-sql").onclick = async () => {
    const sql = $("#sql-box").value;
    out.innerHTML = loading("Running your SQL");
    try { showResult(await post("/api/sql", { sql })); } catch (e) { showRefusal(e.message || String(e), sql); }
    loadHistory();
  };
  $("#copy-sql").onclick = async () => {
    try { await navigator.clipboard.writeText($("#sql-box").value); toast("Copied"); }
    catch (e) { toast("Copy is blocked here; select the SQL and copy it by hand", true); }
  };
  if (document.querySelector("#csv-sql")) $("#csv-sql").onclick = async () => {
    try {
      const r = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sql: $("#sql-box").value }) });
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || r.statusText); }
      const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = "analyst-export.csv"; a.click(); URL.revokeObjectURL(a.href);
      toast(`${r.headers.get("X-Rows")} rows exported${r.headers.get("X-Truncated") === "true" ? " (cut at the cap)" : ""}`);
    } catch (x) { toast(x.message, true); }
  };
}
function showResult(res) {
  const out = $("#ask-out");
  if (!res.ok) {
    const extra = `${res.note ? `<p class="small muted">${esc(res.note)}</p>` : ""}${res.examples && res.examples.length ? `<p class="small muted">Try:</p><div class="chips">${res.examples.map((x) => `<button class="chip" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>` : ""}`;
    showRefusal(res.error, res.sql || null, extra);
    return;
  }
  out.innerHTML = `<div class="card stack">
    <div class="row"><span class="badge ${res.mode === "llm" ? "info" : "acc"}">${res.mode === "llm" ? "written by a language model" : res.mode === "edited" ? "your SQL" : "built-in parser (no model)"}</span>
      ${res.language === "roman-ur" ? '<span class="badge warn">Roman Urdu understood</span>' : ""}
      <span class="badge good" title="Parsed to one SELECT, then run on a read-only connection with an authoriser">read-only checked</span>
      <span class="small muted">${esc(res.explanation || "")}</span></div>
    ${res.note ? `<div class="note warn">${esc(res.note)}</div>` : ""}
    ${resultChart(res)}
    ${resultTable(res)}
    <details open><summary>The SQL that ran</summary><textarea id="sql-box" rows="${Math.min(14, res.sql.split("\n").length + 1)}" spellcheck="false">${esc(res.sql)}</textarea>
    <div class="row" style="margin-top:8px"><button class="btn ghost small" id="run-sql">Run edited SQL</button><button class="btn ghost small" id="copy-sql">Copy</button><button class="btn ghost small" id="csv-sql" title="Up to 50,000 rows; text starting with = + - @ is made safe for spreadsheets">Download CSV</button><span class="small muted">Edits are checked the same way: anything but one SELECT is refused.</span></div></details></div>`;
  wireSql(out);
}
async function loadHistory() {
  const h = await api("/api/ask/history");
  $("#hist").innerHTML = h.length ? h.slice(0, 8).map((x) => `<div class="row small" style="padding:3px 0"><span class="badge ${x.ok ? "" : "bad"}">${x.ok ? x.rows + " rows" : "no answer"}</span><button class="chip" data-q="${esc(x.question || "")}" ${x.question ? "" : "disabled"}>${esc(x.question || "(edited SQL)")}</button></div>`).join("") : '<span class="muted small">Nothing yet.</span>';
}
PAGES.ask = async (app) => {
  const ex = await api("/api/ask/examples");
  const hasLLM = S.state.llm.configured;
  app.innerHTML = head("Ask your data", "Ask in English or Roman Urdu. You always see the SQL, and nothing can write to your data.")
    + `<div class="card stack"><div class="row" style="flex-wrap:nowrap"><input type="text" id="q" placeholder="e.g. Top 10 products by revenue in 2011   |   har mahine ki bikri" autocomplete="off"><button class="btn" id="go">Ask</button></div>
    <div class="chips">${ex.map((x) => `<button class="chip" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>
    <p class="small muted guide">What input looks like: one plain question, in English or Roman Urdu, for example <i>Top 10 products by revenue in 2011</i>. Pasting a long question or an SQL query here works too, and the chips below load an example. No data of your own yet? Add some on <a href="#/data">Data</a>.</p>
    <p class="small muted">${hasLLM ? `Language model: <b>${esc(S.state.llm.provider)}:${esc(S.state.llm.model)}</b>. Its SQL goes through the same checks, and the built-in parser answers if it fails.` : "No language model configured: questions are answered by the built-in parser, which knows revenue (net of cancellations; say &ldquo;gross revenue&rdquo; for the figure before them), orders, customers, units, refunds, averages, rankings, time breakdowns, countries and categories. Set ANALYST_LLM to add one."}</p></div>
    <div id="ask-out" style="margin-top:16px"></div>
    <div class="card" style="margin-top:16px"><h2>Recent questions</h2><div id="hist"></div></div>`;
  const go = async (q) => {
    if (!q.trim()) { Motion.shake($("#q")); return; } $("#q").value = q; $("#ask-out").innerHTML = loading("Asking");
    try { showResult(await post("/api/ask", { question: q })); } catch (e) { showRefusal(e.message || String(e), null); }
    loadHistory();
  };
  $("#go").onclick = () => go($("#q").value);
  $("#q").onkeydown = (e) => { if (e.key === "Enter") go($("#q").value); };
  app.onclick = (e) => { const c = e.target.closest("[data-q]"); if (c && !c.disabled) go(c.dataset.q); };
  loadHistory();
};

/* ---------------------------------------------------------------- forecast */
const METHOD_LABEL = { base: "Own forecasts", bottom_up: "Bottom-up", top_down: "Top-down", mint_ols: "MinT (OLS)", mint_wls: "MinT (weighted)", weighted_blend: "Blend" };
const FS = { horizon: 8, per_category: 3, method: "mint_wls", sel: "All products" };
PAGES.forecast = async (app) => {
  app.innerHTML = head("Forecasts", "Weekly units per product and category, forecast so the numbers add up, with a backtest that says where to trust it.")
    + `<div class="card"><div class="row">
      <label class="f">Weeks ahead<select id="f-h">${[4, 8, 13, 26].map((v) => `<option ${v === FS.horizon ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="f">Products per category<select id="f-k">${[1, 2, 3, 4, 5].map((v) => `<option ${v === FS.per_category ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="f">Reconciliation<select id="f-m">${[["mint_wls", "MinT (weighted)"], ["mint_ols", "MinT (OLS)"], ["bottom_up", "Bottom-up"], ["top_down", "Top-down"], ["weighted_blend", "Blend"]].map(([v, l]) => `<option value="${v}" ${v === FS.method ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <button class="btn" id="f-go" style="align-self:end">Run forecast</button></div></div><div id="f-out" style="margin-top:16px"></div>`;
  const run = async () => {
    FS.horizon = +$("#f-h").value; FS.per_category = +$("#f-k").value; FS.method = $("#f-m").value;
    $("#f-out").innerHTML = loading("Forecasting and backtesting every series");
    try { drawForecast(await api(`/api/forecast?horizon=${FS.horizon}&per_category=${FS.per_category}&method=${FS.method}`)); } catch (e) { $("#f-out").innerHTML = err(e); }
  };
  $("#f-go").onclick = run; await run();
};
function drawForecast(r) {
  const bt = r.backtest, best = [...Object.keys(bt.by_level[0]).filter((k) => !["level", "nodes", "seasonal_naive"].includes(k))];
  const total = r.nodes.find((n) => n.level === "total");
  const badge = (n) => n.backtest.beats_seasonal_naive == null ? '<span class="badge">n/a</span>' : n.backtest.beats_seasonal_naive ? `<span class="badge good" title="Backtest MAE is ${n.backtest.relative_mae}x seasonal naive's">beats naive ${n.backtest.relative_mae}x</span>` : `<span class="badge warn" title="Backtest MAE is ${n.backtest.relative_mae}x seasonal naive's">naive wins ${n.backtest.relative_mae}x</span>`;
  const sum = (a) => a.reduce((x, y) => x + y, 0);
  const moved = (own, fin) => Math.abs(fin - own) >= 50 && Math.abs(fin - own) >= 0.5 * Math.max(own, 1);  // flag what reconciliation changed a lot
  const order = []; const walk = (name, depth) => { const n = r.nodes.find((x) => x.name === name); order.push([n, depth]); r.nodes.filter((c) => c.parent === name).forEach((c) => walk(c.name, depth + 1)); };
  walk(total.name, 0);
  $("#f-out").innerHTML = `<div class="kpis">
      <div class="kpi"><div class="l">Totals add up</div><div class="v">${r.coherent ? "Yes" : "No"}</div><div class="delta muted">before reconciling: ${r.coherent_before_reconciliation ? "yes" : "no"}</div></div>
      <div class="kpi"><div class="l">Series beating seasonal naive</div><div class="v">${bt.nodes_beating_seasonal_naive} of ${bt.nodes_judged}</div><div class="delta muted">${bt.folds} backtest origins, ${bt.horizon} weeks each</div></div>
      <div class="kpi"><div class="l">Forecast, all products</div><div class="v">${num(sum(total.forecast))}</div><div class="delta muted">units over ${r.horizon} weeks</div></div>
      <div class="kpi"><div class="l">Best method at the total</div><div class="v" style="font-size:1.2rem">${esc(METHOD_LABEL[bt.best_method_at_total] || bt.best_method_at_total)}</div><div class="delta muted">lowest backtest error there</div></div></div>
    ${r.seasonal_note ? `<div class="note warn" style="margin-bottom:16px">${esc(r.seasonal_note)}</div>` : ""}
    <div class="card"><h2 id="f-title"></h2><div id="f-chart"></div><div id="f-facts" class="small muted"></div></div>
    <div class="card" style="margin-top:16px"><h2>Backtest: mean absolute error by level</h2>
        <div class="scroll" style="max-height:none"><table class="fit"><thead><tr><th>Level</th><th class="num">Seasonal naive</th>${best.map((k) => `<th class="num">${esc(METHOD_LABEL[k] || k)}</th>`).join("")}</tr></thead>
        <tbody>${bt.by_level.map((l) => { const lo = Math.min(...best.map((k) => l[k])); return `<tr><td>${esc(l.level)} <span class="muted small">(${l.nodes})</span></td><td class="num">${num(l.seasonal_naive, 1)}</td>${best.map((k) => `<td class="num" style="${l[k] === lo ? "font-weight:700;color:var(--accent)" : ""}">${num(l[k], 1)}</td>`).join("")}</tr>`; }).join("")}</tbody></table></div>
        <p class="small muted">Units per week, averaged over the same ${bt.folds} rolling origins (${esc(bt.origins[0])} to ${esc(bt.origins[bt.origins.length - 1])}); lower is better and the best method per level is bold. "Own forecasts" are each series' forecast before reconciling. Models see only the history before each origin. Demand pattern per product: ${Object.entries(r.patterns).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(", ")}.</p></div>
    <div class="card" style="margin-top:16px"><h2>Every series</h2><div class="scroll"><table><thead><tr><th>Series</th><th>Pattern</th><th>Model</th><th class="num">Last 4 weeks</th><th class="num">Same ${r.horizon} weeks last year</th><th class="num">Own forecast</th><th class="num">Next ${r.horizon} weeks</th><th>Backtest vs seasonal naive</th></tr></thead>
      <tbody>${order.map(([n, d]) => { const own = sum(n.base_forecast), fin = sum(n.forecast), mv = moved(own, fin); return `<tr class="click" data-n="${esc(n.name)}"><td style="padding-left:${10 + d * 18}px">${d === 0 ? "<b>" : ""}${esc(n.name.split("/").pop())}${d === 0 ? "</b>" : ""}</td><td><span class="badge">${esc(n.pattern)}</span></td><td class="small">${esc(n.model)}</td><td class="num">${num(sum(n.history.slice(-4)))}</td><td class="num">${n.last_year ? num(sum(n.last_year)) : "n/a"}</td><td class="num">${num(own)}</td><td class="num">${num(fin)}${mv ? ` <span class="badge warn" title="Reconciliation (${esc(METHOD_LABEL[r.method] || r.method)}) changed this series' own forecast of ${num(own)} to ${num(fin)}">${fin > own ? "+" : "−"}${num(Math.abs(fin - own))} reconciled</span>` : ""}</td><td>${badge(n)}</td></tr>`; }).join("")}</tbody></table></div>
      <p class="small muted">Click a row to chart it. "Other" rows hold every product of the category that is not listed, so each category and the total are real totals. "Own forecast" is the series' model on its own history; "Next ${r.horizon} weeks" is after reconciliation, which makes every level add up. ${r.method.startsWith("mint") ? "MinT spreads the gap between a category's (or the total's) own forecast and the sum of its products' forecasts over the products in roughly equal units, not in proportion to their size, so a small or dormant product can be moved by hundreds of units (badge). Choose Bottom-up to keep every product's own forecast." : ""} ETS is exponential smoothing${r.seasonal ? " with a 52-week season" : ""}; TSB handles products that sell in occasional bursts and decays toward zero while a product does not sell. Negative forecasts are set to zero before adding up.</p></div>`;
  const pick = (name) => {
    FS.sel = name; const n = r.nodes.find((x) => x.name === name) || total;
    $("#f-title").textContent = n.name;
    const hist = n.history, labels = [...r.weeks.slice(-hist.length), ...r.future_weeks];
    // this card spans the page, so the chart is drawn as wide as its box (readable text at any width)
    const fw = document.querySelector("#f-chart").clientWidth || 520;
    $("#f-chart").innerHTML = Charts.line({ labels, split: hist.length, width: Math.min(1100, Math.max(520, fw)), height: fw > 700 ? 280 : 230, series: [
      { name: "actual units", values: [...hist, ...r.future_weeks.map(() => null)] },
      { name: "forecast", dashed: true, color: "var(--c2)", values: [...hist.map((_, i) => (i === hist.length - 1 ? hist[i] : null)), ...n.forecast] },
      { name: "own forecast, before reconciling", dashed: true, color: "var(--c3)", values: [...hist.map(() => null), ...n.base_forecast] },
      ...(n.last_year ? [{ name: "same weeks last year", dashed: true, color: "var(--c4)", values: [...hist.map(() => null), ...n.last_year] }] : [])] });
    const b = n.backtest;
    $("#f-facts").innerHTML = `Model <b>${esc(n.model)}</b> (${esc(n.pattern)}${n.adi ? `, ADI ${n.adi}, CV² ${n.cv2}` : ""}). Backtest MAE ${num(b.mae, 1)} against seasonal naive ${num(b.seasonal_naive_mae, 1)}; MASE ${b.mase ?? "n/a"}; sMAPE ${b.smape}.`;
    $$("#f-out tr.click").forEach((tr) => tr.style.background = tr.dataset.n === name ? "var(--accent-soft)" : "");
  };
  $("#f-out").onclick = (e) => { const tr = e.target.closest("tr[data-n]"); if (tr) { pick(tr.dataset.n); $("#f-title").scrollIntoView({ block: "nearest", behavior: "smooth" }); } };
  pick(r.nodes.some((n) => n.name === FS.sel) ? FS.sel : total.name);
}

/* ---------------------------------------------------------------- alerts */
const AS = { status: "open", severity: "", kind: "" };
PAGES.alerts = async (app) => {
  app.innerHTML = head("Fraud & anomalies", "Odd orders, refunds and trading days, each with the reasons it was flagged.")
    + `<div id="a-sum"></div><div class="card"><div class="row"><label class="f">Status<select id="a-st">${["open", "ticketed", "dismissed", "confirmed", ""].map((v) => `<option value="${v}" ${v === AS.status ? "selected" : ""}>${v || "any"}</option>`).join("")}</select></label>
    <label class="f">Severity<select id="a-sv">${["", "high", "medium", "low"].map((v) => `<option value="${v}" ${v === AS.severity ? "selected" : ""}>${v || "any"}</option>`).join("")}</select></label>
    <label class="f">Kind<select id="a-k">${[["", "any"], ["order", "orders"], ["refund", "refunds"], ["day", "trading days"]].map(([v, l]) => `<option value="${v}" ${v === AS.kind ? "selected" : ""}>${l}</option>`).join("")}</select></label></div></div>
    <div id="a-list" style="margin-top:16px"></div>`;
  const load = async () => {
    AS.status = $("#a-st").value; AS.severity = $("#a-sv").value; AS.kind = $("#a-k").value;
    $("#a-list").innerHTML = loading("Screening");
    try {
      const r = await api(`/api/alerts?status=${AS.status}&severity=${AS.severity}&kind=${AS.kind}&limit=60`);
      const s = r.summary;
      $("#a-sum").innerHTML = `<div class="kpis"><div class="kpi"><div class="l">Orders screened</div><div class="v">${num(s.orders_screened)}</div></div>
        <div class="kpi"><div class="l">Alerts raised</div><div class="v">${num(s.alerts)}</div><div class="delta muted">${s.by_severity.high} high · ${s.by_severity.medium} medium · ${s.by_severity.low} low</div></div>
        <div class="kpi"><div class="l">By kind</div><div class="v" style="font-size:1.1rem">${s.by_kind.order} orders · ${s.by_kind.refund} refunds · ${s.by_kind.day} days</div></div></div>
        <div class="note" style="margin-bottom:16px">${esc(s.note)} Robust z-scores use the median and MAD, so one huge order does not hide the next.</div>`;
      $("#a-list").innerHTML = `<p class="muted small">${r.matching} matching${r.matching > r.alerts.length ? `, showing the top ${r.alerts.length}` : ""}.</p>` + (r.alerts.map((a) => `<div class="alert ${a.severity}">
        <div class="row"><b>${esc(a.subject)}</b><span class="badge ${a.severity}">${a.severity}</span><span class="badge">score ${a.score}</span>
          <span class="muted small">${esc(a.date)}${a.customer_id ? " · customer " + esc(a.customer_id) : ""} · ${gbp(a.amount)}</span>
          ${a.status !== "open" ? `<span class="badge acc">${esc(a.status)}${a.ticket_id ? " #" + a.ticket_id : ""}</span>` : ""}
          <span class="right row">${a.status === "open" && a.kind !== "day" ? `<button class="btn small" data-ticket="${esc(a.key)}">Open review ticket</button>` : ""}${a.status === "open" ? `<button class="btn ghost small" data-dismiss="${esc(a.key)}">Dismiss</button>` : ""}</span></div>
        <ul>${a.reasons.map((x) => `<li>${esc(x.text)} <span class="muted small">(${esc(x.code)}, +${x.weight})</span></li>`).join("")}</ul></div>`).join("") || '<div class="empty">Nothing matches.</div>');
    } catch (e) { $("#a-list").innerHTML = err(e); }
  };
  ["a-st", "a-sv", "a-k"].forEach((id) => ($("#" + id).onchange = load));
  app.onclick = async (e) => {
    const t = e.target.closest("[data-ticket]"), d = e.target.closest("[data-dismiss]");
    try {
      if (t) { const r = await post(`/api/alerts/${t.dataset.ticket}/ticket`, { }); toast(`Ticket #${r.id} opened, waiting for a ${r.next_gate}`); refreshCounts(); load(); }
      if (d) { await post(`/api/alerts/${d.dataset.dismiss}/dismiss`, { note: "dismissed from the alert list" }); load(); }
    } catch (x) { toast(x.message, true); }
  };
  load();
};

/* ---------------------------------------------------------------- risk */
const RS = { approve_rate: 0.8 };
PAGES.risk = async (app) => {
  const r = await api(`/api/risk?approve_rate=${RS.approve_rate}`);
  const m = r.metrics, fairBlock = (label, f) => {
    if (!f || !f.groups) return `<p class="muted small">${esc((f && f.note) || "")}</p>`;
    const cmp = f.comparisons ? Object.entries(f.comparisons)[0] : null;
    return `<h3>By ${esc(label.replace("_", " "))}</h3><div class="scroll" style="max-height:none"><table><thead><tr><th>Group</th><th class="num">Customers</th><th class="num">Approved</th><th class="num">Observed bad rate</th><th class="num">Good customers approved</th></tr></thead><tbody>
      ${Object.values(f.groups).map((g) => `<tr><td>${esc(g.group)}${g.group === f.reference_group ? ' <span class="badge">reference</span>' : ""}</td><td class="num">${g.n}</td><td class="num">${pct(g.approval_rate)}</td><td class="num">${pct(g.bad_rate)}</td><td class="num">${pct(g.true_positive_rate)}</td></tr>`).join("")}</tbody></table></div>
      ${cmp ? `<p class="small">Disparate impact (${esc(cmp[0])} vs ${esc(f.reference_group)}): <b>${cmp[1].disparate_impact}</b> ${f.four_fifths_rule_flag ? '<span class="badge bad">below four-fifths</span>' : '<span class="badge good">above four-fifths</span>'} · equalised-odds gap ${cmp[1].equalised_odds_gap}</p>` : ""}`;
  };
  app.innerHTML = head("Customer risk", `A points scorecard from customer behaviour. Outcome: <b>${esc(r.outcome)}</b>.`)
    + `<div class="note warn">${r.notes.map(esc).join(" ")}</div>
    <div class="kpis" style="margin-top:16px"><div class="kpi"><div class="l">Gini, validation</div><div class="v">${m.validation.gini == null ? "-" : m.validation.gini.toFixed(3)}</div><div class="delta muted">train ${m.train.gini == null ? "-" : m.train.gini.toFixed(3)} · test ${m.test.gini == null ? "n/a" : m.test.gini.toFixed(3)} (${m.test.customers} customers, ${m.test.bad} bad)</div></div>
      <div class="kpi"><div class="l">Bad rate</div><div class="v">${pct(r.base_bad_rate)}</div><div class="delta muted">${r.snapshot_bad} of ${r.snapshot_customers} customers</div></div>
      <div class="kpi"><div class="l">Score cut-off</div><div class="v">${r.score_cutoff}</div><div class="delta muted">approves ${pct(r.approve_rate, 0)} of the training customers</div></div>
      <div class="kpi"><div class="l">Scored now</div><div class="v">${r.live.customers}</div><div class="delta muted">${r.live.approved} approve · median ${r.live.median_score}</div></div></div>
    <div class="grid cols-2">
      <div class="card"><h2>What moves the score</h2><div class="scroll" style="max-height:none"><table><thead><tr><th>Feature</th><th class="num">Information value</th><th>Strength</th></tr></thead><tbody>${r.features.map((f) => `<tr><td>${esc(f.label)}</td><td class="num">${f.iv}</td><td><span class="badge">${esc(f.strength)}</span>${f.monotonic ? "" : ' <span class="badge warn">not monotonic</span>'}</td></tr>`).join("")}</tbody></table></div>
        <details><summary>Points table (the card)</summary><div class="scroll"><table><thead><tr><th>Feature</th><th>Band</th><th class="num">Customers</th><th class="num">Bad rate</th><th class="num">Points</th></tr></thead><tbody>${r.points_table.filter((p) => p.n > 0).map((p) => `<tr><td>${esc(p.feature)}</td><td class="mono small">${esc(p.bin)}</td><td class="num">${p.n}</td><td class="num">${pct(p.bad_rate)}</td><td class="num"><b>${p.points}</b></td></tr>`).join("")}</tbody></table></div></details></div>
      <div class="card"><h2>Fairness panel</h2>${fairBlock("country_group", r.fairness.country_group)}${fairBlock("tenure_band", r.fairness.tenure_band)}
        <p class="small muted">Country is not a model feature; it is audited. Four-fifths is a screening threshold that calls for a closer look, not a verdict. Demographic parity and equalised odds cannot both hold when base rates differ.</p></div></div>
    <div class="card" style="margin-top:16px"><div class="row"><h2 style="margin:0">Customers</h2>
      <label class="f right" style="grid-auto-flow:column;align-items:center">Approve rate<select id="r-ar">${[0.7, 0.8, 0.9].map((v) => `<option value="${v}" ${v === RS.approve_rate ? "selected" : ""}>${v * 100}%</option>`).join("")}</select></label>
      <select id="r-dec" style="width:auto"><option value="">all decisions</option><option>DECLINE</option><option>APPROVE</option></select></div>
      <div id="r-tab" style="margin-top:10px"></div></div><div id="r-drawer"></div>`;
  const loadTab = async () => {
    const rows = await api(`/api/risk/customers?approve_rate=${RS.approve_rate}&order=score&limit=80&decision=${$("#r-dec").value}`);
    $("#r-tab").innerHTML = `<div class="scroll"><table><thead><tr><th>Customer</th><th>Country</th><th class="num">Score</th><th class="num">Risk</th><th>Decision</th><th class="num">Spend</th><th>Main reason</th></tr></thead><tbody>${rows.map((c) => `<tr class="click" data-c="${esc(c.customer_id)}"><td>${esc(c.customer_id)}</td><td>${esc(c.country)}</td><td class="num"><b>${c.score}</b></td><td class="num">${pct(c.pd)}</td><td><span class="badge ${c.decision === "APPROVE" ? "good" : "bad"}">${c.decision}</span></td><td class="num">${gbp0(c.spend)}</td><td class="small">${esc(c.top_reason || "")}</td></tr>`).join("")}</tbody></table></div><p class="small muted">Lowest scores first. Click a customer for the points breakdown and to raise a credit-limit ticket.</p>`;
  };
  $("#r-dec").onchange = loadTab;
  $("#r-ar").onchange = (e) => { RS.approve_rate = +e.target.value; route(); };
  app.onclick = async (e) => {
    const tr = e.target.closest("tr[data-c]"); if (!tr) return;
    const c = await api(`/api/risk/customer/${tr.dataset.c}?approve_rate=${RS.approve_rate}`);
    const best = Math.max(...c.points.map((p) => p.points), 1);
    $("#r-drawer").innerHTML = `<div class="scrim" data-close></div><div class="drawer stack"><div class="row"><h2 style="margin:0">Customer ${esc(c.customer_id)}</h2><button class="btn ghost small right" data-close>Close</button></div>
      <div class="row"><span class="badge ${c.decision === "APPROVE" ? "good" : "bad"}">${c.decision}</span><span class="badge">score ${c.score} (cut-off ${c.score_cutoff})</span><span class="badge">risk ${pct(c.pd)}</span><span class="muted small">${esc(c.country)} · ${c.split} split</span></div>
      <div><h3>Points by feature</h3>${c.points.map((p) => `<div class="row small" style="flex-wrap:nowrap;margin:4px 0"><span style="width:150px">${esc(p.feature)}</span><div class="bar" style="flex:1"><i style="width:${(p.points / best) * 100}%"></i></div><span class="num" style="width:36px">${p.points}</span><span class="muted mono" style="width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(p.bin)}">${esc(p.bin)}</span></div>`).join("")}</div>
      <div><h3>Reason codes</h3>${c.reasons.length ? `<ol>${c.reasons.map((x) => `<li>${esc(x.text)}</li>`).join("")}</ol>` : '<p class="muted small">No feature lost points against its best band.</p>'}${c.warnings.length ? `<div class="note warn">${c.warnings.map(esc).join("<br>")}</div>` : ""}</div>
      <div><h3>Current credit limit</h3><p>${c.credit_limit ? gbp0(c.credit_limit.limit_gbp) + ' <span class="muted small">set ' + esc(c.credit_limit.set_at) + "</span>" : '<span class="muted">none set</span>'}</p></div>
      <div class="card flat"><h3>Change the credit limit</h3><div class="row"><label class="f">New limit (£)<input type="number" id="cl-new" min="0" step="50" value="${c.credit_limit ? c.credit_limit.limit_gbp : 1000}"></label><button class="btn" id="cl-go" style="align-self:end">Raise ticket</button></div><p class="small muted">Goes to the approval queue as ${esc(S.actor || "you once you sign in")}. An increase for a declined customer needs the owner too.</p></div></div>`;
    $("#cl-go").onclick = async () => {
      try { const t = await post("/api/tickets", { kind: "credit_limit_change", title: `Credit limit for customer ${c.customer_id}`, customer_id: c.customer_id, reason: `Scorecard ${c.score} (${c.decision}); ` + (c.reasons[0] ? c.reasons[0].text : "no weak features"), payload: { new_limit: +$("#cl-new").value } });
        toast(`Ticket #${t.id} raised, needs: ${t.gates.join(" then ")}`); $("#r-drawer").innerHTML = ""; refreshCounts(); } catch (x) { toast(x.message, true); }
    };
  };
  app.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) $("#r-drawer").innerHTML = ""; });
  loadTab();
};

/* ---------------------------------------------------------------- workflows */
const WS = { status: "pending", open: null };
PAGES.workflows = async (app) => {
  const [list, ledger] = await Promise.all([api(`/api/tickets${WS.status ? "?status=" + WS.status : ""}`), api("/api/ledger")]);
  const sm = list.summary.by_status;
  const gateHtml = (t) => `<div class="gates">${t.gates.map((g, i) => `<span class="gate ${i < t.approvals_given ? "done" : ""}">${i < t.approvals_given ? "✓ " : ""}${g}</span>`).join('<span class="muted">›</span>')}</div>`;
  app.innerHTML = head("Workflows", "Refunds, credit-limit changes and fraud reviews go through approval gates, and every step is written to a tamper-evident audit trail.", `<div class="right row"><span class="muted small">${S.actor ? `Signed in as <b>${esc(S.actor)}</b> (${esc(roleOf(S.actor))})` : "Not signed in: you can read everything, but acting needs your name and PIN (bottom left)"}</span></div>`)
    + `<div class="grid split wide"><div class="card"><div class="tabs">${["pending", "approved", "executed", "rejected", "cancelled", ""].map((s) => `<button data-st="${s}" class="${WS.status === s ? "on" : ""}">${s || "all"}${s && sm[s] != null ? " " + sm[s] : ""}</button>`).join("")}</div>
      ${list.tickets.length ? list.tickets.map((t) => `<div class="alert" data-t="${t.id}" style="cursor:pointer"><div class="row"><b>#${t.id} ${esc(t.title)}</b><span class="badge ${{ pending: "warn", approved: "info", executed: "good", rejected: "bad", cancelled: "" }[t.status]}">${t.status}</span><span class="muted small right">${esc(t.kind.replace(/_/g, " "))}${t.amount != null ? " · " + gbp0(t.amount) : ""}</span></div>
        <div class="row small" style="margin-top:6px">${gateHtml(t)}<span class="muted">raised by ${esc(t.requested_by)}</span></div></div>`).join("") : '<div class="empty">No tickets here. Tickets are raised from a fraud alert or a customer-risk page; sign in as a manager to approve them.</div>'}</div>
      <div class="stack"><div class="card"><h2>New ticket</h2><div class="stack">
        <label class="f">Type<select id="n-kind"><option value="refund">Refund</option><option value="credit_limit_change">Credit limit change</option><option value="fraud_review">Fraud review</option></select></label>
        <label class="f">Title<input type="text" id="n-title" placeholder="e.g. Refund for damaged goods"></label>
        <div class="row"><label class="f" style="flex:1">Customer id<input type="text" id="n-cust"></label><label class="f" style="flex:1">Order id<input type="text" id="n-order"></label></div>
        <label class="f" id="n-amt-l">Amount (£) or new limit<input type="number" id="n-amt" min="0" step="any"></label>
        <label class="f">Reason<input type="text" id="n-reason"></label>
        <button class="btn" id="n-go">Raise ticket</button></div></div>
      <div class="card"><h2>Approval policy</h2><ul class="small" style="margin:0;padding-left:18px">${S.state.policy.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div></div></div>
    <div class="grid cols-2" style="margin-top:16px"><div class="card"><div class="row"><h2 style="margin:0">Audit trail</h2><span class="right row"><a class="btn ghost small" href="/api/audit/export" download>Download CSV</a><button class="btn ghost small" id="verify">Verify</button></span></div><div id="aud" style="margin-top:10px"></div></div>
      <div class="card"><h2>Executed actions</h2>${ledger.ledger.length || ledger.credit_limits.length ? `<table><tbody>${ledger.ledger.map((l) => `<tr><td>${esc(l.kind)}</td><td>${l.order_id ? "order " + esc(l.order_id) : ""} ${l.customer_id ? "· customer " + esc(l.customer_id) : ""}</td><td class="num">${l.amount != null ? gbp(l.amount) : ""}</td><td class="muted small">#${l.ticket_id}</td></tr>`).join("")}${ledger.credit_limits.map((l) => `<tr><td>credit limit</td><td>customer ${esc(l.customer_id)}</td><td class="num">${gbp0(l.limit_gbp)}</td><td class="muted small">#${l.ticket_id}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">Nothing executed yet.</div>'}
      <p class="small muted">Executing writes to this app's own ledger; your business data is never changed.</p></div></div><div id="t-drawer"></div>`;
  const kindUI = () => { const k = $("#n-kind").value; $("#n-amt-l").firstChild.textContent = k === "credit_limit_change" ? "New limit (£)" : k === "refund" ? "Refund amount (£)" : "Amount (£, optional)"; };
  $("#n-kind").onchange = kindUI; kindUI();
  $("#n-go").onclick = async () => {
    const k = $("#n-kind").value, amt = $("#n-amt").value === "" ? null : +$("#n-amt").value;
    try {
      const t = await post("/api/tickets", { kind: k, title: $("#n-title").value || k.replace(/_/g, " "), customer_id: $("#n-cust").value || null, order_id: $("#n-order").value || null, amount: k === "credit_limit_change" ? null : amt, reason: $("#n-reason").value, payload: k === "credit_limit_change" ? { new_limit: amt } : {} });
      toast(`Ticket #${t.id} raised, needs: ${t.gates.join(" then ")}`); WS.status = "pending"; refreshCounts(); route();
    } catch (x) { toast(x.message, true); }
  };
  const loadAudit = async () => {
    const a = await api("/api/audit?limit=12");
    $("#aud").innerHTML = a.length ? a.map((x) => `<div class="small" style="padding:4px 0;border-bottom:1px solid var(--border)"><span class="muted">${esc(x.at)}</span> <b>${esc(x.actor)}</b> ${esc(x.action)}${x.ticket_id ? " #" + x.ticket_id : ""} <span class="mono muted" title="${esc(x.hash)}">${esc(x.hash.slice(0, 8))}</span></div>`).join("") : '<div class="empty">No entries yet.</div>';
  };
  $("#verify").onclick = async () => {
    try {
      const v = await api("/api/audit/verify");
      toast(v.ok ? `Audit intact: ${v.entries} entries, signed head ${v.anchor}, ${v.reconcile.tickets_checked} tickets match the trail` : `Audit problem: ${v.problems[0]}${v.problems.length > 1 ? ` (+${v.problems.length - 1} more)` : ""}`, !v.ok);
    } catch (x) { toast(x.message, true); }
  };
  loadAudit();
  const openTicket = async (id) => {
    WS.open = id;
    const t = await api(`/api/tickets/${id}`);
    const mine = t.requested_by === S.actor, role = roleOf(S.actor) || "nobody";
    $("#t-drawer").innerHTML = `<div class="scrim" data-close></div><div class="drawer stack"><div class="row"><h2 style="margin:0">#${t.id} ${esc(t.title)}</h2><button class="btn ghost small right" data-close>Close</button></div>
      <div class="row"><span class="badge ${{ pending: "warn", approved: "info", executed: "good", rejected: "bad", cancelled: "" }[t.status]}">${t.status}</span><span class="badge">${esc(t.kind.replace(/_/g, " "))}</span>${t.amount != null ? `<span class="badge">${gbp(t.amount)}</span>` : ""}</div>
      <p class="small">${t.customer_id ? "Customer " + esc(t.customer_id) + ". " : ""}${t.order_id ? "Order " + esc(t.order_id) + ". " : ""}Raised by ${esc(t.requested_by)}.</p>
      ${t.reason ? `<div class="note small">${esc(t.reason)}</div>` : ""}
      <div><h3>Gates</h3>${gateHtml(t)}${t.status === "pending" ? `<p class="small muted">Next approval: a ${esc(t.next_gate)} (not ${esc(t.requested_by)}).</p>` : ""}</div>
      <div><h3>Decisions</h3>${t.approvals.map((a) => `<div class="small"><b>${esc(a.approver)}</b> (${a.role}) ${a.decision}d ${a.comment ? "- " + esc(a.comment) : ""} <span class="muted">${esc(a.at)}</span></div>`).join("") || '<span class="muted small">None yet.</span>'}</div>
      <label class="f">Comment<input type="text" id="t-c"></label>
      <div class="row">${t.status === "pending" ? '<button class="btn" data-do="approve">Approve</button><button class="btn danger" data-do="reject">Reject</button>' : ""}${t.status === "approved" ? '<button class="btn" data-do="execute">Execute</button>' : ""}${["pending", "approved"].includes(t.status) ? '<button class="btn ghost" data-do="cancel">Cancel ticket</button>' : ""}</div>
      <p class="small muted">${S.actor ? `Signed in as ${esc(S.actor)} (${esc(role)})${mine ? ", who raised this ticket" : ""}.` : "Sign in to act on this ticket."} The server enforces the rules; a refused action says why.</p>
      <div><h3>Audit</h3>${t.audit.map((x) => `<div class="small"><span class="muted">${esc(x.at)}</span> <b>${esc(x.actor)}</b> ${esc(x.action)} <span class="mono muted">${esc(x.hash.slice(0, 8))}</span></div>`).join("")}</div></div>`;
  };
  app.onclick = async (e) => {
    const st = e.target.closest("[data-st]"); if (st) { WS.status = st.dataset.st; return route(); }
    if (e.target.closest("[data-close]")) { WS.open = null; $("#t-drawer").innerHTML = ""; return; }
    const act = e.target.closest("[data-do]");
    if (act) {
      const id = $(".drawer h2").textContent.match(/#(\d+)/)[1];
      try { await post(`/api/tickets/${id}/${act.dataset.do}`, { comment: $("#t-c").value }); toast("Done"); refreshCounts(); await route(); openTicket(id); }
      catch (x) { toast(x.message, true); }
      return;
    }
    const card = e.target.closest("[data-t]"); if (card) openTicket(card.dataset.t);
  };
  if (WS.open) openTicket(WS.open).catch(() => { WS.open = null; });
};
const roleOf = (n) => (S.state.users.find((u) => u.name === n) || {}).role || "";

/* ---------------------------------------------------------------- about & guide */
PAGES.about = async (app) => {
  const L = (r, t) => `<a href="#/${r}">${t}</a>`;
  const sec = (id, title, body, wide = false) => `<section class="card guide-sec${wide ? " wide" : ""}" id="g-${id}"><h2>${title}</h2>${body}</section>`;
  app.innerHTML = head("About & guide", "What this is, how to use it, what it will not do, and where your data lives.")
    + `<nav class="chips guide-toc" aria-label="On this page">${[["what", "What it is"], ["does", "What it does"], ["use", "How to use it"], ["not", "What it does not do"], ["privacy", "Privacy"], ["vision", "Vision and goal"], ["maker", "About the maker"]]
      .map(([i, t]) => `<a class="chip" href="#/about" data-jump="g-${i}">${t}</a>`).join("")}</nav>`
    + `<div class="guide guide-grid">`
    + sec("what", "What it is", `<p>Analyst-in-a-Box is an AI back office for a small business that runs on your own computer. Connect a database or add a spreadsheet and you can ask questions in English or Roman Urdu, see the SQL that answered them, forecast demand, screen for odd orders and refunds, score customers, and push refunds and credit-limit changes through approval gates with a tamper-evident audit trail. It works offline; a language model is optional and its output is never trusted.</p>`)
    + sec("does", "What it does", `<ul>
      <li>${L("", "Dashboard")}: KPI cards with sparklines and charts for revenue, orders, customers and refunds, plus a live example question.</li>
      <li>${L("data", "Data")}: connect SQLite or PostgreSQL, or add a CSV, TSV or Excel file, or paste CSV text; browse the schema.</li>
      <li>${L("ask", "Ask your data")}: plain-language questions turned into one read-only SELECT, shown to you, editable, exportable as CSV.</li>
      <li>${L("forecast", "Forecasts")}: weekly unit demand per product, with totals that add up and a check against a seasonal-naive baseline.</li>
      <li>${L("alerts", "Fraud & anomalies")}: flagged orders and refunds with the reasons, which you can dismiss or turn into a ticket.</li>
      <li>${L("risk", "Customer risk")}: a points scorecard with reason codes and a fairness panel.</li>
      <li>${L("workflows", "Workflows")}: refund and credit-limit tickets that need the right people to approve, a ledger, and an audit trail you can verify.</li></ul>`)
    + sec("use", "How to use it", `<ol>
      <li><b>Look first.</b> The ${L("", "Dashboard")} opens on the bundled sample business (about 15,860 orders), so you can try everything before adding your own data. Asking needs no sign-in.</li>
      <li><b>Add your data</b> on ${L("data", "Data")}. Input: a <span class="mono">.csv</span>, <span class="mono">.tsv</span>, <span class="mono">.txt</span>, <span class="mono">.xlsx</span> or <span class="mono">.xlsm</span> file (drop it on the box or choose it; 40 MB, 1,000,000 rows, 500 columns), or pasted text whose first line is the header, for example
        <pre class="mono">invoice,sku,qty,price
A1001,MUG1,2,4.50
A1002,TEA2,1,6.00</pre>Press Load example to try it. Output: a table per sheet under "My uploads", with inferred column types, set as the active source. Importing needs a manager sign-in; the PINs are in <span class="mono">pins.txt</span> in the data folder on first run.</li>
      <li><b>Build the business tables</b> (optional) on ${L("data", "Data")}: map the columns of an order-lines sheet (invoice, stock code, quantity, date, price, customer, country) and the dashboard, forecasts, fraud screen and risk page work on your data.</li>
      <li><b>Ask</b> on ${L("ask", "Ask your data")}. Input: one question, for example <i>Top 10 products by revenue in 2011</i> or <i>har mahine ki bikri</i>. Output: the SQL, a table, a chart, and a note on how it was understood. "Revenue" means net revenue (completed sales minus cancellations); say "gross revenue" for the figure before cancellations.</li>
      <li><b>Check</b> ${L("forecast", "Forecasts")}, ${L("alerts", "Fraud & anomalies")} and ${L("risk", "Customer risk")}. Output: tables and charts with the reasons shown.</li>
      <li><b>Act</b> on ${L("workflows", "Workflows")}. Sign in, raise a ticket from an alert or a customer, and have the required people approve it. Nobody can approve their own request; every step goes into the audit trail, and Verify checks it.</li></ol>`, true)
    + sec("not", "What it does not do", `<ul>
      <li>It gives no fraud verdicts. There are no labels, so alerts are prompts for a person to look at; many flags are real customers buying a lot.</li>
      <li>The risk model is a refund-behaviour proxy, not credit-loss prediction, and it is weak on the small held-out split.</li>
      <li>Forecasts cover units, not revenue.</li>
      <li>The built-in parser is not a language model. When it cannot map a question it says so instead of guessing.</li>
      <li>PostgreSQL supports Ask and the schema browser only, and was not tested against a live server.</li>
      <li>Sign-in is PINs for a fixed list of four people on one machine, not single sign-on. Approving a refund writes the app's own ledger; it does not pay anyone.</li>
      <li>Uploads have limits (40 MB, 1,000,000 rows, 500 columns), not quotas.</li></ul><p class="small muted">The full list, with numbers, is in the README.</p>`)
    + sec("privacy", "Privacy", `<ul>
      <li>Everything is stored in one folder on this computer (<span class="mono">~/.analyst-in-a-box</span>, or the folder named by <span class="mono">ANALYST_HOME</span>): the app database, your uploads, the sample data, the sign-in PINs file and the audit head and key.</li>
      <li>Uploaded and pasted data stay in that folder. The app has no analytics and sends nothing to its maker.</li>
      <li>The fonts are bundled, so opening a page makes no request to another site.</li>
      <li>Only if you set <span class="mono">ANALYST_LLM</span> (Ollama, Anthropic or an OpenAI-compatible server) are your question and the table and column names sent to that model. Result rows are not. With Ollama it stays on your machine.</li></ul>`)
    + sec("vision", "Vision and goal", `<p>The goal is a back office a small shop can run without a data team or a cloud account: questions answered with the SQL visible, actions that need two people, and a record nobody can quietly rewrite. It is a one-machine tool today. The gaps still open, from the review notes, are adding and removing people from the screen, saved questions and a scheduled digest, refunds that call a real payment provider, revenue forecasts with what-if prices, and attachments and comments on tickets.</p>`)
    + sec("maker", "About the maker", `<p>Built by <b>Muhammad Hammas</b>, AI engineer. Source and issues: <a href="https://github.com/hammasbuilds/analyst-in-a-box" target="_blank" rel="noopener">github.com/hammasbuilds/analyst-in-a-box</a>. More projects: <a href="https://github.com/hammasbuilds" target="_blank" rel="noopener">github.com/hammasbuilds</a>.</p>`)
    + `</div>`;
  app.onclick = (e) => {
    const j = e.target.closest("[data-jump]"); if (!j) return;
    e.preventDefault(); const t = document.getElementById(j.dataset.jump); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
  };
};

/* ---------------------------------------------------------------- boot */
async function boot() {
  S.state = await api("/api/state");
  const l = S.state.llm;
  $("#llmstat").textContent = l.configured ? `model: ${l.provider}` : "no model needed";
  S.actor = S.state.me ? S.state.me.name : null;
  drawWho();
}
function drawWho() {
  const box = $("#who"), me = S.state.me;
  $("#who-toggle").textContent = me ? me.name.split(" ")[0] : "Sign in";
  $("#who-toggle").setAttribute("aria-label", me ? `Signed in as ${me.name}; account` : "Sign in");
  if (me) {
    box.innerHTML = `<div class="small">Signed in as <b>${esc(me.name)}</b> (${esc(me.role)})</div>
      <div class="row"><button class="btn ghost small" id="pin-btn">Change PIN</button><button class="btn ghost small" id="out-btn">Sign out</button></div>`;
    $("#out-btn").onclick = async () => { await post("/api/logout", {}); await afterAuth(); };
    $("#pin-btn").onclick = () => {
      box.innerHTML = `<form id="pin-form" class="stack" style="gap:6px"><label class="small" for="pin-old">Current PIN</label><input id="pin-old" type="password" autocomplete="current-password" required>
        <label class="small" for="pin-new">New PIN (6+ characters)</label><input id="pin-new" type="password" autocomplete="new-password" minlength="6" required>
        <div class="row"><button class="btn small" type="submit">Save</button><button class="btn ghost small" type="button" id="pin-cancel">Cancel</button></div></form>`;
      $("#pin-cancel").onclick = drawWho;
      $("#pin-form").onsubmit = async (e) => { e.preventDefault(); try { await post("/api/me/pin", { old_pin: $("#pin-old").value, new_pin: $("#pin-new").value }); toast("PIN changed; sign in again"); await afterAuth(); } catch (x) { toast(x.message, true); } };
    };
    return;
  }
  box.innerHTML = `<form id="login" class="stack" style="gap:6px"><label class="small" for="actor">Sign in as</label>
    <select id="actor">${S.state.users.map((u) => `<option value="${esc(u.name)}">${esc(u.name)} (${u.role})</option>`).join("")}</select>
    <label class="small" for="pin">PIN</label><input id="pin" type="password" autocomplete="current-password" required>
    <button class="btn small" type="submit">Sign in</button>
    <span class="small muted">${S.state.pins_file ? "First-run PINs are in pins.txt in the data folder." : "Ask the owner for your PIN."}</span></form>`;
  $("#login").onsubmit = async (e) => {
    e.preventDefault();
    try { await post("/api/login", { name: $("#actor").value, pin: $("#pin").value }); await afterAuth(); toast("Signed in"); }
    catch (x) { toast(x.message, true); }
  };
}
/* A table wider than its card scrolls inside it; mark it so the cut-off edge reads as "more", not as clipped. */
function markScrolls() {
  $$(".scroll").forEach((el) => {
    const more = el.scrollWidth > el.clientWidth + 2;
    el.classList.toggle("x-more", more && el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
    let hint = el.nextElementSibling && el.nextElementSibling.classList.contains("scroll-hint") ? el.nextElementSibling : null;
    if (more && !hint) { el.insertAdjacentHTML("afterend", '<p class="scroll-hint">Scroll sideways in the table for more columns.</p>'); el.onscroll = markScrolls; }
    if (!more && hint) hint.remove();
  });
}
let markT;
new MutationObserver(() => { clearTimeout(markT); markT = setTimeout(markScrolls, 120); }).observe(document.querySelector("#app"), { childList: true, subtree: true });
window.addEventListener("resize", () => { clearTimeout(markT); markT = setTimeout(markScrolls, 120); });
async function afterAuth() { setWhoOpen(false); await boot(); await refreshCounts(); route(); }
function setWhoOpen(on) {
  document.querySelector(".side").classList.toggle("who-open", on);
  $("#who-toggle").setAttribute("aria-expanded", String(on));
  if (on) { const f = document.querySelector("#who input, #who select, #who button"); if (f) f.focus(); }
}
$("#who-toggle").onclick = () => setWhoOpen(!document.querySelector(".side").classList.contains("who-open"));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setWhoOpen(false); });
window.addEventListener("hashchange", () => setWhoOpen(false));
$("#theme").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = next;
  try { localStorage.setItem("aib-theme", next); } catch (e) { /* ignore */ }
};
(async () => { await boot(); await refreshCounts(); route(); })();
