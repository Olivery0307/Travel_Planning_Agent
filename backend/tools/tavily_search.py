"""Tavily web search — finds booking/website URLs for venues using a 3-tier priority order.

Priority for ALL categories (restaurant, activity, lodging):
  1. Official website from Places API (caller checks this before calling us)
  2. Official website via web search (Step 1 — filters out platforms/aggregators)
  3. Third-party booking link via web search (Step 2 — allows reservation/ticketing platforms)

Results are cached in backend/data/tavily_url_cache.json to avoid redundant API calls
and to stay within daily limits. Rate-limit (429) and missing-key errors return "" gracefully.

Country-specific platforms (TheFork in Italy, OpenTable in US, Viator for tours, etc.)
are handled by search engine ranking — no hardcoding needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Literal

from agents import function_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared URL cache — persisted across restarts
# ---------------------------------------------------------------------------
_URL_CACHE_PATH = Path(__file__).parents[1] / "data" / "tavily_url_cache.json"
_TRANSPORT_CACHE_PATH = Path(__file__).parents[1] / "data" / "tavily_transport_cache.json"

def _load_json_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}

def _save_json_cache(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        logger.warning("tavily cache write failed %s: %s", path.name, exc)

# In-memory layer — loaded once at import, flushed on write
_url_cache: dict = _load_json_cache(_URL_CACHE_PATH)
_transport_cache: dict = _load_json_cache(_TRANSPORT_CACHE_PATH)

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


def _tavily_client():
    """Return a TavilyClient instance, or None if key is not set."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return None
    from tavily import TavilyClient
    return TavilyClient(api_key=api_key)


def _tavily_search(client, query: str, max_results: int = 5) -> list[dict] | None:
    """
    Run a Tavily search. Returns results list, empty list on error, or None on rate-limit.
    None signals the caller to skip writing to cache so the key stays unset for retry later.
    """
    try:
        resp = client.search(query=query, max_results=max_results, search_depth="basic")
        return resp.get("results", [])
    except Exception as e:
        err = str(e).lower()
        if "429" in err or "rate limit" in err or "quota" in err:
            logger.warning("Tavily rate limit hit — skipping query=%r", query[:60])
            return None  # signal: do not cache, caller should degrade gracefully
        logger.warning("Tavily search error query=%r: %s", query[:60], e)
        return []


def _url_cache_key(venue_name: str, city: str, category: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", f"{venue_name}__{city}__{category}".lower())


def find_booking_url(venue_name: str, city: str, category: str = "attraction") -> str:
    """Plain Python version — call this directly from other tools (not via LLM).
    Checks a persistent JSON cache first. Falls back gracefully on rate-limit or missing key.
    """
    if not venue_name:
        return ""

    ckey = _url_cache_key(venue_name, city, category)

    # Cache hit — even a cached "" is valid (means "nothing found, don't retry")
    if ckey in _url_cache:
        cached = _url_cache[ckey]
        if cached:
            logger.debug("find_booking_url cache HIT %r → %s", venue_name, cached)
        return cached

    client = _tavily_client()
    if not client:
        return ""

    try:
        url = ""

        # Step 1: official website
        official_query = _OFFICIAL_QUERIES.get(category, "{name} {city} official website")
        results = _tavily_search(client, official_query.format(name=venue_name, city=city))
        if results is None:          # rate-limited — don't cache, just return empty
            return ""
        url = _best_url(results, allow_platforms=False)
        if url:
            logger.info("find_booking_url [official] %r → %s", venue_name, url)

        # Step 2: third-party booking platform
        if not url:
            platform_query = _PLATFORM_QUERIES.get(category, "book {name} {city}")
            results = _tavily_search(client, platform_query.format(name=venue_name, city=city))
            if results is None:
                return ""
            url = _best_url(results, allow_platforms=True)
            if url:
                logger.info("find_booking_url [platform] %r → %s", venue_name, url)

        # Cache result (including "" so we don't retry on future calls)
        _url_cache[ckey] = url
        _save_json_cache(_URL_CACHE_PATH, _url_cache)
        return url

    except Exception as e:
        logger.warning("find_booking_url failed venue=%r: %s", venue_name, e)
        return ""


@function_tool
def search_booking_url(
    venue_name: str,
    city: str,
    category: Literal["lodging", "activity", "restaurant", "attraction"] = "attraction",
) -> str:
    """Find a booking or website URL for a venue using a 3-tier priority search.

    Checks a persistent cache first. Falls back gracefully on rate-limit or missing key.
    Returns the best URL found, or empty string if nothing useful found.
    """
    # Reuse find_booking_url which already handles caching and rate-limits
    return find_booking_url(venue_name, city, category)
