# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""Web dashboard: starts the real HTTP server on a free port and talks to it."""

import http.client
import json
import threading
import unittest
import urllib.error
import urllib.request

from helpers import SAMPLE_DISRUPTIONS, SAMPLE_EMERGENCIES, make_system
from loader.input_loader import load_disruptions_file, load_emergencies_file
from sim.simulator import Simulator
from ui.web import Dashboard


class WebDashboardTests(unittest.TestCase):
    def setUp(self):
        self.system = make_system()
        self.sim = Simulator(self.system)
        for raw in load_emergencies_file(SAMPLE_EMERGENCIES):
            self.sim.schedule_emergency(raw)
        for raw in load_disruptions_file(SAMPLE_DISRUPTIONS):
            self.sim.schedule_disruption(raw)
        self.server = Dashboard(self.system, self.sim).make_server(port=0)  # 0 = any free port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as reply:
            return reply.read().decode("utf-8")

    def post(self, path):
        request = urllib.request.Request(self.base + path, data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=5) as reply:
            return json.loads(reply.read())

    def test_page_and_state(self):
        self.assertIn("<svg", self.get("/"))
        state = json.loads(self.get("/state"))
        self.assertEqual(state["clock"], "08:00")
        self.assertEqual(len(state["nodes"]), 48)
        expected = len(load_emergencies_file(SAMPLE_EMERGENCIES)) + len(load_disruptions_file(SAMPLE_DISRUPTIONS))
        self.assertEqual(state["scheduled"], expected)

    def test_tick_and_road_toggle(self):
        self.assertTrue(self.post("/tick?minutes=5")["ok"])
        state = json.loads(self.get("/state"))
        self.assertEqual(state["clock"], "08:05")
        self.assertGreater(len(state["emergencies"]), 0)
        self.assertIn("closed", self.post("/road?edge=62")["message"])
        self.assertIn(62, self.system.graph.closed_edges)
        self.assertIn("reopened", self.post("/road?edge=62")["message"])
        self.assertNotIn(62, self.system.graph.closed_edges)

    def test_bad_tick_is_a_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/tick?minutes=-5")
        self.assertEqual(ctx.exception.code, 400)

    def test_event_stream_sends_bus_events(self):
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
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
