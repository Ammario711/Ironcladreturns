# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""Tests for the fixes made after the external (Gemini) code review.

One class per area, in the same order as the review: input, loaders/config,
routing/trips/fuel/matcher, scoring/system/simulator, CLI.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from core.disruptions import parse_disruption
from core.emergency import validate_emergency
from core.graph import Edge, Graph, Node
from core.routing import alternative_routes, find_route, path_from_tree, shortest_path_tree
from core.scoring import compute_score
from core.state_machine import CrisisState as S
from core.trips import STRANDED
from core.utils import haversine_km, normalize_id, parse_time
from helpers import TORONTO, check_invariants, config, make_system, request, run_until
from loader.config_loader import ConfigError, apply_overrides, load_config
from loader.dataset_loader import DatasetError, load_dataset
from loader.input_loader import InputFileError, load_records
from sim.scenario_generator import generate_scenario
from sim.simulator import Simulator
from ui.cli import CrisisCLI


def tiny_dataset(nodes, edges, hubs=(), trucks=(), pois=()):
    """Raw dataset dict built from short tuples (keeps the tests readable)."""
    return {
        "nodes": [{"id": n} for n in nodes],
        "edges": [{"id": eid, "a": a, "b": b, "w": w, "km": w} for eid, a, b, w in edges],
        "pois": [{"id": pid, "kind": kind, "node": node} for pid, kind, node in pois],
        "hubs": [{"poiId": pid, "normal": normal, "cold": cold} for pid, normal, cold in hubs],
        "trucks": [{"id": vid, "home": home, "refrigerated": reefer, "capacity": cap}
                   for vid, home, reefer, cap in trucks],
    }


# ====================================================================== 1. input
class InputSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config()
        cls.ds = load_dataset(TORONTO, cls.cfg)

    def check(self, **fields):
        """request() always has food_normal/food_cold; drop them unless this test gives them."""
        raw = request(**fields)
        for key in ("food_normal", "food_cold"):
            if key not in fields:
                raw.pop(key, None)
        return validate_emergency(raw, self.ds.graph, self.ds.pois, self.cfg)

    def test_single_food_quantity_goes_to_normal_or_cold(self):
        normal = self.check(food_quantity=50).emergency
        self.assertEqual((normal.need_normal, normal.need_cold), (50, 0))
        cold = self.check(food_quantity_needed=30, refrigeration_requirements=True).emergency
        self.assertEqual((cold.need_normal, cold.need_cold), (0, 30))
        both = self.check(food_normal=10, units=99).emergency  # explicit split wins
        self.assertEqual(both.need_normal, 10)

    def test_number_of_people_affected_alias(self):
        r = self.check(food_normal=5, people_affected=None, number_of_people_affected=12)
        self.assertEqual(r.emergency.people, 12)

    def test_lat_lng_must_be_in_range(self):
        r = self.check(food_normal=5, location={"lat": 95, "lng": 0})
        self.assertIn("lat must be -90..90", " ".join(r.errors))

    def test_blank_or_non_text_fields_rejected(self):
        self.assertIn("id cannot be blank", " ".join(self.check(food_normal=5, id="").errors))
        self.assertIn("id cannot be blank", " ".join(self.check(food_normal=5, id="   ").errors))
        self.assertIn("id must be text", " ".join(self.check(food_normal=5, id=[1]).errors))
        self.assertTrue(self.check(food_normal=5, id=7).ok)  # numeric ids are fine
        self.assertIn("crisis_type is required", " ".join(self.check(food_normal=5, crisis_type="  ").errors))
        self.assertIn("crisis_type must be text", " ".join(self.check(food_normal=5, crisis_type=5).errors))
        r = self.check(food_normal=5, location=12, location_category="   ")
        self.assertIn("location_category is required", " ".join(r.errors))

    def test_edges_must_be_a_non_empty_list(self):
        g, v, h = self.ds.graph, self.ds.vehicles, self.ds.hubs
        _d, errors = parse_disruption({"type": "road_closure", "edges": "19"}, g, v, h)
        self.assertIn("non-empty list", " ".join(errors))  # NOT edges 1 and 9
        _d, errors = parse_disruption({"type": "road_closure", "edges": []}, g, v, h)
        self.assertIn("non-empty list", " ".join(errors))

    def test_restock_null_amount_counts_as_zero(self):
        d, errors = parse_disruption({"type": "restock", "hub": "P1", "normal": None, "cold": 5},
                                     self.ds.graph, self.ds.vehicles, self.ds.hubs)
        self.assertEqual(errors, [])
        self.assertEqual((d.normal, d.cold), (0, 5))

    def test_parse_time_forms(self):
        self.assertEqual(parse_time("08:05:30"), 485.5)
        self.assertEqual(parse_time("00:10+1d"), 1450)            # what fmt_time prints
        self.assertEqual(parse_time("2026-11-07T08:30"), 510)     # ISO date ignored
        for bad in ("24:00", "25:00", "08:60", "8:5"):
            with self.assertRaises(ValueError):
                parse_time(bad)

    def test_id_and_haversine_edge_cases(self):
        self.assertEqual(normalize_id("--1"), "--1")  # stays text instead of crashing
        self.assertEqual(normalize_id(" -4 "), -4)
        self.assertEqual(haversine_km(43.6, -79.4, 43.6, -79.4), 0.0)


