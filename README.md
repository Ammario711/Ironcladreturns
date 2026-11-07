
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

## Requirements and dependencies
- **Python 3.10 or newer.** Nothing to install: only the Python standard library is used. The modules used are `heapq`, `dataclasses`, `enum`, `json`, `argparse`, `cmd`, `shlex`, `random`, `re`, `math`, `collections`, `copy` and `difflib`. The web dashboard also uses `http.server`, `threading` and `urllib.parse`. The optional weather download (`--fetch-weather`) uses `urllib.request`, `ssl` and `datetime`. The tests also use `unittest`, `unittest.mock`, `tempfile`, `os`, `io`, `http.client` and `urllib.request`.
- No third-party libraries.

## How to run (from this folder)
```
python3 --version                       # must be 3.10+
python3 main.py --demo --run            # Dataset 1 + sample scenario, prints the event log and a final report
python3 main.py --demo --run --quiet    # same, report only
python3 main.py --demo                  # same scenario in the interactive CLI (type 'help')
python3 main.py --demo --web            # same scenario in a live web dashboard: open http://localhost:8765
python3 main.py                         # empty interactive CLI on Dataset 1 (add emergencies yourself)
python3 -m unittest discover -s tests -v   # 142 automated tests, about 10 seconds (no internet needed)
```

### Dataset 2 (zero code changes)
```
python3 main.py --dataset path/to/dataset2.json --generate 10 --run      # random but repeatable scenario (seed 42)
python3 main.py --dataset path/to/dataset2.json --emergencies E.json --disruptions D.json --run
python3 main.py --dataset path/to/dataset2.json                          # interactive
```
- `--generate N --seed S` builds requests and disruptions from whatever places, roads and vehicles that dataset contains.
- If Dataset 2 embeds `emergencies` or `disruptions` lists, they are loaded automatically.
- To change speed factors, units or fuel for Dataset 2, copy [config/config.example.json](config/config.example.json), edit it, and pass `--config my.json`. The algorithm code does not change.

### Live web dashboard
Run `python3 main.py --demo --web` and open http://localhost:8765. It works with any dataset, needs no internet, and uses only the standard library on the server and plain HTML/CSS/JavaScript in the browser (`ui/static/`).
- **Live city:** the server runs the city clock itself and starts immediately (add `--paused` to start paused).
  - Every open browser tab shows the same city and updates by itself, every second.
  - Anything one person changes appears in all tabs straight away.
- **Controls:** Run live / Pause (Space), speed **Real time** (1 simulated minute per real minute), 1x, 5x or 15x, step +1 or +10 minutes, run to the end, and Reset (click twice) to start the same scenario again, running live if the dashboard was started live.
- **City events menu:** restock a food hub with any amounts, set traffic on any road (×1 to ×5, 1 = clear), change the weather, close or reopen a road or an intersection, and break down or repair a vehicle. It uses the same checks as a disruptions file.
- **Dataset menu (D):** every place (POI), food hub, vehicle, intersection (node) and road (edge), with all their ids, lat/lng, km, `w`, current travel time, highway/terrain and live status.
  - Tables are searchable and sortable, and can be downloaded as CSV.
  - Clicking a row shows it on the map.
  - The Overview shows the dataset's sources, disclosure, loader warnings and the weather and rush-hour settings.
- **Map:** zoom with the scroll wheel and drag to pan. It shows trucks moving along their remaining routes, request pins coloured by state (pulsing when Delayed, Re-Routing or Failed), hubs with live stock, closed roads in red, jams in orange, and a weather effect.
- **Details panel:** click any request, truck, hub, road, intersection or place.
  - A request shows the full timestamped timeline, the lifecycle stepper, *why it has this priority* (all six score factors plus waiting time), and its vehicles.
  - Trucks, hubs and roads have action buttons: breakdown/repair, restock, close/reopen, traffic jam.
- **New emergency form (N):** checked by the same validation as files and the CLI. Errors appear next to the form, including "did you mean" for unknown places.
- **Also:** KPI cards, a priority queue, fleet and hub lists, a filterable live activity feed, pop-up alerts, a light/dark theme and a phone layout.

### Weather, rush hour and traffic
- **Real weather (Open-Meteo, free, no API key):** `python3 main.py --demo --weather samples/weather/toronto-downtown_2025-02-16.json --run` replays a real snowy Toronto morning that is saved in the repo, so it works offline and gives the same result every time.
- **Any city and day:** `python3 main.py --dataset X --fetch-weather 2025-02-16 --run`. This downloads that day's hourly weather for the dataset's centre **once**, saves it to `samples/weather/`, and uses it.
- **Rush hours:** `--config config/city_conditions.example.json` turns on typical weekday rush hours. The factors are team assumptions; tune them in the file.
- **Traffic incidents:** `traffic` events in a disruptions file, or the `traffic` command in the CLI.
- **What changes:** slower roads change ETAs, route choices for new trips, and the deadline part of each request's priority. Vehicles already on the road get updated ETAs.

