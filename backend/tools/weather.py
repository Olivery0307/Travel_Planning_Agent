"""Weather tool — fetches forecasts and historical climate data from Open-Meteo (free, no key needed)."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import httpx
from agents import RunContextWrapper, function_tool

logger = logging.getLogger(__name__)

# WMO weather code → (label, emoji, outdoor_suitable)
_WMO: dict[int, tuple[str, str, bool]] = {
    0:  ("Clear sky",         "☀️",  True),
    1:  ("Mainly clear",      "🌤️",  True),
    2:  ("Partly cloudy",     "⛅",  True),
    3:  ("Overcast",          "☁️",  False),
    45: ("Fog",               "🌫️", False),
    48: ("Icy fog",           "🌫️", False),
    51: ("Light drizzle",     "🌦️", False),
    53: ("Drizzle",           "🌧️", False),
    55: ("Heavy drizzle",     "🌧️", False),
    61: ("Light rain",        "🌧️", False),
    63: ("Rain",              "🌧️", False),
    65: ("Heavy rain",        "🌧️", False),
    71: ("Light snow",        "❄️",  False),
    73: ("Snow",              "❄️",  False),
    75: ("Heavy snow",        "❄️",  False),
    80: ("Rain showers",      "🌦️", False),
    81: ("Heavy showers",     "🌧️", False),
    82: ("Violent showers",   "⛈️",  False),
    95: ("Thunderstorm",      "⛈️",  False),
    99: ("Severe thunderstorm","⛈️", False),
}


def _decode_wmo(code: int) -> tuple[str, str, bool]:
    if code in _WMO:
        return _WMO[code]
    nearest = min(_WMO, key=lambda c: abs(c - code))
    return _WMO[nearest]


async def _geocode(city: str, country: str) -> tuple[float, float] | None:
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=en&format=json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            data = (await client.get(url)).json()
            results = data.get("results", [])
            if results:
                return results[0]["latitude"], results[0]["longitude"]
    except Exception as e:
        logger.warning("geocode failed city=%r: %s", city, e)
    return None


async def _forecast(lat: float, lng: float, start: date, days: int) -> list[dict]:
    """Real forecast — Open-Meteo supports up to 16 days from today."""
    # Clamp end_date to 16 days from today so we never exceed the API window
    max_end = date.today() + timedelta(days=16)
    end = min(start + timedelta(days=days - 1), max_end)
    if end < start:
        return []
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lng}"
        f"&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        f"&start_date={start}&end_date={end}&timezone=auto"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            body = resp.json()
        if body.get("error"):
            logger.warning("forecast API error: %s", body.get("reason"))
            return []
        d = body.get("daily", {})
        out = []
        for i, dt in enumerate(d.get("time", [])):
            code = int(d["weathercode"][i])
            label, icon, base_outdoor = _decode_wmo(code)
            prob = int(d.get("precipitation_probability_max", [0] * days)[i] or 0)
            out.append({
                "date": dt,
                "condition": label,
                "icon": icon,
                "temp_high_c": round(d["temperature_2m_max"][i]),
                "temp_low_c":  round(d["temperature_2m_min"][i]),
                "precipitation_probability": prob,
                "outdoor_suitable": base_outdoor and prob < 60,
                "is_forecast": True,
            })
        return out
    except Exception as e:
        logger.warning("forecast failed: %s", e)
        return []


async def _historical(lat: float, lng: float, start: date, days: int) -> list[dict]:
    """Historical climate — uses same dates from last year as proxy for long-range trips."""
    hy_start = start.replace(year=start.year - 1)
    hy_end   = hy_start + timedelta(days=days - 1)
    url = (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat}&longitude={lng}"
        f"&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum"
        f"&start_date={hy_start}&end_date={hy_end}&timezone=auto"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            d = (await client.get(url)).json().get("daily", {})
        out = []
        for i in range(len(d.get("time", []))):
            code = int(d["weathercode"][i]) if d.get("weathercode") else 0
            label, icon, base_outdoor = _decode_wmo(code)
            precip = float(d.get("precipitation_sum", [0] * days)[i] or 0)
            prob = min(int(precip * 20), 100)
            out.append({
                "date": (start + timedelta(days=i)).isoformat(),
                "condition": label + " (historical avg)",
                "icon": icon,
                "temp_high_c": round(d["temperature_2m_max"][i]) if d.get("temperature_2m_max") else None,
                "temp_low_c":  round(d["temperature_2m_min"][i]) if d.get("temperature_2m_min") else None,
                "precipitation_probability": prob,
                "outdoor_suitable": base_outdoor and precip < 5,
                "is_forecast": False,
            })
        return out
    except Exception as e:
        logger.warning("historical climate failed: %s", e)
        return []


@function_tool
async def get_weather_forecast(
    ctx: RunContextWrapper,
    city: str,
    country: str,
    start_date: str,
    duration_days: int,
) -> str:
    """Fetch weather for the trip dates and store it in session context.
    Within 16 days ahead: real forecast. Beyond 16 days: historical climate averages from last year.
    start_date must be YYYY-MM-DD format.
    Returns a plain-text day-by-day weather summary to include in the solver prompt.
    """
    try:
        trip_start = date.fromisoformat(start_date)
    except ValueError:
        return "Weather unavailable — invalid start_date format (use YYYY-MM-DD)."

    coords = await _geocode(city, country)
    if not coords:
        return f"Weather unavailable — could not locate {city}, {country}."
    lat, lng = coords

    today = date.today()
    forecast_cutoff = today + timedelta(days=16)

    if trip_start >= forecast_cutoff:
        # Entirely beyond forecast window — use historical only
        weather = await _historical(lat, lng, trip_start, duration_days)
    elif trip_start + timedelta(days=duration_days - 1) <= forecast_cutoff:
        # Entirely within forecast window
        weather = await _forecast(lat, lng, trip_start, duration_days)
    else:
        # Straddles the boundary — forecast for the near days, historical for the rest
        forecast_days = (forecast_cutoff - trip_start).days
        hist_start = forecast_cutoff
        hist_days = duration_days - forecast_days
        weather = await _forecast(lat, lng, trip_start, forecast_days)
        weather += await _historical(lat, lng, hist_start, hist_days)

    if not weather:
        return "Weather data unavailable — plan without weather constraints."

    # Cache in AppContext for frontend badge rendering
    if ctx.context:
        ctx.context.weather_data = json.dumps(weather)
        ctx.context.save()

    lines = []
    for i, w in enumerate(weather, 1):
        temp = f"{w['temp_high_c']}°C" if w.get("temp_high_c") is not None else "N/A"
        outdoor = "outdoor OK" if w["outdoor_suitable"] else "prefer indoor"
        tag = "forecast" if w["is_forecast"] else "historical avg"
        lines.append(
            f"Day {i} ({w['date']}): {w['icon']} {w['condition']} | High {temp} | "
            f"Rain {w['precipitation_probability']}% | {outdoor} [{tag}]"
        )
    return "\n".join(lines)
