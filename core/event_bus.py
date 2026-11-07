"""Publish/subscribe event bus.

Components never call each other directly to announce news. They publish
an event; whoever cares subscribes. Who subscribes to what:

  publisher                      event                 subscribers
  ----------------------------   -------------------   ---------------------------------
  system.submit_emergency        crisis-created        system (score + queue)
  system.refresh_priorities      priority-changed      CLI / log
  dispatcher, matcher            route-updated         CLI / log
  matcher                        resource-depleted     system (re-score)
  system.apply_disruption        road-closed           dispatcher (re-route), system (re-score)
  system.apply_disruption        road-reopened         dispatcher (un-block), system (re-score)
  system.apply_disruption        vehicle-breakdown     dispatcher (rescue cargo), system
  system.apply_disruption        vehicle-repaired      system (re-score)
  system.apply_disruption        resource-restocked    system (re-score)
  state machine                  state-changed         CLI / log
  state machine                  crisis-resolved       CLI / log
  system (weather/traffic/rush)  conditions-changed    dispatcher (update ETAs), system (re-score)

The first seven are the required events; the last five are extras.

Delivery is synchronous and in subscription order, so runs are fully
deterministic. Every event is kept in `log` (the CLI "events" command and
any future WebSocket dashboard read from it). Payloads are plain dicts so
they can be sent as JSON later.
"""

from dataclasses import dataclass

REQUIRED_EVENTS = (
    "crisis-created", "priority-changed", "route-updated", "resource-depleted",
    "road-closed", "vehicle-breakdown", "crisis-resolved",
)
EXTRA_EVENTS = ("state-changed", "road-reopened", "vehicle-repaired", "resource-restocked",
                "conditions-changed")
EVENT_TYPES = REQUIRED_EVENTS + EXTRA_EVENTS
WILDCARD = "*"  # subscribe to everything (loggers, dashboards)


@dataclass(frozen=True)
class Event:
    seq: int          # 1, 2, 3... publication order
    time: float       # simulation clock (minutes)
    type: str
    payload: dict


class EventBus:
    def __init__(self, raise_handler_errors=True):
        self._subscribers = {}   # event type -> [handler, ...]
        self.log = []            # every event ever published
        self.handler_errors = [] # (event, handler name, error text) when not raising
        # True while developing/testing (bugs surface immediately);
        # the CLI sets False so one failing subscriber cannot stop the system.
        self.raise_handler_errors = raise_handler_errors

    def subscribe(self, event_type, handler):
        if event_type != WILDCARD and event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}; expected one of {', '.join(EVENT_TYPES)}")
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type, handler):
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event_type, payload, time):
        """Record the event, then call wildcard subscribers, then specific ones.

        Wildcard subscribers (loggers, the CLI, a dashboard) run FIRST so they
        see events in publication order, even when a specific handler
        publishes follow-up events while it runs (e.g. road-closed ->
        state-changed to Re-Routing).
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}")
        event = Event(len(self.log) + 1, time, event_type, dict(payload))
        self.log.append(event)
        # Copy the lists so a handler may (un)subscribe while we loop.
        handlers = list(self._subscribers.get(WILDCARD, [])) + list(self._subscribers.get(event_type, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # fault isolation for live use
                if self.raise_handler_errors:
                    raise
                name = getattr(handler, "__qualname__", repr(handler))
                self.handler_errors.append((event, name, repr(exc)))
        return event

    def events_of(self, event_type):
        return [e for e in self.log if e.type == event_type]
