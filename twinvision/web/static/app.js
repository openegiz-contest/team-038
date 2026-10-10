"use strict";
// TwinVision Copper Pit map. Data: /api/static (twin attributes), /api/stream (twin features
// from Ditto, once per poll), /api/trend (InfluxDB). World coordinates are metres, y north;
// SVG y points down, so every point is drawn at (x, -y).

const SVGNS = "http://www.w3.org/2000/svg";
const $ = (s, root = document) => root.querySelector(s);
const fmt = (v, d = 0) => (typeof v === "number" && isFinite(v))
  ? v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : "–";
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function svg(tag, attrs = {}, parent = null) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function html(tag, attrs = {}, text = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

// ------------------------------------------------------------------ colours
const GRADE_STOPS = [[0, "#3b4250"], [0.249, "#4a5262"], [0.25, "#e8d44d"], [0.55, "#f59e0b"],
                     [0.85, "#ef4444"], [1.4, "#c026d3"], [2.2, "#7c3aed"]];
function hex(c) { return [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16)); }
function gradeColor(g) {
  if (!(g > 0)) return GRADE_STOPS[0][1];
  for (let i = 1; i < GRADE_STOPS.length; i++) {
    const [g1, c1] = GRADE_STOPS[i];
    if (g <= g1) {
      const [g0, c0] = GRADE_STOPS[i - 1];
      const t = (g - g0) / (g1 - g0 || 1), a = hex(c0), b = hex(c1);
      return `rgb(${a.map((x, j) => Math.round(x + (b[j] - x) * t)).join(",")})`;
    }
  }
  return GRADE_STOPS[GRADE_STOPS.length - 1][1];
}
const TRUCK_GROUPS = [
  ["Hauling (loaded)", "#3b82f6", ["hauling"]],
  ["Returning empty", "#7dd3fc", ["returning"]],
  ["Loading", "#22c55e", ["loading"]],
  ["Queued / waiting", "#f59e0b", ["queued_at_shovel", "queued_at_crusher", "queued_at_rom", "queued_at_dump",
                                    "queued_at_fuel_bay", "queued_at_workshop", "waiting", "queued"]],
  ["Tipping", "#a78bfa", ["dumping_crusher", "dumping_rom", "dumping_waste", "dumping"]],
  ["Fuel", "#2dd4bf", ["to_fuel_bay", "refueling"]],
  ["Maintenance", "#f472b6", ["to_workshop", "maintenance"]],
  ["Breakdown / blocked", "#ef4444", ["breakdown", "waiting_road"]],
  ["Parked", "#6b7280", ["parked", "to_park"]],
];
const TRUCK_COLOR = {};
TRUCK_GROUPS.forEach(([, c, states]) => states.forEach(s => (TRUCK_COLOR[s] = c)));
const MACHINE_COLOR = { loading: "#22c55e", waiting_for_truck: "#f59e0b", crushing: "#22c55e", starved: "#f59e0b",
  blocked_cos_full: "#fb923c", failed: "#ef4444", breakdown: "#ef4444", maintenance: "#f472b6" };
const healthColor = h => h >= 70 ? "#22c55e" : h >= 40 ? "#f59e0b" : "#ef4444";

const SCN_TEXT = {
  crusher_failure: "Primary crusher stops. Ore goes to the ROM pad, the coarse ore stockpile drains, then the plant starves.",
  road_closure: "Closes a road. The dispatcher re-routes trucks over the remaining network: longer cycles, more diesel, fewer tonnes.",
  excavator_breakdown: "Takes a shovel out of service. Its trucks are re-assigned to the other faces.",
  hardness_spike: "Raises the Bond work index at a face: slower digging, lower crusher capacity, higher kWh/t.",
  low_grade_zone: "Lowers the copper grade at a face. Feed grade and copper output fall; the dispatcher re-blends.",
  fuel_shortage: "Cuts diesel deliveries and the fuel bay stock. The dispatcher parks trucks and stops waste stripping to protect ore.",
  energy_cap: "Caps site electrical demand. The concentrator slows to stay under the cap; the crusher keeps filling the stockpile.",
};

// ------------------------------------------------------------------ state
let STATIC = null, S = null, prevS = null;
const A = () => STATIC.attributes;
const roads = {};               // sid -> {pts:[[x,y]], cum:[], len, el, hit}
const blocksEl = {};            // cluster -> [polygon]
const icons = {};               // name -> {g, ...}
const trucks = {};              // "live:truck-01" -> anim state
const conv = {};                // conveyor paths
let view = { x: -2500, y: -1050, w: 4500, h: 2100 }, selectedKpi = "plant_feed_tph";
const VIEWS = {};
const map = $("#map");
const layers = {};

// ------------------------------------------------------------------ geometry helpers
function prepRoad(sid, geom) {
  const pts = geom.map(p => [p[0], p[1]]);
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  return { sid, pts, cum, len: cum[cum.length - 1] };
}
function project(r, x, y) {
  let best = [Infinity, 0];
  for (let i = 1; i < r.pts.length; i++) {
    const [ax, ay] = r.pts[i - 1], [bx, by] = r.pts[i];
    const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy || 1;
    const t = clamp(((x - ax) * dx + (y - ay) * dy) / L2, 0, 1);
    const d = Math.hypot(ax + t * dx - x, ay + t * dy - y);
    if (d < best[0]) best = [d, r.cum[i - 1] + t * Math.sqrt(L2)];
  }
  return best;
}
function pointAt(r, s) {
  s = clamp(s, 0, r.len);
  for (let i = 1; i < r.pts.length; i++) {
    if (s <= r.cum[i] || i === r.pts.length - 1) {
      const t = (s - r.cum[i - 1]) / ((r.cum[i] - r.cum[i - 1]) || 1);
      const [ax, ay] = r.pts[i - 1], [bx, by] = r.pts[i];
      return [ax + (bx - ax) * t, ay + (by - ay) * t];
    }
  }
  return r.pts[0];
}

