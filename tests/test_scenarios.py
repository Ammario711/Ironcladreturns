# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""End-to-end scenarios through the simulator (the "hidden test" style cases)."""

import unittest

from core.state_machine import CrisisState as S
from core.trips import BLOCKED
from helpers import (SAMPLE_DISRUPTIONS, SAMPLE_EMERGENCIES, SPARSE, check_invariants,
                     make_system, request, run_until)
from loader.input_loader import load_disruptions_file, load_emergencies_file
from sim.scenario_generator import generate_scenario
from sim.simulator import Simulator


def states_visited(e):
    return [h.to_state for h in e.history if not h.is_note]


def sample_run():
    s = make_system()
    sim = Simulator(s)
    for raw in load_emergencies_file(SAMPLE_EMERGENCIES):
        sim.schedule_emergency(raw)
    for raw in load_disruptions_file(SAMPLE_DISRUPTIONS):
        sim.schedule_disruption(raw)
    return s, sim


class ScenarioTests(unittest.TestCase):
    def test_sample_scenario_finishes_with_invariants(self):
        s, sim = sample_run()
        run_until(s, sim, 700, test=self)
        self.assertTrue(sim.finished())
        self.assertTrue(all(e.is_terminal() for e in s.emergencies.values()))
        self.assertEqual(sim.input_errors, [])
        e5 = s.emergencies["E5"]
        self.assertIn(S.REROUTING, states_visited(e5))     # closure on its route
        self.assertEqual(e5.state, S.RESOLVED)
        e3 = s.emergencies["E3"]                            # reefer V9 broke down with cargo
        self.assertIn(S.DELAYED, states_visited(e3))
        self.assertEqual(e3.state, S.RESOLVED)
        self.assertTrue(any(t.source_kind == "cargo" for t in s.dispatcher.trips_for("E3", active_only=False)))
        self.assertEqual(s.emergencies["E8"].state, S.FAILED)  # cold stock ran out

    def test_runs_are_deterministic(self):
        logs = []
        for _ in range(2):
            s, sim = sample_run()
            sim.run()
            logs.append([(ev.time, ev.type, sorted(ev.payload.items(), key=str)) for ev in s.bus.log])
        self.assertEqual(logs[0], logs[1])

    def test_closure_off_route_does_not_reroute(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P4"))  # route 23 -> 22 on Queen St
        sim.schedule_disruption({"time": "08:02", "type": "road_closure", "edge": 75})  # Bloor St, far away
        run_until(s, sim, 540, test=self)
        self.assertNotIn(S.REROUTING, states_visited(s.emergencies["E1"]))
        self.assertEqual(s.emergencies["E1"].state, S.RESOLVED)

    def test_closure_on_route_reroutes(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P4"))
        sim.schedule_disruption({"time": "08:02", "type": "road_closure", "edge": 42})  # Queen St 22-23
        run_until(s, sim, 540, test=self)
        e = s.emergencies["E1"]
        self.assertIn(S.REROUTING, states_visited(e))
        self.assertEqual(e.state, S.RESOLVED)
        self.assertTrue(s.bus.events_of("route-updated"))

    def test_breakdown_after_pickup_reassigns_cargo(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P5", food_normal=40))
        run_until(s, sim, 480)
        (trip,) = s.dispatcher.trips_for("E1")
        run_until(s, sim, int(trip.pickup_time) + 1)       # food is on board
        s.apply_disruption({"type": "vehicle_breakdown", "vehicle": trip.vehicle_id})
        self.assertEqual(len(s.stranded), 1)
        run_until(s, sim, 600, test=self)
        e = s.emergencies["E1"]
        self.assertEqual(e.state, S.RESOLVED)
        self.assertEqual(e.delivered_normal, 40)
        rescue = [t for t in s.dispatcher.trips_for("E1", active_only=False) if t.source_kind == "cargo"]
        self.assertEqual(len(rescue), 1)
        self.assertNotEqual(rescue[0].vehicle_id, trip.vehicle_id)
        hub_total = sum(h.normal for h in s.hubs.values())
        self.assertEqual(hub_total, 900 - 40)  # stranded food reused, hub stock not taken twice

    def test_breakdown_before_pickup_returns_stock(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request(location="P5", food_normal=40))
        run_until(s, sim, 480)
        (trip,) = s.dispatcher.trips_for("E1")
        s.apply_disruption({"type": "vehicle_breakdown", "vehicle": trip.vehicle_id})
        self.assertEqual(s.hubs[trip.source_id].normal, 300)  # reservation released
        run_until(s, sim, 600, test=self)
        self.assertEqual(s.emergencies["E1"].state, S.RESOLVED)

    def test_unreachable_destination_delayed_then_failed(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_disruption({"time": "08:00", "type": "road_closure", "edges": [74, 81]})
        sim.schedule_emergency(request(location=47, deadline="08:40"))
        run_until(s, sim, 700, test=self)
        e = s.emergencies["E1"]
        self.assertEqual(states_visited(e), [S.OPEN, S.PRIORITIZED, S.DELAYED, S.FAILED])
        self.assertIn("Route unavailable", e.history[-1].explanation)

    def test_blocked_vehicle_waits_then_continues_after_reopen(self):
        s = make_system()
        sim = Simulator(s)
        sim.schedule_emergency(request(location=47, deadline="11:00"))
        run_until(s, sim, 480)
        (trip,) = s.dispatcher.trips_for("E1")
        run_until(s, sim, int(trip.pickup_time) + 1)
        s.apply_disruption({"type": "road_closure", "edges": [74, 81]})
        self.assertEqual(trip.status, BLOCKED)
        self.assertEqual(s.emergencies["E1"].state, S.DELAYED)
        run_until(s, sim, int(s.now) + 10, test=self)
        s.apply_disruption({"type": "road_reopen", "edge": 81})
        run_until(s, sim, 700, test=self)
        e = s.emergencies["E1"]
        self.assertEqual(e.state, S.RESOLVED)
        visited = states_visited(e)
        self.assertLess(visited.index(S.REROUTING), visited.index(S.DELAYED))

    def test_simultaneous_requests_most_urgent_served_first(self):
        s = make_system(overrides={"matching": {"max_vehicles_per_emergency_per_cycle": 12}})
        sim = Simulator(s)
        # 3 refrigerated trucks x 40 = 120 cold per cycle; both want 120 at once.
        sim.schedule_emergency(request(id="LOW", location="P6", severity=1, people_affected=10,
                                       location_category="community", food_normal=0, food_cold=120))
        sim.schedule_emergency(request(id="HIGH", location="P5", severity=5, people_affected=400,
                                       food_normal=0, food_cold=120))
        run_until(s, sim, 480, test=self)
        self.assertEqual(sum(t.cold for t in s.dispatcher.trips_for("HIGH")), 120)
        self.assertEqual(s.dispatcher.trips_for("LOW"), [])
        run_until(s, sim, 650, test=self)
        self.assertEqual(s.emergencies["LOW"].state, S.RESOLVED)  # served later, not starved

    def test_invalid_disruptions_are_rejected_with_reasons(self):
        s = make_system()
        for raw, expected in (({"type": "volcano"}, "unknown disruption type"),
                              ({"type": "road_closure", "edge": 9999}, "does not exist"),
                              ({"type": "road_closure", "a": 0, "b": 47}, "no road directly connects"),
                              ({"type": "vehicle_breakdown", "vehicle": "V99"}, "does not exist"),
                              ({"type": "restock", "hub": "P0", "normal": -5}, "positive")):
            ok, message = s.apply_disruption(raw)
            self.assertFalse(ok)
            self.assertIn(expected, message)

    def test_dataset2_style_fixture_with_generated_scenario(self):
        s = make_system(SPARSE)
        emergencies, disruptions = generate_scenario(s.dataset, n_emergencies=6, n_disruptions=3, seed=7)
        sim = Simulator(s)
        for raw in emergencies:
            sim.schedule_emergency(raw)
        for raw in disruptions:
            sim.schedule_disruption(raw)
        sim.run()
        check_invariants(self, s)
        self.assertTrue(sim.finished())
        self.assertEqual(generate_scenario(s.dataset, 6, 3, seed=7)[0], emergencies)  # same seed, same scenario

    def test_disconnected_site_fails_immediately(self):
        s = make_system(SPARSE)
        s.set_clock(480)
        s.submit_emergency({"id": "X", "location": "S3", "crisis_type": "flood", "people_affected": 20,
                            "food_normal": 10, "severity": 4, "deadline": "10:00", "submitted_at": "08:00"})
        s.tick(480)
        e = s.emergencies["X"]
        self.assertEqual(e.state, S.FAILED)
        self.assertIn("not connected", e.history[-1].explanation)


if __name__ == "__main__":
    unittest.main()
