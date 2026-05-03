"""
Tests for inter-city transport tool — static lookup, cost midpoint, fallback chain.
No API calls made (Tavily fallback is not invoked in these tests).
"""

from __future__ import annotations

import pytest

from backend.tools.transport import (
    InterCityRouteResult,
    TransportOption,
    _lookup,
    _driving_fallback,
    _pick_recommended,
)


# ── Static lookup ─────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestStaticLookup:
    def test_known_route_forward(self):
        raw = _lookup("Rome", "Florence")
        assert raw is not None
        assert len(raw) >= 1
        assert any(r["mode"] == "train" for r in raw)

    def test_known_route_reversed(self):
        """Lookup is bidirectional."""
        assert _lookup("Florence", "Rome") is not None

    def test_case_insensitive(self):
        assert _lookup("ROME", "FLORENCE") is not None
        assert _lookup("rome", "florence") is not None

    def test_unknown_route_returns_none(self):
        assert _lookup("Atlantis", "Narnia") is None

    def test_japan_shinkansen(self):
        raw = _lookup("Tokyo", "Kyoto")
        assert raw is not None
        assert raw[0]["mode"] == "train"
        assert raw[0]["price_usd_low"] >= 70  # Shinkansen is not cheap

    def test_greece_ferry(self):
        raw = _lookup("Athens", "Santorini")
        assert raw is not None
        modes = {r["mode"] for r in raw}
        assert "ferry" in modes or "flight" in modes

    def test_all_routes_have_required_fields(self):
        from backend.tools.transport import _ROUTES
        required = {"mode", "duration_hours", "price_usd_low", "price_usd_high"}
        for key, routes in _ROUTES.items():
            for r in routes:
                missing = required - r.keys()
                assert not missing, f"Route {key} missing fields: {missing}"

    def test_all_prices_positive(self):
        from backend.tools.transport import _ROUTES
        for key, routes in _ROUTES.items():
            for r in routes:
                assert r["price_usd_low"] > 0, f"Route {key} has zero low price"
                assert r["price_usd_high"] >= r["price_usd_low"], \
                    f"Route {key} high < low"


# ── _pick_recommended ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestPickRecommended:
    def _opts(self, specs: list[dict]) -> list[TransportOption]:
        return [TransportOption(**s) for s in specs]

    def test_prefers_short_train_over_flight(self):
        opts = self._opts([
            {"mode": "train",  "duration_hours": 2.0, "price_usd_low": 20, "price_usd_high": 60, "notes": ""},
            {"mode": "flight", "duration_hours": 1.0, "price_usd_low": 80, "price_usd_high": 150, "notes": ""},
        ])
        mode, cost = _pick_recommended(opts)
        assert mode == "train"

    def test_prefers_flight_when_train_too_long(self):
        opts = self._opts([
            {"mode": "train",  "duration_hours": 8.0, "price_usd_low": 40, "price_usd_high": 80, "notes": ""},
            {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 60, "price_usd_high": 120, "notes": ""},
        ])
        mode, _ = _pick_recommended(opts)
        assert mode == "flight"

    def test_cost_is_midpoint(self):
        opts = self._opts([
            {"mode": "train", "duration_hours": 2.0, "price_usd_low": 30, "price_usd_high": 70, "notes": ""},
        ])
        _, cost = _pick_recommended(opts)
        assert cost == 50  # (30 + 70) // 2

    def test_single_option_always_recommended(self):
        opts = self._opts([
            {"mode": "bus", "duration_hours": 5.0, "price_usd_low": 15, "price_usd_high": 25, "notes": ""},
        ])
        mode, cost = _pick_recommended(opts)
        assert mode == "bus"
        assert cost == 20

    def test_ferry_chosen_over_drive(self):
        opts = self._opts([
            {"mode": "drive", "duration_hours": 6.0, "price_usd_low": 30, "price_usd_high": 80, "notes": ""},
            {"mode": "ferry", "duration_hours": 2.5, "price_usd_low": 35, "price_usd_high": 60, "notes": ""},
        ])
        mode, _ = _pick_recommended(opts)
        assert mode == "ferry"


# ── Driving fallback ──────────────────────────────────────────────────────────

@pytest.mark.unit
class TestDrivingFallback:
    def test_returns_drive_option(self):
        raw = _driving_fallback("Atlantis", "Narnia")
        assert len(raw) == 1
        assert raw[0]["mode"] == "drive"

    def test_price_range_positive(self):
        raw = _driving_fallback("A", "B")
        assert raw[0]["price_usd_low"] > 0
        assert raw[0]["price_usd_high"] >= raw[0]["price_usd_low"]


# ── InterCityRouteResult model ────────────────────────────────────────────────

@pytest.mark.unit
class TestInterCityRouteResult:
    def test_model_validates(self):
        result = InterCityRouteResult(
            origin="Rome",
            destination="Florence",
            options=[TransportOption(
                mode="train", duration_hours=1.5,
                price_usd_low=20, price_usd_high=60, notes="Frecciarossa"
            )],
            recommended="train",
            cost_per_person_usd=40,
            source="static",
        )
        assert result.cost_per_person_usd == 40
        assert result.source == "static"

    def test_source_default(self):
        result = InterCityRouteResult(
            origin="A", destination="B",
            options=[], recommended="drive",
            cost_per_person_usd=50,
        )
        assert result.source == "static"
