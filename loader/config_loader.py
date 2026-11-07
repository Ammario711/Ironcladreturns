# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""Default configuration + optional JSON override file.

Every tunable number lives here (not in /core) so the team can change
behaviour for Dataset 2 without touching algorithm code. A JSON file passed
with --config only needs the keys you want to change; it is deep-merged on
top of DEFAULT_CONFIG.
"""

import copy
import json

DEFAULT_CONFIG = {
    "scoring": {
        # Points per factor. They are normalised, so they do not have to add
        # up to 100, but they do here so each weight reads as a percentage.
        "weights": {
            "severity": 30,   # how bad the situation is (1-5)
            "people": 20,     # how many people are affected
            "deadline": 20,   # how little slack is left before the deadline
            "shortage": 10,   # how much food is still missing
            "location": 10,   # type of site (hospital > shelter > community...)
            "travel": 10,     # quicker to reach = help arrives sooner
        },
        "people_cap": 500,               # people count where the people factor maxes out
        "shortage_cap": 200,             # missing food units where shortage maxes out
        "deadline_horizon_minutes": 240, # slack >= this means "not urgent yet"
        "travel_cap_minutes": 60,        # travel >= this gets 0 travel points
        "category_weights": {            # location-type factor (0..1)
            "hospital": 1.0,
            "senior_home": 0.9,
            "shelter": 0.85,
            "school": 0.75,
            "community": 0.6,
            "other": 0.5,
        },
    },
    "queue": {
        # Aging: +0.3 priority points per minute waited (18 points/hour).
        # Worst case a score-0 request overtakes a fresh score-100 request
        # after 100 / 0.3 = 333 minutes, so nothing waits forever.
        "aging_points_per_minute": 0.3,
        # Only publish a priority-changed event when the priority moved by at
        # least this much since the last event (avoids flooding the log).
        "priority_change_event_threshold": 5.0,
        # Priorities are rounded before comparing, so near-equal scores count
        # as a tie and the documented tie-break rules decide.
        "round_digits": 2,
    },
    "routing": {
        # cost = w * terrain factor * (highway factor if hwy). 1.0 = use w as-is.
        "terrain_multipliers": {"urban": 1.0},
        "highway_multiplier": 1.0,
        # Only used when an edge has km but no w: w = km / speed * 60.
        "default_speed_kmh": 30,
    },
    "matching": {
        "max_vehicles_per_emergency_per_cycle": 3,  # fairness: one request cannot grab the whole fleet
        "load_minutes": 5,     # time to load at a hub
        "unload_minutes": 5,   # time to unload at the site
        "prefer_non_refrigerated_for_normal": True,  # keep reefers free for cold food
    },
    "fuel": {
        "enabled": True,
        "per_km": 0.25,        # fuel units used per km (dataset fuel=100 -> 400 km range)
        "default_fuel": 100,   # used when a truck has no fuel field
    },
    "units": {
        # Scale factors in case Dataset 2 uses different units
        # (e.g. stock in kg and capacity in crates).
        "stock_multiplier": 1.0,
        "capacity_multiplier": 1.0,
        "default_capacity": 50,  # used when a truck has no capacity field
    },
    "lifecycle": {
        "confirm_minutes": 5,      # Delivered -> Resolved after the site confirms
        "fail_grace_minutes": 30,  # how long past the deadline we keep trying
    },
    "validation": {
        "known_crisis_types": [
            "supply_shortage", "power_outage", "flood", "fire", "storm",
            "displacement", "contamination", "heat_wave", "other",
        ],
        "min_severity": 1,
        "max_severity": 5,
    },
    "simulation": {
        "step_minutes": 1,          # clock resolution of the simulator
        "max_minutes": 2 * 24 * 60, # hard stop so a stuck scenario still ends
    },
}


class ConfigError(Exception):
    """Raised when a config file cannot be read or is not a JSON object."""


def deep_merge(base, override):
    """Return a copy of base with override's keys applied recursively."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key.startswith("_"):  # "_comment" style keys are documentation only
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path=None):
    """Return the default config, optionally merged with a JSON file."""
    if path is None:
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8") as handle:
            override = json.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid JSON (line {exc.lineno}): {exc.msg}") from None
    if not isinstance(override, dict):
        raise ConfigError(f"Config file {path} must contain a JSON object at the top level")
    return deep_merge(DEFAULT_CONFIG, override)
