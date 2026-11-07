# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""Loader robustness (unknown keys, missing fields, empty data) and routing."""

import json
import os
import tempfile
import unittest

from core.graph import Edge, Graph, Node
from core.routing import alternative_routes, find_route, shortest_path_tree
from helpers import SPARSE, TORONTO, config, make_system
from loader.dataset_loader import DatasetError, load_dataset, parse_dataset


class LoaderTests(unittest.TestCase):
    def test_toronto_loads_completely(self):
        ds = load_dataset(TORONTO, config())
        self.assertEqual(len(ds.graph.nodes), 48)
        self.assertEqual(len(ds.graph.edges), 82)
        self.assertEqual(len(ds.hubs), 3)
        self.assertEqual(len(ds.vehicles), 12)
        self.assertEqual(ds.warnings, [])
        self.assertEqual(ds.hubs["P0"].node, 33)

    def test_sparse_fixture_tolerates_unknown_keys_and_missing_fields(self):
        ds = load_dataset(SPARSE, config())
        text = " | ".join(ds.warnings)
        self.assertIn("unknown top-level keys: weather", text)
        self.assertIn("nodes[8] skipped", text)               # node with no id
        self.assertIn("edges[7] skipped", text)               # unknown endpoint
        self.assertIn("edges[8] skipped", text)               # negative cost
        self.assertIn("no capacity given", text)              # K1 default capacity
        self.assertEqual(ds.vehicles["K1"].capacity, 50)
        self.assertEqual(ds.vehicles["K2"].location, "T1")    # home given as a POI id
        self.assertFalse(ds.vehicles["K2"].refrigerated)      # "no" parsed as False
        self.assertIn("auto-9", ds.graph.edges)               # edge without id kept
        self.assertAlmostEqual(ds.graph.edges["auto-9"].w, 4.0)  # w derived from km at 30 km/h

    def test_terrain_section_sets_speed_factors(self):
        g = load_dataset(SPARSE, config()).graph
        self.assertAlmostEqual(g.edge_cost("e4"), 4.0 * 1.5)   # gravel
        self.assertAlmostEqual(g.edge_cost("e3"), 3.0 * 1.2)   # rural highway

    def test_empty_dataset_still_builds_a_system(self):
        system = make_system(raw={})
        self.assertIn("no usable nodes", " ".join(system.dataset.warnings))
        result = system.submit_emergency({"location": 1, "crisis_type": "flood", "location_category": "shelter",
                                          "people_affected": 5, "food_normal": 5, "severity": 2,
                                          "deadline": 100, "submitted_at": 0})
        self.assertFalse(result.ok)
        self.assertIn("not a known place id", result.errors[0])
        system.tick(1)  # must not crash

    def test_fatal_file_problems_raise_clear_errors(self):
        with self.assertRaises(DatasetError) as ctx:
            load_dataset("does/not/exist.json", config())
        self.assertIn("not found", str(ctx.exception))
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w") as handle:
                handle.write("{ not json")
            with self.assertRaises(DatasetError) as ctx:
                load_dataset(bad, config())
            self.assertIn("not valid JSON", str(ctx.exception))
            listy = os.path.join(tmp, "list.json")
            with open(listy, "w") as handle:
                json.dump([1, 2], handle)
            with self.assertRaises(DatasetError):
                load_dataset(listy, config())

    def test_string_and_int_ids_match(self):
        raw = {"nodes": [{"id": "1"}, {"id": 2}], "edges": [{"id": 0, "a": 1, "b": "2", "w": 1, "km": 1}],
               "pois": [{"id": "H", "kind": "hub", "node": "1"}], "hubs": [{"poiId": "H", "normal": 5}],
               "trucks": [{"id": "V", "home": "2", "capacity": 5}]}
        ds = parse_dataset(raw, config())
        self.assertEqual(ds.hubs["H"].node, 1)
        self.assertEqual(ds.vehicles["V"].home, 2)
        self.assertTrue(find_route(ds.graph, 1, 2).found)


def square_graph():
    """A -1- B
       |     |
       1     5      plus a slow diagonal A-C (cost 10)
       |     |
       D -1- C
    """
    g = Graph()
    for n in "ABCD":
        g.add_node(Node(n, n))
    for eid, a, b, w in (("ab", "A", "B", 1), ("bc", "B", "C", 5), ("ad", "A", "D", 1),
                         ("dc", "D", "C", 1), ("ac", "A", "C", 10)):
        g.add_edge(Edge(eid, a, b, km=w, w=w))
    return g


class RoutingTests(unittest.TestCase):
    def test_shortest_path(self):
        r = find_route(square_graph(), "A", "C")
        self.assertTrue(r.found)
        self.assertEqual(r.path, ["A", "D", "C"])
        self.assertEqual(r.cost, 2)

    def test_closed_edge_uses_alternative(self):
        g = square_graph()
        g.close_edge("dc")
        r = find_route(g, "A", "C")
        self.assertEqual(r.path, ["A", "B", "C"])
        self.assertEqual(r.cost, 6)

    def test_closed_node_is_avoided(self):
        g = square_graph()
        g.close_node("D")
        self.assertEqual(find_route(g, "A", "C").path, ["A", "B", "C"])

    def test_route_unavailable_has_reason(self):
        g = square_graph()
        for e in ("bc", "dc", "ac"):
            g.close_edge(e)
        r = find_route(g, "A", "C")
        self.assertFalse(r.found)
        self.assertIn("closed", r.reason)
        self.assertIn("ROUTE UNAVAILABLE", r.describe(g))
        g.reopen_edge("ac")
        self.assertEqual(find_route(g, "A", "C").cost, 10)  # recalculated after reopening

    def test_disconnected_graph(self):
        g = square_graph()
        g.add_node(Node("Z", "island"))
        r = find_route(g, "A", "Z")
        self.assertFalse(r.found)
        self.assertIn("disconnected", r.reason)

    def test_same_node_and_unknown_nodes(self):
        g = square_graph()
        self.assertEqual(find_route(g, "A", "A").cost, 0)
        self.assertIn("does not exist", find_route(g, "A", "Q").reason)

    def test_alternative_routes_sorted_and_distinct(self):
        routes = alternative_routes(square_graph(), "A", "C", k=3)
        # Single-edge detours of A-D-C find A-B-C, but not the A-C diagonal
        # (that needs two edges blocked at once). Documented limitation.
        self.assertEqual([r.cost for r in routes], [2, 6])
        self.assertEqual(len({tuple(r.path) for r in routes}), 2)

    def test_closed_destination_reason(self):
        g = square_graph()
        g.close_node("C")
        self.assertIn("is closed", find_route(g, "A", "C").reason)

    def test_tree_matches_single_routes(self):
        g = square_graph()
        tree = shortest_path_tree(g, "C")
        for n in "ABD":
            self.assertAlmostEqual(tree.dist[n], find_route(g, n, "C").cost)

    def test_toronto_known_route(self):
        g = load_dataset(TORONTO, config()).graph
        r = find_route(g, 33, 22)
        self.assertTrue(r.found)
        self.assertAlmostEqual(r.cost, 8.324, places=3)


if __name__ == "__main__":
    unittest.main()
