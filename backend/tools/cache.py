"""PlacesCache — local JSON cache + GCS bucket backend for Google Places data.

Lookup priority for every tool call:
  1. In-memory cache (populated at startup from local JSON files)
  2. GCS bucket  (if PLACES_CACHE_BUCKET is set — downloads on first miss)
  3. Live Google Places API (result is stored back to memory + GCS)

This means the planner makes zero API calls for seeded cities (Rome, Paris, etc.)
and only calls the live API for genuinely new places, which are then persisted
so subsequent calls are also free.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Directory where seeded JSON files live
_DATA_DIR = Path(__file__).parents[1] / "data"

# GCS bucket name — set PLACES_CACHE_BUCKET env var to enable
_GCS_BUCKET = os.environ.get("PLACES_CACHE_BUCKET", "")


class PlacesCache:
    """In-memory cache backed by local JSON and optionally GCS.

    Keyed two ways:
      - by place_id  → exact lookup for get_place_details
      - by (city, category) → list lookup for search_places
    """

    def __init__(self) -> None:
        # place_id → full place dict
        self._by_id: dict[str, dict] = {}
        # (city, category) → list of place dicts
        self._by_city_cat: dict[tuple[str, str], list[dict]] = {}
        self._loaded_cities: set[str] = set()
        self._gcs_client = None

    # ── Loading ──────────────────────────────────────────────────────────────

    def load_local(self) -> None:
        """Load all backend/data/places_*.json files into memory at startup."""
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        for path in sorted(_DATA_DIR.glob("places_*.json")):
            city = path.stem.replace("places_", "").replace("_", " ")
            self._load_file(path, city)

    def _load_file(self, path: Path, city: str) -> int:
        """Load one JSON file. Returns number of places loaded."""
        try:
            places = json.loads(path.read_text())
            count = 0
            for p in places:
                self._index(p)
                count += 1
            self._loaded_cities.add(city.lower())
            logger.info("cache: loaded %d places for '%s' from %s", count, city, path.name)
            return count
        except Exception as exc:
            logger.warning("cache: failed to load %s: %s", path, exc)
            return 0

    def _index(self, place: dict) -> None:
        """Add one place dict to both indexes."""
        pid = place.get("place_id")
        if not pid:
            return
        self._by_id[pid] = place

        city = (place.get("city") or "").lower().strip()
        cat  = (place.get("category") or "").lower().strip()
        if city and cat:
            key = (city, cat)
            if key not in self._by_city_cat:
                self._by_city_cat[key] = []
            # avoid duplicates
            existing_ids = {p["place_id"] for p in self._by_city_cat[key]}
            if pid not in existing_ids:
                self._by_city_cat[key].append(place)

    # ── GCS ──────────────────────────────────────────────────────────────────

    def _gcs(self):
        if not _GCS_BUCKET:
            return None
        if self._gcs_client is None:
            try:
                from google.cloud import storage  # type: ignore
                self._gcs_client = storage.Client()
            except Exception as exc:
                logger.warning("cache: GCS client init failed: %s", exc)
                return None
        return self._gcs_client

    def _try_load_from_gcs(self, city: str) -> bool:
        """Download places_{city}.json from GCS and load it. Returns True on success."""
        client = self._gcs()
        if not client:
            return False
        city_slug = city.lower().replace(" ", "_")
        local_path = _DATA_DIR / f"places_{city_slug}.json"
        try:
            bucket = client.bucket(_GCS_BUCKET)
            blob = bucket.blob(f"places/{city_slug}.json")
            if not blob.exists():
                return False
            blob.download_to_filename(str(local_path))
            count = self._load_file(local_path, city)
            logger.info("cache: downloaded %d places for '%s' from GCS", count, city)
            return count > 0
        except Exception as exc:
            logger.warning("cache: GCS download failed for '%s': %s", city, exc)
            return False

    def _upload_place_to_gcs(self, city: str) -> None:
        """Re-upload the city's cache file to GCS after adding a new place."""
        client = self._gcs()
        if not client:
            return
        city_slug = city.lower().replace(" ", "_")
        local_path = _DATA_DIR / f"places_{city_slug}.json"
        if not local_path.exists():
            return
        try:
            bucket = client.bucket(_GCS_BUCKET)
            blob = bucket.blob(f"places/{city_slug}.json")
            blob.upload_from_filename(str(local_path), content_type="application/json")
            logger.debug("cache: uploaded updated cache for '%s' to GCS", city)
        except Exception as exc:
            logger.warning("cache: GCS upload failed for '%s': %s", city, exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def has_city(self, city: str) -> bool:
        return city.lower() in self._loaded_cities

    def ensure_city(self, city: str) -> bool:
        """Try to load city from GCS if not already in memory. Returns True if available."""
        if self.has_city(city):
            return True
        return self._try_load_from_gcs(city)

    def search(
        self,
        city: str,
        category: str,
        query: str,
        max_results: int = 10,
        min_rating: float = 3.5,
    ) -> list[dict]:
        """Return cached places matching city + category, ranked by query relevance.

        Returns empty list if city not in cache (caller should fall back to live API).
        Supplements with lower-rated results if fewer than 3 strong matches found.
        """
        self.ensure_city(city)

        # map tool category to cache category keys
        cat_aliases = {
            "activity":   ["attraction"],
            "attraction": ["attraction"],
            "lodging":    ["lodging"],
            "restaurant": ["restaurant"],
        }
        cats = cat_aliases.get(category.lower(), [category.lower()])

        candidates: list[dict] = []
        for cat in cats:
            candidates.extend(self._by_city_cat.get((city.lower(), cat), []))

        if not candidates:
            return []

        # score by query word overlap + rating + log-scaled popularity (review count)
        query_words = set(re.sub(r"[^a-z0-9 ]", "", query.lower()).split())

        import math
        def score(p: dict) -> float:
            name_words = set(re.sub(r"[^a-z0-9 ]", "", p.get("name", "").lower()).split())
            addr_words = set(re.sub(r"[^a-z0-9 ]", "", p.get("address", "").lower()).split())
            overlap    = len(query_words & (name_words | addr_words))
            rating     = float(p.get("rating") or 0)
            n_reviews  = int(p.get("user_ratings_total") or 0)
            # log10(1+n) gives ~0 for 0 reviews, ~2 for 100, ~4 for 10k, ~5 for 100k
            popularity = math.log10(1 + n_reviews)
            return overlap * 10 + rating * 2 + popularity

        rated = [p for p in candidates if (p.get("rating") or 0) >= min_rating]
        if len(rated) < 3:
            # loosen rating filter rather than returning nothing
            rated = candidates

        rated.sort(key=score, reverse=True)
        return rated[:max_results]

    def get(self, place_id: str) -> dict | None:
        """Return full place dict by place_id, or None if not cached."""
        return self._by_id.get(place_id)

    def store(self, place: dict, city: str) -> None:
        """Add a live API result to the in-memory cache and persist to disk + GCS."""
        if not place.get("place_id"):
            return
        place["city"] = city.lower()
        self._index(place)
        self._persist_city(city)
        self._upload_place_to_gcs(city)

    def _persist_city(self, city: str) -> None:
        """Write all in-memory places for a city back to its JSON file."""
        city_slug = city.lower().replace(" ", "_")
        out_path = _DATA_DIR / f"places_{city_slug}.json"
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        city_places = [
            p for p in self._by_id.values()
            if (p.get("city") or "").lower() == city.lower()
        ]
        out_path.write_text(json.dumps(city_places, indent=2))
        logger.debug("cache: persisted %d places for '%s'", len(city_places), city)


# Module-level singleton — initialized once at import time
_cache = PlacesCache()
_cache.load_local()


def get_cache() -> PlacesCache:
    return _cache
