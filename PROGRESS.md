<!-- AI-GENERATED DRAFT - team to review and modify -->
# PROGRESS: Ironcladreturns, Urban Food Crisis Response and Routing System

**Last updated:** 2026-11-07. All 9 draft steps are DONE, plus location suggestions (branch `feature/location-suggestions`). 71 tests pass. The demo runs.
**Status:** complete first draft (AI-generated). **Next:** team review, rewriting, and citations (see "Next steps").

## Resume here (teammate or another AI)
1. Read this file, then [README.md](README.md) (how to run) and [DECISIONS.md](DECISIONS.md) (why it works this way).
2. Python 3.10+, standard library only, nothing to install. Run every command from the project root.
3. Sanity check (about 1 second): `python3 -m unittest discover -s tests` should report 71 tests OK.
4. Demo: `python3 main.py --demo --run` (or `--quiet` for the report only, or drop `--run` for the interactive CLI).
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
| 7 | Simulator + tests | DONE | 64 tests including hidden-test categories; seeded generator; Dataset-2-style fixture |
| 8 | CLI | DONE | `cmd`-based dashboard; `--run` batch mode; `--demo`; `--generate N --seed S` |
| 9 | README + DECISIONS + zip | DONE | `make_zip.py` rebuilds the zip; presentation NOT drafted (rules forbid AI scripts) |

In progress: nothing.

### Changes after the first draft
| Change | Where | Notes |
|---|---|---|
| Unknown locations get "did you mean" suggestions; the `add` form re-asks until the location is valid | `core/emergency.py` (`suggest_locations`), `ui/cli.py` (`_ask_location`, `_print_places`) | Matches part of a name, words in any order, and typos (difflib). Works on any dataset. Tested in `tests/test_cli.py` |

## How to run
```
python3 main.py --demo --run                 # Dataset 1 + samples: event log + report
python3 main.py --demo                       # interactive (help, tick 5, queue, trips, show E5, close 42, breakdown V2, run)
python3 main.py --dataset other.json --generate 10 --run   # any dataset, e.g. Dataset 2
python3 main.py --emergencies samples/invalid_emergencies.json --run --quiet   # validation demo
python3 -m unittest discover -s tests -v     # tests
python3 make_zip.py                          # submission zip
```
Expected demo result: 7 Resolved and 1 Failed (E8: cold stock runs out, 210/220 delivered).
- E5 shows Re-Routing (King St closed).
- E3 shows a reefer breakdown with its cargo collected by another reefer.
- E6 is unreachable (Delayed) until Bloor reopens, then delivered 15 min late.

## File map
| Path | Purpose |
|------|---------|
| `main.py` | Entry point: arguments, loads everything, batch run or CLI |
| `data/toronto-downtown.json` | OFFICIAL Dataset 1 (unchanged copy; no AI header on purpose) |
| `samples/emergencies.json` | AI-GENERATED sample requests (8), not official |
| `samples/disruptions.json` | AI-GENERATED sample disruptions, timed to hit active trips |
| `samples/invalid_emergencies.json` | Broken requests to demo validation |
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
| `loader/config_loader.py` | DEFAULT_CONFIG (every tunable number, commented) + deep-merge override |
| `loader/dataset_loader.py` | Tolerant dataset parser to Dataset (graph, pois, hubs, vehicles, warnings) |
| `loader/input_loader.py` | Reads emergency/disruption files (list or `{key: list}`) |
| `sim/simulator.py` | Fixed-step timeline runner + text report |
| `sim/scenario_generator.py` | Seeded random scenario for ANY dataset (Dataset 2 demo) |
| `ui/cli.py` | Interactive dashboard (`cmd.Cmd`), table/event formatting |
| `tests/helpers.py` | `make_system`, `request`, `check_invariants`, `run_until` |
| `tests/test_loader_routing.py` | Loader robustness + routing |
| `tests/test_input_scoring_queue.py` | Validation, scoring, queue, aging/starvation |
| `tests/test_matcher_states_events.py` | Matcher constraints, state table, event bus |
| `tests/test_cli.py` | CLI add form: re-asking unknown locations (scripted input via unittest.mock) |
| `tests/test_scenarios.py` | End-to-end: closures, breakdowns, unreachable, simultaneous, Dataset-2 fixture, determinism |
| `tests/fixtures/sparse_town.json` | Dataset-2-style fixture: string ids, terrain list, island node, bad records, extra keys |
| `make_zip.py` | Builds `Ironcladreturns_Programming_Code.zip` |
| `README.md` / `DECISIONS.md` | Required docs (run, deps, citations / algorithms, complexity, trade-offs) |

## Known bugs / limitations (all documented in DECISIONS.md, section 8)
- `alternative_routes` uses single-edge detours, so it can miss alternatives that differ in 2+ edges.
- A vehicle's position is the last node it reached. Re-routing ignores progress along the current road.
- One trip serves one emergency (no multi-stop tours). No refuelling. Vehicles stay where they delivered.
- When a request fails, its stranded cargo is abandoned. A blocked vehicle's cargo goes back to its hub's stock even though the vehicle may not physically get there.
- The generic `priority-changed` event fires every ~17 minutes per waiting request (5-point threshold), so the event log is chatty in long scenarios.
- The CLI `add` form does not let you edit or cancel an emergency after it is accepted.
- Not tested on Windows (only paths built with `os.path`, so it should work).

## Next steps (for the team)
1. ~~Fill in the competitor names in README.md~~ DONE: Ammar, Haziq, Mihir, Daniel.
2. **Review and rewrite** each file. As you go, change its header from `AI-GENERATED DRAFT - team to review and modify` to `Reviewed and modified by <name>: <what changed>`, and fill in the table below. The rules require meaningful modification and citation.
3. Re-tune the scoring weights and aging rate together (config only), and be ready to justify the numbers to judges.
4. When Dataset 2 arrives, run `python3 main.py --dataset <file> --generate 10 --run` and read the loader warnings. Adjust `--config` only, not `/core`.
5. Optional stretch: a WebSocket live map dashboard. The event log payloads are already plain dicts/JSON. This needs a dependency such as `websockets`, or a stdlib SSE (`http.server`) page.
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
| (others) | | | |
