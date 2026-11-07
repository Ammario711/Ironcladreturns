/* Dashboard logic, plain JavaScript (no libraries, works offline).
   How data flows:
     1. GET /state            -> `state` (everything to draw)          see refresh()
     2. GET /events (SSE)     -> each event-bus event, live             see connect()
        every event adds a line to the feed and schedules a refresh;
        a "clock" signal arrives whenever the server's live clock moves
     3. buttons / forms       -> POST /play, /tick, /disrupt, ...      see act()
     4. GET /dataset          -> tables for the Dataset explorer       see loadDataset()
   The SERVER runs the city clock, so every open tab shows the same live city.
   Sections: 1 helpers, 2 app state, 3 refresh + render, 4 header and controls,
   5 KPI cards, 6 map, 7 side lists, 8 details drawer, 9 live feed + toasts,
   10 add-emergency form, 11 playback + actions, 12 input handling,
   13 city events form, 14 dataset explorer, 15 start. */
"use strict";

// ====================================================================== 1. helpers
const $ = (selector) => document.querySelector(selector);
const SVGNS = "http://www.w3.org/2000/svg";

/** Escape text before putting it into HTML (data can contain any characters). */
function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
/** Ids can be numbers (33) or text ("P3"); compare them as text. */
const same = (a, b) => String(a) === String(b);
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const pct = (part, whole) => (whole > 0 ? clamp((100 * part) / whole, 0, 100) : 0);
const nice = (text) => String(text ?? "").replace(/_/g, " ");

// Small line icons (24x24, drawn with the current text colour).
const ICONS = {
  play: '<path d="M7 4.5v15l12.5-7.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  forward: '<path d="M4 6l7 6-7 6zM13 6l7 6-7 6z" fill="currentColor" stroke="none"/>',
  reset: '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20.5 13.5A8.5 8.5 0 1 1 10.5 3.5a6.5 6.5 0 0 0 10 10z"/>',
  cloud: '<path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.6 1.5A3.5 3.5 0 0 0 7 18z"/>',
  rain: '<path d="M7 14h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.6 1.5A3.5 3.5 0 0 0 7 14z"/><path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>',
  snow: '<path d="M12 2v20M4.2 7l15.6 10M4.2 17L19.8 7"/><path d="M9.5 3.5L12 5l2.5-1.5M9.5 20.5L12 19l2.5 1.5"/>',
  fog: '<path d="M4 9h16M6 13h12M8 17h8"/>',
  storm: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  truck: '<path d="M2 6h11v10H2zM13 10h4l3 3v3h-7z"/><circle cx="6" cy="18" r="2"/><circle cx="16" cy="18" r="2"/>',
  hub: '<path d="M3 21V9l9-5 9 5v12"/><path d="M8 21v-6h8v6"/>',
  pin: '<path d="M12 22s7-6.3 7-12a7 7 0 0 0-14 0c0 5.7 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/>',
  alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
  road: '<path d="M8 3L5 21M16 3l3 18M12 4v3M12 11v3M12 18v2"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 20a6.5 6.5 0 0 0-2.5-5.1"/>',
  box: '<path d="M3 7.5L12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5l9 4.5 9-4.5M12 12v9"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>',
  zoomIn: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M11 8v6M8 11h6"/>',
  zoomOut: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 1v3M12 20v3M1 12h3M20 12h3"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
  node: '<circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>',
};
function icon(name, extra = "") {
  return `<svg class="i ${extra}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}
function weatherIcon(condition) {
  if (condition === "fog") return icon("fog");
  if (["snow", "heavy_snow"].includes(condition)) return icon("snow");
  if (["drizzle", "rain", "heavy_rain", "freezing_rain"].includes(condition)) return icon("rain");
  if (condition === "storm") return icon("storm");
  return icon("sun");
}

// Crisis state -> CSS colour class (colours are defined once in app.css).
const STATE_CLASS = {
  "Open": "s-open", "Prioritized": "s-prioritized", "Resource Assigned": "s-assigned",
  "In Progress": "s-progress", "Re-Routing": "s-rerouting", "Delayed": "s-delayed",
  "Delivered": "s-delivered", "Resolved": "s-resolved", "Failed": "s-failed",
};
const TERMINAL = new Set(["Resolved", "Failed"]);
const URGENT = new Set(["Delayed", "Re-Routing", "Failed"]);
const badge = (name) => `<span class="badge ${STATE_CLASS[name] || "s-open"}">${esc(name)}</span>`;

async function getJSON(url) {
  const reply = await fetch(url, { cache: "no-store" });
  return reply.json();
}
async function post(url, body) {
  const options = { method: "POST" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const reply = await fetch(url, options);
  return reply.json();
}

// ====================================================================== 2. app state
let state = null;              // last /state reply from the server
let selection = null;          // what the details drawer shows: {kind, id}
let activeTab = "queue";
// (Play/pause and speed live on the SERVER: see state.playing and state.speed.)
let busy = false;              // a button request is in flight
let explorerOpen = false;      // Dataset explorer (section 14)
let explorerData = null;       // last /dataset reply
let explorerSort = { key: null, dir: 1 };
let wasFinished = false;
let feedFilter = "all";
let feedEvents = [];           // every event received (newest last)
let lastSeq = 0;               // number of the last event received
let toastAfterSeq = Infinity;  // only events newer than this pop up as toasts
let positions = {};            // node id -> [x, y] on the map
let view = { x: 0, y: 0, w: 1000, h: 640 };  // visible part of the map (zoom/pan)
const ui = { routes: true, labels: true };
const MAP_W = 1000, MAP_H = 640, MAP_PAD = 56;
// Route colours: no red or amber, which mean "closed" and "traffic jam" on this map.
const ROUTE_COLOURS = ["#2563eb", "#7c3aed", "#0891b2", "#db2777", "#4f46e5", "#0d9488", "#9333ea", "#0284c7"];

const byId = (list) => Object.fromEntries(list.map((item) => [String(item.id), item]));

// ====================================================================== 3. refresh + render
let refreshQueued = false;
/** Many events can arrive at once: redraw at most about 6 times per second. */
function queueRefresh() {
  if (!refreshQueued) {
    refreshQueued = true;
    setTimeout(refresh, 150);
  }
}
async function refresh() {
  refreshQueued = false;
  const next = await getJSON("/state");
  const firstLoad = state === null;
  const restarted = !firstLoad && next.run_id !== state.run_id;
  state = next;
  if (firstLoad || restarted) {
    positions = layout(state.nodes);
    toastAfterSeq = state.last_seq;  // older events are history, not news
    if (firstLoad) fitView();
  }
  if (state.finished && !wasFinished && !firstLoad) {
    toast("Scenario finished", "Every request is resolved or failed. Press Reset to run it again.", "k-good", "check");
  }
  wasFinished = state.finished;
  render();
  if (explorerOpen) loadDataset();  // keep the Dataset tables live too
}
function render() {
  renderHeader();
  renderControls();
  renderKpis();
  renderMap();
  renderTabs();
  renderDrawer();
}

// ====================================================================== 4. header + controls
function renderHeader() {
  $("#dataset").textContent = state.dataset;
  $("#clock").textContent = state.clock;
  const speedText = state.speed === "real" ? "real time" : `1 second = ${state.speed} min`;
  $("#clock-sub").textContent = state.finished ? "Scenario finished"
    : state.playing ? `\u25CF Live - ${speedText}`
    : `Paused - ${state.scheduled} scheduled event${state.scheduled === 1 ? "" : "s"} to come`;
  $("#clock-sub").classList.toggle("is-live", state.playing);
  const c = state.conditions;
  const pill = $("#conditions-pill");
  pill.className = "pill" + (c.factor > 1.001 ? " slow" : "");
  pill.innerHTML = `${weatherIcon(c.weather)}<b>${esc(nice(c.weather))}</b>`
    + (c.rush_factor > 1 ? " &middot; rush hour" : "")
    + ` &middot; roads &times;${c.factor.toFixed(2)}`;
  // Decorative weather effect over the map.
  const fx = { snow: "fx-snow", heavy_snow: "fx-snow fx-heavy", drizzle: "fx-rain", rain: "fx-rain",
               heavy_rain: "fx-rain", freezing_rain: "fx-rain", fog: "fx-fog", storm: "fx-rain fx-storm" }[c.weather];
  $("#weather-fx").className = "weather-fx" + (fx ? ` on ${fx}` : "");
}

function renderControls() {
  const play = $("#play-btn");
  play.innerHTML = state.finished ? `${icon("check")}<span>Finished</span>`
    : state.playing ? `${icon("pause")}<span>Pause</span>` : `${icon("play")}<span>Run live</span>`;
  play.disabled = state.finished;
  $("#run-btn").disabled = state.finished;
  $("#reset-btn").disabled = !state.can_reset;
  document.querySelectorAll("#speed button").forEach((b) => {
    const on = b.dataset.speed === state.speed;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", on);
  });
  const select = $("#weather-select");
  if (select.options.length !== state.options.weather.length) {
    select.innerHTML = state.options.weather.map((w) => `<option value="${esc(w)}">${esc(nice(w))}</option>`).join("");
  }
  select.value = state.conditions.weather;
  $("#weather-icon").innerHTML = weatherIcon(state.conditions.weather);
}

// ====================================================================== 5. KPI cards
function kpi(iconName, tone, label, value, sub, extra = "") {
  return `<div class="card kpi"><div class="kpi-top"><span>${label}</span><span class="kpi-icon ${tone}">${icon(iconName)}</span></div>
    <div class="kpi-value">${value}</div>${extra}<div class="kpi-sub">${sub}</div></div>`;
}
function renderKpis() {
  const st = state.stats;
  const all = state.emergencies;
  const active = all.filter((e) => !TERMINAL.has(e.state)).length;
  const states = Object.keys(STATE_CLASS);
  const stateBar = `<div class="bar">${states.map((name) => {
    const n = st.by_state[name] || 0;
    return n ? `<span class="${STATE_CLASS[name]}" style="width:${pct(n, all.length)}%" title="${esc(name)}: ${n}"></span>` : "";
  }).join("")}</div>`;
  const vehicles = state.vehicles;
  const count = (status) => vehicles.filter((v) => v.status === status).length;
  const [busyN, idleN, brokenN] = [count("busy"), count("idle"), count("broken")];
  const fleetBar = `<div class="bar"><span style="width:${pct(busyN, vehicles.length)}%"></span>`
    + `<span style="width:${pct(idleN, vehicles.length)}%;background:var(--faint)"></span>`
    + `<span style="width:${pct(brokenN, vehicles.length)}%;background:var(--bad)"></span></div>`;
  const sum = (key) => state.hubs.reduce((total, h) => total + h[key], 0);
  const stockBars = `<div class="bar thin"><span style="width:${pct(sum("normal"), sum("initial_normal"))}%"></span></div>`
    + `<div class="bar thin cold"><span style="width:${pct(sum("cold"), sum("initial_cold"))}%"></span></div>`;
  const delivered = pct(st.units_delivered, st.units_requested);
  $("#kpis").innerHTML = [
    kpi("activity", "", "Active requests", `${active}<small>/ ${all.length}</small>`,
        `${st.by_state.Resolved || 0} resolved &middot; ${st.by_state.Failed || 0} failed`, stateBar),
    kpi("box", "ok", "Food delivered", `${st.units_delivered}<small>/ ${st.units_requested}</small>`,
        `${Math.round(delivered)}% of requested units`,
        `<div class="bar"><span style="width:${delivered}%;background:var(--ok)"></span></div>`),
    kpi("clock", "ok", "On-time deliveries", `${st.on_time_percent ?? "&ndash;"}<small>%</small>`,
        `${st.on_time} of ${st.fully_delivered} completed requests`),
    kpi("users", "", "People fully served", `${st.people_fully_served}`,
        st.avg_response_minutes == null ? "no completed requests yet" : `${st.avg_response_minutes} min average response`),
    kpi("truck", "", "Fleet", `${busyN}<small> busy</small>`,
        `${idleN} idle &middot; ${brokenN} broken &middot; ${vehicles.length} total`, fleetBar),
    kpi("hub", "cold", "Food in stock", `${sum("normal")}<small>N</small> ${sum("cold")}<small>C</small>`,
        `normal / cold across ${state.hubs.length} hub${state.hubs.length === 1 ? "" : "s"}`, stockBars),
  ].join("");
}

// ====================================================================== 6. map
/** Node positions: flat projection of lat/lng, or a circle if coordinates are missing. */
function layout(nodes) {
  const pos = {};
  const geo = nodes.length > 0 && nodes.every((n) => n.lat !== null && n.lng !== null);
  if (geo) {
    const lats = nodes.map((n) => n.lat), lngs = nodes.map((n) => n.lng);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats), minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
    const k = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);  // longitude shrinks away from the equator
    const w = Math.max((maxLng - minLng) * k, 1e-9), h = Math.max(maxLat - minLat, 1e-9);
    const s = Math.min((MAP_W - 2 * MAP_PAD) / w, (MAP_H - 2 * MAP_PAD) / h);
    const ox = (MAP_W - w * s) / 2, oy = (MAP_H - h * s) / 2;
    nodes.forEach((n) => { pos[n.id] = [ox + (n.lng - minLng) * k * s, oy + (maxLat - n.lat) * s]; });
  } else {
    nodes.forEach((n, i) => {
      const a = (2 * Math.PI * i) / Math.max(nodes.length, 1);
      pos[n.id] = [MAP_W / 2 + (MAP_W / 2 - MAP_PAD) * Math.cos(a), MAP_H / 2 + (MAP_H / 2 - MAP_PAD) * Math.sin(a)];
    });
  }
  return pos;
}
function applyView() { $("#map").setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`); }
function fitView() { view = { x: 0, y: 0, w: MAP_W, h: MAP_H }; applyView(); }
/** Zoom by `factor` (<1 = closer) keeping map point (cx, cy) under the same spot. */
function zoomAt(factor, cx, cy) {
  const w = clamp(view.w * factor, MAP_W / 8, MAP_W * 2);
  const k = w / view.w;
  view = { x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k, w, h: view.h * k };
  applyView();
}
const zoomCenter = (factor) => zoomAt(factor, view.x + view.w / 2, view.y + view.h / 2);
function focusNode(nodeId) {
  const [x, y] = positions[nodeId];
  view = { w: MAP_W / 2.5, h: MAP_H / 2.5, x: x - MAP_W / 5, y: y - MAP_H / 5 };
  applyView();
}
const isSelected = (kind, id) => selection && selection.kind === kind && same(selection.id, id);
const routeColour = (vehicleId) => {
  const text = String(vehicleId);
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) % 9973;
  return ROUTE_COLOURS[h % ROUTE_COLOURS.length];
};
/** Where a vehicle is drawn: `f` of the way from node `from` to node `to`. */
function vehiclePoint(v) {
  const a = positions[v.from], b = positions[v.to];
  return [a[0] + (b[0] - a[0]) * v.f, a[1] + (b[1] - a[1]) * v.f];
}

