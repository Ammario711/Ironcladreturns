"""Scenario / disruption simulator: replays a timeline against the system.

Fixed time step (default 1 simulated minute). At each step t:
  1. move the clock to t (deliveries that are due are recorded)
  2. apply disruptions scheduled at or before t   (world changes first)
  3. submit emergencies scheduled at or before t   (in file order)
  4. system.tick(t)                                (re-score, dispatch, fail)
It stops when no emergency is still scheduled, every emergency is
Resolved/Failed and no vehicle is moving, or after simulation.max_minutes.

Nothing random happens here, so the same inputs give the same event log.
"""

from core.utils import fmt_time, parse_time


class Simulator:
    def __init__(self, system, step_minutes=None):
        self.system = system
        self.step = step_minutes or system.config["simulation"]["step_minutes"]
        self.max_minutes = system.config["simulation"]["max_minutes"]
        self._pending = []     # [time, order, seq, kind, raw]; order 0 = disruption, 1 = emergency
        self._seq = 0
        self._undated = []     # (order, kind, raw) with no usable time; see _place_undated
        self.input_errors = [] # (kind, raw, [messages]) for the report
        self.started = False

    # ------------------------------------------------------------ scheduling
    def _time_of(self, raw, keys):
        """Scheduled time of a raw record, or None if it has no usable time."""
        if isinstance(raw, dict):
            for key in keys:
                if raw.get(key) is not None:
                    try:
                        return parse_time(raw[key])
                    except ValueError:
                        return None  # validation will report the bad time
        return None

    def schedule_emergency(self, raw):
        t = self._time_of(raw, ("submitted_at", "submission_time", "submitted", "time"))
        self._push(t, 1, "emergency", raw)

    def schedule_disruption(self, raw):
        self._push(self._time_of(raw, ("time", "at")), 0, "disruption", raw)

    def _push(self, t, order, kind, raw):
        if t is None:
            if not self.started:
                # No time given: it happens when the scenario STARTS (the earliest
                # timed input), not at 00:00. Placed in _place_undated().
                self._undated.append((order, kind, raw))
                return
            t = self.system.now  # during a run: right now
        self._seq += 1
        self._pending.append([max(t, self.system.now), order, self._seq, kind, raw])
        self._pending.sort(key=lambda item: item[:3])

    def _place_undated(self):
        if self._undated:
            start = min((item[0] for item in self._pending), default=self.system.now)
            undated, self._undated = self._undated, []
            for order, kind, raw in undated:
                self._push(start, order, kind, raw)

    def pending(self):
        undated = [(None, kind, raw) for _order, kind, raw in self._undated]
        return [(item[0], item[3], item[4]) for item in self._pending] + undated

    # ------------------------------------------------------------ running
    def start_time(self):
        """Where the clock starts: jump to the first scheduled input, but only
        if nothing has happened yet (so a request typed in at 00:00 is not skipped)."""
        if not self.started:
            self._place_undated()
            nothing_yet = self.system.now == 0 and not any(
                ev.type != "conditions-changed" for ev in self.system.bus.log)
            if self._pending and nothing_yet:
                return float(int(self._pending[0][0]))
        return self.system.now

    def step_to(self, t):
        """Process everything scheduled up to t, then tick the system at t."""
        if not self.started:
            self._place_undated()
        self.started = True
        s = self.system
        s.set_clock(t)
        while self._pending and self._pending[0][0] <= t:
            _time, _order, _seq, kind, raw = self._pending.pop(0)
            if kind == "disruption":
                ok, message = s.apply_disruption(raw)
                if not ok:
                    self.input_errors.append(("disruption", raw, [message]))
            else:
                result = s.submit_emergency(raw)
                if not result.ok:
                    self.input_errors.append(("emergency", raw, result.errors))
        s.tick(t)

    def finished(self):
        """Nothing left that can change the outcome: no emergency still to come,
        every emergency Resolved/Failed, and no vehicle moving. Scheduled
        disruptions AFTER that (e.g. evening weather changes) do not keep the
        simulation running."""
        s = self.system
        emergencies_to_come = any(item[3] == "emergency" for item in self._pending) or any(
            kind == "emergency" for _order, kind, _raw in self._undated)
        return not emergencies_to_come and s.all_done() and not s.dispatcher.active_trips()

    def run(self, until=None):
        """Run until finished (or until clock time `until`). Returns the end time.

        Never re-ticks the current minute when already running, and never
        steps past `until`.
        """
        if self.started and self.finished():
            return self.system.now
        t = self.start_time() if not self.started else self.system.now + self.step
        first = t
        while until is None or t <= until + 1e-9:
            self.step_to(t)
            if self.finished() or t - first >= self.max_minutes:
                break
            t += self.step
        return self.system.now

    def advance(self, minutes):
        """Move forward `minutes` of simulated time (used by the CLI and web)."""
        t = self.start_time() if not self.started else self.system.now
        end = t + minutes
        if not self.started:
            self.step_to(t)
        while t < end - 1e-9:
            t = min(t + self.step, end)
            self.step_to(t)
        return self.system.now

    # ------------------------------------------------------------ report
    def report(self):
        s = self.system
        st = s.stats()
        lines = [f"=== Scenario report at {st['time']} ({s.dataset.name}) ==="]
        bad_disruptions = [item for item in self.input_errors if item[0] == "disruption"]
        # system.rejected holds EVERY rejected request (files, CLI one-liners, generator)
        lines.append(f"Emergencies: {st['emergencies']} accepted, {len(s.rejected)} rejected requests, "
                     f"{len(bad_disruptions)} rejected disruptions")
        lines.append("Final states: " + (", ".join(f"{k} {v}" for k, v in sorted(st["by_state"].items())) or "none"))
        lines.append(f"Fully delivered: {st['fully_delivered']} ({st['on_time']} on time); "
                     f"avg request-to-delivery {_or_na(st['avg_response_minutes'], ' min')}")
        lines.append(f"Food delivered: {st['units_delivered']}/{st['units_requested']} units; "
                     f"people fully served: {st['people_fully_served']}")
        lines.append(f"Stock left: {st['stock_left_normal']} normal / {st['stock_left_cold']} cold; "
                     f"vehicles broken: {st['vehicles_broken']}; events published: {st['events']}")
        lines.append("")
        lines.append(f"{'ID':<6}{'State':<18}{'Delivered':>12}  {'Deadline':<9}{'Done at':<9}Last explanation")
        for e in s._sorted_emergencies():
            done = fmt_time(e.delivered_at) if e.delivered_at is not None else "-"
            last = e.history[-1].explanation if e.history else ""
            lines.append(f"{e.id:<6}{e.state.value:<18}{e.delivered_total():>5}/{e.requested_total():<6}  "
                         f"{fmt_time(e.deadline):<9}{done:<9}{last[:90]}")
        for raw, errors in s.rejected:
            ident = raw.get("id", "?") if isinstance(raw, dict) else "?"
            lines.append(f"REJECTED emergency {ident}: " + "; ".join(errors))
        for _kind, raw, errors in bad_disruptions:
            lines.append(f"REJECTED disruption {raw.get('type', '?') if isinstance(raw, dict) else '?'}: "
                         + "; ".join(errors))
        return "\n".join(lines)


def _or_na(value, unit=""):
    """Readable value for the report: None (nothing to average yet) -> N/A."""
    return "N/A" if value is None else f"{value}{unit}"
