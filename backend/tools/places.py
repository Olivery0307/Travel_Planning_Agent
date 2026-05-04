"""Google Places API tools exposed to specialist agents.

Lookup order for every call:
  1. PlacesCache (local JSON + GCS)   — zero cost, zero latency
  2. Live Google Places API           — only on cache miss
"""

from __future__ import annotations

import logging
import os
import re
from typing import Literal

import googlemaps
from agents import RunContextWrapper, function_tool

from backend.models.places import OpeningHours, PlaceResult
from backend.tools.cache import get_cache

logger = logging.getLogger(__name__)

_gmaps: googlemaps.Client | None = None


def _client() -> googlemaps.Client:
    global _gmaps
    if _gmaps is None:
        key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_PLACES_API_KEY not set")
        _gmaps = googlemaps.Client(key=key)
    return _gmaps


def _city_from_location(location: str) -> str:
    """Extract city name from 'Rome, Italy' or 'Rome' or '41.9,12.4'."""
    if re.match(r"^-?\d+\.?\d*,\s*-?\d+\.?\d*$", location.strip()):
        return ""  # lat,lng — can't determine city
    return location.split(",")[0].strip().lower()


def _dict_to_place_result(p: dict, category: str = "other") -> PlaceResult:
    """Convert a cache dict to a PlaceResult model."""
    hours_raw = p.get("opening_hours") or {}
    return PlaceResult(
        place_id=p["place_id"],
        name=p.get("name", ""),
        address=p.get("address") or p.get("formatted_address", ""),
        lat=float(p.get("lat", 0.0)),
        lng=float(p.get("lng", 0.0)),
        rating=p.get("rating"),
        user_ratings_total=p.get("user_ratings_total"),
        price_level=p.get("price_level"),
        opening_hours=OpeningHours(
            open_now=hours_raw.get("open_now"),
            weekday_text=hours_raw.get("weekday_text", []),
        ),
        photo_urls=p.get("photo_urls", []),
        website=p.get("website"),
        phone=p.get("phone"),
        editorial_summary=p.get("editorial_summary"),
        category=p.get("category") or category,  # type: ignore[arg-type]
    )


def _pool_key(category: str) -> str:
    """Map Places API category to candidate_pool dict key."""
    return "lodging" if category == "lodging" else (
        "dining" if category == "restaurant" else "activities"
    )


def _add_to_pool(ctx: RunContextWrapper | None, results: list[PlaceResult], category: str) -> None:
    """Append new PlaceResults to the session candidate pool, deduplicating by place_id."""
    if ctx is None or not hasattr(ctx, "context") or ctx.context is None:
        return
    pool = ctx.context.candidate_pool
    key = _pool_key(category)
    existing_ids = {p["place_id"] for p in pool.get(key, [])}
    added = 0
    for r in results:
        if r.place_id not in existing_ids:
            pool.setdefault(key, []).append(r.model_dump())
            existing_ids.add(r.place_id)
            added += 1
    if added:
        ctx.context.save()
        logger.info("candidate_pool updated key=%r added=%d total=%d", key, added, len(pool[key]))


