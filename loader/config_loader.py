"""Default configuration + optional JSON override file.

Every tunable number lives here (not in /core) so the team can change
behaviour for Dataset 2 without touching algorithm code. A JSON file passed
with --config only needs the keys you want to change; it is deep-merged on
top of DEFAULT_CONFIG.
"""

import copy
import json
import math

from core.utils import parse_time

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
    "conditions": {
        # Travel-time multipliers for weather (1.0 = normal roads). These are TEAM
        # ASSUMPTIONS, not measurements: wet/snowy roads are slower. Tune them here.
        "weather_factors": {
            "clear": 1.0, "fog": 1.2, "drizzle": 1.1, "rain": 1.15, "heavy_rain": 1.3,
            "freezing_rain": 1.6, "snow": 1.4, "heavy_snow": 1.7, "storm": 1.5,
        },
        # Rush hours: [{"start": "07:00", "end": "09:30", "factor": 1.3}, ...].
        # Empty by default so the dataset's w is used unchanged; see
        # your --config JSON file to configure time windows.
        "rush_hours": [],
        # Publish route-updated when a vehicle's ETA moves at least this many minutes.
        "eta_change_event_minutes": 1.0,
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


# Settings that must be > 0: zero would divide by zero (caps, speed) or make
# the simulator loop forever (step_minutes). Paths are inside the config dict.
POSITIVE_SETTINGS = [
    ("scoring", "people_cap"), ("scoring", "shortage_cap"),
    ("scoring", "deadline_horizon_minutes"), ("scoring", "travel_cap_minutes"),
    ("routing", "default_speed_kmh"), ("routing", "highway_multiplier"),
    ("matching", "max_vehicles_per_emergency_per_cycle"),
    ("units", "stock_multiplier"), ("units", "capacity_multiplier"), ("units", "default_capacity"),
    ("simulation", "step_minutes"), ("simulation", "max_minutes"),
]
# Settings that may be 0 but never negative.
NON_NEGATIVE_SETTINGS = [
    ("queue", "aging_points_per_minute"), ("queue", "priority_change_event_threshold"),
    ("matching", "load_minutes"), ("matching", "unload_minutes"),
    ("fuel", "per_km"), ("fuel", "default_fuel"),
    ("lifecycle", "confirm_minutes"), ("lifecycle", "fail_grace_minutes"),
    ("conditions", "eta_change_event_minutes"),
]


def validate_config(config):
    """Raise ConfigError if a setting is missing, the wrong type, or out of range."""
    if not isinstance(config, dict):
        raise ConfigError("config must be a JSON object")
    for section in DEFAULT_CONFIG:
        if not isinstance(config.get(section), dict):
            raise ConfigError(f"config {section} must be an object (got {config.get(section)!r})")

    def finite_number(value, label):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ConfigError(f"config {label} must be a finite number (got {value!r})")
        return value

    def number_at(section, key):
        return finite_number(config[section].get(key), f"{section}.{key}")

    for section, key in POSITIVE_SETTINGS:
        if number_at(section, key) <= 0:
            raise ConfigError(f"config {section}.{key} must be greater than 0 (got {config[section][key]})")
    for section, key in NON_NEGATIVE_SETTINGS:
        if number_at(section, key) < 0:
            raise ConfigError(f"config {section}.{key} cannot be negative (got {config[section][key]})")
    terrain = config["routing"].get("terrain_multipliers")
    if not isinstance(terrain, dict):
        raise ConfigError("config routing.terrain_multipliers must be an object")
    for name, factor in terrain.items():
        if finite_number(factor, f"routing.terrain_multipliers.{name}") <= 0:
            raise ConfigError(f"config routing.terrain_multipliers.{name} must be > 0 (got {factor!r})")
    weights = config["scoring"].get("weights")
    if not isinstance(weights, dict) or not weights:
        raise ConfigError("config scoring.weights must be a non-empty object")
    for name, weight in weights.items():
        if finite_number(weight, f"scoring.weights.{name}") < 0:
            raise ConfigError(f"config scoring.weights.{name} cannot be negative (got {weight!r})")
    if sum(weights.values()) <= 0:
        raise ConfigError("config scoring.weights must contain at least one positive weight")
    cat_weights = config["scoring"].get("category_weights")
    if not isinstance(cat_weights, dict) or not cat_weights:
        raise ConfigError("config scoring.category_weights must be a non-empty object")
    for name, weight in cat_weights.items():
        if finite_number(weight, f"scoring.category_weights.{name}") < 0:
            raise ConfigError(f"config scoring.category_weights.{name} cannot be negative (got {weight!r})")
    weather_factors = config["conditions"].get("weather_factors")
    if not isinstance(weather_factors, dict) or not weather_factors:
        raise ConfigError("config conditions.weather_factors must be a non-empty object")
    for name, factor in weather_factors.items():
        if finite_number(factor, f"conditions.weather_factors.{name}") <= 0:
            raise ConfigError(f"config conditions.weather_factors.{name} must be > 0 (got {factor!r})")
    rush_hours = config["conditions"].get("rush_hours")
    if not isinstance(rush_hours, list):
        raise ConfigError("config conditions.rush_hours must be a list")
    for i, rush in enumerate(rush_hours):
        if not isinstance(rush, dict):
            raise ConfigError(f"config conditions.rush_hours[{i}] must be an object")
        try:
            start, end = parse_time(rush["start"]), parse_time(rush["end"])
            factor = finite_number(rush["factor"], f"conditions.rush_hours[{i}].factor")
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"config conditions.rush_hours[{i}] needs start, end (HH:MM) and factor ({exc})") from None
        if start >= end or factor <= 0:
            raise ConfigError(f"config conditions.rush_hours[{i}]: start must be before end and factor > 0")


def apply_overrides(override):
    """Defaults + override, validated. The override is also kept in
    config["_overrides"] so values the team set explicitly (e.g. terrain
    factors) can win over values read from the dataset."""
    if not isinstance(override, dict):
        raise ConfigError(f"config override must be a JSON object (got {type(override).__name__})")
    config = deep_merge(DEFAULT_CONFIG, override)
    config["_overrides"] = copy.deepcopy(override)
    validate_config(config)
    return config


def load_config(path=None):
    """Return the default config, optionally merged with a JSON file."""
    if path is None:
        return apply_overrides({})
    try:
        # utf-8-sig also accepts files saved with a byte-order mark
        with open(path, encoding="utf-8-sig") as handle:
            override = json.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid JSON (line {exc.lineno}): {exc.msg}") from None
    except (OSError, UnicodeDecodeError) as exc:  # a folder, no permission, not UTF-8 text...
        raise ConfigError(f"Cannot read config file {path}: {exc}") from None
    if not isinstance(override, dict):
        raise ConfigError(f"Config file {path} must contain a JSON object at the top level")
    return apply_overrides(override)
