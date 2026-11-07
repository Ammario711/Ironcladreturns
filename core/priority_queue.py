# AI-assisted (Claude Opus 5.5, Google Antigravity); reviewed and modified by Team Ironcladreturns — see README.md.
"""Dynamic max-priority queue for emergencies, built on heapq.

heapq is a MIN-heap, so we store the priority negated. Each heap entry is

    [-priority, submitted_at, -people, id_sort_key(id), id, active]

Python compares lists element by element, so this ONE ordering gives the
documented tie-break for equal priority:
    1. higher priority first
    2. earlier submission first
    3. more people affected first
    4. id in natural order last ("E2" before "E10"), so the order is
       always deterministic
Priorities are rounded (config queue.round_digits) before storing, so tiny
floating-point differences count as a real tie.

Changing a priority: we do not search the heap (O(n)). We mark the old
entry inactive and push a new one ("lazy deletion", the pattern from the
Python heapq docs). Inactive entries are skipped when popped. So the heap
list may briefly hold dead entries; `_entries` always holds exactly one
LIVE entry per waiting emergency, and len() counts only those.
    push / update / pop : O(log n)      remove : O(1)
    rebuild (all priorities change, e.g. aging each tick) : O(n) heapify
"""

import heapq

from core.utils import id_sort_key


class CrisisQueue:
    def __init__(self, round_digits=2):
        self._heap = []
        self._entries = {}  # id -> its current (active) heap entry
        self._round = round_digits

    def _make_entry(self, emergency_id, priority, submitted_at, people):
        return [-round(priority, self._round), submitted_at, -people, id_sort_key(emergency_id), emergency_id, True]

    def push(self, emergency_id, priority, submitted_at, people):
        """Add an emergency, or update it if it is already queued."""
        if emergency_id in self._entries:
            self.remove(emergency_id)
        entry = self._make_entry(emergency_id, priority, submitted_at, people)
        self._entries[emergency_id] = entry
        heapq.heappush(self._heap, entry)

    # update is the same operation as push; the name reads better at call sites
    update = push

    def remove(self, emergency_id):
        """Lazy delete: mark inactive, it will be skipped by pop()."""
        entry = self._entries.pop(emergency_id, None)
        if entry is not None:
            entry[-1] = False

    def pop(self):
        """Remove and return the id with the highest priority (None if empty)."""
        while self._heap:
            entry = heapq.heappop(self._heap)
            if entry[-1]:
                del self._entries[entry[4]]
                return entry[4]
        return None

    def peek(self):
        while self._heap and not self._heap[0][-1]:
            heapq.heappop(self._heap)  # throw away dead entries at the top
        return self._heap[0][4] if self._heap else None

    def rebuild(self, items):
        """Replace everything at once. items: iterable of (id, priority, submitted_at, people).

        Used every clock tick, when aging changes every priority anyway.
        heapify is O(n), cheaper than n separate updates.
        """
        self._entries = {}
        self._heap = []
        for emergency_id, priority, submitted_at, people in items:
            entry = self._make_entry(emergency_id, priority, submitted_at, people)
            self._entries[emergency_id] = entry
            self._heap.append(entry)
        heapq.heapify(self._heap)

    def priority_of(self, emergency_id):
        entry = self._entries.get(emergency_id)
        return None if entry is None else -entry[0]

    def ids(self):
        """Ids of everything waiting, in no particular order. O(n), no sorting."""
        return list(self._entries)

    def ordered(self):
        """[(id, priority)] in pop order, WITHOUT changing the queue (for display)."""
        active = sorted(self._entries.values())
        return [(entry[4], -entry[0]) for entry in active]

    def __contains__(self, emergency_id):
        return emergency_id in self._entries

    def __len__(self):
        return len(self._entries)
