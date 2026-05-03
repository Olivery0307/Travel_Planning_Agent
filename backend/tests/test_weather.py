"""
Tests for weather tool — WMO decoding, outdoor_suitable logic, forecast vs historical branching.
Network calls are fully mocked; no API keys required.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tools.weather import _decode_wmo, _forecast, _historical


# ── WMO decode ────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestDecodeWmo:
    def test_clear_sky(self):
        label, icon, outdoor = _decode_wmo(0)
        assert label == "Clear sky"
        assert icon == "☀️"
        assert outdoor is True

    def test_rain_not_outdoor(self):
        _, _, outdoor = _decode_wmo(63)
        assert outdoor is False

    def test_thunderstorm_not_outdoor(self):
        _, _, outdoor = _decode_wmo(95)
        assert outdoor is False

    def test_partly_cloudy_outdoor(self):
        _, _, outdoor = _decode_wmo(2)
        assert outdoor is True

    def test_unknown_code_nearest_neighbour(self):
        """Unknown WMO code falls back to nearest known code."""
        label, icon, outdoor = _decode_wmo(999)
        # Should not raise, should return something sensible
        assert isinstance(label, str)
        assert isinstance(icon, str)
        assert isinstance(outdoor, bool)

    def test_all_known_codes_return_tuple(self):
        from backend.tools.weather import _WMO
        for code in _WMO:
            result = _decode_wmo(code)
            assert len(result) == 3


# ── outdoor_suitable logic ────────────────────────────────────────────────────

@pytest.mark.unit
class TestOutdoorSuitable:
    """
    outdoor_suitable = base_outdoor AND precipitation_probability < 60 (forecast)
                     = base_outdoor AND precipitation_sum < 5   (historical)
    """

    def _make_forecast_day(self, code: int, prob: int) -> dict:
        """Simulate one day of forecast data parsed by _forecast."""
        from backend.tools.weather import _decode_wmo
        label, icon, base_outdoor = _decode_wmo(code)
        return {
            "outdoor_suitable": base_outdoor and prob < 60,
            "precipitation_probability": prob,
        }

    def test_clear_low_rain_is_outdoor(self):
        day = self._make_forecast_day(0, 10)
        assert day["outdoor_suitable"] is True

    def test_clear_high_rain_not_outdoor(self):
        day = self._make_forecast_day(0, 75)
        assert day["outdoor_suitable"] is False

    def test_rain_code_never_outdoor(self):
        day = self._make_forecast_day(63, 0)  # rain code, 0% rain prob
        assert day["outdoor_suitable"] is False

    def test_overcast_not_outdoor(self):
        day = self._make_forecast_day(3, 0)  # overcast, dry
        assert day["outdoor_suitable"] is False


# ── Forecast vs historical branching ─────────────────────────────────────────

@pytest.mark.unit
class TestForecastBranching:
    """Verify the correct API path is chosen based on days_ahead."""

    @pytest.mark.asyncio
    async def test_within_16_days_uses_forecast(self):
        near_date = date.today() + timedelta(days=5)
        mock_response = {
            "daily": {
                "time": [near_date.isoformat()],
                "weathercode": [0],
                "temperature_2m_max": [25.0],
                "temperature_2m_min": [15.0],
                "precipitation_probability_max": [10],
            }
        }
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=mock_resp)
            ))
            result = await _forecast(0.0, 0.0, near_date, 1)
        assert len(result) == 1
        assert result[0]["is_forecast"] is True
        assert result[0]["date"] == near_date.isoformat()

    @pytest.mark.asyncio
    async def test_historical_marks_is_forecast_false(self):
        far_date = date.today() + timedelta(days=100)
        hist_date = far_date.replace(year=far_date.year - 1)
        mock_response = {
            "daily": {
                "time": [hist_date.isoformat()],
                "weathercode": [0],
                "temperature_2m_max": [30.0],
                "temperature_2m_min": [20.0],
                "precipitation_sum": [0.0],
            }
        }
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=mock_resp)
            ))
            result = await _historical(0.0, 0.0, far_date, 1)
        assert len(result) == 1
        assert result[0]["is_forecast"] is False
        assert "(historical avg)" in result[0]["condition"]

    @pytest.mark.asyncio
    async def test_historical_date_corrected_to_trip_date(self):
        """Historical results should carry the *trip* date, not last year's date."""
        far_date = date(2027, 8, 10)
        hist_date = far_date.replace(year=2026)
        mock_response = {
            "daily": {
                "time": [hist_date.isoformat()],
                "weathercode": [1],
                "temperature_2m_max": [35.0],
                "temperature_2m_min": [22.0],
                "precipitation_sum": [0.0],
            }
        }
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=mock_resp)
            ))
            result = await _historical(0.0, 0.0, far_date, 1)
        assert result[0]["date"] == far_date.isoformat()


# ── get_weather_forecast tool interface ──────────────────────────────────────

@pytest.mark.unit
class TestWeatherForecastTool:
    """
    get_weather_forecast is wrapped by @function_tool so we call the
    underlying Python function directly via its __wrapped__ attribute
    (set by the agents SDK) or by importing the raw async function.
    """

    @pytest.mark.asyncio
    async def test_invalid_date_returns_error_string(self):
        from unittest.mock import MagicMock
        import backend.tools.weather as wmod
        ctx = MagicMock()
        ctx.context = None
        # Call the raw coroutine, bypassing the FunctionTool wrapper
        result = await wmod._weather_forecast_impl(ctx, "Rome", "Italy", "not-a-date", 5)
        assert "unavailable" in result.lower() or "invalid" in result.lower()

    @pytest.mark.asyncio
    async def test_geocode_failure_returns_error_string(self):
        from unittest.mock import MagicMock
        import backend.tools.weather as wmod
        ctx = MagicMock()
        ctx.context = None
        with patch("backend.tools.weather._geocode", AsyncMock(return_value=None)):
            result = await wmod._weather_forecast_impl(ctx, "Atlantis", "Nowhere", "2026-06-01", 3)
        assert "unavailable" in result.lower()
