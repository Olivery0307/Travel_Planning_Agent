"""Candidate pool tool — lets the replanner reuse already-fetched places."""

from __future__ import annotations

import logging
from typing import Literal

from agents import RunContextWrapper, function_tool

from backend.models.places import PlaceResult

logger = logging.getLogger(__name__)

_POOL_KEY = {
    "lodging": "lodging",
    "activity": "activities",
    "attraction": "activities",
    "restaurant": "dining",
    "dining": "dining",
}


@function_tool
def get_candidates_from_pool(
    ctx: RunContextWrapper,
    category: Literal["lodging", "activity", "restaurant", "dining"],
    exclude_place_ids: list[str] | None = None,
) -> list[PlaceResult] | dict:
    """Return already-fetched place candidates from this session's pool.
    Call this BEFORE search_places to avoid redundant API calls.
    category: one of lodging, activity, restaurant, dining.
    exclude_place_ids: place_ids already in the disrupted slot (to avoid re-suggesting them).
    Returns a list of PlaceResult objects, empty list if pool is empty for this category.
    """
    if ctx.context is None:
        return {"error": "No session context available."}

    pool = ctx.context.candidate_pool
    key = _POOL_KEY.get(category, "activities")
    entries = pool.get(key, [])

    exclude = set(exclude_place_ids or [])
    results = [
        PlaceResult(**e) for e in entries
        if e.get("place_id") not in exclude
    ]

    logger.info(
        "get_candidates_from_pool category=%r key=%r pool_size=%d returned=%d excluded=%d",
        category, key, len(entries), len(results), len(exclude),
    )
    return results
