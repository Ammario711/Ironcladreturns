"""Emergency request model and input validation.

validate_emergency() never raises on bad user input. It returns ALL the
problems as readable messages, so the CLI can show everything at once and
a batch file can skip bad records but keep loading the good ones.

Field rules (what judges will ask about):
  id                optional text or whole number (blank -> error, missing -> AUTOn)
  location          required. POI id ("P3"), node id (33), or {"lat":..,"lng":..}
                    (lat -90..90, lng -180..180)
  crisis_type       required text. Unknown types are accepted with a warning.
  location_category required text unless the location is a POI (then POI kind is used)
  people_affected   required whole number >= 1
  food_normal       whole number >= 0 (default 0)
  food_cold         whole number >= 0 (default 0); total food must be > 0
  food_quantity     the abstract's single quantity; used only if food_normal and
                    food_cold are both absent: all cold if refrigeration_required
                    is true, otherwise all normal
  refrigeration_required  true/false; derived from food_cold if missing;
                    must be true exactly when food_cold > 0
  severity          required whole number in [min_severity, max_severity] (1-5)
  deadline          required time ("HH:MM" or minutes), after submitted_at
  submitted_at      time; if missing we use the current clock (with a warning)
"""

import difflib
from dataclasses import dataclass, field

from core.state_machine import CrisisState, TERMINAL_STATES
from core.utils import fmt_time, id_sort_key, normalize_id, parse_time, to_bool, to_number

# Words too common to count as a match ("Yonge St & King St" vs "king and yonge").
_FILLER_WORDS = {"and", "the", "st", "ave", "rd", "at", "of", "near", "&"}

# Other names we accept for each field (helps if Dataset 2 input files use
# slightly different headers). First match wins.
FIELD_ALIASES = {
    "location": ["location", "node", "poi", "site", "location_id"],
    "crisis_type": ["crisis_type", "type", "crisis"],
    "location_category": ["location_category", "category", "location_type"],
    "people_affected": ["people_affected", "number_of_people_affected", "people", "population"],
    "food_normal": ["food_normal", "normal", "normal_units"],
    "food_cold": ["food_cold", "cold", "cold_units"],
    # The abstract describes ONE "food quantity" plus a refrigeration flag.
    # Used only when food_normal / food_cold are not given (see validate_emergency).
    "food_quantity": ["food_quantity", "food_quantity_needed", "quantity", "units"],
    "refrigeration_required": ["refrigeration_required", "refrigeration_requirements",
                               "refrigeration", "needs_refrigeration"],
    "severity": ["severity", "severity_level"],
    "deadline": ["deadline", "delivery_deadline", "due"],
    "submitted_at": ["submitted_at", "submission_time", "submitted", "time"],
    "id": ["id", "emergency_id", "request_id"],
}


@dataclass
class Emergency:
    # ---- validated input ----
    id: str
    node: object              # graph node where food must be delivered
    location_label: str       # what the user typed, for display
    crisis_type: str
    category: str
    people: int
    need_normal: int
    need_cold: int
    refrigeration_required: bool
    severity: int
    deadline: float           # minutes since scenario start
    submitted_at: float
    # ---- runtime tracking ----
    state: CrisisState = CrisisState.OPEN
    history: list = field(default_factory=list)  # StateChange records (see state_machine.py)
    delivered_normal: int = 0
    delivered_cold: int = 0
    # Units not yet on any vehicle. Invariant:
    #   unassigned + (units on active trips) + delivered == requested
    unassigned_normal: int = 0
    unassigned_cold: int = 0
    trip_ids: list = field(default_factory=list)
    priority: float = 0.0          # score + aging, what the queue uses
    base_score: float = 0.0
    aging_bonus: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    last_published_priority: float | None = None
    last_wait_reason: str = ""
    delivered_at: float | None = None

    def requested_total(self):
        return self.need_normal + self.need_cold

    def delivered_total(self):
        return self.delivered_normal + self.delivered_cold

    def unassigned_total(self):
        return self.unassigned_normal + self.unassigned_cold

    def is_terminal(self):
        return self.state in TERMINAL_STATES

    def short(self):
        return (f"{self.id} @ {self.location_label} [{self.category}] sev {self.severity}, "
                f"{self.people} people, need {self.need_normal}N/{self.need_cold}C, "
                f"due {fmt_time(self.deadline)}")


