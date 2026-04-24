from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend.models.places import LodgingOption
from backend.models.request import TripRequest


class SlotPeriod(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class Slot(BaseModel):
    period: SlotPeriod
    place_id: str
    place_name: str
    category: Literal["activity", "dining", "transit", "lodging", "free"]
    address: str
    lat: float
    lng: float
    duration_minutes: int
    cost_usd: float
    notes: str = ""
    booking_url: str | None = None
    locked: bool = Field(default=False, description="Advisor-locked slots are never touched by the re-planner.")


class DayPlan(BaseModel):
    day_number: int
    date: Optional[date] = None
    slots: list[Slot]
    daily_cost_usd: float
    lodging: LodgingOption
    transit_summary: str = ""


class Itinerary(BaseModel):
    trip_id: str
    request: TripRequest
    days: list[DayPlan]
    total_cost_usd: float
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1
