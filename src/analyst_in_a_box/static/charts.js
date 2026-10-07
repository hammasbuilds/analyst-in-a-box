/* Small dependency-free SVG charts. Every chart is a string of inline SVG, so it themes with CSS variables. */
const Charts = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];

  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  function compact(v) {
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "m";
    if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k";
    return String(Math.round(v * 10) / 10);
  }

  /* series: [{name, values, dashed, color}], labels shared. split: index where the forecast starts (draws a divider). */
  function line({ labels, series, fmt = compact, height = 230, split = null }) {
    const W = 520, H = height, L = 44, R = 12, T = 12, B = 28;
    const all = series.flatMap((s) => s.values.filter((v) => v != null));
    if (!all.length || labels.length < 2) return '<div class="empty">Not enough points to draw.</div>';
    const lo = Math.min(0, ...all), hi = niceMax(Math.max(...all));
    const x = (i) => L + (i * (W - L - R)) / (labels.length - 1);
    const y = (v) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
    let g = "";
    for (let k = 0; k <= 4; k++) {
      const v = lo + ((hi - lo) * k) / 4, yy = y(v);
      g += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text x="${L - 6}" y="${yy + 4}" text-anchor="end">${esc(fmt(v))}</text>`;
    }
    const step = Math.ceil(labels.length / 7);
    let xl = "";
    labels.forEach((lb, i) => { if (i % step === 0 && (labels.length - 1 - i >= step * 0.6 || i === labels.length - 1)) xl += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(String(lb).slice(0, 10))}</text>`; });
    let body = "";
    if (split != null && split > 0 && split < labels.length) {
      const xs = x(split - 0.5);
      body += `<rect x="${xs}" y="${T}" width="${W - R - xs}" height="${H - T - B}" fill="var(--accent-soft)" opacity=".55"/><text x="${xs + 6}" y="${T + 12}">forecast</text>`;
    }
    series.forEach((s, si) => {
      const col = s.color || COLORS[si % COLORS.length];
      let d = "", pen = false;
      s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
      body += `<path d="${d}" fill="none" stroke="${col}" stroke-width="2.2" stroke-linejoin="round" ${s.dashed ? 'stroke-dasharray="6 4"' : ""}/>`;
      if (labels.length <= 60) s.values.forEach((v, i) => { if (v != null) body += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3" fill="${col}"><title>${esc(s.name)} ${esc(labels[i])}: ${esc(fmt(v))}</title></circle>`; });
    });
    const legend = series.length > 1 ? `<div class="legend">${series.map((s, i) => `<span><i style="background:${s.color || COLORS[i % COLORS.length]}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
    return `<svg class="chart draw" viewBox="0 0 ${W} ${H}" role="img"><g class="grid">${g}</g>${xl}${body}</svg>${legend}`;
  }

  /* Horizontal bars: rows [[label, value], ...] */
  function bars({ rows, fmt = compact, color = "var(--c1)", labelW = 150 }) {
    if (!rows.length) return '<div class="empty">No rows.</div>';
    const W = 520, rowH = 26, H = rows.length * rowH + 8, R = 58;
    const max = Math.max(...rows.map((r) => Math.abs(r[1])), 1e-9);
    let s = "";
    rows.forEach((r, i) => {
      const yy = 4 + i * rowH, w = (Math.abs(r[1]) / max) * (W - labelW - R);
      const lb = String(r[0]);
      s += `<text x="${labelW - 8}" y="${yy + 16}" text-anchor="end">${esc(lb.length > Math.floor(labelW / 7.4) ? lb.slice(0, Math.floor(labelW / 7.4) - 1) + "…" : lb)}<title>${esc(lb)}</title></text>`
        + `<rect x="${labelW}" y="${yy + 3}" width="${Math.max(w, 1).toFixed(1)}" height="${rowH - 9}" rx="4" fill="${color}" class="bx" style="--i:${i}"><title>${esc(lb)}: ${esc(fmt(r[1]))}</title></rect>`
        + `<text x="${labelW + w + 6}" y="${yy + 16}">${esc(fmt(r[1]))}</text>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">${s}</svg>`;
  }

  /* Vertical bars for time-like or few categories */
  function columns({ rows, fmt = compact, color = "var(--c1)", height = 230, highlightLast = false }) {
    if (!rows.length) return '<div class="empty">No rows.</div>';
    const W = 520, H = height, L = 44, R = 8, T = 12, B = 30;
    const hi = niceMax(Math.max(...rows.map((r) => r[1]), 1e-9));
    const bw = (W - L - R) / rows.length;
    let g = "";
    for (let k = 0; k <= 4; k++) { const v = (hi * k) / 4, yy = T + (1 - v / hi) * (H - T - B); g += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text x="${L - 6}" y="${yy + 4}" text-anchor="end">${esc(fmt(v))}</text>`; }
    const step = Math.ceil(rows.length / 8);
    let b = "";
    rows.forEach((r, i) => {
      const h = (r[1] / hi) * (H - T - B), xx = L + i * bw + bw * 0.12;
      const fade = highlightLast && i === rows.length - 1;
      b += `<rect x="${xx.toFixed(1)}" y="${(H - B - h).toFixed(1)}" width="${(bw * 0.76).toFixed(1)}" height="${Math.max(h, 0).toFixed(1)}" rx="3" fill="${color}" class="bc" style="--i:${i}" ${fade ? 'opacity=".45"' : ""}><title>${esc(r[0])}: ${esc(fmt(r[1]))}${fade ? " (partial month)" : ""}</title></rect>`;
      if (i % step === 0) b += `<text x="${(xx + bw * 0.38).toFixed(1)}" y="${H - 10}" text-anchor="middle">${esc(String(r[0]).slice(2, 10))}</text>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img"><g class="grid">${g}</g>${b}</svg>`;
  }

  /* Share bar: one stacked bar with a legend */
  function share({ rows, fmt = compact }) {
    const total = rows.reduce((a, r) => a + r[1], 0) || 1;
    const segs = rows.map((r, i) => `<i style="display:inline-block;height:100%;width:${((r[1] / total) * 100).toFixed(2)}%;background:${COLORS[i % COLORS.length]}" title="${esc(r[0])}: ${esc(fmt(r[1]))} (${((r[1] / total) * 100).toFixed(1)}%)"></i>`).join("");
    const leg = rows.map((r, i) => `<span><i style="background:${COLORS[i % COLORS.length]};height:10px;width:10px;border-radius:3px"></i>${esc(r[0])} ${((r[1] / total) * 100).toFixed(0)}%</span>`).join("");
    return `<div style="height:22px;border-radius:8px;overflow:hidden;display:flex;background:var(--surface-2)">${segs}</div><div class="legend">${leg}</div>`;
  }

  /* Sparkline for a KPI card: stretches to the card width, the end point is a gold dot. */
  function spark(values) {
    const v = values.filter((x) => x != null);
    if (v.length < 2 || Math.max(...v) === Math.min(...v)) return "";
    const W = 160, H = 34, P = 5, lo = Math.min(...v), hi = Math.max(...v);
    const x = (i) => P + (i * (W - 2 * P)) / (v.length - 1), y = (n) => H - P - ((n - lo) / (hi - lo)) * (H - 2 * P);
    const d = v.map((n, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(n).toFixed(1)}`).join("");
    const last = v.length - 1;
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="trend over the last ${v.length} weeks"><path class="ar" d="${d}L${x(last).toFixed(1)},${H}L${x(0).toFixed(1)},${H}Z"/><path class="ln" d="${d}"/><path class="dot" d="M${x(last).toFixed(1)},${y(v[last]).toFixed(1)}h.01"/></svg>`;
  }

  return { line, bars, columns, share, spark, esc, compact };
})();
