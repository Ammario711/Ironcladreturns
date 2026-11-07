<!-- AI-GENERATED DRAFT - team to review and modify -->
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
  - Payloads are plain dicts, ready to send as JSON to a future WebSocket dashboard.

## 2. Routing: Dijkstra with `heapq` ([core/routing.py](core/routing.py))
- **Cost.** Edge cost is `w × terrain factor × highway factor`. All factors default to 1.0, so the dataset's `w` (travel minutes) is used unchanged. This avoids counting terrain twice if `w` already includes it. `km` is only used for fuel.
- **Why not A\*.** A\* needs an admissible heuristic. With time-based costs and terrain multipliers, a straight-line km heuristic is only admissible if we know the fastest possible speed. Dijkstra is always correct, and at this graph size it costs well under a millisecond.
- **Complexity.** O((V + E) log V) per run.
  - A heap entry is `(distance, counter, node)`. The counter makes ties deterministic and avoids comparing an int id with a str id.
  - Stale heap entries are skipped (lazy deletion).
  - `find_route` stops as soon as the target is settled.
- **Closures.** Closed edges and nodes are kept in two sets. `Graph.open_neighbors()` skips them, so every route request automatically avoids them. Closing or reopening is O(1). A `version` counter lets the score cache know when travel times are stale.
- **One run for many questions.** Roads are two-way, so one Dijkstra run from the emergency site gives the travel time from every hub and vehicle to the site (`shortest_path_tree`). The matcher uses one run from the site plus one per hub with stock, instead of one per (hub, vehicle) pair.
- **"Route unavailable" is explicit.** `RouteResult.found=False` comes with a reason:
  - destination closed
  - closures cut it off
  - graph genuinely disconnected (checked with a BFS that ignores closures)

  The dispatcher treats these differently. Closures mean **Delayed** and retry. A disconnected site means immediate **Failed**.
- **Alternatives.** `alternative_routes` blocks each edge of the best route in turn and re-runs Dijkstra (a simplified version of Yen's k-shortest-paths). This costs one Dijkstra per edge on the path. Limitation: it can miss routes that differ from the best one in two or more edges.
- **Re-routing only when needed.** On `road-closed`, only trips whose *remaining* edges or nodes are closed are re-routed. A closure elsewhere changes nothing; a test checks this.

## 3. Priority queue ([core/priority_queue.py](core/priority_queue.py), [core/scoring.py](core/scoring.py))
**Score (0-100).** `100 × Σ wᵢ·fᵢ / Σ wᵢ`, where every factor fᵢ is between 0 and 1. All weights live in config:

| Factor | Weight | Formula | Why |
|---|---|---|---|
| severity | 30 | (sev-1)/4 | biggest driver: how bad the situation is |
| people | 20 | min(people/500, 1) | more people means more harm |
| deadline | 20 | 1 - slack/240, where slack = deadline - now - travel | includes travel time, so far sites start earlier |
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

**Dynamic updates.** Every tick re-scores all waiting requests, since aging and deadlines change continuously, and rebuilds the heap with `heapify` in O(n). Single changes use lazy deletion: mark the old entry dead and push a new one, O(log n). `priority-changed` is published when a priority moves by at least 5 points, or by any amount after a condition change (closure, breakdown, restock).

**Score cache.** Travel time to the nearest stocked hub is cached per site until `graph.version` changes.

## 4. Resource matching: greedy ([core/matcher.py](core/matcher.py))
- **Greedy in two places.**
  1. Emergencies are popped in priority order, so the most urgent gets first pick.
  2. For each emergency we repeatedly choose the single best (source, vehicle) pair by **earliest delivery time**, load as much as fits, and repeat.
- **Constraints.**
  - stock > 0
  - vehicle idle
  - cold food only in refrigerated vehicles
  - load ≤ capacity
  - vehicle → hub → site route open
  - fuel ≥ trip km × fuel.per_km (optional)
- **Never assigned twice.** Stock is subtracted and the vehicle marked busy at the moment of assignment, which is a reservation. The tests check after every simulated minute that no vehicle has two active trips, that stock never goes negative, and that `unassigned + in transit + delivered == requested` for every emergency.
- **Order of work.**
  - **Cold first.** It is scarcer and needs reefers, and a reefer fills spare space with normal food.
  - **Normal next.** Non-refrigerated trucks are preferred so reefers stay free for cold requests.
  - **Stranded cargo first.** Food from a broken-down truck is collected before any new hub stock is used.
- **Fairness cap.** At most 3 vehicles per emergency per cycle (config), so one big request cannot take the whole fleet in a single minute. The rest is retried in the next cycle.
- **Partial and multi-trip fulfilment.** Whatever cannot be covered stays unassigned, the emergency stays in the queue, and the reason is recorded. A vehicle stays where it delivered and can be re-used; its next route starts from there.
- **Failure reasons** are written in plain words, for example:
  - "No cold stock left in any hub"
  - "No refrigerated vehicle available (2 busy, 1 broken)"
  - "Route unavailable: road closures cut the site off from every supply hub"
  - "V3 skipped: needs 12.0 fuel..."
- **Complexity per emergency.** One Dijkstra from the site, plus one per source used, plus O(H·K) to compare pairs for each trip created.
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
| Unknown keys / missing fields | ignored with a warning / defaults from config; `_`-prefixed keys are comments |

## 8. Assumptions (say these to judges)
1. `w` is travel time in minutes and is used as given. The graph is undirected (two-way roads), as stated for Dataset 1.
2. One trip serves one emergency. There are no multi-stop tours; this is a trade-off for simplicity and explainability.
3. A vehicle's position is the last node it reached. Re-routing starts there; mid-road positions are not modelled.
4. Loading takes 5 min and unloading 5 min (config). A vehicle stays at the delivery site afterwards.
5. Fuel is optional. It is deducted for planned km, refunded when a trip is cancelled, and there is no refuelling in this draft.
6. Units: one stock unit uses one capacity unit. Multipliers in config convert other units.

## 9. Complexity summary
| Operation | Cost |
|---|---|
| Dijkstra (one route or one full tree) | O((V+E) log V) |
| Queue push / pop / update | O(log n); remove O(1) |
| Re-score all + rebuild heap each tick | O(n) scoring (cached travel) + O(n) heapify |
| Match one emergency | O((1+S)·(V+E) log V + T·H·K), where S = sources used and T = trips created |
| Closure handling | O(active trips × route length) check + one Dijkstra per affected trip |

## 10. What we would do with more time
- A WebSocket live map dashboard (the event log is already JSON-ready).
- Multi-stop tours, optimal assignment (min-cost flow) as an option, refuelling, and vehicles returning to base.
- A* with a calibrated heuristic for much larger cities.
