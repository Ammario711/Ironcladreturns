# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""Route planning with Dijkstra's algorithm (heapq).

Why Dijkstra and not A*: edge cost is travel *time* scaled by terrain
factors, so a straight-line heuristic in km is not guaranteed admissible
without knowing the fastest possible speed. Dijkstra is always correct and
the graphs here are small. Complexity: O((V + E) log V).

What this module gives the rest of the system:
* find_route(graph, s, t)        -> RouteResult (found or an explicit reason)
* shortest_path_tree(graph, s)   -> distances from s to EVERY node (one run)
* path_from_tree(tree, node)     -> RouteResult from `node` back to the root
* alternative_routes(graph, s, t)-> other good routes (single-edge detours)

Closed roads/intersections are skipped by Graph.open_neighbors(), so any
closure is automatically respected and a new call "recalculates".
"""

import heapq
from dataclasses import dataclass, field


@dataclass
class RouteResult:
    found: bool
    source: object
    target: object
    path: list = field(default_factory=list)   # node ids, in travel order
    edges: list = field(default_factory=list)  # edge ids, edges[i] joins path[i] -> path[i+1]
    cost: float = 0.0                          # minutes
    km: float = 0.0
    reason: str = ""                           # filled when found is False

    def describe(self, graph):
        """Readable one-liner, e.g. 'Front St -> Bay St -> King St (6.2 min, 1.9 km)'."""
        if not self.found:
            return f"ROUTE UNAVAILABLE from {self.source} to {self.target}: {self.reason}"
        if not self.edges:
            return f"Already at destination {graph.node_label(self.target)} (0 min)"
        streets = []
        for edge_id in self.edges:  # collapse consecutive segments of the same street
            street = graph.edges[edge_id].street or f"edge {edge_id}"
            if not streets or streets[-1] != street:
                streets.append(street)
        return f"{' -> '.join(streets)} ({self.cost:.1f} min, {self.km:.2f} km, {len(self.edges)} segments)"


@dataclass
class PathTree:
    """Result of one full Dijkstra run from `root`."""
    root: object
    dist: dict       # node -> minutes from root (only reachable nodes)
    km: dict         # node -> km along that same fastest path
    prev_node: dict  # node -> previous node on the fastest path from root
    prev_edge: dict  # node -> edge used to arrive at node


def _dijkstra(graph, source, target=None, blocked_edges=(), closed_ends=False):
    """Core Dijkstra loop shared by every public function.

    Heap entries are (distance, counter, node). The counter breaks ties in
    insertion order, so results are deterministic and we never have to
    compare node ids of different types (int vs str).
    If `target` is given we stop as soon as it is settled (early exit).

    Closed intersections are never passed THROUGH. The search may always
    leave `source` even if it is closed. With closed_ends=True (the reverse
    search in shortest_path_tree) a closed node may also be reached as the
    final step, because in the real forward trip that node is where the
    vehicle STARTS, and a vehicle may drive away from a closed intersection.
    This keeps trees and find_route() giving the same answers.
    """
    dist = {source: 0.0}
    km = {source: 0.0}
    prev_node, prev_edge = {}, {}
    settled = set()
    heap = [(0.0, 0, source)]
    counter = 1
    while heap:
        d, _, node = heapq.heappop(heap)
        if node in settled:
            continue  # stale entry: we already found a shorter way here
        settled.add(node)
        if node == target:
            break
        if node != source and node in graph.closed_nodes:
            continue  # reached a closed node as an end point: never drive on through it
        for neighbor, edge_id in graph.open_neighbors(node, blocked_edges, include_closed_nodes=closed_ends):
            if neighbor in settled:
                continue
            new_d = d + graph.edge_cost(edge_id)
            if neighbor not in dist or new_d < dist[neighbor]:  # strict < keeps the first equal path found
                dist[neighbor] = new_d
                km[neighbor] = km[node] + graph.edges[edge_id].km
                prev_node[neighbor] = node
                prev_edge[neighbor] = edge_id
                heapq.heappush(heap, (new_d, counter, neighbor))
                counter += 1
    # Only settled nodes have final distances.
    dist = {n: d for n, d in dist.items() if n in settled}
    km = {n: k for n, k in km.items() if n in settled}
    return PathTree(source, dist, km, prev_node, prev_edge)


def _unavailable(graph, source, target):
    """Return a RouteResult with a reason if a route is impossible up front."""
    if source not in graph.nodes:
        return RouteResult(False, source, target, reason=f"start node {source!r} does not exist")
    if target not in graph.nodes:
        return RouteResult(False, source, target, reason=f"destination node {target!r} does not exist")
    if target in graph.closed_nodes and target != source:
        return RouteResult(False, source, target, reason=f"destination {graph.node_label(target)} is closed")
    return None


def find_route(graph, source, target, blocked_edges=()):
    """Fastest open route from source to target, or found=False with a reason.

    The start node may itself be closed (a vehicle standing there can still
    drive away); the destination may not.
    """
    problem = _unavailable(graph, source, target)
    if problem:
        return problem
    tree = _dijkstra(graph, source, target, blocked_edges)
    if target not in tree.dist:
        if graph.connected(source, target, ignore_closures=True):
            why = "every connecting road is currently closed"
        else:
            why = "the road network has no connection at all (disconnected graph)"
        return RouteResult(False, source, target, reason=why)
    # Walk the prev pointers backwards from target to source, then reverse.
    path, edges = [target], []
    node = target
    while node != source:
        edges.append(tree.prev_edge[node])
        node = tree.prev_node[node]
        path.append(node)
    path.reverse()
    edges.reverse()
    return RouteResult(True, source, target, path, edges, tree.dist[target], tree.km[target])


def shortest_path_tree(graph, source):
    """Distances from `source` to every reachable node, in ONE Dijkstra run.

    Roads are two-way, so this also gives the distance FROM every node TO
    `source`. The matcher uses that: one run from the emergency site tells
    it how far every hub is. Same closure rules as find_route(x, source):
    a closed `source` cannot be driven INTO, so only itself is reachable.
    """
    if source not in graph.nodes:
        return PathTree(source, {}, {}, {}, {})
    if source in graph.closed_nodes:
        return PathTree(source, {source: 0.0}, {source: 0.0}, {}, {})
    return _dijkstra(graph, source, closed_ends=True)


def path_from_tree(tree, start):
    """Route from `start` to the tree's root, following prev pointers.

    Because prev pointers point back toward the root, walking them from
    `start` already gives the nodes in travel order start -> root.
    """
    if start not in tree.dist:
        return RouteResult(False, start, tree.root, reason="not reachable with current closures")
    path, edges = [start], []
    node = start
    while node != tree.root:
        edges.append(tree.prev_edge[node])
        node = tree.prev_node[node]
        path.append(node)
    return RouteResult(True, start, tree.root, path, edges, tree.dist[start], tree.km[start])


def alternative_routes(graph, source, target, k=3):
    """Up to k distinct routes, best first.

    Method (simple single-edge detour, a cut-down version of Yen's
    algorithm): take the best route, then for each of its edges find the
    best route that avoids just that edge. Keep distinct paths, sort by cost.
    Cost: one Dijkstra per edge on the best path.
    """
    best = find_route(graph, source, target)
    if not best.found:
        return [best]
    routes = [best]
    # Compare by EDGES, not nodes: two parallel roads between the same
    # intersections give the same node list but are different routes.
    seen = {tuple(best.edges)}
    for edge_id in best.edges:
        detour = find_route(graph, source, target, blocked_edges={edge_id})
        if detour.found and tuple(detour.edges) not in seen:
            seen.add(tuple(detour.edges))
            routes.append(detour)
    routes.sort(key=lambda r: (round(r.cost, 6), len(r.edges)))
    return routes[:k]
