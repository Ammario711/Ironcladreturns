# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""Read emergency / disruption input files.

Accepted layouts (so a differently-shaped official file still loads):
  [ {...}, {...} ]                          a plain list
  {"emergencies": [ ... ], "_note": "..."}  an object with a list under a known key
Records are returned raw; validation happens in core (emergency.py /
disruptions.py) so the CLI and files share exactly the same rules.
"""

import json

EMERGENCY_KEYS = ("emergencies", "requests", "crises", "items")
DISRUPTION_KEYS = ("disruptions", "events", "items")


class InputFileError(Exception):
    """File missing, not JSON, or no list of records inside."""


def load_records(path, keys):
    try:
        # utf-8-sig also accepts files saved with a byte-order mark
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise InputFileError(f"Input file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise InputFileError(f"{path} is not valid JSON (line {exc.lineno}): {exc.msg}") from None
    except (OSError, UnicodeDecodeError) as exc:  # a folder, no permission, not UTF-8 text...
        raise InputFileError(f"Cannot read input file {path}: {exc}") from None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            if isinstance(data.get(key), list):
                return data[key]
    raise InputFileError(f"{path} must be a list or an object with one of these list keys: {', '.join(keys)}")


def load_emergencies_file(path):
    return load_records(path, EMERGENCY_KEYS)


def load_disruptions_file(path):
    return load_records(path, DISRUPTION_KEYS)
