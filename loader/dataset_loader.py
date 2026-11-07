# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""Load a city dataset JSON file (Dataset 1 = toronto-downtown.json).

Robustness rules (the hidden tests check these):
* Fatal problems (file missing, not JSON, top level not an object) raise
  DatasetError with a clear message.
* Everything else is tolerated: unknown keys are ignored, missing optional
  fields get defaults, and bad records are skipped with a warning that
  says exactly which record and why. The system still starts.
* No city-specific assumptions: ids may be ints or strings, any section may
  be empty or missing.
"""

import json
from dataclasses import dataclass, field

from core.graph import Edge, Graph, Node
from core.resources import Hub, Poi, Vehicle
from core.utils import haversine_km, normalize_id, to_bool, to_number

# Keys we understand at the top level. Anything else -> warning, ignored.
KNOWN_TOP_LEVEL_KEYS = {
    "id", "name", "center", "zoom", "nodes", "edges", "pois", "hubs",
    "trucks", "vehicles", "terrain", "sources", "disclosure",
    "emergencies", "requests", "disruptions", "events",
}


class DatasetError(Exception):
    """A problem so serious the dataset cannot be used at all."""


@dataclass
class Dataset:
    id: str
    name: str
    graph: Graph
    pois: dict
    hubs: dict
    vehicles: dict
    center: list | None = None
    zoom: float | None = None
    sources: list = field(default_factory=list)
    disclosure: str = ""
    warnings: list = field(default_factory=list)
    # Some datasets may embed requests/disruptions; we pass them on untouched.
    embedded_emergencies: list = field(default_factory=list)
    embedded_disruptions: list = field(default_factory=list)

    def summary(self):
        g = self.graph
        normal = sum(h.normal for h in self.hubs.values())
        cold = sum(h.cold for h in self.hubs.values())
        reefers = sum(1 for v in self.vehicles.values() if v.refrigerated)
        return (f"{self.name} ({self.id}): {len(g.nodes)} nodes, {len(g.edges)} roads, "
                f"{len(self.pois)} places, {len(self.hubs)} hubs (stock {normal} normal / {cold} cold), "
                f"{len(self.vehicles)} vehicles ({reefers} refrigerated)")


def load_dataset(path, config):
    """Read and parse a dataset file. Raises DatasetError on fatal problems."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        raise DatasetError(f"Dataset file not found: {path}") from None
    except OSError as exc:
        raise DatasetError(f"Cannot read dataset file {path}: {exc}") from None
    if text.strip() == "":
        raise DatasetError(f"Dataset file {path} is empty")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"Dataset {path} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}") from None
    if not isinstance(raw, dict):
        raise DatasetError(f"Dataset {path} must have a JSON object at the top level, found {type(raw).__name__}")
    return parse_dataset(raw, config)


def parse_dataset(raw, config):
    """Build a Dataset from an already-parsed JSON object (used by tests too)."""
    warnings = []
    # Keys starting with "_" are treated as comments (e.g. "_note"), silently.
    unknown = sorted(str(k) for k in raw if k not in KNOWN_TOP_LEVEL_KEYS and not str(k).startswith("_"))
    if unknown:
        warnings.append(f"Ignoring unknown top-level keys: {', '.join(unknown)}")

    routing_cfg = config["routing"]
    terrain = dict(routing_cfg["terrain_multipliers"])
    terrain.update(_read_terrain_section(raw.get("terrain"), warnings))
    graph = Graph(terrain_multipliers=terrain, highway_multiplier=routing_cfg["highway_multiplier"])

    _load_nodes(_as_list(raw, "nodes", warnings), graph, warnings)
    _load_edges(_as_list(raw, "edges", warnings), graph, warnings, routing_cfg["default_speed_kmh"])
    pois = _load_pois(_as_list(raw, "pois", warnings), graph, warnings)
    hubs = _load_hubs(_as_list(raw, "hubs", warnings), pois, graph, warnings, config["units"])
    truck_key = "trucks" if "trucks" in raw else "vehicles"
    vehicles = _load_vehicles(_as_list(raw, truck_key, warnings), graph, pois, warnings, config)

    if not graph.nodes:
        warnings.append("Dataset has no usable nodes: every emergency will be rejected as an unknown location")
    if not hubs:
        warnings.append("Dataset has no supply hubs: no food can be assigned")
    if not vehicles:
        warnings.append("Dataset has no vehicles: no deliveries can be made")

    center = raw.get("center") if isinstance(raw.get("center"), list) else None
    return Dataset(
        id=str(raw.get("id", "unnamed-dataset")),
        name=str(raw.get("name", raw.get("id", "Unnamed dataset"))),
        graph=graph, pois=pois, hubs=hubs, vehicles=vehicles,
        center=center, zoom=raw.get("zoom"),
        sources=[str(s) for s in raw.get("sources", []) if isinstance(s, (str, int, float))]
        if isinstance(raw.get("sources"), list) else [],
        disclosure=str(raw.get("disclosure", "")),
        warnings=warnings,
        embedded_emergencies=_as_list(raw, "emergencies", warnings) or _as_list(raw, "requests", warnings),
        embedded_disruptions=_as_list(raw, "disruptions", warnings) or _as_list(raw, "events", warnings),
    )


