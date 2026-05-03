"""
Tests for Pydantic models — validation, defaults, and field constraints.
No LLM calls, no API keys required.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from backend.models.request import TripRequest, CityLeg, ClientProfile
from backend.models.disruption import DisruptionEvent, ItineraryDelta, DeltaSlot, DailyCost


def _client(**kw) -> ClientProfile:
    return ClientProfile(budget_per_day_usd=200, **kw)


# ── TripRequest ───────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestTripRequest:
    def _base(self, **overrides) -> dict:
        base = {
            "destination_city": "Rome",
            "destination_country": "Italy",
            "duration_days": 5,
            "client": _client(),
        }
        base.update(overrides)
        return base

    def test_minimal_valid(self):
        req = TripRequest(**self._base())
        assert req.destination_city == "Rome"
        assert req.duration_days == 5
        assert req.client.budget_per_day_usd == 200

    def test_defaults(self):
        req = TripRequest(**self._base())
        assert req.client.group_size == 2
        assert req.client.travel_style == "mid-range"
        assert req.start_date is None

    def test_start_date_parsed(self):
        req = TripRequest(**self._base(start_date=date(2026, 6, 15)))
        assert req.start_date == date(2026, 6, 15)

    def test_must_include_list(self):
        req = TripRequest(**self._base(must_include=["Vatican", "Colosseum"]))
        assert "Vatican" in req.must_include
        assert "Colosseum" in req.must_include

    def test_multi_city_destinations(self):
        req = TripRequest(**self._base(
            destinations=[
                CityLeg(city="Lisbon", country="Portugal", nights=4),
                CityLeg(city="Porto", country="Portugal", nights=3),
            ],
            duration_days=7,
        ))
        assert len(req.destinations) == 2
        assert req.destinations[0].city == "Lisbon"

    def test_city_leg_nights_positive(self):
        with pytest.raises(ValidationError):
            CityLeg(city="Rome", country="Italy", nights=0)

    def test_solo_group_type(self):
        req = TripRequest(**self._base(client=_client(group_size=1, group_type="solo")))
        assert req.client.group_type == "solo"

    def test_travel_style_enum(self):
        with pytest.raises(ValidationError):
            ClientProfile(budget_per_day_usd=200, travel_style="ultra-luxury")

    def test_single_city_auto_populates_destinations(self):
        """Single-city trip should auto-create destinations list."""
        req = TripRequest(**self._base())
        assert len(req.destinations) == 1
        assert req.destinations[0].city == "Rome"
        assert req.destinations[0].nights == 5


# ── DisruptionEvent ───────────────────────────────────────────────────────────

@pytest.mark.unit
class TestDisruptionEvent:
    def test_minimal_valid(self):
        event = DisruptionEvent(
            day_number=2,
            period="morning",
            description="Colosseum closed for maintenance",
        )
        assert event.day_number == 2
        assert event.period == "morning"

    def test_period_defaults_empty(self):
        event = DisruptionEvent(day_number=1, description="Rain")
        assert event.period == ""

    def test_disruption_type_optional(self):
        event = DisruptionEvent(day_number=1, description="x")
        assert event.disruption_type == ""


# ── ItineraryDelta ────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestItineraryDelta:
    def _disruption(self):
        return DisruptionEvent(day_number=2, period="morning", description="Test")

    def test_empty_delta_valid(self):
        delta = ItineraryDelta(
            disruption=self._disruption(),
            affected_days=[2],
            changed_slots=[],
            removed_slots=[],
            reasoning="Nothing changed.",
            new_daily_costs=[],
        )
        assert delta.affected_days == [2]
        assert delta.reasoning == "Nothing changed."

    def test_changed_slots_structure(self):
        slot = DeltaSlot(
            day_number=2,
            period="morning",
            place_name="Capitoline Museums",
        )
        delta = ItineraryDelta(
            disruption=self._disruption(),
            affected_days=[2],
            changed_slots=[slot],
            removed_slots=[],
            reasoning="Swapped morning slot.",
            new_daily_costs=[],
        )
        assert delta.changed_slots[0].place_name == "Capitoline Museums"

    def test_affected_days_list(self):
        delta = ItineraryDelta(
            disruption=self._disruption(),
            affected_days=[2, 3],
            changed_slots=[],
            removed_slots=[],
            reasoning="Cascade.",
            new_daily_costs=[DailyCost(day=2, cost_usd=150.0), DailyCost(day=3, cost_usd=130.0)],
        )
        assert 3 in delta.affected_days
        assert delta.new_daily_costs[0].cost_usd == 150.0


# ── Hotel pricing estimates ───────────────────────────────────────────────────

@pytest.mark.unit
class TestPriceLevelEstimate:
    def test_cheap_city_lower_rate(self):
        from backend.tools.hotel_pricing import _price_level_estimate
        bangkok = _price_level_estimate(2, "Bangkok")
        rome = _price_level_estimate(2, "Rome")
        assert bangkok < rome

    def test_expensive_city_higher_rate(self):
        from backend.tools.hotel_pricing import _price_level_estimate
        london = _price_level_estimate(2, "London")
        rome = _price_level_estimate(2, "Rome")
        assert london > rome

    def test_higher_price_level_higher_rate(self):
        from backend.tools.hotel_pricing import _price_level_estimate
        budget = _price_level_estimate(1, "Rome")
        luxury = _price_level_estimate(4, "Rome")
        assert luxury > budget

    def test_none_price_level_returns_none(self):
        from backend.tools.hotel_pricing import _price_level_estimate
        assert _price_level_estimate(None, "Rome") is None
