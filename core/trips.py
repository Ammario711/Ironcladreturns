
"""A Trip = one vehicle carrying food from one source to one emergency.

Timeline model (kept simple on purpose):
* The planned route is a list of nodes with the clock time the vehicle
  reaches each node (`times[i]` = arrival at `nodes[i]`).
* The vehicle first drives to the source (hub or stranded cargo), spends
  `load_minutes` loading, then drives to the site and spends
  `unload_minutes` unloading. Delivery counts at `delivery_time`.
* Where is the vehicle at time t? At the last node it has reached
  (times[i] <= t). We do not model positions in the middle of a road;
  re-routing starts from the last node reached (documented approximation).
"""

from dataclasses import dataclass, field

from core.utils import fmt_time

# Trip status values
EN_ROUTE = "en_route"      # moving normally
BLOCKED = "blocked"        # holding cargo, no open route to the site yet
DELIVERED = "delivered"
CANCELLED = "cancelled"    # released before pickup (stock went back to source)
STRANDED = "stranded"      # vehicle broke down with cargo; cargo moved to StrandedCargo

ACTIVE_STATUSES = (EN_ROUTE, BLOCKED)


@dataclass
class Trip:
    id: str
    emergency_id: object
    vehicle_id: object
    source_kind: str          # "hub" or "cargo" (stranded cargo)
    source_id: object
    source_node: object
    dest_node: object
    normal: int
    cold: int
    assigned_at: float
    status: str = EN_ROUTE
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    times: list = field(default_factory=list)
    pickup_index: int | None = None   # index in nodes of the source; None = already loaded
    pickup_time: float = 0.0          # when loading finishes
    delivery_time: float = 0.0        # when unloading finishes
    km: float = 0.0
    reroutes: int = 0

    def units(self):
        return self.normal + self.cold

    def is_active(self):
        return self.status in ACTIVE_STATUSES

    def is_loaded(self, now):
        """Has the vehicle finished loading the food?

        One rule for every plan: loading is done at pickup_time. (A plan made
        for an already-loaded vehicle sets pickup_time to its start time.)
        """
        return now >= self.pickup_time

    def position_index(self, now):
        """Index of the last node reached at time `now`."""
        index = 0
        for i, t in enumerate(self.times):
            if t <= now:
                index = i
            else:
                break
        return index

    def current_node(self, now):
        return self.nodes[self.position_index(now)] if self.nodes else None

    def remaining_km(self, graph, now):
        return sum(graph.edges[e].km for e in self.edges[self.position_index(now):])

    def uses_closed_road(self, graph, now):
        """True if the part of the route still ahead hits a closed road/node."""
        index = self.position_index(now)
        if any(e in graph.closed_edges for e in self.edges[index:]):
            return True
        return any(n in graph.closed_nodes for n in self.nodes[index + 1:])

    def summary(self):
        load = []
        if self.normal:
            load.append(f"{self.normal} normal")
        if self.cold:
            load.append(f"{self.cold} cold")
        source = f"{self.source_id}" if self.source_kind == "hub" else f"cargo {self.source_id}"
        return f"{self.vehicle_id} carries {' + '.join(load)} from {source} (ETA {fmt_time(self.delivery_time)})"

    def apply_plan(self, graph, start_time, to_source, to_dest, load_minutes, unload_minutes):
        """Fill in nodes/edges/times from route legs.

        to_source: RouteResult vehicle -> source, or None if already loaded.
        to_dest:   RouteResult source (or current node) -> site.
        """
        if to_source is not None:
            self.nodes = list(to_source.path) + list(to_dest.path[1:])
            self.edges = list(to_source.edges) + list(to_dest.edges)
            self.pickup_index = len(to_source.path) - 1
            self.km = to_source.km + to_dest.km
        else:
            self.nodes = list(to_dest.path)
            self.edges = list(to_dest.edges)
            self.pickup_index = None
            self.km = to_dest.km
        clock = start_time
        self.times = [clock]
        for i, edge_id in enumerate(self.edges):
            if i == self.pickup_index:
                clock += load_minutes  # loading happens before leaving the source
            clock += graph.edge_cost(edge_id)
            self.times.append(clock)
        if self.pickup_index is None:
            self.pickup_time = start_time
            arrival = self.times[-1]
        else:
            self.pickup_time = self.times[self.pickup_index] + load_minutes
            arrival = max(self.times[-1], self.pickup_time)  # covers source == site
        self.delivery_time = arrival + unload_minutes