@function_tool
def search_places(
    ctx: RunContextWrapper,
    query: str,
    location: str,
    category: Literal["lodging", "activity", "restaurant", "attraction"],
    max_results: int = 10,
    min_rating: float = 3.5,
) -> list[PlaceResult] | dict:
    """Search for travel-relevant places matching a query in a given location.
    Checks local cache first (fast, free). Falls back to live Google Places API on miss.
    Use category to narrow results. location should be a city name e.g. 'Rome, Italy'.
    Returns up to max_results places with name, address, rating, price_level,
    opening_hours summary, and place_id. Always call this before get_place_details.
    """
    cache = get_cache()
    city  = _city_from_location(location)

    # ── Cache lookup ─────────────────────────────────────
    if city:
        cached = cache.search(city, category, query, max_results, min_rating)
        if len(cached) >= 3:
            results = [_dict_to_place_result(p, category) for p in cached]
            logger.info("search_places cache HIT city=%r cat=%r query=%r → %d results",
                        city, category, query, len(results))
            _add_to_pool(ctx, results, category)
            return results
        if cached:
            logger.info("search_places cache PARTIAL city=%r cat=%r → %d results, supplementing live",
                        city, category, len(cached))
        else:
            logger.info("search_places cache MISS city=%r cat=%r, calling live API", city, category)

    # ── Live API ─────────────────────────────────────────
    try:
        client = _client()
        category_type_map = {
            "lodging":    "lodging",
            "restaurant": "restaurant",
            "activity":   "tourist_attraction",
            "attraction": "tourist_attraction",
        }
        raw = client.places(
            query=f"{query} in {location}",
            type=category_type_map.get(category),
        )

        live_results: list[PlaceResult] = []
        for p in raw.get("results", [])[:max_results]:
            rating = p.get("rating")
            if rating is not None and rating < min_rating:
                continue
            loc = p.get("geometry", {}).get("location", {})
            place_dict = {
                "place_id":           p["place_id"],
                "name":               p.get("name", ""),
                "address":            p.get("formatted_address", p.get("vicinity", "")),
                "lat":                loc.get("lat", 0.0),
                "lng":                loc.get("lng", 0.0),
                "rating":             rating,
                "user_ratings_total": p.get("user_ratings_total"),
                "price_level":        p.get("price_level"),
                "category":           category,
                "opening_hours":      {"open_now": p.get("opening_hours", {}).get("open_now")},
            }
            if city:
                cache.store(place_dict, city)
            live_results.append(_dict_to_place_result(place_dict, category))

        logger.info("search_places live API city=%r query=%r → %d results",
                    city, query, len(live_results))

        # merge cached partials + live, dedupe by place_id
        if city and cached:
            seen = {r.place_id for r in live_results}
            for p in cached:
                if p["place_id"] not in seen:
                    live_results.append(_dict_to_place_result(p, category))
                    seen.add(p["place_id"])

        # Sort by popularity (review count) then rating so top attractions surface first
        import math
        live_results.sort(
            key=lambda r: math.log10(1 + (r.user_ratings_total or 0)) + (r.rating or 0) * 0.5,
            reverse=True,
        )
        final = live_results[:max_results]
        _add_to_pool(ctx, final, category)
        return final

    except Exception as exc:
        logger.error("search_places live API error: %s", exc)
        # return whatever we have from cache rather than an error dict
        if city and cached:
            fallback = [_dict_to_place_result(p, category) for p in cached]
            _add_to_pool(ctx, fallback, category)
            return fallback
        return {"error": str(exc)}


