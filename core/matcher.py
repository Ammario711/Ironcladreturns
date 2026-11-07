"""Greedy resource matching: which hub + which vehicle serve an emergency.

Greedy on two levels:
1. Emergencies are matched one at a time in PRIORITY ORDER (the dispatcher
   pops them from the heap), so the most urgent request gets first pick.
2. For one emergency we repeatedly take the single best (source, vehicle)
   pair, load as much as fits, and repeat until the need is covered or
   nothing feasible is left. "Best" = earliest delivery time (ETA).

Constraints checked for every candidate pair:
* source has stock of the needed type (stock is reserved immediately, so
  no unit is promised twice)
* vehicle is idle (status changes to busy immediately, so no vehicle is
  assigned twice)
* cold food only in refrigerated vehicles
* vehicle capacity (one trip carries at most `capacity` units)
* route availability: vehicle -> source and source -> site must be open
* fuel (optional): enough fuel for the whole trip at config fuel.per_km

Order of work:
* Cold food first: it is the scarcer, more constrained resource. Among
  on-time options, the reefer trip carrying the MOST cold food wins, so a
  scarce reefer is not spent on a tiny remnant. A refrigerated truck fills
  any spare space with normal food from the same source (cold is loaded
  first, so this never takes the place of cold food).
* Normal food next: prefer non-refrigerated trucks so reefers stay free
  for cold requests (config matching.prefer_non_refrigerated_for_normal).
* Stranded cargo (food in a broken-down truck) for this emergency is used
  before hub stock, so it is not wasted.

Partial fulfilment: whatever cannot be covered stays "unassigned" and is
retried next cycle; `reasons` says why in plain words.
Cost per emergency: 1 Dijkstra from the site + 1 per reachable source that
has stock of the needed type (each tree is computed once and reused), plus
O(sources x vehicles) per trip created.
"""

from dataclasses import dataclass, field

from core.resources import BUSY, BROKEN, IDLE
from core.routing import path_from_tree, shortest_path_tree
from core.trips import Trip
from core.utils import id_sort_key


@dataclass
class SupplySource:
    kind: str          # "hub" or "cargo"
    id: object
    node: object
    store: object      # the Hub or StrandedCargo whose .normal/.cold we take from
    dedicated: bool    # True = stranded cargo that belongs to this emergency


@dataclass
class MatchResult:
    emergency_id: object
    trips: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    permanently_unreachable: bool = False

    def add_reason(self, text):
        if text not in self.reasons:
            self.reasons.append(text)


@dataclass
class _Option:
    key: tuple
    source: SupplySource
    vehicle: object
    normal: int
    cold: int
    fuel_needed: float