function renderMap() {
  $("#map").classList.toggle("hide-labels", !ui.labels);
  // Roads: a visible line + a wide invisible line that is easy to hover and click.
  $("#layer-roads").innerHTML = state.edges.map((e) => {
    const [x1, y1] = positions[e.a], [x2, y2] = positions[e.b];
    const cls = ["road", e.closed ? "closed" : "", e.slow > 1 ? "slow" : "", isSelected("road", e.id) ? "selected" : ""].join(" ");
    const tip = `${e.street || "Road"} (edge ${e.id}) - ${e.minutes} min${e.slow > 1 ? `, traffic x${e.slow}` : ""}${e.closed ? " - CLOSED" : ""}`;
    const xy = `x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"`;
    return `<line class="${cls}" ${xy}/><line class="road-hit" ${xy} data-kind="road" data-id="${esc(e.id)}" data-tip="${esc(tip)}"/>`;
  }).join("");

  // Remaining route of each moving vehicle, starting from where the vehicle is now.
  const vehicles = byId(state.vehicles);
  $("#layer-routes").innerHTML = !ui.routes ? "" : state.trips.map((t) => {
    if (t.status !== "en_route" || !vehicles[String(t.vehicle)]) return "";
    const points = [vehiclePoint(vehicles[String(t.vehicle)]), ...t.path.slice(1).map((n) => positions[n])];
    if (points.length < 2) return "";
    const focus = selection && (selection.kind === "vehicle" || selection.kind === "emergency");
    const mine = isSelected("vehicle", t.vehicle) || isSelected("emergency", t.emergency);
    const dim = focus && !mine ? " dim" : "";
    const d = points.map((p) => p.join(",")).join(" ");
    const colour = routeColour(t.vehicle);
    return `<polyline class="route under${dim}" stroke="${colour}" points="${d}"/><polyline class="route${dim}" stroke="${colour}" points="${d}"/>`;
  }).join("");

  // Intersections: tiny dots (with a bigger invisible circle that is easy to click);
  // closed ones get a red ring with a cross.
  $("#layer-nodes").innerHTML = state.nodes.map((n) => {
    const [x, y] = positions[n.id];
    const attrs = `data-kind="node" data-id="${esc(n.id)}" data-tip="${esc(n.name)} (node ${esc(n.id)})${n.closed ? " - CLOSED" : ""}"`;
    if (!n.closed) {
      return `<g ${attrs}><circle class="node-dot${isSelected("node", n.id) ? " selected" : ""}" cx="${x}" cy="${y}" r="${isSelected("node", n.id) ? 6 : 2.6}"/>`
        + `<circle class="node-hit" cx="${x}" cy="${y}" r="7"/></g>`;
    }
    return `<g ${attrs}><circle class="node-closed" cx="${x}" cy="${y}" r="9"/>`
      + `<path d="M${x - 4} ${y - 4}l8 8M${x + 4} ${y - 4}l-8 8" stroke="var(--bad)" stroke-width="2.2" stroke-linecap="round"/></g>`;
  }).join("");

  // Named places (hospitals, shelters...) that are not hubs.
  const hubIds = new Set(state.hubs.map((h) => String(h.id)));
  $("#layer-places").innerHTML = state.places.filter((p) => !hubIds.has(String(p.id))).map((p) => {
    const [x, y] = positions[p.node];
    return `<g class="place${isSelected("place", p.id) ? " selected" : ""}" data-kind="place" data-id="${esc(p.id)}" data-tip="${esc(p.name)} (${esc(p.kind)})">`
      + `<circle cx="${x}" cy="${y}" r="7"/><text class="map-label small" x="${x - 6}" y="${y + 21}">${esc(p.id)}</text></g>`;
  }).join("");

  // Food hubs with their stock.
  $("#layer-hubs").innerHTML = state.hubs.map((h) => {
    const [x, y] = positions[h.node];
    return `<g class="hub${isSelected("hub", h.id) ? " selected" : ""}" data-kind="hub" data-id="${esc(h.id)}" data-tip="${esc(h.name)}: ${h.normal} normal / ${h.cold} cold">`
      + `<rect x="${x - 13}" y="${y - 13}" width="26" height="26" rx="8"/>`
      + `<path d="M${x - 6} ${y + 6}V${y - 1}l6-4.5 6 4.5v7"/>`
      + `<text class="map-label" x="${x + 17}" y="${y - 10}">${esc(h.id)} &middot; ${h.normal}N / ${h.cold}C</text></g>`;
  }).join("");

  // Emergencies: a pin per request, fanned out when several share a place.
  const atNode = {};
  state.emergencies.forEach((e) => (atNode[e.node] = atNode[e.node] || []).push(e));
  const pins = [];
  Object.values(atNode).forEach((list) => list.forEach((e, k) => {
    const [nx, ny] = positions[e.node];
    const angle = (k / list.length) * 2 * Math.PI - Math.PI / 2;
    const r = list.length > 1 ? 16 : 0;
    const x = nx + r * Math.cos(angle), y = ny + r * Math.sin(angle);
    const cls = `site ${STATE_CLASS[e.state]}${TERMINAL.has(e.state) ? " done" : ""}${isSelected("emergency", e.id) ? " selected" : ""}`;
    const text = String(e.id).length <= 3 ? esc(e.id) : "";
    pins.push(`<g class="${cls}" data-kind="emergency" data-id="${esc(e.id)}" data-tip="${esc(e.id)}: ${esc(e.state)} - ${e.delivered}/${e.requested} units">`
      + (URGENT.has(e.state) ? `<circle class="pulse" cx="${x}" cy="${y}" r="12"/>` : "")
      + `<circle class="pin" cx="${x}" cy="${y}" r="12"/><text class="pin-text" x="${x}" y="${y + 3.5}">${text}</text>`
      + (text ? "" : `<text class="map-label" x="${x + 15}" y="${y - 10}">${esc(e.id)}</text>`) + `</g>`);
  }));
  $("#layer-sites").innerHTML = pins.join("");
  renderVehicles();
}

