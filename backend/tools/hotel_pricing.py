"""LiteAPI hotel pricing — real nightly rates with local JSON cache.

Flow per lookup:
  1. Check local JSON cache (backend/data/hotel_rates_<city>.json)
  2. On miss: call LiteAPI /data/hotels to get hotel IDs for the city,
     then /hotels/rates for the cheapest available room.
  3. Write result back to cache so subsequent calls are free.

Cache key: (city_lower, hotel_name_lower, checkin_month)
Stored as: backend/data/hotel_rates_<city_lower>.json
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parents[1] / "data"
_BASE_URL  = "https://api.liteapi.travel/v3.0"
_TIMEOUT   = 12  # seconds per request


def _api_key() -> str | None:
    return os.environ.get("LITEAPI_KEY")


def _cache_path(city: str) -> Path:
    return _DATA_DIR / f"hotel_rates_{city.lower().replace(' ', '_')}.json"


def _load_cache(city: str) -> dict:
    path = _cache_path(city)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _save_cache(city: str, data: dict) -> None:
    path = _cache_path(city)
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        logger.warning("hotel_pricing: cache write failed: %s", exc)


def _cache_key(hotel_name: str, checkin_month: str) -> str:
    """Stable cache key: normalised name + YYYY-MM."""
    name = re.sub(r"[^a-z0-9]", "_", hotel_name.lower()).strip("_")
    return f"{name}__{checkin_month}"


def _price_level_estimate(price_level: int | None, city: str) -> float | None:
    """
    Fallback nightly rate estimate when LiteAPI is unavailable.
    Uses price_level from Google Places + a rough city-tier multiplier.
    """
    if price_level is None:
        return None
    # Base rates per price_level (mid-range city, USD/night)
    base = {1: 60, 2: 120, 3: 200, 4: 350}[price_level]

    # City tier multipliers
    expensive = {"tokyo", "london", "paris", "new york", "zurich", "amsterdam"}
    cheap     = {"bangkok", "chiang mai", "hanoi", "ho chi minh city", "marrakech"}
    c = city.lower()
    if any(e in c for e in expensive):
        base = int(base * 1.5)
    elif any(e in c for e in cheap):
        base = int(base * 0.6)
    return float(base)


async def get_hotel_nightly_rate(
    hotel_name: str,
    city: str,
    country_code: str,
    checkin: date,
    nights: int,
    adults: int = 2,
    price_level: int | None = None,
) -> float | None:
    """
    Return estimated nightly rate (USD/night) for a hotel.

    Tries LiteAPI (with cache) first. Falls back to price_level estimate
    if LITEAPI_KEY is not set, or if the hotel is not found / unavailable.
    Returns None if no estimate is possible.
    """
    key_month = checkin.strftime("%Y-%m")
    cache = _load_cache(city)
    ckey  = _cache_key(hotel_name, key_month)

    # Cache hit
    if ckey in cache:
        logger.info("hotel_pricing cache HIT city=%r hotel=%r", city, hotel_name)
        return cache[ckey]

    api_key = _api_key()
    if not api_key:
        logger.debug("LITEAPI_KEY not set — using price_level estimate for %r", hotel_name)
        return _price_level_estimate(price_level, city)

    checkout = checkin + timedelta(days=nights)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            headers = {"X-API-Key": api_key}

            # Step 1: find hotel IDs for city
            resp = await client.get(
                f"{_BASE_URL}/data/hotels",
                params={"countryCode": country_code.upper(), "cityName": city, "limit": 20},
                headers=headers,
            )
            resp.raise_for_status()
            hotels = resp.json().get("data", [])

            # Match by name (fuzzy: check if key words overlap)
            name_words = set(re.sub(r"[^a-z\s]", "", hotel_name.lower()).split())
            best_id = None
            best_score = 0
            for h in hotels:
                h_words = set(re.sub(r"[^a-z\s]", "", h.get("name", "").lower()).split())
                score = len(name_words & h_words)
                if score > best_score:
                    best_score, best_id = score, h["id"]

            if not best_id or best_score < 1:
                logger.info("hotel_pricing: no LiteAPI match for %r in %r", hotel_name, city)
                result = _price_level_estimate(price_level, city)
                cache[ckey] = result
                _save_cache(city, cache)
                return result

            # Step 2: fetch rates for matched hotel
            rates_resp = await client.post(
                f"{_BASE_URL}/hotels/rates",
                headers=headers,
                json={
                    "hotelIds": [best_id],
                    "checkin":  checkin.isoformat(),
                    "checkout": checkout.isoformat(),
                    "currency": "USD",
                    "guestNationality": "US",
                    "occupancies": [{"adults": adults}],
                },
            )
            rates_resp.raise_for_status()
            rate_data = rates_resp.json().get("data", [])

            nightly: float | None = None
            if rate_data:
                room_types = rate_data[0].get("roomTypes", [])
                if room_types:
                    rates = room_types[0].get("rates", [])
                    if rates:
                        total_list = rates[0].get("retailRate", {}).get("total", [])
                        if total_list:
                            total_usd = float(total_list[0].get("amount", 0))
                            if total_usd > 0:
                                nightly = round(total_usd / nights, 2)

            if nightly is None:
                nightly = _price_level_estimate(price_level, city)

            logger.info("hotel_pricing LiteAPI city=%r hotel=%r nightly=$%.0f/night (source=%s)",
                        city, hotel_name, nightly or 0,
                        "liteapi" if nightly and rate_data else "estimate")

            cache[ckey] = nightly
            _save_cache(city, cache)
            return nightly

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 2001:
            logger.info("hotel_pricing: no availability for %r in %r", hotel_name, city)
        else:
            logger.warning("hotel_pricing API error: %s", exc)
    except Exception as exc:
        logger.warning("hotel_pricing unexpected error: %s", exc)

    # Final fallback
    result = _price_level_estimate(price_level, city)
    cache[ckey] = result
    _save_cache(city, cache)
    return result