class Matcher:
    def __init__(self, graph, hubs, vehicles, stranded, config, bus, new_trip_id):
        self.graph = graph
        self.hubs = hubs
        self.vehicles = vehicles
        self.stranded = stranded          # id -> StrandedCargo (shared with the system)
        self.cfg = config
        self.bus = bus
        self.new_trip_id = new_trip_id    # callable returning "T1", "T2", ...

    # ------------------------------------------------------------------ main
    def match(self, emergency, now):
        """Reserve sources + vehicles for as much of the emergency as possible.

        After this returns, every trip in result.trips already owns its
        vehicle and its stock. The emergency's unassigned counters are
        reduced accordingly.
        """
        result = MatchResult(emergency.id)
        if emergency.unassigned_total() == 0:
            return result
        mcfg = self.cfg["matching"]

        if emergency.node in self.graph.closed_nodes:
            result.add_reason(f"Route unavailable: destination {self.graph.node_label(emergency.node)} is closed")
            return result
        # One Dijkstra from the site gives the time from EVERY node to the site
        # (roads are two-way, so distance site->x equals x->site).
        to_site = shortest_path_tree(self.graph, emergency.node)
        sources = self._sources_for(emergency)
        reachable = [s for s in sources if s.node in to_site.dist]
        if not reachable:
            self._explain_unreachable(emergency, sources, result)
            return result

        trees = {}  # source id -> path tree from that source (to reach vehicles)
        limit = mcfg["max_vehicles_per_emergency_per_cycle"]
        for cold_phase in (True, False):
            while len(result.trips) < limit:
                if cold_phase and emergency.unassigned_cold == 0:
                    break
                if not cold_phase and emergency.unassigned_normal == 0:
                    break
                option = self._best_option(emergency, reachable, to_site, trees, cold_phase, now, result)
                if option is None:
                    self._explain_no_option(reachable, trees, cold_phase, result)
                    break
                result.trips.append(self._reserve(emergency, option, to_site, trees, now))
        if emergency.unassigned_total() > 0 and len(result.trips) >= limit:
            result.add_reason(f"limit of {limit} vehicles per request per cycle reached; rest is retried next cycle")
        return result

    # ------------------------------------------------------------ candidates
    def _sources_for(self, emergency):
        """Stranded cargo of THIS emergency first, then every hub (sorted)."""
        sources = []
        for cargo in sorted(self.stranded.values(), key=lambda c: c.id):
            if cargo.emergency_id == emergency.id and cargo.units() > 0:
                sources.append(SupplySource("cargo", cargo.id, cargo.node, cargo, True))
        for hub in sorted(self.hubs.values(), key=lambda h: id_sort_key(h.id)):
            sources.append(SupplySource("hub", hub.id, hub.node, hub, False))
        return sources

    def _best_option(self, emergency, reachable, to_site, trees, cold_phase, now, result):
        """Try every (source, vehicle) pair; return the one with the best key."""
        mcfg, fcfg = self.cfg["matching"], self.cfg["fuel"]
        best = None
        for source in reachable:
            if cold_phase and source.store.cold <= 0:
                continue
            if not cold_phase and source.store.normal <= 0:
                continue
            if source.id not in trees:
                trees[source.id] = shortest_path_tree(self.graph, source.node)
            tree = trees[source.id]
            for vehicle in sorted(self.vehicles.values(), key=lambda v: id_sort_key(v.id)):
                if vehicle.status != IDLE:
                    continue
                if cold_phase and not vehicle.refrigerated:
                    continue
                if vehicle.location not in tree.dist:
                    continue  # this vehicle cannot reach this source right now
                # How much can this vehicle carry from this source?
                if cold_phase:
                    cold = min(emergency.unassigned_cold, source.store.cold, vehicle.capacity)
                    normal = min(emergency.unassigned_normal, source.store.normal, vehicle.capacity - cold)
                else:
                    cold = 0
                    normal = min(emergency.unassigned_normal, source.store.normal, vehicle.capacity)
                if cold + normal <= 0:
                    continue
                trip_km = tree.km[vehicle.location] + to_site.km[source.node]
                fuel_needed = trip_km * fcfg["per_km"] if fcfg["enabled"] else 0.0
                if fuel_needed > vehicle.fuel:
                    result.add_reason(f"{vehicle.id} skipped: needs {fuel_needed:.1f} fuel for {trip_km:.1f} km, has {vehicle.fuel:.1f}")
                    continue
                eta = (now + tree.dist[vehicle.location] + mcfg["load_minutes"]
                       + to_site.dist[source.node] + mcfg["unload_minutes"])
                # Lower key = better. Read left to right.
                if cold_phase:
                    # Reefers are scarce, so each reefer trip should carry as much
                    # cold food as possible: stranded cargo first, then on-time
                    # options, then MOST cold units, then earliest ETA. A nearby hub
                    # with only 5 cold units loses to one with 40 that is still on time.
                    late = 1 if eta > emergency.deadline else 0
                    key = (0 if source.dedicated else 1, late, -cold, round(eta, 4),
                           -(cold + normal), id_sort_key(source.id), id_sort_key(vehicle.id))
                else:
                    # Normal food: stranded cargo first, non-reefers first,
                    # earliest ETA, bigger load, then ids for determinism.
                    spare_reefer = vehicle.refrigerated and mcfg["prefer_non_refrigerated_for_normal"]
                    key = (0 if source.dedicated else 1, 1 if spare_reefer else 0, round(eta, 4),
                           -(cold + normal), id_sort_key(source.id), id_sort_key(vehicle.id))
                if best is None or key < best.key:
                    best = _Option(key, source, vehicle, normal, cold, fuel_needed)
        return best

    # ------------------------------------------------------------ reserve
    def _reserve(self, emergency, option, to_site, trees, now):
        """Take the stock, mark the vehicle busy, build the trip."""
        mcfg = self.cfg["matching"]
        source, vehicle = option.source, option.vehicle
        to_source = path_from_tree(trees[source.id], vehicle.location)  # vehicle -> source
        to_dest = path_from_tree(to_site, source.node)                   # source -> site
        before_normal, before_cold = source.store.normal, source.store.cold
        source.store.normal -= option.normal
        source.store.cold -= option.cold
        emergency.unassigned_normal -= option.normal
        emergency.unassigned_cold -= option.cold
        vehicle.status = BUSY
        vehicle.fuel -= option.fuel_needed
        trip = Trip(id=self.new_trip_id(), emergency_id=emergency.id, vehicle_id=vehicle.id,
                    source_kind=source.kind, source_id=source.id, source_node=source.node,
                    dest_node=emergency.node, normal=option.normal, cold=option.cold, assigned_at=now)
        trip.apply_plan(self.graph, now, to_source, to_dest, mcfg["load_minutes"], mcfg["unload_minutes"])
        vehicle.trip_id = trip.id
        if source.kind == "hub":  # tell everyone when a hub runs dry
            for food, before, after in (("normal", before_normal, source.store.normal),
                                        ("cold", before_cold, source.store.cold)):
                if before > 0 and after == 0:
                    remaining = sum(getattr(h, food) for h in self.hubs.values())
                    self.bus.publish("resource-depleted", {
                        "hub": source.id, "food": food, "system_remaining": remaining,
                        "message": f"Hub {source.id} has no {food} stock left ({remaining} {food} left in all hubs)",
                    }, now)
        return trip

    # ------------------------------------------------------------ reasons
    def _explain_unreachable(self, emergency, sources, result):
        """No source can reach the site. Permanent (disconnected) or temporary (closures)?"""
        if not sources:
            result.add_reason("No supply hubs exist in this dataset")
            result.permanently_unreachable = True
            return
        if any(self.graph.connected(s.node, emergency.node, ignore_closures=True) for s in sources):
            result.add_reason("Route unavailable: road closures cut the site off from every supply hub")
        else:
            result.permanently_unreachable = True
            result.add_reason("Route unavailable: the site is not connected to any supply hub even with all roads open")

    def _explain_no_option(self, reachable, trees, cold_phase, result):
        """Say in plain words why no (source, vehicle) pair worked."""
        food = "cold" if cold_phase else "normal"
        with_stock = [s for s in reachable if (s.store.cold if cold_phase else s.store.normal) > 0]
        if not with_stock:
            total = sum(getattr(h, food) for h in self.hubs.values())
            if total > 0:
                result.add_reason(f"{food} stock exists ({total} units) but no hub holding it can reach the site")
            else:
                result.add_reason(f"No {food} stock left in any hub")
            return
        fleet = [v for v in self.vehicles.values() if v.refrigerated or not cold_phase]
        kind = "refrigerated vehicle" if cold_phase else "vehicle"
        idle = [v for v in fleet if v.status == IDLE]
        if not fleet:
            result.add_reason(f"Dataset has no {kind}s for {food} food")
        elif not idle:
            busy = sum(1 for v in fleet if v.status == BUSY)
            broken = sum(1 for v in fleet if v.status == BROKEN)
            result.add_reason(f"No {kind} available ({busy} busy, {broken} broken)")
        else:
            # One Dijkstra per stocked source (reusing the ones match() already ran).
            for s in with_stock:
                if s.id not in trees:
                    trees[s.id] = shortest_path_tree(self.graph, s.node)
            if not any(v.location in trees[s.id].dist for s in with_stock for v in idle):
                # Is it closures, or is the fleet on a separate piece of the network?
                # (Not marked permanent: a restock or repair can still fix it.)
                linked = any(self.graph.connected(v.location, s.node, ignore_closures=True)
                             for s in with_stock for v in idle)
                if linked:
                    result.add_reason(f"Idle {kind}s cannot reach any hub with {food} stock (road closures)")
                else:
                    result.add_reason(f"Idle {kind}s are on a part of the road network with no road at all "
                                      f"to any hub with {food} stock")
        # fuel problems were already recorded per vehicle in _best_option