/** Vehicles are kept as the SAME elements between refreshes, so CSS can glide
    them smoothly from their old position to the new one. */
function renderVehicles() {
  const layer = $("#layer-vehicles");
  const standing = (v) => v.f === 0 && same(v.from, v.to);
  const total = {};  // how many vehicles stand at each node
  state.vehicles.forEach((v) => { if (standing(v)) total[v.from] = (total[v.from] || 0) + 1; });
  const placed = {};
  const seen = new Set();
  state.vehicles.forEach((v) => {
    let [x, y] = vehiclePoint(v);
    if (standing(v)) {  // standing still: line them up in a neat row under the node
      const k = (placed[v.from] = (placed[v.from] || 0) + 1) - 1;
      x += (k - (total[v.from] - 1) / 2) * 15;
      y += 26;
    }
    const key = String(v.id);
    let g = [...layer.children].find((el) => el.dataset.vid === key);
    if (!g) {
      g = document.createElementNS(SVGNS, "g");
      g.dataset.vid = key;
      g.dataset.kind = "vehicle";
      g.dataset.id = key;
      g.innerHTML = '<circle class="reefer" r="10.5"/><circle class="body" r="7"/><text class="map-label veh-label" x="12" y="-9"></text>';
      layer.appendChild(g);
    }
    g.setAttribute("class", `veh ${v.status}${isSelected("vehicle", v.id) ? " selected" : ""}`);
    g.querySelector(".reefer").style.display = v.refrigerated ? "" : "none";
    g.querySelector(".veh-label").textContent = v.id;
    const trip = v.trip ? ` -> ${v.trip.emergency} (${v.trip.load})${v.trip.eta ? `, ETA ${v.trip.eta}` : ""}` : "";
    g.dataset.tip = `${v.id} ${v.name}${v.refrigerated ? " (refrigerated)" : ""} - ${v.status}${trip}`;
    g.style.transform = `translate(${x}px, ${y}px)`;
    seen.add(key);
  });
  [...layer.children].forEach((g) => { if (!seen.has(g.dataset.vid)) g.remove(); });
}

// ====================================================================== 7. side lists
const TAB_LABELS = { queue: "Queue", emergencies: "Emergencies", fleet: "Fleet", hubs: "Hubs" };
function renderTabs() {
  const counts = { queue: state.queue.length, emergencies: state.emergencies.length,
                   fleet: state.vehicles.length, hubs: state.hubs.length };
  document.querySelectorAll("#tabs button").forEach((b) => {
    const tab = b.dataset.tab;
    b.setAttribute("aria-selected", tab === activeTab);
    b.innerHTML = `${TAB_LABELS[tab]}<span class="count">${counts[tab]}</span>`;
  });
  const html = { queue: queueHtml, emergencies: emergenciesHtml, fleet: fleetHtml, hubs: hubsHtml }[activeTab]();
  $("#tab-body").innerHTML = html;
}
function empty(iconName, title, text) {
  return `<div class="empty">${icon(iconName)}<b>${title}</b><div>${text}</div></div>`;
}
/** "due in 12 min", "overdue 5 min" or "delivered 09:22" for an emergency. */
function dueText(e) {
  if (e.delivered_at) return { text: `delivered ${e.delivered_at}`, cls: "" };
  if (TERMINAL.has(e.state)) return { text: "closed", cls: "" };
  const left = Math.round(e.deadline_min - state.now);
  if (left < 0) return { text: `overdue ${-left} min`, cls: "late" };
  if (left <= 20) return { text: `due in ${left} min`, cls: "soon" };
  return { text: `due ${e.deadline}`, cls: "" };
}
/** Short note next to the deadline in the drawer: "in 42 min", "12 min ago", "met". */
function deadlineNote(e) {
  if (e.delivered_at) return e.delivered_at <= e.deadline ? "met" : "missed";
  const left = Math.round(e.deadline_min - state.now);
  return left >= 0 ? `in ${left} min` : `${-left} min ago`;
}
function queueHtml() {
  if (!state.queue.length) {
    return empty("check", "Nothing waiting", "Every request already has vehicles assigned, or is finished.");
  }
  const emergencies = byId(state.emergencies);
  return state.queue.map((q, i) => {
    const e = emergencies[String(q.id)];
    const due = dueText(e);
    return `<div class="row${isSelected("emergency", e.id) ? " selected" : ""}" data-kind="emergency" data-id="${esc(e.id)}">
      <div class="rank${i === 0 ? " first" : ""}">${i + 1}</div>
      <div class="row-main">
        <div class="row-title">${esc(e.id)} <span class="name">${esc(e.label)}</span></div>
        <div class="bar thin s-prioritized"><span style="width:${clamp(q.priority, 0, 100)}%"></span></div>
        <div class="row-sub">${badge(e.state)}<span>waiting ${esc(e.unassigned)}</span></div>
      </div>
      <div class="row-side"><div class="prio">${q.priority.toFixed(1)}</div><div class="due ${due.cls}">${due.text}</div></div>
    </div>`;
  }).join("") + '<p class="muted" style="font-size:12px;padding:4px 10px">Highest priority first. Ties: earlier submission, then more people, then id.</p>';
}
function emergenciesHtml() {
  if (!state.emergencies.length) return empty("pin", "No emergencies yet", "Press Play, or add one with New emergency.");
  const open = state.emergencies.filter((e) => !TERMINAL.has(e.state)).sort((a, b) => b.priority - a.priority);
  const closed = state.emergencies.filter((e) => TERMINAL.has(e.state));
  return [...open, ...closed].map((e) => {
    const due = dueText(e);
    return `<div class="row${isSelected("emergency", e.id) ? " selected" : ""}" data-kind="emergency" data-id="${esc(e.id)}">
      <div class="veh-icon ${STATE_CLASS[e.state]}" style="color:var(--c);background:color-mix(in srgb, var(--c) 14%, transparent)">${icon("pin")}</div>
      <div class="row-main">
        <div class="row-title">${esc(e.id)} <span class="name">${esc(e.label)}</span></div>
        <div class="bar thin ${STATE_CLASS[e.state]}"><span style="width:${pct(e.delivered, e.requested)}%"></span></div>
        <div class="row-sub">${badge(e.state)}<span>${e.delivered}/${e.requested} units</span></div>
      </div>
      <div class="row-side"><div class="due ${due.cls}">${due.text}</div></div>
    </div>`;
  }).join("");
}
function fleetHtml() {
  return state.vehicles.map((v) => {
    const status = { idle: "Idle", busy: "On a trip", broken: "Broken down" }[v.status] || v.status;
    const trip = v.trip ? `&rarr; ${esc(v.trip.emergency)} &middot; ${esc(v.trip.load)}${v.trip.eta ? ` &middot; ETA ${esc(v.trip.eta)}` : ""}` : "";
    return `<div class="row${isSelected("vehicle", v.id) ? " selected" : ""}" data-kind="vehicle" data-id="${esc(v.id)}">
      <div class="veh-icon ${v.status}">${icon(v.status === "broken" ? "alert" : "truck")}</div>
      <div class="row-main">
        <div class="row-title">${esc(v.id)} <span class="name">${esc(v.name)}</span>${v.refrigerated ? `<span class="tag">${icon("snow")}cold</span>` : ""}</div>
        <div class="row-sub"><span>${status}</span>${trip ? `<span>${trip}</span>` : ""}</div>
      </div>
      <div class="row-side">fuel ${Math.round(pct(v.fuel, v.max_fuel))}%<div class="bar thin" style="width:60px;margin-top:4px"><span style="width:${pct(v.fuel, v.max_fuel)}%"></span></div></div>
    </div>`;
  }).join("");
}
function stockLine(label, left, start, cold) {
  return `<div class="stock-line"><span>${label}</span><div class="bar${cold ? " cold" : ""}"><span style="width:${pct(left, Math.max(start, left))}%"></span></div><span class="num" style="text-align:right">${left} / ${start}</span></div>`;
}
function hubsHtml() {
  if (!state.hubs.length) return empty("hub", "No hubs", "This dataset has no supply hubs.");
  return state.hubs.map((h) => `<div class="hub-card${isSelected("hub", h.id) ? " selected" : ""}" data-kind="hub" data-id="${esc(h.id)}">
      <div class="row-title">${icon("hub")} ${esc(h.id)} <span class="name">${esc(h.name)}</span></div>
      ${stockLine("Normal", h.normal, h.initial_normal, false)}${stockLine("Cold", h.cold, h.initial_cold, true)}
    </div>`).join("");
}

