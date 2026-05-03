from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.itinerary import SlotPeriod


class DisruptionType(str, Enum):
    VENUE_CLOSED = "venue_closed"
    WEATHER = "weather"
    HEALTH = "health"
    TRANSIT_DELAY = "transit_delay"
    GROUP_PREFERENCE_SHIFT = "group_preference_shift"
    BUDGET_CHANGE = "budget_change"
    SAFETY = "safety"
    OPPORTUNITY = "opportunity"


class DisruptionEvent(BaseModel):
    day_number: int
    period: str = Field(default="", description="Affected period: morning, afternoon, evening, or empty for whole day.")
    description: str = Field(description="Free text: e.g. 'Borghese Gallery closed', 'sick day', 'flight delayed 4 hours'")
    disruption_type: str = Field(default="", description="One of: venue_closed, weather, health, transit_delay, group_preference_shift, budget_change, safety, opportunity.")


class DeltaSlot(BaseModel):
    """A slot entry in an ItineraryDelta — includes day_number for lock validation."""
    day_number: int = Field(description="Day of the trip (1-indexed).")
    period: str = Field(description="One of: morning, afternoon, evening.")
    place_name: str
    place_id: str = ""
    category: str = ""
    address: str = ""
    notes: str = ""
    cost_usd: float = 0.0
    booking_url: str = ""
    duration_minutes: int = 0


class DailyCost(BaseModel):
    day: int = Field(description="Day number (1-indexed).")
    cost_usd: float = Field(description="Total cost for this day in USD per person.")


class AffectedSlot(BaseModel):
    """One slot the user wants changed — extracted from free text by DisruptionParser."""
    day_number: int = Field(description="Day of the trip (1-indexed).")
    period: str = Field(description="One of: morning, afternoon, evening. Empty string means whole day.")
    venue_name: str = Field(description="Name of the venue being disrupted/removed, as mentioned by the user. Empty if not specified.")
    category: str = Field(description="One of: activity, dining, lodging. Infer from context.")


class DisruptionRequest(BaseModel):
    """Structured output from DisruptionParser — the only LLM step in the replan pipeline."""
    disruption_type: DisruptionType = Field(description="Type of disruption.")
    affected_slots: list[AffectedSlot] = Field(description="All slots that need to change. Must have at least one entry.")
    locked_slot_keys: list[str] = Field(default_factory=list, description="Slots that must NOT be touched, e.g. ['day1_morning', 'day2_evening'].")
    new_budget_per_day: Optional[float] = Field(default=None, description="New daily budget in USD, only set for budget_change disruptions.")
    special_instructions: str = Field(default="", description="Free-text constraints from the user: 'indoor only', 'max 0.5km walking', 'vegetarian', etc.")
    reasoning: str = Field(description="One sentence: what happened and what the system should do.")


class ItineraryDelta(BaseModel):
    disruption: DisruptionEvent
    affected_days: list[int]
    changed_slots: list[DeltaSlot]
    removed_slots: list[DeltaSlot]
    reasoning: str = Field(description="2-3 sentences explaining what changed and why.")
    new_daily_costs: list[DailyCost] = Field(description="Updated daily costs for affected days only.")
