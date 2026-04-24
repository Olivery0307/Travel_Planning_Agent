"""Google Maps routing tools exposed to specialist agents."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Literal

import googlemaps
from agents import function_tool

from backend.models.places import DirectionsResult, LatLng, RouteResult

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


@function_tool
def compute_route_matrix(
    origins: list[LatLng],
    destinations: list[LatLng],
    mode: Literal["transit", "walking", "driving"] = "transit",
) -> list[list[RouteResult]] | dict:
    """Compute travel time and distance between multiple origins and destinations.
    Returns matrix[i][j] = RouteResult for origin i to destination j.
    Batch all pairs in one call — never call this in a loop per-pair.
    mode='walking' for legs under 1km; 'transit' for cross-city hops.
    """
    try:
        client = _client()
        origins_str = [f"{o.lat},{o.lng}" for o in origins]
        destinations_str = [f"{d.lat},{d.lng}" for d in destinations]

        raw = client.distance_matrix(
            origins=origins_str,
            destinations=destinations_str,
            mode=mode,
            departure_time=datetime.now(),
        )

        matrix: list[list[RouteResult]] = []
        for i, row in enumerate(raw.get("rows", [])):
            row_results: list[RouteResult] = []
            for j, element in enumerate(row.get("elements", [])):
                if element.get("status") == "OK":
                    duration_sec = element.get("duration", {}).get("value", 0)
                    distance_m = element.get("distance", {}).get("value", 0)
                    row_results.append(
                        RouteResult(
                            origin=origins[i],
                            destination=destinations[j],
                            duration_minutes=round(duration_sec / 60),
                            distance_km=round(distance_m / 1000, 2),
                            mode=mode,
                            summary=element.get("duration", {}).get("text", ""),
                        )
                    )
                else:
                    row_results.append(
                        RouteResult(
                            origin=origins[i],
                            destination=destinations[j],
                            duration_minutes=-1,
                            distance_km=-1,
                            mode=mode,
                            summary="unavailable",
                        )
                    )
            matrix.append(row_results)

        logger.info("compute_route_matrix %dx%d matrix mode=%s", len(origins), len(destinations), mode)
        return matrix
    except Exception as exc:
        logger.error("compute_route_matrix error: %s", exc)
        return {"error": str(exc)}


@function_tool
def get_directions(
    origin: LatLng,
    destination: LatLng,
    mode: Literal["transit", "walking", "driving"] = "transit",
) -> DirectionsResult | dict:
    """Get turn-by-turn directions between two points. Use only for final itinerary
    output where a human will follow the route. For sequencing/optimization, use
    compute_route_matrix instead (cheaper — one call for all pairs).
    """
    try:
        client = _client()
        raw = client.directions(
            origin=f"{origin.lat},{origin.lng}",
            destination=f"{destination.lat},{destination.lng}",
            mode=mode,
        )
        if not raw:
            return {"error": "No route found"}
        leg = raw[0].get("legs", [{}])[0]
        steps = [s.get("html_instructions", "") for s in leg.get("steps", [])]
        return DirectionsResult(
            steps=steps,
            total_duration_minutes=round(leg.get("duration", {}).get("value", 0) / 60),
            total_distance_km=round(leg.get("distance", {}).get("value", 0) / 1000, 2),
            mode=mode,
        )
    except Exception as exc:
        logger.error("get_directions error: %s", exc)
        return {"error": str(exc)}
