
"""Priority scoring: how urgent is an emergency right now?

score = 100 * sum(weight_i * factor_i) / sum(weight_i)      (0..100)

Every factor is scaled to 0..1 so the weights (config "scoring.weights")
are directly comparable:

  severity  (severity - min) / (max - min)        1 -> 0.0, 5 -> 1.0
  people    min(people / people_cap, 1)            500+ people -> 1.0
  deadline  1 - slack / horizon, clamped           slack = deadline - now - travel
                                                   - load - unload;
                                                   slack <= 0 (will be late) -> 1.0
  shortage  min(unmet units / shortage_cap, 1)     food still missing
  location  category weight from config            hospital 1.0 ... other 0.5
  travel    1 - travel / travel_cap, clamped       closer site -> help arrives sooner;
                                                   unreachable -> 0

Final queue priority = score + aging bonus (see aging_bonus below).
The breakdown dict lets the CLI explain *why* one request outranks another.
"""


def _clamp01(x):
    return max(0.0, min(1.0, x))


def compute_score(emergency, now, travel_minutes, scoring_cfg, validation_cfg=None, service_minutes=0.0):
    """Return (score 0..100, breakdown) for one emergency at time `now`.

    travel_minutes:  fastest hub-to-site drive time, or None if unreachable.
    service_minutes: time spent loading + unloading (counts against the deadline too).
    breakdown: {factor: {"factor": 0..1, "points": contribution}}
    """
    weights = scoring_cfg["weights"]
    lo = (validation_cfg or {}).get("min_severity", 1)
    hi = (validation_cfg or {}).get("max_severity", 5)
    categories = scoring_cfg["category_weights"]

    # Slack = time to spare if we sent food right now. An unreachable site is
    # assumed to be at least travel_cap away (not 0 minutes, which would
    # wrongly make it look less urgent).
    drive = travel_minutes if travel_minutes is not None else scoring_cfg["travel_cap_minutes"]
    slack = emergency.deadline - now - drive - service_minutes
    factors = {
        "severity": _clamp01((emergency.severity - lo) / max(hi - lo, 1)),
        "people": _clamp01(emergency.people / scoring_cfg["people_cap"]),
        "deadline": 1.0 if slack <= 0 else _clamp01(1 - slack / scoring_cfg["deadline_horizon_minutes"]),
        "shortage": _clamp01(emergency.unassigned_total() / scoring_cfg["shortage_cap"]),
        "location": categories.get(emergency.category, categories.get("other", 0.5)),
        "travel": 0.0 if travel_minutes is None
        else _clamp01(1 - travel_minutes / scoring_cfg["travel_cap_minutes"]),
    }
    total_weight = sum(weights.get(name, 0) for name in factors) or 1
    breakdown = {}
    score = 0.0
    for name, value in factors.items():
        points = 100.0 * weights.get(name, 0) * value / total_weight
        breakdown[name] = {"factor": round(value, 3), "points": round(points, 2)}
        score += points
    return round(score, 2), breakdown


def aging_bonus(emergency, now, points_per_minute):
    """Waiting-time bonus so low-priority requests are never starved.

    Grows linearly and without a cap: after W minutes of waiting a request
    gains W * rate points. Any request eventually outranks a newer one,
    because a newer one has waited less.
    """
    waited = max(0.0, now - emergency.submitted_at)
    return round(waited * points_per_minute, 2)


def explain(breakdown, aging=0.0):
    """Readable explanation, e.g. 'severity 30.0 + people 9.6 + ... + aging 3.0'."""
    parts = [f"{name} {info['points']:.1f}" for name, info in breakdown.items()]
    if aging:
        parts.append(f"aging {aging:.1f}")
    return " + ".join(parts)
