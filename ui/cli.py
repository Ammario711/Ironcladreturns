# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""Interactive command-line dashboard (Python's built-in `cmd` module).

Type `help` inside the program for the command list; every command's help
text is its docstring. The CLI only calls public methods of the system and
subscribes to the event bus to print live updates, so a web dashboard
could later replace it without touching /core.
"""

import cmd
import shlex

from core.emergency import resolve_location, suggest_locations, validate_emergency
from core.routing import alternative_routes
from core.utils import fmt_time, id_sort_key, normalize_id, parse_time, to_number
from loader.input_loader import InputFileError, load_disruptions_file, load_emergencies_file


# ------------------------------------------------------------------ formatting
def table(headers, rows):
    """Plain-text table with columns sized to fit."""
    rows = [[str(c) for c in row] for row in rows]
    widths = [max(len(str(h)), *(len(r[i]) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    out = [line, "-" * len(line)]
    out += ["  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in rows]
    return "\n".join(out)


def format_event(event):
    """One readable line per event (used by live mode and `events`)."""
    p = event.payload
    if event.type == "state-changed":
        text = f"{p['id']}: {p['from']} -> {p['to']}: {p['explanation']}"
    elif event.type == "crisis-created":
        text = p["summary"] + (f"  [warnings: {'; '.join(p['warnings'])}]" if p.get("warnings") else "")
    elif event.type == "crisis-resolved":
        text = f"{p['id']} resolved ({p['delivered_normal']} normal + {p['delivered_cold']} cold delivered)"
    else:
        text = p.get("message", str(p))
    return f"{fmt_time(event.time)}  {event.type:<18} {text}"


def parse_key_values(text):
    """'location=P3 people=40' -> {'location': 'P3', 'people': '40'}"""
    result = {}
    for token in shlex.split(text):
        if "=" not in token:
            raise ValueError(f"expected key=value, got {token!r}")
        key, value = token.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def location_from_text(text):
    """Typed location -> value the validator understands.

    "43.65, -79.38" -> {"lat": ..., "lng": ...};  "33" -> 33;  "P3" -> "P3"
    """
    if "," in text:
        parts = text.split(",")
        if len(parts) == 2:
            try:
                to_number(parts[0])
                to_number(parts[1])
                return {"lat": parts[0].strip(), "lng": parts[1].strip()}
            except ValueError:
                pass
    try:
        return normalize_id(text)
    except ValueError:
        return text


MAX_PLACES_LISTED = 25  # bigger datasets: type part of a name to search instead

ADD_FIELDS = [  # (field, prompt) for the interactive `add` form; location is asked separately
    ("crisis_type", "Crisis type (supply_shortage, flood, fire, power_outage, ...)"),
    ("location_category", "Location category (hospital, shelter, community, school, senior_home, other)"),
    ("people_affected", "People affected"),
    ("food_normal", "Normal food units needed"),
    ("food_cold", "Cold food units needed"),
    ("refrigeration_required", "Refrigeration required (yes/no, blank = from cold units)"),
    ("severity", "Severity 1-5"),
    ("deadline", "Delivery deadline (HH:MM)"),
    ("submitted_at", "Submission time (HH:MM, blank = now)"),
]


class CrisisCLI(cmd.Cmd):
    prompt = "crisis> "

    def __init__(self, system, simulator, live=True):
        super().__init__()
        self.system = system
        self.sim = simulator
        self.live = live
        # Show the scenario's start time from the beginning (not 00:00).
        if not simulator.started:
            system.set_clock(simulator.start_time())
        system.bus.subscribe("*", self._print_live)
        self.intro = (f"\nUrban Food Crisis Response - {system.dataset.name}\n"
                      f"Clock {fmt_time(self.sim.start_time())}. {len(self.sim.pending())} scheduled inputs. "
                      f"Type 'help' for commands, 'tick 10' to advance 10 minutes, 'run' to finish.\n")

    def _print_live(self, event):
        if self.live:
            print("  " + format_event(event))

    def emptyline(self):
        pass  # pressing Enter does nothing (default would repeat the last command)

    def default(self, line):
        if line == "EOF":  # Ctrl-D / closed stdin ends cmdloop without adding an 'EOF' command
            print()
            return True
        print(f"Unknown command: {line!r}. Type 'help'.")

    # ------------------------------------------------------------ views
    def do_status(self, _arg):
        """status : clock, emergencies per state, fleet and stock summary"""
        st = self.system.stats()
        print(f"Clock {st['time']} | emergencies {st['emergencies']} {st['by_state']} | "
              f"queue {len(self.system.queue)} | vehicles busy {st['vehicles_busy']}, broken {st['vehicles_broken']} | "
              f"stock {st['stock_left_normal']} normal / {st['stock_left_cold']} cold | "
              f"scheduled inputs {len(self.sim.pending())}")
        closed = sorted(self.system.graph.closed_edges, key=id_sort_key)
        nodes = sorted(self.system.graph.closed_nodes, key=id_sort_key)
        if closed or nodes:
            print(f"Closed roads: {closed}  Closed intersections: {nodes}")
        c = self.system.conditions()
        slow = ", ".join(f"{e} x{f:g}" for e, f in sorted(c["slow_roads"].items(), key=lambda i: id_sort_key(i[0])))
        print(f"Conditions: weather {c['weather']} x{c['weather_factor']:g}, rush hour x{c['rush_factor']:g} "
              f"-> all roads x{c['factor']:.2f}" + (f"; slow roads: {slow}" if slow else ""))

    def do_queue(self, _arg):
        """queue : waiting emergencies in the order they will be served, with score breakdown"""
        rows = []
        for position, (eid, priority) in enumerate(self.system.queue.ordered(), start=1):
            e = self.system.emergencies[eid]
            parts = " ".join(f"{k[:4]}={v['points']:.0f}" for k, v in e.score_breakdown.items())
            rows.append([position, eid, f"{priority:.1f}", f"{e.base_score:.1f}", f"{e.aging_bonus:.1f}",
                         e.state.value, f"{e.unassigned_normal}N/{e.unassigned_cold}C",
                         fmt_time(e.deadline), parts])
        if not rows:
            print("Queue is empty (nothing waiting for resources).")
            return
        print(table(["#", "ID", "Priority", "Score", "Aging", "State", "Unassigned", "Deadline", "Breakdown"], rows))
        print("Tie-break for equal priority: earlier submission, then more people, then id.")

    def do_list(self, _arg):
        """list : every emergency with state and delivery progress"""
        rows = [[e.id, e.state.value, f"{e.priority:.1f}", e.location_label[:32], e.category, e.severity,
                 f"{e.delivered_total()}/{e.requested_total()}", fmt_time(e.deadline)]
                for e in self.system._sorted_emergencies()]
        print(table(["ID", "State", "Prio", "Location", "Category", "Sev", "Delivered", "Deadline"], rows)
              if rows else "No emergencies yet. Use 'add' or 'tick'.")

    def do_show(self, arg):
        """show ID : full details of one emergency, its trips and its timestamped history"""
        e = self.system.emergencies.get(arg.strip())
        if e is None:
            print(f"No emergency {arg.strip()!r}. Try 'list'.")
            return
        print(e.short())
        print(f"State {e.state.value}; priority {e.priority:.1f} = score {e.base_score:.1f} + aging {e.aging_bonus:.1f}")
        for name, info in e.score_breakdown.items():
            print(f"   {name:<9} factor {info['factor']:.2f} -> {info['points']:.1f} pts")
        print(f"Delivered {e.delivered_normal}N/{e.delivered_cold}C of {e.need_normal}N/{e.need_cold}C; "
              f"unassigned {e.unassigned_normal}N/{e.unassigned_cold}C")
        for trip in self.system.dispatcher.trips_for(e.id, active_only=False):
            print(f"   trip {trip.id} [{trip.status}] {trip.summary()}  path {trip.nodes}")
        print("History:")
        for change in e.history:
            print("   " + str(change))

    def do_trips(self, _arg):
        """trips : vehicles currently on the road (assignments, routes, ETAs)"""
        rows = []
        now = self.system.now
        for t in self.system.dispatcher.active_trips():
            rows.append([t.id, t.vehicle_id, t.emergency_id, t.status, f"{t.normal}N/{t.cold}C", t.source_id,
                         "loaded" if t.is_loaded(now) else "to pickup", t.current_node(now),
                         fmt_time(t.delivery_time) if t.status == "en_route" else "-", t.nodes[t.position_index(now):]])
        print(table(["Trip", "Vehicle", "Emerg", "Status", "Load", "From", "Phase", "At", "ETA", "Remaining path"], rows)
              if rows else "No vehicles on the road.")

    def do_vehicles(self, _arg):
        """vehicles : the whole fleet"""
        rows = [[v.id, v.name, v.status, "yes" if v.refrigerated else "no", v.capacity, f"{v.fuel:.1f}",
                 self.system.graph.node_label(v.location), v.trip_id or ""]
                for v in sorted(self.system.vehicles.values(), key=lambda v: id_sort_key(v.id))]
        print(table(["ID", "Name", "Status", "Reefer", "Cap", "Fuel", "Location", "Trip"], rows))

    def do_hubs(self, _arg):
        """hubs : stock left at every supply hub"""
        rows = [[h.id, h.name, h.node, f"{h.normal}/{h.initial_normal}", f"{h.cold}/{h.initial_cold}"]
                for h in sorted(self.system.hubs.values(), key=lambda h: id_sort_key(h.id))]
        print(table(["Hub", "Name", "Node", "Normal left/start", "Cold left/start"], rows))

    def do_route(self, arg):
        """route FROM TO : fastest route + alternatives (place ids or node ids), e.g. route P0 P4"""
        parts = arg.split()
        if len(parts) != 2:
            print("Usage: route FROM TO   (e.g. route P0 P4  or  route 33 22)")
            return
        g = self.system.graph
        nodes = []
        for part in parts:
            node, _poi, problem = resolve_location(part, g, self.system.pois)
            if problem:
                print(problem)
                return
            nodes.append(node)
        for i, route in enumerate(alternative_routes(g, nodes[0], nodes[1], k=3)):
            label = "Best" if i == 0 else f"Alt {i}"
            print(f"{label}: {route.describe(g)}")
            if route.found:
                print(f"      nodes {route.path}")

    def do_events(self, arg):
        """events [N] [TYPE] : last N events (default 20), optionally only one type, e.g. events 10 road-closed"""
        count, kind = 20, None
        for part in arg.split():
            if part.lstrip("-").isdigit():
                count = int(part)
            else:
                kind = part
        if count < 1:  # log[-0:] would print the WHOLE log
            print("Usage: events [N] [TYPE]  (N must be 1 or more)")
            return
        log = [ev for ev in self.system.bus.log if kind is None or ev.type == kind]
        for event in log[-count:]:
            print(format_event(event))
        if not log:
            print("No events yet.")

    def do_report(self, _arg):
        """report : scenario summary (same as the end of --run)"""
        print(self.sim.report())

    # ------------------------------------------------------------ input
    def do_add(self, arg):
        """add : enter a new emergency (interactive form)
