"""Download TomTom traffic data for the offline delivery simulation.

Python 3.10 or newer. Standard library only.

Run (input dataset is required):
    python traffic_loader.py city.json --output city-traffic.json

API key:
    Enter it at the hidden terminal prompt, or set TOMTOM_API_KEY.

Only edge weights are updated. Failed lookups retain their original weights.
If no lookups succeed, no output is written and any saved snapshot is preserved.
"""

import argparse
import copy
import getpass
import http.client
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import ssl


ROUTING_URL = "https://api.tomtom.com/routing/1/calculateRoute"



class TrafficError(Exception):
    """A traffic lookup failed; retain the original edge weight."""


def is_finite_number(value):
    """Reject booleans, strings, infinity, and NaN."""
    try:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    except OverflowError:
        return False


def coordinates(node):
    """Read the exact node coordinates without rounding."""
    lat = node["lat"]
    lng = node["lng"]

    if not (
        is_finite_number(lat)
        and -90 <= lat <= 90
        and is_finite_number(lng)
        and -180 <= lng <= 180
    ):
        raise ValueError("invalid GPS coordinates")

    return lat, lng


def validate_dataset(data):
    """Check baseline weights so failed lookups have safe fallbacks."""
    if not isinstance(data, dict):
        raise ValueError("dataset must be a JSON object")

    if not isinstance(data.get("nodes"), list):
        raise ValueError("dataset must contain a 'nodes' list")

    if not isinstance(data.get("edges"), list):
        raise ValueError("dataset must contain an 'edges' list")

    seen = set()

    for index, node in enumerate(data["nodes"]):
        if (
            not isinstance(node, dict)
            or isinstance(node.get("id"), bool)
            or not isinstance(node.get("id"), (int, str))
        ):
            raise ValueError(
                f"nodes[{index}] must have an integer or string id"
            )

        if node["id"] in seen:
            raise ValueError(
                f"nodes[{index}] has a duplicate id"
            )

        seen.add(node["id"])

    for index, edge in enumerate(data["edges"]):
        if (
            not isinstance(edge, dict)
            or not is_finite_number(edge.get("w"))
            or edge["w"] <= 0
        ):
            raise ValueError(
                f"edges[{index}] must have a finite numeric "
                "'w' greater than zero"
            )


def fetch_travel_minutes(start, end, api_key, timeout=10):
    """Request a traffic-aware route and convert seconds to minutes."""
    query = urllib.parse.urlencode({
        "key": api_key,
        "traffic": "true",
    })

    url = (
        f"{ROUTING_URL}/"
        f"{start[0]},{start[1]}:"
        f"{end[0]},{end[1]}"
        f"/json?{query}"
    )

    # Do not print the URL: it contains the API key.
    # HTTPS certificate verification remains enabled.
    try:
        with urllib.request.urlopen(
            url,
            timeout=timeout,
            context=ssl.create_default_context()
        ) as response:
            payload = json.load(response)

    except urllib.error.HTTPError as exc:
        raise TrafficError(
            f"TomTom returned HTTP {exc.code}"
        ) from None

    except (
        TimeoutError,
        urllib.error.URLError,
        OSError,
        http.client.HTTPException,
    ):
        raise TrafficError(
            "network, TLS, or timeout error"
        ) from None

    except (ValueError, UnicodeError):
        raise TrafficError(
            "TomTom returned invalid JSON"
        ) from None

    try:
        seconds = (
            payload["routes"][0]["summary"]["travelTimeInSeconds"]
        )
    except (KeyError, IndexError, TypeError):
        raise TrafficError(
            "TomTom returned no usable route summary"
        ) from None

    if not is_finite_number(seconds) or seconds <= 0:
        raise TrafficError(
            "TomTom returned an invalid travel time"
        )

    minutes = round(seconds / 60, 4)

    if minutes <= 0:
        raise TrafficError(
            "TomTom travel time rounds to zero minutes"
        )

    return minutes


