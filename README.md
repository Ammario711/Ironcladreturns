
# Urban Food Crisis Response and Routing System

| | |
|---|---|
| **Team name** | Ironcladreturns |
| **Competitors** | Ammar, Haziq, Mihir, Daniel |
| **Project title** | Urban Food Crisis Response and Routing System |
| **Competition** | MEC 2026, Programming Competition (November 7-8, 2026) |

## Overview
The system receives emergency food requests (hospital, shelter, community site and so on) and checks every field. It scores each request's urgency and keeps all waiting requests in a heap-based priority queue that ages them, so nothing waits forever.
It then greedily assigns a supply hub and a vehicle to each request in priority order, respecting:
- stock
- vehicle capacity
- refrigeration for cold food
- fuel
- whether a route is open

Routes are planned with Dijkstra's algorithm on the weighted road graph. When a road or intersection closes, only the vehicles whose remaining route is affected are re-routed, and the system says clearly when no route exists.

Each emergency moves through a validated state machine with these states:
- Open
- Prioritized
- Resource Assigned
- In Progress
- Re-Routing
- Delivered
- Resolved
- Delayed
- Failed

Every change is logged with a timestamp and an explanation. Components talk through a publish/subscribe event bus. A command-line dashboard shows the queue, assignments, routes and the event log, and lets you add emergencies and trigger disruptions.

The same code runs on any dataset with the competition file structure. The dataset path is a command-line argument.

## Submission version
This branch contains no bundled city dataset, demo requests, disruption scenarios, or saved weather/traffic observations. Supply any JSON dataset using the same structure as the competition Toronto dataset. All node IDs, roads, coordinates, locations, inventories and vehicles come from that input. Numeric and text IDs are supported. Embedded `emergencies` and `disruptions` lists are supported; otherwise the dashboard starts empty for you to add requests.

Python 3.10+; standard library only. No installation step or API key is needed for simulation. Files under `ui/static/` are served locally, so the UI needs no CDN or internet connection.

### Local use
Run from the extracted project directory, replacing the dataset path with the provided file:
```sh
python main.py --dataset /path/to/dataset.json --web
```
Open http://localhost:8765. For the terminal interface, omit `--web`. For batch processing:
```sh
python main.py --dataset /path/to/dataset.json --emergencies /path/to/requests.json --disruptions /path/to/events.json --run
```
`--dataset` is required unless `DATASET_PATH` is set. A missing input produces a clear usage error instead of loading a sample. Explicit `--dataset` overrides the environment. Paths may be absolute, and the program can be launched from another working directory.

### Hosted / LAN use
```sh
python main.py --dataset /path/to/dataset.json --web --host 0.0.0.0 --port 8765
```
Other computers connect to the server address and port. On a managed host, set `DATASET_PATH` to a readable file on that server, then use `python main.py --web` as the start command. The platform's `PORT` automatically selects `0.0.0.0:$PORT`; `HOST`, `--host` and `--port` can override the bind settings. Health checks use `/healthz`. The included Render configuration asks for `DATASET_PATH`; provision your actual input file on the host before starting it.

Docker, with the supplied dataset mounted read-only (replace `/absolute/input-folder` with your own folder):
```sh
docker build -t ironcladreturns .
docker run --rm -p 8765:8765 -v /absolute/input-folder:/inputs:ro -e DATASET_PATH=/inputs/dataset.json ironcladreturns
```
The dashboard uses same-origin requests and supports reverse-proxy event streams. For public access, use the hosting provider's HTTPS endpoint and restrict access to intended users: all connected users control the same simulation. The application keeps state in memory; use one process/instance for a shared simulation.

### Configuration and optional refreshes
Algorithm defaults (scoring, speed fallback, fuel, capacity, timing, weather factors) are centralized in `loader/config_loader.py` and can be overridden using `--config /path/to/config.json`. These are configurable model parameters, not city-specific data. Input weights are used directly; no Toronto values are substituted.

Traffic is optional and fetched separately:
```sh
python traffic_loader.py /path/to/dataset.json --output /path/to/traffic.json
python main.py --dataset /path/to/traffic.json --web
```
Enter the TomTom key at the hidden prompt or set `TOMTOM_API_KEY`. TLS verification stays enabled. Failed edges retain their input weights; an entirely failed refresh preserves the previous output. The simulator never calls TomTom.

