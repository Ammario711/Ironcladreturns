
# PROGRESS: Ironcladreturns, Urban Food Crisis Response and Routing System

**Last updated:** 2026-11-07. All 9 core steps, location suggestions, live web dashboard, fuller city timeline + Open-Meteo weather / rush hour / traffic (`feature/city-conditions`), and Round-1 + Round-2 review fixes are merged on `main`. **131 tests pass.** The demo runs cleanly.
**Status:** active team integration & refinement on `main`. **Next:** final team review notes in the tracking table below, Dataset 2 config tuning when released, and `python3 make_zip.py` before submission.

## Resume here (teammate or another AI)
1. Read this file, then [README.md](README.md) (how to run) and [DECISIONS.md](DECISIONS.md) (why it works this way).
2. Python 3.10+, standard library only, nothing to install. Run every command from the project root.
3. Sanity check (a few seconds): `python3 -m unittest discover -s tests` should report 131 tests OK.
4. Demo: `python3 main.py --demo --run` (or `--quiet` for the report only, drop `--run` for the interactive CLI, or use `--web` for the browser dashboard at http://localhost:8765).
5. Before submitting: `python3 make_zip.py` creates `Ironcladreturns_Programming_Code.zip`.

## Step status
| # | Step | Status | Notes |
|---|------|--------|-------|
| 1 | Loader + graph | DONE | Tolerant loader (warnings, not crashes); closures as sets; `graph.version` |
| 2 | Dijkstra with closures | DONE | find_route / shortest_path_tree / alternative_routes; explicit reason when unavailable |
| 3 | Validation + scoring + heap queue | DONE | All errors listed together; 6-factor score; uncapped aging; tie-break submission, then people, then id |
| 4 | Greedy matcher | DONE | Cold first, reefer fill, stranded cargo first, ETA-greedy, reservations, reasons |
| 5 | State machine | DONE | Exact 9 states, transition table, timestamped explanations, notes |
| 6 | Event bus | DONE | 7 required + 4 extra events; dispatcher/system react via subscriptions |
| 7 | Simulator + tests | DONE | 131 tests now, including hidden-test categories; seeded generator; Dataset-2-style fixture |
| 8 | CLI | DONE | `cmd`-based dashboard; `--run` batch mode; `--demo`; `--generate N --seed S` |
| 9 | README + DECISIONS + zip | DONE | `make_zip.py` rebuilds the zip; presentation NOT drafted (rules forbid AI scripts) |

In progress: nothing.

### Changes after the first draft
| Change | Where | Notes |
|---|---|---|
| Unknown locations get "did you mean" suggestions; the `add` form re-asks until the location is valid | `core/emergency.py` (`suggest_locations`), `ui/cli.py` (`_ask_location`, `_print_places`) | Matches part of a name, words in any order, and typos (difflib). Works on any dataset. Tested in `tests/test_cli.py` |
| Gemini review, input: single `food_quantity`, extra aliases, lat/lng ranges, blank or non-text fields, `edges` must be a list, `null` restock amounts | `core/emergency.py`, `core/disruptions.py`, `core/utils.py` | `parse_time` adds `HH:MM:SS`, `+Nd`, ISO and rejects hours >= 24; `"--1"` stays a text id |
| Gemini review, loaders/config: `speed_factor` inverted, dict terrain, `--config` terrain wins, `null` stock = 0, `trucks` falls back to `vehicles`, byte-order mark files, folders give clear errors, bad config numbers refused | `loader/*.py` | `apply_overrides()` + `validate_config()` |
| Gemini review, routing/trips/fuel: one closed-node rule for trees and `find_route`; parallel-road alternatives; loading time kept on re-route; fuel checked before detours (strand or cancel); stranded cargo tidied after delivery | `core/routing.py`, `core/graph.py`, `core/trips.py`, `core/dispatcher.py` | Fuel never negative (checked every minute in tests) |
| Gemini review, matcher: cold phase picks the most cold on time; disconnected fleet gets an honest reason | `core/matcher.py` | Demo: E6 now delivered 09:22 instead of 09:30 |
| Gemini review, scoring/system/simulator/CLI: slack includes load+unload and the travel cap when unreachable; `resource-depleted` re-scores; O(n) refresh; partial-assignment priority events; no double ticks; undated inputs start with the scenario; report N/A and every rejection; `tick`/`events`/`add` guards; future `submitted_at` scheduled | `core/scoring.py`, `core/system.py`, `sim/*.py`, `ui/cli.py` | |
| Live web dashboard (+5 rubric points) | `ui/web.py`, `main.py --web` | stdlib `http.server` + Server-Sent Events; map, queue, emergencies, live events, tick/run buttons, click a road to close/reopen |
| Fuller city timeline in the sample: every closure reopens, a 2nd breakdown (V7, cargo rescued) + repair, food-bank restocks (normal food only, so E8 still shows the cold shortage), a traffic collision that clears | `samples/disruptions.json` | City ends the morning back to normal (tested) |
| Weather / rush hour / traffic incidents change road costs; ETAs of moving vehicles are recomputed; new trips route around jams | `core/graph.py`, `core/system.py`, `core/trips.py` (`retime`), `core/dispatcher.py`, `core/disruptions.py` | New `weather` / `traffic` disruptions, `conditions-changed` event, CLI `weather` / `traffic` commands, dashboard shows conditions |
| Real weather from Open-Meteo (free, no key), downloaded once and replayed | `loader/weather_loader.py`, `main.py --fetch-weather / --weather`, `samples/weather/` | Real snowy Toronto morning (2025-02-16) saved; tests use a fake network |
| Round-2 review fixes + CLI cleanup (Antigravity): removed redundant `EOF`/`exit` CLI commands (keeping Ctrl-D clean exit); exact `Trip.retime` across multiple speed changes (`edge_costs`); rush-hour synced in `set_clock` before scoring/startup; `Simulator.advance` works when `minutes < step_minutes`; Dataset 2 coord/IO guards in `dataset_loader`/`weather_loader`; comma-safe `location_from_text`; `traffic` factor >= 1, finite config validation, `--weather` midnight/undated start, and `id: ""` rejection | `ui/cli.py`, `core/trips.py`, `core/dispatcher.py`, `core/system.py`, `sim/simulator.py`, `core/disruptions.py`, `core/emergency.py`, `loader/*.py`, `main.py`, `tests/*` | Merged `feature/city-conditions` to `main`; 131 unit tests passing; standardized `# AI-assisted ...` headers across `.py` files |

**Parts of the weather/traffic idea we changed (and why):**
- *Live traffic API:* no free source works without an account and key, a key would sit in a public repo, and live data makes runs non-repeatable. We use rush hours (config) + `traffic` incident events instead.
- *Live weather during a run:* replaced by a one-time download saved to a file, so it is repeatable and works offline.
- *Re-Routing for slowdowns:* not used; Re-Routing stays for routes that become unavailable. Slowdowns update ETAs instead.
- *Cold-food restocks:* left out on purpose, so E8 still shows the limited-stock case.

**Gemini suggestions we did NOT follow (and why):**
- *Leave PROGRESS.md out of the zip:* README points here for the AI-use table, which is part of our citation trail.
- *Do not fill spare reefer space with normal food:* cold is loaded first, so the extra space never displaces cold food. Filling it saves a truck.
- *Fail at once if no suitable vehicle can reach any stocked hub:* our rule is that only a site that can never be reached fails immediately. Resource problems stay Delayed until deadline + grace, because a restock or repair can still fix them. We kept only the corrected reason text.
- *Reject all non-text ids:* number ids are allowed, as everywhere else in our loader. Blank text, true/false, lists and objects are rejected.
- *Read `queue._entries` directly:* added a public `queue.ids()` instead (same O(n)).

## How to run
```
python3 main.py --demo --run                 # Dataset 1 + samples: event log + report
python3 main.py --demo                       # interactive (help, tick 5, queue, trips, show E5, close 42, breakdown V2, run)
python3 main.py --demo --web                 # browser dashboard at http://localhost:8765 (Ctrl-C to stop)
python3 main.py --demo --weather samples/weather/toronto-downtown_2025-02-16.json --config config/city_conditions.example.json --run   # snow + rush hour
python3 main.py --dataset other.json --generate 10 --run   # any dataset, e.g. Dataset 2
python3 main.py --emergencies samples/invalid_emergencies.json --run --quiet   # validation demo
python3 -m unittest discover -s tests -v     # tests
python3 make_zip.py                          # submission zip
```
Expected demo result: 7 Resolved and 1 Failed (E8: cold stock runs out, 210/220 delivered).
- E5 shows Re-Routing (King St closed), then truck V7 breaks down with 60 units and V10 collects the cargo (delivered 08:34).
- E3 shows a reefer breakdown with its cargo collected by another reefer.
- E6 is unreachable (Delayed) until Bloor reopens, then delivered 7 min late (09:22, deadline 09:15).

## File map
| Path | Purpose |
|------|---------|
| `main.py` | Entry point: arguments, loads everything, then batch run, CLI or web dashboard |
| `data/toronto-downtown.json` | OFFICIAL Dataset 1 (unchanged copy; no AI header on purpose) |
| `samples/emergencies.json` | AI-GENERATED sample requests (8), not official |
| `samples/disruptions.json` | AI-GENERATED sample disruptions, timed to hit active trips |
| `samples/invalid_emergencies.json` | Broken requests to demo validation |
| `samples/weather/toronto-downtown_2025-02-16.json` | Real hourly weather (Open-Meteo, CC BY 4.0), snowy morning |
| `config/city_conditions.example.json` | Example `--config` with weekday rush hours (assumed factors) |
| `config/config.example.json` | Example override file for `--config` |
| `core/utils.py` | id normalisation, natural id sort, time parsing (HH:MM to minutes), haversine |
| `core/graph.py` | Graph, Node, Edge; closures; `edge_cost` with terrain/highway factors; BFS `connected` |
| `core/routing.py` | Dijkstra (heapq), RouteResult (found/reason), PathTree, alternatives |
| `core/emergency.py` | Emergency dataclass, field aliases, `validate_emergency`, `resolve_location` |
| `core/scoring.py` | 6-factor weighted score (0-100), aging bonus, explanation text |
| `core/priority_queue.py` | heapq max-queue: tie-break, lazy update/remove, O(n) rebuild |
| `core/resources.py` | Hub, Vehicle, Poi, StrandedCargo dataclasses + vehicle status constants |
| `core/trips.py` | Trip: route timeline, position at time t, closure check, `apply_plan` |
| `core/matcher.py` | Greedy hub + vehicle matching, reservations, `resource-depleted`, failure reasons |
| `core/state_machine.py` | CrisisState (9 states), ALLOWED_TRANSITIONS, StateChange, CrisisStateMachine |
| `core/event_bus.py` | EventBus (pub/sub, wildcard, log, error isolation), event type list |
| `core/disruptions.py` | Disruption dataclass + `parse_disruption` (types and aliases) |
| `core/dispatcher.py` | Dispatch cycle, deliveries, re-routing, breakdown recovery, `settle_state` |
| `core/system.py` | CrisisResponseSystem facade: intake, priorities, disruptions, `tick`, stats |
| `loader/config_loader.py` | DEFAULT_CONFIG (every tunable number, commented), `apply_overrides`, `validate_config` |
| `loader/dataset_loader.py` | Tolerant dataset parser to Dataset (graph, pois, hubs, vehicles, warnings) |
| `loader/weather_loader.py` | Open-Meteo download (once), snapshot save/load, WMO code to condition, weather events |
| `loader/input_loader.py` | Reads emergency/disruption files (list or `{key: list}`) |
| `sim/simulator.py` | Fixed-step timeline runner + text report |
| `sim/scenario_generator.py` | Seeded random scenario for ANY dataset (Dataset 2 demo) |
| `ui/cli.py` | Interactive dashboard (`cmd.Cmd`), table/event formatting |
| `ui/web.py` | Live web dashboard: `http.server`, `/state` JSON, `/events` Server-Sent Events, inline HTML/SVG/JS page |
| `tests/helpers.py` | `make_system`, `request`, `check_invariants`, `run_until` |
| `tests/test_loader_routing.py` | Loader robustness + routing |
| `tests/test_input_scoring_queue.py` | Validation, scoring, queue, aging/starvation |
| `tests/test_matcher_states_events.py` | Matcher constraints, state table, event bus |
| `tests/test_cli.py` | CLI add form: re-asking unknown locations (scripted input via unittest.mock) |
| `tests/test_review_fixes.py` | One test (or more) per Gemini review fix |
| `tests/test_conditions.py` | Weather/rush hour/traffic, ETA retiming, weather snapshot loader (fake network), sample city timeline |
| `tests/test_web.py` | Starts the real dashboard server on a free port: page, state, tick, road toggle, event stream |
| `tests/test_scenarios.py` | End-to-end: closures, breakdowns, unreachable, simultaneous, Dataset-2 fixture, determinism |
| `tests/fixtures/sparse_town.json` | Dataset-2-style fixture: string ids, terrain list, island node, bad records, extra keys |
| `make_zip.py` | Builds `Ironcladreturns_Programming_Code.zip` |
| `README.md` / `DECISIONS.md` | Required docs (run, deps, citations / algorithms, complexity, trade-offs) |

## Known bugs / limitations (all documented in DECISIONS.md, section 8)
- `alternative_routes` uses single-edge detours, so it can miss alternatives that differ in 2+ edges.
- A vehicle's position is the last node it reached. Re-routing ignores progress along the current road.
- One trip serves one emergency (no multi-stop tours). No refuelling. Vehicles stay where they delivered.
- When a request fails, uncollected stranded cargo is written off. A blocked vehicle's hub cargo goes back to that hub's stock even though the vehicle may not physically get there.
- ISO times: only the time of day is used (a scenario is one day; write later days as `HH:MM+1d`).
- The web dashboard is for local use (binds to 127.0.0.1, no login).
- Weather/rush-hour factors are assumptions; vehicles already driving are not diverted around a new jam (only new trips avoid it).
- The generic `priority-changed` event fires every ~17 minutes per waiting request (5-point threshold), so the event log is chatty in long scenarios.
- The CLI `add` form does not let you edit or cancel an emergency after it is accepted.
- Not tested on Windows (only paths built with `os.path`, so it should work).

## Next steps (for the team)
1. ~~Fill in the competitor names in README.md~~ DONE: Ammar, Haziq, Mihir, Daniel.
2. **Review and verify** each file as a team, and fill in who reviewed/modified each module in the table below before final submission. Every `.py` file now carries an inline `# AI-assisted ...` citation comment referencing `README.md`.
3. Re-tune the scoring weights and aging rate together (config only), and be ready to justify the numbers to judges.
4. When Dataset 2 arrives, run `python3 main.py --dataset <file> --generate 10 --run` and read the loader warnings. Adjust `--config` only, not `/core`.
5. ~~Optional stretch: live map dashboard~~ DONE: `python3 main.py --demo --web` (standard library, Server-Sent Events). Try it before the demo and decide whether to present it.
6. The team writes the presentation (`Ironcladreturns_Programming_Presentation.pdf`) itself. The rules forbid AI-written presentation scripts. Screenshots from `--demo --run` and `show E5` / `show E3` make good material.
7. Run `python3 make_zip.py` as the very last step before the deadline.

## AI-use tracking (fill in as you modify)
| File | Reviewed by | What we changed | Date |
|------|-------------|-----------------|------|
| core/routing.py | | | |
| core/priority_queue.py | | | |
| core/scoring.py | | | |
| core/matcher.py | | | |
| core/state_machine.py | | | |
| core/event_bus.py | | | |
| core/dispatcher.py | | | |
| core/system.py | | | |
| ui/web.py | | | |
| (others) | | | |