# ---------------------------------------------------------------- helpers
def _as_list(raw, key, warnings):
    """Return raw[key] if it is a list; [] (with a warning) otherwise."""
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"'{key}' should be a list but is {type(value).__name__}; ignoring it")
        return []
    return value


def _optional_float(record, key):
    try:
        return to_number(record[key]) if record.get(key) is not None else None
    except ValueError:
        return None


def _read_terrain_section(section, warnings):
    """Dataset 1 has "terrain": []. If Dataset 2 lists terrain types with a
    speed factor (e.g. {"type": "gravel", "multiplier": 1.4}) we use them.
    Anything we cannot interpret is ignored with a warning.
    """
    factors = {}
    if not isinstance(section, list):
        return factors
    for i, item in enumerate(section):
        if not isinstance(item, dict):
            warnings.append(f"terrain[{i}] is not an object; ignored")
            continue
        name = next((item[k] for k in ("type", "name", "terrain", "id") if k in item), None)
        factor = next((item[k] for k in ("multiplier", "factor", "speed_factor", "cost_multiplier") if k in item), None)
        try:
            if name is None or factor is None:
                raise ValueError("needs a type/name and a multiplier/factor")
            value = to_number(factor)
            if value <= 0:
                raise ValueError("multiplier must be > 0")
            factors[str(name)] = value
        except ValueError as exc:
            warnings.append(f"terrain[{i}] ignored: {exc}")
    return factors


def _load_nodes(records, graph, warnings):
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            warnings.append(f"nodes[{i}] is not an object; skipped")
            continue
        try:
            node_id = normalize_id(rec.get("id"))
        except ValueError as exc:
            warnings.append(f"nodes[{i}] skipped: bad id ({exc})")
            continue
        if node_id in graph.nodes:
            warnings.append(f"nodes[{i}] skipped: duplicate node id {node_id!r}")
            continue
        graph.add_node(Node(id=node_id, name=str(rec.get("name") or f"Node {node_id}"),
                            lat=_optional_float(rec, "lat"), lng=_optional_float(rec, "lng")))


def _load_edges(records, graph, warnings, default_speed_kmh):
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            warnings.append(f"edges[{i}] is not an object; skipped")
            continue
        try:
            a = normalize_id(rec.get("a"))
            b = normalize_id(rec.get("b"))
            edge_id = normalize_id(rec["id"]) if rec.get("id") is not None else f"auto-{i}"
            if a not in graph.nodes or b not in graph.nodes:
                raise ValueError(f"endpoint {a if a not in graph.nodes else b!r} is not a known node")
            km = to_number(rec["km"]) if rec.get("km") is not None else None
            w = to_number(rec["w"]) if rec.get("w") is not None else None
            if km is None:  # fall back to straight-line distance between the endpoints
                na, nb = graph.nodes[a], graph.nodes[b]
                if None not in (na.lat, na.lng, nb.lat, nb.lng):
                    km = haversine_km(na.lat, na.lng, nb.lat, nb.lng)
                elif w is not None:
                    km = w / 60.0 * default_speed_kmh
                else:
                    raise ValueError("has neither 'w' nor 'km'")
            if w is None:  # derive travel minutes from distance
                w = km / default_speed_kmh * 60.0
            if km < 0 or w < 0:
                raise ValueError("negative km or w")
            hwy = to_bool(rec["hwy"]) if rec.get("hwy") is not None else False
            graph.add_edge(Edge(id=edge_id, a=a, b=b, km=km, w=w,
                                street=str(rec.get("street") or ""), hwy=hwy,
                                terrain=str(rec.get("terrain") or "")))
        except (ValueError, KeyError) as exc:
            warnings.append(f"edges[{i}] skipped: {exc}")


