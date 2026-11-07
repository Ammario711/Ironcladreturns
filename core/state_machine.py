# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""Crisis state machine: validated transitions, each logged with a
timestamp and an explanation.

States (exact names required by the problem statement):
  Open, Prioritized, Resource Assigned, In Progress, Re-Routing,
  Delivered, Resolved, Delayed, Failed

Normal path:
  Open -> Prioritized -> Resource Assigned -> In Progress -> Delivered -> Resolved

When things go wrong:
  * Re-Routing ONLY when a road/node on an active vehicle's remaining route
    closes (dispatcher.on_road_closed is the only caller). Afterwards:
    back to In Progress (alternative found) or Delayed (no route).
  * Delayed = cannot move forward right now (no stock, no vehicle, no route,
    breakdown). Leaves Delayed when resources are assigned again, the
    vehicle can move again, or the request finally fails.
  * Failed = given up (deadline + grace passed with nothing moving, or the
    site is permanently unreachable). Resolved and Failed are final.
  * In Progress -> Resource Assigned is allowed for partial fulfilment
    (an extra vehicle is added for the remaining food).

Any other transition raises InvalidTransitionError, so a bug can never
silently put an emergency into an impossible state.
"""

from dataclasses import dataclass
from enum import Enum

from core.utils import fmt_time


class CrisisState(str, Enum):
    OPEN = "Open"
    PRIORITIZED = "Prioritized"
    RESOURCE_ASSIGNED = "Resource Assigned"
    IN_PROGRESS = "In Progress"
    REROUTING = "Re-Routing"
    DELIVERED = "Delivered"
    RESOLVED = "Resolved"
    DELAYED = "Delayed"
    FAILED = "Failed"


S = CrisisState  # short alias for the table below

# from-state -> set of allowed to-states
ALLOWED_TRANSITIONS = {
    S.OPEN: {S.PRIORITIZED, S.FAILED},
    S.PRIORITIZED: {S.RESOURCE_ASSIGNED, S.DELAYED, S.FAILED},
    S.RESOURCE_ASSIGNED: {S.IN_PROGRESS, S.REROUTING, S.DELAYED},
    S.IN_PROGRESS: {S.RESOURCE_ASSIGNED, S.REROUTING, S.DELAYED, S.DELIVERED},
    S.REROUTING: {S.IN_PROGRESS, S.DELAYED},
    S.DELAYED: {S.RESOURCE_ASSIGNED, S.IN_PROGRESS, S.REROUTING, S.DELIVERED, S.FAILED},
    S.DELIVERED: {S.RESOLVED},
    S.RESOLVED: set(),
    S.FAILED: set(),
}

TERMINAL_STATES = {S.RESOLVED, S.FAILED}


class InvalidTransitionError(Exception):
    """Raised when code tries a transition that is not in the table."""


@dataclass
class StateChange:
    """One line of an emergency's history."""
    time: float
    from_state: CrisisState | None   # None for the very first entry
    to_state: CrisisState
    explanation: str
    is_note: bool = False            # True = information only, state unchanged

    def __str__(self):
        if self.is_note:
            return f"[{fmt_time(self.time)}] (note, {self.to_state.value}) {self.explanation}"
        start = self.from_state.value if self.from_state else "-"
        return f"[{fmt_time(self.time)}] {start} -> {self.to_state.value}: {self.explanation}"


class CrisisStateMachine:
    """Applies transitions and announces them on the event bus."""

    def __init__(self, bus):
        self.bus = bus

    def start(self, emergency, time, explanation):
        """First history entry: the emergency exists and is Open."""
        emergency.state = S.OPEN
        emergency.history.append(StateChange(time, None, S.OPEN, explanation))

    def can_transition(self, emergency, new_state):
        return new_state in ALLOWED_TRANSITIONS[emergency.state]

    def transition(self, emergency, new_state, time, explanation):
        """Move to new_state or raise InvalidTransitionError. Always logs."""
        old_state = emergency.state
        if new_state not in ALLOWED_TRANSITIONS[old_state]:
            raise InvalidTransitionError(
                f"{emergency.id}: cannot go from {old_state.value} to {new_state.value}")
        if not explanation or not explanation.strip():
            raise ValueError("every state change needs an explanation")
        emergency.state = new_state
        change = StateChange(time, old_state, new_state, explanation)
        emergency.history.append(change)
        self.bus.publish("state-changed", {
            "id": emergency.id, "from": old_state.value, "to": new_state.value,
            "explanation": explanation,
        }, time)
        if new_state == S.RESOLVED:
            self.bus.publish("crisis-resolved", {
                "id": emergency.id, "delivered_normal": emergency.delivered_normal,
                "delivered_cold": emergency.delivered_cold, "explanation": explanation,
            }, time)
        return change

    def note(self, emergency, time, explanation):
        """Record information that does not change the state (e.g. partial delivery)."""
        emergency.history.append(StateChange(time, emergency.state, emergency.state, explanation, is_note=True))
