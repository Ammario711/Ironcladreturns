# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""Dispatcher: owns the trips.

* dispatch_cycle  - pops emergencies in priority order and asks the matcher
                    for vehicles (greedy, most urgent first)
* advance_trips   - moves the clock forward for every trip; records deliveries
* on_road_closed  - (bus subscriber) re-routes ONLY trips whose remaining
                    route is now blocked -> Re-Routing state
* on_road_reopened- (bus subscriber) lets blocked vehicles move again
* on_vehicle_breakdown - (bus subscriber) returns un-picked stock to its hub,
                    or turns on-board food into StrandedCargo that another
                    vehicle will collect (reassigning the remaining cargo)

The emergency's state is decided in ONE place, settle_state(), from the
trips it has. That keeps multi-vehicle emergencies consistent.
"""

from core.resources import BROKEN, IDLE, StrandedCargo
from core.routing import RouteResult, find_route
from core.state_machine import CrisisState as S
from core.trips import BLOCKED, CANCELLED, DELIVERED, EN_ROUTE, STRANDED
from core.utils import fmt_time, id_sort_key


def _units_text(normal, cold):
    parts = []
    if normal:
        parts.append(f"{normal} normal")
    if cold:
        parts.append(f"{cold} cold")
    return " + ".join(parts) or "0 units"


class Dispatcher:
    def __init__(self, system):
        self.system = system
        self.trips = {}  # trip id -> Trip (all trips ever, including finished)
        system.bus.subscribe("road-closed", self.on_road_closed)
        system.bus.subscribe("road-reopened", self.on_road_reopened)
        system.bus.subscribe("vehicle-breakdown", self.on_vehicle_breakdown)

    # ------------------------------------------------------------ queries
    def trips_for(self, emergency_id, active_only=True):
        return [t for t in self.trips.values()
                if t.emergency_id == emergency_id and (t.is_active() or not active_only)]

    def active_trips(self):
        return sorted((t for t in self.trips.values() if t.is_active()), key=lambda t: int(t.id[1:]))

    # ------------------------------------------------------------ state rule
    def settle_state(self, e, now, explanation, force_delayed=False):
        """Pick the right state from the emergency's trips (single rule):

        Delayed     if any vehicle is blocked, cargo is stranded, a breakdown
                    just happened, or nothing is moving while food is unassigned
        In Progress if at least one vehicle is moving
        (otherwise nothing is outstanding and the state is left alone)
        """
        s = self.system
        trips = self.trips_for(e.id)
        moving = any(t.status == EN_ROUTE for t in trips)
        blocked = any(t.status == BLOCKED for t in trips)
        stranded = any(c.emergency_id == e.id and c.units() > 0 for c in s.stranded.values())
        if force_delayed or blocked or stranded or (not moving and e.unassigned_total() > 0):
            target = S.DELAYED
        elif moving:
            target = S.IN_PROGRESS
        else:
            return
        if e.state != target:
            s.state_machine.transition(e, target, now, explanation)
        else:
            s.state_machine.note(e, now, explanation)

    # ------------------------------------------------------------ assignment
    def dispatch_cycle(self, now):
        """Greedy: most urgent emergency first gets first pick of resources."""
        s = self.system
        still_waiting = []
        while True:
            eid = s.queue.pop()
            if eid is None:
                break
            e = s.emergencies[eid]
            if e.is_terminal() or e.unassigned_total() == 0:
                continue
            result = s.matcher.match(e, now)
            if result.trips:
                self._start_trips(e, result, now)
            if e.unassigned_total() == 0:
                continue  # fully covered, leaves the queue
            reason = "; ".join(result.reasons) or "waiting for resources"
            if result.permanently_unreachable and not self.trips_for(e.id):
                s.fail(e, now, f"Site can never be reached: {reason}")
                continue
            self._record_waiting(e, now, reason)
            still_waiting.append(e)
        for e in still_waiting:  # back into the queue for the next cycle
            s.enqueue(e, now)

    def _start_trips(self, e, result, now):
        s = self.system
        for trip in result.trips:
            self.trips[trip.id] = trip
            e.trip_ids.append(trip.id)
            route = RouteResult(True, trip.nodes[0], trip.nodes[-1], trip.nodes, trip.edges, 0, trip.km)
            s.bus.publish("route-updated", {
                "trip": trip.id, "vehicle": trip.vehicle_id, "emergency": e.id, "status": "assigned",
                "path": list(trip.nodes), "eta": trip.delivery_time,
                "message": f"{trip.vehicle_id} -> {e.id}: {route.describe(s.graph).split(' (')[0]}, "
                           f"ETA {fmt_time(trip.delivery_time)}",
            }, now)
        text = "Assigned " + "; ".join(t.summary() for t in result.trips)
        if e.unassigned_total() > 0:
            text += (f". Still unassigned: {_units_text(e.unassigned_normal, e.unassigned_cold)}"
                     f" ({'; '.join(result.reasons) or 'retry next cycle'})")
        s.state_machine.transition(e, S.RESOURCE_ASSIGNED, now, text)
        last_eta = max(t.delivery_time for t in self.trips_for(e.id) if t.status == EN_ROUTE)
        late = last_eta - e.deadline
        timing = f"on time ({-late:.0f} min spare)" if late <= 0 else f"LATE by {late:.0f} min"
        self.settle_state(e, now, f"{len(result.trips)} vehicle(s) departed; last ETA {fmt_time(last_eta)}, "
                                  f"deadline {fmt_time(e.deadline)}: {timing}")

    def _record_waiting(self, e, now, reason):
        """Explain why (part of) the request is still waiting, once per new reason."""
        if reason == e.last_wait_reason and e.state == S.DELAYED:
            return
        moving = any(t.status == EN_ROUTE for t in self.trips_for(e.id))
        text = f"{_units_text(e.unassigned_normal, e.unassigned_cold)} still unassigned: {reason}"
        if moving and e.state == S.IN_PROGRESS:
            if reason != e.last_wait_reason:
                self.system.state_machine.note(e, now, text)
        else:
            self.settle_state(e, now, text)
        e.last_wait_reason = reason

    # ------------------------------------------------------------ time
    def advance_trips(self, now):
        """Complete every trip whose delivery time has been reached."""
        due = [t for t in self.trips.values() if t.status == EN_ROUTE and t.delivery_time <= now]
        for trip in sorted(due, key=lambda t: (t.delivery_time, int(t.id[1:]))):
            self._complete(trip, now)

    def _complete(self, trip, now):
        s = self.system
        trip.status = DELIVERED
        vehicle = s.vehicles[trip.vehicle_id]
        vehicle.location = trip.dest_node
        vehicle.status = IDLE
        vehicle.trip_id = None
        e = s.emergencies[trip.emergency_id]
        e.delivered_normal += trip.normal
        e.delivered_cold += trip.cold
        outstanding = e.requested_total() - e.delivered_total()
        if e.is_terminal():
            return  # should not happen: failures only fire when nothing is moving
        if outstanding == 0:
            e.delivered_at = now
            late = now - e.deadline
            timing = f"on time ({-late:.0f} min before deadline)" if late <= 0 else f"LATE by {late:.0f} min"
            s.state_machine.transition(e, S.DELIVERED, now,
                                       f"All food delivered ({_units_text(e.need_normal, e.need_cold)}); "
                                       f"last drop by {vehicle.id}; {timing}")
        else:
            s.state_machine.note(e, now, f"{vehicle.id} delivered {_units_text(trip.normal, trip.cold)}; "
                                         f"{outstanding} units still outstanding")

    # ------------------------------------------------------------ disruptions
    def on_road_closed(self, event):
        """Re-route only the trips whose REMAINING route uses the closed road/node."""
        s, now = self.system, event.time
        affected = {}
        for trip in self.active_trips():
            if trip.status == EN_ROUTE and trip.uses_closed_road(s.graph, now):
                affected.setdefault(trip.emergency_id, []).append(trip)
        for eid in sorted(affected, key=id_sort_key):
            e = s.emergencies[eid]
            vehicles = ", ".join(str(t.vehicle_id) for t in affected[eid])
            s.state_machine.transition(e, S.REROUTING, now,
                                       f"{event.payload['message']} blocks the route of {vehicles}; recalculating")
            lines = [self._reroute(trip, e, now) for trip in affected[eid]]
            self.settle_state(e, now, " ".join(lines))

    def on_road_reopened(self, event):
        """Give every blocked vehicle another chance to reach its site."""
        s, now = self.system, event.time
        for trip in self.active_trips():
            if trip.status != BLOCKED:
                continue
            here = s.vehicles[trip.vehicle_id].location
            route = find_route(s.graph, here, trip.dest_node)
            if not route.found:
                continue
            trip.status = EN_ROUTE
            self._apply_new_plan(trip, now, None, route, s.vehicles[trip.vehicle_id], 0.0)
            e = s.emergencies[trip.emergency_id]
            self.settle_state(e, now, f"{event.payload['message']}: {trip.vehicle_id} can move again via "
                                      f"{route.describe(s.graph)}; ETA {fmt_time(trip.delivery_time)}")

    def on_vehicle_breakdown(self, event):
        """Recover the food of a broken vehicle (the system already marked it BROKEN)."""
        s, now = self.system, event.time
        vehicle = s.vehicles[event.payload["vehicle"]]
        trip = self.trips.get(vehicle.trip_id) if vehicle.trip_id else None
        if trip is None or not trip.is_active():
            return  # it was idle: simply unavailable until repaired
        e = s.emergencies[trip.emergency_id]
        here = trip.current_node(now)
        vehicle.location = here
        if trip.is_loaded(now):
            cargo = StrandedCargo(id=s.new_cargo_id(), emergency_id=e.id, node=here,
                                  normal=trip.normal, cold=trip.cold, from_vehicle=vehicle.id)
            s.stranded[cargo.id] = cargo
            trip.status = STRANDED
            vehicle.trip_id = None
            e.unassigned_normal += trip.normal
            e.unassigned_cold += trip.cold
            text = (f"{vehicle.id} broke down at {s.graph.node_label(here)} carrying "
                    f"{_units_text(trip.normal, trip.cold)}; cargo {cargo.id} will be transferred to another vehicle")
        else:
            self._cancel_trip(trip, now, here)
            text = (f"{vehicle.id} broke down before pickup; {_units_text(trip.normal, trip.cold)} returned to "
                    f"{trip.source_id}; reassigning")
        s.enqueue(e, now)
        self.settle_state(e, now, text, force_delayed=True)

    # ------------------------------------------------------------ helpers
    def _reroute(self, trip, e, now):
        """Find a new route for one trip from where the vehicle is now."""
        s = self.system
        g, vehicle = s.graph, s.vehicles[trip.vehicle_id]
        mcfg = s.config["matching"]
        index = trip.position_index(now)
        here = trip.nodes[index]
        old_eta = trip.delivery_time
        old_km = trip.remaining_km(g, now)
        if trip.is_loaded(now):
            to_dest = find_route(g, here, trip.dest_node)
            if not to_dest.found:
                self._park(trip, now, here)
                s.bus.publish("route-updated", {
                    "trip": trip.id, "vehicle": vehicle.id, "emergency": e.id, "status": "unavailable",
                    "message": f"ROUTE UNAVAILABLE for {vehicle.id}: {to_dest.reason}"}, now)
                return (f"{vehicle.id}: no alternative route to the site ({to_dest.reason}); holding at "
                        f"{g.node_label(here)} with cargo until a road reopens.")
            self._apply_new_plan(trip, now, None, to_dest, vehicle, old_km)
        else:
            if index >= trip.pickup_index:   # at the source, still loading
                to_source, start, load = None, trip.pickup_time, 0.0
                to_dest = find_route(g, trip.source_node, trip.dest_node)
            else:
                to_source = find_route(g, here, trip.source_node)
                to_dest = find_route(g, trip.source_node, trip.dest_node)
                start, load = now, mcfg["load_minutes"]
            if not to_dest.found or (to_source is not None and not to_source.found):
                why = to_dest.reason if not to_dest.found else to_source.reason
                self._cancel_trip(trip, now, here)
                s.enqueue(e, now)
                return (f"{vehicle.id} cannot complete its trip via {trip.source_id} ({why}); assignment cancelled, "
                        f"{_units_text(trip.normal, trip.cold)} returned to {trip.source_id}; will re-match.")
            trip.apply_plan(g, start, to_source, to_dest, load, mcfg["unload_minutes"])
            self._charge_fuel(vehicle, trip.km - old_km)
            trip.reroutes += 1
            self._publish_reroute(trip, e, now)
        return f"{vehicle.id} rerouted, ETA {fmt_time(old_eta)} -> {fmt_time(trip.delivery_time)}."

    def _apply_new_plan(self, trip, now, to_source, to_dest, vehicle, old_km):
        """Loaded vehicle: new plan straight to the site from where it is."""
        unload = self.system.config["matching"]["unload_minutes"]
        trip.apply_plan(self.system.graph, now, to_source, to_dest, 0.0, unload)
        self._charge_fuel(vehicle, trip.km - old_km)
        trip.reroutes += 1
        self._publish_reroute(trip, self.system.emergencies[trip.emergency_id], now)

    def _publish_reroute(self, trip, e, now):
        s = self.system
        route = RouteResult(True, trip.nodes[0], trip.nodes[-1], trip.nodes, trip.edges, 0, trip.km)
        s.bus.publish("route-updated", {
            "trip": trip.id, "vehicle": trip.vehicle_id, "emergency": e.id, "status": "rerouted",
            "path": list(trip.nodes), "eta": trip.delivery_time,
            "message": f"{trip.vehicle_id} rerouted: {route.describe(s.graph).split(' (')[0]}, "
                       f"new ETA {fmt_time(trip.delivery_time)}"}, now)

    def _charge_fuel(self, vehicle, extra_km):
        fcfg = self.system.config["fuel"]
        if fcfg["enabled"]:
            vehicle.fuel -= extra_km * fcfg["per_km"]

    def _park(self, trip, now, here):
        """Blocked vehicle: it stays put (with its cargo) until a road reopens."""
        vehicle = self.system.vehicles[trip.vehicle_id]
        self._charge_fuel(vehicle, -trip.remaining_km(self.system.graph, now))  # refund the unused route
        trip.status = BLOCKED
        trip.nodes, trip.edges, trip.times = [here], [], [now]
        trip.pickup_index = None
        self.system.vehicles[trip.vehicle_id].location = here

    def _cancel_trip(self, trip, now, here):
        """Undo an assignment that never picked its food up."""
        s = self.system
        vehicle = s.vehicles[trip.vehicle_id]
        self._charge_fuel(vehicle, -trip.remaining_km(s.graph, now))  # refund unused fuel
        trip.status = CANCELLED
        vehicle.trip_id = None
        vehicle.location = here
        if vehicle.status != BROKEN:
            vehicle.status = IDLE
        store = s.hubs.get(trip.source_id) if trip.source_kind == "hub" else s.stranded.get(trip.source_id)
        if store is not None:
            store.normal += trip.normal
            store.cold += trip.cold
        e = s.emergencies[trip.emergency_id]
        e.unassigned_normal += trip.normal
        e.unassigned_cold += trip.cold
