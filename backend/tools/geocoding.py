"""Geocoding tool — address/place name to lat/lng."""

from __future__ import annotations

import logging
import os

import googlemaps
from agents import function_tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_gmaps: googlemaps.Client | None = None


def _client() -> googlemaps.Client:
    global _gmaps
    if _gmaps is None:
        key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY not set")
        _gmaps = googlemaps.Client(key=key)
    return _gmaps


class GeocodeRequest(BaseModel):
    address: str = Field(description="Street address or place name to geocode, e.g. 'Colosseum, Rome, Italy'")


@function_tool
def geocode_address(request: GeocodeRequest) -> dict:
    """Convert a place name or street address to lat/lng coordinates.
    Returns {'lat': float, 'lng': float, 'formatted_address': str} or {'error': ...}.
    Use when a place_id is not available and you need coordinates for routing.
    """
    try:
        client = _client()
        results = client.geocode(request.address)
        if not results:
            return {"error": f"No results for address: {request.address!r}"}
        loc = results[0]["geometry"]["location"]
        return {
            "lat": loc["lat"],
            "lng": loc["lng"],
            "formatted_address": results[0].get("formatted_address", ""),
        }
    except Exception as exc:
        logger.error("geocode_address error: %s", exc)
        return {"error": str(exc)}