// ------------------------------------------------------------------ view / zoom
function iconScale() { return view.w / Math.max(map.clientWidth, 1); }
function applyView() {
  map.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  const k = iconScale();
  for (const ic of Object.values(icons)) ic.g.setAttribute("transform", `translate(${ic.x},${-ic.y}) scale(${k})`);
}
function fitView(b) {
  const W = map.clientWidth || 1000, H = map.clientHeight || 600, ar = W / H;
  let w = b.w, h = b.h;
  if (w / h < ar) w = h * ar; else h = w / ar;
  view = { x: b.x + b.w / 2 - w / 2, y: b.y + b.h / 2 - h / 2, w, h };
  applyView();
}
function setupZoom() {
  map.addEventListener("wheel", ev => {
    ev.preventDefault();
    const r = map.getBoundingClientRect();
    const mx = view.x + (ev.clientX - r.left) / r.width * view.w, my = view.y + (ev.clientY - r.top) / r.height * view.h;
    const f = Math.exp(clamp(ev.deltaY, -100, 100) * 0.0015);
    const w = clamp(view.w * f, 150, 9000), h = view.h * (w / view.w);
    view = { x: mx - (mx - view.x) * (w / view.w), y: my - (my - view.y) * (h / view.h), w, h };
    applyView();
  }, { passive: false });
  let drag = null;
  map.addEventListener("pointerdown", ev => { drag = { x: ev.clientX, y: ev.clientY, v: { ...view }, moved: false }; });
  window.addEventListener("pointermove", ev => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; map.classList.add("drag"); }
    const k = view.w / map.clientWidth;
    view = { ...view, x: drag.v.x - dx * k, y: drag.v.y - dy * k };
    applyView();
  });
  window.addEventListener("pointerup", () => { if (drag && drag.moved) map.dataset.dragged = "1"; drag = null; map.classList.remove("drag"); });
  map.addEventListener("click", ev => { if (map.dataset.dragged) { delete map.dataset.dragged; ev.stopPropagation(); } }, true);
  window.addEventListener("resize", () => applyView());
  document.querySelectorAll("#views button").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll("#views button").forEach(x => x.classList.toggle("on", x === b));
    fitView(VIEWS[b.dataset.view]);
  }));
}

