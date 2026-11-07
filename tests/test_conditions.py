# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""City conditions (weather, rush hour, traffic jams), the Open-Meteo weather
snapshot loader, and the fuller sample city timeline.

No test touches the internet: the Open-Meteo call is replaced by a fake.
"""

import io
import json
import os
import ssl
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

import main
from core.disruptions import parse_disruption
from core.graph import Edge, Graph, Node
from core.routing import find_route
from core.scoring import compute_score
from core.state_machine import CrisisState as S
from core.trips import Trip
from helpers import (ROOT, SAMPLE_DISRUPTIONS, SAMPLE_EMERGENCIES, check_invariants, config, make_system,
                     request, run_until)
from loader.config_loader import ConfigError, apply_overrides
from loader.input_loader import load_disruptions_file, load_emergencies_file
from loader.weather_loader import (SYSTEM_CA_FILES, WeatherError, city_point, fetch_open_meteo, load_snapshot,
                                   save_snapshot, weather_condition, weather_disruptions)
from sim.simulator import Simulator
from ui.cli import CrisisCLI

SNAPSHOT = os.path.join(ROOT, "samples", "weather", "toronto-downtown_2025-02-16.json")
DOUBLE = {"conditions": {"weather_factors": {"clear": 1.0, "double": 2.0}}}


def line_graph():
    g = Graph()
    for n in "ABC":
        g.add_node(Node(n, n))
    g.add_edge(Edge("ab", "A", "B", km=1, w=1))
    g.add_edge(Edge("bc", "B", "C", km=1, w=1))
    g.add_edge(Edge("ac", "A", "C", km=3, w=3))
    return g


class GraphCostTests(unittest.TestCase):
    def test_city_factor_and_slowdowns_change_costs_and_version(self):
        g = line_graph()
        v = g.version
        self.assertTrue(g.set_condition_factor(1.5))
        self.assertEqual(g.edge_cost("ab"), 1.5)
        self.assertTrue(g.set_edge_slowdown("ab", 2.0))
        self.assertEqual(g.edge_cost("ab"), 3.0)
        self.assertEqual(g.version, v + 2)  # caches know to recompute
        self.assertFalse(g.set_condition_factor(1.5))  # no change -> no version bump
        self.assertTrue(g.set_edge_slowdown("ab", 1.0))  # cleared
        self.assertEqual(g.edge_slowdowns, {})

    def test_new_routes_avoid_a_jam(self):
        g = line_graph()
        self.assertEqual(find_route(g, "A", "C").edges, ["ab", "bc"])
        g.set_edge_slowdown("bc", 5.0)  # A-B-C now costs 1 + 5 = 6 > 3
        self.assertEqual(find_route(g, "A", "C").edges, ["ac"])


class ConditionInputTests(unittest.TestCase):
    def setUp(self):
        self.s = make_system()

    def parse(self, raw):
        return parse_disruption(raw, self.s.graph, self.s.vehicles, self.s.hubs,
                                weather_factors=self.s.config["conditions"]["weather_factors"])

    def test_weather_and_traffic_validation(self):
        d, errors = self.parse({"type": "weather", "condition": "Heavy Snow"})
        self.assertEqual((errors, d.condition, d.factor), ([], "heavy_snow", 1.7))
        self.assertIn("unknown", " ".join(self.parse({"type": "weather", "condition": "blizzard"})[1]))
        self.assertIn("factor", " ".join(self.parse({"type": "traffic", "edge": 38})[1]))
        self.assertIn(">= 1", " ".join(self.parse({"type": "traffic", "edge": 38, "factor": 0.5})[1]))
        self.assertIn("non-empty list", " ".join(self.parse({"type": "traffic", "edges": "38", "factor": 2})[1]))
        d, errors = self.parse({"type": "congestion", "a": 20, "b": 21, "factor": "2.5"})
        self.assertEqual((errors, d.edges, d.factor), ([], [38], 2.5))

    def test_bad_condition_config_is_refused(self):
        for bad in ({"conditions": {"weather_factors": {"snow": 0}}},
                    {"conditions": {"weather_factors": {"snow": float("inf")}}},
                    {"conditions": {"weather_factors": {"snow": float("nan")}}},
                    {"conditions": None},
                    {"conditions": {"rush_hours": "08:00-09:00"}},
                    {"conditions": {"rush_hours": [{"start": "09:00", "end": "08:00", "factor": 1.2}]}},
                    {"conditions": {"rush_hours": [{"start": "07:00", "end": "08:00", "factor": float("inf")}]}},
                    {"conditions": {"rush_hours": [{"start": "07:00", "factor": 1.2}]}},
                    {"scoring": {"weights": None}},
                    {"scoring": {"weights": {"severity": -5, "people": 10}}}):
            with self.assertRaises(ConfigError):
                apply_overrides(bad)


class SystemConditionTests(unittest.TestCase):
    def test_weather_slows_every_road_and_is_announced(self):
        s = make_system(overrides=DOUBLE)
        cost = s.graph.edge_cost(0)
        ok, _msg = s.apply_disruption({"type": "weather", "condition": "double"})
        self.assertTrue(ok)
        self.assertEqual(s.graph.edge_cost(0), 2 * cost)
        self.assertEqual(len(s.bus.events_of("conditions-changed")), 1)
        self.assertIn("No change", s.apply_disruption({"type": "weather", "condition": "double"})[1])

    def test_rush_hours_switch_on_and_off(self):
        s = make_system(overrides={"conditions": {"rush_hours": [{"start": "08:00", "end": "09:00", "factor": 1.5}]}})
        self.assertEqual((s.rush_hour_factor(479), s.rush_hour_factor(480), s.rush_hour_factor(540)), (1.0, 1.5, 1.0))
        s.tick(480)
        self.assertEqual(s.graph.condition_factor, 1.5)
        s.tick(540)
        self.assertEqual(s.graph.condition_factor, 1.0)
        self.assertEqual(len(s.bus.events_of("conditions-changed")), 2)

    def test_slowdown_updates_eta_without_rerouting_state(self):
        s = make_system(overrides=DOUBLE)
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P5", food_normal=40))
        run_until(s, sim, 482)
        (trip,) = s.dispatcher.trips_for("E1")
        old_eta = trip.delivery_time
        old_path = list(trip.nodes)
        s.apply_disruption({"type": "weather", "condition": "double"})
        self.assertGreater(trip.delivery_time, old_eta)  # slower roads, later ETA
        self.assertEqual(trip.nodes, old_path)  # same route: still open, so no Re-Routing
        self.assertTrue(any(ev.payload.get("status") == "retimed" for ev in s.bus.events_of("route-updated")))
        run_until(s, sim, 600, test=self)
        e = s.emergencies["E1"]
        self.assertEqual(e.state, S.RESOLVED)
        self.assertNotIn(S.REROUTING, [h.to_state for h in e.history])
        self.assertGreaterEqual(e.delivered_at, int(trip.delivery_time))

    def test_retime_while_loading_keeps_pickup_time(self):
        s = make_system(overrides=DOUBLE)
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P4", food_normal=40))  # loads at P2 (node 23) until 08:05
        run_until(s, sim, 482)
        (trip,) = s.dispatcher.trips_for("E1")
        s.apply_disruption({"type": "weather", "condition": "double"})
        self.assertEqual(trip.pickup_time, 485)
        self.assertAlmostEqual(trip.delivery_time, 485 + 2 * s.graph.edges[42].w + 5, places=6)

    def test_retime_is_exact_across_multiple_speed_changes(self):
        g = Graph()
        g.add_node(Node("A", "A"))
        g.add_node(Node("B", "B"))
        g.add_edge(Edge("ab", "A", "B", km=10, w=10))
        trip = Trip("T1", "E1", "V1", "hub", "H1", "A", "B", 10, 0, assigned_at=0.0)
        trip.apply_plan(g, 0.0, None, find_route(g, "A", "B"), load_minutes=0, unload_minutes=0)
        self.assertAlmostEqual(trip.times[1], 10.0)

        g.set_edge_slowdown("ab", 10.0)  # 50% left at t=5 -> 5 + 0.5 * 100 = 55
        trip.retime(g, 5.0, load_minutes=0, unload_minutes=0)
        self.assertAlmostEqual(trip.times[1], 55.0)

        g.set_edge_slowdown("ab", 1.0)   # 1% driven during [5, 6] -> 49% left -> 6 + 4.9 = 10.9
        trip.retime(g, 6.0, load_minutes=0, unload_minutes=0)
        self.assertAlmostEqual(trip.times[1], 10.9)

        g.set_edge_slowdown("ab", 2.0)   # 20% driven during [6, 8] -> 29% left -> 8 + 0.29 * 20 = 13.8
        trip.retime(g, 8.0, load_minutes=0, unload_minutes=0)
        self.assertAlmostEqual(trip.times[1], 13.8)

    def test_rush_hour_syncs_before_scoring_and_cli_startup(self):
        rush_cfg = {"conditions": {"rush_hours": [{"start": "08:00", "end": "09:00", "factor": 2.0}]}}
        s = make_system(overrides=rush_cfg)
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P5", submitted_at="08:00", deadline="08:40"))
        cli = CrisisCLI(s, sim, live=False)
        self.assertEqual(cli.system.conditions()["rush_factor"], 2.0)

        # Fresh run: initial crisis-created score at 08:00 already sees the 2.0x rush-hour travel time.
        s_plain = make_system()
        sim_plain = Simulator(s_plain)
        sim_plain.schedule_emergency(request(location="P5", submitted_at="08:00", deadline="08:40"))
        plain_scores = []
        s_plain.bus.subscribe("state-changed", lambda ev: (
            plain_scores.append(s_plain.emergencies["E1"].base_score)
            if ev.payload["to"] == S.PRIORITIZED.value else None))
        sim_plain.step_to(480)

        s2 = make_system(overrides=rush_cfg)
        sim2 = Simulator(s2)
        sim2.schedule_emergency(request(location="P5", submitted_at="08:00", deadline="08:40"))
        rush_factors_at_score = []
        rush_scores = []
        s2.bus.subscribe("state-changed", lambda ev: (
            (rush_factors_at_score.append(s2.rush_factor), rush_scores.append(s2.emergencies["E1"].base_score))
            if ev.payload["to"] == S.PRIORITIZED.value else None))
        sim2.step_to(480)
        self.assertEqual(rush_factors_at_score[0], 2.0)
        self.assertNotEqual(rush_scores[0], plain_scores[0])


class WeatherSnapshotTests(unittest.TestCase):
    def test_wmo_codes_map_to_conditions(self):
        cases = {0: "clear", 3: "clear", 45: "fog", 53: "drizzle", 56: "freezing_rain", 63: "rain",
                 65: "heavy_rain", 73: "snow", 75: "heavy_snow", 86: "heavy_snow", 95: "storm", None: "clear"}
        for code, name in cases.items():
            self.assertEqual(weather_condition(code), name)

    def test_hours_become_change_events_from_the_scenario_start(self):
        snap = {"date": "2025-02-16", "hours": [
            {"time": "06:00", "condition": "snow", "weather_code": 73},
            {"time": "07:00", "condition": "heavy_snow", "weather_code": 75},
            {"time": "08:00", "condition": "heavy_snow", "weather_code": 75},
            {"time": "09:00", "condition": "snow", "weather_code": 73},
            {"time": "bad"}]}
        events = weather_disruptions(snap, start_minutes=470)  # scenario starts 07:50
        self.assertEqual([(e["time"], e["condition"]) for e in events], [("07:50", "heavy_snow"), ("09:00", "snow")])
        self.assertEqual(weather_disruptions(snap)[0]["time"], "06:00")

    def test_saved_toronto_snapshot_replays_offline(self):
        snapshot = load_snapshot(SNAPSHOT)
        self.assertEqual(snapshot["date"], "2025-02-16")
        events = weather_disruptions(snapshot, start_minutes=480)
        self.assertEqual(events[0]["time"], "08:00")
        self.assertIn("snow", events[0]["condition"])

    def test_bad_snapshot_files(self):
        with self.assertRaises(WeatherError):
            load_snapshot(os.path.join(ROOT, "nope.json"))
        with self.assertRaises(WeatherError):
            load_snapshot(SAMPLE_EMERGENCIES)  # valid JSON, but no "hours"

    def test_fetch_uses_one_request_and_builds_a_snapshot(self):
        fake = {"latitude": 43.69, "longitude": -79.41, "timezone": "America/Toronto",
                "hourly": {"time": ["2025-02-16T08:00", "2025-02-16T09:00"], "weather_code": [73, 75],
                           "precipitation": [0.5, 1.0], "snowfall": [0.4, 0.9], "wind_speed_10m": [20, 25]}}
        reply = mock.MagicMock()
        reply.__enter__.return_value = io.BytesIO(json.dumps(fake).encode())
        with mock.patch("urllib.request.urlopen", return_value=reply) as urlopen:
            snap = fetch_open_meteo(43.657, -79.387, "2025-02-16")
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("archive-api.open-meteo.com", urlopen.call_args[0][0])
        self.assertEqual([(h["time"], h["condition"]) for h in snap["hours"]], [("08:00", "snow"), ("09:00", "heavy_snow")])

    def test_fetch_errors_are_clear(self):
        with self.assertRaises(WeatherError):
            fetch_open_meteo(43.6, -79.4, "16/02/2025")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(WeatherError) as ctx:
                fetch_open_meteo(43.6, -79.4, "2025-02-16")
        self.assertIn("could not reach", str(ctx.exception))

    @unittest.skipUnless(any(os.path.exists(p) for p in SYSTEM_CA_FILES), "no system certificate bundle")
    def test_certificate_problem_retries_with_system_bundle_still_verified(self):
        good = mock.MagicMock()
        good.__enter__.return_value = io.BytesIO(json.dumps({"hourly": {"time": ["2025-02-16T08:00"],
                                                                       "weather_code": [0]}}).encode())
        failure = urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))
        with mock.patch("urllib.request.urlopen", side_effect=[failure, good]) as urlopen:
            fetch_open_meteo(43.6, -79.4, "2025-02-16")
        context = urlopen.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)  # verification never switched off

    def test_main_weather_flag_replays_the_snapshot(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main.main(["--demo", "--weather", SNAPSHOT, "--run", "--quiet"])
        self.assertEqual(code, 0)
        self.assertIn("Weather 2025-02-16", out.getvalue())
        self.assertIn("Scenario report at 10:", out.getvalue())  # later weather does not keep it running

    def test_city_point_and_save_snapshot_guard_bad_coords_and_io_errors(self):
        bad_raw = {"center": [None, None], "nodes": [{"id": 1, "lat": 999, "lng": 0}], "edges": []}
        s = make_system(raw=bad_raw)
        self.assertTrue(any("center" in w for w in s.dataset.warnings))
        self.assertTrue(any("out-of-range" in w for w in s.dataset.warnings))
        with self.assertRaises(WeatherError):
            city_point(s.dataset)

        fallback_raw = {"center": ["bad", 0], "nodes": [{"id": 1, "lat": 44.0, "lng": -79.0}], "edges": []}
        s_ok = make_system(raw=fallback_raw)
        self.assertEqual(city_point(s_ok.dataset), (44.0, -79.0))

        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "not_a_dir")
            with open(blocker, "w", encoding="utf-8") as handle:
                handle.write("file")
            with self.assertRaises(WeatherError):
                save_snapshot({"hours": []}, os.path.join(blocker, "snap.json"))

    def test_main_weather_handles_midnight_and_undated_emergencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            midnight = os.path.join(tmp, "midnight.json")
            with open(midnight, "w", encoding="utf-8") as handle:
                json.dump([request(submitted_at="00:00", deadline="02:00")], handle)
            out = io.StringIO()
            with redirect_stdout(out):
                code = main.main(["--emergencies", midnight, "--weather", SNAPSHOT, "--run", "--quiet"])
            self.assertEqual(code, 0)
            self.assertIn("00:00", out.getvalue().splitlines()[1])  # Weather line starts at 00:00

            undated = os.path.join(tmp, "undated.json")
            with open(undated, "w", encoding="utf-8") as handle:
                json.dump([request(submitted_at=None, deadline="09:30")], handle)
            out2 = io.StringIO()
            with redirect_stdout(out2), redirect_stderr(io.StringIO()):
                code2 = main.main(["--emergencies", undated, "--weather", SNAPSHOT, "--run", "--quiet"])
            self.assertEqual(code2, 0)
            self.assertIn("08:00", out2.getvalue())
            self.assertIn("1 on time", out2.getvalue())


class CityTimelineTests(unittest.TestCase):
    def test_sample_morning_ends_with_city_back_to_normal(self):
        s = make_system()
        sim = Simulator(s)
        for raw in load_emergencies_file(SAMPLE_EMERGENCIES):
            sim.schedule_emergency(raw)
        for raw in load_disruptions_file(SAMPLE_DISRUPTIONS):
            sim.schedule_disruption(raw)
        run_until(s, sim, 700, test=self)
        self.assertEqual(sim.input_errors, [])
        self.assertEqual((s.graph.closed_edges, s.graph.closed_nodes, s.graph.edge_slowdowns), (set(), set(), {}))
        self.assertEqual([v.id for v in s.vehicles.values() if v.status == "broken"], [])
        self.assertEqual(s.stranded, {})
        rescued = [t for t in s.dispatcher.trips_for("E5", active_only=False) if t.source_kind == "cargo"]
        self.assertEqual(len(rescued), 1)  # V7's cargo was collected by another truck
        self.assertEqual(s.emergencies["E8"].state, S.FAILED)  # cold shortage kept on purpose
        self.assertEqual(sum(1 for e in s.emergencies.values() if e.state == S.RESOLVED), 7)

    def test_simulation_stops_when_only_later_disruptions_remain(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request())
        sim.schedule_disruption({"time": "23:00", "type": "weather", "condition": "snow"})
        end = sim.run()
        self.assertLess(end, 600)


if __name__ == "__main__":
    unittest.main()