add key=value ... : one line, e.g. add location=P3 crisis_type=flood location_category=hospital people_affected=80 food_normal=40 severity=4 deadline=09:30"""
        if arg.strip():
            try:
                raw = parse_key_values(arg)
            except ValueError as exc:
                print(exc)
                return
        else:
            try:
                location = self._ask_location()  # keeps asking until valid (or 'cancel')
                if location is None:
                    print("Cancelled; nothing was added.")
                    return
                raw = {"location": location}
                for field, question in ADD_FIELDS:
                    answer = input(f"  {question}: ").strip()
                    if answer:
                        raw[field] = answer
            except (EOFError, KeyboardInterrupt):  # Ctrl-D / Ctrl-C in the middle of the form
                print("\nCancelled; nothing was added.")
                return
        if isinstance(raw.get("location"), str):
            raw["location"] = location_from_text(raw["location"])  # also turns "43.65,-79.38" into lat/lng
        if self._schedule_if_future(raw):
            return
        result = self.system.submit_emergency(raw)
        if result.ok:
            print(f"Accepted {result.emergency.id}." + (f" Warnings: {'; '.join(result.warnings)}" if result.warnings else ""))
            print("It will be dispatched on the next 'tick'.")
        else:
            print("Rejected. Please fix:")
            for error in result.errors:
                print(f"  - {error}")

    def _schedule_if_future(self, raw):
        """A request "submitted" later than the clock is scheduled for that time,
        not dispatched early. It is checked now so mistakes show immediately.
        Returns True if it was handled here (scheduled or rejected)."""
        s = self.system
        try:
            submitted = parse_time(raw["submitted_at"]) if raw.get("submitted_at") else None
        except ValueError:
            return False  # submit_emergency will report the bad time
        if submitted is None or submitted <= s.now:
            return False
        check = validate_emergency(raw, s.graph, s.pois, s.config, existing_ids=s.emergencies.keys(), now=submitted)
        if not check.ok:
            print("Rejected. Please fix:")
            for error in check.errors:
                print(f"  - {error}")
            return True
        self.sim.schedule_emergency(raw)
        print(f"Scheduled: it will be submitted at {fmt_time(submitted)} (the clock is {fmt_time(s.now)}).")
        return True

    def _ask_location(self):
        """Ask for the location until it is valid, helping when it is not.

        Unknown text (e.g. "Toronto") gets the closest matches; 'y' accepts
        the first one. '?' (or a blank answer) lists the places. 'cancel' stops.
        Returns the location value, or None if the user cancelled.
        """
        g, pois = self.system.graph, self.system.pois
        best = None  # top suggestion from the last failed attempt, used by 'y'
        while True:
            answer = input("  Location (place id like P3, place name, node id, or lat,lng; '?' lists places): ").strip()
            if answer.lower() == "cancel":
                return None
            if answer.lower() in ("y", "yes") and best is not None:
                answer = str(best)
            if answer in ("", "?") or answer.lower() == "list":
                if answer == "":
                    print("    A location is required.")
                self._print_places()
                continue
            value = location_from_text(answer)
            node, poi, _problem = resolve_location(value, g, pois)
            if node is not None:
                where = f"{poi.id} {poi.name}" if poi else g.node_label(node)
                print(f"    Using {where} (node {node}).")
                return value
            print(f"    {answer!r} is not a known place, intersection, or lat,lng.")
            hints = suggest_locations(answer, g, pois)
            if hints:
                best = hints[0][0]
                print("    Did you mean:")
                for hint_value, label, kind in hints:
                    print(f"      {str(hint_value):<5} {label}  [{kind}]")
                print(f"    Type y to use {best}, type another location, '?' to list places, or 'cancel'.")
            else:
                best = None
                print("    No close match. These are the places in this dataset:")
                self._print_places()

    def _print_places(self):
        """List the dataset's named places (delivery sites first, hubs last)."""
        pois = sorted(self.system.pois.values(), key=lambda p: (p.kind == "hub", id_sort_key(p.id)))
        for poi in pois[:MAX_PLACES_LISTED]:
            print(f"      {str(poi.id):<5} {poi.name}  [{poi.kind}]")
        if len(pois) > MAX_PLACES_LISTED:
            print(f"      ... and {len(pois) - MAX_PLACES_LISTED} more: type part of a name to search")
        if not pois:
            print("      (this dataset has no named places; use a node id or lat,lng)")
        print("    You can also type any intersection node id (see 'route') or lat,lng.")

    def _disrupt(self, raw):
        ok, message = self.system.apply_disruption(raw)
        print(("OK: " if ok else "") + message)

    def _road_target(self, arg):
        parts = arg.split()
        if len(parts) == 1:
            return {"edge": parts[0]}
        if len(parts) == 2:
            return {"a": parts[0], "b": parts[1]}
        return None

    def do_close(self, arg):
        """close EDGE | close A B : close a road by edge id or by its two end nodes"""
        target = self._road_target(arg)
        self._disrupt({"type": "road_closure", **target, "reason": "manual"} if target else {"type": "road_closure"})

    def do_reopen(self, arg):
        """reopen EDGE | reopen A B : reopen a road"""
        target = self._road_target(arg)
        self._disrupt({"type": "road_reopen", **target, "reason": "manual"} if target else {"type": "road_reopen"})

    def do_closenode(self, arg):
        """closenode NODE : close an intersection"""
        self._disrupt({"type": "node_closure", "node": arg.strip(), "reason": "manual"})

    def do_reopennode(self, arg):
        """reopennode NODE : reopen an intersection"""
        self._disrupt({"type": "node_reopen", "node": arg.strip(), "reason": "manual"})

    def do_breakdown(self, arg):
        """breakdown VEHICLE : vehicle breaks down (its cargo is reassigned)"""
        self._disrupt({"type": "vehicle_breakdown", "vehicle": arg.strip(), "reason": "manual"})

    def do_repair(self, arg):
        """repair VEHICLE : vehicle back in service"""
        self._disrupt({"type": "vehicle_repair", "vehicle": arg.strip(), "reason": "manual"})

    def do_restock(self, arg):
        """restock HUB NORMAL COLD : add stock to a hub, e.g. restock P1 50 20"""
        parts = arg.split()
        if len(parts) != 3:
            print("Usage: restock HUB NORMAL COLD")
            return
        self._disrupt({"type": "restock", "hub": parts[0], "normal": parts[1], "cold": parts[2], "reason": "manual"})

    def do_weather(self, arg):
        """weather CONDITION : set the weather, e.g. weather heavy_snow (see 'status' for the current one)"""
        if not arg.strip():
            names = ", ".join(self.system.config["conditions"]["weather_factors"])
            print(f"Usage: weather CONDITION   ({names})")
            return
        self._disrupt({"type": "weather", "condition": arg.strip(), "reason": "manual"})

    def do_traffic(self, arg):
        """traffic EDGE FACTOR | traffic A B FACTOR : slow a road (2 = twice as slow, 1 = cleared)"""
        parts = arg.split()
        if len(parts) not in (2, 3):
            print("Usage: traffic EDGE FACTOR   or   traffic A B FACTOR   (factor 1 clears it)")
            return
        target = self._road_target(" ".join(parts[:-1]))
        self._disrupt({"type": "traffic", **target, "factor": parts[-1], "reason": "manual"})

    def do_load(self, arg):
        """load emergencies PATH | load disruptions PATH : schedule records from a JSON file"""
        parts = arg.split(maxsplit=1)
        if len(parts) != 2 or parts[0] not in ("emergencies", "disruptions"):
            print("Usage: load emergencies PATH   or   load disruptions PATH")
            return
        try:
            if parts[0] == "emergencies":
                records = load_emergencies_file(parts[1])
                for raw in records:
                    self.sim.schedule_emergency(raw)
            else:
                records = load_disruptions_file(parts[1])
                for raw in records:
                    self.sim.schedule_disruption(raw)
        except InputFileError as exc:
            print(exc)
            return
        print(f"Scheduled {len(records)} {parts[0]}.")

    # ------------------------------------------------------------ time
    def do_tick(self, arg):
        """tick [MINUTES] : advance the clock (default 1 minute); scheduled inputs fire on time"""
        try:
            minutes = float(arg) if arg.strip() else 1.0
        except ValueError:
            print("Usage: tick [MINUTES]")
            return
        limit = self.sim.max_minutes
        if not (0 < minutes <= limit):  # also rejects nan and inf, which would loop forever
            print(f"MINUTES must be more than 0 and at most {limit:g}")
            return
        end = self.sim.advance(minutes)
        print(f"Clock is now {fmt_time(end)}.")

    def do_run(self, arg):
        """run [HH:MM] : run until everything is finished (or until a clock time)"""
        until = None
        if arg.strip():
            try:
                until = parse_time(arg.strip())
            except ValueError as exc:
                print(exc)
                return
        end = self.sim.run(until=until)
        print(f"Clock is now {fmt_time(end)}.")
        if until is None:
            print(self.sim.report())

    def do_live(self, arg):
        """live on|off : print events as they happen"""
        self.live = arg.strip().lower() != "off"
        print(f"Live events {'on' if self.live else 'off'}.")

    def do_quit(self, _arg):
        """quit : leave the program"""
        return True