### Interactive commands
| Command | What it does |
|---|---|
| `status` | Clock, emergencies per state, fleet, stock, closures |
| `queue` | Waiting requests in service order, with the score breakdown |
| `list` / `show E3` | All emergencies / one emergency's details, trips and timestamped history |
| `trips` | Vehicles on the road: load, route, ETA |
| `vehicles` / `hubs` | Fleet table / stock left at each hub |
| `route P0 P4` | Fastest route plus alternatives between places or nodes |
| `events [N] [type]` | Recent events from the event bus |
| `add` or `add key=value ...` | New emergency (guided form or one line); shows every validation error. In the form, an unknown location (e.g. "Toronto") is asked again with the closest matches (`y` accepts the first, `?` lists places, `cancel` stops) |
| `close 42` / `close 11 12` / `reopen ...` | Close or reopen a road by edge id or by its two end nodes |
| `closenode 20` / `reopennode 20` | Close or reopen an intersection |
| `breakdown V2` / `repair V2` | Vehicle breakdown (its cargo is reassigned) / repair |
| `restock P1 50 20` | Add normal and cold stock to a hub |
| `weather heavy_snow` / `traffic 38 2.5` | Change the weather (all roads slower) / slow one road (`traffic 38 1` clears it) |
| `tick [min]` / `run [HH:MM]` | Advance the clock / run to the end (or to a time) |
| `load emergencies FILE` | Schedule requests or disruptions from a file |
| `live on/off`, `report`, `quit` | Toggle live events, print the summary, exit |

## Input formats
**Dataset** ([data/toronto-downtown.json](data/toronto-downtown.json)): nodes, edges, pois, hubs and trucks, in the official structure. Unknown keys are ignored with a warning. Missing optional fields get defaults from config, and bad records are skipped with a message saying which record and why.

**Emergencies** (see [samples/emergencies.json](samples/emergencies.json)). The file can be a list, or an object with an `emergencies` list.

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

**Disruptions** (see [samples/disruptions.json](samples/disruptions.json)):
- `road_closure` and `road_reopen`, by `edge`, `edges`, or `a` + `b`
- `node_closure` and `node_reopen`
- `vehicle_breakdown` and `vehicle_repair`
- `restock`, with `hub`, `normal` and `cold`
- `weather`, with `condition` (clear, fog, drizzle, rain, heavy_rain, freezing_rain, snow, heavy_snow, storm)
- `traffic`, with the same road fields as `road_closure` plus `factor` (2 = twice as slow, 1 = cleared)

Every disruption has a `time` and an optional `reason`.

## Project structure
```
main.py            entry point (CLI arguments)
data/              OFFICIAL dataset (unchanged)
samples/           OUR sample emergencies/disruptions (AI-generated, not official)
                   + samples/weather/: saved real weather (Open-Meteo)
config/            example config overrides (incl. rush hours)
core/              algorithms, no dataset-specific code:
                   graph, routing (Dijkstra), emergency (validation), scoring,
                   priority_queue (heapq), matcher (greedy), trips, state_machine,
                   event_bus, disruptions, dispatcher, system (facade)
loader/            dataset / config / input-file loading
sim/               simulator + seeded scenario generator
ui/                command-line dashboard (cli.py) + web dashboard server (web.py)
                   + ui/static/: the dashboard page (index.html, app.css, app.js)
tests/             unittest suite + Dataset-2-style fixture
DECISIONS.md       algorithms, data structures, complexity, trade-offs
PROGRESS.md        build status, file map, known issues, next steps
make_zip.py        builds Ironcladreturns_Programming_Code.zip
```

## Citations
**Dataset.** `toronto-downtown.json` (Dataset 1) was provided by the MEC 2026 Programming Competition organisers. The sources listed inside it are:
- City of Toronto Open Data, https://www.toronto.ca/city-government/data-research-maps/open-data/
- Fort York Food Bank, https://www.fyfb.com/

The dataset's own disclosure says it is a generated simulation: an approximate downtown street graph with illustrative depots and resources. It is not surveyed roads, live traffic, or real food-bank inventories.

**Our own test data.** `samples/*.json` and `tests/fixtures/sparse_town.json` were written for this project (AI-assisted, see below). They are not official competition data.

**Generative AI.**
- **Claude (Anthropic, model *Claude Opus 5.5*, `claude-opus-5-5`)**, used via Claude Code on 2026-11-07, generated the initial draft of the modules, tests, sample scenarios, Open-Meteo weather snapshot integration, and documentation from the team's specification, implemented Round-1 review fixes, and drafted the redesigned web dashboard (`ui/web.py`, `ui/static/`).
- **Google Antigravity (model *Gemini*)**, used on 2026-11-07, performed two rounds of code review against the competition abstract and rulebook, merged city-conditions into `main`, removed redundant CLI commands, and implemented Round-2 bug fixes and regression tests (`Trip.retime` multi-change ETA math, rush-hour clock sync, `Simulator.advance`, coordinate/config/input guards).
- The team manually refactored and reviewed the source files. This section and [PROGRESS.md](PROGRESS.md) retain the history of AI contributions and the team’s verification.
- No AI tool was used to write presentation scripts.

**Weather data.** Weather data by Open-Meteo.com (https://open-meteo.com/), licensed CC BY 4.0. The saved file is `samples/weather/toronto-downtown_2025-02-16.json`. Weather codes follow the WMO code table used by Open-Meteo. The weather and rush-hour travel-time factors are our own assumptions, not measured data.

**Algorithms and references.**
- E. W. Dijkstra, "A note on two problems in connexion with graphs", *Numerische Mathematik* 1 (1959), for shortest paths.
- The Python `heapq` documentation, "Priority Queue Implementation Notes", for the lazy-deletion pattern used in `core/priority_queue.py`.
- J. Y. Yen, "Finding the k shortest loopless paths in a network", *Management Science* 17 (1971). Our `alternative_routes` is a simplified single-edge-detour version of this idea.
