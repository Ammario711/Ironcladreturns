
# PROGRESS: Ironcladreturns, Urban Food Crisis Response and Routing System

**Last updated:** 2026-11-07. The project base is `origin/main` at `baf4c48` (dashboard reset fix). The submission-ready work is on branch `feature/submission-ready`: `--dataset` is required, no sample data or city values are built in, and a fictional rehearsal town exercises a new dataset end to end. The full suite was rerun: **153 tests passed in 12.16 seconds**. The Riverbend dashboard was checked in a browser at `http://localhost:8766`; the dataset explorer showed all 31 roads and the emergency form opened and closed without submitting a request. The submission ZIP was rebuilt and checked: 54 entries, CRC check clean, required source/docs/fixtures present, and no hidden files, caches, Git data, or nested ZIPs.
**Status:** ready for the team's final review. **Next:** complete the review notes in the tracking table below, tune config when Dataset 2 is released, and rebuild the ZIP after any further edits.

## Resume here (teammate or another AI)
1. Read this file, then [README.md](README.md) (how to run) and [DECISIONS.md](DECISIONS.md) (why it works this way).
2. Python 3.10+, standard library only, nothing to install. Run every command from the project root.
3. The implementation run reported 153 tests OK. Rerun `python3 -m unittest discover -s tests` on the final checkout before submission.
4. Try it: `python3 main.py --dataset data/toronto-downtown.json --web` (empty live city at http://localhost:8765), or the same with `--generate 10 --run` for a scenario and a report. Any dataset in the competition format works the same way.
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
| 7 | Simulator + tests | DONE | 153 tests reported; seeded generator; Dataset-2-style and fictional rehearsal fixtures |
| 8 | CLI | DONE | `cmd`-based dashboard; `--run` batch mode; `--dataset` required; `--start`; `--generate N --seed S` |
| 9 | README + DECISIONS + zip | DONE | `make_zip.py` rebuilds the zip; presentation NOT drafted (rules forbid AI scripts) |

In progress: team review and final verification.

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
| Fuller city timeline in the Toronto test scenario: every closure reopens, a 2nd breakdown (V7, cargo rescued) + repair, food-bank restocks (normal food only, so E8 still shows the cold shortage), a traffic collision that clears | `tests/fixtures/toronto_disruptions.json` | City ends the morning back to normal (tested) |
| Weather / rush hour / traffic incidents change road costs; ETAs of moving vehicles are recomputed; new trips route around jams | `core/graph.py`, `core/system.py`, `core/trips.py` (`retime`), `core/dispatcher.py`, `core/disruptions.py` | New `weather` / `traffic` disruptions, `conditions-changed` event, CLI `weather` / `traffic` commands, dashboard shows conditions |
| Dashboard redesign: play/pause with speed, KPI cards, zoomable map with moving trucks and weather effects, click-anything details panel with actions, new-emergency form, filterable feed, pop-up alerts, reset, light/dark, phone layout | `ui/web.py` (server), `ui/static/index.html`, `app.css`, `app.js`, `main.py` (`make_world`) | Page moved out of the Python string into plain files; checked in the browser at desktop, phone, light and dark |
| Dashboard live mode: the server runs the clock (real time, 1x, 5x, 15x; `--paused` to start paused); all tabs update every second; City events menu (restock with any amounts, traffic x1-x5, weather, road/intersection closures, breakdown/repair); Dataset explorer (all POIs, hubs, vehicles, nodes, edges with ids, lat/lng, w, live status; search, sort, CSV); clickable intersections | `ui/web.py` (`/play`, `/dataset`, clock thread, `clock` stream signal), `ui/static/*`, `main.py --paused` | Tested: live clock, play checks, dataset contents, clock signal |
| Real weather from Open-Meteo (free, no key), downloaded once and replayed | `loader/weather_loader.py`, `main.py --fetch-weather / --weather` | Saved to the current folder; the tests replay a real snowy Toronto morning (`tests/fixtures/toronto_weather_2025-02-16.json`) and use a fake network |
| Round-2 review fixes + CLI cleanup (Antigravity): removed redundant `EOF`/`exit` CLI commands (keeping Ctrl-D clean exit); exact `Trip.retime` across multiple speed changes (`edge_costs`); rush-hour synced in `set_clock` before scoring/startup; `Simulator.advance` works when `minutes < step_minutes`; Dataset 2 coord/IO guards in `dataset_loader`/`weather_loader`; comma-safe `location_from_text`; `traffic` factor >= 1, finite config validation, `--weather` midnight/undated start, and `id: ""` rejection | `ui/cli.py`, `core/trips.py`, `core/dispatcher.py`, `core/system.py`, `sim/simulator.py`, `core/disruptions.py`, `core/emergency.py`, `loader/*.py`, `main.py`, `tests/*` | Merged `feature/city-conditions` to `main`; 131 unit tests passing; standardized `# AI-assisted ...` headers across `.py` files |
| Submission-ready version: `--dataset` required and `--demo` removed; sample scenarios moved to `tests/fixtures/` (only tests read them); start time is a setting (`simulation.start_time`, `--start`) instead of a hard-coded 08:00 in `main.py` and the generator; an empty live city keeps running on the dashboard (it used to count as "finished" and refuse Play); CLI help and form hints use ids from the loaded dataset; Toronto names removed from code comments; typo words count in "did you mean" ranking; weather downloads save to the current folder | `main.py`, `loader/config_loader.py`, `sim/simulator.py`, `sim/scenario_generator.py`, `ui/web.py`, `ui/cli.py`, `ui/static/*`, `core/emergency.py` | New `tests/fixtures/rehearsal_town.json` (fictional town in exactly the Dataset 1 format) and `tests/test_new_dataset.py` (11 end-to-end tests: load, routing, generated scenario, input files, batch, CLI, empty live dashboard, explorer, reset). The undated-request start no longer uses "deadline minus 60 min": it is the configured start, like everywhere else |

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
python3 main.py --dataset CITY.json --web                  # browser dashboard at http://localhost:8765 (Ctrl-C to stop)
python3 main.py --dataset CITY.json                        # interactive (help, tick 5, queue, trips, show ID, run)
python3 main.py --dataset CITY.json --generate 10 --run    # any dataset, e.g. Dataset 2
python3 main.py --dataset tests/fixtures/rehearsal_town.json --web   # rehearse a NEW dataset (fictional town)
python3 -m unittest discover -s tests -v                   # tests
python3 make_zip.py                                        # submission zip
```
Toronto test scenario (Dataset 1 only, it names Toronto ids):
```
python3 main.py --dataset data/toronto-downtown.json --emergencies tests/fixtures/toronto_emergencies.json --disruptions tests/fixtures/toronto_disruptions.json --run
python3 main.py --dataset data/toronto-downtown.json --emergencies tests/fixtures/toronto_emergencies.json --disruptions tests/fixtures/toronto_disruptions.json --weather tests/fixtures/toronto_weather_2025-02-16.json --config config/city_conditions.example.json --run   # snow + rush hour
python3 main.py --dataset data/toronto-downtown.json --emergencies tests/fixtures/invalid_emergencies.json --run --quiet   # validation
```
Expected Toronto result: 7 Resolved and 1 Failed (E8: cold stock runs out, 210/220 delivered).
- E5 shows Re-Routing (King St closed), then truck V7 breaks down with 60 units and V10 collects the cargo (delivered 08:34).
- E3 shows a reefer breakdown with its cargo collected by another reefer.
- E6 is unreachable (Delayed) until Bloor reopens, then delivered 7 min late (09:22, deadline 09:15).

## File map
| Path | Purpose |
|------|---------|
| `main.py` | Entry point: arguments, loads everything, then batch run, CLI or web dashboard |
| `data/toronto-downtown.json` | OFFICIAL Dataset 1 (unchanged copy; no AI header on purpose) |
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
| `sim/scenario_generator.py` | Seeded random scenario for ANY dataset (start time passed in from config) |
| `ui/cli.py` | Interactive dashboard (`cmd.Cmd`), table/event formatting |
| `ui/web.py` | Dashboard server: `http.server`, `/state` JSON, `/events` Server-Sent Events, POST actions (tick, run, road, disrupt, emergency, reset) |
| `ui/static/index.html` | Dashboard page skeleton |
| `ui/static/app.css` | Dashboard styling: colour tokens (light/dark), layout, components, responsive, motion |
| `ui/static/app.js` | Dashboard logic in 13 numbered sections: state, rendering, map, details panel, feed, form, playback, input |
| `tests/helpers.py` | `make_system`, `request`, `check_invariants`, `run_until` |
| `tests/test_loader_routing.py` | Loader robustness + routing |
| `tests/test_input_scoring_queue.py` | Validation, scoring, queue, aging/starvation |
| `tests/test_matcher_states_events.py` | Matcher constraints, state table, event bus |
| `tests/test_cli.py` | CLI add form: re-asking unknown locations (scripted input via unittest.mock) |
| `tests/test_review_fixes.py` | One test (or more) per Gemini review fix |
| `tests/test_conditions.py` | Weather/rush hour/traffic, ETA retiming, weather snapshot loader (fake network), Toronto city timeline |
| `tests/test_web.py` | Starts the real dashboard server on a free port: page files (whitelist), state, every action, reset, event stream |
| `tests/test_scenarios.py` | End-to-end: closures, breakdowns, unreachable, simultaneous, Dataset-2 fixture, determinism |
| `tests/test_new_dataset.py` | A NEW dataset end to end: rehearsal town through loader, routing, generator, input files, batch, CLI and the empty live dashboard |
| `tests/fixtures/rehearsal_town.json` | Fictional town in exactly the Dataset 1 format (different ids, highways, rural/gravel roads, one-bridge island) |
| `tests/fixtures/sparse_town.json` | Dataset-2-style fixture: string ids, terrain list, island node, bad records, extra keys |
| `tests/fixtures/toronto_emergencies.json`, `toronto_disruptions.json` | AI-GENERATED Toronto test scenario (8 requests + a full morning timeline), not official |
| `tests/fixtures/invalid_emergencies.json` | Broken requests for validation tests |
| `tests/fixtures/toronto_weather_2025-02-16.json` | Real hourly weather (Open-Meteo, CC BY 4.0), snowy morning |
| `make_zip.py` | Builds `Ironcladreturns_Programming_Code.zip` |
| `README.md` / `DECISIONS.md` | Required docs (run, deps, citations / algorithms, complexity, trade-offs) |

## Known bugs / limitations (all documented in DECISIONS.md, section 8)
- `alternative_routes` uses single-edge detours, so it can miss alternatives that differ in 2+ edges.
- A vehicle's position is the last node it reached. Re-routing ignores progress along the current road.
- One trip serves one emergency (no multi-stop tours). No refuelling. Vehicles stay where they delivered.
- When a request fails, uncollected stranded cargo is written off. A blocked vehicle's hub cargo goes back to that hub's stock even though the vehicle may not physically get there.
- ISO times: only the time of day is used (a scenario is one day; write later days as `HH:MM+1d`).
- The web dashboard is for local use (binds to 127.0.0.1, no login). Reset asks for a second click instead of a browser pop-up (embedded and kiosk browsers block pop-ups), and returns to the starting live/paused mode. Browser-side JavaScript has no automated tests; the rehearsal dashboard, dataset explorer, and emergency form were checked by hand in the browser on 2026-11-07.
- Weather/rush-hour factors are assumptions; vehicles already driving are not diverted around a new jam (only new trips avoid it).
- The generic `priority-changed` event fires every ~17 minutes per waiting request (5-point threshold), so the event log is chatty in long scenarios.
- The CLI `add` form does not let you edit or cancel an emergency after it is accepted.
- Not tested on Windows (only paths built with `os.path`, so it should work).

## Next steps (for the team)
1. ~~Fill in the competitor names in README.md~~ DONE: Ammar, Haziq, Mihir, Daniel.
2. **Finish team review and verification** for each file, and fill in who reviewed/modified each module in the table below before final submission. The README documents the original AI contributions and review history; repetitive inline banners were removed after the team refactor.
3. Re-tune the scoring weights and aging rate together (config only), and be ready to justify the numbers to judges.
4. When Dataset 2 arrives, run `python3 main.py --dataset <file> --generate 10 --run` and read the loader warnings. Adjust `--config` only, not `/core`.
5. ~~Optional stretch: live map dashboard~~ DONE: `python3 main.py --dataset CITY.json --web` (standard library, Server-Sent Events). Try it before the demo and decide whether to present it.
6. The team writes the presentation (`Ironcladreturns_Programming_Presentation.pdf`) itself. The rules forbid AI-written presentation scripts. Screenshots from the Toronto test scenario (`--run`, `show E5` / `show E3`) make good material.
7. Rebuild `Ironcladreturns_Programming_Code.zip` after any final edits; the current archive was verified on 2026-11-07.

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
| ui/static/app.js | | | |
| ui/static/app.css | | | |
| (others) | | | |