// ====================================================================== 8. details drawer
function select(next) {
  selection = next;
  render();
}
function renderDrawer() {
  const drawer = $("#drawer");
  const detail = selection ? detailFor(selection) : null;
  if (!detail) {
    selection = null;
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    return;
  }
  $("#drawer-title").innerHTML = detail.title;
  $("#drawer-body").innerHTML = detail.body;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}
function detailFor(sel) {
  const find = (list) => list.find((item) => same(item.id, sel.id));
  if (sel.kind === "emergency") { const e = find(state.emergencies); return e && emergencyDetail(e); }
  if (sel.kind === "vehicle") { const v = find(state.vehicles); return v && vehicleDetail(v); }
  if (sel.kind === "hub") { const h = find(state.hubs); return h && hubDetail(h); }
  if (sel.kind === "road") { const r = find(state.edges); return r && roadDetail(r); }
  if (sel.kind === "place") { const p = find(state.places); return p && placeDetail(p); }
  if (sel.kind === "node") { const n = find(state.nodes); return n && nodeDetail(n); }
  return null;
}
const fact = (k, v) => `<div class="fact"><div class="k">${k}</div><div class="v">${v}</div></div>`;
const section = (title, html) => `<div><div class="section-title">${title}</div>${html}</div>`;
const nodeName = (id) => (state.nodes.find((n) => same(n.id, id)) || { name: String(id) }).name;

function emergencyDetail(e) {
  const due = dueText(e);
  const dots = Array.from({ length: state.options.severity[1] }, (_, i) => `<i class="${i < e.severity ? "on" : ""}"></i>`).join("");
  let callout = "";
  if (e.state === "Failed") callout = `<div class="callout bad">${icon("alert")}<span>${esc(e.last)}</span></div>`;
  else if (URGENT.has(e.state)) callout = `<div class="callout warn">${icon("alert")}<span>${esc(e.waiting_reason || e.last)}</span></div>`;
  else if (e.state === "Resolved" || e.state === "Delivered") callout = `<div class="callout ok">${icon("check")}<span>Delivered at ${esc(e.delivered_at)} (deadline ${esc(e.deadline)})</span></div>`;

  // Lifecycle stepper along the normal path; side states show as the badge.
  const path = ["Open", "Prioritized", "Resource Assigned", "In Progress", "Delivered", "Resolved"];
  const reached = new Set(e.history.filter((h) => !h.note).map((h) => h.to));
  const stepper = `<div class="stepper ${STATE_CLASS[e.state]}">${path.map((s) => {
    const now = s === e.state;
    const done = reached.has(s) && !now;
    return `<div class="step${done ? " done" : ""}${now ? " now" : ""}">${esc(s)}</div>`;
  }).join("")}</div>`;

  const factors = ["severity", "people", "deadline", "shortage", "location", "travel"]
    .filter((name) => e.breakdown[name])
    .map((name) => {
      const f = e.breakdown[name];
      return `<div class="factor"><span>${name}</span><div class="bar thin"><span style="width:${f.factor * 100}%"></span></div><span class="pts">${f.points.toFixed(1)}</span></div>`;
    }).join("");
  const scoring = `${factors}<div class="factor"><span>waiting</span><div class="bar thin s-prioritized"><span style="width:${clamp(e.aging, 0, 100)}%"></span></div><span class="pts">+${e.aging.toFixed(1)}</span></div>
    <div class="total-line"><span>Priority</span><span class="num">${e.priority.toFixed(1)}</span></div>`;

  const trips = e.trips.length ? `<table class="mini-table"><tr><th>Trip</th><th>Vehicle</th><th>Load</th><th>From</th><th>Status</th><th>ETA</th></tr>${e.trips.map((t) =>
    `<tr><td>${esc(t.id)}</td><td><a href="#" data-action="select" data-kind="vehicle" data-id="${esc(t.vehicle)}">${esc(t.vehicle)}</a></td><td>${esc(t.load)}</td><td>${esc(t.source)}</td><td>${esc(nice(t.status))}</td><td class="num">${esc(t.eta)}</td></tr>`).join("")}</table>`
    : '<div class="muted">No vehicles assigned yet.</div>';

  const timeline = `<ol class="timeline">${e.history.map((h) => `<li class="${h.note ? "note" : STATE_CLASS[h.to] || ""}">
      <div class="when">${esc(h.time)}</div>
      <div class="what">${h.note ? "Note" : `${esc(h.from || "New")} &rarr; ${esc(h.to)}`}</div>
      <div class="why">${esc(h.text)}</div></li>`).join("")}</ol>`;

  return {
    title: `<div class="drawer-title">${esc(e.id)} ${badge(e.state)}</div><div class="drawer-sub">${esc(e.label)}</div>`,
    body: callout
      + section("Request", `<div class="facts">${fact("Crisis", esc(nice(e.crisis_type)))}${fact("Location type", esc(nice(e.category)))}`
        + `${fact("People", e.people)}${fact("Severity", `<span class="dots">${dots}</span> ${e.severity}`)}`
        + `${fact("Submitted", esc(e.submitted))}${fact("Deadline", `${esc(e.deadline)} <span class="due ${due.cls}" style="text-transform:none">(${deadlineNote(e)})</span>`)}</div>`)
      + section("Delivery", stockLine("Normal", e.delivered_normal, e.need_normal, false) + stockLine("Cold", e.delivered_cold, e.need_cold, true))
      + section("Lifecycle", stepper)
      + section("Why this priority", scoring)
      + section("Vehicles", trips)
      + section("Timeline", timeline)
      + `<div class="actions"><button class="btn small" data-action="locate" data-node="${esc(e.node)}">${icon("target")}Show on map</button></div>`,
  };
}

function vehicleDetail(v) {
  const status = { idle: "Idle", busy: "On a trip", broken: "Broken down" }[v.status] || v.status;
  const trip = v.trip ? section("Current trip", `<div class="facts">${fact("For", `<a href="#" data-action="select" data-kind="emergency" data-id="${esc(v.trip.emergency)}">${esc(v.trip.emergency)}</a>`)}`
      + `${fact("Load", esc(v.trip.load))}${fact("Phase", v.trip.loaded ? "Driving to site" : "Going to load")}${fact("ETA", esc(v.trip.eta || "waiting for a road"))}</div>`) : "";
  const action = v.status === "broken"
    ? `<button class="btn small" data-action="disrupt" data-type="vehicle_repair" data-vehicle="${esc(v.id)}">${icon("wrench")}Repair</button>`
    : `<button class="btn small btn-danger" data-action="disrupt" data-type="vehicle_breakdown" data-vehicle="${esc(v.id)}">${icon("alert")}Report breakdown</button>`;
  return {
    title: `<div class="drawer-title">${icon("truck")} ${esc(v.id)}</div><div class="drawer-sub">${esc(v.name)} &middot; ${status}</div>`,
    body: section("Vehicle", `<div class="facts">${fact("Type", v.refrigerated ? "Refrigerated" : "Standard")}${fact("Capacity", `${v.capacity} units`)}`
        + `${fact("Location", esc(nodeName(v.f > 0.5 ? v.to : v.from)))}${fact("Home", esc(nodeName(v.home)))}</div>`)
      + section("Fuel", stockLine("Fuel", Math.round(v.fuel), Math.round(v.max_fuel), false))
      + trip
      + section("Actions", `<div class="actions">${action}</div>`),
  };
}

function hubDetail(h) {
  return {
    title: `<div class="drawer-title">${icon("hub")} ${esc(h.id)}</div><div class="drawer-sub">${esc(h.name)}</div>`,
    body: section("Stock", stockLine("Normal", h.normal, h.initial_normal, false) + stockLine("Cold", h.cold, h.initial_cold, true))
      + section("Restock", `<div class="actions">
          <button class="btn small" data-action="disrupt" data-type="restock" data-hub="${esc(h.id)}" data-normal="50" data-cold="0">${icon("plus")}50 normal</button>
          <button class="btn small" data-action="disrupt" data-type="restock" data-hub="${esc(h.id)}" data-normal="0" data-cold="20">${icon("snow")}20 cold</button>
          <button class="btn small" data-action="city" data-open="restock" data-target="${esc(h.id)}">${icon("sliders")}Other amounts...</button></div>`)
      + `<div class="actions"><button class="btn small" data-action="locate" data-node="${esc(h.node)}">${icon("target")}Show on map</button></div>`,
  };
}

function roadDetail(r) {
  const status = r.closed ? "Closed" : r.slow > 1 ? `Traffic x${r.slow}` : "Open";
  return {
    title: `<div class="drawer-title">${icon("road")} ${esc(r.street || "Road")}</div><div class="drawer-sub">Edge ${esc(r.id)} &middot; ${status}</div>`,
    body: section("Road", `<div class="facts">${fact("From", esc(nodeName(r.a)))}${fact("To", esc(nodeName(r.b)))}`
        + `${fact("Length", `${r.km} km`)}${fact("Travel time now", `${r.minutes} min`)}</div>`)
      + section("Actions", `<div class="actions">
          <button class="btn small${r.closed ? "" : " btn-danger"}" data-action="road" data-edge="${esc(r.id)}">${icon(r.closed ? "check" : "x")}${r.closed ? "Reopen road" : "Close road"}</button>
          ${r.slow > 1
            ? `<button class="btn small" data-action="disrupt" data-type="traffic" data-edge="${esc(r.id)}" data-factor="1">${icon("check")}Clear traffic</button>`
            : `<button class="btn small" data-action="disrupt" data-type="traffic" data-edge="${esc(r.id)}" data-factor="2.5">${icon("alert")}Traffic jam (x2.5)</button>`}
          <button class="btn small" data-action="city" data-open="traffic" data-target="${esc(r.id)}">${icon("sliders")}Set traffic level...</button>
        </div><p class="muted" style="font-size:12.5px">Closing a road on a vehicle's route sends that request into Re-Routing; a jam only slows it down.</p>`),
  };
}

