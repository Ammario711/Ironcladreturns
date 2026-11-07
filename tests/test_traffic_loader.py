"""Offline traffic refresh, snapshot preservation, and startup regression tests."""

import copy
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.parse

import main
import traffic_loader as traffic


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "toronto-live-traffic.json"
BASELINE = ROOT / "data" / "toronto-downtown.json"


def response(seconds):
    return io.BytesIO(json.dumps({
        "routes": [{"summary": {"travelTimeInSeconds": seconds}}],
    }).encode())


class TrafficRefreshTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.input = Path(self.folder.name) / "city.json"
        self.output = Path(self.folder.name) / "city-live-traffic.json"
        self.original = {
            "nodes": [{"id": 1, "lat": 43.6432, "lng": -79.4096},
                      {"id": 2, "lat": 43.645, "lng": -79.37859999999999}],
            "edges": [{"id": n, "a": 1, "b": 2, "w": n + 3, "km": 0.8}
                      for n in range(3)],
            "disclosure": "Keep all non-weight fields.",
        }
        self.input.write_text(json.dumps(self.original), encoding="utf-8")
        self.stdout = self.stack.enter_context(mock.patch("sys.stdout", new_callable=io.StringIO))
        self.stderr = self.stack.enter_context(mock.patch("sys.stderr", new_callable=io.StringIO))
        self.sleep = self.stack.enter_context(mock.patch("traffic_loader.time.sleep"))
        self.urlopen = self.stack.enter_context(mock.patch("traffic_loader.urllib.request.urlopen"))
        self.stack.enter_context(mock.patch.dict(os.environ, {"TOMTOM_API_KEY": "test-only-key"}))

    def run_refresh(self):
        return traffic.main([str(self.input)])

    def test_request_coordinates_traffic_minutes_and_verified_tls(self):
        self.urlopen.return_value = response(101)
        minutes = traffic.fetch_travel_minutes((43.6432, -79.4096),
                                               (43.645, -79.37859999999999),
                                               "test-only-key", timeout=7)
        self.assertEqual(minutes, 1.6833)
        args, kwargs = self.urlopen.call_args
        url = urllib.parse.urlsplit(args[0])
        self.assertEqual((url.scheme, url.netloc), ("https", "api.tomtom.com"))
        self.assertEqual(url.path, "/routing/1/calculateRoute/"
                         "43.6432,-79.4096:43.645,-79.37859999999999/json")
        self.assertEqual(urllib.parse.parse_qs(url.query),
                         {"traffic": ["true"], "key": ["test-only-key"]})
        self.assertEqual(kwargs["timeout"], 7)
        self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(kwargs["context"].check_hostname)

    def test_partial_failure_keeps_baseline_and_paces_every_request(self):
        calls = mock.Mock()
        calls.attach_mock(self.urlopen, "request")
        calls.attach_mock(self.sleep, "sleep")
        self.urlopen.side_effect = [response(101), urllib.error.URLError("offline"), response(60)]
        self.assertEqual(self.run_refresh(), 0)
        saved = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual([edge["w"] for edge in saved["edges"]], [1.6833, 4, 1.0])
        for old, new in zip(self.original["edges"], saved["edges"]):
            new["w"] = old["w"]
        self.assertEqual(saved, self.original)
        self.assertEqual(json.loads(self.input.read_text()), self.original)
        self.assertEqual([call[0] for call in calls.mock_calls],
                         ["request", "sleep", "request", "sleep", "request"])
        self.assertEqual(self.sleep.call_args_list, [mock.call(0.5), mock.call(0.5)])

    def test_all_failed_requests_preserve_good_snapshot_byte_for_byte(self):
        good = SNAPSHOT.read_bytes()
        failures = [urllib.error.HTTPError("https://example.invalid/?key=test-only-key",
                                          403, "Forbidden", {}, None),
                    urllib.error.URLError(ssl.SSLCertVerificationError("certificate failed")),
                    TimeoutError("test-only-key")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.output.write_bytes(good)
                self.urlopen.side_effect = failure
                self.assertEqual(self.run_refresh(), 1)
                self.assertEqual(self.output.read_bytes(), good)
        self.assertIn("snapshot has been preserved", self.stderr.getvalue())
        self.assertNotIn("test-only-key", self.stdout.getvalue() + self.stderr.getvalue())
        self.assertEqual(list(Path(self.folder.name).glob("*.tmp")), [])

    def test_all_failed_requests_do_not_create_an_output(self):
        self.urlopen.side_effect = urllib.error.URLError("offline")
        self.assertEqual(self.run_refresh(), 1)
        self.assertFalse(self.output.exists())

    def test_invalid_responses_preserve_snapshot(self):
        good = SNAPSHOT.read_bytes()
        for payload in (b"not json", b'{}', b'{"routes": []}',
                        response(-1).getvalue(), response(float("nan")).getvalue(),
                        response(True).getvalue(), response("120").getvalue(),
                        response(0.0001).getvalue()):
            with self.subTest(payload=payload):
                self.output.write_bytes(good)
                self.urlopen.side_effect = lambda *a, **kw: io.BytesIO(payload)
                self.assertEqual(self.run_refresh(), 1)
                self.assertEqual(self.output.read_bytes(), good)

    def test_no_usable_coordinates_or_no_edges_preserves_snapshot(self):
        good = SNAPSHOT.read_bytes()
        invalid = copy.deepcopy(self.original)
        invalid["nodes"][0]["lat"] = 100
        empty = copy.deepcopy(self.original)
        empty["edges"] = []
        for data in (invalid, empty):
            with self.subTest(data=data):
                self.output.write_bytes(good)
                self.input.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(self.run_refresh(), 1)
                self.assertEqual(self.output.read_bytes(), good)
        self.urlopen.assert_not_called()
        self.sleep.assert_not_called()

    def test_successful_unchanged_weights_are_still_saved(self):
        self.output.write_bytes(SNAPSHOT.read_bytes())
        self.urlopen.side_effect = [response(180), response(240), response(300)]
        self.assertEqual(self.run_refresh(), 0)
        self.assertEqual(json.loads(self.output.read_text()), self.original)

    def test_atomic_replace_failure_preserves_snapshot_and_cleans_up(self):
        good = SNAPSHOT.read_bytes()
        self.output.write_bytes(good)
        self.urlopen.side_effect = [response(60) for _ in range(3)]
        with mock.patch("traffic_loader.os.replace", side_effect=OSError("write failed")):
            self.assertEqual(self.run_refresh(), 1)
        self.assertEqual(self.output.read_bytes(), good)
        self.assertEqual(list(Path(self.folder.name).glob("*.tmp")), [])

    def test_input_cannot_be_overwritten(self):
        before = self.input.read_bytes()
        with self.assertRaises(SystemExit) as error:
            traffic.main([str(self.input), "--output", str(self.input)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.input.read_bytes(), before)
        self.urlopen.assert_not_called()


class SavedTrafficStartupTests(unittest.TestCase):
    def test_snapshot_has_82_valid_changed_weights_and_preserves_other_fields(self):
        saved = json.loads(SNAPSHOT.read_text(encoding="utf-8-sig"))
        base = json.loads(BASELINE.read_text(encoding="utf-8-sig"))
        traffic.validate_dataset(saved)
        self.assertEqual(len(saved["edges"]), 82)
        for old, new in zip(base["edges"], saved["edges"]):
            self.assertNotEqual(old["w"], new["w"])
            self.assertEqual(new["w"], round(new["w"], 4))
            new["w"] = old["w"]
        self.assertEqual(saved, base)

    def test_default_uses_saved_snapshot(self):
        self.assertEqual(Path(main.build_parser().parse_args([]).dataset), SNAPSHOT)

    def test_missing_snapshot_falls_back_and_explicit_dataset_wins(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(main, "HERE", folder):
            self.assertEqual(Path(main.build_parser().parse_args([]).dataset),
                             Path(folder) / "data" / "toronto-downtown.json")
            data = Path(folder) / "data"
            data.mkdir()
            (data / "toronto-live-traffic.json").write_text("{}")
            self.assertEqual(main.build_parser().parse_args(["--dataset", "custom.json"]).dataset,
                             "custom.json")

    def test_normal_startup_loads_saved_weights_without_network(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("unexpected network")), \
                mock.patch.object(main, "CrisisCLI") as cli, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main.main([]), 0)
        cli.return_value.cmdloop.assert_called_once_with()
        system, _sim = cli.call_args.args
        saved = json.loads(SNAPSHOT.read_text(encoding="utf-8-sig"))
        self.assertEqual({edge.id: edge.w for edge in system.graph.edges.values()},
                         {edge["id"]: edge["w"] for edge in saved["edges"]})


if __name__ == "__main__":
    unittest.main()
