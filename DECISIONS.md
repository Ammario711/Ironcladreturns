
# Engineering decisions: algorithms, data structures, complexity, trade-offs

Notation: **V** = intersections (nodes), **E** = roads (edges), **H** = hubs, **K** = vehicles, **n** = waiting emergencies.
Dataset 1 has V=48, E=82, H=3, K=12.

## 1. Architecture
```
 CLI / simulator ──► CrisisResponseSystem (core/system.py, facade)
                        │ submit_emergency   apply_disruption   tick(now)
                        ▼
   ┌────────────── EventBus (publish / subscribe) ───────────────┐
   │ crisis-created ─► system: score + push on heap ─► Prioritized │
   │ road-closed ────► dispatcher: re-route affected trips         │
   │                 ► system: re-score queue (priority-changed)   │
   │ vehicle-breakdown ► dispatcher: return stock / strand cargo   │
   │ road-reopened ──► dispatcher: un-block waiting vehicles       │
   │ resource-depleted, route-updated, state-changed,              │
   │ crisis-resolved ─► CLI live view / event log                  │
   └───────────────────────────────────────────────────────────────┘
 Graph + routing (Dijkstra) ◄── Matcher (greedy) ◄── Dispatcher (trips) ── StateMachine
```
- **/core has no dataset knowledge.** Ids can be ints or strings. Speed factors, units and fuel come from config. All parsing tolerance lives in /loader.
- **Simulation clock, not wall clock.** Time is in minutes since the start of the scenario day. Combined with stable sorting everywhere, the same inputs always produce the same event log, and a test checks this.
- **Synchronous event bus.**
  - Handlers run in subscription order. Wildcard subscribers (loggers, the CLI) run first, so they print events in publication order.
  - The dispatcher subscribes before the system, so vehicles are re-routed before priorities are recalculated.
  - Payloads are plain dicts, so the web dashboard streams them as JSON (section 10).

## 2. Routing: Dijkstra with `heapq` ([core/routing.py](core/routing.py))
- **Cost.** Edge cost is `w × terrain factor × highway factor`. All factors default to 1.0, so the dataset's `w` (travel minutes) is used unchanged. This avoids counting terrain twice if `w` already includes it. `km` is only used for fuel.
- **Why not A\*.** A\* needs an admissible heuristic. With time-based costs and terrain multipliers, a straight-line km heuristic is only admissible if we know the fastest possible speed. Dijkstra is always correct, and at this graph size it costs well under a millisecond.
- **Complexity.** O((V + E) log V) per run.
  - A heap entry is `(distance, counter, node)`. The counter makes ties deterministic and avoids comparing an int id with a str id.
  - Stale heap entries are skipped (lazy deletion).
  - `find_route` stops as soon as the target is settled.
- **Closures.** Closed edges and nodes are kept in two sets. `Graph.open_neighbors()` skips them, so every route request automatically avoids them. Closing or reopening is O(1). A `version` counter lets the score cache know when travel times are stale.
- **Closed intersections, one rule everywhere.** A route never passes *through* a closed node and never ends *at* one. It may *start* at one, because a vehicle standing there can drive away. `find_route` and the reverse `shortest_path_tree` follow exactly this rule, so the matcher and re-routing always agree.
- **One run for many questions.** Roads are two-way, so one Dijkstra run from the emergency site gives the travel time from every hub and vehicle to the site (`shortest_path_tree`). The matcher uses one run from the site plus one per hub with stock, instead of one per (hub, vehicle) pair.
- **"Route unavailable" is explicit.** `RouteResult.found=False` comes with a reason:
  - destination closed
  - closures cut it off
  - graph genuinely disconnected (checked with a BFS that ignores closures)

  The dispatcher treats these differently. Closures mean **Delayed** and retry. A disconnected site means immediate **Failed**.
