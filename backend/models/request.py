from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ClientProfile(BaseModel):
    name: str = ""
    budget_per_day_usd: int = Field(description="Hard daily budget cap in USD including meals, activities, and transport.")
    interests: list[str] = Field(default=[], description="e.g. ['ancient history', 'food', 'art']")
    dietary_restrictions: list[str] = Field(default=[], description="e.g. ['vegetarian', 'gluten-free']")
    mobility_notes: str = Field(default="", description="e.g. 'uses a cane, no more than 2km walking per day'")
    travel_style: Literal["budget", "mid-range", "luxury"] = "mid-range"
    group_size: int = 2
    group_type: Literal["solo", "couple", "family", "friends"] = "couple"


class CityLeg(BaseModel):
    city: str = Field(description="City name, e.g. 'Lisbon'")
    country: str = Field(description="Country name, e.g. 'Portugal'")
    nights: int = Field(description="Number of nights to spend in this city", ge=1)


class TripRequest(BaseModel):
    destination_city: str = Field(description="Primary destination city (first city for multi-city trips).")
    destination_country: str = Field(description="Primary destination country.")
    destinations: list[CityLeg] = Field(
        default=[],
        description=(
            "Ordered list of city legs for multi-city trips. "
            "For a single-city trip leave empty; destination_city/country are used instead. "
            "For multi-city trips populate this list; destination_city must equal destinations[0].city. "
            "The sum of all nights across destinations MUST equal duration_days exactly."
        ),
    )
    start_date: date | None = None
    duration_days: int
    client: ClientProfile
    must_include: list[str] = Field(default=[], description="Place names that MUST appear in the itinerary.")
    must_exclude: list[str] = Field(default=[], description="Places or categories to skip entirely.")
    lodging_preference: Literal["hotel", "bnb", "hostel", "any"] = "any"
    notes: str = ""

    @model_validator(mode="after")
    def _sync_and_fix_destinations(self) -> "TripRequest":
        """Ensure destinations is always populated and nights sum equals duration_days."""
        if self.destinations and not self.destination_city:
            self.destination_city = self.destinations[0].city
            self.destination_country = self.destinations[0].country

        elif not self.destinations and self.destination_city:
            self.destinations = [CityLeg(
                city=self.destination_city,
                country=self.destination_country,
                nights=self.duration_days,
            )]

        # Hard correction: if LLM nights don't sum to duration_days, rescale proportionally.
        if self.destinations:
            total = sum(leg.nights for leg in self.destinations)
            if total != self.duration_days:
                target = self.duration_days
                # Distribute target nights proportionally, ensuring every city gets >= 1.
                weights = [leg.nights for leg in self.destinations]
                scaled = [max(1, round(w * target / total)) for w in weights]
                # Fix any rounding drift by adjusting the largest city.
                diff = target - sum(scaled)
                if diff != 0:
                    largest = scaled.index(max(scaled))
                    scaled[largest] += diff
                for leg, n in zip(self.destinations, scaled):
                    leg.nights = n

        return self
