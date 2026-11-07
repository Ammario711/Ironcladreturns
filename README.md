<!-- AI-GENERATED DRAFT - team to review and modify -->
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
- **Python 3.10 or newer.** Nothing to install: only the Python standard library is used. The modules used are `heapq`, `dataclasses`, `enum`, `json`, `argparse`, `cmd`, `shlex`, `random`, `re`, `math`, `collections` and `copy`. The tests also use `unittest`, `tempfile` and `os`.
- No third-party libraries.

## How to run (from this folder)
```
python3 --version                       # must be 3.10+
python3 main.py --demo --run            # Dataset 1 + sample scenario, prints the event log and a final report
python3 main.py --demo --run --quiet    # same, report only
python3 main.py --demo                  # same scenario in the interactive CLI (type 'help')
python3 main.py                         # empty interactive CLI on Dataset 1 (add emergencies yourself)
python3 -m unittest discover -s tests -v   # 64 automated tests, under 1 second
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
| `add` or `add key=value ...` | New emergency (guided form or one line); shows every validation error |
| `close 42` / `close 11 12` / `reopen ...` | Close or reopen a road by edge id or by its two end nodes |
| `closenode 20` / `reopennode 20` | Close or reopen an intersection |
| `breakdown V2` / `repair V2` | Vehicle breakdown (its cargo is reassigned) / repair |
| `restock P1 50 20` | Add normal and cold stock to a hub |
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
| `refrigeration_required` | must be true exactly when `food_cold` > 0 (derived if omitted) |
| `severity` | 1 to 5 |
| `deadline`, `submitted_at` | `"HH:MM"` or minutes; the deadline must come after submission. A missing submission time means "now" |

**Disruptions** (see [samples/disruptions.json](samples/disruptions.json)):
- `road_closure` and `road_reopen`, by `edge`, `edges`, or `a` + `b`
- `node_closure` and `node_reopen`
- `vehicle_breakdown` and `vehicle_repair`
- `restock`, with `hub`, `normal` and `cold`

Every disruption has a `time` and an optional `reason`.

## Project structure
```
main.py            entry point (CLI arguments)
data/              OFFICIAL dataset (unchanged)
samples/           OUR sample emergencies/disruptions (AI-generated, not official)
config/            example config override
core/              algorithms, no dataset-specific code:
                   graph, routing (Dijkstra), emergency (validation), scoring,
                   priority_queue (heapq), matcher (greedy), trips, state_machine,
                   event_bus, disruptions, dispatcher, system (facade)
loader/            dataset / config / input-file loading
sim/               simulator + seeded scenario generator
ui/                command-line dashboard
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

**Generative AI.** Claude (Anthropic), model *Claude Opus 5.5* (`claude-opus-5-5`), was used through Claude Code on 2026-11-07. It generated the first draft of the code, tests, sample data and documentation, from a specification written by the team.
- Every AI-drafted file starts with `AI-GENERATED DRAFT - team to review and modify`.
- The team reviewed, tested and modified this draft. The table in [PROGRESS.md](PROGRESS.md) records who changed what. TODO: complete it before submission.
- No AI tool was used to write presentation scripts.

**Algorithms and references.**
- E. W. Dijkstra, "A note on two problems in connexion with graphs", *Numerische Mathematik* 1 (1959), for shortest paths.
- The Python `heapq` documentation, "Priority Queue Implementation Notes", for the lazy-deletion pattern used in `core/priority_queue.py`.
- J. Y. Yen, "Finding the k shortest loopless paths in a network", *Management Science* 17 (1971). Our `alternative_routes` is a simplified single-edge-detour version of this idea.
