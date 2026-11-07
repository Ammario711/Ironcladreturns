# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""Greedy matcher constraints, the state machine table, and the event bus."""

import unittest

from core.event_bus import EVENT_TYPES, EventBus, REQUIRED_EVENTS
from core.resources import BROKEN, BUSY
from core.state_machine import (ALLOWED_TRANSITIONS, TERMINAL_STATES, CrisisState as S,
                                CrisisStateMachine, InvalidTransitionError)
from helpers import check_invariants, make_system, request


class MatcherTests(unittest.TestCase):
    def test_capacity_limit_splits_load_over_vehicles(self):
        s = make_system()
        s.set_clock(480)
        s.submit_emergency(request(food_normal=150))  # trucks hold 60 -> 3 trucks
        s.tick(480)
        trips = s.dispatcher.trips_for("E1")
        self.assertEqual(sum(t.normal for t in trips), 150)
        self.assertTrue(all(t.units() <= s.vehicles[t.vehicle_id].capacity for t in trips))
        self.assertEqual(len(trips), 3)
        check_invariants(self, s)

    def test_cold_food_only_in_refrigerated_vehicles(self):
        s = make_system()
        s.set_clock(480)
        s.submit_emergency(request(food_normal=20, food_cold=70))
        s.tick(480)
        for trip in s.dispatcher.trips_for("E1"):
            if trip.cold:
                self.assertTrue(s.vehicles[trip.vehicle_id].refrigerated)
        self.assertEqual(sum(t.cold for t in s.dispatcher.trips_for("E1")), 70)

    def test_normal_food_prefers_non_refrigerated(self):
        s = make_system()
        s.set_clock(480)
        s.submit_emergency(request(food_normal=30))
        s.tick(480)
        (trip,) = s.dispatcher.trips_for("E1")
        self.assertFalse(s.vehicles[trip.vehicle_id].refrigerated)

    def test_no_resource_assigned_twice(self):
        s = make_system()
        s.set_clock(480)
        for i in range(6):
            s.submit_emergency(request(id=f"E{i}", location=["P3", "P4", "P5", "P6"][i % 4],
                                       food_normal=100, food_cold=20))
        s.tick(480)
        vehicles = [t.vehicle_id for t in s.dispatcher.active_trips()]
        self.assertEqual(len(vehicles), len(set(vehicles)))
        self.assertTrue(all(s.vehicles[v].status == BUSY for v in vehicles))
        check_invariants(self, s)

    def test_limited_stock_gives_partial_fulfilment_and_depletion_event(self):
        s = make_system(overrides={"matching": {"max_vehicles_per_emergency_per_cycle": 12}})
        s.set_clock(480)
        s.submit_emergency(request(food_normal=0, food_cold=350))  # only 300 cold exist
        for minute in range(480, 560):
            s.tick(minute)
        e = s.emergencies["E1"]
        self.assertEqual(e.delivered_cold, 300)
        self.assertEqual(len(s.bus.events_of("resource-depleted")), 3)
        self.assertIn("No cold stock left", e.last_wait_reason)
        check_invariants(self, s)

    def test_fuel_constraint(self):
        s = make_system(overrides={"fuel": {"per_km": 1000}})
        s.set_clock(480)
        s.submit_emergency(request())
        s.tick(480)
        e = s.emergencies["E1"]
        self.assertEqual(e.state, S.DELAYED)
        self.assertIn("fuel", e.last_wait_reason)

    def test_unreachable_site_reason(self):
        s = make_system()
        s.set_clock(480)
        s.apply_disruption({"type": "road_closure", "edges": [74, 81]})
        s.submit_emergency(request(location=47))
        s.tick(480)
        e = s.emergencies["E1"]
        self.assertEqual(e.state, S.DELAYED)
        self.assertIn("Route unavailable", e.last_wait_reason)


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.sm = CrisisStateMachine(self.bus)
        s = make_system()
        self.e = s.submit_emergency(request()).emergency  # fresh object, not driven further
        self.e.history.clear()
        self.sm.start(self.e, 480, "received")

    def test_all_nine_states_exist(self):
        self.assertEqual({st.value for st in S}, {
            "Open", "Prioritized", "Resource Assigned", "In Progress", "Re-Routing",
            "Delivered", "Resolved", "Delayed", "Failed"})

    def test_happy_path_logged_with_time_and_explanation(self):
        path = [S.PRIORITIZED, S.RESOURCE_ASSIGNED, S.IN_PROGRESS, S.DELIVERED, S.RESOLVED]
        for minute, state in enumerate(path, start=481):
            self.sm.transition(self.e, state, minute, f"to {state.value}")
        self.assertEqual(self.e.state, S.RESOLVED)
        self.assertEqual([h.time for h in self.e.history], [480, 481, 482, 483, 484, 485])
        self.assertTrue(all(h.explanation for h in self.e.history))
        self.assertEqual(len(self.bus.events_of("crisis-resolved")), 1)

    def test_invalid_transitions_raise(self):
        with self.assertRaises(InvalidTransitionError):
            self.sm.transition(self.e, S.DELIVERED, 481, "skip ahead")
        with self.assertRaises(InvalidTransitionError):
            self.sm.transition(self.e, S.REROUTING, 481, "nothing to re-route yet")
        self.assertEqual(self.e.state, S.OPEN)

    def test_explanation_required(self):
        with self.assertRaises(ValueError):
            self.sm.transition(self.e, S.PRIORITIZED, 481, "  ")

    def test_terminal_states_have_no_exits_and_rerouting_only_from_moving_states(self):
        for state in TERMINAL_STATES:
            self.assertEqual(ALLOWED_TRANSITIONS[state], set())
        sources = {st for st, targets in ALLOWED_TRANSITIONS.items() if S.REROUTING in targets}
        self.assertEqual(sources, {S.RESOURCE_ASSIGNED, S.IN_PROGRESS, S.DELAYED})


