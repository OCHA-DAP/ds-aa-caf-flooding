/* Charts, map and downloads for the CAR flood impact vs rainfall page.
 *
 * Hand-rolled SVG (team convention for static data apps): thin marks, 4px rounded
 * data-ends anchored to the baseline, 2px surface gaps, hairline grid, a hover/focus
 * tooltip on every mark, and a "Show data" table under each chart. All text that comes
 * from the data is inserted with textContent. */
"use strict";

const D = JSON.parse(document.getElementById("page-data").textContent);

const C = {
  impact: "#1e795f", era5: "#1862d8", imerg: "#aa7222", chirps: "#c2457a",
  grid: "#ebeff0", axis: "#c4d0d1", ink: "#1f2324", ink2: "#3f4748", muted: "#5e6a6b",
  surface: "#ffffff", band: "#f5f7f7", zero: "#7e8e8f",
};
const PRODUCTS = ["ERA5", "IMERG", "CHIRPS"];
const PRODUCT_COLOR = { ERA5: C.era5, IMERG: C.imerg, CHIRPS: C.chirps };
const productLegend = (type) => PRODUCTS.map((p) => ({ label: p, color: PRODUCT_COLOR[p], type }));
const productSeg = PRODUCTS.map((p) => ({ label: p, value: p }));
const YEARS = [2021, 2022, 2023, 2024, 2025];
const YEAR_COLOR = { 2021: "#7dc1ad", 2022: "#51ac92", 2023: "#269777", 2024: "#18614c", 2025: "#0f3c30" };
const MAP_RAMP = ["#7dc1ad", "#51ac92", "#269777", "#18614c", "#0f3c30"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const fmt = (v) => (v == null || Number.isNaN(v) ? "–" : Math.round(v).toLocaleString("en-US"));
const fmtR = (v, d = 2) => v.toFixed(d).replace("-", "−");
const fmtSigned = (v, d = 0) => {
  const a = Math.abs(v).toFixed(d);
  return Number(a) === 0 ? a : (v > 0 ? "+" : "−") + a;
};
const compact = (v) => {
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "k";
  return String(Math.round(v * 100) / 100);
};

/* ── svg + scale helpers ─────────────────────────────────────────────── */
const NS = "http://www.w3.org/2000/svg";
function S(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function T(parent, x, y, str, attrs) {
  const t = S("text", Object.assign({ x, y, fill: C.muted, "font-size": 11 }, attrs || {}), parent);
  t.textContent = str;
  return t;
}
const lin = (d0, d1, r0, r1) => (v) => r0 + ((v - d0) / (d1 - d0 || 1)) * (r1 - r0);
function ticks(min, max, n) {
  const span = max - min || 1;
  const raw = span / (n || 5);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const e = raw / mag;
  const step = (e >= 7.5 ? 10 : e >= 3.5 ? 5 : e >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(min / step - 1e-9) * step; v <= max + step * 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}
function niceDomain(min, max, n) {
  const t = ticks(min, max, n);
  const step = t.length > 1 ? t[1] - t[0] : 1;
  let lo = t[0], hi = t[t.length - 1];
  if (lo > min) lo -= step;
  if (hi < max) hi += step;
  return [lo, hi];
}
/* vertical bar, rounded at the data end only (works for negative values too) */
function vbar(x, y0, w, y1, r) {
  const h = Math.abs(y1 - y0);
  r = Math.min(r == null ? 4 : r, w / 2, h);
  const up = y1 <= y0, s = up ? 1 : -1;
  return `M${x},${y0}V${y1 + s * r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + s * r}V${y0}Z`;
}
/* horizontal bar segment from x0 to x1, rounded at the right end when `end` */
function hbar(x0, y, x1, h, end) {
  if (!end) return `M${x0},${y}H${x1}V${y + h}H${x0}Z`;
  const r = Math.min(4, h / 2, Math.max(0, x1 - x0));
  return `M${x0},${y}H${x1 - r}Q${x1},${y} ${x1},${y + r}V${y + h - r}Q${x1},${y + h} ${x1 - r},${y + h}H${x0}Z`;
}
function widthOf(el) { return Math.max(280, Math.floor(el.getBoundingClientRect().width)); }

/* ── tooltip ─────────────────────────────────────────────────────────── */
const tip = document.createElement("div");
tip.className = "tip";
tip.setAttribute("role", "tooltip");
document.body.appendChild(tip);
function showTip(x, y, title, rows) {
  tip.replaceChildren();
  const t = document.createElement("div");
  t.className = "tip-title";
  t.textContent = title;
  tip.appendChild(t);
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "tip-row";
    if (r.color) {
      const k = document.createElement("span");
      k.className = "tip-key";
      k.style.background = r.color;
      row.appendChild(k);
    }
    const v = document.createElement("strong");
    v.textContent = r.value;
    const l = document.createElement("span");
    l.className = "tip-label";
    l.textContent = r.label;
    row.append(v, l);
    tip.appendChild(row);
  }
  tip.style.display = "block";
  const pad = 14, b = tip.getBoundingClientRect();
  let left = x + pad, top = y + pad;
  if (left + b.width > innerWidth - 8) left = x - b.width - pad;
  if (top + b.height > innerHeight - 8) top = y - b.height - pad;
  tip.style.left = Math.max(4, left) + "px";
  tip.style.top = Math.max(4, top) + "px";
}
function hideTip() { tip.style.display = "none"; }
/* pointer + keyboard tooltip on a hit target; `mark` (optional) lifts on hover */
function bindTip(hit, mark, content) {
  hit.classList.add("hit");
  hit.setAttribute("tabindex", "0");
  const on = (x, y) => { const c = content(); showTip(x, y, c[0], c[1]); if (mark) mark.classList.add("mark-hover"); };
  const off = () => { hideTip(); if (mark) mark.classList.remove("mark-hover"); };
  hit.addEventListener("pointermove", (e) => on(e.clientX, e.clientY));
  hit.addEventListener("pointerleave", off);
  hit.addEventListener("focus", () => { const b = hit.getBoundingClientRect(); on(b.x + b.width / 2, b.y); });
  hit.addEventListener("blur", off);
}

/* ── legend + data tables ────────────────────────────────────────────── */
function legend(el, items) {
  el.replaceChildren();
  for (const it of items) {
    const s = document.createElement("span");
    s.className = "item";
    const k = document.createElement("span");
    k.className = it.type === "line" ? "ln" : it.type === "dot" ? "dot" : "sw";
    k.style.background = it.color;
    const l = document.createElement("span");
    l.textContent = it.label;
    s.append(k, l);
    el.appendChild(s);
  }
}
function dataTable(el, cols, rows) {
  el.replaceChildren();
  const wrap = document.createElement("div");
  wrap.className = "tbl-wrap";
  const t = document.createElement("table");
  t.className = "t";
  const hr = t.createTHead().insertRow();
  for (const c of cols) {
    const th = document.createElement("th");
    th.textContent = c.label;
    if (c.num) th.className = "num";
    hr.appendChild(th);
  }
  const tb = t.createTBody();
  for (const r of rows) {
    const tr = tb.insertRow();
    for (const c of cols) {
      const td = tr.insertCell();
      const v = c.get(r);
      td.textContent = v == null ? "–" : v;
      if (c.num) td.className = "num";
    }
  }
  wrap.appendChild(t);
  el.appendChild(wrap);
}
/* only build tables when opened (keeps the initial render light) */
function lazyTable(id, cols, rowsFn) {
  const det = document.getElementById(id);
  if (!det) return;
  det.addEventListener("toggle", () => {
    if (det.open && !det.dataset.built) {
      dataTable(det.querySelector(".tbl"), cols, rowsFn());
      det.dataset.built = "1";
    }
  });
}

/* ── chart: columns (one series) ─────────────────────────────────────── */
function columnChart(el, o) {
  el.replaceChildren();
  const W = widthOf(el), H = o.height || 220;
  const m = { l: 50, r: 8, t: 10, b: o.groups ? 40 : 24 };
  const svg = S("svg", { width: W, height: H, role: "img", "aria-label": o.aria || "" }, el);
  const n = o.items.length, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const step = iw / n, bw = Math.min(24, Math.max(2, step - 2));
  const ymax = niceDomain(0, Math.max(...o.items.map((d) => d.value), 1), 4)[1];
  const y = lin(0, ymax, m.t + ih, m.t);
  for (const v of ticks(0, ymax, 4)) {
    S("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === 0 ? C.axis : C.grid, "stroke-width": 1 }, svg);
    T(svg, m.l - 6, y(v) + 3.5, compact(v), { "text-anchor": "end" });
  }
  o.items.forEach((d, i) => {
    const x = m.l + i * step + (step - bw) / 2;
    if (d.value > 0) {
      const p = S("path", { d: vbar(x, y(0), bw, y(d.value)), fill: d.color || o.color }, svg);
      const hit = S("rect", { x: m.l + i * step, y: m.t, width: step, height: ih, fill: "transparent" }, svg);
      bindTip(hit, p, () => d.tip);
    } else {
      const hit = S("rect", { x: m.l + i * step, y: m.t, width: step, height: ih, fill: "transparent" }, svg);
      bindTip(hit, null, () => d.tip);
    }
    if (!o.groups && (o.labelEvery ? i % o.labelEvery === 0 : true))
      T(svg, m.l + i * step + step / 2, H - m.b + 15, d.label, { "text-anchor": "middle" });
    if (o.valueLabels && d.value > 0)
      T(svg, x + bw / 2, y(d.value) - 5, fmt(d.value), { "text-anchor": "middle", fill: C.ink2, "font-size": 11 });
  });
  if (o.groups) {
    for (const g of o.groups) {
      const x0 = m.l + g.from * step, x1 = m.l + (g.to + 1) * step;
      S("line", { x1: x0, x2: x0, y1: H - m.b, y2: H - m.b + 8, stroke: C.axis }, svg);
      T(svg, (x0 + x1) / 2, H - m.b + 24, g.label, { "text-anchor": "middle", fill: C.ink2, "font-size": 12 });
      if (g.ticks) for (const tk of g.ticks)
        T(svg, m.l + (g.from + tk.i) * step + step / 2, H - m.b + 11, tk.label, { "text-anchor": "middle", "font-size": 9.5 });
    }
  }
}

/* ── chart: lines with crosshair ─────────────────────────────────────── */
function lineChart(el, o) {
  el.replaceChildren();
  const W = widthOf(el), H = o.height || 260;
  const m = { l: 50, r: 14, t: 12, b: 26 };
  const svg = S("svg", { width: W, height: H, role: "img", "aria-label": o.aria || "" }, el);
  const xs = [...new Set(o.series.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b);
  const all = o.series.flatMap((s) => s.points.map((p) => p[1]));
  const [ylo, yhi] = o.yDomain || niceDomain(Math.min(...all), Math.max(...all), 5);
  const x = lin(xs[0], xs[xs.length - 1], m.l, W - m.r), y = lin(ylo, yhi, H - m.b, m.t);
  if (o.band) {
    S("rect", { x: x(o.band[0] - 0.5), y: m.t, width: x(o.band[1] + 0.5) - x(o.band[0] - 0.5), height: H - m.b - m.t, fill: C.band }, svg);
    T(svg, x(o.band[1] + 0.5) - 4, m.t + 12, o.bandLabel || "", { "text-anchor": "end", "font-size": 10.5 });
  }
  for (const v of ticks(ylo, yhi, 5)) {
    S("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: C.grid }, svg);
    T(svg, m.l - 6, y(v) + 3.5, fmt(v), { "text-anchor": "end" });
  }
  for (const v of ticks(xs[0], xs[xs.length - 1], Math.max(3, Math.floor(W / 90))))
    T(svg, x(v), H - m.b + 16, String(v), { "text-anchor": "middle" });
  S("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: C.axis }, svg);
  for (const s of o.series) {
    const d = s.points.map((p, i) => (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1)).join("");
    S("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    if (s.trend) {
      const [a, b] = s.trend;
      S("line", { x1: x(a[0]), y1: y(a[1]), x2: x(b[0]), y2: y(b[1]), stroke: s.color, "stroke-width": 1, opacity: 0.55 }, svg);
    }
  }
  const cross = S("line", { y1: m.t, y2: H - m.b, stroke: C.ink2, "stroke-width": 1, visibility: "hidden" }, svg);
  const dots = o.series.map((s) => S("circle", { r: 4, fill: s.color, stroke: C.surface, "stroke-width": 2, visibility: "hidden" }, svg));
  const ov = S("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.b - m.t, fill: "transparent" }, svg);
  ov.setAttribute("tabindex", "0");
  let cur = xs.length - 1;
  const draw = (cx, cy) => {
    const xv = xs[cur];
    cross.setAttribute("x1", x(xv)); cross.setAttribute("x2", x(xv)); cross.setAttribute("visibility", "visible");
    const rows = [];
    o.series.forEach((s, i) => {
      const p = s.points.find((q) => q[0] === xv);
      if (p) {
        dots[i].setAttribute("cx", x(xv)); dots[i].setAttribute("cy", y(p[1])); dots[i].setAttribute("visibility", "visible");
        rows.push({ color: s.color, value: o.fmtY ? o.fmtY(p[1]) : fmt(p[1]), label: s.name });
      } else dots[i].setAttribute("visibility", "hidden");
    });
    showTip(cx, cy, o.fmtX ? o.fmtX(xv) : String(xv), rows);
  };
  ov.addEventListener("pointermove", (e) => {
    const b = svg.getBoundingClientRect(), px = e.clientX - b.left;
    let best = 0, bd = Infinity;
    xs.forEach((v, i) => { const dd = Math.abs(x(v) - px); if (dd < bd) { bd = dd; best = i; } });
    cur = best; draw(e.clientX, e.clientY);
  });
  const leave = () => { hideTip(); cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); };
  ov.addEventListener("pointerleave", leave);
  ov.addEventListener("blur", leave);
  ov.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    cur = Math.max(0, Math.min(xs.length - 1, cur + (e.key === "ArrowRight" ? 1 : -1)));
    const b = svg.getBoundingClientRect();
    draw(b.left + x(xs[cur]), b.top + m.t);
  });
  ov.addEventListener("focus", () => { const b = svg.getBoundingClientRect(); draw(b.left + x(xs[cur]), b.top + m.t); });
}

/* ── chart: grouped bars around zero, optional CI whiskers + ref lines ─ */
function groupedBars(el, o) {
  el.replaceChildren();
  const W = widthOf(el), H = o.height || 240;
  const m = { l: 50, r: 10, t: 12, b: 24 };
  const svg = S("svg", { width: W, height: H, role: "img", "aria-label": o.aria || "" }, el);
  const vals = o.series.flatMap((s) => s.values.flatMap((v) => [v.v, v.lo ?? v.v, v.hi ?? v.v]));
  for (const r of o.refs || []) vals.push(r.v);
  const [ylo, yhi] = o.yDomain || niceDomain(Math.min(0, ...vals), Math.max(0, ...vals), 5);
  const y = lin(ylo, yhi, H - m.b, m.t);
  const n = o.cats.length, iw = W - m.l - m.r, step = iw / n;
  const k = o.series.length, bw = Math.min(16, (step - 6) / k - 2);
  for (const v of ticks(ylo, yhi, 5)) {
    S("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === 0 ? C.axis : C.grid }, svg);
    T(svg, m.l - 6, y(v) + 3.5, o.fmtTick ? o.fmtTick(v) : compact(v), { "text-anchor": "end" });
  }
  for (const r of o.refs || []) {
    S("line", { x1: m.l, x2: W - m.r, y1: y(r.v), y2: y(r.v), stroke: C.zero, "stroke-width": 1 }, svg);
    if (r.label) T(svg, m.l + 4, y(r.v) - 4, r.label, { "font-size": 10 });
  }
  o.cats.forEach((cat, i) => {
    const gx = m.l + i * step + (step - (k * bw + (k - 1) * 2)) / 2;
    const marks = [];
    o.series.forEach((s, j) => {
      const d = s.values[i];
      if (d == null || d.v == null) return;
      const bx = gx + j * (bw + 2);
      marks.push(S("path", { d: vbar(bx, y(0), bw, y(d.v)), fill: s.color }, svg));
      if (d.lo != null) {
        const cx = bx + bw / 2;
        S("line", { x1: cx, x2: cx, y1: y(d.lo), y2: y(d.hi), stroke: C.ink2, "stroke-width": 1 }, svg);
        S("line", { x1: cx - 3, x2: cx + 3, y1: y(d.lo), y2: y(d.lo), stroke: C.ink2, "stroke-width": 1 }, svg);
        S("line", { x1: cx - 3, x2: cx + 3, y1: y(d.hi), y2: y(d.hi), stroke: C.ink2, "stroke-width": 1 }, svg);
      }
    });
    T(svg, m.l + i * step + step / 2, H - m.b + 15, cat, { "text-anchor": "middle" });
    const hit = S("rect", { x: m.l + i * step, y: m.t, width: step, height: H - m.b - m.t, fill: "transparent" }, svg);
    const g = { classList: { add: () => marks.forEach((p) => p.classList.add("mark-hover")), remove: () => marks.forEach((p) => p.classList.remove("mark-hover")) } };
    bindTip(hit, g, () => [o.tipTitle(i), o.series.map((s) => ({ color: s.color, value: s.values[i] ? o.fmtTip(s.values[i]) : "–", label: s.name }))]);
  });
}

/* ── chart: scatter with labelled points ─────────────────────────────── */
function scatter(el, o) {
  el.replaceChildren();
  const W = widthOf(el), H = o.height || 300;
  const m = { l: 56, r: 18, t: 14, b: 40 };
  const svg = S("svg", { width: W, height: H, role: "img", "aria-label": o.aria || "" }, el);
  const xsv = o.points.map((p) => p.x), ysv = o.points.map((p) => p.y);
  const [xlo, xhi] = niceDomain(Math.min(...xsv), Math.max(...xsv), 5);
  const [ylo, yhi] = o.yZero ? niceDomain(0, Math.max(...ysv), 5) : niceDomain(Math.min(...ysv), Math.max(...ysv), 5);
  const x = lin(xlo, xhi, m.l, W - m.r), y = lin(ylo, yhi, H - m.b, m.t);
  for (const v of ticks(ylo, yhi, 5)) {
    S("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === ylo ? C.axis : C.grid }, svg);
    T(svg, m.l - 6, y(v) + 3.5, compact(v), { "text-anchor": "end" });
  }
  for (const v of ticks(xlo, xhi, Math.max(3, Math.floor(W / 100)))) {
    S("line", { x1: x(v), x2: x(v), y1: H - m.b, y2: H - m.b + 4, stroke: C.axis }, svg);
    T(svg, x(v), H - m.b + 16, o.fmtX ? o.fmtX(v) : fmt(v), { "text-anchor": "middle" });
  }
  if (o.vref != null && o.vref > xlo && o.vref < xhi)
    S("line", { x1: x(o.vref), x2: x(o.vref), y1: m.t, y2: H - m.b, stroke: C.zero }, svg);
  T(svg, (m.l + W - m.r) / 2, H - 6, o.xTitle, { "text-anchor": "middle", fill: C.ink2 });
  T(svg, m.l, m.t - 3, o.yTitle, { fill: C.ink2 });
  const placed = [];
  const free = (b) => placed.every((q) => b.x1 < q.x0 || b.x0 > q.x1 || b.y1 < q.y0 || b.y0 > q.y1);
  for (const p of o.points) {
    const c = S("circle", { cx: x(p.x), cy: y(p.y), r: o.r || 5, fill: p.color || o.color, stroke: C.surface, "stroke-width": 2, opacity: o.opacity || 1 }, svg);
    if (p.label) {
      /* first spot (right-above, right-below, left-above, left-below) that clears earlier labels */
      const lw = p.label.length * 6.8, px = x(p.x), py = y(p.y);
      const spots = [[px + 8, py - 6, "start"], [px + 8, py + 15, "start"], [px - 8, py - 6, "end"], [px - 8, py + 15, "end"]];
      const box = ([lx, ly, a]) => ({ x0: a === "end" ? lx - lw : lx, x1: a === "end" ? lx : lx + lw, y0: ly - 11, y1: ly + 2 });
      const spot = spots.find((sp) => free(box(sp))) || spots[0];
      placed.push(box(spot));
      T(svg, spot[0], spot[1], p.label, { fill: C.ink2, "font-size": 11.5, "text-anchor": spot[2] });
    }
    const hit = S("circle", { cx: x(p.x), cy: y(p.y), r: 12, fill: "transparent" }, svg);
    bindTip(hit, c, () => p.tip);
  }
}

/* ── chart: stacked horizontal bars ──────────────────────────────────── */
function stackedH(el, o) {
  el.replaceChildren();
  const rowH = 22, gap = 8;
  const labW = Math.min(170, Math.max(110, ...o.rows.map((r) => r.label.length * 6.6)));
  const W = widthOf(el), m = { l: labW, r: 64, t: 6, b: 22 };
  const H = m.t + m.b + o.rows.length * (rowH + gap);
  const svg = S("svg", { width: W, height: H, role: "img", "aria-label": o.aria || "" }, el);
  const max = Math.max(...o.rows.map((r) => r.segs.reduce((a, s) => a + s.v, 0)));
  const xmax = niceDomain(0, max, 4)[1];
  const x = lin(0, xmax, m.l, W - m.r);
  for (const v of ticks(0, xmax, 4)) {
    S("line", { x1: x(v), x2: x(v), y1: m.t, y2: H - m.b, stroke: v === 0 ? C.axis : C.grid }, svg);
    T(svg, x(v), H - m.b + 14, compact(v), { "text-anchor": "middle" });
  }
  o.rows.forEach((r, i) => {
    const y0 = m.t + i * (rowH + gap);
    T(svg, m.l - 8, y0 + rowH / 2 + 4, r.label, { "text-anchor": "end", fill: C.ink2, "font-size": 12 });
    let acc = 0;
    const segs = r.segs.filter((s) => s.v > 0);
    const marks = [];
    segs.forEach((s, j) => {
      const xa = x(acc) + (j ? 1 : 0), xb = x(acc + s.v) - (j < segs.length - 1 ? 1 : 0);
      acc += s.v;
      if (xb > xa) marks.push(S("path", { d: hbar(xa, y0, xb, rowH, j === segs.length - 1), fill: o.colors[s.key] }, svg));
    });
    T(svg, x(acc) + 6, y0 + rowH / 2 + 4, fmt(acc), { fill: C.ink2, "font-size": 11.5 });
    const hit = S("rect", { x: 0, y: y0 - gap / 2, width: W, height: rowH + gap, fill: "transparent" }, svg);
    const g = { classList: { add: () => marks.forEach((p) => p.classList.add("mark-hover")), remove: () => marks.forEach((p) => p.classList.remove("mark-hover")) } };
    bindTip(hit, g, () => [r.label, [
      { value: fmt(acc), label: "people affected, all years" },
      ...o.keys.map((k) => ({ color: o.colors[k], value: fmt((r.segs.find((s) => s.key === k) || { v: 0 }).v), label: String(k) })),
    ]]);
  });
}

/* ── segmented control ───────────────────────────────────────────────── */
function seg(el, options, value, onChange) {
  el.replaceChildren();
  el.classList.add("seg");
  el.setAttribute("role", "group");
  for (const opt of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = opt.label;
    b.setAttribute("aria-pressed", String(opt.value === value));
    b.addEventListener("click", () => {
      el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true");
      onChange(opt.value);
    });
    el.appendChild(b);
  }
}

/* ================================================================== */
/*  Page sections                                                       */
/* ================================================================== */
const $ = (id) => document.getElementById(id);
const renders = [];
function register(fn) { renders.push(fn); fn(); }

/* 1 · when */
register(() => {
  const items = D.impact_monthly.map((d) => ({
    label: MONTHS[d.month - 1], value: d.people,
    tip: [`${MONTHS[d.month - 1]} ${d.year}`, [
      { color: C.impact, value: fmt(d.people), label: "people affected" },
      { value: fmt(d.events), label: d.events === 1 ? "alert" : "alerts" },
    ]],
  }));
  const groups = YEARS.map((yr, i) => ({ label: String(yr), from: i * 12, to: i * 12 + 11, ticks: [{ i: 0, label: "J" }, { i: 6, label: "J" }] }));
  columnChart($("c-monthly"), { items, color: C.impact, groups, height: 230, aria: "People affected by month of alert, 2021 to 2025" });
});
lazyTable("t-monthly", [
  { label: "Year", get: (r) => r.year }, { label: "Month", get: (r) => MONTHS[r.month - 1] },
  { label: "Alerts", num: true, get: (r) => r.events }, { label: "People affected", num: true, get: (r) => fmt(r.people) },
], () => D.impact_monthly);

register(() => {
  const by = MONTHS.map((_, i) => {
    const rows = D.impact_monthly.filter((d) => d.month === i + 1);
    return { people: rows.reduce((a, d) => a + d.people, 0), events: rows.reduce((a, d) => a + d.events, 0) };
  });
  columnChart($("c-season"), {
    color: C.impact, height: 200, aria: "People affected by calendar month, 2021 to 2025 combined",
    items: by.map((d, i) => ({ label: MONTHS[i][0], value: d.people, tip: [MONTHS[i] + ", 2021–2025", [
      { color: C.impact, value: fmt(d.people), label: "people affected" }, { value: fmt(d.events), label: "alerts" }]] })),
  });
  const clim = (p) => D.rain_clim.filter((d) => d.product === p).sort((a, b) => a.month - b.month);
  groupedBars($("c-clim"), {
    height: 200, cats: MONTHS.map((m) => m[0]), aria: "Average monthly rainfall 2001 to 2020, ERA5, IMERG and CHIRPS",
    series: PRODUCTS.map((p) => ({ name: p, color: PRODUCT_COLOR[p], values: clim(p).map((d) => ({ v: d.mm })) })),
    fmtTick: (v) => fmt(v), tipTitle: (i) => MONTHS[i] + " average, 2001–2020", fmtTip: (d) => fmt(d.v) + " mm",
  });
});
legend($("l-clim"), productLegend());
lazyTable("t-season", [
  { label: "Month", get: (r) => MONTHS[r.month - 1] },
  { label: "People affected (2021–25)", num: true, get: (r) => fmt(r.people) },
  ...PRODUCTS.map((p) => ({ label: `${p} avg (mm)`, num: true, get: (r) => fmt(r[p]) })),
], () => MONTHS.map((_, i) => Object.assign({
  month: i + 1,
  people: D.impact_monthly.filter((d) => d.month === i + 1).reduce((a, d) => a + d.people, 0),
}, Object.fromEntries(PRODUCTS.map((p) => [p, (D.rain_clim.find((d) => d.product === p && d.month === i + 1) || {}).mm])))));

register(() => {
  columnChart($("c-annual"), {
    color: C.impact, height: 200, valueLabels: true, aria: "People affected per year",
    items: D.comp_annual.map((d) => ({ label: String(d.year), value: d.people, tip: [String(d.year), [
      { color: C.impact, value: fmt(d.people), label: "people affected" }, { value: fmt(d.events), label: "alerts" },
      { value: fmt(d.households), label: "households affected" }]] })),
  });
});

/* 2 · where */
register(() => {
  const rows = D.adm1_totals.map((a) => ({
    label: a.adm1, segs: YEARS.map((yr) => ({ key: yr, v: (D.adm1_year.find((d) => d.adm1 === a.adm1 && d.year === yr) || { people: 0 }).people })),
  }));
  stackedH($("c-adm1"), { rows, keys: YEARS, colors: YEAR_COLOR, aria: "People affected by prefecture and year" });
});
legend($("l-adm1"), YEARS.map((y) => ({ label: String(y), color: YEAR_COLOR[y] })));
lazyTable("t-adm3", [
  { label: "Prefecture", get: (r) => r.adm1 }, { label: "Sous-préfecture", get: (r) => r.adm2 },
  { label: "Commune (adm3)", get: (r) => r.adm3 }, { label: "Pcode", get: (r) => r.pcode },
  { label: "Alerts", num: true, get: (r) => r.events },
  ...YEARS.map((yr) => ({ label: String(yr), num: true, get: (r) => (r.by_year[yr] ? fmt(r.by_year[yr]) : "") })),
  { label: "Total", num: true, get: (r) => fmt(r.total) },
], () => D.adm3);

/* map */
(function map() {
  if (!window.L) { $("map").textContent = "The map library could not be loaded."; return; }
  const m = L.map("map", { scrollWheelZoom: false, zoomSnap: 0.25 });
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 13, attribution: "Tiles © Esri — Esri, HERE, Garmin, OpenStreetMap contributors",
  }).addTo(m);
  const quant = (vals, k) => {
    const s = vals.filter((v) => v > 0).sort((a, b) => a - b);
    return Array.from({ length: k - 1 }, (_, i) => s[Math.floor(((i + 1) * s.length) / k)]);
  };
  const layers = {};
  let legendCtl = null;
  const build = (key, geo, nameOf, label) => {
    const vals = geo.features.map((f) => f.properties.people_affected || 0);
    const br = quant(vals, 5);
    const cls = (v) => { let i = 0; while (i < br.length && v >= br[i]) i++; return i; };
    const lyr = L.geoJSON(geo, {
      style: (f) => {
        const v = f.properties.people_affected || 0;
        return { color: "#ffffff", weight: key === "adm1" ? 1.2 : 0.6, fillOpacity: v > 0 ? 0.85 : 0.55, fillColor: v > 0 ? MAP_RAMP[cls(v)] : "#d8e0e1" };
      },
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.on("mousemove", (e) => showTip(e.originalEvent.clientX, e.originalEvent.clientY, nameOf(p), [
          { color: C.impact, value: fmt(p.people_affected || 0), label: "people affected, 2021–2025" },
          { value: fmt(p.events || 0), label: "alerts" },
        ]));
        l.on("mouseout", hideTip);
      },
    });
    const lo = Math.min(...vals.filter((v) => v > 0));
    const edges = [lo, ...br, Math.max(...vals)];
    layers[key] = { lyr, edges, label };
  };
  build("adm3", D.geo_adm3, (p) => `${p.adm3_name} (${p.adm1_name})`, "Communes (adm3)");
  build("adm1", D.geo_adm1, (p) => p.adm1_name, "Prefectures (adm1)");
  const pts = L.layerGroup(D.events_pts.map((e) => {
    const c = L.circleMarker([e.lat, e.lon], { radius: 2.5 + Math.sqrt(e.people || 0) / 14, color: "#ffffff", weight: 1.5, fillColor: "#1f2324", fillOpacity: 0.55 });
    c.on("mousemove", (ev) => showTip(ev.originalEvent.clientX, ev.originalEvent.clientY, `${e.commune} · ${e.date}`, [
      { value: fmt(e.people), label: "people affected" }, { value: e.localities || "–", label: "" },
    ]));
    c.on("mouseout", hideTip);
    return c;
  }));
  const setLegend = (key) => {
    if (legendCtl) legendCtl.remove();
    legendCtl = L.control({ position: "bottomleft" });
    legendCtl.onAdd = () => {
      const div = L.DomUtil.create("div", "map-legend");
      const t = document.createElement("div"); t.className = "ttl"; t.textContent = "People affected, 2021–2025"; div.appendChild(t);
      const e = layers[key].edges;
      MAP_RAMP.forEach((col, i) => {
        const r = document.createElement("div"); r.className = "row";
        const sw = document.createElement("span"); sw.className = "sw"; sw.style.background = col;
        const hi = i < MAP_RAMP.length - 1 ? e[i + 1] - 1 : e[i + 1]; /* classes are [lo, next) */
        const lab = document.createElement("span"); lab.textContent = `${fmt(e[i])} – ${fmt(hi)}`;
        r.append(sw, lab); div.appendChild(r);
      });
      const r = document.createElement("div"); r.className = "row";
      const sw = document.createElement("span"); sw.className = "sw"; sw.style.background = "#d8e0e1";
      const lab = document.createElement("span"); lab.textContent = "No alert recorded";
      r.append(sw, lab); div.appendChild(r);
      return div;
    };
    legendCtl.addTo(m);
  };
  let active = "adm3";
  layers.adm3.lyr.addTo(m);
  setLegend("adm3");
  m.fitBounds(layers.adm1.lyr.getBounds(), { padding: [8, 8] });
  seg($("s-map"), [{ label: "Communes", value: "adm3" }, { label: "Prefectures", value: "adm1" }], "adm3", (v) => {
    layers[active].lyr.remove(); active = v; layers[v].lyr.addTo(m);
    if (m.hasLayer(pts)) pts.eachLayer((l) => l.bringToFront());
    setLegend(v);
  });
  seg($("s-pts"), [{ label: "Hide alerts", value: false }, { label: "Show alert locations", value: true }], false, (v) => {
    if (v) pts.addTo(m); else pts.remove();
  });
})();

/* 3 · rainfall */
register(() => {
  const series = PRODUCTS.map((p) => {
    const pts = D.rain_annual.filter((d) => d.product === p).map((d) => [d.year, d.mm]);
    const tr = D.trend_lines[p];
    return { name: p, color: PRODUCT_COLOR[p], points: pts, trend: tr ? [[tr.x0, tr.y0], [tr.x1, tr.y1]] : null };
  });
  lineChart($("c-rain-annual"), { series, band: [2021, 2025], bandLabel: "impact record", height: 280, fmtY: (v) => fmt(v) + " mm", aria: "Annual national rainfall: ERA5 and CHIRPS 1981 to 2025, IMERG 1998 to 2025" });
});
legend($("l-rain-annual"), productLegend("line"));
lazyTable("t-rain-annual", [
  { label: "Year", get: (r) => r.year },
  ...PRODUCTS.map((p) => ({ label: `${p} (mm)`, num: true, get: (r) => fmt(r[p]) })),
], () => {
  const yrs = [...new Set(D.rain_annual.map((d) => d.year))].sort();
  const g = (p, y) => (D.rain_annual.find((d) => d.product === p && d.year === y) || {}).mm;
  return yrs.map((y) => Object.assign({ year: y }, Object.fromEntries(PRODUCTS.map((p) => [p, g(p, y)]))));
});

register(() => {
  const tr = (p) => D.trends.filter((d) => d.product === p && d.period === "1998-2025" && d.month > 0).sort((a, b) => a.month - b.month);
  groupedBars($("c-trend"), {
    height: 240, cats: MONTHS.map((m) => m[0]), aria: "Linear rainfall trend by calendar month, 1998 to 2025",
    series: PRODUCTS.map((p) => ({ name: p, color: PRODUCT_COLOR[p], values: tr(p).map((d) => ({ v: d.slope, lo: d.slope - d.ci, hi: d.slope + d.ci, p: d.p })) })),
    fmtTick: (v) => fmtSigned(v), tipTitle: (i) => MONTHS[i] + " trend, 1998–2025",
    fmtTip: (d) => `${fmtSigned(d.v, 1)} ± ${(d.hi - d.v).toFixed(1)} mm/decade`,
  });
});
legend($("l-trend"), productLegend());
lazyTable("t-trend", [
  { label: "Product", get: (r) => r.product }, { label: "Period", get: (r) => r.period },
  { label: "Month", get: (r) => (r.month ? MONTHS[r.month - 1] : "Annual") },
  { label: "Trend (mm/decade)", num: true, get: (r) => fmtSigned(r.slope, 1) },
  { label: "95% CI ±", num: true, get: (r) => r.ci.toFixed(1) }, { label: "p", num: true, get: (r) => r.p.toFixed(3) },
], () => D.trends);

/* 4 · impact vs rainfall */
const BASIS = [{ label: "Rainfall", value: "raw" }, { label: "Detrended", value: "detrended" }];
let annualProduct = "ERA5", annualBasis = "raw";
function drawAnnualScatter() {
  const p = annualProduct, detr = annualBasis === "detrended";
  const key = p.toLowerCase() + (detr ? "_detr" : "");
  const what = detr ? `${p} annual rainfall, detrended` : `${p} annual rainfall`;
  const pts = D.comp_annual.map((d) => ({
    x: d[key], y: d.people, label: String(d.year), color: PRODUCT_COLOR[p],
    tip: [String(d.year), [{ color: PRODUCT_COLOR[p], value: (detr ? fmtSigned(d[key]) : fmt(d[key])) + " mm", label: what }, { value: fmt(d.people), label: "people affected" }]],
  }));
  scatter($("c-annual-scatter"), {
    points: pts, yZero: true, height: 300, vref: detr ? 0 : null, fmtX: detr ? (v) => fmtSigned(v) : fmt,
    xTitle: detr ? `${p} annual national rainfall minus its 1998–2025 trend (mm)` : `${p} annual national rainfall (mm)`,
    yTitle: "People affected", aria: `People affected per year against ${what}`,
  });
  const c = D.corr.find((d) => d.product === p && d.basis === annualBasis && d.month === 12);
  $("r-annual").textContent = `${p}${detr ? ", detrended" : ""}: r = ${fmtR(c.r)} (Spearman ρ = ${fmtR(c.rho, 1)}), n = ${c.n} years`;
}
seg($("s-annual"), productSeg, "ERA5", (v) => { annualProduct = v; drawAnnualScatter(); });
seg($("s-annual-b"), BASIS, "raw", (v) => { annualBasis = v; drawAnnualScatter(); });
register(drawAnnualScatter);
lazyTable("t-annual", [
  { label: "Year", get: (r) => r.year }, { label: "Alerts", num: true, get: (r) => r.events },
  { label: "People affected", num: true, get: (r) => fmt(r.people) },
  ...PRODUCTS.map((p) => ({ label: `${p} (mm)`, num: true, get: (r) => fmt(r[p.toLowerCase()]) })),
  ...PRODUCTS.map((p) => ({ label: `${p} detrended (mm)`, num: true, get: (r) => fmtSigned(r[p.toLowerCase() + "_detr"]) })),
], () => D.comp_annual);

let corrBasis = "raw";
function drawCorr() {
  const cr = (p) => D.corr.filter((d) => d.product === p && d.basis === corrBasis).sort((a, b) => a.month - b.month);
  groupedBars($("c-corr"), {
    height: 240, cats: MONTHS.map((m) => m[0]), yDomain: [-1, 1], aria: "Correlation of cumulative rainfall since January with annual people affected",
    series: PRODUCTS.map((p) => ({ name: p, color: PRODUCT_COLOR[p], values: cr(p).map((d) => ({ v: d.r, p: d.p })) })),
    refs: [{ v: D.summary.r_crit, label: `p < 0.05 at n = 5 (r = ${D.summary.r_crit.toFixed(2)})` }, { v: -D.summary.r_crit }],
    fmtTick: (v) => v.toFixed(1), tipTitle: (i) => `Rain Jan–${MONTHS[i]}${corrBasis === "detrended" ? " (detrended)" : ""} vs people affected that year`,
    fmtTip: (d) => `r = ${fmtR(d.v)} (p = ${d.p.toFixed(2)})`,
  });
}
seg($("s-corr"), BASIS, "raw", (v) => { corrBasis = v; drawCorr(); });
register(drawCorr);
legend($("l-corr"), productLegend());
lazyTable("t-corr", [
  { label: "Product", get: (r) => r.product }, { label: "Rainfall", get: (r) => r.basis },
  { label: "Rain from Jan through", get: (r) => MONTHS[r.month - 1] },
  { label: "Pearson r", num: true, get: (r) => fmtR(r.r) }, { label: "p", num: true, get: (r) => r.p.toFixed(3) },
  { label: "Spearman ρ", num: true, get: (r) => fmtR(r.rho) }, { label: "Years", num: true, get: (r) => r.n },
], () => D.corr);

const MVAR = [{ label: "Rainfall", value: "total" }, { label: "Anomaly", value: "anom" }, { label: "Detrended", value: "detr" }];
const MVAR_TEXT = { total: "rainfall", anom: "anomaly vs 2001–2020 average", detr: "anomaly vs 1998–2025 trend" };
let monthlyProduct = "ERA5", monthlyVar = "total";
function drawMonthlyScatter() {
  const key = monthlyProduct.toLowerCase() + (monthlyVar === "total" ? "" : "_" + monthlyVar);
  const signed = monthlyVar !== "total";
  const pts = D.comp_monthly.filter((d) => d[key] != null).map((d) => ({
    x: d[key], y: d.people, color: PRODUCT_COLOR[monthlyProduct],
    tip: [`${MONTHS[d.month - 1]} ${d.year}`, [
      { color: PRODUCT_COLOR[monthlyProduct], value: (signed ? fmtSigned(d[key]) : fmt(d[key])) + " mm", label: `${monthlyProduct} ${MVAR_TEXT[monthlyVar]}` },
      { value: fmt(d.people), label: "people affected" }]],
  }));
  scatter($("c-monthly-scatter"), {
    points: pts, yZero: true, r: 4.5, opacity: 0.85, vref: signed ? 0 : null, height: 300,
    xTitle: `${monthlyProduct} monthly national ${MVAR_TEXT[monthlyVar]} (mm)`, yTitle: "People affected that month",
    fmtX: signed ? (v) => fmtSigned(v) : fmt, aria: "People affected per month against monthly rainfall",
  });
  const s = D.monthly_corr.find((d) => d.product === monthlyProduct && d.var === monthlyVar);
  $("r-monthly").textContent = `Spearman ρ = ${fmtR(s.rho)}, Pearson r = ${fmtR(s.r)}, n = ${s.n} months`;
}
seg($("s-monthly-p"), productSeg, "ERA5", (v) => { monthlyProduct = v; drawMonthlyScatter(); });
seg($("s-monthly-v"), MVAR, "total", (v) => { monthlyVar = v; drawMonthlyScatter(); });
register(drawMonthlyScatter);
lazyTable("t-monthly-comp", [
  { label: "Year", get: (r) => r.year }, { label: "Month", get: (r) => MONTHS[r.month - 1] },
  { label: "People affected", num: true, get: (r) => fmt(r.people) },
  ...PRODUCTS.flatMap((p) => {
    const k = p.toLowerCase();
    return [
      { label: `${p} (mm)`, num: true, get: (r) => fmt(r[k]) },
      { label: `${p} anomaly`, num: true, get: (r) => (r[k + "_anom"] == null ? "–" : fmtSigned(r[k + "_anom"])) },
      { label: `${p} detrended`, num: true, get: (r) => (r[k + "_detr"] == null ? "–" : fmtSigned(r[k + "_detr"])) },
    ];
  }),
], () => D.comp_monthly);

/* ── heading anchors: a "#" link on every section heading and figure title ── */
(function anchors() {
  const add = (host, id, label) => {
    if (!host || !id) return;
    const a = document.createElement("a");
    a.className = "anchor";
    a.href = "#" + id;
    a.textContent = "#";
    a.setAttribute("aria-label", "Link to " + label);
    a.addEventListener("click", () => {
      try { navigator.clipboard.writeText(location.href.split("#")[0] + "#" + id); } catch (e) { /* clipboard is optional */ }
    });
    host.appendChild(a);
  };
  document.querySelectorAll("section[id] > h2").forEach((h) => add(h, h.parentElement.id, h.textContent));
  document.querySelectorAll(".fig[id] .fig-title").forEach((t) => add(t, t.closest(".fig").id, t.textContent));
  document.querySelectorAll("h3[id]").forEach((h) => add(h, h.id, h.textContent));
  /* staticrypt writes the page after load, so the browser never scrolls to the hash
     itself. Jump now (the charts above are already drawn), then once more after fonts
     and the page finish loading, unless the reader has scrolled in the meantime. */
  const target = () => location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  const go = () => { const el = target(); if (el) el.scrollIntoView({ behavior: "instant", block: "start" }); };
  go();
  const settled = scrollY;
  const again = () => { if (Math.abs(scrollY - settled) < 2) go(); };
  if (document.fonts) document.fonts.ready.then(again);
  if (document.readyState !== "complete") addEventListener("load", again, { once: true });
  addEventListener("hashchange", go);
})();

/* re-render on width change */
let rt = null, lastW = innerWidth;
addEventListener("resize", () => {
  if (Math.abs(innerWidth - lastW) < 8) return;
  lastW = innerWidth;
  clearTimeout(rt);
  rt = setTimeout(() => renders.forEach((f) => f()), 150);
});

/* ── downloads: files are embedded (gzip+base64) so they stay inside the encrypted page ── */
(function downloads() {
  const files = JSON.parse(document.getElementById("page-files").textContent);
  const b64ToBytes = (b64) => { const bin = atob(b64); const out = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out; };
  async function bytesOf(f) {
    const raw = b64ToBytes(f.b64);
    if (!f.gz) return raw;
    const ds = new Blob([raw]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Uint8Array(await new Response(ds).arrayBuffer());
  }
  function save(name, bytes, mime) {
    const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  /* minimal STORE-only zip writer (no compression; CRC-32 per entry) */
  const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  const crc32 = (b) => { let c = 0xffffffff; for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  /* DOS date for every entry: today (time left at 00:00) */
  const now = new Date();
  const DOS_DATE = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  function zip(entries) {
    const enc = new TextEncoder(), parts = [], central = [];
    let off = 0;
    for (const e of entries) {
      const name = enc.encode(e.name), crc = crc32(e.bytes), size = e.bytes.length;
      const h = new DataView(new ArrayBuffer(30));
      h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true);
      h.setUint16(12, DOS_DATE, true);
      h.setUint32(14, crc, true); h.setUint32(18, size, true); h.setUint32(22, size, true); h.setUint16(26, name.length, true);
      parts.push(new Uint8Array(h.buffer), name, e.bytes);
      const c = new DataView(new ArrayBuffer(46));
      c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
      c.setUint16(14, DOS_DATE, true);
      c.setUint32(16, crc, true); c.setUint32(20, size, true); c.setUint32(24, size, true); c.setUint16(28, name.length, true);
      c.setUint32(42, off, true);
      central.push(new Uint8Array(c.buffer), name);
      off += 30 + name.length + size;
    }
    const csize = central.reduce((a, p) => a + p.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, entries.length, true); end.setUint16(10, entries.length, true);
    end.setUint32(12, csize, true); end.setUint32(16, off, true);
    return new Blob([...parts, ...central, new Uint8Array(end.buffer)], { type: "application/zip" });
  }
  const sizeLabel = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1e3)) + " KB");
  for (const group of ["raw", "processed"]) {
    const host = $("dl-" + group);
    for (const f of files.filter((x) => x.group === group)) {
      const row = document.createElement("div"); row.className = "dl";
      const meta = document.createElement("div"); meta.className = "meta";
      const nm = document.createElement("div"); nm.className = "name"; nm.textContent = f.name;
      const ds = document.createElement("div"); ds.className = "desc"; ds.textContent = `${f.desc} · ${sizeLabel(f.size)}`;
      meta.append(nm, ds);
      const b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = "Download";
      b.setAttribute("aria-label", "Download " + f.name);
      b.addEventListener("click", async () => save(f.name, await bytesOf(f), f.mime));
      row.append(meta, b);
      host.appendChild(row);
    }
    const all = $("dl-all-" + group);
    all.addEventListener("click", async () => {
      const entries = [];
      for (const f of files.filter((x) => x.group === group)) entries.push({ name: f.name, bytes: await bytesOf(f) });
      const url = URL.createObjectURL(zip(entries));
      const a = document.createElement("a"); a.href = url; a.download = `caf_floods_${group}_data.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    });
  }
})();
