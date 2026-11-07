
"""Weighted, undirected road graph with closable roads and intersections.

Design notes (see DECISIONS.md for the full reasoning):
* Adjacency list: node id -> list of edge ids. Memory O(V + E); listing a
  node's neighbours is O(degree), which is exactly what Dijkstra needs.
* Closures are kept in two sets (closed_edges, closed_nodes) instead of
  deleting edges. Closing/reopening is O(1) and no data is ever lost.
* `version` goes up on every closure change. Other modules use it to know
  when a cached route/distance is out of date.
* Nothing here knows about Toronto: ids can be ints or strings, terrain
  and highway speed factors come from config.
"""

from collections import deque
from dataclasses import dataclass

from core.utils import haversine_km


@dataclass
class Node:
    """A road intersection."""
    id: object
    name: str
    lat: float | None = None
    lng: float | None = None


@dataclass
class Edge:
    """A road segment between two nodes (travel is allowed both ways)."""
    id: object
    a: object
    b: object
    km: float          # physical length (used for fuel)
    w: float           # base travel cost from the dataset, treated as minutes
    street: str = ""
    hwy: bool = False
    terrain: str = ""

    def other_end(self, node_id):
        """Given one endpoint, return the other one."""
        return self.b if node_id == self.a else self.a


class Graph:
    def __init__(self, terrain_multipliers=None, highway_multiplier=1.0):
        self.nodes = {}            # node id -> Node
        self.edges = {}            # edge id -> Edge
        self.adjacency = {}        # node id -> [edge id, ...]
        self.closed_edges = set()  # edge ids currently closed
        self.closed_nodes = set()  # node ids currently closed
        # Speed factors: cost = w * terrain factor * (highway factor if hwy).
        # Defaults are 1.0 so the dataset's w is used unchanged.
        self.terrain_multipliers = dict(terrain_multipliers or {})
        self.highway_multiplier = float(highway_multiplier)
        self.version = 0           # bumped on every open/close change

    # ------------------------------------------------------------ building
    def add_node(self, node):
        if node.id in self.nodes:
            raise ValueError(f"duplicate node id {node.id!r}")
        self.nodes[node.id] = node
        self.adjacency[node.id] = []

    def add_edge(self, edge):
        if edge.id in self.edges:
            raise ValueError(f"duplicate edge id {edge.id!r}")
        for end in (edge.a, edge.b):
            if end not in self.nodes:
                raise ValueError(f"edge {edge.id!r} uses unknown node {end!r}")
        if edge.w < 0:
            raise ValueError(f"edge {edge.id!r} has negative cost {edge.w} (Dijkstra needs >= 0)")
        self.edges[edge.id] = edge
        self.adjacency[edge.a].append(edge.id)
        if edge.b != edge.a:  # a self-loop is listed once
            self.adjacency[edge.b].append(edge.id)

    # ------------------------------------------------------------ costs
    def edge_cost(self, edge_id):
        """Travel time (minutes) for one edge after terrain/highway factors."""
        edge = self.edges[edge_id]
        factor = self.terrain_multipliers.get(edge.terrain, 1.0)
        if edge.hwy:
            factor *= self.highway_multiplier
        return edge.w * factor

    def open_neighbors(self, node_id, blocked_edges=(), include_closed_nodes=False):
        """Yield (neighbour id, edge id) for every usable road out of node_id.

        Skips closed edges, any extra `blocked_edges` (used when searching
        for alternative routes) and, unless include_closed_nodes is True,
        closed neighbour nodes.
        """
        for edge_id in self.adjacency.get(node_id, []):
            if edge_id in self.closed_edges or edge_id in blocked_edges:
                continue
            neighbor = self.edges[edge_id].other_end(node_id)
            if neighbor in self.closed_nodes and not include_closed_nodes:
                continue
            yield neighbor, edge_id

    # ------------------------------------------------------------ disruptions
    def close_edge(self, edge_id):
        """Close a road. Returns True if something changed."""
        if edge_id not in self.edges:
            raise KeyError(f"unknown edge {edge_id!r}")
        if edge_id in self.closed_edges:
            return False
        self.closed_edges.add(edge_id)
        self.version += 1
        return True

    def reopen_edge(self, edge_id):
        if edge_id not in self.edges:
            raise KeyError(f"unknown edge {edge_id!r}")
        if edge_id not in self.closed_edges:
            return False
        self.closed_edges.discard(edge_id)
        self.version += 1
        return True

    def close_node(self, node_id):
        """Close an intersection (no route may pass through it)."""
        if node_id not in self.nodes:
            raise KeyError(f"unknown node {node_id!r}")
        if node_id in self.closed_nodes:
            return False
        self.closed_nodes.add(node_id)
        self.version += 1
        return True

    def reopen_node(self, node_id):
        if node_id not in self.nodes:
            raise KeyError(f"unknown node {node_id!r}")
        if node_id not in self.closed_nodes:
            return False
        self.closed_nodes.discard(node_id)
        self.version += 1
        return True

    # ------------------------------------------------------------ queries
    def edges_between(self, a, b):
        """All edge ids that directly connect a and b (either direction)."""
        return [eid for eid in self.adjacency.get(a, []) if self.edges[eid].other_end(a) == b]

    def connected(self, a, b, ignore_closures=True):
        """Breadth-first search: can you get from a to b at all?

        With ignore_closures=True this answers "is the road network itself
        disconnected?" (a permanent problem) as opposed to "are roads
        temporarily closed?". O(V + E).
        """
        if a not in self.nodes or b not in self.nodes:
            return False
        seen = {a}
        frontier = deque([a])
        while frontier:
            node = frontier.popleft()
            if node == b:
                return True
            if ignore_closures:
                steps = ((self.edges[e].other_end(node), e) for e in self.adjacency[node])
            else:
                steps = self.open_neighbors(node)
            for neighbor, _edge in steps:
                if neighbor not in seen:
                    seen.add(neighbor)
                    frontier.append(neighbor)
        return False

    def nearest_node(self, lat, lng):
        """Closest node to a lat/lng point (only nodes that have coordinates)."""
        best_id, best_km = None, None
        for node in self.nodes.values():
            if node.lat is None or node.lng is None:
                continue
            km = haversine_km(lat, lng, node.lat, node.lng)
            if best_km is None or km < best_km:
                best_id, best_km = node.id, km
        return best_id

    def node_label(self, node_id):
        node = self.nodes.get(node_id)
        return f"{node_id} ({node.name})" if node else str(node_id)

    def edge_label(self, edge_id):
        edge = self.edges.get(edge_id)
        if edge is None:
            return str(edge_id)
        street = edge.street or "road"
        return f"edge {edge_id} [{street}: {self.nodes[edge.a].name} - {self.nodes[edge.b].name}]"