class EventBusTests(unittest.TestCase):
    def test_required_events_supported(self):
        for name in ("crisis-created", "priority-changed", "route-updated", "resource-depleted",
                     "road-closed", "vehicle-breakdown", "crisis-resolved"):
            self.assertIn(name, REQUIRED_EVENTS)
            self.assertIn(name, EVENT_TYPES)

    def test_publish_subscribe_order_and_log(self):
        bus, seen = EventBus(), []
        bus.subscribe("road-closed", lambda ev: seen.append(("specific", ev.seq)))
        bus.subscribe("*", lambda ev: seen.append(("wildcard", ev.seq)))
        bus.publish("road-closed", {"edge": 1}, 5)
        self.assertEqual(seen, [("wildcard", 1), ("specific", 1)])
        self.assertEqual(bus.log[0].payload, {"edge": 1})
        self.assertEqual(bus.log[0].time, 5)

    def test_unknown_event_rejected(self):
        bus = EventBus()
        with self.assertRaises(ValueError):
            bus.publish("pizza-ordered", {}, 0)
        with self.assertRaises(ValueError):
            bus.subscribe("pizza-ordered", print)

    def test_unsubscribe_and_error_isolation(self):
        bus, seen = EventBus(raise_handler_errors=False), []

        def broken(_ev):
            raise RuntimeError("boom")

        def good(ev):
            seen.append(ev.type)

        bus.subscribe("road-closed", broken)
        bus.subscribe("road-closed", good)
        bus.publish("road-closed", {}, 0)
        self.assertEqual(seen, ["road-closed"])
        self.assertEqual(len(bus.handler_errors), 1)
        bus.unsubscribe("road-closed", good)
        bus.publish("road-closed", {}, 1)
        self.assertEqual(seen, ["road-closed"])

    def test_components_react_through_bus(self):
        s = make_system()
        s.set_clock(480)
        s.submit_emergency(request())
        types = [ev.type for ev in s.bus.log]
        self.assertEqual(types[0], "crisis-created")  # intake publishes ...
        self.assertIn("state-changed", types)         # ... and the prioritiser reacted
        self.assertEqual(s.emergencies["E1"].state, S.PRIORITIZED)
        s.apply_disruption({"type": "vehicle_breakdown", "vehicle": "V2"})
        self.assertEqual(s.vehicles["V2"].status, BROKEN)
        self.assertEqual(s.bus.log[-1].type, "vehicle-breakdown")


if __name__ == "__main__":
    unittest.main()
