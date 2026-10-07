/* Micro-interactions: ripple, button busy/success/error, pointer spotlight, staggered entrances, KPI count-up.
   Delegated and dependency-free; the app code does not need to know about any of it. */
const Motion = (() => {
  const reduce = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
  const ease = (t) => 1 - Math.pow(1 - t, 3);  // easeOutCubic, a gentler cousin of the emphasized-decelerate curve

  function shake(el) {
    if (!el) return;
    el.classList.remove("shake"); void el.offsetWidth; el.classList.add("shake");
    el.setAttribute("aria-invalid", "true");
    el.addEventListener("animationend", () => el.classList.remove("shake"), { once: true });
    el.addEventListener("input", () => el.removeAttribute("aria-invalid"), { once: true });
  }

  /* Ripple from the pointer position. */
  document.addEventListener("pointerdown", (e) => {
    const b = e.target.closest && e.target.closest(".btn, .chip, .tabs button");
    if (!b || b.disabled || reduce()) return;
    const r = b.getBoundingClientRect(), s = Math.max(r.width, r.height) * 2;
    const dot = document.createElement("span");
    dot.className = "ripple";
    dot.style.cssText = `width:${s}px;height:${s}px;left:${e.clientX - r.left - s / 2}px;top:${e.clientY - r.top - s / 2}px`;
    b.appendChild(dot);
    dot.addEventListener("animationend", () => dot.remove());
  });

  /* A button that triggers a request shows a spinner until it settles, then flashes cyan or red. */
  let lastBtn = null, lastT = 0;
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("button.btn");
    if (b) { lastBtn = b; lastT = performance.now(); }
  }, true);
  const realFetch = window.fetch.bind(window);
  window.fetch = (...args) => {
    const b = lastBtn && performance.now() - lastT < 150 && lastBtn.isConnected ? lastBtn : null;
    if (b) { b._n = (b._n || 0) + 1; b.classList.add("is-busy"); b.setAttribute("aria-busy", "true"); }
    const settle = (ok) => {
      if (!b) return;
      if (--b._n > 0) return;
      b.classList.remove("is-busy"); b.removeAttribute("aria-busy");
      const k = ok ? "flash-ok" : "flash-bad";
      b.classList.add(k); setTimeout(() => b.classList.remove(k), 700);
    };
    const p = realFetch(...args);
    p.then((r) => settle(r.ok), () => settle(false));
    return p;
  };

  /* Pointer-following spotlight on cards (fine pointers only). */
  document.addEventListener("pointermove", (e) => {
    if (!fine.matches || reduce()) return;
    const c = e.target.closest && e.target.closest(".card, .kpi");
    if (!c) return;
    const r = c.getBoundingClientRect();
    c.style.setProperty("--mx", e.clientX - r.left + "px");
    c.style.setProperty("--my", e.clientY - r.top + "px");
  }, { passive: true });

  /* KPI count-up: "£12,345", "4,312", "37.5%" roll up from zero; anything else is left alone. */
  function countUp(el) {
    if (reduce() || el.dataset.counted) return;
    el.dataset.counted = "1";
    const txt = el.textContent.trim();
    const m = /^([^\d\-]*)(-?[\d,]+(?:\.\d+)?)(.{0,2})$/.exec(txt);
    if (!m || /\sof\s/.test(txt)) return;
    const end = parseFloat(m[2].replace(/,/g, "")), dec = (m[2].split(".")[1] || "").length, commas = m[2].includes(",");
    if (!isFinite(end) || end === 0) return;
    const fmt = (v) => m[1] + (commas ? v.toLocaleString("en-GB", { minimumFractionDigits: dec, maximumFractionDigits: dec }) : v.toFixed(dec)) + m[3];
    const t0 = performance.now(), dur = 1100;
    el.setAttribute("aria-label", txt);
    const tick = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      el.textContent = t < 1 ? fmt(end * ease(t)) : txt;
      if (t < 1) requestAnimationFrame(tick); else el.removeAttribute("aria-label");
    };
    requestAnimationFrame(tick);
  }

  /* Stagger entrances for whatever the app has just rendered. */
  const STAG = ".kpis > .kpi, .grid > .card, .alert, .chips > .chip";
  function enter(root) {
    if (reduce()) return;
    root.querySelectorAll(STAG).forEach((n) => {
      if (n.dataset.in) return;
      n.dataset.in = "1";
      const sib = Array.prototype.indexOf.call(n.parentElement.children, n);
      n.style.setProperty("--i", Math.min(sib, 12));
      n.classList.add("enter");
    });
    root.querySelectorAll(".kpi .v").forEach(countUp);
  }
  const app = document.getElementById("app");
  if (app) {
    let q = false;
    new MutationObserver(() => { if (q) return; q = true; requestAnimationFrame(() => { q = false; enter(app); }); })
      .observe(app, { childList: true, subtree: true });
  }
  return { shake };
})();