@function_tool
def get_place_details(place_id: str) -> PlaceResult | dict:
    """Fetch full details for a place by its Google Places place_id.
    Returns name, address, lat/lng, phone, website, opening_hours (all days),
    rating, user_ratings_total, price_level, photos, editorial_summary.
    Checks local cache first. Falls back to live API on miss.
    """
    cache = get_cache()

    # ── Cache lookup ─────────────────────────────────────
    cached = cache.get(place_id)
    if cached and cached.get("opening_hours", {}).get("weekday_text"):
        logger.info("get_place_details cache HIT place_id=%r name=%r",
                    place_id, cached.get("name"))
        return _dict_to_place_result(cached)

    # ── Live API ─────────────────────────────────────────
    logger.info("get_place_details cache MISS place_id=%r, calling live API", place_id)
    try:
        client = _client()
        fields = [
            "place_id", "name", "formatted_address", "geometry",
            "rating", "user_ratings_total", "price_level",
            "opening_hours", "photo", "website",
            "formatted_phone_number", "editorial_summary",
        ]
        raw = client.place(place_id, fields=fields).get("result", {})
        loc = raw.get("geometry", {}).get("location", {})
        hours_raw = raw.get("opening_hours", {})

        photo_urls = []
        for photo in raw.get("photos", [])[:3]:
            ref = photo.get("photo_reference")
            if ref:
                # Store without key — never embed credentials in cached data.
                photo_urls.append(
                    f"https://maps.googleapis.com/maps/api/place/photo"
                    f"?maxwidth=800&photoreference={ref}"
                )

        place_dict = {
            "place_id":           raw.get("place_id", place_id),
            "name":               raw.get("name", ""),
            "address":            raw.get("formatted_address", ""),
            "lat":                loc.get("lat", 0.0),
            "lng":                loc.get("lng", 0.0),
            "rating":             raw.get("rating"),
            "user_ratings_total": raw.get("user_ratings_total"),
            "price_level":        raw.get("price_level"),
            "website":            raw.get("website"),
            "phone":              raw.get("formatted_phone_number"),
            "editorial_summary":  raw.get("editorial_summary", {}).get("overview"),
            "photo_urls":         photo_urls,
            "opening_hours": {
                "open_now":      hours_raw.get("open_now"),
                "weekday_text":  hours_raw.get("weekday_text", []),
                "periods":       hours_raw.get("periods", []),
            },
        }

        # store back — use existing city tag if present in cache
        existing = cache.get(place_id)
        city = (existing or {}).get("city", "")
        if city:
            place_dict["category"] = (existing or {}).get("category", "attraction")
            cache.store(place_dict, city)

        logger.info("get_place_details live API place_id=%r name=%r", place_id, place_dict["name"])
        return _dict_to_place_result(place_dict)

    except Exception as exc:
        logger.error("get_place_details error: %s", exc)
        return {"error": str(exc)}


@function_tool
def get_opening_hours(place_id: str, day_of_week: int) -> dict:
    """Get opening hours for a specific place on a given day (0=Monday, 6=Sunday).
    Returns {'open': bool, 'hours': '9:00 AM - 6:00 PM', 'note': str}.
    Checks cache first; falls back to live API.
    """
    cache = get_cache()
    cached = cache.get(place_id)

    # try to answer from cache periods
    if cached:
        periods = (cached.get("opening_hours") or {}).get("periods", [])
        weekday_text = (cached.get("opening_hours") or {}).get("weekday_text", [])
        google_day = (day_of_week + 1) % 7
        for period in periods:
            if period.get("open", {}).get("day") == google_day:
                open_time  = period["open"].get("time", "")
                close_time = period.get("close", {}).get("time", "")
                return {
                    "open":  True,
                    "hours": f"{open_time[:2]}:{open_time[2:]} - {close_time[:2]}:{close_time[2:]}",
                    "note":  weekday_text[day_of_week] if weekday_text else "",
                }
        if weekday_text:
            return {"open": False, "hours": "Closed",
                    "note": weekday_text[day_of_week] if weekday_text else ""}

    # live API fallback
    try:
        client = _client()
        raw = client.place(place_id, fields=["opening_hours"]).get("result", {})
        periods = raw.get("opening_hours", {}).get("periods", [])
        weekday_text = raw.get("opening_hours", {}).get("weekday_text", [])
        google_day = (day_of_week + 1) % 7
        for period in periods:
            if period.get("open", {}).get("day") == google_day:
                open_time  = period["open"].get("time", "")
                close_time = period.get("close", {}).get("time", "")
                return {
                    "open":  True,
                    "hours": f"{open_time[:2]}:{open_time[2:]} - {close_time[:2]}:{close_time[2:]}",
                    "note":  weekday_text[day_of_week] if weekday_text else "",
                }
        return {"open": False, "hours": "Closed",
                "note": weekday_text[day_of_week] if weekday_text else ""}
    except Exception as exc:
        logger.error("get_opening_hours error: %s", exc)
        return {"error": str(exc)}
