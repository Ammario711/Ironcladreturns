"""Web dashboard: starts the real HTTP server on a free port and talks to it."""

import http.client
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from helpers import SAMPLE_DISRUPTIONS, SAMPLE_EMERGENCIES, make_system
from loader.input_loader import load_disruptions_file, load_emergencies_file
from sim.simulator import Simulator
from ui.web import Dashboard


def sample_world():
    """Fresh system + simulator with the sample scenario (also used by Reset)."""
    system = make_system()
    sim = Simulator(system)
    for raw in load_emergencies_file(SAMPLE_EMERGENCIES):
        sim.schedule_emergency(raw)
    for raw in load_disruptions_file(SAMPLE_DISRUPTIONS):
        sim.schedule_disruption(raw)
    return system, sim


class WebDashboardTests(unittest.TestCase):
    def setUp(self):
        # tick_seconds=0.05: the live clock moves 20 times a second, so tests stay fast
        self.dashboard = Dashboard(*sample_world(), make_world=sample_world, tick_seconds=0.05)
        self.server = self.dashboard.make_server(port=0)  # 0 = any free port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.dashboard.stop()
        self.server.shutdown()
        self.server.server_close()

    @property
    def system(self):
        return self.dashboard.system  # changes after a reset

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as reply:
            return reply.read().decode("utf-8"), reply.headers.get("Content-Type")

    def state(self):
        return json.loads(self.get("/state")[0])

    def post(self, path, body=None):
        data = b"" if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    # ------------------------------------------------------------ page + state
    def test_page_files_are_served_and_nothing_else(self):
        page, kind = self.get("/")
        self.assertIn('id="map"', page)
        self.assertTrue(kind.startswith("text/html"))
        self.assertTrue(self.get("/app.css")[1].startswith("text/css"))
        self.assertIn("function refresh", self.get("/app.js")[0])
        for path in ("/nope", "/../core/system.py", "/static/app.js"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code, 404)

    def test_state_has_everything_the_page_draws(self):
        state = self.state()
        self.assertEqual(state["clock"], "08:00")
        self.assertEqual(len(state["nodes"]), 48)
        expected = len(load_emergencies_file(SAMPLE_EMERGENCIES)) + len(load_disruptions_file(SAMPLE_DISRUPTIONS))
        self.assertEqual(state["scheduled"], expected)
        self.assertIn("heavy_snow", state["options"]["weather"])
        self.assertTrue(state["can_reset"])
        self.post("/tick?minutes=5")
        state = self.state()
        moving = [v for v in state["vehicles"] if v["trip"]]
        self.assertTrue(moving)
        self.assertTrue(all(0 <= v["f"] <= 1 for v in state["vehicles"]))
        e1 = next(e for e in state["emergencies"] if e["id"] == "E1")
        self.assertEqual(e1["history"][0]["to"], "Open")
        self.assertIn("severity", e1["breakdown"])
        self.assertTrue(e1["trips"])

    # ------------------------------------------------------------ actions
    def test_tick_and_road_toggle(self):
        self.assertEqual(self.post("/tick?minutes=5")[0], 200)
        self.assertEqual(self.state()["clock"], "08:05")
        self.assertIn("closed", self.post("/road?edge=62")[1]["message"])
        self.assertIn(62, self.system.graph.closed_edges)
        self.assertIn("reopened", self.post("/road?edge=62")[1]["message"])
        self.assertNotIn(62, self.system.graph.closed_edges)

    def test_bad_requests_are_400(self):
        self.assertEqual(self.post("/tick?minutes=-5")[0], 400)
        self.assertEqual(self.post("/nope")[0], 400)
        request = urllib.request.Request(self.base + "/disrupt", data=b"{not json", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_disrupt_endpoint(self):
        code, reply = self.post("/disrupt", {"type": "weather", "condition": "heavy_snow"})
        self.assertEqual(code, 200)
        self.assertEqual(self.state()["conditions"]["weather"], "heavy_snow")
        code, reply = self.post("/disrupt", {"type": "restock", "hub": "P1", "normal": "50", "cold": "0"})
        self.assertEqual(code, 200)
        self.assertEqual(self.system.hubs["P1"].normal, 350)
        code, reply = self.post("/disrupt", {"type": "volcano"})
        self.assertEqual(code, 400)
        self.assertIn("unknown disruption type", reply["message"])

    def test_emergency_form_endpoint(self):
        code, reply = self.post("/emergency", {"location": "Toronto", "crisis_type": "flood", "people_affected": ""})
        self.assertEqual(code, 400)
        self.assertTrue(any("did you mean P3" in e for e in reply["errors"]))
        code, reply = self.post("/emergency", {"location": "P3", "crisis_type": "flood", "people_affected": "80",
                                               "food_normal": "30", "severity": "4", "deadline": "09:30",
                                               "submitted_at": "08:00"})
        self.assertEqual(code, 200)
        e = self.system.emergencies[reply["id"]]
        self.assertTrue(e.trip_ids)  # dispatched immediately, not a minute later

    def test_reset_starts_the_same_scenario_again(self):
        self.post("/tick?minutes=30")
        old_run = self.state()["run_id"]
        code, reply = self.post("/reset")
        self.assertEqual(code, 200)
        state = self.state()
        self.assertEqual((state["clock"], state["run_id"]), ("08:00", old_run + 1))
        self.assertEqual(state["emergencies"], [])

    def test_reset_goes_back_to_the_starting_mode(self):
        self.dashboard.autoplay = True  # as if started with `--web` (live by default)
        self.post("/run")  # finished: the live clock has stopped
        self.assertFalse(self.state()["playing"])
        self.post("/reset")
        state = self.state()
        self.assertEqual(state["clock"], "08:00")
        self.assertTrue(state["playing"])  # live again, not stuck paused

    def test_reset_without_builder_is_refused(self):
        self.dashboard.make_world = None
        self.assertEqual(self.post("/reset")[0], 400)

    # ------------------------------------------------------------ live clock
    def test_live_clock_runs_on_its_own_and_pauses(self):
        self.assertFalse(self.state()["playing"])
        code, reply = self.post("/play?on=1&speed=5")
        self.assertEqual(code, 200)
        time.sleep(0.6)  # ~12 ticks x 5 simulated minutes x 0.05 s
        running = self.state()
        self.assertTrue(running["playing"])
        self.assertEqual(running["speed"], "5")
        self.assertGreater(running["now"], 480)  # moved without any /tick call
        self.post("/play?on=0")
        stopped_at = self.state()["now"]
        time.sleep(0.3)
        self.assertEqual(self.state()["now"], stopped_at)

    def test_play_input_checks(self):
        self.assertEqual(self.post("/play?speed=99")[0], 400)
        self.assertEqual(self.post("/play?speed=real")[0], 200)
        self.assertEqual(self.state()["speed"], "real")
        self.post("/play?on=1")
        self.post("/run")  # Run to end pauses the live clock first
        self.assertFalse(self.state()["playing"])
        self.assertEqual(self.post("/play?on=1")[0], 400)  # finished: Reset first

    # ------------------------------------------------------------ dataset explorer
    def test_dataset_endpoint_lists_every_record_live(self):
        data = json.loads(self.get("/dataset")[0])
        self.assertEqual(data["meta"]["counts"], {"intersections": 48, "roads": 82, "places": 7, "hubs": 3, "vehicles": 12})
        node = next(n for n in data["nodes"] if n["id"] == 33)
        self.assertEqual((node["lat"], node["lng"], node["roads"]), (43.65965, -79.40140000000001, 4))
        edge = next(e for e in data["edges"] if e["id"] == 0)
        self.assertEqual((edge["a"], edge["b"], edge["street"], edge["w"]), (0, 1, "Front St", 2.4136))
        self.assertEqual(edge["a_name"], "Bathurst St & Front St")
        self.assertEqual(next(h for h in data["hubs"] if h["id"] == "P0")["lat"], 43.65965)
        self.assertTrue(any(p["hub"] for p in data["places"]))
        self.assertEqual(len(data["vehicles"]), 12)
        self.post("/road?edge=0")
        self.post("/disrupt", {"type": "restock", "hub": "P0", "normal": 10})
        data = json.loads(self.get("/dataset")[0])
        self.assertTrue(next(e for e in data["edges"] if e["id"] == 0)["closed"])
        self.assertEqual(next(h for h in data["hubs"] if h["id"] == "P0")["normal"], 310)

    def test_intersection_closure_from_the_page(self):
        code, _reply = self.post("/disrupt", {"type": "node_closure", "node": "20"})
        self.assertEqual(code, 200)
        self.assertIn(20, self.system.graph.closed_nodes)

    # ------------------------------------------------------------ live stream
    def test_stream_sends_a_clock_signal_when_time_moves(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request("GET", f"/events?since={len(self.system.bus.log)}")
            reply = conn.getresponse()
            self.post("/tick?minutes=1")
            seen = b""
            while b"event: clock" not in seen:
                line = reply.fp.readline()
                self.assertTrue(line, "stream closed before the clock signal")
                seen += line
        finally:
            conn.close()

    def test_event_stream_sends_bus_events_and_reset(self):
        self.post("/tick?minutes=1")
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request("GET", "/events?since=0")
            reply = conn.getresponse()
            self.assertEqual(reply.getheader("Content-Type"), "text/event-stream")
            lines = [reply.fp.readline() for _ in range(3)]  # id:, event:, data:
            self.assertEqual(lines[0], b"id: 1\n")
            self.assertEqual(lines[1], b"event: bus\n")
            self.assertEqual(json.loads(lines[2][len(b"data: "):])["type"], "crisis-created")
            self.post("/reset")
            seen = b""
            while b"event: reset" not in seen:  # skip the rest of the old run's events
                line = reply.fp.readline()
                self.assertTrue(line, "stream closed before the reset event")
                seen += line
            self.assertIn(b"id: 0\n", seen)
        finally:
            conn.close()

    def test_cloud_hosting_health_head_and_bind_defaults(self):
        import os
        import main
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main.default_bind(), ("127.0.0.1", 8765))
        with mock.patch.dict(os.environ, {"PORT": "10000"}, clear=True):
            self.assertEqual(main.default_bind(), ("0.0.0.0", 10000))
        with mock.patch.dict(os.environ, {"PORT": "9000", "HOST": "127.0.0.1"}, clear=True):
            self.assertEqual(main.default_bind(), ("127.0.0.1", 9000))

        body, ctype = self.get("/healthz")
        self.assertEqual(body, "ok")
        self.assertIn("text/plain", ctype)

        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            for path in ("/", "/healthz", "/state"):
                conn.request("HEAD", path)
                reply = conn.getresponse()
                self.assertEqual(reply.status, 200, path)
                self.assertEqual(reply.read(), b"")
        finally:
            conn.close()

    def test_stream_proxy_headers_negative_since_and_paused_emergency(self):
        # Negative Content-Length is rejected cleanly instead of reading until EOF.
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request("POST", "/disrupt", body=b"", headers={"Content-Length": "-1"})
            reply = conn.getresponse()
            self.assertEqual(reply.status, 400)
            reply.read()
        finally:
            conn.close()

        # Negative since=-1 on an empty log must wait calmly, not spin keep-alives,
        # and include X-Accel-Buffering: no for Cloudflare / Nginx proxies.
        conn2 = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn2.request("GET", "/events?since=-1")
            reply = conn2.getresponse()
            self.assertEqual(reply.getheader("X-Accel-Buffering"), "no")
            self.assertIn("no-transform", reply.getheader("Cache-Control"))
            self.post("/tick?minutes=1")
            first = reply.fp.readline()
            self.assertEqual(first, b"id: 1\n")
        finally:
            conn2.close()

        # Reset leaves sim unstarted at 08:00; adding an emergency while paused
        # must still load scheduled 08:00 inputs first, and future submitted_at
        # must be scheduled instead of dispatched early.
        self.post("/reset")
        self.post("/play?on=0")
        code, reply = self.post("/emergency", {
            "location": "P5", "crisis_type": "shelter_surge",
            "people_affected": 25, "food_normal": 15, "food_cold": 0,
            "severity": 2, "submitted_at": "08:00", "deadline": "10:00",
        })
        self.assertEqual(code, 200)
        self.assertIn("E1", self.system.emergencies)  # scheduled 08:00 E1 loaded first

        code2, reply2 = self.post("/emergency", {
            "location": "P5", "crisis_type": "shelter_surge",
            "people_affected": 25, "food_normal": 15, "food_cold": 0,
            "severity": 3, "submitted_at": "09:30", "deadline": "10:30",
        })
        self.assertEqual(code2, 200)
        self.assertIn("Scheduled for 09:30", reply2["message"])


if __name__ == "__main__":
    unittest.main()
