from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LatLng(BaseModel):
    lat: float
    lng: float


class OpeningHours(BaseModel):
    open_now: bool | None = None
    weekday_text: list[str] = Field(default=[], description="Human-readable hours per day, e.g. 'Monday: 9:00 AM - 6:00 PM'")


class PlaceResult(BaseModel):
    place_id: str
    name: str
    address: str
    lat: float
    lng: float
    rating: float | None = None
    user_ratings_total: int | None = None
    price_level: int | None = Field(default=None, description="0=free, 1=$, 2=$$, 3=$$$, 4=$$$$")
    opening_hours: OpeningHours | None = None
    photo_urls: list[str] = []
    website: str | None = None
    phone: str | None = None
    editorial_summary: str | None = None
    category: Literal["lodging", "activity", "restaurant", "attraction", "other"] = "other"


class LodgingOption(BaseModel):
    place: PlaceResult
    estimated_cost_per_night_usd: float
    notes: str = ""


class ActivityOption(BaseModel):
    place: PlaceResult
    estimated_duration_minutes: int
    estimated_cost_usd: float
    time_of_day_suitability: list[Literal["morning", "afternoon", "evening"]] = ["morning", "afternoon"]
    booking_required: bool = False
    booking_url: str | None = None
    accessibility_notes: str = ""


class DiningOption(BaseModel):
    place: PlaceResult
    estimated_cost_per_person_usd: float
    cuisine_tags: list[str] = []
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] = "lunch"


class RouteResult(BaseModel):
    origin: LatLng
    destination: LatLng
    duration_minutes: int
    distance_km: float
    mode: Literal["transit", "walking", "driving"] = "transit"
    summary: str = ""


class DirectionsResult(BaseModel):
    steps: list[str]
    total_duration_minutes: int
    total_distance_km: float
    mode: Literal["transit", "walking", "driving"] = "transit"
