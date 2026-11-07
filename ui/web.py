# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""Live web dashboard, standard library only (http.server).

Run:  python3 main.py --demo --web      then open http://localhost:8765

Endpoints
  GET  /                the page (HTML + SVG map + JavaScript, all in PAGE below)
  GET  /state           everything the page draws, as JSON
  GET  /events?since=N  live stream of every event-bus event after number N
                        (Server-Sent Events; the browser reconnects by itself)
  POST /tick?minutes=N  advance the clock N minutes
  POST /run             run the scenario to the end
  POST /road?edge=ID    close the road if it is open, reopen it if closed

Why Server-Sent Events and not WebSocket: the standard library has no
WebSocket server. SSE is plain HTTP and one-way (server -> browser), which
is all a live view needs; button clicks are ordinary POST requests.

Threads: ThreadingHTTPServer gives every browser connection its own thread,
but CrisisResponseSystem is not thread-safe. So ALL access to the system
happens while holding one lock (inside `self.changed`, a Condition).
Event streams sleep on that Condition and are woken after every change.
"""

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from core.trips import EN_ROUTE
from core.utils import fmt_time, id_sort_key, normalize_id
from ui.cli import format_event

KEEPALIVE_SECONDS = 15  # an SSE comment line this often keeps proxies/browsers connected


class Dashboard:
    def __init__(self, system, simulator):
        self.system = system
        self.sim = simulator
        self.changed = threading.Condition()  # its lock guards every use of the system
        if not simulator.started:  # show the scenario's start time, not 00:00
            system.set_clock(simulator.start_time())

    # ------------------------------------------------------------ data for the page
    def state(self):
        """Snapshot of the whole system as plain JSON-able data. Call with the lock held."""
        s, g, now = self.system, self.system.graph, self.system.now
        trips = s.dispatcher.active_trips()
        vehicle_node = {t.vehicle_id: t.current_node(now) for t in trips}
        return {
            "dataset": s.dataset.name,
            "clock": fmt_time(now),
            "finished": self.sim.finished(),
            "scheduled": len(self.sim.pending()),
            "last_seq": len(s.bus.log),
            "stats": s.stats(),
            "conditions": {k: v for k, v in s.conditions().items() if k != "slow_roads"},
            "nodes": [{"id": n.id, "name": n.name, "lat": n.lat, "lng": n.lng, "closed": n.id in g.closed_nodes}
                      for n in g.nodes.values()],
            "edges": [{"id": e.id, "a": e.a, "b": e.b, "street": e.street, "closed": e.id in g.closed_edges,
                       "slow": g.edge_slowdowns.get(e.id, 1.0), "minutes": round(g.edge_cost(e.id), 2)}
                      for e in g.edges.values()],
            "places": [{"id": p.id, "name": p.name, "kind": p.kind, "node": p.node} for p in s.pois.values()],
            "hubs": [{"id": h.id, "name": h.name, "node": h.node, "normal": h.normal, "cold": h.cold}
                     for h in sorted(s.hubs.values(), key=lambda h: id_sort_key(h.id))],
            "vehicles": [{"id": v.id, "status": v.status, "refrigerated": v.refrigerated,
                          "node": vehicle_node.get(v.id, v.location), "trip": v.trip_id}
                         for v in sorted(s.vehicles.values(), key=lambda v: id_sort_key(v.id))],
            "trips": [{"id": t.id, "vehicle": t.vehicle_id, "emergency": t.emergency_id, "status": t.status,
                       "load": f"{t.normal}N/{t.cold}C",
                       "eta": fmt_time(t.delivery_time) if t.status == EN_ROUTE else "-",
                       "path": t.nodes[t.position_index(now):]} for t in trips],
            "queue": [{"id": eid, "priority": p, "state": s.emergencies[eid].state.value,
                       "unassigned": f"{s.emergencies[eid].unassigned_normal}N/{s.emergencies[eid].unassigned_cold}C",
                       "deadline": fmt_time(s.emergencies[eid].deadline)} for eid, p in s.queue.ordered()],
            "emergencies": [{"id": e.id, "state": e.state.value, "node": e.node, "label": e.location_label,
                             "delivered": e.delivered_total(), "requested": e.requested_total(),
                             "deadline": fmt_time(e.deadline),
                             "last": e.history[-1].explanation if e.history else ""}
                            for e in s._sorted_emergencies()],
        }

    # ------------------------------------------------------------ actions (POST)
    def act(self, path, query):
        """Run one button action. Returns (ok, message). Call with the lock held."""
        if path == "/tick":
            try:
                minutes = float(query.get("minutes", ["1"])[0])
            except ValueError:
                return False, "minutes must be a number"
            if not (0 < minutes <= self.sim.max_minutes) or math.isnan(minutes):
                return False, f"minutes must be more than 0 and at most {self.sim.max_minutes:g}"
            self.sim.advance(minutes)
            return True, f"Clock is now {fmt_time(self.system.now)}"
        if path == "/run":
            self.sim.run()
            return True, f"Ran to {fmt_time(self.system.now)}"
        if path == "/road":
            try:
                edge = normalize_id(query.get("edge", [""])[0])
            except ValueError as exc:
                return False, f"bad edge id ({exc})"
            closed = edge in self.system.graph.closed_edges
            return self.system.apply_disruption(
                {"type": "road_reopen" if closed else "road_closure", "edge": edge, "reason": "dashboard click"})
        return False, f"unknown action {path}"

    # ------------------------------------------------------------ server
    def make_server(self, port=8765, host="127.0.0.1"):
        handler = type("DashboardHandler", (_Handler,), {"dashboard": self})
        server = ThreadingHTTPServer((host, port), handler)
        server.daemon_threads = True  # open event streams must not block shutdown
        return server

    def serve(self, port=8765, host="127.0.0.1"):
        server = self.make_server(port, host)
        print(f"Dashboard: open http://{host}:{server.server_port}/ in a browser (Ctrl-C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
        finally:
            server.server_close()


class _Handler(BaseHTTPRequestHandler):
    dashboard = None  # set by Dashboard.make_server

    def log_message(self, *_args):
        pass  # keep the terminal quiet (one line per request otherwise)

    def _send(self, code, body, content_type):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        d = self.dashboard
        if url.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif url.path == "/state":
            with d.changed:
                body = json.dumps(d.state(), default=str)
            self._send(200, body, "application/json")
        elif url.path == "/events":
            self._stream(parse_qs(url.query))
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        d = self.dashboard
        with d.changed:
            ok, message = d.act(url.path, parse_qs(url.query))
            d.changed.notify_all()  # wake the event streams
        self._send(200 if ok else 400, json.dumps({"ok": ok, "message": message}), "application/json")

    def _stream(self, query):
        """Server-Sent Events: 'id: N / event: bus / data: {json}' blocks, forever."""
        d = self.dashboard
        try:  # a reconnecting browser sends the last id it saw
            sent = int(self.headers.get("Last-Event-ID") or query.get("since", ["0"])[0])
        except ValueError:
            sent = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                with d.changed:
                    d.changed.wait_for(lambda: len(d.system.bus.log) > sent, timeout=KEEPALIVE_SECONDS)
                    new = d.system.bus.log[sent:]  # event seq numbers are 1, 2, 3... = list position + 1
                    lines = [(ev.seq, json.dumps({"seq": ev.seq, "type": ev.type, "line": format_event(ev)},
                                                 default=str)) for ev in new]
                if lines:
                    for seq, data in lines:
                        self.wfile.write(f"id: {seq}\nevent: bus\ndata: {data}\n\n".encode("utf-8"))
                    sent = lines[-1][0]
                else:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # the browser tab was closed


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crisis Dashboard</title>
<style>
  :root { --bg:#f6f7f9; --panel:#ffffff; --text:#1d2330; --muted:#667085; --line:#d0d5dd;
          --road:#98a2b3; --closed:#d92d20; --hub:#12b76a; --site:#2e90fa; --vehicle:#7a5af8;
          --ok:#12b76a; --warn:#f79009; --bad:#d92d20; --busy:#2e90fa; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1115; --panel:#181b22; --text:#e6e8ee; --muted:#98a2b3; --line:#2c313c;
            --road:#4b5565; --closed:#f97066; --hub:#32d583; --site:#53b1fd; --vehicle:#9b8afb; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.4 system-ui, sans-serif; }
  header { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; padding:12px 16px;
           border-bottom:1px solid var(--line); background:var(--panel); }
  header h1 { font-size:16px; margin:0; }
  .clock { font-variant-numeric:tabular-nums; font-weight:600; }
  button { font:inherit; padding:6px 12px; border:1px solid var(--line); border-radius:6px;
           background:var(--bg); color:var(--text); cursor:pointer; }
  button:hover { border-color:var(--muted); }
  .chips { display:flex; flex-wrap:wrap; gap:6px; color:var(--muted); }
  .chips span { border:1px solid var(--line); border-radius:999px; padding:2px 8px; }
  main { display:grid; grid-template-columns:minmax(0,3fr) minmax(300px,2fr); gap:12px; padding:12px; }
  @media (max-width:900px) { main { grid-template-columns:1fr; } }
  section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px; min-width:0; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:0 0 8px; }
  svg { width:100%; height:auto; display:block; }
  .legend { color:var(--muted); font-size:12px; margin-top:6px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:3px 6px; border-bottom:1px solid var(--line); white-space:nowrap; }
  td.wrap { white-space:normal; color:var(--muted); font-size:12px; }
  .badge { border-radius:4px; padding:1px 6px; font-size:12px; color:#fff; background:var(--muted); }
  .Resolved, .Delivered { background:var(--ok); } .Failed { background:var(--bad); }
  .Delayed, .Re-Routing { background:var(--warn); } .In-Progress, .Resource-Assigned { background:var(--busy); }
  #log { height:260px; overflow:auto; font:12px/1.4 ui-monospace, monospace; white-space:pre-wrap; }
  #toast { color:var(--muted); }
  .side { display:grid; gap:12px; align-content:start; min-width:0; }
  .scroll { max-height:260px; overflow:auto; }
</style>
</head>
<body>
<header>
  <h1 id="title">Crisis Dashboard</h1>
  <span class="clock" id="clock">--:--</span>
  <button onclick="post('/tick?minutes=1')">Tick 1 min</button>
  <button onclick="post('/tick?minutes=10')">Tick 10 min</button>
  <button onclick="post('/run')">Run to end</button>
  <span id="toast"></span>
  <div class="chips" id="chips"></div>
</header>
<main>
  <section>
    <h2>Road network</h2>
    <svg id="map" viewBox="0 0 1000 680" role="img" aria-label="Road map"></svg>
    <div class="legend">Square = hub (stock N/C) &middot; ring = emergency (colour = state) &middot;
      dot = vehicle &middot; coloured line = vehicle's remaining route &middot; red dashed = closed &middot;
      thick orange = traffic jam.
      Click a road to close or reopen it.</div>
  </section>
  <div class="side">
    <section><h2>Priority queue</h2><div class="scroll"><table id="queue"></table></div></section>
    <section><h2>Emergencies</h2><div class="scroll"><table id="emerg"></table></div></section>
    <section><h2>Live events</h2><div id="log"></div></section>
  </div>
</main>
<script>
const W = 1000, H = 680, PAD = 40;
const ROUTE_COLOURS = ["#2e90fa", "#f79009", "#7a5af8", "#12b76a", "#ee46bc", "#0e9384", "#dc6803", "#6172f3"];
let state = null, lastSeq = 0, refreshQueued = false;

function esc(text) {
  return String(text).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// Place nodes using lat/lng (flat projection) or, if any node has no coordinates, on a circle.
function layout(nodes) {
  const pos = {};
  const geo = nodes.length > 0 && nodes.every(n => n.lat !== null && n.lng !== null);
  if (geo) {
    const lats = nodes.map(n => n.lat), lngs = nodes.map(n => n.lng);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats), minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
    const k = Math.cos((minLat + maxLat) / 2 * Math.PI / 180);  // shrink longitude away from the equator
    const w = Math.max((maxLng - minLng) * k, 1e-9), h = Math.max(maxLat - minLat, 1e-9);
    const s = Math.min((W - 2 * PAD) / w, (H - 2 * PAD) / h);
    const ox = (W - w * s) / 2, oy = (H - h * s) / 2;
    nodes.forEach(n => { pos[n.id] = [ox + (n.lng - minLng) * k * s, oy + (maxLat - n.lat) * s]; });
  } else {
    nodes.forEach((n, i) => {
      const a = 2 * Math.PI * i / Math.max(nodes.length, 1);
      pos[n.id] = [W / 2 + (W / 2 - PAD) * Math.cos(a), H / 2 + (H / 2 - PAD) * Math.sin(a)];
    });
  }
  return pos;
}

function stateClass(name) { return esc(name.replace(/ /g, "-")); }

function drawMap() {
  const pos = layout(state.nodes);
  const out = [];
  for (const e of state.edges) {  // roads: visible line + wide invisible line that is easy to click
    const [x1, y1] = pos[e.a], [x2, y2] = pos[e.b];
    const style = e.closed ? 'stroke="var(--closed)" stroke-width="3" stroke-dasharray="8 6"'
                : e.slow > 1 ? 'stroke="var(--warn)" stroke-width="5"'
                : 'stroke="var(--road)" stroke-width="2"';
    out.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" ${style}/>`);
    out.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="transparent" stroke-width="14"
      style="cursor:pointer" onclick="post('/road?edge=' + encodeURIComponent(${esc(JSON.stringify(e.id))}))">
      <title>edge ${esc(e.id)} ${esc(e.street)} (${e.minutes} min${e.slow > 1 ? ", traffic x" + e.slow : ""})${e.closed ? " CLOSED - click to reopen" : " - click to close"}</title></line>`);
  }
  state.trips.forEach((t, i) => {  // each vehicle's remaining route
    if (t.path.length < 2) return;
    const pts = t.path.map(n => pos[n].join(",")).join(" ");
    out.push(`<polyline points="${pts}" fill="none" stroke="${ROUTE_COLOURS[i % ROUTE_COLOURS.length]}"
      stroke-width="5" stroke-opacity="0.75" stroke-linejoin="round"><title>${esc(t.vehicle)} -> ${esc(t.emergency)} ETA ${esc(t.eta)}</title></polyline>`);
  });
  for (const n of state.nodes) {
    const [x, y] = pos[n.id];
    out.push(n.closed ? `<circle cx="${x}" cy="${y}" r="7" fill="none" stroke="var(--closed)" stroke-width="3"><title>${esc(n.name)} CLOSED</title></circle>`
                      : `<circle cx="${x}" cy="${y}" r="2.5" fill="var(--road)"><title>${esc(n.id)} ${esc(n.name)}</title></circle>`);
  }
  for (const h of state.hubs) {
    const [x, y] = pos[h.node];
    out.push(`<rect x="${x - 8}" y="${y - 8}" width="16" height="16" fill="var(--hub)"><title>${esc(h.name)}</title></rect>`);
    out.push(`<text x="${x + 11}" y="${y - 10}" font-size="13" fill="var(--text)">${esc(h.id)} ${h.normal}N/${h.cold}C</text>`);
  }
  const seenAt = {};
  for (const e of state.emergencies) {  // several emergencies at one node: growing rings
    const k = seenAt[e.node] = (seenAt[e.node] || 0) + 1;
    const [x, y] = pos[e.node];
    out.push(`<circle cx="${x}" cy="${y}" r="${8 + 5 * k}" fill="none" stroke-width="3" class="ring-${stateClass(e.state)}"
      stroke="${ringColour(e.state)}"><title>${esc(e.id)}: ${esc(e.state)} (${e.delivered}/${e.requested})</title></circle>`);
    out.push(`<text x="${x - 10}" y="${y + 24 + 12 * (k - 1)}" font-size="12" fill="var(--text)">${esc(e.id)}</text>`);
  }
  const vehAt = {};
  for (const v of state.vehicles) {  // fan out vehicles that share a node
    const k = vehAt[v.node] = (vehAt[v.node] || 0) + 1;
    const [x, y] = pos[v.node];
    const a = k * 0.9, r = 12;
    const colour = v.status === "broken" ? "var(--closed)" : v.status === "busy" ? "var(--vehicle)" : "var(--muted)";
    out.push(`<circle cx="${x + r * Math.cos(a)}" cy="${y + r * Math.sin(a)}" r="4.5" fill="${colour}"
      stroke="${v.refrigerated ? "var(--site)" : "none"}" stroke-width="2"><title>${esc(v.id)} ${esc(v.status)}${v.refrigerated ? " (refrigerated)" : ""}</title></circle>`);
  }
  document.getElementById("map").innerHTML = out.join("");
}

function ringColour(name) {
  return {"Resolved":"var(--ok)","Delivered":"var(--ok)","Failed":"var(--bad)","Delayed":"var(--warn)",
          "Re-Routing":"var(--warn)","In Progress":"var(--busy)","Resource Assigned":"var(--busy)"}[name] || "var(--muted)";
}

function badge(name) { return `<span class="badge ${stateClass(name)}">${esc(name)}</span>`; }

function drawTables() {
  const q = state.queue.map((r, i) => `<tr><td>${i + 1}</td><td>${esc(r.id)}</td><td>${r.priority.toFixed(1)}</td>
    <td>${badge(r.state)}</td><td>${esc(r.unassigned)}</td><td>${esc(r.deadline)}</td></tr>`);
  document.getElementById("queue").innerHTML = "<tr><th>#</th><th>ID</th><th>Priority</th><th>State</th><th>Waiting</th><th>Due</th></tr>"
    + (q.join("") || '<tr><td colspan="6">Nothing waiting for resources</td></tr>');
  const e = state.emergencies.map(r => `<tr><td>${esc(r.id)}</td><td>${badge(r.state)}</td>
    <td>${r.delivered}/${r.requested}</td><td>${esc(r.deadline)}</td></tr><tr><td></td><td class="wrap" colspan="3">${esc(r.last)}</td></tr>`);
  document.getElementById("emerg").innerHTML = "<tr><th>ID</th><th>State</th><th>Delivered</th><th>Due</th></tr>"
    + (e.join("") || '<tr><td colspan="4">No emergencies yet - press Tick</td></tr>');
}

function drawHeader() {
  const st = state.stats, c = state.conditions;
  document.getElementById("title").textContent = state.dataset;
  document.getElementById("clock").textContent = state.clock + (state.finished ? " (finished)" : "");
  document.getElementById("chips").innerHTML = [
    `${st.emergencies} emergencies`, `${st.units_delivered}/${st.units_requested} units delivered`,
    `stock ${st.stock_left_normal}N / ${st.stock_left_cold}C`, `${st.vehicles_busy} vehicles busy`,
    `${st.vehicles_broken} broken`, `${state.scheduled} scheduled`,
    `weather ${c.weather} x${c.weather_factor}`, `rush hour x${c.rush_factor}`, `roads x${c.factor.toFixed(2)}`].map(t => `<span>${esc(t)}</span>`).join("");
}

async function refresh() {
  refreshQueued = false;
  state = await (await fetch("/state")).json();
  drawHeader(); drawMap(); drawTables();
}

function queueRefresh() {  // many events arrive at once: redraw at most ~4 times a second
  if (!refreshQueued) { refreshQueued = true; setTimeout(refresh, 250); }
}

async function post(url) {
  const reply = await (await fetch(url, {method: "POST"})).json();
  document.getElementById("toast").textContent = reply.message;
  queueRefresh();
}

function connect() {
  const log = document.getElementById("log");
  const source = new EventSource("/events?since=" + lastSeq);
  source.addEventListener("bus", msg => {
    const ev = JSON.parse(msg.data);
    lastSeq = ev.seq;
    const line = document.createElement("div");
    line.textContent = ev.line;  // textContent: event text is never treated as HTML
    log.appendChild(line);
    while (log.childNodes.length > 400) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
    queueRefresh();
  });
}

refresh().then(connect);
</script>
</body>
</html>
"""