// ------------------------------------------------------------------ static drawing
function drawStatic() {
  const at = A();
  map.innerHTML = "";
  const defs = svg("defs", {}, map);
  const pat = svg("pattern", { id: "hatch", width: 8, height: 8, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
  svg("rect", { width: 8, height: 8, fill: "rgba(249,115,22,.08)" }, pat);
  svg("line", { x1: 0, y1: 0, x2: 0, y2: 8, stroke: "rgba(249,115,22,.55)", "stroke-width": 2 }, pat);
  for (const n of ["terrain", "pit", "blocks", "zones", "roads", "conv", "fac", "ghosts", "equip", "trucks"]) {
    layers[n] = svg("g", { class: n }, map);
  }
  // pit benches, rim to floor
  const benches = [...(at.pit?.benches || [])].sort((a, b) => b.z - a.z);
  const zs = benches.map(b => b.z), zmax = Math.max(...zs), zmin = Math.min(...zs);
  svg("ellipse", { cx: 0, cy: 0, rx: (at.pit?.rim?.rx || 700) + 60, ry: (at.pit?.rim?.ry || 450) + 60, fill: "#1e1b17", stroke: "#2c2722" }, layers.pit);
  for (const b of benches) {
    const t = (zmax - b.z) / ((zmax - zmin) || 1);
    const c = Math.round(52 - 30 * t), c2 = Math.round(45 - 27 * t);
    svg("ellipse", { cx: 0, cy: 0, rx: b.rx, ry: b.ry, fill: `rgb(${c},${c2},${Math.round(c2 * 0.85)})`, stroke: "#4a4136", "stroke-width": 0.8, "vector-effect": "non-scaling-stroke" }, layers.pit);
  }
  label(layers.pit, 0, -(at.pit?.rim?.ry || 450) - 40, `Main pit · floor ${at.pit?.floor_z ?? ""} m`, "lbl dim");

  // waste dump footprint and workshop pad
  const fac = at.mine?.facilities || {};
  if (fac["waste-dump"]) {
    const { x, y } = fac["waste-dump"];
    svg("path", { d: blob(x, y, 260, 170), fill: "#2a2f37", stroke: "#3d4450", "vector-effect": "non-scaling-stroke" }, layers.terrain);
  }
  if (fac["stockpile-rom"]) { const { x, y } = fac["stockpile-rom"]; svg("path", { d: blob(x, y, 120, 70), fill: "#2b2620", stroke: "#4a4136", "vector-effect": "non-scaling-stroke" }, layers.terrain); }

  // block clusters
  for (const [name, a] of Object.entries(at)) {
    if (!name.startsWith("blocks-") || !a.blocks) continue;
    const [ux, uy, vx, vy] = a.axes, [dx, dy] = a.block_size_m;
    const g = svg("g", { class: "ico", "data-twin": name }, layers.blocks);
    blocksEl[name] = a.blocks.map(([x, y, grade, bwi, t], i) => {
      const hu = dy / 2, hv = dx / 2;
      const pts = [[1, 1], [1, -1], [-1, -1], [-1, 1]].map(([su, sv]) => `${x + su * hu * ux + sv * hv * vx},${-(y + su * hu * uy + sv * hv * vy)}`);
      const p = svg("polygon", { points: pts.join(" "), class: "block", fill: gradeColor(grade) }, g);
      const tt = svg("title", {}, p); tt.textContent = `${name} block ${i}: ${grade.toFixed(2)} % Cu, BWi ${bwi.toFixed(1)} kWh/t, ${fmt(t)} t`;
      return p;
    });
    const c = a.blocks[Math.floor(a.blocks.length / 2)];
    a._center = [c[0], c[1]];
  }

  // roads
  for (const [name, a] of Object.entries(at)) {
    if (!name.startsWith("road-") || !a.geometry) continue;
    const sid = name.slice(5), r = prepRoad(sid, a.geometry);
    const d = "M" + r.pts.map(p => `${p[0]},${-p[1]}`).join("L");
    r.el = svg("path", { d, class: "road", "stroke-width": 5 }, layers.roads);
    svg("path", { d, class: "roadc", "stroke-width": 1 }, layers.roads);
    r.hit = svg("path", { d, class: "roadhit", "data-twin": name }, layers.roads);
    const t = svg("title", {}, r.hit);
    t.textContent = `${a.name}\n${fmt(a.length_m)} m, grade ${fmt(a.grade_pct, 1)} %, limit ${a.speed_limit_kmh} km/h`;
    roads[sid] = r;
  }

  // conveyors crusher -> COS -> plant
  const P = k => fac[k] ? [fac[k].x, fac[k].y] : null;
  const cr = P("crusher"), cos = P("stockpile-cos"), pl = P("plant-feed");
  if (cr && cos) conv.c1 = svg("path", { d: `M${cr[0]},${-cr[1]}L${cos[0]},${-cos[1]}`, class: "conv" }, layers.conv);
  if (cos && pl) conv.c2 = svg("path", { d: `M${cos[0]},${-cos[1]}L${pl[0]},${-pl[1]}`, class: "conv" }, layers.conv);

  // facility icons (px-sized, scaled with the view)
  facility("crusher-01", fac.crusher, g => {
    svg("rect", { x: -13, y: -13, width: 26, height: 26, rx: 4, class: "body", fill: "#22c55e", stroke: "#0d1117", "stroke-width": 2 }, g);
    svg("path", { d: "M-7,-6 L7,-6 L3,7 L-3,7Z", fill: "#0d1117", opacity: .55 }, g);
  });
  facility("stockpile-rom", fac["stockpile-rom"], g => pileIcon(g, 34, 18));
  facility("stockpile-cos", fac["stockpile-cos"], g => pileIcon(g, 30, 22));
  facility("plant-feed", fac["plant-feed"], g => {
    svg("rect", { x: -20, y: -14, width: 40, height: 28, rx: 3, fill: "#334155", stroke: "#64748b" }, g);
    svg("rect", { x: -20, y: 10, width: 40, height: 4, class: "body", fill: "#22c55e" }, g);
    svg("circle", { cx: -6, cy: -2, r: 7, fill: "none", stroke: "#94a3b8", "stroke-width": 2 }, g);
    svg("circle", { cx: 9, cy: -2, r: 4, fill: "none", stroke: "#94a3b8", "stroke-width": 2 }, g);
  });
  facility("waste-dump", fac["waste-dump"], g => svg("path", { d: "M-18,10 L-8,-8 L4,-2 L18,10Z", fill: "#525a67", stroke: "#7b8494" }, g));
  facility("fuel-bay", fac.workshop, g => {
    svg("rect", { x: -18, y: -12, width: 22, height: 24, rx: 3, fill: "#334155", stroke: "#64748b" }, g);
    svg("text", { x: -7, y: 4, "text-anchor": "middle", "font-size": 9, fill: "#cbd5e1" }, g).textContent = "WS";
    svg("rect", { x: 8, y: -12, width: 9, height: 24, rx: 2, fill: "#0c1015", stroke: "#2dd4bf" }, g);
    svg("rect", { x: 9, y: -11, width: 7, height: 22, class: "level", fill: "#2dd4bf" }, g);
  });

  // excavators
  for (const [name, a] of Object.entries(at)) {
    if (!name.startsWith("ex-") || !a.location) continue;
    const g = svg("g", { class: "ico", "data-twin": name }, layers.equip);
    svg("circle", { r: 15, class: "down-ring", style: "display:none" }, g);
    svg("path", { d: "M0,-11 L11,0 L0,11 L-11,0Z", class: "body", fill: "#22c55e", stroke: "#0d1117", "stroke-width": 2 }, g);
    svg("circle", { r: 13.5, class: "hring", fill: "none", stroke: "#22c55e", "stroke-width": 2, "stroke-dasharray": "0 100", transform: "rotate(-90)" }, g);
    svg("text", { y: 3.5, "text-anchor": "middle", "font-size": 8.5, "font-weight": 700, fill: "#0d1117" }, g).textContent = name.slice(-1);
    const [l1, l2] = iconLabels(name, g, [18, -2, "start"]);
    icons[name] = { g, x: a.location.x, y: a.location.y, l1, l2 };
  }

  // trucks (live and baseline ghosts)
  for (const [name] of Object.entries(at)) {
    if (!name.startsWith("truck-")) continue;
    for (const copy of ["live", "base"]) {
      const g = svg("g", { class: copy === "live" ? "ico" : "ghost", "data-twin": name }, copy === "live" ? layers.trucks : layers.ghosts);
      if (copy === "live") {
        svg("circle", { r: 11, class: "down-ring", style: "display:none" }, g);
        svg("circle", { r: 7, class: "body", fill: "#7dd3fc", stroke: "#0d1117", "stroke-width": 1.5 }, g);
        svg("circle", { r: 3, class: "load", fill: "none" }, g);
        svg("text", { x: 9, y: -6, class: "lbl dim" }, g).textContent = name.slice(-2);
      } else {
        svg("circle", { r: 6, fill: "none", stroke: "#cbd5e1", "stroke-width": 1.5, "stroke-dasharray": "2 2" }, g);
      }
      const ic = { g, x: 0, y: 0, copy };
      icons[`${copy}:${name}`] = ic;
      trucks[`${copy}:${name}`] = { ic, from: null, to: null, t0: 0, dur: 1 };
    }
  }

  // bounds and preset views
  const xs = [], ys = [];
  Object.values(roads).forEach(r => r.pts.forEach(p => { xs.push(p[0]); ys.push(-p[1]); }));
  Object.values(fac).forEach(f => { xs.push(f.x); ys.push(-f.y); });
  const minx = Math.min(...xs) - 80, maxx = Math.max(...xs) + 280, miny = Math.min(...ys) - 120, maxy = Math.max(...ys) + 90;
  VIEWS.mine = { x: minx, y: miny, w: maxx - minx, h: maxy - miny };
  VIEWS.pit = { x: -820, y: -560, w: 1640, h: 1120 };
  const pf = ["crusher", "stockpile-rom", "stockpile-cos", "plant-feed"].map(k => fac[k]).filter(Boolean);
  const px = pf.map(f => f.x), py = pf.map(f => -f.y);
  VIEWS.plant = { x: Math.min(...px) - 380, y: Math.min(...py) - 220, w: Math.max(...px) - Math.min(...px) + 560, h: Math.max(...py) - Math.min(...py) + 420 };
  const q = new URLSearchParams(location.search);
  const v0 = VIEWS[q.get("view")] ? q.get("view") : "mine";
  document.querySelectorAll("#views button").forEach(x => x.classList.toggle("on", x.dataset.view === v0));
  if (q.get("ghost") === "1") $("#optGhost").checked = true;
  fitView(VIEWS[v0]);

  map.addEventListener("click", ev => {
    const t = ev.target.closest("[data-twin]");
    if (t) inspect(t.dataset.twin);
  });
  drawLegend();
}
function blob(x, y, rx, ry) {
  const n = 14, pts = [];
  for (let i = 0; i < n; i++) {
    const a = i / n * 2 * Math.PI, k = 0.82 + 0.18 * Math.sin(i * 2.3 + x);
    pts.push(`${x + Math.cos(a) * rx * k},${-(y + Math.sin(a) * ry * k)}`);
  }
  return "M" + pts.join("L") + "Z";
}
function label(parent, x, y, text, cls = "lbl") {
  const t = svg("text", { x, y: -y, class: cls, "text-anchor": "middle" }, parent);
  t.textContent = text;
  return t;
}
const LABEL_AT = {   // label anchor per icon: [dx, dy, text-anchor]
  "crusher-01": [-20, -22, "end"], "stockpile-cos": [0, -36, "middle"], "plant-feed": [26, -4, "start"],
  "stockpile-rom": [0, 30, "middle"], "fuel-bay": [0, -34, "middle"], "waste-dump": [0, 28, "middle"],
  "ex-01": [-18, -4, "end"], "ex-02": [18, -4, "start"], "ex-03": [18, 8, "start"],
};
function iconLabels(name, g, def) {
  const [dx, dy, anchor] = LABEL_AT[name] || def;
  const l1 = svg("text", { x: dx, y: dy, class: "lbl big", "text-anchor": anchor }, g);
  const l2 = svg("text", { x: dx, y: dy + 12, class: "lbl dim", "text-anchor": anchor }, g);
  return [l1, l2];
}
function facility(name, pos, draw) {
  if (!pos) return;
  const g = svg("g", { class: "ico", "data-twin": name }, layers.fac);
  svg("circle", { r: 24, class: "down-ring", style: "display:none" }, g);
  draw(g);
  const [l1, l2] = iconLabels(name, g, [0, 32, "middle"]);
  icons[name] = { g, x: pos.x, y: pos.y, l1, l2 };
}
function pileIcon(g, w, h) {
  const id = "clip" + Math.random().toString(36).slice(2);
  const cp = svg("clipPath", { id }, g);
  svg("path", { d: `M${-w / 2},${h / 2} Q0,${-h * 1.1} ${w / 2},${h / 2}Z` }, cp);
  svg("path", { d: `M${-w / 2},${h / 2} Q0,${-h * 1.1} ${w / 2},${h / 2}Z`, fill: "#1c2128", stroke: "#7b6a55", "stroke-width": 1.5 }, g);
  svg("rect", { x: -w / 2, y: h / 2, width: w, height: 0, class: "level", fill: "#b08350", "clip-path": `url(#${id})`, "data-h": h * 1.2, "data-base": h / 2 }, g);
}
function drawLegend() {
  const L = $("#legend");
  L.innerHTML = "<b>Trucks</b>";
  for (const [lbl, c] of TRUCK_GROUPS) L.insertAdjacentHTML("beforeend", `<div class="li"><i style="background:${c}"></i>${lbl}</div>`);
  const grad = GRADE_STOPS.slice(1).map(([g, c]) => `${c} ${clamp(g / 1.6 * 100, 0, 100)}%`).join(",");
  L.insertAdjacentHTML("beforeend", `<div style="margin-top:5px"><b>Block / load grade, % Cu</b></div>
    <div class="gr" style="background:linear-gradient(90deg,#4a5262 0%,#4a5262 15%,${grad})"></div>
    <div class="gl"><span>waste</span><span>0.55</span><span>0.85</span><span>1.6</span></div>
    <div class="li" style="margin-top:4px"><i style="background:#ef4444;border-radius:2px"></i>failed / closed</div>
    <div class="li"><i style="border:1.5px dashed #cbd5e1;background:none"></i>baseline truck (toggle)</div>`);
}

// ------------------------------------------------------------------ dynamic update
function V(name, f, copy = "live") { const t = S?.[copy]?.[name]; return t ? t[f] : undefined; }

function update() {
  if (!S || !STATIC) return;
  const now = performance.now();
  // roads
  for (const [sid, r] of Object.entries(roads)) {
    const st = V(`road-${sid}`, "status"), n = V(`road-${sid}`, "trucks_on") || 0;
    r.el.classList.toggle("closed", st === "closed");
    r.el.classList.toggle("busy", n > 0);
  }
  // blocks
  for (const [name, els] of Object.entries(blocksEl)) {
    const k = V(name, "current_block") ?? 0;
    els.forEach((p, i) => { p.classList.toggle("mined", i < k); p.classList.toggle("cur", i === k); });
  }
  drawZones();
  // excavators
  for (const name of Object.keys(icons).filter(n => n.startsWith("ex-"))) {
    const ic = icons[name], st = V(name, "state"), h = V(name, "health_pct") ?? 100;
    ic.g.querySelector(".body").setAttribute("fill", MACHINE_COLOR[st] || "#94a3b8");
    ic.g.querySelector(".down-ring").style.display = ["failed", "breakdown", "maintenance"].includes(st) ? "" : "none";
    const ring = ic.g.querySelector(".hring");
    ring.setAttribute("stroke", healthColor(h));
    ring.setAttribute("stroke-dasharray", `${(h / 100) * 84.8} 100`);
    ic.l1.textContent = `${name.toUpperCase()}  ${fmt(V(name, "tph"))} t/h`;
    ic.l2.textContent = `${st?.replaceAll("_", " ")} · face ${fmt(V(name, "face_grade"), 2)} % Cu · queue ${V(name, "queue_len") ?? 0}`;
  }
  // facilities
  const c = "crusher-01", cst = V(c, "state");
  if (icons[c]) {
    icons[c].g.querySelector(".body").setAttribute("fill", MACHINE_COLOR[cst] || "#94a3b8");
    icons[c].g.querySelector(".down-ring").style.display = ["failed", "breakdown", "maintenance"].includes(cst) ? "" : "none";
    icons[c].l1.textContent = `Crusher ${fmt(V(c, "throughput_tph"))} t/h`;
    icons[c].l2.textContent = `${cst?.replaceAll("_", " ")} · ${fmt(V(c, "power_kw"))} kW · queue ${V(c, "queue_len") ?? 0}`;
  }
  pileLevel("stockpile-rom", V("stockpile-rom", "fill_pct"), `ROM ${fmt((V("stockpile-rom", "stock_t") || 0) / 1000, 1)} kt`,
    `${fmt(V("stockpile-rom", "grade"), 2)} % Cu · loader ${V("stockpile-rom", "loader_state") || ""}`);
  pileLevel("stockpile-cos", V("stockpile-cos", "fill_pct"), `COS ${fmt(V("stockpile-cos", "stock_t"))} t`,
    `${fmt(V("stockpile-cos", "hours_of_feed"), 1)} h of feed · ${fmt(V("stockpile-cos", "grade"), 2)} % Cu`);
  if (icons["plant-feed"]) {
    const gs = V("plant-feed", "grade_status");
    icons["plant-feed"].g.querySelector(".body").setAttribute("fill", gs === "in_spec" ? "#22c55e" : gs === "starved" ? "#ef4444" : "#f59e0b");
    icons["plant-feed"].l1.textContent = `Plant ${fmt(V("plant-feed", "feed_tph"))} t/h`;
    const lim = V("plant-feed", "limited_by");
    icons["plant-feed"].l2.textContent = `${fmt(V("plant-feed", "feed_grade"), 2)} % Cu ${gs?.replace("_", " ") || ""} · ${fmt((V("plant-feed", "power_kw") || 0) / 1000, 1)} MW${lim === "grid_cap" ? " · GRID CAP" : lim === "load_shed" ? " · LOAD SHED" : ""}`;
    icons["plant-feed"].g.querySelector(".down-ring").style.display = lim === "load_shed" ? "" : "none";
  }
  if (icons["waste-dump"]) {
    icons["waste-dump"].l1.textContent = `Waste dump ${fmt(V("waste-dump", "tph"))} t/h`;
    icons["waste-dump"].l2.textContent = `${fmt((V("waste-dump", "tonnes_total") || 0) / 1000, 1)} kt total`;
  }
  if (icons["fuel-bay"]) {
    const fp = V("fuel-bay", "fill_pct") || 0, lv = icons["fuel-bay"].g.querySelector(".level");
    lv.setAttribute("height", 22 * fp / 100); lv.setAttribute("y", -11 + 22 * (1 - fp / 100));
    lv.setAttribute("fill", V("fuel-bay", "status") === "supply_cut" ? "#ef4444" : "#2dd4bf");
    icons["fuel-bay"].g.querySelector(".down-ring").style.display = V("fuel-bay", "status") === "supply_cut" ? "" : "none";
    icons["fuel-bay"].l1.textContent = `Fuel bay ${fmt(V("fuel-bay", "stock_l"))} L`;
    const hc = V("fuel-bay", "hours_of_cover");
    icons["fuel-bay"].l2.textContent = `${hc >= 999 ? "stock rising" : fmt(hc, 1) + " h cover"} · workshop`;
  }
  const crushing = cst === "crushing", feeding = (V("plant-feed", "feed_tph") || 0) > 1;
  conv.c1?.classList.toggle("run", crushing);
  conv.c2?.classList.toggle("run", feeding);
  // trucks: set animation targets
  const interval = prevS ? clamp((S.ts - prevS.ts) * 1000, 400, 4000) : 1000;
  for (const [key, tr] of Object.entries(trucks)) {
    const [copy, name] = key.split(":");
    const f = S[copy]?.[name];
    if (!f) continue;
    const cur = currentPos(tr, now);
    const seg = f.segment && roads[f.segment] ? f.segment : null;
    const target = { x: f.x, y: f.y, seg, s: seg ? project(roads[seg], f.x, f.y)[1] : 0 };
    tr.from = cur || target;
    if (tr.from.seg !== target.seg && tr.from.seg) tr.from = { ...tr.from, seg: null };
    tr.to = target; tr.t0 = now; tr.dur = interval;
    if (!cur) {
      tr.ic.x = target.x; tr.ic.y = target.y;
      tr.ic.g.setAttribute("transform", `translate(${target.x},${-target.y}) scale(${iconScale()})`);
    }
    if (copy === "live") {
      const st = f.state, body = tr.ic.g.querySelector(".body"), load = tr.ic.g.querySelector(".load");
      body.setAttribute("fill", TRUCK_COLOR[st] || "#94a3b8");
      tr.ic.g.querySelector(".down-ring").style.display = (st === "breakdown" || st === "waiting_road") ? "" : "none";
      const loaded = (f.payload_t || 0) > 0;
      load.setAttribute("fill", loaded ? (f.material === "waste" ? "#4a5262" : gradeColor(f.load_grade)) : "none");
      load.setAttribute("stroke", loaded ? "#0d1117" : "none");
      let t = tr.ic.g.querySelector("title") || svg("title", {}, tr.ic.g);
      t.textContent = `${name}: ${st.replaceAll("_", " ")}${f.waiting_reason ? " (" + f.waiting_reason + ")" : ""}\n` +
        `${fmt(f.payload_t)} t ${f.material || "empty"}${loaded && f.material === "ore" ? " @ " + fmt(f.load_grade, 2) + " % Cu" : ""} → ${f.destination || "–"}\n` +
        `${fmt(f.speed_kmh)} km/h · fuel ${fmt(f.fuel_pct)} % · health ${fmt(f.health_pct)} %`;
    }
  }
  layers.ghosts.style.display = $("#optGhost").checked ? "" : "none";
}
function pileLevel(name, pct, l1, l2) {
  const ic = icons[name];
  if (!ic) return;
  const lv = ic.g.querySelector(".level"), H = +lv.dataset.h, base = +lv.dataset.base, h = H * clamp((pct || 0) / 100, 0, 1);
  lv.setAttribute("y", base - h); lv.setAttribute("height", h);
  ic.l1.textContent = l1; ic.l2.textContent = l2;
}
function drawZones() {
  layers.zones.innerHTML = "";
  const sc = S.scenarios || {};
  for (const [key, txt] of [["hardness_spike", "hardness ×"], ["low_grade_zone", "grade ×"]]) {
    const p = sc[key];
    if (!p?.active) continue;
    for (const cl of p.clusters || []) {
      const a = A()[cl];
      if (!a?._center) continue;
      const [x, y] = a._center;
      svg("ellipse", { cx: x, cy: -y, rx: 95, ry: 95, class: "zone" }, layers.zones);
      label(layers.zones, x, y + 105, `${txt}${(+p.factor).toFixed(2)}`, "lbl big");
    }
  }
}
function currentPos(tr, now) {
  if (!tr.to) return null;
  const t = clamp((now - tr.t0) / tr.dur, 0, 1), a = tr.from, b = tr.to;
  if (a.seg && a.seg === b.seg) {
    const s = a.s + (b.s - a.s) * t, [x, y] = pointAt(roads[a.seg], s);
    return { x, y, seg: a.seg, s };
  }
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, seg: t >= 1 ? b.seg : null, s: b.s };
}
function animate() {
  const now = performance.now(), k = iconScale();
  for (const tr of Object.values(trucks)) {
    const p = currentPos(tr, now);
    if (!p) continue;
    tr.ic.x = p.x; tr.ic.y = p.y;
    tr.ic.g.setAttribute("transform", `translate(${p.x},${-p.y}) scale(${k})`);
  }
  setTimeout(() => requestAnimationFrame(animate), 33);
}

// ------------------------------------------------------------------ panels
function header() {
  const h = V("mine", "sim_time_h") || 0, d = Math.floor(h / 24), hh = Math.floor(h % 24), mm = Math.floor((h * 60) % 60);
  $("#clock").textContent = `Sim day ${d + 1}, ${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  const conn = $("#conn");
  conn.textContent = S.ok ? "● live from Ditto" : "● Ditto unreachable";
  conn.className = "badge " + (S.ok ? "ok" : "bad");
  const ml = V("mine", "mass_balance_residual_t"), mb = V("mine", "mass_balance_residual_t", "base");
  const okm = Math.abs(ml ?? 1) < 1e-3 && Math.abs(mb ?? 1) < 1e-3;
  $("#mass").textContent = `${okm ? "✓" : "✗"} mass balance ${fmt(ml, 3)} t`;
  $("#mass").className = "badge " + (okm ? "ok" : "bad");
  const fl = V("mine", "fuel_balance_residual_l"), fb = V("mine", "fuel_balance_residual_l", "base");
  const okf = Math.abs(fl ?? 1) < 1e-2 && Math.abs(fb ?? 1) < 1e-2;
  $("#fuel").textContent = `${okf ? "✓" : "✗"} fuel balance ${fmt(fl, 3)} L`;
  $("#fuel").className = "badge " + (okf ? "ok" : "bad");
  const el = V("mine", "energy_balance_residual_kwh"), eb = V("mine", "energy_balance_residual_kwh", "base");
  const oke = Math.abs(el ?? 1) < 1e-2 && Math.abs(eb ?? 1) < 1e-2;
  $("#energy").textContent = `${oke ? "✓" : "✗"} energy balance ${fmt(el, 3)} kWh`;
  $("#energy").className = "badge " + (oke ? "ok" : "bad");
  const act = Object.entries(S.scenarios || {}).filter(([, p]) => p?.active).map(([k]) => STATIC.scenarios[k]?.label || k);
  for (const [k, m] of Object.entries(STATIC.modules || {})) if (S.live[m.twin]?.module_enabled === false) act.push(`${m.label} OFF`);
  $("#active").textContent = act.length ? `what-if: ${act.join(", ")}` : "no what-if active";
  $("#active").className = "badge " + (act.length ? "warn" : "");
}

function kpiTable() {
  const tb = $("#kpis tbody");
  tb.innerHTML = "";
  for (const r of S.kpis) {
    const tr = html("tr");
    if (r.key === selectedKpi) tr.className = "sel";
    const pct = r.pct == null ? "" : ` (${r.pct > 0 ? "+" : ""}${fmt(r.pct, 1)}%)`;
    const dsign = r.delta == null ? "" : (r.delta > 0 ? "+" : "");
    tr.innerHTML = `<td>${r.label}<small>${r.unit}</small></td><td class="live">${fmt(r.live, r.decimals)}</td>` +
      `<td class="base">${fmt(r.base, r.decimals)}</td><td class="${r.verdict}">${r.delta == null ? "–" : dsign + fmt(r.delta, r.decimals) + pct}</td>`;
    tr.addEventListener("click", () => { selectedKpi = r.key; kpiTable(); loadTrend(); });
    tb.appendChild(tr);
  }
}

const MOD_TEXT = {
  dispatcher: ["Off: fixed truck-to-shovel allocation, all ore to the crusher, no blending, no fuel rationing.",
    d => [["decisions", d.decisions_total], ["re-routes", d.reroutes_total], ["ore to ROM", d.ore_to_rom_total],
      ["blend diversions", d.blend_diversions_total], ["trucks rationed", d.fuel_rationed_trucks]]],
  maintenance: ["Off: run to failure, no planned maintenance.",
    m => [["PM decisions", m.decisions_total], ["failures", m.failures_total], ["planned jobs", m.planned_total],
      ["loss avoided", `${fmt(m.expected_loss_avoided_t)} t`]]],
  energy: ["Off: no throttling; a 15-min block above a grid cap sheds the concentrator for 15 min.",
    g => [["decisions", g.decisions_total], ["cap violations", g.cap_violations_total],
      ["peak", `${fmt((g.peak_demand_kw || 0) / 1000, 1)} MW`], ["plant", g.plant_load_shed ? "SHED" : g.plant_throttled ? "throttled" : "normal"]]],
};
function hm(h) { const d = Math.floor(h / 24), hh = Math.floor(h % 24), mm = Math.floor((h * 60) % 60); return `d${d + 1} ${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`; }
function decisions() {
  const box = $("#modules");
  for (const [name, meta] of Object.entries(STATIC.modules || {})) {
    let card = $(`#mod-${name}`);
    if (!card) {
      card = html("div", { class: "mod", id: `mod-${name}` });
      card.innerHTML = `<div class="t"><span>${meta.label}</span><label class="switch" title="switch the module on or off in the live twin"><input type="checkbox"><span></span></label></div>
        <div class="muted small off-text"></div><ol></ol><div class="counters small"></div>`;
      card.querySelector(".off-text").textContent = MOD_TEXT[name][0];
      card.querySelector("input").addEventListener("change", e =>
        call("POST", `/api/modules/${name}`, { enabled: e.target.checked }, `${meta.label} ${e.target.checked ? "on" : "off"} (live twin)`));
      box.appendChild(card);
    }
    const tw = S.live[meta.twin] || {}, on = tw.module_enabled !== false;
    card.classList.toggle("off", !on);
    const inp = card.querySelector("input");
    if (document.activeElement !== inp) inp.checked = on;
    let log = [];
    try { log = JSON.parse(tw.decision_log || "[]"); } catch (e) { /* keep empty */ }
    const ol = card.querySelector("ol");
    ol.innerHTML = "";
    for (const e of log.slice().reverse()) {
      const li = html("li");
      li.appendChild(html("time", {}, hm(e.t_h)));
      li.appendChild(document.createTextNode(e.text));
      ol.appendChild(li);
    }
    if (!log.length) ol.innerHTML = `<li class="muted">${on ? "no decision yet" : "module off"}</li>`;
    const cnt = card.querySelector(".counters");
    cnt.innerHTML = "";
    for (const [k, v] of MOD_TEXT[name][1](tw)) {
      const sp = html("span", {}, `${k} `);
      sp.appendChild(html("b", {}, typeof v === "number" ? fmt(v) : (v ?? "–")));
      cnt.appendChild(sp);
    }
  }
}

let lastEventKey = "";
function events() {
  const ol = $("#events"), ev = [...(S.events || [])].reverse();
  const key = ev.map(e => e.t_h + e.text).join("|");
  if (key === lastEventKey) return;
  const old = new Set(lastEventKey.split("|"));
  lastEventKey = key;
  ol.innerHTML = "";
  for (const e of ev) {
    const li = html("li");
    if (!old.has(e.t_h + e.text) && old.size > 1) li.className = "new";
    const h = e.t_h, d = Math.floor(h / 24), hh = Math.floor(h % 24), mm = Math.floor((h * 60) % 60);
    li.appendChild(html("time", {}, `d${d + 1} ${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`));
    li.appendChild(html("span", {}, e.text));
    ol.appendChild(li);
  }
  if (!ev.length) ol.innerHTML = '<li class="muted">no events yet</li>';
}

function gauges() {
  const G = $("#gauges");
  const cap = V("power-grid", "cap_kw") || 0, dem = V("power-grid", "demand_15min_kw") || V("power-grid", "site_power_kw") || 0;
  const items = [
    ["Coarse ore stockpile", `${fmt(V("stockpile-cos", "stock_t"))} t`, V("stockpile-cos", "fill_pct"), V("stockpile-cos", "fill_pct", "base"),
      `${fmt(V("stockpile-cos", "hours_of_feed"), 1)} h of feed`, "#b08350"],
    ["ROM stockpile", `${fmt((V("stockpile-rom", "stock_t") || 0) / 1000, 1)} kt`, V("stockpile-rom", "fill_pct"), V("stockpile-rom", "fill_pct", "base"),
      `${fmt(V("stockpile-rom", "grade"), 2)} % Cu`, "#8b6b3d"],
    ["Fuel bay", `${fmt(V("fuel-bay", "stock_l"))} L`, V("fuel-bay", "fill_pct"), V("fuel-bay", "fill_pct", "base"),
      `${fmt(V("fuel-bay", "consumption_lph"))} L/h use · ${fmt(V("fuel-bay", "resupply_lph"))} L/h in`, V("fuel-bay", "status") === "supply_cut" ? "#ef4444" : "#2dd4bf"],
    ["Grid demand (15 min)", `${fmt(dem / 1000, 1)} MW`, 100 * dem / 40000, 100 * (V("power-grid", "demand_15min_kw", "base") || 0) / 40000,
      cap ? `cap ${fmt(cap / 1000, 1)} MW` : `peak ${fmt((V("power-grid", "peak_demand_kw") || 0) / 1000, 1)} MW`, cap ? "#f59e0b" : "#60a5fa"],
    ["Plant feed grade", `${fmt(V("plant-feed", "feed_grade_avg"), 3)} % Cu`, 100 * (V("plant-feed", "feed_grade_avg") || 0) / 1.2,
      100 * (V("plant-feed", "feed_grade_avg", "base") || 0) / 1.2, `band ${S.band[0]}–${S.band[1]} % Cu`, "#f59e0b"],
  ];
  G.innerHTML = items.map(([h, v, p, pb, sub, col]) => `<div class="gauge"><div class="h"><span>${h}</span></div><div class="v">${v}</div>
    <div class="bar"><i style="width:${clamp(p || 0, 0, 100)}%;background:${col}"></i><b style="left:${clamp(pb || 0, 0, 100)}%" title="baseline"></b></div>
    <div class="h"><span>${sub}</span><span>▮ baseline</span></div></div>`).join("");
}

function healthStrip() {
  const H = $("#health");
  const units = ["crusher-01", ...Object.keys(S.live).filter(n => n.startsWith("ex-")).sort(), ...Object.keys(S.live).filter(n => n.startsWith("truck-")).sort()];
  H.innerHTML = "";
  for (const n of units) {
    const f = S.live[n] || {}, h = f.health_pct ?? 0, ms = f.maintenance_status || "ok";
    const card = html("div", { class: `hcard ${ms}`, title: `${n}: health ${fmt(h, 0)} %, P(fail in 8 h) ${fmt(100 * (f.failure_prob_8h || 0), 0)} %, failures ${f.failures_total ?? 0}, planned jobs ${f.planned_maintenance_total ?? 0}` });
    const lbl = n === "crusher-01" ? "CR-01" : n.toUpperCase().replace("TRUCK-", "T");
    const icon = ms === "breakdown" ? "✗" : ms === "in_maintenance" ? "🔧" : ms === "pm_planned" ? "⏱" : "";
    card.innerHTML = `<div class="n"><b>${lbl}</b><span>${icon} ${fmt(h, 0)}%</span></div>
      <div class="bar"><i style="width:${h}%;background:${healthColor(h)}"></i></div><div class="s">${(f.state || ms).replaceAll("_", " ")}</div>`;
    card.addEventListener("click", () => inspect(n));
    H.appendChild(card);
  }
}

// ------------------------------------------------------------------ scenarios
const ctlValues = {};
function scenarioPanel() {
  const box = $("#scenarios");
  if (!box.dataset.built) {
    box.dataset.built = "1";
    for (const [name, meta] of Object.entries(STATIC.scenarios)) {
      const card = html("div", { class: "scn", id: `scn-${name}` });
      card.innerHTML = `<div class="t"><span>${meta.label}</span><span class="pill" hidden>ACTIVE</span></div><div class="d"></div><div class="ctl"></div>
        <div class="btns"><button class="btn run">Run</button><button class="btn undo" hidden>Undo</button></div>`;
      card.querySelector(".d").textContent = SCN_TEXT[name] || "";
      const ctl = card.querySelector(".ctl");
      for (const p of meta.params) ctl.appendChild(control(name, p));
      if (!meta.params.length) ctl.remove();
      card.querySelector(".run").addEventListener("click", () => runScenario(name));
      card.querySelector(".undo").addEventListener("click", () => call("DELETE", `/api/scenarios/${name}`, null, `${meta.label} undone`));
      box.appendChild(card);
    }
    $("#undo").addEventListener("click", () => call("POST", "/api/scenarios/undo", null, "Last change undone"));
    $("#reset").addEventListener("click", () => call("POST", "/api/scenarios/reset", null, "All scenarios reset"));
  }
  for (const [name, meta] of Object.entries(STATIC.scenarios)) {
    const card = $(`#scn-${name}`), p = S.scenarios?.[name] || {};
    card.classList.toggle("active", !!p.active);
    card.querySelector(".pill").hidden = !p.active;
    card.querySelector(".undo").hidden = !p.active;
    card.querySelector(".run").textContent = p.active ? "Update" : "Run";
    for (const k of meta.params) {
      const inp = card.querySelector(`[data-p="${k}"]`);
      if (!inp || document.activeElement === inp || ctlValues[`${name}.${k}`] !== undefined) continue;
      const v = p[k];
      if (v === undefined) continue;
      inp.value = Array.isArray(v) ? v[0] : v;
      inp.dispatchEvent(new Event("input"));
    }
  }
  const u = S.undo || [];
  $("#undo").disabled = !u.length;
  $("#undoList").textContent = u.length ? "Undo stack: " + u.slice(-5).map(x => x.label).join(" → ") : "";
}
function control(name, p) {
  const wrap = html("label");
  const at = A();
  const opts = {
    segments: Object.keys(at).filter(n => n.startsWith("road-")).sort().map(n => [n.slice(5), `${n.slice(5)} · ${at[n].name.split(": ")[1] || ""}`]),
    excavators: Object.keys(at).filter(n => n.startsWith("ex-")).sort().map(n => [n, `${n.toUpperCase()} · ${at[n].role}`]),
    clusters: Object.keys(at).filter(n => n.startsWith("blocks-")).sort().map(n => [n, n.slice(7)]),
  }[p];
  let inp;
  if (opts) {
    wrap.append(p === "segments" ? "road" : p === "excavators" ? "shovel" : "zone");
    inp = html("select");
    for (const [v, t] of opts) inp.appendChild(html("option", { value: v }, t));
  } else {
    const R = { factor: name === "hardness_spike" ? [1.0, 2.5, 0.05, 1.4] : [0.1, 1.0, 0.05, 0.6],
                cap_kw: [15000, 40000, 500, 28000], resupply_lph: [0, 2200, 100, 0], bay_stock_l: [0, 80000, 1000, 8000] }[p];
    const txt = { factor: "×", cap_kw: "cap kW", resupply_lph: "supply L/h", bay_stock_l: "stock L" }[p];
    wrap.append(txt);
    if (p === "factor" || p === "cap_kw") {
      inp = html("input", { type: "range", min: R[0], max: R[1], step: R[2], value: R[3] });
      const out = html("b", {}, String(R[3]));
      inp.addEventListener("input", () => (out.textContent = p === "cap_kw" ? fmt(+inp.value) : (+inp.value).toFixed(2)));
      wrap.append(inp, out);
    } else {
      inp = html("input", { type: "number", min: R[0], max: R[1], step: R[2], value: R[3] });
    }
  }
  inp.dataset.p = p;
  inp.addEventListener("change", () => (ctlValues[`${name}.${p}`] = inp.value));
  if (!wrap.contains(inp)) wrap.append(inp);
  return wrap;
}
function runScenario(name) {
  const card = $(`#scn-${name}`), body = {};
  card.querySelectorAll("[data-p]").forEach(inp => {
    const p = inp.dataset.p;
    body[p] = ["segments", "excavators", "clusters"].includes(p) ? [inp.value] : +inp.value;
  });
  STATIC.scenarios[name].params.forEach(p => delete ctlValues[`${name}.${p}`]);
  call("POST", `/api/scenarios/${name}`, body, `${STATIC.scenarios[name].label} running on the live twin`);
}
async function call(method, url, body, msg) {
  try {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const j = await r.json().catch(() => ({}));
    toast(r.ok ? msg : `Error: ${j.detail || r.status}`);
  } catch (e) { toast(`Error: ${e}`); }
}
let toastT = 0;
function toast(t) {
  const el = $("#toast");
  el.textContent = t; el.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => (el.hidden = true), 2600);
}

