from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.models.itinerary import Slot, SlotPeriod


class DisruptionEvent(BaseModel):
    day_number: int
    period: SlotPeriod | None = Field(default=None, description="None means the disruption affects the whole day.")
    description: str = Field(description="Free text: e.g. 'Borghese Gallery closed', 'sick day', 'flight delayed 4 hours'")
    reported_at: datetime = Field(default_factory=datetime.utcnow)


class ItineraryDelta(BaseModel):
    disruption: DisruptionEvent
    affected_days: list[int]
    changed_slots: list[Slot]
    removed_slots: list[Slot]
    reasoning: str = Field(description="2-3 sentences explaining what changed and why.")
    new_daily_costs: dict[int, float]
