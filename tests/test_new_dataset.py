"""A NEW dataset in the competition format works with zero code changes.

tests/fixtures/rehearsal_town.json is a fictional town written in exactly the
toronto-downtown.json format (same top-level keys, same fields on every
record) but with nothing else in common: node ids start at 101 with gaps,
road ids start at 1000, places are H1-H4 / S1-S7, trucks are T01-T09, and it
has highways, rural and gravel roads, a one-bridge island and a different
size. Every feature is run against it: loading, routing, a generated
scenario, input files, the batch run, the text CLI, and the live dashboard
started with NO scenario (an operator typing in crises and city events).
"""

import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import main
from core.routing import find_route
from core.state_machine import CrisisState as S
from helpers import ROOT, TORONTO, check_invariants, make_system, run_until
from sim.scenario_generator import generate_scenario
from sim.simulator import Simulator
from ui.web import Dashboard

REHEARSAL = os.path.join(ROOT, "tests", "fixtures", "rehearsal_town.json")


def run_main(argv, answers=None):
    """Run main.main(argv) and return (exit code, printed text).
    answers: lines typed into the interactive CLI (it ends at the last one)."""
    out = io.StringIO()
    typed = mock.patch("builtins.input", side_effect=list(answers) + [EOFError()]) if answers else mock.MagicMock()
    with typed, redirect_stdout(out), redirect_stderr(out):
        code = main.main(argv)
    return code, out.getvalue()


def bridge_edge(system):
    """The only road to the island (found by name, not by a hard-coded id)."""
    return next(e.id for e in system.graph.edges.values() if e.street == "Island Rd bridge")


