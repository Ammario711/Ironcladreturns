"""Input validation, scoring, and the heap priority queue (aging + tie-break)."""

import unittest

from core.emergency import suggest_locations, validate_emergency
from core.priority_queue import CrisisQueue
from core.scoring import aging_bonus, compute_score
from helpers import TORONTO, config, request
from loader.dataset_loader import load_dataset


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config()
        cls.ds = load_dataset(TORONTO, cls.cfg)

    def check(self, raw, **kw):
        return validate_emergency(raw, self.ds.graph, self.ds.pois, self.cfg, **kw)

    def test_valid_request(self):
        r = self.check(request(food_cold=10))
        self.assertTrue(r.ok, r.errors)
        e = r.emergency
        self.assertEqual(e.node, 34)                 # P3 hospital node
        self.assertTrue(e.refrigeration_required)    # derived from food_cold
        self.assertEqual(e.deadline, 600)
        self.assertEqual(e.unassigned_total(), 50)

    def test_every_problem_reported_at_once(self):
        r = self.check({"location": "nowhere", "people_affected": 0, "food_normal": 2.5,
                        "severity": 7, "deadline": "9h", "submitted_at": "08:00"})
        self.assertFalse(r.ok)
        text = " | ".join(r.errors)
        for expected in ("not a known place", "crisis_type is required", "location_category is required",
                         "people_affected must be at least 1", "food_normal must be a whole number",
                         "severity must be at most 5", "deadline"):
            self.assertIn(expected, text)

    def test_missing_fields(self):
        r = self.check({})
        self.assertIn("location is required (a place id like 'P3', a node id, or {lat, lng})", r.errors)
        self.assertTrue(any("people_affected is required" in e for e in r.errors))
        self.assertTrue(any("food quantity is required" in e for e in r.errors))

    def test_refrigeration_must_match_cold_food(self):
        r1 = self.check(request(food_cold=5, refrigeration_required=False))
        self.assertIn("cold food needs a refrigerated vehicle", " ".join(r1.errors))
        r2 = self.check(request(food_cold=0, refrigeration_required=True))
        self.assertIn("food_cold is 0", " ".join(r2.errors))

    def test_deadline_must_follow_submission(self):
        r = self.check(request(deadline="07:59"))
        self.assertIn("must be after submission time", " ".join(r.errors))

    def test_category_inferred_from_poi_and_latlng_location(self):
        r = self.check(request(location_category=None, location={"lat": 43.6489, "lng": -79.3711}))
        self.assertIn("location_category is required", " ".join(r.errors))  # lat/lng is not a POI
        r = self.check(request(location_category=None))
        self.assertEqual(r.emergency.category, "hospital")
        r = self.check(request(location={"lat": 43.6489, "lng": -79.3711}))
        self.assertEqual(r.emergency.node, 14)

    def test_aliases_and_defaults(self):
        raw = {"site": "P4", "type": "Flood", "category": "Shelter", "people": "40", "normal": 10,
               "severity_level": 2, "due": "09:00"}
        r = self.check(raw, now=480)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.emergency.submitted_at, 480)
        self.assertIn("submitted_at missing", " ".join(r.warnings))

    def test_duplicate_id(self):
        r = self.check(request(id="E1"), existing_ids={"E1"})
        self.assertIn("already used", " ".join(r.errors))

    def test_unknown_location_suggests_close_matches(self):
        r = self.check(request(location="Toronto"))
        self.assertIn("did you mean P3", " ".join(r.errors))
        g, pois = self.ds.graph, self.ds.pois
        self.assertEqual(suggest_locations("waterfrnt", g, pois)[0][0], "P5")       # typo
        self.assertEqual(suggest_locations("moss park", g, pois)[0][0], "P4")       # part of a name
        self.assertEqual(suggest_locations("king and yonge", g, pois)[0][0], 12)    # intersection words
        self.assertEqual(suggest_locations("xyzzy", g, pois), [])                  # no false hints

    def test_unknown_crisis_type_is_only_a_warning(self):
        r = self.check(request(crisis_type="alien invasion"))
        self.assertTrue(r.ok)
        self.assertIn("not a known type", " ".join(r.warnings))


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config()
        cls.ds = load_dataset(TORONTO, cls.cfg)

    def score(self, travel=5.0, now=480, **fields):
        e = validate_emergency(request(**fields), self.ds.graph, self.ds.pois, self.cfg).emergency
        return compute_score(e, now, travel, self.cfg["scoring"], self.cfg["validation"])[0]

    def test_each_factor_raises_priority(self):
        self.assertGreater(self.score(severity=5), self.score(severity=2))
        self.assertGreater(self.score(people_affected=400), self.score(people_affected=10))
        self.assertGreater(self.score(deadline="08:30"), self.score(deadline="11:30"))
        self.assertGreater(self.score(food_normal=180), self.score(food_normal=10))
        self.assertGreater(self.score(location_category="hospital"), self.score(location_category="community"))
        self.assertGreater(self.score(travel=2), self.score(travel=40))

    def test_unreachable_site_gets_no_travel_points(self):
        e = validate_emergency(request(), self.ds.graph, self.ds.pois, self.cfg).emergency
        _score, breakdown = compute_score(e, 480, None, self.cfg["scoring"], self.cfg["validation"])
        self.assertEqual(breakdown["travel"]["points"], 0)

    def test_weights_are_configurable(self):
        cfg = config({"scoring": {"weights": {"severity": 1, "people": 0, "deadline": 0, "shortage": 0,
                                              "location": 0, "travel": 0}}})
        e = validate_emergency(request(severity=5), self.ds.graph, self.ds.pois, cfg).emergency
        self.assertEqual(compute_score(e, 480, 5, cfg["scoring"], cfg["validation"])[0], 100.0)

    def test_score_range(self):
        for sev in range(1, 6):
            self.assertTrue(0 <= self.score(severity=sev) <= 100)