// ------------------------------------------------------------------ inspector
async function inspect(name) {
  const box = $("#inspector");
  box.hidden = false;
  box.innerHTML = `<span class="x">✕</span><h3>${name}</h3><div class="muted">loading from Ditto…</div>`;
  box.querySelector(".x").onclick = () => (box.hidden = true);
  try {
    const r = await fetch(`/api/twins/${name}`);
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.status);
    const L = j.live, B = j.baseline, a = L.attributes || {};
    const skip = new Set(["blocks", "geometry", "benches", "road_nodes", "facilities", "failure_model", "controls", "maintains", "supplies"]);
    let rows = "";
    for (const [k, v] of Object.entries(a)) {
      if (skip.has(k) || k.startsWith("_is")) continue;
      rows += `<tr><td>${k}</td><td>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</td><td></td></tr>`;
    }
    let feats = "";
    for (const [k, v] of Object.entries(L.features || {})) {
      if (k === "event_log") continue;
      const lv = v.properties?.value, u = v.properties?.unit || "", bv = B?.features?.[k]?.properties?.value;
      const show = x => typeof x === "number" ? fmt(x, Math.abs(x) < 10 ? 3 : 1) : esc(String(x ?? "–"));
      const diff = B && JSON.stringify(lv) !== JSON.stringify(bv);
      feats += `<tr><td>${k}</td><td>${show(lv)} <span class="muted">${u}</span></td><td class="${diff ? "diff" : "muted"}">${B ? show(bv) : ""}</td></tr>`;
    }
    box.innerHTML = `<span class="x">✕</span><h3>${esc(a.name || name)}</h3>
      <div class="muted">Ditto thing <code>${esc(L.thingId)}</code><br>type ${esc(a.type || "")} · policy ${esc(L.policyId)}</div>
      <table><tr><td colspan="3"><b>Attributes</b></td></tr>${rows}
      <tr><td><b>Features</b></td><td><b>live</b></td><td><b>baseline</b></td></tr>${feats}</table>`;
    box.querySelector(".x").onclick = () => (box.hidden = true);
  } catch (e) {
    box.innerHTML = `<span class="x">✕</span><h3>${name}</h3><div class="muted">${esc(String(e))}</div>`;
    box.querySelector(".x").onclick = () => (box.hidden = true);
  }
}
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------------------------ trend (InfluxDB)
async function loadTrend() {
  const k = (S?.kpis || []).find(r => r.key === selectedKpi) || { thing: "mine", feature: selectedKpi, label: selectedKpi, unit: "" };
  $("#trendTitle").innerHTML = `Trend · ${k.label} <span class="muted small">${k.unit || ""}</span>`;
  try {
    const r = await fetch(`/api/trend?thing=${k.thing}&feature=${k.feature}&minutes=30`);
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.status);
    drawTrend(j);
  } catch (e) { $("#trendNote").textContent = `Trend unavailable: ${e}`; }
}
function drawTrend(j) {
  const cv = $("#trend"), dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
  const all = [...j.live, ...j.baseline];
  if (!all.length) { g.fillStyle = "#8b96a5"; g.fillText("waiting for data in InfluxDB…", 10, 20); return; }
  const ts = all.map(p => Date.parse(p[0])), vs = all.map(p => p[1]);
  const t0 = Math.min(...ts), t1 = Math.max(...ts);
  let v0 = Math.min(...vs), v1 = Math.max(...vs);
  if (v1 - v0 < 1e-9) { v0 -= 1; v1 += 1; }
  const padv = (v1 - v0) * 0.1; v0 -= padv; v1 += padv;
  const X = t => 36 + (W - 44) * (t - t0) / ((t1 - t0) || 1), Y = v => 8 + (H - 26) * (1 - (v - v0) / (v1 - v0));
  g.strokeStyle = "#222a35"; g.fillStyle = "#8b96a5"; g.font = "10px sans-serif"; g.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const v = v0 + (v1 - v0) * i / 3, y = Y(v);
    g.beginPath(); g.moveTo(36, y); g.lineTo(W - 8, y); g.stroke();
    g.fillText(fmt(v, Math.abs(v1) < 10 ? 2 : 0), 2, y + 3);
  }
  g.fillText(`${Math.round((t1 - t0) / 60000)} min`, W - 44, H - 3);
  for (const [series, col, dash] of [[j.baseline, "#9aa4b2", [5, 4]], [j.live, "#2dd4bf", []]]) {
    if (!series.length) continue;
    g.strokeStyle = col; g.setLineDash(dash); g.lineWidth = 2; g.beginPath();
    series.forEach((p, i) => { const x = X(Date.parse(p[0])), y = Y(p[1]); i ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.stroke();
  }
  g.setLineDash([]);
  g.fillStyle = "#2dd4bf"; g.fillText("— live", 40, H - 3); g.fillStyle = "#9aa4b2"; g.fillText("- - baseline", 82, H - 3);
  $("#trendNote").textContent = "InfluxDB (Telegraf-written twin events), last 30 min real time. Click a KPI row to change.";
}

// ------------------------------------------------------------------ boot
function onState(s) {
  prevS = S; S = s;
  update(); header(); kpiTable(); decisions(); events(); gauges(); healthStrip(); scenarioPanel();
}
async function boot() {
  for (;;) {
    try {
      const r = await fetch("/api/static");
      if (r.ok) { STATIC = await r.json(); break; }
    } catch (e) { /* retry */ }
    $("#conn").textContent = "waiting for twins…";
    await new Promise(r => setTimeout(r, 2000));
  }
  if (STATIC.grafana) $("#grafana").href = STATIC.grafana; else $("#grafana").remove();
  drawStatic(); setupZoom();
  $("#optBlocks").addEventListener("change", e => document.body.classList.toggle("noblocks", !e.target.checked));
  $("#optLabels").addEventListener("change", e => document.body.classList.toggle("nolabels", !e.target.checked));
  $("#optGhost").addEventListener("change", () => update());
  $("#optLegend").addEventListener("change", e => ($("#legend").hidden = !e.target.checked));
  const es = new EventSource("/api/stream");
  es.onmessage = ev => onState(JSON.parse(ev.data));
  es.onerror = () => { $("#conn").textContent = "● reconnecting…"; $("#conn").className = "badge bad"; };
  animate();
  setTimeout(loadTrend, 1500);
  setInterval(loadTrend, 10000);
}
boot();