# ====================================================================== 2. loaders + config
class LoaderConfigTests(unittest.TestCase):
    def test_terrain_speed_factor_dict_form_and_config_precedence(self):
        raw = tiny_dataset([1, 2], [("e", 1, 2, 2.0)])
        raw["edges"][0]["terrain"] = "mud"
        raw["terrain"] = {"mud": {"speed_factor": 0.5}}  # half speed -> double cost
        self.assertEqual(make_system(raw=raw).graph.edge_cost("e"), 4.0)
        raw["terrain"] = {"mud": 1.5}
        self.assertEqual(make_system(raw=raw).graph.edge_cost("e"), 3.0)
        forced = make_system(raw=raw, overrides={"routing": {"terrain_multipliers": {"mud": 3.0}}})
        self.assertEqual(forced.graph.edge_cost("e"), 6.0)  # explicit --config value wins

    def test_null_stock_and_vehicles_fallback(self):
        raw = tiny_dataset([1, 2], [("e", 1, 2, 1.0)], pois=[("H", "hub", 1)])
        raw["hubs"] = [{"poiId": "H", "normal": None, "cold": 3}]
        raw["trucks"] = None
        raw["vehicles"] = [{"id": "V", "home": 1, "capacity": 5}]
        s = make_system(raw=raw)
        self.assertEqual((s.hubs["H"].normal, s.hubs["H"].cold), (0, 3))
        self.assertEqual(list(s.vehicles), ["V"])

    def test_files_with_byte_order_mark_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bom.json")
            with open(path, "w", encoding="utf-8-sig") as handle:
                json.dump({"nodes": [{"id": 1}], "emergencies": [], "simulation": {}}, handle)
            self.assertEqual(len(load_dataset(path, config()).graph.nodes), 1)
            self.assertEqual(load_records(path, ("emergencies",)), [])
            load_config(path)  # unknown top-level keys are just merged; must not crash

    def test_folders_give_clear_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DatasetError):
                load_dataset(tmp, config())
            with self.assertRaises(InputFileError):
                load_records(tmp, ("emergencies",))
            with self.assertRaises(ConfigError):
                load_config(tmp)

    def test_config_values_that_would_crash_or_hang_are_rejected(self):
        for bad in ({"simulation": {"step_minutes": 0}}, {"routing": {"default_speed_kmh": 0}},
                    {"scoring": {"people_cap": 0}}, {"fuel": {"per_km": -1}},
                    {"simulation": {"step_minutes": "5"}}):
            with self.assertRaises(ConfigError):
                apply_overrides(bad)


# ====================================================================== 3. routing, trips, fuel, matcher
def square():
    g = Graph()
    for n in "ABCD":
        g.add_node(Node(n, n))
    for eid, a, b, w in (("ab", "A", "B", 1), ("bc", "B", "C", 5), ("ad", "A", "D", 1), ("dc", "D", "C", 1)):
        g.add_edge(Edge(eid, a, b, km=w, w=w))
    return g


class RoutingConsistencyTests(unittest.TestCase):
    def test_trees_agree_with_find_route_on_closed_nodes(self):
        g = square()
        g.close_node("D")  # a vehicle standing at D may still drive away
        self.assertEqual(find_route(g, "D", "C").cost, 1)
        self.assertEqual(path_from_tree(shortest_path_tree(g, "C"), "D").cost, 1)
        self.assertEqual(find_route(g, "A", "C").path, ["A", "B", "C"])  # never THROUGH D
        g.close_node("C")  # nobody may drive INTO a closed destination
        self.assertEqual(shortest_path_tree(g, "C").dist, {"C": 0.0})
        self.assertFalse(find_route(g, "A", "C").found)

    def test_parallel_roads_are_separate_alternatives(self):
        g = Graph()
        g.add_node(Node("X", "X"))
        g.add_node(Node("Y", "Y"))
        g.add_edge(Edge("fast", "X", "Y", km=1, w=1))
        g.add_edge(Edge("slow", "X", "Y", km=1, w=2))
        self.assertEqual([r.edges for r in alternative_routes(g, "X", "Y")], [["fast"], ["slow"]])


