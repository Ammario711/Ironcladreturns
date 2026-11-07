# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""CLI `add` form: unknown locations are re-asked with suggestions.

input() is replaced by a list of scripted answers (unittest.mock), and the
printed text is captured, so these run without a person at the keyboard.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import make_system
from sim.simulator import Simulator
from ui.cli import CrisisCLI


def ask_location(answers):
    """Run CrisisCLI._ask_location with scripted answers; return (value, printed text)."""
    system = make_system()
    cli = CrisisCLI(system, Simulator(system), live=False)
    out = io.StringIO()
    with mock.patch("builtins.input", side_effect=answers), redirect_stdout(out):
        value = cli._ask_location()
    return value, out.getvalue()


class AddFormLocationTests(unittest.TestCase):
    def test_unknown_name_suggests_and_y_accepts(self):
        value, printed = ask_location(["Toronto", "y"])
        self.assertEqual(value, "P3")
        self.assertIn("Did you mean", printed)
        self.assertIn("Toronto General Hospital", printed)

    def test_no_match_lists_places_then_accepts_valid_id(self):
        value, printed = ask_location(["xyzzy", "P4"])
        self.assertEqual(value, "P4")
        self.assertIn("No close match", printed)
        self.assertIn("Moss Park", printed)  # the place list was shown

    def test_question_mark_lists_places_and_cancel_stops(self):
        value, printed = ask_location(["?", "cancel"])
        self.assertIsNone(value)
        self.assertIn("P6", printed)

    def test_node_id_and_lat_lng_still_work(self):
        self.assertEqual(ask_location(["12"])[0], 12)
        self.assertEqual(ask_location(["43.6489, -79.3711"])[0], {"lat": "43.6489", "lng": "-79.3711"})

    def test_y_without_a_suggestion_is_not_accepted(self):
        value, _printed = ask_location(["y", "P5"])  # 'y' alone means nothing yet
        self.assertEqual(value, "P5")

    def test_full_add_form_creates_emergency_after_correction(self):
        system = make_system()
        cli = CrisisCLI(system, Simulator(system), live=False)
        answers = ["Toronto", "y", "flood", "", "120", "40", "0", "", "4", "09:30", ""]
        with mock.patch("builtins.input", side_effect=answers), redirect_stdout(io.StringIO()):
            cli.do_add("")
        (e,) = system.emergencies.values()
        self.assertEqual(e.node, 34)          # P3's node
        self.assertEqual(e.category, "hospital")


if __name__ == "__main__":
    unittest.main()
