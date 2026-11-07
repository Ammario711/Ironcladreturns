
"""Disruption events (not in the dataset; they come from a separate file,
the CLI, or the scenario generator) and their validation.

Supported types (with accepted spellings):
  road_closure      {"edge": 21} or {"edges": [21, 22]} or {"a": 11, "b": 12}
  road_reopen       same fields as road_closure
  node_closure      {"node": 20}
  node_reopen       {"node": 20}
  vehicle_breakdown {"vehicle": "V2"}
  vehicle_repair    {"vehicle": "V2"}
  restock           {"hub": "P1", "normal": 100, "cold": 20}
Every disruption may have "time" ("HH:MM" or minutes) and "reason" (text).
"""

from dataclasses import dataclass, field

from core.utils import fmt_time, normalize_id, parse_time, to_number

DISRUPTION_TYPES = (
    "road_closure", "road_reopen", "node_closure", "node_reopen",
    "vehicle_breakdown", "vehicle_repair", "restock",
)
TYPE_ALIASES = {
    "road_closed": "road_closure", "close_road": "road_closure", "closure": "road_closure",
    "road_open": "road_reopen", "road_reopened": "road_reopen", "reopen_road": "road_reopen",
    "node_closed": "node_closure", "close_node": "node_closure",
    "node_reopened": "node_reopen", "reopen_node": "node_reopen",
    "breakdown": "vehicle_breakdown", "vehicle_broken": "vehicle_breakdown",
    "repair": "vehicle_repair", "vehicle_repaired": "vehicle_repair",
    "resupply": "restock", "hub_restock": "restock",
}


@dataclass
class Disruption:
    time: float
    type: str
    edges: list = field(default_factory=list)
    node: object = None
    vehicle: object = None
    hub: object = None
    normal: int = 0
    cold: int = 0
    reason: str = ""

    def describe(self):
        target = (f"edges {self.edges}" if self.edges else f"node {self.node}" if self.node is not None
                  else f"vehicle {self.vehicle}" if self.vehicle is not None
                  else f"hub {self.hub} +{self.normal}N/+{self.cold}C")
        why = f" ({self.reason})" if self.reason else ""
        return f"{fmt_time(self.time)} {self.type} {target}{why}"


def parse_disruption(raw, graph, vehicles, hubs, now=0.0):
    """Return (Disruption, []) or (None, [error messages])."""
    if not isinstance(raw, dict):
        return None, [f"disruption must be an object, got {type(raw).__name__}"]
    errors = []
    kind = str(raw.get("type", "")).strip().lower().replace("-", "_").replace(" ", "_")
    kind = TYPE_ALIASES.get(kind, kind)
    if kind not in DISRUPTION_TYPES:
        return None, [f"unknown disruption type {raw.get('type')!r}; expected one of {', '.join(DISRUPTION_TYPES)}"]
    try:
        time = parse_time(raw["time"]) if raw.get("time") is not None else now
    except ValueError as exc:
        return None, [f"time: {exc}"]
    d = Disruption(time=time, type=kind, reason=str(raw.get("reason", "")))

    if kind in ("road_closure", "road_reopen"):
        try:
            if raw.get("edges") is not None:
                # Must be a real, non-empty list: iterating the text "19" would
                # silently close edges 1 and 9.
                if not isinstance(raw["edges"], list) or not raw["edges"]:
                    raise ValueError(f"'edges' must be a non-empty list like [19, 20] (got {raw['edges']!r}); "
                                     "use 'edge' for a single road")
                d.edges = [normalize_id(e) for e in raw["edges"]]
            elif raw.get("edge") is not None:
                d.edges = [normalize_id(raw["edge"])]
            elif raw.get("a", raw.get("from")) is not None and raw.get("b", raw.get("to")) is not None:
                a, b = normalize_id(raw.get("a", raw.get("from"))), normalize_id(raw.get("b", raw.get("to")))
                d.edges = graph.edges_between(a, b)
                if not d.edges:
                    errors.append(f"no road directly connects node {a!r} and node {b!r}")
            else:
                errors.append("road disruption needs 'edge', 'edges', or 'a' and 'b'")
        except (ValueError, TypeError) as exc:
            errors.append(f"bad edge list ({exc})")
        for edge_id in d.edges:
            if edge_id not in graph.edges:
                errors.append(f"edge {edge_id!r} does not exist")
    elif kind in ("node_closure", "node_reopen"):
        try:
            d.node = normalize_id(raw.get("node"))
            if d.node not in graph.nodes:
                errors.append(f"node {d.node!r} does not exist")
        except ValueError as exc:
            errors.append(f"bad node id ({exc})")
    elif kind in ("vehicle_breakdown", "vehicle_repair"):
        try:
            d.vehicle = normalize_id(raw.get("vehicle", raw.get("truck")))
            if d.vehicle not in vehicles:
                errors.append(f"vehicle {d.vehicle!r} does not exist")
        except ValueError as exc:
            errors.append(f"bad vehicle id ({exc})")
    elif kind == "restock":
        try:
            d.hub = normalize_id(raw.get("hub", raw.get("poi")))
            if d.hub not in hubs:
                errors.append(f"hub {d.hub!r} does not exist")
            # JSON null for an amount means "none of this type" (0).
            d.normal = int(to_number(raw.get("normal") if raw.get("normal") is not None else 0))
            d.cold = int(to_number(raw.get("cold") if raw.get("cold") is not None else 0))
            if d.normal < 0 or d.cold < 0 or d.normal + d.cold == 0:
                errors.append("restock needs positive 'normal' and/or 'cold' amounts")
        except ValueError as exc:
            errors.append(f"restock: {exc}")
    return (None, errors) if errors else (d, [])
