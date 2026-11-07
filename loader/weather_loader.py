# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""Real weather for a real city, from Open-Meteo (free, no API key), saved
to a file ONCE and replayed from that file.

Why a saved "snapshot" instead of asking the internet during a run:
* runs stay repeatable (same file in -> same events out), so tests and the
  judges' demo always give the same result;
* the demo works without internet;
* nothing secret is needed (Open-Meteo has no API key), so nothing secret
  ends up in the public repo.

Flow:
  1. fetch_open_meteo(lat, lng, "2025-02-16")   -> snapshot dict (one network call)
  2. save_snapshot(snapshot, path)               -> JSON file you can commit
  3. load_snapshot(path)                          -> snapshot dict (offline)
  4. weather_disruptions(snapshot, start)         -> "weather" disruptions for the
     simulator, one per change in conditions (hour by hour)

Open-Meteo reports a WMO weather code per hour. WEATHER_CODES maps each code
to one of our condition names; config "conditions.weather_factors" turns a
condition into a travel-time factor. Data: Open-Meteo.com, CC BY 4.0.
"""

import datetime
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

from core.utils import fmt_time, parse_time

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"   # past days (a few days' delay)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"         # recent past, today, next ~2 weeks
ARCHIVE_DELAY_DAYS = 7  # newer than this -> ask the forecast service instead


class WeatherError(Exception):
    """Fetching or reading weather failed (message says why)."""


def weather_condition(code):
    """WMO weather code (Open-Meteo's weather_code) -> our condition name."""
    if code is None:
        return "clear"
    code = int(code)
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55):
        return "drizzle"
    if code in (56, 57, 66, 67):
        return "freezing_rain"
    if code in (61, 63, 80, 81):
        return "rain"
    if code in (65, 82):
        return "heavy_rain"
    if code in (71, 73, 77, 85):
        return "snow"
    if code in (75, 86):
        return "heavy_snow"
    if code >= 95:
        return "storm"
    return "clear"  # 0-3: clear to overcast


def city_point(dataset):
    """(lat, lng) for the weather request: the dataset's centre, else the average node."""
    if dataset.center and len(dataset.center) == 2:
        return float(dataset.center[0]), float(dataset.center[1])
    points = [(n.lat, n.lng) for n in dataset.graph.nodes.values() if n.lat is not None and n.lng is not None]
    if not points:
        raise WeatherError("dataset has no coordinates, so we cannot ask for its weather")
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


# Operating-system certificate bundles (macOS, Debian/Ubuntu, Red Hat/Fedora).
SYSTEM_CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


def _get_json(url, timeout):
    """HTTPS GET -> parsed JSON, with certificate checking ALWAYS on.

    Python installed from python.org on macOS does not use the system's
    certificates until "Install Certificates.command" is run, so the first
    try can fail verification. We then retry with the operating system's own
    bundle (still fully verified). We never switch verification off.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reply:
            return json.load(reply)
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        for cafile in SYSTEM_CA_FILES:
            if os.path.exists(cafile):
                context = ssl.create_default_context(cafile=cafile)
                with urllib.request.urlopen(url, timeout=timeout, context=context) as reply:
                    return json.load(reply)
        raise WeatherError("HTTPS certificates could not be verified. On macOS run "
                           "'/Applications/Python 3.x/Install Certificates.command' once, then retry.") from None


def fetch_open_meteo(lat, lng, date_text, timeout=20):
    """Hourly weather for one local day. ONE network request; raises WeatherError."""
    try:
        day = datetime.date.fromisoformat(date_text)
    except ValueError:
        raise WeatherError(f"date must look like 2025-02-16 (got {date_text!r})") from None
    recent = day >= datetime.date.today() - datetime.timedelta(days=ARCHIVE_DELAY_DAYS)
    base = FORECAST_URL if recent else ARCHIVE_URL
    params = {"latitude": f"{lat:.4f}", "longitude": f"{lng:.4f}", "start_date": date_text, "end_date": date_text,
              "hourly": "weather_code,precipitation,snowfall,wind_speed_10m", "timezone": "auto"}
    url = f"{base}?{urllib.parse.urlencode(params)}"
    try:
        data = _get_json(url, timeout)
    except urllib.error.HTTPError as exc:  # the service explains bad requests in JSON
        try:
            reason = json.load(exc).get("reason", str(exc))
        except (ValueError, AttributeError):
            reason = str(exc)
        raise WeatherError(f"Open-Meteo refused the request: {reason}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise WeatherError(f"could not reach Open-Meteo (no internet?): {exc}") from None
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise WeatherError(f"Open-Meteo returned no hourly data for {date_text}")

    def column(name):
        values = hourly.get(name) or []
        return values + [None] * (len(times) - len(values))

    hours = []
    for stamp, code, rain, snow, wind in zip(times, column("weather_code"), column("precipitation"),
                                              column("snowfall"), column("wind_speed_10m")):
        hours.append({"time": stamp[-5:], "weather_code": code, "condition": weather_condition(code),
                      "precipitation_mm": rain, "snowfall_cm": snow, "wind_kmh": wind})
    return {
        "_note": "Weather snapshot from Open-Meteo.com (CC BY 4.0), saved so runs are repeatable and work offline.",
        "source": url, "latitude": data.get("latitude", lat), "longitude": data.get("longitude", lng),
        "timezone": data.get("timezone", ""), "date": date_text, "hours": hours,
    }


def save_snapshot(snapshot, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=1)


def load_snapshot(path):
    try:
        with open(path, encoding="utf-8-sig") as handle:
            snapshot = json.load(handle)
    except FileNotFoundError:
        raise WeatherError(f"weather file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise WeatherError(f"weather file {path} is not valid JSON (line {exc.lineno}): {exc.msg}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise WeatherError(f"cannot read weather file {path}: {exc}") from None
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("hours"), list):
        raise WeatherError(f"weather file {path} needs an 'hours' list (make one with --fetch-weather)")
    return snapshot


def weather_disruptions(snapshot, start_minutes=None):
    """Turn hourly weather into "weather" disruptions: one whenever the condition
    changes. Hours before `start_minutes` (the scenario start) are folded into
    ONE event at the start, so the simulation does not begin at midnight."""
    hours = []
    for hour in snapshot.get("hours", []):
        try:
            hours.append((parse_time(hour["time"]), str(hour["condition"]), hour.get("weather_code")))
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed hours; the rest still works
    hours.sort(key=lambda h: h[0])
    if not hours:
        return []
    if start_minutes is not None:
        before = [h for h in hours if h[0] <= start_minutes]
        after = [h for h in hours if h[0] > start_minutes]
        first = before[-1] if before else hours[0]
        hours = [(max(first[0], start_minutes), first[1], first[2])] + after
    events, last = [], None
    for minutes, condition, code in hours:
        if condition == last:
            continue
        last = condition
        events.append({"time": fmt_time(minutes), "type": "weather", "condition": condition,
                       "reason": f"Open-Meteo {snapshot.get('date', '')} {fmt_time(minutes)}, WMO code {code}"})
    return events