class NewDatasetTests(unittest.TestCase):
    def test_same_format_as_dataset_1_and_loads_cleanly(self):
        with open(TORONTO, encoding="utf-8") as f:
            toronto = json.load(f)
        with open(REHEARSAL, encoding="utf-8") as f:
            town = json.load(f)
        self.assertEqual(list(town), list(toronto))  # same top-level keys, same order
        for key in ("nodes", "edges", "pois", "hubs", "trucks"):
            self.assertEqual(set(town[key][0]), set(toronto[key][0]), key)
        s = make_system(REHEARSAL)
        self.assertEqual(s.dataset.warnings, [])
        self.assertEqual((len(s.graph.nodes), len(s.graph.edges), len(s.pois), len(s.hubs), len(s.vehicles)),
                         (22, 31, 11, 4, 9))
        self.assertEqual(s.hubs["H3"].cold, 140)
        self.assertEqual(s.vehicles["T01"].refrigerated, True)

    def test_routing_and_the_one_bridge(self):
        s = make_system(REHEARSAL)
        start, island = s.hubs["H1"].node, s.pois["S6"].node
        self.assertTrue(find_route(s.graph, start, island).found)
        s.apply_disruption({"type": "road_closure", "edge": bridge_edge(s)})
        self.assertFalse(find_route(s.graph, start, island).found)  # island cut off
        s.apply_disruption({"type": "road_reopen", "edge": bridge_edge(s)})
        self.assertTrue(find_route(s.graph, start, island).found)

    def test_generated_scenario_runs_clean(self):
        s = make_system(REHEARSAL)
        emergencies, disruptions = generate_scenario(s.dataset, 480, n_emergencies=10, n_disruptions=5, seed=3)
        sim = Simulator(s)
        for raw in emergencies:
            sim.schedule_emergency(raw)
        for raw in disruptions:
            sim.schedule_disruption(raw)
        run_until(s, sim, 480 + 600, test=self)  # invariants checked every minute
        check_invariants(self, s)
        self.assertTrue(sim.finished())
        self.assertEqual(sim.input_errors, [])
        self.assertTrue(all(e.state in (S.RESOLVED, S.FAILED) for e in s.emergencies.values()))

    def test_input_files_written_for_the_new_town(self):
        s = make_system(REHEARSAL)
        emergencies = [  # by place id, by place NAME, by node id, by lat/lng
            {"id": "R1", "location": "S1", "crisis_type": "power_outage", "location_category": "hospital",
             "people_affected": 120, "food_normal": 40, "food_cold": 20, "severity": 5,
             "deadline": "09:30", "submitted_at": "08:00"},
            {"id": "R2", "location": "Eastfield Community Hall", "crisis_type": "flood",
             "location_category": "community", "people_affected": 60, "food_normal": 30, "severity": 3,
             "deadline": "10:00", "submitted_at": "08:05"},
            {"id": "R3", "location": 302, "crisis_type": "supply_shortage", "location_category": "shelter",
             "people_affected": 40, "food_normal": 25, "severity": 4, "deadline": "10:30", "submitted_at": "08:10"},
            {"id": "R4", "location": {"lat": 45.0511, "lng": -77.8489}, "crisis_type": "storm",
             "location_category": "school", "people_affected": 200, "food_normal": 50, "severity": 2,
             "deadline": "11:00", "submitted_at": "08:15"},
        ]
        disruptions = [
            {"time": "08:20", "type": "road_closure", "edge": bridge_edge(s), "reason": "bridge inspection"},
            {"time": "08:25", "type": "vehicle_breakdown", "vehicle": "T06"},
            {"time": "08:30", "type": "traffic", "edge": 1022, "factor": 2},
            {"time": "08:40", "type": "weather", "condition": "snow"},
            {"time": "09:00", "type": "restock", "hub": "H2", "normal": 40, "cold": 10},
            {"time": "09:10", "type": "road_reopen", "edge": bridge_edge(s)},
            {"time": "09:20", "type": "vehicle_repair", "vehicle": "T06"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            e_path, d_path = os.path.join(tmp, "e.json"), os.path.join(tmp, "d.json")
            with open(e_path, "w", encoding="utf-8") as f:
                json.dump(emergencies, f)
            with open(d_path, "w", encoding="utf-8") as f:
                json.dump(disruptions, f)
            code, out = run_main(["--dataset", REHEARSAL, "--emergencies", e_path, "--disruptions", d_path,
                                  "--run", "--quiet"])
        self.assertEqual(code, 0)
        self.assertIn("Riverbend", out)
        self.assertIn("4 accepted, 0 rejected requests, 0 rejected disruptions", out)
        self.assertIn("Resolved 4", out)

    def test_batch_run_with_generated_scenario(self):
        code, out = run_main(["--dataset", REHEARSAL, "--generate", "8", "--run", "--quiet"])
        self.assertEqual(code, 0)
        self.assertIn("Generated 8 emergencies", out)
        self.assertIn("=== Scenario report", out)
        self.assertIn("0 rejected requests", out)

    def test_cli_uses_the_new_towns_ids_and_the_start_flag(self):
        code, out = run_main(["--dataset", REHEARSAL, "--start", "07:15"], answers=["route", "restock", "route H1 S6"])
        self.assertEqual(code, 0)
        self.assertIn("Clock 07:15. 0 scheduled inputs", out)
        self.assertIn("e.g. route H1 S1", out)      # examples come from the loaded dataset
        self.assertIn("e.g. restock H1 50 20", out)
        self.assertIn("Island Rd bridge", out)

    def test_dataset_argument_is_required_and_start_is_checked(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            main.main([])
        self.assertEqual(ctx.exception.code, 2)
        code, out = run_main(["--dataset", REHEARSAL, "--start", "25:00", "--run"])
        self.assertEqual(code, 2)
        self.assertIn("start_time", out)


class EmptyCityDashboardTests(unittest.TestCase):
    """The dashboard started on the new town with NOTHING scheduled."""

    def setUp(self):
        def empty_world():
            system = make_system(REHEARSAL)
            return system, Simulator(system)

        self.dashboard = Dashboard(*empty_world(), make_world=empty_world, tick_seconds=0.05)
        self.server = self.dashboard.make_server(port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.dashboard.stop()
        self.server.shutdown()
        self.server.server_close()

    @property
    def system(self):
        return self.dashboard.system

    def state(self):
        with urllib.request.urlopen(self.base + "/state", timeout=5) as reply:
            return json.loads(reply.read())

    def post(self, path, body=None):
        data = b"" if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_starts_at_the_configured_time_and_runs_live(self):
        state = self.state()
        self.assertEqual((state["clock"], state["scheduled"], state["finished"]), ("08:00", 0, False))
        self.assertEqual(self.post("/play?on=1&speed=5")[0], 200)  # not refused as "finished"
        time.sleep(0.6)
        state = self.state()
        self.assertTrue(state["playing"])  # an empty city keeps running, it does not stop
        self.assertGreater(state["now"], 480)

    def test_operator_adds_crises_and_city_events(self):
        code, reply = self.post("/emergency", {"location": "riverbend hospitl", "crisis_type": "flood"})
        self.assertEqual(code, 400)
        self.assertTrue(any("did you mean S1" in e for e in reply["errors"]))
        code, reply = self.post("/emergency", {"location": "S1", "crisis_type": "power_outage",
                                               "people_affected": "90", "food_normal": "30", "food_cold": "10",
                                               "severity": "5", "deadline": "09:30"})
        self.assertEqual(code, 200)
        self.assertTrue(self.system.emergencies[reply["id"]].trip_ids)  # dispatched at once

        for body, check in (
            ({"type": "restock", "hub": "H2", "normal": "20", "cold": "5"}, lambda s: s.hubs["H2"].normal == 200),
            ({"type": "traffic", "edge": "1000", "factor": "2"}, lambda s: s.graph.edge_slowdowns.get(1000) == 2),
            ({"type": "weather", "condition": "heavy_snow"}, lambda s: s.conditions()["weather"] == "heavy_snow"),
            # 114 = Mill St & 3rd Ave, a plain intersection (closing 119 would cut off S1 itself)
            ({"type": "node_closure", "node": "114"}, lambda s: 114 in s.graph.closed_nodes),
            ({"type": "vehicle_breakdown", "vehicle": "T09"}, lambda s: s.vehicles["T09"].status == "broken"),
            ({"type": "vehicle_repair", "vehicle": "T09"}, lambda s: s.vehicles["T09"].status != "broken"),
        ):
            code, reply = self.post("/disrupt", body)
            self.assertEqual(code, 200, reply)
            self.assertTrue(check(self.system), body)
        edge = bridge_edge(self.system)
        self.assertIn("closed", self.post(f"/road?edge={edge}")[1]["message"])
        self.assertIn(edge, self.system.graph.closed_edges)

        self.assertEqual(self.post("/run")[0], 200)
        self.assertEqual(self.state()["stats"]["by_state"], {"Resolved": 1})
        self.assertFalse(self.state()["finished"])          # no scenario, so never "over"
        self.assertEqual(self.post("/play?on=1")[0], 200)   # the city can keep running live

    def test_dataset_explorer_lists_the_new_ids(self):
        with urllib.request.urlopen(self.base + "/dataset", timeout=5) as reply:
            data = json.loads(reply.read())
        self.assertEqual(data["meta"]["counts"],
                         {"intersections": 22, "roads": 31, "places": 11, "hubs": 4, "vehicles": 9})
        self.assertEqual([h["id"] for h in data["hubs"]], ["H1", "H2", "H3", "H4"])
        self.assertEqual(next(e for e in data["edges"] if e["id"] == 1000)["street"], "River Rd")
        island = next(n for n in data["nodes"] if n["id"] == 401)
        self.assertEqual((island["name"], island["roads"]), ("Island Rd (bridge end)", 1))

    def test_run_on_an_empty_city_and_reset(self):
        self.assertIn("No emergencies to run yet", self.post("/run")[1]["message"])
        self.post("/emergency", {"location": "S2", "crisis_type": "fire", "location_category": "shelter",
                                 "people_affected": "30", "food_normal": "20", "severity": "3", "deadline": "10:00"})
        self.post("/tick?minutes=20")
        old_run = self.state()["run_id"]
        self.assertEqual(self.post("/reset")[0], 200)
        state = self.state()
        self.assertEqual((state["clock"], state["emergencies"], state["run_id"]), ("08:00", [], old_run + 1))


if __name__ == "__main__":
    unittest.main()