@dataclass
class ValidationResult:
    emergency: Emergency | None
    errors: list
    warnings: list

    @property
    def ok(self):
        return not self.errors


def _get(raw, name):
    """Value of a field under its main name or any alias (None if absent)."""
    for key in FIELD_ALIASES.get(name, [name]):
        if key in raw and raw[key] is not None and raw[key] != "":
            return raw[key]
    return None


def _is_blank(value):
    """Missing, or text made only of spaces."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def _whole_number(value, name, errors, minimum=None, maximum=None):
    """Parse a whole number; append a clear message to errors if invalid."""
    try:
        number = to_number(value)
    except ValueError:
        errors.append(f"{name} must be a whole number (got {value!r})")
        return None
    if not number.is_integer():
        errors.append(f"{name} must be a whole number (got {value!r})")
        return None
    number = int(number)
    if minimum is not None and number < minimum:
        errors.append(f"{name} must be at least {minimum} (got {number})")
        return None
    if maximum is not None and number > maximum:
        errors.append(f"{name} must be at most {maximum} (got {number})")
        return None
    return number


def resolve_location(value, graph, pois):
    """Turn user input into (node_id, poi or None, error message or None)."""
    if isinstance(value, dict):
        if "lat" in value and "lng" in value:
            try:
                lat, lng = to_number(value["lat"]), to_number(value["lng"])
            except ValueError:
                return None, None, f"location lat/lng must be numbers (got {value!r})"
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                return None, None, f"location lat must be -90..90 and lng -180..180 (got {lat}, {lng})"
            node = graph.nearest_node(lat, lng)
            if node is None:
                return None, None, "location lat/lng given but no node has coordinates"
            return node, None, None
        value = value.get("poi", value.get("node"))
        if value is None:
            return None, None, "location object needs 'lat'+'lng', 'poi', or 'node'"
    try:
        key = normalize_id(value)
    except ValueError as exc:
        return None, None, f"location is invalid ({exc})"
    if key in pois:
        return pois[key].node, pois[key], None
    if key in graph.nodes:
        return key, None, None
    for poi in pois.values():  # allow the POI's name, case-insensitive
        if isinstance(value, str) and poi.name.lower() == value.strip().lower():
            return poi.node, poi, None
    message = f"location {value!r} is not a known place id, place name, or node id"
    hints = suggest_locations(str(value), graph, pois)
    if hints:
        message += "; did you mean " + " or ".join(f"{v} ({label})" for v, label, _kind in hints) + "?"
    return None, None, message


def suggest_locations(text, graph, pois, limit=3):
    """Closest places/intersections to free text, best first.

    Returns [(value to type, readable label, "place" or "intersection")].
    Scoring (higher is better), per candidate:
      3.0          the whole text appears in its id/name/address/kind
                   ("Toronto" -> "Toronto General Hospital")
      2 x share    share of the text's words found in it, if at least half
                   ("king and yonge" -> "Yonge St & King St")
      ratio        difflib similarity (typos), if >= 0.6 for the whole name
                   or >= 0.8 for a single word ("waterfrnt" -> "waterfront")
    Places (POIs) are listed before plain intersections on equal score.
    Works on any dataset: it only reads the loaded names.
    """
    query = text.strip().lower()
    if not query:
        return []
    words = [w for w in query.replace(",", " ").split() if len(w) >= 3 and w not in _FILLER_WORDS]
    candidates = [(p.id, p.name, f"{p.id} {p.name} {p.address} {p.kind}".lower(), "place", 0)
                  for p in pois.values()]
    candidates += [(n.id, n.name, f"{n.id} {n.name}".lower(), "intersection", 1)
                   for n in graph.nodes.values()]
    scored = []
    for value, name, haystack, kind, order in candidates:
        if query in haystack:
            score = 3.0
        elif words and sum(w in haystack for w in words) / len(words) >= 0.5:
            score = 2.0 * sum(w in haystack for w in words) / len(words)
        else:
            whole = max(difflib.SequenceMatcher(None, query, name.lower()).ratio(),
                        difflib.SequenceMatcher(None, query, str(value).lower()).ratio())
            per_word = max((difflib.SequenceMatcher(None, q, h).ratio()
                            for q in words for h in haystack.split()), default=0.0)
            if whole >= 0.6 or per_word >= 0.8:
                score = max(whole, per_word)
            else:
                score = 0.0
        if score > 0:
            scored.append((-score, order, id_sort_key(value), value, name, kind))
    scored.sort(key=lambda item: item[:3])
    return [(value, name, kind) for _s, _o, _k, value, name, kind in scored[:limit]]


def validate_emergency(raw, graph, pois, config, existing_ids=(), auto_id="E?", now=0.0):
    """Check a raw request dict and build an Emergency if it is valid."""
    errors, warnings = [], []
    if not isinstance(raw, dict):
        return ValidationResult(None, [f"request must be an object/dict, got {type(raw).__name__}"], [])
    vcfg = config["validation"]
    categories = config["scoring"]["category_weights"]

    # --- id (text or a whole number; ids elsewhere in the data may be numbers) ---
    raw_id = next((raw[k] for k in FIELD_ALIASES["id"] if k in raw and raw[k] is not None), None)
    emergency_id = None
    if raw_id is None:
        emergency_id = auto_id
    elif isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
        errors.append(f"id must be text or a whole number (got {raw_id!r})")
    elif str(raw_id).strip() == "":
        errors.append("id cannot be blank (leave it out to get an automatic id)")
    else:
        emergency_id = str(raw_id).strip()
    if emergency_id is not None and emergency_id in existing_ids:
        errors.append(f"id {emergency_id!r} is already used by another emergency")

    # --- location ---
    node, poi, label = None, None, ""
    loc_value = _get(raw, "location")
    if loc_value is None:
        errors.append("location is required (a place id like 'P3', a node id, or {lat, lng})")
    else:
        node, poi, problem = resolve_location(loc_value, graph, pois)
        if problem:
            errors.append(problem)
        else:
            label = f"{poi.id} {poi.name}" if poi else graph.node_label(node)

    # --- crisis type (must be real text; "   " counts as missing) ---
    crisis_type = _get(raw, "crisis_type")
    if _is_blank(crisis_type):
        errors.append("crisis_type is required (e.g. " + ", ".join(vcfg["known_crisis_types"][:4]) + ")")
        crisis_type = None
    elif not isinstance(crisis_type, str):
        errors.append(f"crisis_type must be text (got {crisis_type!r})")
        crisis_type = None
    else:
        crisis_type = crisis_type.strip().lower().replace(" ", "_")
        if crisis_type not in vcfg["known_crisis_types"]:
            warnings.append(f"crisis_type {crisis_type!r} is not a known type; accepted as-is")

    # --- location category (falls back to the POI's kind) ---
    category = _get(raw, "location_category")
    if _is_blank(category) and poi is not None:
        category = poi.kind
        warnings.append(f"location_category missing; using place type {category!r}")
    if _is_blank(category):
        errors.append("location_category is required (" + ", ".join(categories) + ")")
        category = None
    elif not isinstance(category, str):
        errors.append(f"location_category must be text (got {category!r})")
        category = None
    else:
        category = category.strip().lower().replace(" ", "_")
        if category not in categories:
            warnings.append(f"location_category {category!r} unknown; scored as 'other'")

    # --- numbers ---
    people = _get(raw, "people_affected")
    if people is None:
        errors.append("people_affected is required")
    else:
        people = _whole_number(people, "people_affected", errors, minimum=1)

    severity = _get(raw, "severity")
    if severity is None:
        errors.append(f"severity is required ({vcfg['min_severity']}-{vcfg['max_severity']})")
    else:
        severity = _whole_number(severity, "severity", errors, vcfg["min_severity"], vcfg["max_severity"])

    # --- refrigeration flag (read first: a single food_quantity needs it) ---
    refrigeration = _get(raw, "refrigeration_required")
    if refrigeration is not None:
        try:
            refrigeration = to_bool(refrigeration)
        except ValueError:
            errors.append(f"refrigeration_required must be true or false (got {refrigeration!r})")
            refrigeration = None

    # --- food: food_normal / food_cold, or one food_quantity (abstract's schema) ---
    raw_normal, raw_cold = _get(raw, "food_normal"), _get(raw, "food_cold")
    raw_total = _get(raw, "food_quantity")
    need_normal = _whole_number(raw_normal, "food_normal", errors, minimum=0) if raw_normal is not None else 0
    need_cold = _whole_number(raw_cold, "food_cold", errors, minimum=0) if raw_cold is not None else 0
    if raw_normal is None and raw_cold is None and raw_total is not None:
        # One quantity: it is all cold if refrigeration is required, else all normal.
        total = _whole_number(raw_total, "food_quantity", errors, minimum=1)
        if total is not None:
            if refrigeration:
                need_cold = total
            else:
                need_normal = total
            warnings.append(f"single food_quantity {total} counted as {'cold' if refrigeration else 'normal'} food "
                            f"(refrigeration_required is {bool(refrigeration)})")
    elif raw_normal is None and raw_cold is None:
        errors.append("food quantity is required: give food_normal and/or food_cold (or one food_quantity)")
    else:
        if raw_total is not None:
            warnings.append("food_quantity ignored because food_normal/food_cold were given")
        if need_normal is not None and need_cold is not None and need_normal + need_cold == 0:
            errors.append("total food requested is 0: food_normal + food_cold must be > 0")

    # --- refrigeration must agree with cold food ---
    if need_cold is not None:
        if refrigeration is None:
            refrigeration = need_cold > 0
        elif refrigeration and need_cold == 0:
            errors.append("refrigeration_required is true but food_cold is 0: say how many cold units are needed")
        elif not refrigeration and need_cold > 0:
            errors.append(f"food_cold is {need_cold} but refrigeration_required is false: cold food needs a refrigerated vehicle")

    # --- times ---
    submitted_at = _get(raw, "submitted_at")
    if submitted_at is None:
        submitted_at = now
        warnings.append(f"submitted_at missing; using current time {fmt_time(now)}")
    else:
        try:
            submitted_at = parse_time(submitted_at)
        except ValueError as exc:
            errors.append(f"submitted_at: {exc}")
            submitted_at = None
    deadline = _get(raw, "deadline")
    if deadline is None:
        errors.append("deadline is required (e.g. '09:30')")
    else:
        try:
            deadline = parse_time(deadline)
        except ValueError as exc:
            errors.append(f"deadline: {exc}")
            deadline = None
    if deadline is not None and submitted_at is not None:
        if deadline <= submitted_at:
            errors.append(f"deadline {fmt_time(deadline)} must be after submission time {fmt_time(submitted_at)}")
        elif deadline < now:
            warnings.append(f"deadline {fmt_time(deadline)} has already passed; request will be treated as most urgent")

    if errors:
        return ValidationResult(None, errors, warnings)
    emergency = Emergency(
        id=emergency_id, node=node, location_label=label, crisis_type=crisis_type,
        category=category, people=people, need_normal=need_normal, need_cold=need_cold,
        refrigeration_required=refrigeration, severity=severity, deadline=deadline,
        submitted_at=submitted_at, unassigned_normal=need_normal, unassigned_cold=need_cold,
    )
    return ValidationResult(emergency, [], warnings)