def _load_pois(records, graph, warnings):
    pois = {}
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            warnings.append(f"pois[{i}] is not an object; skipped")
            continue
        try:
            poi_id = normalize_id(rec.get("id"))
            node = normalize_id(rec.get("node"))
        except ValueError as exc:
            warnings.append(f"pois[{i}] skipped: {exc}")
            continue
        if node not in graph.nodes:
            warnings.append(f"pois[{i}] ({poi_id}) skipped: node {node!r} does not exist")
            continue
        if poi_id in pois:
            warnings.append(f"pois[{i}] skipped: duplicate id {poi_id!r}")
            continue
        pois[poi_id] = Poi(id=poi_id, name=str(rec.get("name") or poi_id),
                           kind=str(rec.get("kind") or "other").strip().lower(), node=node,
                           lat=_optional_float(rec, "lat"), lng=_optional_float(rec, "lng"),
                           address=str(rec.get("address") or ""))
    return pois


def _load_hubs(records, pois, graph, warnings, units_cfg):
    hubs = {}
    mult = units_cfg["stock_multiplier"]
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            warnings.append(f"hubs[{i}] is not an object; skipped")
            continue
        try:
            ref = rec.get("poiId", rec.get("poi", rec.get("id")))
            if ref is not None and normalize_id(ref) in pois:
                poi = pois[normalize_id(ref)]
                hub_id, name, node = poi.id, poi.name, poi.node
            elif rec.get("node") is not None and normalize_id(rec["node"]) in graph.nodes:
                node = normalize_id(rec["node"])
                hub_id = normalize_id(ref) if ref is not None else f"H{node}"
                name = str(rec.get("name") or hub_id)
            else:
                raise ValueError(f"refers to unknown place {ref!r}")
            normal = int(to_number(rec.get("normal", 0)) * mult)
            cold = int(to_number(rec.get("cold", 0)) * mult)
            if normal < 0 or cold < 0:
                raise ValueError("stock cannot be negative")
        except ValueError as exc:
            warnings.append(f"hubs[{i}] skipped: {exc}")
            continue
        if hub_id in hubs:
            warnings.append(f"hubs[{i}] skipped: duplicate hub {hub_id!r}")
            continue
        hubs[hub_id] = Hub(id=hub_id, name=name, node=node, normal=normal, cold=cold,
                           initial_normal=normal, initial_cold=cold)
    # A POI marked as a hub but without a stock record still exists (stock 0),
    # so it can be restocked later.
    for poi in pois.values():
        if poi.kind == "hub" and poi.id not in hubs:
            warnings.append(f"POI {poi.id} is a hub but has no stock record; starting with 0 stock")
            hubs[poi.id] = Hub(id=poi.id, name=poi.name, node=poi.node, normal=0, cold=0)
    return hubs


def _load_vehicles(records, graph, pois, warnings, config):
    vehicles = {}
    cap_mult = config["units"]["capacity_multiplier"]
    default_cap = config["units"]["default_capacity"]
    default_fuel = config["fuel"]["default_fuel"]
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            warnings.append(f"trucks[{i}] is not an object; skipped")
            continue
        try:
            vid = normalize_id(rec.get("id"))
            home_raw = rec.get("home", rec.get("node"))
            home = normalize_id(home_raw)
            if home not in graph.nodes and home in pois:  # allow a POI id as home
                home = pois[home].node
            if home not in graph.nodes:
                raise ValueError(f"home {home_raw!r} is not a known node")
            if rec.get("capacity") is None:
                warnings.append(f"truck {vid}: no capacity given, using default {default_cap}")
                capacity = default_cap
            else:
                capacity = to_number(rec["capacity"])
            capacity = int(capacity * cap_mult)
            if capacity <= 0:
                raise ValueError("capacity must be > 0")
            fuel = to_number(rec["fuel"]) if rec.get("fuel") is not None else float(default_fuel)
            if fuel < 0:
                raise ValueError("fuel cannot be negative")
            refrigerated = to_bool(rec["refrigerated"]) if rec.get("refrigerated") is not None else False
        except ValueError as exc:
            warnings.append(f"trucks[{i}] skipped: {exc}")
            continue
        if vid in vehicles:
            warnings.append(f"trucks[{i}] skipped: duplicate id {vid!r}")
            continue
        vehicles[vid] = Vehicle(id=vid, name=str(rec.get("name") or vid), home=home, location=home,
                                refrigerated=refrigerated, capacity=capacity,
                                fuel=fuel, max_fuel=fuel)
    return vehicles
