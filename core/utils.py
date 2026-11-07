# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""Small helper functions shared by every module.

Standard library only, so the core runs on any Python 3.10+ install.
"""

import math
import re

MINUTES_PER_DAY = 24 * 60


def normalize_id(value):
    """Make ids comparable no matter how a JSON file wrote them.

    The same id can appear as 33 in one place and "33" in another.
    We turn digit-only strings into ints so both match.
    Anything else ("P3", "V1", "N-north") stays a stripped string.
    Raises ValueError for unusable ids (None, bool, empty string, lists...).
    """
    if isinstance(value, bool):  # bool is a subclass of int in Python, so reject it first
        raise ValueError(f"id cannot be true/false (got {value!r})")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            raise ValueError("id cannot be an empty string")
        # Only plain ASCII integers like "33" or "-4" become ints. Anything else
        # ("--1", "P3", "²") stays text instead of crashing int().
        if re.fullmatch(r"-?[0-9]+", text):
            return int(text)
        return text
    raise ValueError(f"id must be a number or text (got {value!r})")


def id_sort_key(value):
    """Sort key for a mix of int and str ids: ints first, then "natural" text.

    Python cannot compare 3 < "P1", so we sort on a tuple instead.
    Text is split into letter and digit chunks so "V2" sorts before "V10"
    (plain string order would put "V10" first).
    Used everywhere we need a deterministic order, including the queue's
    final tie-break.
    """
    if isinstance(value, int):
        return (0, value, ())
    chunks = re.split(r"(\d+)", str(value))
    natural = tuple(int(c) if i % 2 else c for i, c in enumerate(chunks))
    return (1, 0, natural)


def to_number(value):
    """Convert int/float/numeric-string to float. Raises ValueError otherwise.

    Booleans are rejected on purpose: True would silently become 1.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"expected a number, got {value!r}")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise ValueError(f"expected a number, got {value!r}") from None
    else:
        raise ValueError(f"expected a number, got {value!r}")
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"expected a finite number, got {value!r}")
    return number


def to_bool(value):
    """Lenient boolean parsing for JSON and CLI input ("yes", "true", 1...)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "y", "1"):
            return True
        if text in ("false", "no", "n", "0"):
            return False
    raise ValueError(f"expected true/false, got {value!r}")


# "HH:MM", "HH:MM:SS", "HH:MM+1d" (what fmt_time prints after midnight), and
# the same with an ISO date in front ("2026-11-07T08:30" or "2026-11-07 08:30").
_CLOCK = re.compile(r"(?:\d{4}-\d{2}-\d{2}[T ])?(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\+(\d+)d)?")


def parse_time(value):
    """Convert a time value into minutes since the start of the scenario day.

    Accepted forms:
      * a number         -> already minutes (485 means 08:05)
      * "HH:MM"          -> clock time ("08:05" means 485); "HH:MM:SS" also works
      * "HH:MM+Nd"       -> N days later (the format fmt_time prints), so
                            printed times can be typed back in
      * ISO date + time  -> "2026-11-07T08:05"; the DATE IS IGNORED because a
                            scenario is one day (use +Nd for later days)
      * numeric text     -> minutes ("485")
    Raises ValueError with a readable message for anything else.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"expected a time like 'HH:MM' or a number of minutes, got {value!r}")
    if isinstance(value, str) and ":" in value:
        match = _CLOCK.fullmatch(value.strip())
        if not match:
            raise ValueError(f"time {value!r} is not in HH:MM format")
        hours, mins = int(match.group(1)), int(match.group(2))
        secs = int(match.group(3) or 0)
        days = int(match.group(4) or 0)
        if hours >= 24:
            raise ValueError(f"time {value!r} has hours >= 24 (write a later day as HH:MM+1d)")
        if mins >= 60:
            raise ValueError(f"time {value!r} has minutes >= 60")
        if secs >= 60:
            raise ValueError(f"time {value!r} has seconds >= 60")
        minutes = days * MINUTES_PER_DAY + hours * 60 + mins + secs / 60.0
    else:
        try:
            minutes = to_number(value)
        except ValueError:
            raise ValueError(f"time {value!r} is not 'HH:MM' or a number of minutes") from None
    if minutes < 0:
        raise ValueError(f"time {value!r} cannot be negative")
    return minutes


def fmt_time(minutes):
    """Format minutes-since-start as HH:MM (adds +Nd after midnight)."""
    if minutes is None:
        return "--:--"
    total = int(round(minutes))
    day, rest = divmod(total, MINUTES_PER_DAY)
    text = f"{rest // 60:02d}:{rest % 60:02d}"
    return text if day == 0 else f"{text}+{day}d"


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km between two lat/lng points.

    Only used as a fallback (missing edge length, nearest-node lookup).
    Routing itself always uses the dataset's edge weights.
    """
    radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    a = min(1.0, max(0.0, a))  # rounding can push a a hair outside [0, 1]; asin/sqrt would fail
    return 2 * radius_km * math.asin(math.sqrt(a))
