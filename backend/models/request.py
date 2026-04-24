from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ClientProfile(BaseModel):
    name: str = ""
    budget_per_day_usd: int = Field(description="Hard daily budget cap in USD including meals, activities, and transport.")
    interests: list[str] = Field(default=[], description="e.g. ['ancient history', 'food', 'art']")
    dietary_restrictions: list[str] = Field(default=[], description="e.g. ['vegetarian', 'gluten-free']")
    mobility_notes: str = Field(default="", description="e.g. 'uses a cane, no more than 2km walking per day'")
    travel_style: Literal["budget", "mid-range", "luxury"] = "mid-range"
    group_size: int = 2
    group_type: Literal["solo", "couple", "family", "friends"] = "couple"


class TripRequest(BaseModel):
    destination_city: str
    destination_country: str
    start_date: date | None = None
    duration_days: int
    client: ClientProfile
    must_include: list[str] = Field(default=[], description="Place names that MUST appear in the itinerary.")
    must_exclude: list[str] = Field(default=[], description="Places or categories to skip entirely.")
    lodging_preference: Literal["hotel", "bnb", "hostel", "any"] = "any"
    notes: str = ""