def update_traffic(original, api_key, timeout=10):
    """Return an updated copy while preserving the original dataset."""
    validate_dataset(original)

    updated = copy.deepcopy(original)
    nodes = {
        node["id"]: node
        for node in original["nodes"]
    }

    attempts = 0
    successes = 0
    total = len(updated["edges"])

    for index, edge in enumerate(updated["edges"]):
        label = (
            f"[{index + 1}/{total}] "
            f"Edge {edge.get('id', index)}"
        )

        try:
            start = coordinates(nodes[edge["a"]])
            end = coordinates(nodes[edge["b"]])

            # Sleep between actual API calls, including failed calls.
            if attempts:
                time.sleep(0.5)

            attempts += 1

            minutes = fetch_travel_minutes(
                start,
                end,
                api_key,
                timeout=timeout,
            )

        except (
            TrafficError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            if isinstance(exc, (TrafficError, ValueError)):
                reason = str(exc)
            else:
                reason = "missing node reference or GPS coordinates"

            print(
                f"WARNING: {label}: {reason}; "
                f"keeping original w={edge['w']}.",
                file=sys.stderr,
            )
            continue

        print(
            f"{label}: "
            f"{edge['w']:.4f} -> {minutes:.4f} minutes"
        )

        edge["w"] = minutes
        successes += 1

    print(
        f"\nFetched {successes}/{total} edge times; "
        f"kept {total - successes} original weights."
    )

    if successes == 0:
        raise TrafficError(
            "No traffic times were fetched; output was not written. "
            "Any existing snapshot has been preserved."
        )

    return updated


def save_dataset(data, output_path):
    """Save atomically to avoid leaving a partially written JSON file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)

            json.dump(
                data,
                handle,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            handle.write("\n")

        # The temporary file is closed before replacement for Windows.
        os.replace(temporary_path, output_path)

    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "input",
        type=Path,
        help="original city JSON",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="new traffic-adjusted JSON file",
    )

    parser.add_argument(
        "--api-key",
        default=os.environ.get("TOMTOM_API_KEY"),
        help=(
            "TomTom key; otherwise uses TOMTOM_API_KEY "
            "or a hidden terminal prompt"
        ),
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10,
        help="timeout per request in seconds (default: 10)",
    )

    args = parser.parse_args(argv)

    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error(
            "--timeout must be finite and greater than zero"
        )

    output_path = args.output

    if output_path is None:
        stem = args.input.stem
        output_path = args.input.with_name(
            f"{stem}-live-traffic.json"
        )

    try:
        same_file = (
            args.input.resolve() == output_path.resolve()
            or (
                args.input.exists()
                and output_path.exists()
                and os.path.samefile(args.input, output_path)
            )
        )

        if same_file:
            parser.error(
                "input and output must be different files; "
                "keep the original dataset intact"
            )

        with args.input.open(encoding="utf-8-sig") as handle:
            original = json.load(handle)

        validate_dataset(original)

    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    api_key = (args.api_key or "").strip()

    if not api_key:
        if not sys.stdin.isatty():
            parser.error(
                "set TOMTOM_API_KEY or supply --api-key "
                "when running without an interactive terminal"
            )

        try:
            api_key = getpass.getpass(
                "Paste your TomTom API key (hidden), "
                "then press Enter: "
            ).strip()

        except (EOFError, KeyboardInterrupt):
            print(
                "\nCancelled; no traffic requests were made.",
                file=sys.stderr,
            )
            return 1

        if not api_key:
            parser.error("a TomTom API key is required")

    print(f"Input:  {args.input}")
    print(f"Output: {output_path}")
    print(
        f"Processing {len(original['edges'])} roads "
        "with TomTom traffic enabled.\n"
    )

    try:
        updated = update_traffic(
            original,
            api_key,
            timeout=args.timeout,
        )

        save_dataset(updated, output_path)

    except (OSError, ValueError, TrafficError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0 if validate_output(output_path, original) else 1


def validate_output(output_path, original):
    """Validate the saved file and print the top three increases.

    Increases are ranked by added minutes compared with the original
    simulation weights. They do not isolate congestion from differences
    between the simulated road graph and TomTom's real road network.
    """
    try:
        with Path(output_path).open(encoding="utf-8-sig") as handle:
            saved = json.load(handle)

        validate_dataset(original)
        validate_dataset(saved)

        if len(saved["edges"]) != len(original["edges"]):
            raise ValueError("the edge count changed")

        restored = copy.deepcopy(saved)
        increases = []
        changed_count = 0

        for index, (old, new) in enumerate(
            zip(original["edges"], saved["edges"])
        ):
            restored["edges"][index]["w"] = old["w"]

            difference = new["w"] - old["w"]

            if difference != 0:
                changed_count += 1

            if difference > 0:
                increases.append(
                    (difference, index, old, new)
                )

        if restored != original:
            raise ValueError(
                "dataset fields other than edge weights changed"
            )

    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            f"VALIDATION FAILED: {exc}",
            file=sys.stderr,
        )
        return False

    print(
        f"\nVALIDATION PASSED: {output_path}\n"
        f"All {len(saved['edges'])} edge weights are finite "
        f"and greater than zero.\n"
        f"{changed_count} weights changed from baseline.\n"
        "All other dataset fields were preserved."
    )

    print(
        "\nTop 3 travel-time increases versus "
        "the original simulation baseline:"
    )

    ranked = sorted(
        increases,
        key=lambda item: item[0],
        reverse=True,
    )[:3]

    for rank, (difference, index, old, new) in enumerate(ranked, 1):
        percentage = difference / old["w"] * 100

        print(
            f"  {rank}. Edge {new.get('id', index)} "
            f"({new['a']} -> {new['b']}, "
            f"{new.get('street', 'unnamed road')}): "
            f"{old['w']:.4f} -> {new['w']:.4f} minutes "
            f"(+{difference:.4f} minutes, +{percentage:.1f}%)"
        )

    if not increases:
        print(
            "  No increases found. Times may be lower, "
            "unchanged, or unavailable."
        )
    elif len(increases) < 3:
        print(
            f"  Only {len(increases)} edge(s) increased."
        )

    return True


if __name__ == "__main__":
    raise SystemExit(main())