class QueueTests(unittest.TestCase):
    def test_highest_priority_first(self):
        q = CrisisQueue()
        q.push("low", 10, 0, 5)
        q.push("high", 90, 0, 5)
        q.push("mid", 50, 0, 5)
        self.assertEqual([q.pop(), q.pop(), q.pop(), q.pop()], ["high", "mid", "low", None])

    def test_tie_break_submission_then_people_then_id(self):
        q = CrisisQueue()
        q.push("B", 50, 10, 5)
        q.push("A", 50, 10, 5)
        q.push("late", 50, 20, 999)
        q.push("early", 50, 5, 1)
        q.push("crowd", 50, 10, 9)
        q.push("almost", 50.001, 10, 1)  # rounds to 50.00: counts as a tie
        self.assertEqual([i for i, _p in q.ordered()], ["early", "crowd", "A", "B", "almost", "late"])

    def test_id_tie_break_uses_natural_order(self):
        q = CrisisQueue()
        for eid in ("E10", "E2", "E1"):
            q.push(eid, 50, 0, 1)
        self.assertEqual([q.pop(), q.pop(), q.pop()], ["E1", "E2", "E10"])

    def test_update_and_remove_are_lazy(self):
        q = CrisisQueue()
        q.push("x", 10, 0, 1)
        q.push("y", 20, 0, 1)
        q.update("x", 30, 0, 1)
        q.remove("y")
        self.assertEqual(len(q), 1)
        self.assertEqual(q.peek(), "x")
        self.assertEqual(q.pop(), "x")
        self.assertIsNone(q.pop())

    def test_rebuild(self):
        q = CrisisQueue()
        q.rebuild([("a", 1, 0, 1), ("b", 3, 0, 1), ("c", 2, 0, 1)])
        self.assertEqual([i for i, _ in q.ordered()], ["b", "c", "a"])
        self.assertIn("a", q)

    def test_aging_prevents_starvation(self):
        """A low-score request submitted at t=0 must eventually beat fresh high-score requests."""
        rate = config()["queue"]["aging_points_per_minute"]

        class Req:
            def __init__(self, submitted):
                self.submitted_at = submitted

        old_low, base_low, base_high = Req(0), 10.0, 90.0
        overtaken_at = None
        for now in range(0, 1000):
            q = CrisisQueue()
            q.push("old-low", base_low + aging_bonus(old_low, now, rate), 0, 1)
            q.push("new-high", base_high + aging_bonus(Req(now), now, rate), now, 1)
            if q.pop() == "old-low":
                overtaken_at = now
                break
        self.assertIsNotNone(overtaken_at)
        self.assertLessEqual(overtaken_at, (base_high - base_low) / rate + 1)


if __name__ == "__main__":
    unittest.main()
