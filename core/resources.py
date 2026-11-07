"""Supply-side data: hubs (food stock), vehicles, points of interest, and
cargo left behind by a broken-down vehicle.

These are plain dataclasses. The rules for using them live in matcher.py
and dispatcher.py.
"""

from dataclasses import dataclass


@dataclass
class Poi:
    """Named place on the map (hub, hospital, shelter, community...)."""
    id: object
    name: str
    kind: str
    node: object
    lat: float | None = None
    lng: float | None = None
    address: str = ""


@dataclass
class Hub:
    """A supply hub. `normal` and `cold` are the units still available.

    Stock is taken out the moment a trip is assigned (a reservation), so the
    same units can never be promised to two emergencies.
    """
    id: object
    name: str
    node: object
    normal: int
    cold: int
    initial_normal: int = 0
    initial_cold: int = 0


# Vehicle status values (plain strings keep printing/JSON simple)
IDLE = "idle"       # free to be assigned
BUSY = "busy"       # currently on a trip
BROKEN = "broken"   # out of service until repaired


@dataclass
class Vehicle:
    id: object
    name: str
    home: object            # starting node
    location: object        # node where the vehicle is now (updated after trips)
    refrigerated: bool
    capacity: int           # max units per trip (normal + cold together)
    fuel: float
    max_fuel: float
    status: str = IDLE
    trip_id: object = None  # trip it is driving, if BUSY


@dataclass
class StrandedCargo:
    """Food sitting in a broken-down vehicle, waiting for another vehicle.

    The matcher treats it like a tiny hub that only serves its own
    emergency, so the remaining cargo is reassigned instead of lost.
    """
    id: str
    emergency_id: object
    node: object
    normal: int
    cold: int
    from_vehicle: object

    def units(self):
        return self.normal + self.cold