function placeDetail(p) {
  const here = state.emergencies.filter((e) => same(e.node, p.node));
  const list = here.length ? here.map((e) => `<div class="row" data-kind="emergency" data-id="${esc(e.id)}"><div class="veh-icon">${icon("pin")}</div>
      <div class="row-main"><div class="row-title">${esc(e.id)}</div><div class="row-sub">${badge(e.state)}<span>${e.delivered}/${e.requested} units</span></div></div><div></div></div>`).join("")
    : '<div class="muted">No requests at this place.</div>';
  return {
    title: `<div class="drawer-title">${icon("pin")} ${esc(p.id)}</div><div class="drawer-sub">${esc(p.name)}</div>`,
    body: section("Place", `<div class="facts">${fact("Type", esc(nice(p.kind)))}${fact("Address", esc(p.address || "-"))}</div>`)
      + section("Requests here", list)
      + `<div class="actions"><button class="btn small btn-accent" data-action="new-here" data-location="${esc(p.id)}">${icon("plus")}New emergency here</button></div>`,
  };
}

function nodeDetail(n) {
  const roads = state.edges.filter((e) => same(e.a, n.id) || same(e.b, n.id));
  const list = roads.map((r) => `<div class="row" data-kind="road" data-id="${esc(r.id)}"><div class="veh-icon">${icon("road")}</div>
      <div class="row-main"><div class="row-title">${esc(r.street || "Road")} <span class="name">edge ${esc(r.id)}</span></div>
      <div class="row-sub"><span>to ${esc(nodeName(same(r.a, n.id) ? r.b : r.a))}</span><span>${r.minutes} min</span>${r.closed ? '<span class="bad-text">closed</span>' : ""}</div></div><div></div></div>`).join("");
  return {
    title: `<div class="drawer-title">${icon("node")} ${esc(n.name)}</div><div class="drawer-sub">Intersection (node ${esc(n.id)}) &middot; ${n.closed ? "Closed" : "Open"}</div>`,
    body: section("Intersection", `<div class="facts">${fact("Node id", esc(n.id))}${fact("Roads", roads.length)}`
        + `${fact("Latitude", n.lat ?? "-")}${fact("Longitude", n.lng ?? "-")}</div>`)
      + section("Roads from here", list || '<div class="muted">No roads.</div>')
      + section("Actions", `<div class="actions">
          <button class="btn small${n.closed ? "" : " btn-danger"}" data-action="disrupt" data-type="${n.closed ? "node_reopen" : "node_closure"}" data-node="${esc(n.id)}">${icon(n.closed ? "check" : "x")}${n.closed ? "Reopen intersection" : "Close intersection"}</button>
          <button class="btn small btn-accent" data-action="new-here" data-location="${esc(n.id)}">${icon("plus")}New emergency here</button></div>`),
  };
}

// ====================================================================== 9. live feed + toasts
const EVENT_STYLE = {  // event type -> [colour class, short label, icon]
  "crisis-created": ["k-request", "New request", "plus"],
  "state-changed": ["k-state", "State", "activity"],
  "crisis-resolved": ["k-good", "Resolved", "check"],
  "route-updated": ["k-route", "Route", "truck"],
  "priority-changed": ["k-prio", "Priority", "activity"],
  "road-closed": ["k-bad", "Road closed", "road"],
  "road-reopened": ["k-good", "Road open", "road"],
  "vehicle-breakdown": ["k-bad", "Breakdown", "alert"],
  "vehicle-repaired": ["k-good", "Repaired", "wrench"],
  "resource-depleted": ["k-warn", "Stock out", "box"],
  "resource-restocked": ["k-good", "Restock", "box"],
  "conditions-changed": ["k-cond", "Conditions", "cloud"],
};
const FEED_GROUPS = {
  all: null,
  requests: ["crisis-created", "state-changed", "crisis-resolved"],
  routes: ["route-updated"],
  disruptions: ["road-closed", "road-reopened", "vehicle-breakdown", "vehicle-repaired",
                "resource-depleted", "resource-restocked", "conditions-changed"],
  priority: ["priority-changed"],
};
function eventText(ev) {
  const p = ev.payload || {};
  if (ev.type === "state-changed") return `${p.id}: ${p.from} → ${p.to} — ${p.explanation}`;
  if (ev.type === "crisis-created") return p.summary;
  if (ev.type === "crisis-resolved") return `${p.id} resolved (${p.delivered_normal} normal + ${p.delivered_cold} cold delivered)`;
  return p.message || ev.line;
}
function eventHtml(ev, fresh) {
  const [kind, label, iconName] = EVENT_STYLE[ev.type] || ["", ev.type, "activity"];
  const colour = ev.type === "state-changed" ? STATE_CLASS[ev.payload.to] || kind : kind;
  return `<li class="ev${fresh ? " fresh" : ""}"><time>${esc(ev.time)}</time>`
    + `<span class="ev-type ${colour}">${icon(iconName)}${esc(label)}</span><span class="ev-text">${esc(eventText(ev))}</span></li>`;
}
const passesFilter = (ev) => !FEED_GROUPS[feedFilter] || FEED_GROUPS[feedFilter].includes(ev.type);
function renderFeed() {
  const shown = feedEvents.filter(passesFilter).slice(-300).reverse();  // newest first
  $("#feed").innerHTML = shown.length ? shown.map((ev) => eventHtml(ev, false)).join("")
    : `<li class="empty">${icon("activity")}Events appear here as they happen.</li>`;
}
function addEvent(ev) {
  feedEvents.push(ev);
  if (feedEvents.length > 1000) feedEvents.shift();
  if (!passesFilter(ev)) return;
  const feed = $("#feed");
  if (feed.querySelector(".empty")) feed.innerHTML = "";
  feed.insertAdjacentHTML("afterbegin", eventHtml(ev, true));
  while (feed.children.length > 300) feed.lastElementChild.remove();
}

