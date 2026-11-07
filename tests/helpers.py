"""Shared helpers for the test suite."""

import os

from core.system import CrisisResponseSystem
from core.trips import ACTIVE_STATUSES
from loader.config_loader import apply_overrides
from loader.dataset_loader import load_dataset, parse_dataset

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TORONTO = os.path.join(ROOT, "data", "toronto-downtown.json")
SPARSE = os.path.join(ROOT, "tests", "fixtures", "sparse_town.json")
SAMPLE_EMERGENCIES = os.path.join(ROOT, "samples", "emergencies.json")
SAMPLE_DISRUPTIONS = os.path.join(ROOT, "samples", "disruptions.json")


def config(overrides=None):
    """Same path as `--config`: defaults + overrides, validated."""
    return apply_overrides(overrides or {})


def make_system(path=TORONTO, overrides=None, raw=None):
    """Fresh system on a dataset file (or on an in-memory raw dict)."""
    cfg = config(overrides)
    dataset = parse_dataset(raw, cfg) if raw is not None else load_dataset(path, cfg)
    return CrisisResponseSystem(dataset, cfg)


def request(**fields):
    """A valid Toronto request; override any field by keyword."""
    base = {
        "id": "E1", "location": "P3", "crisis_type": "supply_shortage",
        "location_category": "hospital", "people_affected": 100,
        "food_normal": 40, "food_cold": 0, "severity": 3,
        "deadline": "10:00", "submitted_at": "08:00",
    }
    base.update(fields)
    return {k: v for k, v in base.items() if v is not None}


def check_invariants(test, system):
    """Things that must ALWAYS be true, whatever happened."""
    busy_vehicles = []
    for trip in system.dispatcher.trips.values():
        if trip.status in ACTIVE_STATUSES:
            busy_vehicles.append(trip.vehicle_id)
    test.assertEqual(len(busy_vehicles), len(set(busy_vehicles)), "a vehicle is on two active trips")
    for vehicle in system.vehicles.values():
        test.assertGreaterEqual(vehicle.fuel, -1e-6, f"vehicle {vehicle.id} fuel went negative")
    for hub in system.hubs.values():
        test.assertGreaterEqual(hub.normal, 0, f"hub {hub.id} normal stock went negative")
        test.assertGreaterEqual(hub.cold, 0, f"hub {hub.id} cold stock went negative")
    for e in system.emergencies.values():
        in_transit = sum(t.units() for t in system.dispatcher.trips_for(e.id))
        test.assertEqual(e.unassigned_total() + in_transit + e.delivered_total(), e.requested_total(),
                         f"{e.id}: unassigned + in transit + delivered != requested")
        test.assertGreaterEqual(e.unassigned_normal, 0)
        test.assertGreaterEqual(e.unassigned_cold, 0)
        times = [h.time for h in e.history]
        test.assertEqual(times, sorted(times), f"{e.id}: history timestamps go backwards")


def run_until(system, sim, until, test=None):
    """Advance minute by minute, checking invariants each step when `test` is given."""
    t = sim.start_time() if not sim.started else system.now + sim.step
    while t <= until:
        sim.step_to(t)
        if test is not None:
            check_invariants(test, system)
        t += sim.step
