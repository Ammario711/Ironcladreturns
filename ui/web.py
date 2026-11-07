"""Live web dashboard, standard library only (http.server).

Run:  python3 main.py --dataset CITY.json --web      then open http://localhost:8765

The page itself lives in ui/static/ (index.html, app.css, app.js) so it can
be read and edited like any web page. This file is only the small server:

  GET  /, /app.css, /app.js   the page files (read from disk on every request,
                              so edits show up after a browser refresh)
  GET  /state                 everything the page draws, as JSON
  GET  /dataset               every place, hub, vehicle, intersection and road with
                              all their ids, coordinates and values (Dataset menu)
  GET  /events?since=N        live stream of every event-bus event after number N,
                              plus a "clock" signal whenever the clock moves
                              (Server-Sent Events; the browser reconnects by itself)
  POST /play?on=1&speed=5     start/stop the LIVE clock, and/or set its speed
  POST /tick?minutes=N        advance the clock N minutes
  POST /run                   run the scenario to the end
  POST /road?edge=ID          close the road if it is open, reopen it if closed
  POST /disrupt     {json}    any disruption (weather, traffic, breakdown, repair,
                              restock, closures) - same format as disruptions.json
  POST /emergency   {json}    submit a new emergency request (same fields as the CLI)
  POST /reset                 start the scenario again from the beginning

Why Server-Sent Events and not WebSocket: the standard library has no
WebSocket server. SSE is plain HTTP and one-way (server -> browser), which
is all a live view needs; button clicks are ordinary POST requests.

Live clock: the SERVER runs the city clock (a background thread advances it
every second while playing). Browsers are only views, so every open tab sees
the same city at the same time, and anything one person changes (a new
emergency, the weather...) appears in every tab straight away.

Threads: ThreadingHTTPServer gives every browser connection its own thread,
but CrisisResponseSystem is not thread-safe. So ALL access to the system
happens while holding one lock (inside `self.changed`, a Condition).
Event streams sleep on that Condition and are woken after every change.
"""

import json
import math
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from core.trips import EN_ROUTE
from core.utils import fmt_time, id_sort_key, normalize_id
from ui.cli import format_event