`--weather /path/to/weather.json` replays your saved weather; `--fetch-weather YYYY-MM-DD` explicitly downloads weather for the supplied dataset's coordinates into `weather-cache/`. No saved weather is bundled. Weather/rush-hour factors are configurable assumptions. `--generate N` is an optional scenario generator that derives locations from the loaded dataset; it does not load bundled scenarios.

### Validation and packaging
```sh
python -m unittest discover -s tests -v
python make_zip.py
```
The tests generate temporary synthetic inputs during execution; no sample dataset files are shipped. The ZIP builder uses an explicit source-file allowlist and excludes runtime datasets, downloaded weather, credentials, caches and test-generated files.

## Input formats
**Dataset** (your supplied JSON file): nodes, edges, pois, hubs and trucks, in the official structure. Unknown keys are ignored with a warning. Missing optional fields get defaults from config, and bad records are skipped with a message saying which record and why.

**Emergencies** (your supplied JSON file). The file can be a list, or an object with an `emergencies` list.

| Field | Rule |
|---|---|
| `location` | place id (`"P3"`), node id (`33`), place name, or `{"lat": .., "lng": ..}` (nearest node) |
| `crisis_type` | required text (unknown types are accepted with a warning) |
| `location_category` | hospital, senior_home, shelter, school, community or other. Taken from the place if omitted |
| `people_affected` | whole number of at least 1 |
| `food_normal`, `food_cold` | whole numbers of at least 0; the total must be more than 0 |
| `food_quantity` | the abstract's single quantity (also `food_quantity_needed`, `quantity`, `units`), used only when `food_normal`/`food_cold` are absent: all cold if `refrigeration_required` is true, otherwise all normal |
| `refrigeration_required` | must be true exactly when `food_cold` > 0 (derived if omitted) |
| `severity` | 1 to 5 |
| `deadline`, `submitted_at` | `"HH:MM"`, `"HH:MM:SS"`, `"HH:MM+1d"` (next day), an ISO time like `"2026-11-07T08:30"` (date ignored), or minutes. The deadline must come after submission. A missing submission time means "now" (in a file: the scenario start) |

**Disruptions** (your supplied JSON file):
- `road_closure` and `road_reopen`, by `edge`, `edges`, or `a` + `b`
- `node_closure` and `node_reopen`
- `vehicle_breakdown` and `vehicle_repair`
- `restock`, with `hub`, `normal` and `cold`
- `weather`, with `condition` (clear, fog, drizzle, rain, heavy_rain, freezing_rain, snow, heavy_snow, storm)
- `traffic`, with the same road fields as `road_closure` plus `factor` (2 = twice as slow, 1 = cleared)

Every disruption has a `time` and an optional `reason`.


## Citations
**Generative AI.**
- **Claude (Anthropic, model *Claude Opus 5.5*, `claude-opus-5-5`)**, used via Claude Code on 2026-11-07, generated the initial draft of the modules, tests, sample scenarios, Open-Meteo weather snapshot integration, and documentation from the team's specification, implemented Round-1 review fixes, and drafted the redesigned web dashboard (`ui/web.py`, `ui/static/`).
- **Google Antigravity (model *Gemini*)**, used on 2026-11-07, performed two rounds of code review against the competition abstract and rulebook, merged city-conditions into `main`, removed redundant CLI commands, and implemented Round-2 bug fixes and regression tests (`Trip.retime` multi-change ETA math, rush-hour clock sync, `Simulator.advance`, coordinate/config/input guards).
- The team manually refactored and reviewed the source files. This section and [PROGRESS.md](PROGRESS.md) retain the history of AI contributions and the team’s verification.
- No AI tool was used to write presentation scripts.

- OpenAI Codex assisted with the standalone TomTom port, failed-refresh protection, dataset-independent submission packaging, and verification on 2026-11-07.

Weather downloads use Open-Meteo (CC BY 4.0); no weather observations are included in this submission.

**Algorithms and references.**
- E. W. Dijkstra, "A note on two problems in connexion with graphs", *Numerische Mathematik* 1 (1959), for shortest paths.
- The Python `heapq` documentation, "Priority Queue Implementation Notes", for the lazy-deletion pattern used in `core/priority_queue.py`.
- J. Y. Yen, "Finding the k shortest loopless paths in a network", *Management Science* 17 (1971). Our `alternative_routes` is a simplified single-edge-detour version of this idea.