- **Alternatives.** `alternative_routes` blocks each edge of the best route in turn and re-runs Dijkstra (a simplified version of Yen's k-shortest-paths). This costs one Dijkstra per edge on the path. Routes are compared by their *edges*, so two parallel roads between the same intersections count as two routes. Limitation: it can miss routes that differ from the best one in two or more edges.
- **Re-routing only when needed.** On `road-closed`, only trips whose *remaining* edges or nodes are closed are re-routed. A closure elsewhere changes nothing; a test checks this.

## 3. Priority queue ([core/priority_queue.py](core/priority_queue.py), [core/scoring.py](core/scoring.py))
**Score (0-100).** `100 × Σ wᵢ·fᵢ / Σ wᵢ`, where every factor fᵢ is between 0 and 1. All weights live in config:

| Factor | Weight | Formula | Why |
|---|---|---|---|
| severity | 30 | (sev-1)/4 | biggest driver: how bad the situation is |
| people | 20 | min(people/500, 1) | more people means more harm |
| deadline | 20 | 1 - slack/240, where slack = deadline - now - travel - load - unload | includes travel and handling time, so far sites start earlier; an unreachable site counts as 60 min away (the travel cap), not 0 |
| shortage | 10 | min(unmet units/200, 1) | how much food is still missing |
| location | 10 | hospital 1.0 > senior home 0.9 > shelter 0.85 > school 0.75 > community 0.6 > other 0.5 | vulnerable populations first |
| travel | 10 | 1 - travel/60 | a quick win helps people sooner; an unreachable site gets 0 |

The breakdown is stored on each emergency and shown in `queue` and `show`, so we can explain any ranking.

**Aging.** priority = score + 0.3 × minutes waited, with no cap.
- Because the bonus grows without limit, starvation is impossible.
- Worst case, a score-0 request overtakes a brand-new score-100 request after 100/0.3 ≈ 333 minutes.
- In practice, a score-20 request overtakes a fresh score-80 request after 200 minutes.
- A test checks this bound.

**Tie-break for equal priority.** Heap entries are `[-priority, submitted_at, -people, natural id key, id, active]`. Python compares lists left to right, so one comparison gives this order:
1. higher priority
2. earlier submission
3. more people
4. id in natural order (E2 before E10)

Priorities are rounded to 0.01 first, so floating-point noise does not hide a real tie.

**Dynamic updates.** Every tick re-scores all waiting requests, since aging and deadlines change continuously, and rebuilds the heap with `heapify` in O(n). The loop reads `queue.ids()`, so there is no sorting. Single changes use lazy deletion: mark the old entry dead and push a new one, O(log n).
- `priority-changed` is published when a priority moves by at least 5 points since the last announcement, or by any amount after a condition change: closure, breakdown, restock, or a hub running out (`resource-depleted`).
- A partial assignment announces its own drop ("remaining need changed after a partial assignment"), so every event's "old" value is the one listeners last saw.

**Score cache.** Travel time to the nearest stocked hub is cached per site until `graph.version` changes.

## 4. Resource matching: greedy ([core/matcher.py](core/matcher.py))
- **Greedy in two places.**
  1. Emergencies are popped in priority order, so the most urgent gets first pick.
  2. For each emergency we repeatedly choose the single best (source, vehicle) pair, load as much as fits, and repeat. For normal food, "best" means **earliest delivery time**. For cold food, it means **the most cold units among on-time options**, then earliest time (see below).
- **Constraints.**
  - stock > 0
  - vehicle idle
  - cold food only in refrigerated vehicles
  - load ≤ capacity
  - vehicle → hub → site route open
  - fuel ≥ trip km × fuel.per_km (optional)
- **Never assigned twice.** Stock is subtracted and the vehicle marked busy at the moment of assignment, which is a reservation. The tests check after every simulated minute that no vehicle has two active trips, that stock never goes negative, and that `unassigned + in transit + delivered == requested` for every emergency.
- **Order of work.**
  - **Cold first.** It is scarcer and needs reefers. Reefers are scarce too, so each reefer trip should carry as much cold food as possible: a nearby hub with 5 cold units loses to a farther one with 40 if that one still arrives on time. A reefer then fills its *spare* space with normal food. Cold is loaded first, so this never takes the place of cold food.
  - **Normal next.** Non-refrigerated trucks are preferred so reefers stay free for cold requests.
  - **Stranded cargo first.** Food from a broken-down truck is collected before any new hub stock is used.
- **Fairness cap.** At most 3 vehicles per emergency per cycle (config), so one big request cannot take the whole fleet in a single minute. The rest is retried in the next cycle.
- **Partial and multi-trip fulfilment.** Whatever cannot be covered stays unassigned, the emergency stays in the queue, and the reason is recorded. A vehicle stays where it delivered and can be re-used; its next route starts from there.
- **Failure reasons** are written in plain words, for example:
  - "No cold stock left in any hub"
  - "No refrigerated vehicle available (2 busy, 1 broken)"
  - "Route unavailable: road closures cut the site off from every supply hub"
  - "Idle refrigerated vehicles are on a part of the road network with no road at all to any hub with cold stock" (this stays Delayed, not Failed, because a restock or repair can still fix it)
  - "V3 skipped: needs 12.0 fuel..."
- **Complexity per emergency.** One Dijkstra from the site, plus one per reachable source that has stock of the needed type (1 + H_stocked runs, each cached for the call), plus O(H·K) to compare pairs for each trip created.
- **Trade-off.** Greedy is not globally optimal. An assignment solver (Hungarian or min-cost flow) could reduce total travel, but greedy is:
  - O(H·K) per decision and easy to explain
  - naturally priority-aware
  - quick to recompute after every disruption

  For emergency response, predictability and explainability matter more than the last few percent of efficiency.

## 5. State machine ([core/state_machine.py](core/state_machine.py))
| From | Allowed to |
|---|---|
| Open | Prioritized, Failed |
| Prioritized | Resource Assigned, Delayed, Failed |
| Resource Assigned | In Progress, Re-Routing, Delayed |
| In Progress | Resource Assigned (extra vehicle for the remainder), Re-Routing, Delayed, Delivered |
| Re-Routing | In Progress, Delayed |
| Delayed | Resource Assigned, In Progress, Re-Routing, Delivered, Failed |
| Delivered | Resolved |
| Resolved, Failed | (final) |

- **Errors.** Anything else raises `InvalidTransitionError`. An empty explanation raises `ValueError`.
- **Every change is recorded.** Each change is a `StateChange(time, from, to, explanation)` in the emergency's history and is published as `state-changed`. Information that does not change the state, such as a partial delivery, is stored as a *note*.
- **One rule decides the state** for multi-vehicle emergencies (`Dispatcher.settle_state`):
  - **Delayed** if a vehicle is blocked, cargo is stranded, a breakdown just happened, or nothing is moving while food is unassigned
  - otherwise **In Progress** if any vehicle is moving
- **Re-Routing** is entered only by the road-closure handler, and only for emergencies whose active route became unavailable.
- **Delivered → Resolved** happens after a confirmation delay (5 min); `crisis-resolved` is published then.
- **Failed** in two cases:
  - the site is permanently unreachable (disconnected graph)
  - deadline + 30 min grace has passed with no vehicle moving and food still missing

  The explanation gives units delivered and the last reason.

## 6. Disruptions
- **Road or intersection closed.** Affected trips are re-routed from the last node reached.
  - A loaded vehicle with no route **waits** (Blocked → emergency Delayed) and continues when a road reopens.
  - A vehicle that has not picked up yet has its assignment cancelled; the stock goes back to the hub and the request is re-matched, possibly from another hub.
- **Vehicle breakdown before pickup.** The reservation is released and another vehicle is matched.
- **Vehicle breakdown after pickup.** The food becomes `StrandedCargo` at the breakdown node. The matcher treats it as a tiny hub reserved for that emergency, so another vehicle (a reefer if the food is cold) collects the remaining cargo. Hub stock is not used twice; a test checks this.
- **Re-routing while loading.** If a vehicle is still loading at the hub when its route closes, the new plan keeps the loading time that is left (no fresh 5 minutes).
- **Not enough fuel for a detour.** Fuel is checked *before* a detour is driven, so it can never go negative (the tests check this every minute).
  - A loaded vehicle that cannot afford the detour stops, and its food becomes stranded cargo for another vehicle.
  - An empty one has its assignment cancelled.
- **Stranded cargo bookkeeping.** An emptied pile is removed once the rescue trip delivers. If a request fails, only cargo that was really never collected is reported. Food on a stuck rescue vehicle is written off, not put back into a pile nobody will collect.
- **Restock.** Increases hub stock and triggers re-scoring. Waiting requests are matched in the next cycle.

## 7. Robustness (hidden-test categories)
| Case | Handling |
|---|---|
| Limited stock | partial fulfilment, `resource-depleted`, then Delayed, then Failed with units delivered |
| Capacity limits | split over several vehicles and trips |
| Closures | re-route; explicit route unavailable; Delayed until reopened |
| Simultaneous emergencies | heap order plus tie-break; most urgent gets first pick; aging keeps the rest moving |
| Breakdowns | cargo reassigned (stranded-cargo pickup) or stock returned |
| Unreachable destination | temporary: Delayed, then Failed after the grace period; disconnected: Failed at once |
| Invalid inputs | every problem listed in one message; the record is rejected and the rest continue. An unknown location gets the closest matches (`suggest_locations`: part of a name, words in any order, or a typo via `difflib`), and the CLI form asks again instead of rejecting |
| Empty dataset | loads with warnings; requests are rejected with a clear reason; no crash |
| Disconnected graph | BFS distinguishes "closed" from "never connected" |
| Unknown keys / missing fields | ignored with a warning / defaults from config; `_`-prefixed keys are comments; `null` stock means 0; `"trucks": null` falls back to `"vehicles"` |
| Other input shapes | the abstract's single `food_quantity` + `refrigeration_required` (all cold if refrigerated, else all normal); `number_of_people_affected`; times as `HH:MM`, `HH:MM:SS`, `HH:MM+1d` or ISO; files with a byte-order mark |
| Bad numbers in config | `--config` values that would divide by zero or loop forever (e.g. `step_minutes: 0`) are refused with a clear message |

## 8. Assumptions (say these to judges)
1. `w` is travel time in minutes and is used as given. The graph is undirected (two-way roads), as stated for Dataset 1.
2. One trip serves one emergency. There are no multi-stop tours; this is a trade-off for simplicity and explainability.
3. A vehicle's position is the last node it reached. Re-routing starts there; mid-road positions are not modelled.
4. Loading takes 5 min and unloading 5 min (config). A vehicle stays at the delivery site afterwards.
5. Fuel is optional. It is reserved for the planned km, checked before any detour, refunded when a trip is cancelled or stopped, and there is no refuelling in this draft.
7. Terrain speed factors: built-in defaults, then the dataset's `terrain` section (`multiplier` = cost factor, `speed_factor` = speed, so cost is 1/speed), then any `--config` values. An explicit config always wins.
6. Units: one stock unit uses one capacity unit. Multipliers in config convert other units.

## 9. Complexity summary
| Operation | Cost |
|---|---|
| Dijkstra (one route or one full tree) | O((V+E) log V) |
| Queue push / pop / update | O(log n); remove O(1) |
| Re-score all + rebuild heap each tick | O(n) scoring (cached travel) + O(n) heapify |
| Match one emergency | O((1+S)·(V+E) log V + T·H·K), where S = reachable sources with stock and T = trips created |
| Closure handling | O(active trips × route length) check + one Dijkstra per affected trip |

## 10. Live web dashboard ([ui/web.py](ui/web.py))
- **Starting it:** `python3 main.py --demo --web` (or any `--dataset`), then open http://localhost:8765.
- **What it shows:** an SVG map drawn from the dataset's lat/lng, or a circle layout if coordinates are missing. It shows closures, hubs with stock, vehicles and their remaining routes, the priority queue, emergencies and the live event log.
- **Controls:** buttons tick or run the scenario, and clicking a road closes or reopens it.
- **Standard library only:** `http.server` with `ThreadingHTTPServer`.
  - Live updates use **Server-Sent Events** (`/events`), not WebSocket. The standard library has no WebSocket server, and the page only needs server-to-browser updates. Button clicks are plain POST requests.
  - Each event carries its bus sequence number, so a reconnecting browser resumes exactly where it stopped.
- **Thread safety:** each browser connection runs in its own thread, but the system is not thread-safe. Every access holds one lock (a `threading.Condition`), and the event streams sleep on that Condition until something changes.

## 11. City conditions: weather, rush hour, traffic ([core/system.py](core/system.py), [loader/weather_loader.py](loader/weather_loader.py))
- **One formula.** road cost = `w × terrain × (weather × rush hour) × local slowdown`.
  - The weather and rush-hour product is `Graph.condition_factor`; local jams are in `Graph.edge_slowdowns`.
  - Every change bumps `graph.version`, so cached travel times are recomputed.
- **Where the numbers come from.**
  - Weather factors (snow ×1.4, heavy snow ×1.7, ...) and rush hours are config values and **team assumptions**, not measurements.
  - The weather itself is real: **Open-Meteo** (free, no API key, past and forecast hourly data by lat/lng). Each WMO weather code maps to one of our condition names.
- **Why a saved snapshot and not a live call during a run.**
  1. Repeatable: the same file gives the same result in tests and in front of the judges.
  2. Works without internet.
  3. No API key, so no secret in a public repo.

  `--fetch-weather DATE` downloads once and saves; `--weather FILE` replays. Hours before the scenario start fold into one event at the start, and after that only *changes* become events.
- **Why there is no live traffic API.** There is no free traffic data feed that works without an account and API key (TomTom, HERE and Google all need a key). It would also make runs non-repeatable. Traffic is therefore modelled as configurable rush hours plus `traffic` incident events. Swapping in a real feed later only means producing those same `traffic` events.
- **Effect on decisions.**
  - The matcher's ETAs, Dijkstra route choices for new trips, and the deadline-slack part of the score all use current costs, so priorities and "on time / LATE" predictions react to the weather.
  - Vehicles already driving get their remaining arrival times recomputed (`Trip.retime`): `Trip.edge_costs` stores each leg's planned/retimed cost so the remaining fraction of the current road stays exact across any number of speed changes, later roads use new costs, and loading time is kept. A `route-updated` event reports the new ETA.
- **No Re-Routing for slowdowns.** A slow road is still usable, so the route is kept and the state does not change; Re-Routing stays reserved for a route that becomes unavailable. A jam can be made impassable with a road closure.
- **HTTPS on macOS.** If Python cannot find certificates (common with python.org installs), the download retries with the operating system's certificate bundle. Verification is never switched off.

## 12. What we would do with more time
- Divert vehicles that are already driving around a new jam when it saves enough time (today only new trips avoid it).
- Multi-stop tours, optimal assignment (min-cost flow) as an option, refuelling, and vehicles returning to base.
- A* with a calibrated heuristic for much larger cities.
