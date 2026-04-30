"""Tavily web search — finds booking/website URLs for venues using a 3-tier priority order.

Priority for ALL categories (restaurant, activity, lodging):
  1. Official website from Places API (caller checks this before calling us)
  2. Official website via web search (Step 1 — filters out platforms/aggregators)
  3. Third-party booking link via web search (Step 2 — allows reservation/ticketing platforms)

Country-specific platforms (TheFork in Italy, OpenTable in US, Viator for tours, etc.)
are handled by search engine ranking — no hardcoding needed.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from agents import function_tool

logger = logging.getLogger(__name__)

# Domains that are pure info/review sites — skip in BOTH steps
_PURE_INFO_DOMAINS = {
    "google.com", "maps.google", "wikipedia", "wikidata",
    "facebook.com", "instagram.com", "twitter.com", "linkedin.com",
}

# Domains that are platforms/aggregators — skip in Step 1 (official search) but allow in Step 2
_PLATFORM_DOMAINS = {
    # Reviews
    "tripadvisor", "yelp", "trustpilot",
    # Hotel aggregators
    "booking.com", "expedia", "hotels.com", "agoda", "airbnb", "hostelworld",
    # Restaurant reservation platforms
    "thefork", "opentable", "resy", "eatwith", "quandoo", "sevenrooms",
    "fork.com", "lafourchette",
    # Activity/tour platforms
    "viator", "getyourguide", "klook", "tiqets", "musement", "airbnb.com/experiences",
    "civitatis", "veltra", "tourradar", "headout",
}

# Step 1 queries — aimed at official sites
_OFFICIAL_QUERIES = {
    "lodging":    "{name} {city} hotel official website",
    "restaurant": "{name} {city} restaurant official website",
    "activity":   "{name} {city} official website tickets",
    "attraction": "{name} {city} official website tickets",
}

# Step 2 queries — aimed at booking/reservation platforms
_PLATFORM_QUERIES = {
    "lodging":    "book {name} {city} hotel reservation",
    "restaurant": "{name} {city} reservation book table",
    "activity":   "book tickets {name} {city}",
    "attraction": "buy tickets {name} {city}",
}


def _is_pure_info(url: str) -> bool:
    u = url.lower()
    return any(d in u for d in _PURE_INFO_DOMAINS)


def _is_platform(url: str) -> bool:
    u = url.lower()
    return any(d in u for d in _PLATFORM_DOMAINS)


def _best_url(results: list[dict], allow_platforms: bool) -> str:
    """Return the best URL from Tavily results given the platform policy."""
    for r in results:
        url = r.get("url", "")
        if not url or _is_pure_info(url):
            continue
        if not allow_platforms and _is_platform(url):
            continue
        return url
    return ""


def _tavily_search(client, query: str, max_results: int = 5) -> list[dict]:
    try:
        resp = client.search(query=query, max_results=max_results, search_depth="basic")
        return resp.get("results", [])
    except Exception as e:
        logger.warning("Tavily search error query=%r: %s", query, e)
        return []


@function_tool
def search_booking_url(
    venue_name: str,
    city: str,
    category: Literal["lodging", "activity", "restaurant", "attraction"] = "attraction",
) -> str:
    """Find a booking or website URL for a venue using a 3-tier priority search.

    Call this when get_place_details returns a place with no website.
    Priority order:
      1. Official website (no aggregators or reservation platforms)
      2. Third-party booking link (reservation/ticketing platforms allowed)

    Returns the best URL found, or empty string if nothing useful found.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set — skipping web search")
        return ""

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)

        # Step 1: Search for official website
        official_query = _OFFICIAL_QUERIES.get(category, "{name} {city} official website")
        official_query = official_query.format(name=venue_name, city=city)
        results = _tavily_search(client, official_query)
        url = _best_url(results, allow_platforms=False)
        if url:
            logger.info("search_booking_url [official] %r → %s", venue_name, url)
            return url

        # Step 2: Fall back to third-party booking platform
        platform_query = _PLATFORM_QUERIES.get(category, "book {name} {city}")
        platform_query = platform_query.format(name=venue_name, city=city)
        results = _tavily_search(client, platform_query)
        url = _best_url(results, allow_platforms=True)
        if url:
            logger.info("search_booking_url [platform] %r → %s", venue_name, url)
            return url

    except Exception as e:
        logger.warning("Tavily search failed venue=%r: %s", venue_name, e)

    return ""
