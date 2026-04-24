"""Google Places API tools exposed to specialist agents."""

from __future__ import annotations

import logging
import os
from typing import Literal

import googlemaps
from agents import function_tool
from pydantic import Field

from backend.models.places import OpeningHours, PlaceResult

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


@function_tool
def search_places(
    query: str,
    location: str,
    category: Literal["lodging", "activity", "restaurant", "attraction"],
    max_results: int = 10,
    min_rating: float = 3.5,
) -> list[PlaceResult] | dict:
    """Search Google Places for travel-relevant locations matching a query.
    Use category to narrow results. location should be a city name e.g. 'Rome, Italy'.
    Returns up to max_results places with name, address, rating, price_level,
    opening_hours summary, and place_id. Always call this before get_place_details
    to get place_ids first.
    """
    try:
        client = _client()
        category_type_map = {
            "lodging": "lodging",
            "restaurant": "restaurant",
            "activity": "tourist_attraction",
            "attraction": "tourist_attraction",
        }
        place_type = category_type_map.get(category)

        raw = client.places(
            query=f"{query} in {location}",
            type=place_type,
        )

        results: list[PlaceResult] = []
        for p in raw.get("results", [])[:max_results]:
            rating = p.get("rating")
            if rating is not None and rating < min_rating:
                continue
            loc = p.get("geometry", {}).get("location", {})
            hours_raw = p.get("opening_hours", {})
            results.append(
                PlaceResult(
                    place_id=p["place_id"],
                    name=p.get("name", ""),
                    address=p.get("formatted_address", p.get("vicinity", "")),
                    lat=loc.get("lat", 0.0),
                    lng=loc.get("lng", 0.0),
                    rating=rating,
                    user_ratings_total=p.get("user_ratings_total"),
                    price_level=p.get("price_level"),
                    opening_hours=OpeningHours(open_now=hours_raw.get("open_now")),
                    category=category,
                )
            )
        logger.info("search_places query=%r returned %d results", query, len(results))
        return results
    except Exception as exc:
        logger.error("search_places error: %s", exc)
        return {"error": str(exc)}


@function_tool
def get_place_details(place_id: str) -> PlaceResult | dict:
    """Fetch full details for a specific place by its Google Places place_id.
    Returns name, address, lat/lng, phone, website, opening_hours (all days),
    rating, user_ratings_total, price_level, photos (first 3 URLs), editorial_summary.
    Use this after search_places to get complete data for shortlisted candidates.
    """
    try:
        client = _client()
        fields = [
            "place_id", "name", "formatted_address", "geometry",
            "rating", "user_ratings_total", "price_level",
            "opening_hours", "photo", "website", "formatted_phone_number",
            "editorial_summary",
        ]
        raw = client.place(place_id, fields=fields).get("result", {})
        loc = raw.get("geometry", {}).get("location", {})
        hours_raw = raw.get("opening_hours", {})
        photo_urls = []
        for photo in raw.get("photos", [])[:3]:
            ref = photo.get("photo_reference")
            if ref:
                key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY", "")
                photo_urls.append(
                    f"https://maps.googleapis.com/maps/api/place/photo"
                    f"?maxwidth=800&photoreference={ref}&key={key}"
                )
        result = PlaceResult(
            place_id=raw.get("place_id", place_id),
            name=raw.get("name", ""),
            address=raw.get("formatted_address", ""),
            lat=loc.get("lat", 0.0),
            lng=loc.get("lng", 0.0),
            rating=raw.get("rating"),
            user_ratings_total=raw.get("user_ratings_total"),
            price_level=raw.get("price_level"),
            opening_hours=OpeningHours(
                open_now=hours_raw.get("open_now"),
                weekday_text=hours_raw.get("weekday_text", []),
            ),
            photo_urls=photo_urls,
            website=raw.get("website"),
            phone=raw.get("formatted_phone_number"),
            editorial_summary=raw.get("editorial_summary", {}).get("overview"),
        )
        logger.info("get_place_details place_id=%r name=%r", place_id, result.name)
        return result
    except Exception as exc:
        logger.error("get_place_details error: %s", exc)
        return {"error": str(exc)}


@function_tool
def get_opening_hours(place_id: str, day_of_week: int) -> dict:
    """Get opening hours for a specific place on a given day (0=Monday, 6=Sunday).
    Returns {'open': bool, 'hours': '9:00 AM - 6:00 PM', 'note': str}.
    Use this when you need to schedule a slot and must confirm the place is open.
    """
    try:
        client = _client()
        raw = client.place(place_id, fields=["opening_hours"]).get("result", {})
        periods = raw.get("opening_hours", {}).get("periods", [])
        weekday_text = raw.get("opening_hours", {}).get("weekday_text", [])
        # day_of_week: 0=Monday in our contract; Google uses 0=Sunday
        google_day = (day_of_week + 1) % 7
        for period in periods:
            if period.get("open", {}).get("day") == google_day:
                open_time = period["open"].get("time", "")
                close_time = period.get("close", {}).get("time", "")
                return {
                    "open": True,
                    "hours": f"{open_time[:2]}:{open_time[2:]} - {close_time[:2]}:{close_time[2:]}",
                    "note": weekday_text[day_of_week] if weekday_text else "",
                }
        return {"open": False, "hours": "Closed", "note": weekday_text[day_of_week] if weekday_text else ""}
    except Exception as exc:
        logger.error("get_opening_hours error: %s", exc)
        return {"error": str(exc)}
