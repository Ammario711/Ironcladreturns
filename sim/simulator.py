# AI-GENERATED DRAFT - team to review and modify
# Drafted with Claude (Anthropic, model claude-opus-5-5) on 2026-11-07. Cite in README.
"""Scenario / disruption simulator: replays a timeline against the system.

Fixed time step (default 1 simulated minute). At each step t:
  1. move the clock to t (deliveries that are due are recorded)
  2. apply disruptions scheduled at or before t   (world changes first)
  3. submit emergencies scheduled at or before t   (in file order)
  4. system.tick(t)                                (re-score, dispatch, fail)
It stops when nothing is scheduled, every emergency is Resolved/Failed and
no vehicle is moving, or after simulation.max_minutes.

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
        self.input_errors = [] # (kind, raw, [messages]) for the report
        self.started = False

    # ------------------------------------------------------------ scheduling
    def _time_of(self, raw, keys):
        """Scheduled time of a raw record; unparsable/missing -> now (validation will report it)."""
        if isinstance(raw, dict):
            for key in keys:
                if raw.get(key) is not None:
                    try:
                        return parse_time(raw[key])
                    except ValueError:
                        break
        return self.system.now

    def schedule_emergency(self, raw):
        t = self._time_of(raw, ("submitted_at", "submission_time", "submitted", "time"))
        self._push(t, 1, "emergency", raw)

    def schedule_disruption(self, raw):
        self._push(self._time_of(raw, ("time", "at")), 0, "disruption", raw)

    def _push(self, t, order, kind, raw):
        self._seq += 1
        self._pending.append([max(t, self.system.now), order, self._seq, kind, raw])
        self._pending.sort(key=lambda item: item[:3])

    def pending(self):
        return [(item[0], item[3], item[4]) for item in self._pending]

    # ------------------------------------------------------------ running
    def start_time(self):
        if not self.started and self._pending and self.system.now == 0:
            return float(int(self._pending[0][0]))
        return self.system.now

    def step_to(self, t):
        """Process everything scheduled up to t, then tick the system at t."""
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
        s = self.system
        return not self._pending and s.all_done() and not s.dispatcher.active_trips()

    def run(self, until=None):
        """Run until finished (or until clock time `until`). Returns end time."""
        t = self.start_time()
        first = t
        while True:
            self.step_to(t)
            if until is not None and t >= until:
                break
            if self.finished() or t - first >= self.max_minutes:
                break
            t += self.step
        return t

    def advance(self, minutes):
        """Move forward `minutes` of simulated time (used by the CLI)."""
        t = self.start_time() if not self.started else self.system.now
        end = t + minutes
        if not self.started:
            self.step_to(t)
        while t + self.step <= end + 1e-9:
            t += self.step
            self.step_to(t)
        return t

    # ------------------------------------------------------------ report
    def report(self):
        s = self.system
        st = s.stats()
        lines = [f"=== Scenario report at {st['time']} ({s.dataset.name}) ==="]
        lines.append(f"Emergencies: {st['emergencies']} accepted, {len(self.input_errors)} rejected inputs")
        lines.append("Final states: " + ", ".join(f"{k} {v}" for k, v in sorted(st["by_state"].items())))
        lines.append(f"Fully delivered: {st['fully_delivered']} ({st['on_time']} on time); "
                     f"avg request-to-delivery {st['avg_response_minutes']} min")
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
        for kind, raw, errors in self.input_errors:
            ident = raw.get("id", "?") if isinstance(raw, dict) else "?"
            lines.append(f"REJECTED {kind} {ident}: " + "; ".join(errors))
        return "\n".join(lines)