class TripFuelMatcherTests(unittest.TestCase):
    def start_p4_trip(self, until):
        """One request at P4 (node 22) served from hub P2 (node 23) over edge 42."""
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P4", food_normal=40))
        run_until(s, sim, until)
        (trip,) = s.dispatcher.trips_for("E1")
        return s, sim, trip

    def test_reroute_while_loading_keeps_remaining_load_time(self):
        s, sim, trip = self.start_p4_trip(482)
        self.assertEqual(trip.pickup_time, 485)
        s.apply_disruption({"type": "road_closure", "edge": 42})
        self.assertEqual(trip.pickup_time, 485)  # 3 minutes of loading left, not a fresh 5
        self.assertFalse(trip.is_loaded(484))
        self.assertTrue(trip.is_loaded(485))
        self.assertNotIn(42, trip.edges)

    def test_detour_without_fuel_strands_cargo_instead_of_going_negative(self):
        s, sim, trip = self.start_p4_trip(486)  # loaded, still at node 23
        s.vehicles[trip.vehicle_id].fuel = 0.0
        s.apply_disruption({"type": "road_closure", "edge": 42})  # detour is longer
        self.assertEqual(trip.status, STRANDED)
        self.assertEqual(len(s.stranded), 1)
        run_until(s, sim, 600, test=self)  # invariants include fuel >= 0
        self.assertEqual(s.emergencies["E1"].state, S.RESOLVED)
        self.assertEqual(s.stranded, {})  # delivered cargo is cleaned up

    def test_cold_phase_prefers_full_reefer_load_when_on_time(self):
        raw = tiny_dataset(["S", "A", "B"], [("sa", "S", "A", 1.0), ("sb", "S", "B", 10.0)],
                           pois=[("HA", "hub", "A"), ("HB", "hub", "B")],
                           hubs=[("HA", 0, 5), ("HB", 0, 40)], trucks=[("R", "S", True, 40)])
        cold_request = {"id": "X", "location": "S", "crisis_type": "flood", "location_category": "shelter",
                        "people_affected": 10, "food_cold": 40, "severity": 3, "submitted_at": 0}
        s = make_system(raw=raw)
        s.submit_emergency(dict(cold_request, deadline=200))
        s.tick(0)
        (trip,) = s.dispatcher.trips_for("X")
        self.assertEqual((trip.source_id, trip.cold), ("HB", 40))  # not 5 units from the nearer HA
        s = make_system(raw=raw)
        s.submit_emergency(dict(cold_request, deadline=20))  # HB would be late
        s.tick(0)
        (trip,) = s.dispatcher.trips_for("X")
        self.assertEqual(trip.source_id, "HA")

    def test_disconnected_fleet_is_not_blamed_on_closures(self):
        raw = tiny_dataset(["H", "S", "ISLAND"], [("hs", "H", "S", 1.0)],
                           pois=[("H1", "hub", "H")], hubs=[("H1", 0, 10)], trucks=[("R", "ISLAND", True, 40)])
        s = make_system(raw=raw)
        s.submit_emergency({"id": "X", "location": "S", "crisis_type": "flood", "location_category": "shelter",
                            "people_affected": 10, "food_cold": 5, "severity": 3, "deadline": 100,
                            "submitted_at": 0})
        s.tick(0)
        e = s.emergencies["X"]
        self.assertIn("no road at all", e.last_wait_reason)
        self.assertEqual(e.state, S.DELAYED)  # a repair or restock could still fix it

    def test_failure_note_does_not_call_collected_cargo_abandoned(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P5", food_normal=40))
        run_until(s, sim, 480)
        (trip,) = s.dispatcher.trips_for("E1")
        run_until(s, sim, int(trip.pickup_time) + 1)
        s.apply_disruption({"type": "vehicle_breakdown", "vehicle": trip.vehicle_id})
        run_until(s, sim, 620, test=self)
        self.assertEqual(s.emergencies["E1"].state, S.RESOLVED)
        self.assertEqual(s.stranded, {})


# ====================================================================== 4. scoring, system, simulator
class ScoringSystemSimulatorTests(unittest.TestCase):
    def test_slack_counts_service_time_and_unreachable_as_far(self):
        cfg = config()
        e = make_system().submit_emergency(request(deadline="09:00")).emergency
        base = compute_score(e, 480, 0.0, cfg["scoring"], cfg["validation"])[1]["deadline"]["factor"]
        served = compute_score(e, 480, 0.0, cfg["scoring"], cfg["validation"], service_minutes=10)[1]
        unreachable = compute_score(e, 480, None, cfg["scoring"], cfg["validation"])[1]
        self.assertGreater(served["deadline"]["factor"], base)
        self.assertGreater(unreachable["deadline"]["factor"], base)

    def test_resource_depleted_rescores_waiting_requests(self):
        s = make_system()
        s.set_clock(480)
        s.submit_emergency(request(id="A", location=33, location_category="hospital", severity=5,
                                   food_normal=0, food_cold=100))  # empties P0's cold stock
        s.submit_emergency(request(id="B", location="P3", severity=1, food_normal=0, food_cold=10))
        s.tick(480)
        reasons = [(ev.payload["id"], ev.payload["reason"]) for ev in s.bus.events_of("priority-changed")]
        self.assertIn(("B", "resource-depleted"), reasons)

    def test_partial_assignment_announces_its_own_priority_change(self):
        s = make_system()
        s.set_clock(480)
        s.submit_emergency(request(food_normal=250))  # 3 trucks x 60 per cycle -> 70 left over
        s.tick(480)
        reasons = [ev.payload["reason"] for ev in s.bus.events_of("priority-changed")]
        self.assertIn("remaining need changed after a partial assignment", reasons)
        self.assertEqual(len(s.queue.ids()), 1)

    def test_run_never_reticks_or_passes_until(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request())
        ticks = []
        original = s.tick
        s.tick = lambda now: (ticks.append(now), original(now))[1]
        sim.run(until=485.5)
        sim.run(until=487)
        self.assertEqual(ticks, [480, 481, 482, 483, 484, 485, 486, 487])

    def test_undated_input_starts_with_the_scenario(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request(id="DATED"))
        sim.schedule_emergency(request(id="UNDATED", submitted_at=None))
        sim.run(until=480)
        self.assertEqual(s.emergencies["UNDATED"].submitted_at, 480)  # not 00:00

    def test_advance_moves_forward_when_minutes_smaller_than_step(self):
        s = make_system(overrides={"simulation": {"step_minutes": 5}})
        sim = Simulator(s)
        sim.schedule_emergency(request())
        self.assertEqual(sim.advance(1), 481)
        self.assertEqual(s.now, 481)
        self.assertEqual(sim.advance(7), 488)
        self.assertEqual(s.now, 488)

    def test_report_shows_na_and_every_rejected_request(self):
        s = make_system()
        sim = Simulator(s)
        s.submit_emergency({"location": "nowhere"})  # e.g. a CLI one-liner
        report = sim.report()
        self.assertIn("N/A", report)
        self.assertIn("1 rejected requests", report)
        self.assertIn("REJECTED emergency", report)


# ====================================================================== 5. CLI
class CliGuardTests(unittest.TestCase):
    def setUp(self):
        self.system = make_system()
        self.sim = Simulator(self.system)
        self.cli = CrisisCLI(self.system, self.sim, live=False)

    def run_cli(self, command, answers=()):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=list(answers)), redirect_stdout(out):
            self.cli.onecmd(command)
        return out.getvalue()

    def test_tick_rejects_bad_minutes(self):
        for bad in ("nan", "inf", "0", "-1"):
            self.assertIn("must be more than 0", self.run_cli(f"tick {bad}"))
        self.assertEqual(self.system.now, 0)

    def test_events_zero_is_refused(self):
        self.assertIn("N must be 1 or more", self.run_cli("events 0"))

    def test_add_form_survives_ctrl_d(self):
        self.assertIn("Cancelled", self.run_cli("add", answers=[EOFError()]))

    def test_future_submission_is_scheduled_not_dispatched(self):
        text = self.run_cli("add location=P4 crisis_type=flood location_category=shelter people_affected=30 "
                            "food_normal=20 severity=3 deadline=10:00 submitted_at=09:00")
        self.assertIn("Scheduled", text)
        self.assertEqual(self.system.emergencies, {})
        self.assertEqual(len(self.sim.pending()), 1)

    def test_one_line_lat_lng(self):
        self.run_cli("add location=43.6489,-79.3711 crisis_type=fire location_category=community "
                     "people_affected=30 food_normal=20 severity=3 deadline=10:00")
        (e,) = self.system.emergencies.values()
        self.assertEqual(e.node, 14)


if __name__ == "__main__":
    unittest.main()
