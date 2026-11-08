"""Generate temporary inputs to verify the submission without bundled city data."""
import copy
import http.client
import io
import json
import os
from pathlib import Path
import random
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

import main
from core.system import CrisisResponseSystem
from loader.config_loader import load_config
from loader.dataset_loader import load_dataset
from sim.simulator import Simulator
from ui.web import Dashboard
import traffic_loader


def generated_dataset(seed, text_ids):
    rng = random.Random(seed)
    ids = rng.sample(range(1000, 100000), 6)
    if text_ids:
        ids = [f"node-{value}" for value in ids]
    lat, lng = rng.uniform(-50, 50), rng.uniform(-120, 120)
    return {
        "id": f"input-{seed}", "name": f"Input {seed}", "center": [lat, lng], "zoom": 12,
        "nodes": [{"id": value, "lat": lat + i / 1000, "lng": lng + i / 1000}
                  for i, value in enumerate(ids)],
        "edges": [{"id": f"road-{i}", "a": a, "b": b, "km": 0.1,
                   "w": round(rng.uniform(0.1, 1), 4), "street": f"Road {i}", "hwy": False}
                  for i, (a, b) in enumerate(zip(ids, ids[1:]))],
        "pois": [{"id": "depot", "kind": "hub", "node": ids[0]},
                 {"id": "destination", "kind": "hospital", "node": ids[-1]}],
        "hubs": [{"poiId": "depot", "normal": 200, "cold": 100}],
        "trucks": [{"id": "vehicle", "home": ids[0], "capacity": 50,
                    "fuel": 100, "refrigerated": True}],
        "terrain": [], "sources": [], "disclosure": "Temporary test input",
        "emergencies": [{"id": "request", "location": "destination", "crisis_type": "flood",
                         "people_affected": 10, "food_normal": 10, "food_cold": 5,
                         "severity": 3, "submitted_at": "08:00", "deadline": "12:00"}],
    }


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "input.json"
        self.data = generated_dataset(41, True)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def test_dataset_is_required_and_explicit_path_overrides_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main.build_parser().parse_args([])
            self.assertEqual(error.exception.code, 2)
        with mock.patch.dict(os.environ, {"DATASET_PATH": str(self.path)}, clear=True):
            self.assertEqual(main.build_parser().parse_args([]).dataset, str(self.path))
            self.assertEqual(main.build_parser().parse_args(["--dataset", "different.json"]).dataset,
                             "different.json")

    def test_random_datasets_with_numeric_and_text_ids_complete_deliveries_offline(self):
        for seed in range(5):
            for text_ids in (False, True):
                with self.subTest(seed=seed, text_ids=text_ids):
                    data = generated_dataset(seed, text_ids)
                    self.path.write_text(json.dumps(data), encoding="utf-8")
                    out = io.StringIO()
                    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")), redirect_stdout(out):
                        self.assertEqual(main.main(["--dataset", str(self.path), "--run", "--quiet"]), 0)
                    self.assertIn("Resolved 1", out.getvalue())
                    self.assertIn("0 rejected requests, 0 rejected disruptions", out.getvalue())
                    self.assertEqual(json.loads(self.path.read_text()), data)

    def test_no_automatic_emergencies_when_dataset_has_none(self):
        self.data.pop("emergencies")
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main.main(["--dataset", str(self.path), "--run", "--quiet"]), 0)
        self.assertIn("Scheduled 0 emergencies and 0 disruptions", out.getvalue())

    def test_hosted_environment_and_dashboard_reset_use_supplied_dataset(self):
        with mock.patch.dict(os.environ, {"DATASET_PATH": str(self.path), "PORT": "10000"}, clear=True), \
                mock.patch.object(main, "Dashboard") as dashboard, redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(["--web", "--paused"]), 0)
        dashboard.return_value.serve.assert_called_once_with(port=10000, host="0.0.0.0", autoplay=False)
        system, sim = dashboard.call_args.args
        reset_system, reset_sim = dashboard.call_args.kwargs["make_world"]()
        self.assertEqual(set(system.graph.nodes), {n["id"] for n in self.data["nodes"]})
        self.assertEqual(set(reset_system.graph.nodes), set(system.graph.nodes))
        self.assertIsNot(reset_system, system)

    def test_local_and_all_interface_http_servers(self):
        for host in ("127.0.0.1", "0.0.0.0"):
            with self.subTest(host=host):
                cfg = load_config(None)
                system = CrisisResponseSystem(load_dataset(self.path, cfg), cfg)
                dashboard = Dashboard(system, Simulator(system))
                server = dashboard.make_server(port=0, host=host)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    for path in ("/", "/app.js", "/app.css", "/healthz", "/state", "/dataset"):
                        conn.request("GET", path)
                        reply = conn.getresponse()
                        body = reply.read()
                        self.assertEqual(reply.status, 200, path)
                        if path == "/state":
                            state = json.loads(body)
                            self.assertEqual(state["dataset_id"], self.data["id"])
                            self.assertEqual(len(state["nodes"]), len(self.data["nodes"]))
                    conn.request("HEAD", "/healthz")
                    reply = conn.getresponse()
                    self.assertEqual(reply.status, 200)
                    self.assertEqual(reply.read(), b"")
                finally:
                    conn.close()
                    dashboard.stop()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_generic_traffic_requires_input_and_preserves_output_on_failure(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            traffic_loader.main([])
        output = self.path.with_name("input-live-traffic.json")
        before = self.path.read_bytes()
        output.write_bytes(before)
        with mock.patch.dict(os.environ, {"TOMTOM_API_KEY": "test-only"}), \
                mock.patch.object(traffic_loader, "fetch_travel_minutes", side_effect=traffic_loader.TrafficError("offline")), \
                mock.patch.object(traffic_loader.time, "sleep"), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(traffic_loader.main([str(self.path)]), 1)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
