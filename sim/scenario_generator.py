"""Seeded random scenario generator, works on ANY dataset.

Why: hand-written input files name one city's places and roads. A new
dataset has different ids, so we generate requests and disruptions from
whatever that dataset contains. Same dataset + same seed gives exactly the
same scenario (random.Random(seed), sorted inputs).
"""

import random

from core.utils import fmt_time, id_sort_key, parse_time

SITE_CATEGORIES = ("hospital", "shelter", "community", "school", "senior_home")


def generate_scenario(dataset, start_minutes, n_emergencies=8, n_disruptions=4, seed=42, spread_minutes=90):
    """Return (emergencies, disruptions) as raw dicts, ready for the simulator.
    start_minutes: clock time of the first request (main.py passes the
    configured simulation.start_time, so nothing here is hard-coded)."""
    rng = random.Random(seed)
    graph = dataset.graph
    hub_nodes = {h.node for h in dataset.hubs.values()}
    # Prefer named delivery sites (POIs that are not hubs); else any non-hub node.
    # A hub is not a delivery site, whatever its "kind" says in the dataset.
    sites = [p for p in sorted(dataset.pois.values(), key=lambda p: id_sort_key(p.id))
             if p.kind != "hub" and p.id not in dataset.hubs]
    nodes = [n for n in sorted(graph.nodes, key=id_sort_key) if n not in hub_nodes] or sorted(graph.nodes, key=id_sort_key)
    if not nodes:
        return [], []

    # Use the dataset's STARTING stock (not live stock) so the same seed always
    # gives the same scenario, even after a simulation has used some food.
    total_normal = sum(h.initial_normal for h in dataset.hubs.values())
    total_cold = sum(h.initial_cold for h in dataset.hubs.values())
    # Each request asks for up to ~1/3 of an even share of the stock, so a
    # scenario is busy but not hopeless; at least 10 units.
    share_normal = max(10, total_normal // max(3, n_emergencies) // 1)
    share_cold = max(5, total_cold // max(3, n_emergencies))

    emergencies = []
    for i in range(n_emergencies):
        if sites and rng.random() < 0.6:
            poi = rng.choice(sites)
            location, category = poi.id, poi.kind if poi.kind in SITE_CATEGORIES else rng.choice(SITE_CATEGORIES)
        else:
            location, category = rng.choice(nodes), rng.choice(SITE_CATEGORIES)
        cold = rng.randint(5, share_cold) if total_cold > 0 and rng.random() < 0.4 else 0
        normal = rng.randint(10, share_normal)
        submitted = start_minutes + rng.randint(0, spread_minutes)
        emergencies.append({
            "id": f"G{i + 1}",
            "location": location,
            "crisis_type": rng.choice(["supply_shortage", "power_outage", "flood", "storm", "displacement"]),
            "location_category": category,
            "people_affected": rng.randint(20, 400),
            "food_normal": normal,
            "food_cold": cold,
            "refrigeration_required": cold > 0,
            "severity": rng.randint(1, 5),
            "submitted_at": fmt_time(submitted),
            "deadline": fmt_time(submitted + rng.randint(45, 180)),
        })

    disruptions = []
    edges = sorted(graph.edges, key=id_sort_key)
    vehicles = sorted(dataset.vehicles, key=id_sort_key)
    for i in range(n_disruptions):
        t = start_minutes + rng.randint(5, spread_minutes)
        if edges and (i % 3 != 2 or not vehicles):
            edge = rng.choice(edges)
            disruptions.append({"time": fmt_time(t), "type": "road_closure", "edge": edge, "reason": "generated closure"})
            disruptions.append({"time": fmt_time(t + rng.randint(20, 60)), "type": "road_reopen", "edge": edge,
                                "reason": "generated reopening"})
        elif vehicles:
            vehicle = rng.choice(vehicles)
            disruptions.append({"time": fmt_time(t), "type": "vehicle_breakdown", "vehicle": vehicle,
                                "reason": "generated breakdown"})
            disruptions.append({"time": fmt_time(t + rng.randint(30, 90)), "type": "vehicle_repair",
                                "vehicle": vehicle, "reason": "generated repair"})
    # Sort by real time, not text ("00:10+1d" must come after "23:50").
    disruptions.sort(key=lambda d: parse_time(d["time"]))
    return emergencies, disruptions