let pendingToasts = [];
let toastTimer = null;
/** Collect toasts for a moment: a burst (e.g. "Run to end") becomes ONE summary toast. */
function toast(title, text, kind = "k-request", iconName = "activity", onClick = null) {
  pendingToasts.push({ title, text, kind, iconName, onClick });
  if (!toastTimer) toastTimer = setTimeout(flushToasts, 350);
}
function flushToasts() {
  toastTimer = null;
  const batch = pendingToasts;
  pendingToasts = [];
  if (batch.length > 3) {
    showToast({ title: `${batch.length} updates`, text: "See Live activity for the details.", kind: "k-request", iconName: "activity" });
  } else {
    batch.forEach(showToast);
  }
}
function showToast(t) {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${t.kind}`;
  el.innerHTML = `${icon(t.iconName)}<div><b>${esc(t.title)}</b>${esc(t.text || "")}</div>`;
  el.addEventListener("click", () => { if (t.onClick) t.onClick(); el.remove(); });
  box.appendChild(el);
  while (box.children.length > 3) box.firstElementChild.remove();  // never more than 3 on screen
  setTimeout(() => { el.classList.add("leaving"); setTimeout(() => el.remove(), 300); }, 4000);
}
/** Only genuinely important, NEW events pop up. */
function maybeToast(ev) {
  if (ev.seq <= toastAfterSeq) return;
  const p = ev.payload || {};
  const show = (kind, id) => () => select({ kind, id });
  if (ev.type === "crisis-created") toast(`New request ${p.id}`, p.summary, "k-request", "plus", show("emergency", p.id));
  else if (ev.type === "state-changed" && p.to === "Failed") toast(`${p.id} failed`, p.explanation, "k-bad", "alert", show("emergency", p.id));
  else if (ev.type === "state-changed" && p.to === "Delivered") toast(`${p.id} delivered`, p.explanation, "k-good", "check", show("emergency", p.id));
  else if (ev.type === "state-changed" && p.to === "Re-Routing") toast(`${p.id} re-routing`, p.explanation, "k-warn", "road", show("emergency", p.id));
  else if (ev.type === "vehicle-breakdown") toast("Vehicle breakdown", p.message, "k-bad", "alert", show("vehicle", p.vehicle));
  else if (ev.type === "resource-depleted") toast("Hub out of stock", p.message, "k-warn", "box", show("hub", p.hub));
  else if (ev.type === "conditions-changed" && p.kind === "city") toast("Conditions changed", p.message, "k-cond", "cloud");
}

/** Live connection: every event-bus event arrives here as it happens. */
function connect() {
  const live = $("#live");
  const setLive = (on, text) => { live.classList.toggle("on", on); live.querySelector(".live-text").textContent = text; };
  const source = new EventSource(`/events?since=${lastSeq}`);
  source.onopen = () => setLive(true, "Live");
  source.onerror = () => setLive(false, "Reconnecting");
  source.addEventListener("bus", (msg) => {
    const ev = JSON.parse(msg.data);
    lastSeq = ev.seq;
    addEvent(ev);
    maybeToast(ev);
    queueRefresh();
  });
  source.addEventListener("clock", () => queueRefresh());  // the live clock moved
  source.addEventListener("reset", () => {  // the scenario was restarted
    feedEvents = [];
    lastSeq = 0;
    renderFeed();
    queueRefresh();
  });
}

// ====================================================================== 10. add-emergency form
function openModal(location = "") {
  const form = $("#add-form");
  const o = state.options;
  $("#crisis-type").innerHTML = o.crisis_types.map((t) => `<option value="${esc(t)}">${esc(nice(t))}</option>`).join("");
  $("#category").innerHTML = '<option value="">Same as the place</option>'
    + o.categories.map((c) => `<option value="${esc(c)}">${esc(nice(c))}</option>`).join("");
  $("#places-list").innerHTML = state.places.map((p) => `<option value="${esc(p.id)}">${esc(p.name)} (${esc(p.kind)})</option>`).join("")
    + state.nodes.map((n) => `<option value="${esc(n.id)}">${esc(n.name)}</option>`).join("");
  const [lo, hi] = o.severity;
  const picker = $("#severity-picker");
  picker.innerHTML = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
    .map((s) => `<button type="button" class="sev-${s}" data-sev="${s}" role="radio">${s}</button>`).join("");
  setSeverity(Math.round((lo + hi) / 2));
  const later = Math.round(state.now + 60) % 1440;  // default deadline: one hour from now
  form.deadline.value = `${String(Math.floor(later / 60)).padStart(2, "0")}:${String(later % 60).padStart(2, "0")}`;
  form.location.value = location;
  $("#form-messages").innerHTML = "";
  $("#modal").hidden = false;
  (location ? form.people_affected : form.location).focus();
}
function closeModal() { $("#modal").hidden = true; }
function setSeverity(value) {
  $("#add-form").dataset.severity = value;
  document.querySelectorAll("#severity-picker button").forEach((b) => {
    const on = Number(b.dataset.sev) === value;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", on);
  });
}
async function submitModal(event) {
  event.preventDefault();
  const form = event.target;
  const location = form.location.value.trim();
  const body = {
    location: location.includes(",") ? { lat: location.split(",")[0].trim(), lng: location.split(",")[1].trim() } : location,
    crisis_type: form.crisis_type.value,
    people_affected: form.people_affected.value,
    food_normal: form.food_normal.value || 0,
    food_cold: form.food_cold.value || 0,
    severity: form.dataset.severity,
    deadline: form.deadline.value,
    submitted_at: state.clock,  // submitted "now" on the simulation clock
  };
  if (form.location_category.value) body.location_category = form.location_category.value;
  $("#add-submit").disabled = true;
  try {
    const reply = await post("/emergency", body);
    if (!reply.ok) {
      const items = (list, cls) => (list && list.length ? `<div class="callout ${cls}"><ul>${list.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>` : "");
      $("#form-messages").innerHTML = items(reply.errors || [reply.message], "bad") + items(reply.warnings, "warn");
      return;
    }
    closeModal();
    toast(reply.message, (reply.warnings || []).join("; ") || "Dispatching now.", "k-good", "check");
    selection = { kind: "emergency", id: reply.id };
    await refresh();
  } finally {
    $("#add-submit").disabled = false;
  }
}

// ====================================================================== 11. playback + actions
/** Send one action to the server, then redraw. Ignores clicks while one is in flight. */
async function act(url, body) {
  if (busy) return null;
  busy = true;
  try {
    const reply = await post(url, body);
    if (!reply.ok) toast("Not done", reply.message, "k-warn", "alert");
    await refresh();
    return reply;
  } finally {
    busy = false;
  }
}
/** Start or stop the SERVER's live clock (every open tab follows it). */
const setPlaying = (on) => act(`/play?on=${on ? 1 : 0}`);
const setSpeed = (key) => act(`/play?speed=${encodeURIComponent(key)}`);
/** Reset asks for a SECOND click instead of a pop-up: embedded browsers and
    kiosk/projector setups often block window.confirm(), which then silently
    answers "no" and the button would seem broken. */
let resetTimer = null;
function onResetClick() {
  const button = $("#reset-btn");
  if (resetTimer === null) {  // first click: ask for confirmation for 4 seconds
    button.classList.add("btn-danger");
    button.innerHTML = `${icon("reset")}<span>Confirm?</span>`;  // short, so the button never moves
    resetTimer = setTimeout(disarmReset, 4000);
    return;
  }
  disarmReset();  // second click: do it
  selection = null;
  act("/reset");
}
function disarmReset() {
  clearTimeout(resetTimer);
  resetTimer = null;
  const button = $("#reset-btn");
  button.classList.remove("btn-danger");
  button.innerHTML = `${icon("reset")}<span>Reset</span>`;
}
/** Buttons inside the drawer and lists carry data-action attributes. */
function handleAction(el) {
  const d = el.dataset;
  if (d.action === "select") select({ kind: d.kind, id: d.id });
  else if (d.action === "locate") focusNode(state.nodes.find((n) => same(n.id, d.node)).id);
  else if (d.action === "new-here") openModal(d.location);
  else if (d.action === "city") openCity(d.open, d.target);
  else if (d.action === "road") act(`/road?edge=${encodeURIComponent(d.edge)}`);
  else if (d.action === "disrupt") {
    const body = { type: d.type };
    ["vehicle", "hub", "edge", "node", "normal", "cold", "factor"].forEach((key) => { if (d[key] !== undefined) body[key] = d[key]; });
    act("/disrupt", body);
  }
}

// ====================================================================== 12. input handling
function initMapInput() {
  const svg = $("#map");
  let drag = null;
  const unitsPerPixel = () => Math.max(view.w / svg.clientWidth, view.h / svg.clientHeight);
  svg.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, start: { ...view }, moved: false, target: e.target };
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;  // a small wobble is still a click
    drag.moved = true;
    svg.classList.add("panning");
    const k = unitsPerPixel();
    view = { ...drag.start, x: drag.start.x - dx * k, y: drag.start.y - dy * k };
    applyView();
  });
  svg.addEventListener("pointerup", () => {
    if (drag && !drag.moved) {  // it was a click: select what was under the pointer
      const el = drag.target.closest("[data-kind]");
      select(el ? { kind: el.dataset.kind, id: el.dataset.id } : null);
    }
    drag = null;
    svg.classList.remove("panning");
  });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const point = svg.createSVGPoint();
    point.x = e.clientX;
    point.y = e.clientY;
    const p = point.matrixTransform(svg.getScreenCTM().inverse());
    zoomAt(e.deltaY > 0 ? 1.15 : 1 / 1.15, p.x, p.y);
  }, { passive: false });
}

function initTooltip() {
  const tip = $("#tooltip");
  document.addEventListener("pointermove", (e) => {
    const el = e.target.closest ? e.target.closest("[data-tip]") : null;
    if (!el || e.buttons) { tip.classList.remove("on"); return; }
    tip.textContent = el.dataset.tip;
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    const y = Math.min(e.clientY + 16, window.innerHeight - tip.offsetHeight - 8);
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
    tip.classList.add("on");
  });
  document.addEventListener("pointerleave", () => tip.classList.remove("on"));
}

function currentTheme() {
  return document.documentElement.dataset.theme
    || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function initTheme() {
  try {
    const saved = localStorage.getItem("crisis-theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (err) { /* storage can be blocked; the computer's setting is used instead */ }
  const button = $("#theme-btn");
  const paint = () => { button.innerHTML = icon(currentTheme() === "dark" ? "sun" : "moon"); };
  paint();
  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("crisis-theme", next); } catch (err) { /* not saved, still switched */ }
    paint();
  });
}

function initControls() {
  $("#run-btn").innerHTML = `${icon("forward")}<span>Run to end</span>`;
  $("#reset-btn").innerHTML = `${icon("reset")}<span>Reset</span>`;
  $("#add-btn").innerHTML = `${icon("plus")}<span>New emergency</span>`;
  $("#zoom-in").innerHTML = icon("zoomIn");
  $("#zoom-out").innerHTML = icon("zoomOut");
  $("#zoom-fit").innerHTML = icon("fit");
  $("#drawer-close").innerHTML = icon("x");
  $("#add-close").innerHTML = icon("x");
  $("#legend").innerHTML = [
    '<span><i class="sw sq" style="--c:var(--hub)"></i>Food hub</span>',
    '<span><i class="sw s-progress"></i>Request (colour = state)</span>',
    '<span><i class="sw" style="--c:var(--vehicle)"></i>Truck on a trip</span>',
    '<span><i class="sw" style="--c:var(--faint)"></i>Idle truck</span>',
    '<span><i class="sw" style="background:none;border:2.5px solid var(--cold)"></i>Refrigerated</span>',
    '<span><i class="sw line" style="--c:var(--st-progress)"></i>Route</span>',
    '<span><i class="sw line" style="--c:var(--warn)"></i>Traffic jam</span>',
    '<span><i class="sw dash"></i>Closed</span>',
  ].join("");
  $("#feed-filters").innerHTML = Object.keys(FEED_GROUPS).map((g) =>
    `<button class="chip-toggle${g === feedFilter ? " on" : ""}" data-filter="${g}">${g[0].toUpperCase() + g.slice(1)}</button>`).join("");

  $("#dataset-btn").innerHTML = `${icon("database")}<span>Dataset</span>`;
  $("#events-btn").innerHTML = `${icon("sliders")}<span>City events</span>${icon("chevron")}`;
  $("#city-close").innerHTML = icon("x");
  $("#explorer-close").innerHTML = icon("x");
  $("#explorer-csv").innerHTML = `${icon("download")}<span>CSV</span>`;
  const MENU = { restock: ["box", "Restock a food hub"], traffic: ["alert", "Traffic on a road"],
                 weather: ["cloud", "Change the weather"], road: ["road", "Close or reopen a road"],
                 node: ["node", "Close or reopen an intersection"], vehicle: ["truck", "Vehicle breakdown or repair"] };
  document.querySelectorAll("#events-menu [data-open]").forEach((b) => {
    const [ic, label] = MENU[b.dataset.open];
    b.innerHTML = `${icon(ic)}<span>${label}</span>`;
  });
  initCityForm();
  initExplorer();
  $("#play-btn").addEventListener("click", () => setPlaying(!state.playing));
  $("#step-1").addEventListener("click", () => act("/tick?minutes=1"));
  $("#step-10").addEventListener("click", () => act("/tick?minutes=10"));
  $("#run-btn").addEventListener("click", () => act("/run"));  // the server pauses the live clock first
  $("#reset-btn").addEventListener("click", onResetClick);
  $("#speed").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) setSpeed(b.dataset.speed);
  });
  $("#weather-select").addEventListener("change", (e) => act("/disrupt", { type: "weather", condition: e.target.value }));
  $("#add-btn").addEventListener("click", () => openModal());
  $("#zoom-in").addEventListener("click", () => zoomCenter(1 / 1.25));
  $("#zoom-out").addEventListener("click", () => zoomCenter(1.25));
  $("#zoom-fit").addEventListener("click", fitView);
  $("#toggle-routes").addEventListener("click", (e) => toggleUi("routes", e.currentTarget));
  $("#toggle-labels").addEventListener("click", (e) => toggleUi("labels", e.currentTarget));
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) { activeTab = b.dataset.tab; renderTabs(); }
  });
  $("#feed-filters").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    feedFilter = b.dataset.filter;
    document.querySelectorAll("#feed-filters button").forEach((x) => x.classList.toggle("on", x === b));
    renderFeed();
  });
  // Clicks on list rows, drawer buttons and links (one listener for all of them).
  document.addEventListener("click", (e) => {
    const actionEl = e.target.closest("[data-action]");
    if (actionEl) { e.preventDefault(); handleAction(actionEl); return; }
    const row = e.target.closest(".tab-body [data-kind], .drawer-body [data-kind], .explorer-body [data-kind]");
    if (row) {
      select({ kind: row.dataset.kind, id: row.dataset.id });
      if (row.closest(".explorer-body")) focusSelection();
    }
    if (!e.target.closest(".menu-wrap")) toggleMenu(false);  // click outside closes the menu
  });
  $("#drawer-close").addEventListener("click", () => select(null));
  $("#add-form").addEventListener("submit", submitModal);
  $("#add-cancel").addEventListener("click", closeModal);
  $("#add-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  $("#severity-picker").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) setSeverity(Number(b.dataset.sev));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#modal").hidden) closeModal();
      else if (!$("#city-modal").hidden) closeCity();
      else if (!$("#events-menu").hidden) toggleMenu(false);
      else if (selection) select(null);
      else if (explorerOpen) toggleExplorer(false);
      return;
    }
    const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)
      || !$("#modal").hidden || !$("#city-modal").hidden;
    if (typing || !state) return;
    if (e.key === " ") { e.preventDefault(); setPlaying(!state.playing); }
    else if (e.key === "d" || e.key === "D") toggleExplorer(!explorerOpen);
    else if (e.key === ".") act("/tick?minutes=1");
    else if (e.key === "n" || e.key === "N") { e.preventDefault(); openModal(); }
    else if (e.key === "+" || e.key === "=") zoomCenter(1 / 1.25);
    else if (e.key === "-") zoomCenter(1.25);
  });
}
function toggleUi(key, button) {
  ui[key] = !ui[key];
  button.classList.toggle("on", ui[key]);
  button.setAttribute("aria-pressed", ui[key]);
  renderMap();
}

// ====================================================================== 13. city events form
// One form for every kind of change to the city. It sends exactly what a
// disruptions.json file would contain, so it goes through the same checks.
let cityType = "restock";
function toggleMenu(open) {
  $("#events-menu").hidden = !open;
  $("#events-btn").setAttribute("aria-expanded", open);
}
function roadLabel(e) {
  return `${e.id} · ${e.street || "Road"}: ${nodeName(e.a)} → ${nodeName(e.b)}${e.closed ? " (closed)" : e.slow > 1 ? ` (traffic x${e.slow})` : ""}`;
}
/** Fields for each kind of change. `target` pre-selects a hub / road / node / vehicle. */
function cityFieldsHtml(type, target) {
  const pick = (id) => (target !== undefined && same(id, target) ? " selected" : "");
  if (type === "restock") {
    return `<label class="field span-2"><span>Food hub</span><select name="hub">${state.hubs.map((h) =>
        `<option value="${esc(h.id)}"${pick(h.id)}>${esc(h.id)} · ${esc(h.name)} (now ${h.normal} normal / ${h.cold} cold)</option>`).join("")}</select></label>
      <label class="field"><span>Normal food to add</span><input type="number" name="normal" min="0" value="100"></label>
      <label class="field"><span>Cold food to add</span><input type="number" name="cold" min="0" value="0"></label>`;
  }
  if (type === "traffic") {
    const edge = state.edges.find((e) => same(e.id, target));
    return `<label class="field span-2"><span>Road</span><select name="edge">${state.edges.map((e) =>
        `<option value="${esc(e.id)}"${pick(e.id)}>${esc(roadLabel(e))}</option>`).join("")}</select></label>
      <label class="field span-2"><span>Traffic level: how many times slower than normal (1 = clear)</span>
        <div class="range-row"><input type="range" name="factor" min="1" max="5" step="0.5" value="${edge && edge.slow > 1 ? edge.slow : 2.5}">
        <output id="factor-out"></output></div></label>`;
  }
  if (type === "weather") {
    return `<label class="field span-2"><span>Weather now <span class="now">(currently ${esc(nice(state.conditions.weather))})</span></span>
      <select name="condition">${state.options.weather.map((w) =>
        `<option value="${esc(w)}"${w === state.conditions.weather ? " selected" : ""}>${esc(nice(w))}</option>`).join("")}</select>
      <small>Every road becomes slower by the factor set for this weather in the config.</small></label>`;
  }
  const choice = (name, a, b, firstOn) => `<div class="field span-2"><span>Action</span><div class="choice">
      <label><input type="radio" name="${name}" value="${a[0]}"${firstOn ? " checked" : ""}>${a[1]}</label>
      <label><input type="radio" name="${name}" value="${b[0]}"${firstOn ? "" : " checked"}>${b[1]}</label></div></div>`;
  if (type === "road") {
    const edge = state.edges.find((e) => same(e.id, target));
    return `<label class="field span-2"><span>Road</span><select name="edge">${state.edges.map((e) =>
        `<option value="${esc(e.id)}"${pick(e.id)}>${esc(roadLabel(e))}</option>`).join("")}</select></label>`
      + choice("action", ["road_closure", "Close it"], ["road_reopen", "Reopen it"], !(edge && edge.closed));
  }
  if (type === "node") {
    const node = state.nodes.find((n) => same(n.id, target));
    return `<label class="field span-2"><span>Intersection</span><select name="node">${state.nodes.map((n) =>
        `<option value="${esc(n.id)}"${pick(n.id)}>${esc(n.id)} · ${esc(n.name)}${n.closed ? " (closed)" : ""}</option>`).join("")}</select></label>`
      + choice("action", ["node_closure", "Close it"], ["node_reopen", "Reopen it"], !(node && node.closed));
  }
  if (type === "vehicle") {
    const v = state.vehicles.find((x) => same(x.id, target));
    return `<label class="field span-2"><span>Vehicle</span><select name="vehicle">${state.vehicles.map((x) =>
        `<option value="${esc(x.id)}"${pick(x.id)}>${esc(x.id)} · ${esc(x.name)} (${esc(x.status)})</option>`).join("")}</select></label>`
      + choice("action", ["vehicle_breakdown", "Breaks down"], ["vehicle_repair", "Repaired"], !(v && v.status === "broken"));
  }
  return "";
}
function renderCityFields(target) {
  $("#city-fields").innerHTML = cityFieldsHtml(cityType, target);
  const range = $("#city-fields input[type=range]");
  if (range) {
    const out = $("#factor-out");
    const show = () => { out.textContent = Number(range.value) === 1 ? "clear" : `x${range.value}`; };
    range.addEventListener("input", show);
    show();
  }
}
function openCity(type = "restock", target) {
  toggleMenu(false);
  cityType = type;
  $("#city-type").value = type;
  renderCityFields(target);
  $("#city-form").reason.value = "";
  $("#city-messages").innerHTML = "";
  $("#city-modal").hidden = false;
  const first = $("#city-fields select, #city-fields input");
  if (first) first.focus();
}
function closeCity() { $("#city-modal").hidden = true; }
/** Turn the form into a disruption, exactly like one line of disruptions.json. */
function cityBody(form) {
  const value = (name) => (form.querySelector(`[name="${name}"]`) || {}).value;
  const action = (form.querySelector('input[name="action"]:checked') || {}).value;
  const reason = form.reason.value.trim();
  const body = {
    restock: { type: "restock", hub: value("hub"), normal: value("normal") || 0, cold: value("cold") || 0 },
    traffic: { type: "traffic", edge: value("edge"), factor: value("factor") },
    weather: { type: "weather", condition: value("condition") },
    road: { type: action, edge: value("edge") },
    node: { type: action, node: value("node") },
    vehicle: { type: action, vehicle: value("vehicle") },
  }[cityType];
  if (reason) body.reason = reason;
  return body;
}
function initCityForm() {
  $("#events-btn").addEventListener("click", () => toggleMenu($("#events-menu").hidden));
  $("#events-menu").addEventListener("click", (e) => {
    const b = e.target.closest("[data-open]");
    if (b) openCity(b.dataset.open);
  });
  $("#city-type").addEventListener("change", (e) => { cityType = e.target.value; renderCityFields(); });
  $("#city-close").addEventListener("click", closeCity);
  $("#city-cancel").addEventListener("click", closeCity);
  $("#city-modal").addEventListener("click", (e) => { if (e.target.id === "city-modal") closeCity(); });
  $("#city-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#city-submit").disabled = true;
    try {
      const reply = await post("/disrupt", cityBody(e.target));
      if (!reply.ok) {
        $("#city-messages").innerHTML = `<div class="callout bad">${icon("alert")}<span>${esc(reply.message)}</span></div>`;
        return;
      }
      closeCity();
      toast("City updated", reply.message, "k-good", "check");
      await refresh();
    } finally {
      $("#city-submit").disabled = false;
    }
  });
}

// ====================================================================== 14. dataset explorer
// Every table of the loaded dataset, searchable and sortable. Rows are live:
// stock, closures, vehicle positions and travel times update as the city runs.
const yesNo = (v) => (v ? "yes" : "no");
const coord = (v) => (v === null || v === undefined ? "-" : Number(v).toFixed(5));
const statusCell = (closed, slow) => (closed ? '<span class="bad-text">closed</span>'
  : slow > 1 ? `<span class="warn-text">traffic x${slow}</span>` : '<span class="ok-text">open</span>');
// For each table: the map selection a row opens, and its columns [header, value(row), html(row)?, mono?].
const TABLES = {
  places: { kind: (r) => (r.hub ? "hub" : "place"), cols: [
    ["ID", (r) => r.id, null, true], ["Name", (r) => r.name], ["Kind", (r) => r.kind], ["Node", (r) => r.node, null, true],
    ["Lat", (r) => coord(r.lat), null, true], ["Lng", (r) => coord(r.lng), null, true], ["Address", (r) => r.address],
    ["Requests here", (r) => r.requests]] },
  hubs: { kind: () => "hub", cols: [
    ["ID", (r) => r.id, null, true], ["Name", (r) => r.name], ["Node", (r) => r.node, null, true],
    ["Lat", (r) => coord(r.lat), null, true], ["Lng", (r) => coord(r.lng), null, true],
    ["Normal", (r) => r.normal], ["Cold", (r) => r.cold], ["Normal at start", (r) => r.initial_normal], ["Cold at start", (r) => r.initial_cold]] },
  vehicles: { kind: () => "vehicle", cols: [
    ["ID", (r) => r.id, null, true], ["Name", (r) => r.name], ["Refrigerated", (r) => yesNo(r.refrigerated)],
    ["Capacity", (r) => r.capacity], ["Fuel", (r) => `${r.fuel} / ${r.max_fuel}`], ["Status", (r) => r.status],
    ["Home node", (r) => r.home, null, true], ["At node", (r) => r.location, null, true], ["Trip", (r) => r.trip || "-", null, true]] },
  nodes: { kind: () => "node", cols: [
    ["ID", (r) => r.id, null, true], ["Name", (r) => r.name], ["Lat", (r) => coord(r.lat), null, true],
    ["Lng", (r) => coord(r.lng), null, true], ["Roads", (r) => r.roads],
    ["Status", (r) => (r.closed ? "closed" : "open"), (r) => statusCell(r.closed, 1)]] },
  edges: { kind: () => "road", cols: [
    ["ID", (r) => r.id, null, true], ["Street", (r) => r.street], ["From (a)", (r) => `${r.a} · ${r.a_name}`],
    ["To (b)", (r) => `${r.b} · ${r.b_name}`], ["km", (r) => r.km, null, true], ["w (min)", (r) => r.w, null, true],
    ["Now (min)", (r) => r.minutes, null, true], ["Highway", (r) => yesNo(r.hwy)], ["Terrain", (r) => r.terrain || "-"],
    ["Status", (r) => (r.closed ? "closed" : r.slow > 1 ? `traffic x${r.slow}` : "open"), (r) => statusCell(r.closed, r.slow)]] },
};
async function loadDataset() {
  explorerData = await getJSON("/dataset");
  renderExplorer();
}
function toggleExplorer(open) {
  explorerOpen = open;
  $("#explorer").classList.toggle("open", open);
  $("#explorer").setAttribute("aria-hidden", String(!open));
  if (open) loadDataset();
}
function tableRows(name) {
  const table = TABLES[name];
  const query = $("#explorer-search").value.trim().toLowerCase();
  let rows = explorerData[name];
  if (query) rows = rows.filter((r) => table.cols.some(([, value]) => String(value(r)).toLowerCase().includes(query)));
  if (explorerSort.key !== null) {
    const value = table.cols[explorerSort.key][1];
    const num = (x) => (x !== "" && !Number.isNaN(Number(x)) ? Number(x) : null);
    rows = [...rows].sort((a, b) => {
      const x = value(a), y = value(b);
      const nx = num(x), ny = num(y);
      const order = nx !== null && ny !== null ? nx - ny : String(x).localeCompare(String(y), undefined, { numeric: true });
      return order * explorerSort.dir;
    });
  }
  return rows;
}
function renderExplorer() {
  if (!explorerData) return;
  const name = $("#explorer-table").value;
  const meta = explorerData.meta;
  $("#explorer-sub").textContent = `${meta.name} (${meta.id})`;
  $("#explorer-search").disabled = name === "overview";
  $("#explorer-csv").disabled = name === "overview";
  const body = $("#explorer-body");
  if (name === "overview") {
    const c = meta.counts;
    const counts = [["Intersections", c.intersections], ["Roads", c.roads], ["Places", c.places],
                    ["Food hubs", c.hubs], ["Vehicles", c.vehicles], ["Emergencies", state.emergencies.length]];
    const factors = Object.entries(meta.weather_factors).map(([k, v]) => `${nice(k)} x${v}`).join(", ");
    const rush = meta.rush_hours.length ? meta.rush_hours.map((r) => `${r.start}-${r.end} x${r.factor}`).join(", ") : "none configured";
    body.innerHTML = `<div class="overview-grid">${counts.map(([k, v]) => fact(k, v)).join("")}</div>
      <dl class="kv">
        <dt>Dataset id</dt><dd>${esc(meta.id)}</dd>
        <dt>Map centre</dt><dd>${meta.center ? meta.center.map(coord).join(", ") : "-"}</dd>
        <dt>Sources</dt><dd>${meta.sources.map((src) => esc(src)).join("<br>") || "-"}</dd>
        <dt>Disclosure</dt><dd>${esc(meta.disclosure || "-")}</dd>
        <dt>Weather factors</dt><dd>${esc(factors)}</dd>
        <dt>Rush hours</dt><dd>${esc(rush)}</dd>
        <dt>Loader warnings</dt><dd>${meta.warnings.length ? meta.warnings.map((w) => esc(w)).join("<br>") : "none: every record loaded"}</dd>
      </dl>
      <p class="table-note">Pick a table above to see every record with its ids and coordinates. Click a row to show it on the map.</p>`;
    return;
  }
  const table = TABLES[name];
  const rows = tableRows(name);
  const head = table.cols.map(([title], i) =>
    `<th data-col="${i}">${esc(title)}${explorerSort.key === i ? ` <span class="arrow">${explorerSort.dir > 0 ? "▲" : "▼"}</span>` : ""}</th>`).join("");
  const kindOf = table.kind;
  const lines = rows.map((r) => `<tr data-kind="${kindOf(r)}" data-id="${esc(r.id)}"${isSelected(kindOf(r), r.id) ? ' class="selected"' : ""}>${table.cols.map(([, value, html, mono]) =>
    `<td${mono ? ' class="mono"' : ""}>${html ? html(r) : esc(value(r))}</td>`).join("")}</tr>`).join("");
  body.innerHTML = `<p class="table-note">${rows.length} of ${explorerData[name].length} rows. Click a header to sort, a row to show it on the map.</p>
    <table class="data-table"><thead><tr>${head}</tr></thead><tbody>${lines}</tbody></table>`;
}
/** Download the table currently shown (after search and sort) as a CSV file. */
function downloadCsv() {
  const name = $("#explorer-table").value;
  if (!explorerData || name === "overview") return;
  const table = TABLES[name];
  const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [table.cols.map(([title]) => cell(title)).join(",")]
    .concat(tableRows(name).map((r) => table.cols.map(([, value]) => cell(value(r))).join(",")));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  link.download = `${explorerData.meta.id}-${name}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
/** After choosing a row in the explorer, zoom the map to it. */
function focusSelection() {
  if (!selection) return;
  const find = (list) => list.find((x) => same(x.id, selection.id));
  let nodeId = null;
  if (selection.kind === "node") nodeId = selection.id;
  else if (selection.kind === "hub") nodeId = (find(state.hubs) || {}).node;
  else if (selection.kind === "place") nodeId = (find(state.places) || {}).node;
  else if (selection.kind === "vehicle") nodeId = (find(state.vehicles) || {}).from;
  else if (selection.kind === "road") nodeId = (find(state.edges) || {}).a;
  if (nodeId !== null && nodeId !== undefined && positions[nodeId]) focusNode(nodeId);
}
function initExplorer() {
  $("#dataset-btn").addEventListener("click", () => toggleExplorer(!explorerOpen));
  $("#explorer-close").addEventListener("click", () => toggleExplorer(false));
  $("#explorer-table").addEventListener("change", () => { explorerSort = { key: null, dir: 1 }; $("#explorer-search").value = ""; renderExplorer(); });
  $("#explorer-search").addEventListener("input", renderExplorer);
  $("#explorer-csv").addEventListener("click", downloadCsv);
  $("#explorer-body").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-col]");
    if (!th) return;
    const col = Number(th.dataset.col);
    explorerSort = { key: col, dir: explorerSort.key === col ? -explorerSort.dir : 1 };
    renderExplorer();
  });
}

// ====================================================================== 15. start
initTheme();
initControls();
initMapInput();
initTooltip();
renderFeed();
refresh().then(connect);
