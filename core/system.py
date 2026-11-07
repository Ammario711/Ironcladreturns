# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""CrisisResponseSystem: the single object the CLI / simulator talk to.

It wires the components together through the event bus:

  submit_emergency()  validate -> Open -> publish crisis-created
  crisis-created      (subscriber here) score -> push on heap -> Prioritized
  apply_disruption()  change graph / vehicle / hub -> publish road-closed,
                      vehicle-breakdown, ... (dispatcher reacts, we re-score)
  tick(now)           1. deliveries due     (dispatcher.advance_trips)
                      2. Delivered -> Resolved after confirmation delay
                      3. re-score everything waiting (aging + deadlines)
                      4. dispatch cycle     (greedy, most urgent first)
                      5. give up on hopeless requests -> Failed

Time is a simulation clock in minutes (not the wall clock), so every run
with the same inputs produces exactly the same log (deterministic).
"""

from core.dispatcher import Dispatcher
from core.disruptions import Disruption, parse_disruption
from core.emergency import validate_emergency
from core.event_bus import EventBus
from core.matcher import Matcher
from core.priority_queue import CrisisQueue
from core.resources import BROKEN, IDLE
from core.routing import shortest_path_tree
from core.scoring import aging_bonus, compute_score, explain
from core.state_machine import CrisisState as S, CrisisStateMachine
from core.trips import BLOCKED, EN_ROUTE
from core.utils import fmt_time, id_sort_key


class CrisisResponseSystem:
    def __init__(self, dataset, config, bus=None):
        self.dataset = dataset
        self.config = config
        self.graph = dataset.graph
        self.pois = dataset.pois
        self.hubs = dataset.hubs
        self.vehicles = dataset.vehicles
        self.bus = bus or EventBus()
        self.state_machine = CrisisStateMachine(self.bus)
        self.queue = CrisisQueue(config["queue"]["round_digits"])
        self.emergencies = {}   # id -> Emergency
        self.stranded = {}      # cargo id -> StrandedCargo
        self.rejected = []      # (raw input, [errors]) for the report
        self.now = 0.0
        self._trip_counter = 0
        self._cargo_counter = 0
        self._tree_cache = {}   # site node -> PathTree (valid for one graph.version)
        self._tree_version = self.graph.version
        self.matcher = Matcher(self.graph, self.hubs, self.vehicles, self.stranded,
                               config, self.bus, self._new_trip_id)
        # The dispatcher subscribes first, so on a road closure vehicles are
        # re-routed BEFORE priorities are recalculated.
        self.dispatcher = Dispatcher(self)
        self.bus.subscribe("crisis-created", self._on_crisis_created)
        # Re-score waiting requests whenever conditions change. resource-depleted
        # can fire DURING a dispatch cycle: that is safe, because the request
        # being matched has already been popped, and the rebuilt heap only
        # holds requests still waiting their turn in this same cycle.
        for event_type in ("road-closed", "road-reopened", "vehicle-breakdown",
                           "vehicle-repaired", "resource-restocked", "resource-depleted"):
            self.bus.subscribe(event_type, self._on_conditions_changed)

    # ================================================================ intake
    def submit_emergency(self, raw, now=None):
        """Validate and register a request. Returns the ValidationResult."""
        if now is not None:
            self.set_clock(now)
        result = validate_emergency(raw, self.graph, self.pois, self.config,
                                    existing_ids=self.emergencies.keys(),
                                    auto_id=self._next_emergency_id(), now=self.now)
        if not result.ok:
            self.rejected.append((raw, result.errors))
            return result
        e = result.emergency
        self.emergencies[e.id] = e
        self.state_machine.start(e, self.now, f"Request received: {e.short()}")
        self.bus.publish("crisis-created", {"id": e.id, "node": e.node, "summary": e.short(),
                                            "warnings": list(result.warnings)}, self.now)
        return result

    def _next_emergency_id(self):
        """Id for a request entered without one. The AUTO prefix avoids
        clashing with ids in input files (E1, E2...) scheduled for later."""
        n = 1
        while f"AUTO{n}" in self.emergencies:
            n += 1
        return f"AUTO{n}"

    def _on_crisis_created(self, event):
        e = self.emergencies[event.payload["id"]]
        self.enqueue(e, event.time)
        e.last_published_priority = e.priority
        travel = self.travel_estimate(e)
        travel_text = f"{travel:.1f} min from nearest stocked hub" if travel is not None else "no hub reachable now"
        self.state_machine.transition(
            e, S.PRIORITIZED, event.time,
            f"Priority {e.priority:.1f} ({explain(e.score_breakdown)}); {travel_text}; "
            f"queue position {self._queue_position(e.id)} of {len(self.queue)}")

    # ================================================================ priorities
    def travel_estimate(self, e):
        """Fastest drive (minutes) from any source that has the needed food to the site.

        Cached per site node until the road network changes (graph.version),
        so repeated scoring does not rerun Dijkstra every minute.
        """
        if self._tree_version != self.graph.version:
            self._tree_cache, self._tree_version = {}, self.graph.version
        if e.node in self.graph.closed_nodes:
            return None
        tree = self._tree_cache.get(e.node)
        if tree is None:
            tree = self._tree_cache[e.node] = shortest_path_tree(self.graph, e.node)
        need_normal = e.unassigned_normal > 0 or e.unassigned_total() == 0
        need_cold = e.unassigned_cold > 0
        best = None
        stores = list(self.hubs.values()) + [c for c in self.stranded.values() if c.emergency_id == e.id]
        for store in stores:
            useful = (need_normal and store.normal > 0) or (need_cold and store.cold > 0)
            if useful and store.node in tree.dist:
                best = tree.dist[store.node] if best is None else min(best, tree.dist[store.node])
        return best

    def compute_priority(self, e, now):
        """Score + aging. Also stores the breakdown on the emergency for display."""
        mcfg = self.config["matching"]
        score, breakdown = compute_score(e, now, self.travel_estimate(e),
                                         self.config["scoring"], self.config["validation"],
                                         service_minutes=mcfg["load_minutes"] + mcfg["unload_minutes"])
        bonus = aging_bonus(e, now, self.config["queue"]["aging_points_per_minute"])
        e.base_score, e.score_breakdown, e.aging_bonus = score, breakdown, bonus
        e.priority = round(score + bonus, self.config["queue"]["round_digits"])
        return e.priority

    def enqueue(self, e, now, reason=None):
        """(Re)insert an emergency that still has unassigned food.

        `reason` (e.g. after a partial assignment) announces the new priority
        right away, so the next priority-changed event starts from the right
        "old" value.
        """
        if e.is_terminal() or e.unassigned_total() == 0:
            return
        before = e.priority
        new = self.compute_priority(e, now)
        self.queue.push(e.id, new, e.submitted_at, e.people)
        if reason:
            self._announce_priority(e, before, new, now, reason)

    def refresh_priorities(self, now, reason=None):
        """Re-score every waiting emergency and rebuild the heap: O(n) scoring
        (travel times are cached) + O(n) heapify. No sorting needed.

        `reason` is given when conditions changed (closure, restock...).
        """
        items = []
        for eid in self.queue.ids():
            e = self.emergencies[eid]
            before = e.priority
            new = self.compute_priority(e, now)
            items.append((eid, new, e.submitted_at, e.people))
            self._announce_priority(e, before, new, now, reason)
        self.queue.rebuild(items)

    def _announce_priority(self, e, before, new, now, reason):
        """Publish priority-changed when the priority moved at least the
        configured threshold since the LAST ANNOUNCEMENT, or, when a reason is
        given, when this update changed it at all. "old" is always the last
        announced value, i.e. what listeners last saw."""
        last = e.last_published_priority
        if last is None:  # first value is announced by the Prioritized transition
            e.last_published_priority = new
            return
        threshold = self.config["queue"]["priority_change_event_threshold"]
        if abs(new - last) >= threshold or (reason and abs(new - before) >= 0.01):
            why = reason or "re-score: waiting time, deadline, remaining need"
            self.bus.publish("priority-changed", {
                "id": e.id, "old": last, "new": new, "reason": why,
                "message": f"{e.id} priority {last:.1f} -> {new:.1f} ({why})",
            }, now)
            e.last_published_priority = new

    def _on_conditions_changed(self, event):
        self.refresh_priorities(event.time, reason=event.type)

    def _queue_position(self, eid):
        for position, (other, _p) in enumerate(self.queue.ordered(), start=1):
            if other == eid:
                return position
        return None

    # ================================================================ disruptions
    def apply_disruption(self, raw, now=None):
        """Apply a disruption (dict or Disruption). Returns (ok, message)."""
        if now is not None:
            self.set_clock(now)
        if isinstance(raw, Disruption):
            d = raw
        else:
            d, errors = parse_disruption(raw, self.graph, self.vehicles, self.hubs, now=self.now)
            if errors:
                return False, "Disruption rejected: " + "; ".join(errors)
        t, g = self.now, self.graph
        why = f" ({d.reason})" if d.reason else ""
        if d.type in ("road_closure", "road_reopen"):
            closing = d.type == "road_closure"
            changed = [e for e in d.edges if (g.close_edge(e) if closing else g.reopen_edge(e))]
            for edge_id in changed:
                label = g.edge_label(edge_id)
                self.bus.publish("road-closed" if closing else "road-reopened", {
                    "edge": edge_id, "reason": d.reason,
                    "message": f"Road {'closed' if closing else 'reopened'}: {label}{why}"}, t)
            if not changed:
                return True, f"No change: road(s) {d.edges} already {'closed' if closing else 'open'}"
            return True, f"Road(s) {changed} {'closed' if closing else 'reopened'}{why}"
        if d.type in ("node_closure", "node_reopen"):
            closing = d.type == "node_closure"
            if not (g.close_node(d.node) if closing else g.reopen_node(d.node)):
                return True, f"No change: node {d.node} already {'closed' if closing else 'open'}"
            self.bus.publish("road-closed" if closing else "road-reopened", {
                "node": d.node, "reason": d.reason,
                "message": f"Intersection {'closed' if closing else 'reopened'}: {g.node_label(d.node)}{why}"}, t)
            return True, f"Node {d.node} {'closed' if closing else 'reopened'}{why}"
        if d.type == "vehicle_breakdown":
            vehicle = self.vehicles[d.vehicle]
            if vehicle.status == BROKEN:
                return True, f"No change: {vehicle.id} is already broken"
            vehicle.status = BROKEN
            self.bus.publish("vehicle-breakdown", {
                "vehicle": vehicle.id, "trip": vehicle.trip_id, "reason": d.reason,
                "message": f"Vehicle {vehicle.id} broke down{why}"}, t)
            return True, f"{vehicle.id} marked broken{why}"
        if d.type == "vehicle_repair":
            vehicle = self.vehicles[d.vehicle]
            if vehicle.status != BROKEN:
                return True, f"No change: {vehicle.id} is not broken"
            vehicle.status = IDLE
            vehicle.trip_id = None
            self.bus.publish("vehicle-repaired", {
                "vehicle": vehicle.id, "message": f"Vehicle {vehicle.id} repaired at {g.node_label(vehicle.location)}"}, t)
            return True, f"{vehicle.id} repaired"
        if d.type == "restock":
            hub = self.hubs[d.hub]
            hub.normal += d.normal
            hub.cold += d.cold
            self.bus.publish("resource-restocked", {
                "hub": hub.id, "normal": d.normal, "cold": d.cold,
                "message": f"Hub {hub.id} restocked +{d.normal} normal / +{d.cold} cold{why}"}, t)
            return True, f"Hub {hub.id} now has {hub.normal} normal / {hub.cold} cold"
        return False, f"Unsupported disruption type {d.type}"

    # ================================================================ clock
    def set_clock(self, now):
        """Move the clock forward and record any deliveries that are due."""
        if now < self.now - 1e-9:
            raise ValueError(f"clock cannot go backwards ({fmt_time(now)} < {fmt_time(self.now)})")
        self.now = now
        self.dispatcher.advance_trips(now)

    def tick(self, now):
        """One full update of the whole system at time `now`."""
        self.set_clock(now)
        self._confirm_deliveries(now)
        self.refresh_priorities(now)
        self.dispatcher.dispatch_cycle(now)
        self._check_failures(now)

    def _confirm_deliveries(self, now):
        wait = self.config["lifecycle"]["confirm_minutes"]
        for e in self._sorted_emergencies():
            if e.state == S.DELIVERED and now >= e.delivered_at + wait:
                self.state_machine.transition(e, S.RESOLVED, now,
                                              f"Site confirmed receipt (delivered {fmt_time(e.delivered_at)}); crisis closed")

    def _check_failures(self, now):
        """Give up on requests past deadline + grace with nothing moving."""
        grace = self.config["lifecycle"]["fail_grace_minutes"]
        for e in self._sorted_emergencies():
            if e.is_terminal() or now <= e.deadline + grace:
                continue
            trips = self.dispatcher.trips_for(e.id)
            if any(t.status == EN_ROUTE for t in trips):
                continue  # still delivering, even if late
            blocked = [t for t in trips if t.status == BLOCKED]
            if e.unassigned_total() == 0 and not blocked:
                continue
            notes = []
            for trip in blocked:  # the stuck vehicle gives up
                here = self.vehicles[trip.vehicle_id].location
                if trip.source_kind == "hub":
                    # Food goes back into its hub's stock (simplification: we assume
                    # it is returned once roads allow).
                    self.dispatcher._cancel_trip(trip, now, here)
                    notes.append(f"{trip.vehicle_id} released, cargo returned to {trip.source_id} stock")
                else:
                    # Rescued cargo belongs to THIS failed request only: written
                    # off, not put back into a stranded pile nobody will collect.
                    self.dispatcher._cancel_trip(trip, now, here, return_stock=False)
                    notes.append(f"{trip.vehicle_id} released; {trip.units()} rescued units written off")
            for cargo in [c for c in self.stranded.values() if c.emergency_id == e.id]:
                if cargo.units() > 0:  # empty piles were already collected; just tidy them away
                    notes.append(f"{cargo.units()} units in stranded cargo {cargo.id} could not be collected")
                del self.stranded[cargo.id]
            missing = e.requested_total() - e.delivered_total()
            reason = e.last_wait_reason or "no resources or route"
            self.fail(e, now, f"Deadline {fmt_time(e.deadline)} + {grace} min grace passed; {missing} units could "
                              f"not be supplied ({reason}). Delivered {e.delivered_total()}/{e.requested_total()} units"
                              + (f". {'; '.join(notes)}" if notes else ""))

    def fail(self, e, now, explanation):
        if not self.state_machine.can_transition(e, S.FAILED):
            self.state_machine.transition(e, S.DELAYED, now, f"Cannot continue: {explanation}")
        self.state_machine.transition(e, S.FAILED, now, explanation)
        self.queue.remove(e.id)

    # ================================================================ helpers / views
    def _new_trip_id(self):
        self._trip_counter += 1
        return f"T{self._trip_counter}"

    def new_cargo_id(self):
        self._cargo_counter += 1
        return f"C{self._cargo_counter}"

    def _sorted_emergencies(self):
        return [self.emergencies[k] for k in sorted(self.emergencies, key=id_sort_key)]

    def all_done(self):
        """True when every emergency is Resolved or Failed."""
        return all(e.is_terminal() for e in self.emergencies.values())

    def stats(self):
        es = list(self.emergencies.values())
        by_state = {}
        for e in es:
            by_state[e.state.value] = by_state.get(e.state.value, 0) + 1
        resolved = [e for e in es if e.delivered_at is not None]
        on_time = [e for e in resolved if e.delivered_at <= e.deadline]
        response = [e.delivered_at - e.submitted_at for e in resolved]
        return {
            "time": fmt_time(self.now),
            "emergencies": len(es),
            "rejected_inputs": len(self.rejected),
            "by_state": by_state,
            "fully_delivered": len(resolved),
            "on_time": len(on_time),
            "avg_response_minutes": round(sum(response) / len(response), 1) if response else None,
            "units_requested": sum(e.requested_total() for e in es),
            "units_delivered": sum(e.delivered_total() for e in es),
            "people_fully_served": sum(e.people for e in resolved),
            "stock_left_normal": sum(h.normal for h in self.hubs.values()),
            "stock_left_cold": sum(h.cold for h in self.hubs.values()),
            "vehicles_busy": sum(1 for v in self.vehicles.values() if v.status != IDLE and v.status != BROKEN),
            "vehicles_broken": sum(1 for v in self.vehicles.values() if v.status == BROKEN),
            "events": len(self.bus.log),
        }