KEEPALIVE_SECONDS = 15  # an SSE comment line this often keeps the connection open
MAX_BODY_BYTES = 64 * 1024  # requests are small forms; refuse anything bigger
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# Live clock speeds: simulated minutes per real second.
# "real" = real time: one simulated minute per real minute.
SPEEDS = {"real": 1 / 60, "1": 1.0, "5": 5.0, "15": 15.0}
STATIC_FILES = {  # URL -> (file name, content type). Only these files are ever served.
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class Dashboard:
    def __init__(self, system, simulator, make_world=None, tick_seconds=1.0):
        """make_world(): optional function returning a fresh (system, simulator)
        pair; it powers the Reset button. Without it, Reset is disabled.
        tick_seconds: how often (real seconds) the live clock moves forward."""
        self.changed = threading.Condition()  # its lock guards every use of the system
        self.make_world = make_world
        self.run_id = 1      # goes up on every reset, so browsers know to clear their log
        self.version = 0     # goes up on every change, so event streams can send "clock"
        self.playing = False # is the live clock running?
        self.autoplay = False  # was the dashboard started live? (Reset returns to this)
        self.speed = "1"     # key of SPEEDS
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._clock_thread = None
        self._use(system, simulator)

    def _use(self, system, simulator):
        self.system, self.sim = system, simulator
        # Was anything scheduled from files / --generate? If not, this is an
        # empty live city that waits for requests typed into the page.
        self.has_scenario = bool(simulator.pending())
        if not simulator.started:  # show the scenario's start time, not 00:00
            system.set_clock(simulator.start_time())

    def scenario_over(self):
        """True once a scheduled scenario has played out to the end (the clock
        then stops). A city started with nothing scheduled is never "over":
        its clock keeps running and waits for the operator's requests."""
        return self.has_scenario and self.sim.finished()

    def reset(self):
        """Fresh copy of the same scenario. Call with the lock held."""
        if self.make_world is None:
            return False, "Reset is not available (dashboard started without a scenario builder)"
        self._use(*self.make_world())
        self.run_id += 1
        # Back to how the dashboard started: live again if it was started live,
        # even if the clock stopped because the last run finished.
        self.playing = self.autoplay
        return True, f"Scenario restarted at {fmt_time(self.system.now)}"

    # ------------------------------------------------------------ live clock
    def bump(self):
        """Something changed: wake every event stream. Call with the lock held."""
        self.version += 1
        self.changed.notify_all()

    def _clock_loop(self):
        """Background thread: while playing, move the clock SPEEDS[speed] simulated
        minutes forward every tick_seconds real seconds. Stops at the scenario end."""
        while not self._stop.wait(self.tick_seconds):
            with self.changed:
                if not self.playing:
                    continue
                if self.scenario_over():
                    self.playing = False
                else:
                    self.sim.advance(SPEEDS[self.speed] * self.tick_seconds)
                self.bump()

    def start_clock(self):
        if self._clock_thread is None:
            self._clock_thread = threading.Thread(target=self._clock_loop, daemon=True)
            self._clock_thread.start()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------ data for the page
    def state(self):
        """Snapshot of the whole system as plain JSON-able data. Call with the lock held."""
        s, g, now = self.system, self.system.graph, self.system.now
        cfg = s.config
        trips = s.dispatcher.trips
        active = {t.vehicle_id: t for t in s.dispatcher.active_trips()}
        stats = s.stats()
        done = stats["fully_delivered"]
        stats["on_time_percent"] = round(100 * stats["on_time"] / done) if done else None
        return {
            "run_id": self.run_id,
            "dataset": s.dataset.name,
            "clock": fmt_time(now),
            "now": now,
            "finished": self.scenario_over(),
            "playing": self.playing,
            "speed": self.speed,
            "scheduled": len(self.sim.pending()),
            "can_reset": self.make_world is not None,
            "last_seq": len(s.bus.log),
            "stats": stats,
            "conditions": {k: v for k, v in s.conditions().items() if k != "slow_roads"},
            "options": {
                "weather": list(cfg["conditions"]["weather_factors"]),
                "crisis_types": list(cfg["validation"]["known_crisis_types"]),
                "categories": list(cfg["scoring"]["category_weights"]),
                "severity": [cfg["validation"]["min_severity"], cfg["validation"]["max_severity"]],
            },
            "nodes": [{"id": n.id, "name": n.name, "lat": n.lat, "lng": n.lng, "closed": n.id in g.closed_nodes}
                      for n in g.nodes.values()],
            "edges": [{"id": e.id, "a": e.a, "b": e.b, "street": e.street, "km": round(e.km, 2),
                       "closed": e.id in g.closed_edges, "slow": g.edge_slowdowns.get(e.id, 1.0),
                       "minutes": round(g.edge_cost(e.id), 1)} for e in g.edges.values()],
            "places": [{"id": p.id, "name": p.name, "kind": p.kind, "node": p.node, "address": p.address}
                       for p in sorted(s.pois.values(), key=lambda p: id_sort_key(p.id))],
            "hubs": [{"id": h.id, "name": h.name, "node": h.node, "normal": h.normal, "cold": h.cold,
                      "initial_normal": h.initial_normal, "initial_cold": h.initial_cold}
                     for h in sorted(s.hubs.values(), key=lambda h: id_sort_key(h.id))],
            "vehicles": [self._vehicle(v, active.get(v.id), now)
                         for v in sorted(s.vehicles.values(), key=lambda v: id_sort_key(v.id))],
            "trips": [self._trip(t, now) for t in active.values()],
            "queue": [{"id": eid, "priority": p} for eid, p in s.queue.ordered()],
            "emergencies": [self._emergency(e, trips) for e in s._sorted_emergencies()],
        }

    def _vehicle(self, v, trip, now):
        """A vehicle plus WHERE it is: between node `from` and node `to`, a
        fraction `f` of the way (the page moves the marker smoothly)."""
        info = {"id": v.id, "name": v.name, "status": v.status, "refrigerated": v.refrigerated,
                "capacity": v.capacity, "fuel": round(v.fuel, 1), "max_fuel": v.max_fuel,
                "home": v.home, "from": v.location, "to": v.location, "f": 0.0, "trip": None}
        if trip is None or not trip.nodes:
            return info
        i = trip.position_index(now)
        info["from"] = info["to"] = trip.nodes[i]
        if i < len(trip.edges):
            depart = trip.pickup_time if i == trip.pickup_index else trip.times[i]  # leaves after loading
            span = trip.times[i + 1] - depart
            if now > depart and span > 0:
                info["to"], info["f"] = trip.nodes[i + 1], round(min(1.0, (now - depart) / span), 3)
        info["trip"] = {"id": trip.id, "emergency": trip.emergency_id, "load": f"{trip.normal}N/{trip.cold}C",
                        "eta": fmt_time(trip.delivery_time) if trip.status == EN_ROUTE else None,
                        "status": trip.status, "loaded": trip.is_loaded(now)}
        return info

    def _trip(self, t, now):
        i = t.position_index(now)
        return {"id": t.id, "vehicle": t.vehicle_id, "emergency": t.emergency_id, "status": t.status,
                "load": f"{t.normal}N/{t.cold}C", "path": t.nodes[i:],
                "eta": fmt_time(t.delivery_time) if t.status == EN_ROUTE else None}

    def _emergency(self, e, trips):
        return {
            "id": e.id, "state": e.state.value, "node": e.node, "label": e.location_label,
            "crisis_type": e.crisis_type, "category": e.category, "people": e.people, "severity": e.severity,
            "need_normal": e.need_normal, "need_cold": e.need_cold,
            "delivered_normal": e.delivered_normal, "delivered_cold": e.delivered_cold,
            "delivered": e.delivered_total(), "requested": e.requested_total(),
            "unassigned": f"{e.unassigned_normal}N/{e.unassigned_cold}C",
            "submitted": fmt_time(e.submitted_at), "deadline": fmt_time(e.deadline), "deadline_min": e.deadline,
            "delivered_at": fmt_time(e.delivered_at) if e.delivered_at is not None else None,
            "priority": e.priority, "base_score": e.base_score, "aging": e.aging_bonus,
            "breakdown": e.score_breakdown, "waiting_reason": e.last_wait_reason,
            "last": e.history[-1].explanation if e.history else "",
            "history": [{"time": fmt_time(h.time), "from": h.from_state.value if h.from_state else "",
                         "to": h.to_state.value, "text": h.explanation, "note": h.is_note} for h in e.history],
            "trips": [{"id": trips[tid].id, "vehicle": trips[tid].vehicle_id, "status": trips[tid].status,
                       "load": f"{trips[tid].normal}N/{trips[tid].cold}C", "source": str(trips[tid].source_id),
                       "eta": fmt_time(trips[tid].delivery_time)} for tid in e.trip_ids if tid in trips],
        }

    def dataset_info(self):
        """Everything in the loaded dataset, as tables (Dataset menu). Call with the lock held."""
        s, g, ds = self.system, self.system.graph, self.system.dataset
        node = g.nodes
        here = {t.vehicle_id: t.current_node(s.now) for t in s.dispatcher.active_trips()}
        requests_at = {}
        for e in s.emergencies.values():
            requests_at[e.node] = requests_at.get(e.node, 0) + 1
        cfg = s.config["conditions"]
        return {
            "meta": {"id": ds.id, "name": ds.name, "center": ds.center, "zoom": ds.zoom,
                     "sources": ds.sources, "disclosure": ds.disclosure, "warnings": ds.warnings,
                     "counts": {"intersections": len(node), "roads": len(g.edges), "places": len(s.pois),
                                "hubs": len(s.hubs), "vehicles": len(s.vehicles)},
                     "weather_factors": cfg["weather_factors"], "rush_hours": cfg["rush_hours"]},
            "places": [{"id": p.id, "name": p.name, "kind": p.kind, "node": p.node, "lat": p.lat, "lng": p.lng,
                        "address": p.address, "hub": p.id in s.hubs, "requests": requests_at.get(p.node, 0)}
                       for p in sorted(s.pois.values(), key=lambda p: id_sort_key(p.id))],
            "hubs": [{"id": h.id, "name": h.name, "node": h.node, "lat": node[h.node].lat, "lng": node[h.node].lng,
                      "normal": h.normal, "cold": h.cold, "initial_normal": h.initial_normal,
                      "initial_cold": h.initial_cold} for h in sorted(s.hubs.values(), key=lambda h: id_sort_key(h.id))],
            "vehicles": [{"id": v.id, "name": v.name, "refrigerated": v.refrigerated, "capacity": v.capacity,
                          "fuel": round(v.fuel, 1), "max_fuel": v.max_fuel, "status": v.status, "home": v.home,
                          "location": here.get(v.id, v.location), "trip": v.trip_id}
                         for v in sorted(s.vehicles.values(), key=lambda v: id_sort_key(v.id))],
            "nodes": [{"id": n.id, "name": n.name, "lat": n.lat, "lng": n.lng, "closed": n.id in g.closed_nodes,
                       "roads": len(g.adjacency.get(n.id, []))} for n in node.values()],
            "edges": [{"id": e.id, "a": e.a, "b": e.b, "a_name": node[e.a].name, "b_name": node[e.b].name,
                       "street": e.street, "km": round(e.km, 3), "w": e.w, "minutes": round(g.edge_cost(e.id), 2),
                       "hwy": e.hwy, "terrain": e.terrain, "closed": e.id in g.closed_edges,
                       "slow": g.edge_slowdowns.get(e.id, 1.0)} for e in g.edges.values()],
        }

    # ------------------------------------------------------------ actions (POST)
    def act(self, path, query, body):
        """Run one button action. Returns (ok, message, extra dict). Call with the lock held."""
        if path == "/tick":
            try:
                minutes = float(query.get("minutes", ["1"])[0])
            except ValueError:
                return False, "minutes must be a number", {}
            if math.isnan(minutes) or not (0 < minutes <= self.sim.max_minutes):
                return False, f"minutes must be more than 0 and at most {self.sim.max_minutes:g}", {}
            self.sim.advance(minutes)
            return True, f"Clock is now {fmt_time(self.system.now)}", {}
        if path == "/play":
            if "speed" in query:
                key = query["speed"][0]
                if key not in SPEEDS:
                    return False, f"speed must be one of {', '.join(SPEEDS)}", {}
                self.speed = key
            if "on" in query:
                on = query["on"][0] in ("1", "true", "yes")
                if on and self.scenario_over():
                    return False, "The scenario has finished: press Reset to run it again", {}
                self.playing = on
            return True, ("Live" if self.playing else "Paused") + f" at speed {self.speed}", {}
        if path == "/run":
            self.playing = False
            self.sim.run()
            if not self.system.emergencies:
                return True, "No emergencies to run yet: add one with New emergency", {}
            return True, f"Ran to {fmt_time(self.system.now)}", {}
        if path == "/reset":
            ok, message = self.reset()
            return ok, message, {}
        if path == "/road":
            try:
                edge = normalize_id(query.get("edge", [""])[0])
            except ValueError as exc:
                return False, f"bad edge id ({exc})", {}
            closed = edge in self.system.graph.closed_edges
            ok, message = self.system.apply_disruption(
                {"type": "road_reopen" if closed else "road_closure", "edge": edge, "reason": "dashboard"})
            return ok, message, {}
        if path == "/disrupt":
            if not isinstance(body, dict):
                return False, "send the disruption as a JSON object", {}
            ok, message = self.system.apply_disruption(dict(body, reason=body.get("reason") or "dashboard"))
            return ok, message, {}
        if path == "/emergency":
            if not isinstance(body, dict):
                return False, "send the request as a JSON object", {}
            result = self.system.submit_emergency(body)
            if not result.ok:
                return False, "Please fix the highlighted problems", {"errors": result.errors,
                                                                     "warnings": result.warnings}
            self.system.tick(self.system.now)  # dispatch right away instead of waiting a minute
            return True, f"{result.emergency.id} received and prioritised", {"id": result.emergency.id,
                                                                            "warnings": result.warnings}
        return False, f"unknown action {path}", {}

    # ------------------------------------------------------------ server
    def make_server(self, port=8765, host="127.0.0.1"):
        handler = type("DashboardHandler", (_Handler,), {"dashboard": self})
        server = ThreadingHTTPServer((host, port), handler)
        server.daemon_threads = True  # open event streams must not block shutdown
        self.start_clock()
        return server

    def serve(self, port=8765, host="127.0.0.1", autoplay=True):
        """autoplay: start the live clock straight away (the city runs on its own)."""
        server = self.make_server(port, host)
        with self.changed:
            self.autoplay = autoplay
            self.playing = autoplay and not self.scenario_over()
        print(f"Dashboard: open http://{host}:{server.server_port}/ in a browser (Ctrl-C to stop)")
        print("The city clock is " + ("running live." if self.playing else "paused: press Play in the page."))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
        finally:
            self.stop()
            server.server_close()


class _Handler(BaseHTTPRequestHandler):
    dashboard = None  # set by Dashboard.make_server

    def log_message(self, *_args):
        pass  # keep the terminal quiet (one line per request otherwise)

    def _send(self, code, body, content_type):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        d = self.dashboard
        if url.path in STATIC_FILES:
            name, content_type = STATIC_FILES[url.path]
            with open(os.path.join(STATIC_DIR, name), "rb") as handle:
                self._send(200, handle.read(), content_type)
        elif url.path == "/state":
            with d.changed:
                body = json.dumps(d.state(), default=str)
            self._send(200, body, "application/json")
        elif url.path == "/dataset":
            with d.changed:
                body = json.dumps(d.dataset_info(), default=str)
            self._send(200, body, "application/json")
        elif url.path == "/events":
            self._stream(parse_qs(url.query))
        else:
            self._send(404, "not found", "text/plain")

    def _read_json(self):
        """Request body as JSON (None if there is none). Raises ValueError if bad."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("request too large")
        if length == 0:
            return None
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_POST(self):
        url = urlparse(self.path)
        d = self.dashboard
        try:
            body = self._read_json()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send(400, json.dumps({"ok": False, "message": f"bad request body ({exc})"}), "application/json")
            return
        with d.changed:
            ok, message, extra = d.act(url.path, parse_qs(url.query), body)
            d.bump()  # wake the event streams
        reply = {"ok": ok, "message": message, **extra}
        self._send(200 if ok else 400, json.dumps(reply, default=str), "application/json")

    def _stream(self, query):
        """Server-Sent Events: 'id: N / event: bus / data: {json}' blocks, forever.
        After a reset, sends one 'event: reset' and starts again from event 1.
        Whenever anything changed (even a quiet minute with no events) it also
        sends 'event: clock', so pages redraw the clock and moving vehicles."""
        d = self.dashboard
        try:  # a reconnecting browser sends the last id it saw
            sent = int(self.headers.get("Last-Event-ID") or query.get("since", ["0"])[0])
        except ValueError:
            sent = 0
        run, version = d.run_id, d.version
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                with d.changed:
                    d.changed.wait_for(lambda: len(d.system.bus.log) > sent or d.run_id != run
                                       or d.version != version, timeout=KEEPALIVE_SECONDS)
                    clock = None
                    if d.version != version:
                        version = d.version
                        clock = json.dumps({"clock": fmt_time(d.system.now), "playing": d.playing})
                    if d.run_id != run:
                        run, sent, lines = d.run_id, 0, None
                    else:
                        new = d.system.bus.log[sent:]  # seq numbers are 1, 2, 3... = list position + 1
                        lines = [(ev.seq, json.dumps({"seq": ev.seq, "type": ev.type, "time": fmt_time(ev.time),
                                                      "line": format_event(ev), "payload": ev.payload},
                                                     default=str)) for ev in new]
                if lines is None:
                    self.wfile.write(f"event: reset\ndata: {run}\n\n".encode("utf-8"))
                elif lines:
                    for seq, data in lines:
                        self.wfile.write(f"id: {seq}\nevent: bus\ndata: {data}\n\n".encode("utf-8"))
                    sent = lines[-1][0]
                if clock is not None:
                    self.wfile.write(f"event: clock\ndata: {clock}\n\n".encode("utf-8"))
                elif not lines:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # the browser tab was closed
