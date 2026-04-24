from __future__ import annotations

from dataclasses import dataclass, field

from backend.models.disruption import DisruptionEvent
from backend.models.itinerary import Itinerary, SlotPeriod
from backend.models.places import ActivityOption, DiningOption, LodgingOption
from backend.models.request import TripRequest


SlotKey = tuple[int, SlotPeriod]  # (day_number, period)


@dataclass
class CandidatePool:
    """All options fetched this session — re-plan reuses these to avoid redundant API calls."""
    lodging: list[LodgingOption] = field(default_factory=list)
    activities: list[ActivityOption] = field(default_factory=list)
    dining: list[DiningOption] = field(default_factory=list)


@dataclass
class AppContext:
    session_id: str
    trip_request: TripRequest | None = None
    itinerary: Itinerary | None = None
    locked_slots: set[SlotKey] = field(default_factory=set)
    disruptions: list[DisruptionEvent] = field(default_factory=list)
    candidate_pool: CandidatePool = field(default_factory=CandidatePool)
    advisor_notes: str = ""

    _store: dict[str, "AppContext"] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def get_or_create(cls, session_id: str, _store: dict[str, "AppContext"] = {}) -> "AppContext":
        if session_id not in _store:
            _store[session_id] = cls(session_id=session_id)
        return _store[session_id]
