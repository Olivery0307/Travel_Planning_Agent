"""One-shot script: pre-fetch Google Places data for a city and save to JSON cache.

Usage:
    uv run python scripts/seed_places_cache.py --city rome
    uv run python scripts/seed_places_cache.py --city paris --output backend/data/places_paris.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import googlemaps
import typer
from dotenv import load_dotenv

load_dotenv()

SEARCHES = [
    ("top historical attractions", "attraction"),
    ("best museums", "attraction"),
    ("top restaurants", "restaurant"),
    ("breakfast cafes", "restaurant"),
    ("mid-range hotels central", "lodging"),
    ("boutique hotels", "lodging"),
]

app = typer.Typer()


@app.command()
def main(
    city: str = typer.Option(..., help="City to seed, e.g. 'rome'"),
    output: str = typer.Option("", help="Output JSON path. Defaults to backend/data/places_{city}.json"),
    limit: int = typer.Option(10, help="Max results per search query"),
) -> None:
    key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        typer.echo("ERROR: GOOGLE_PLACES_API_KEY not set in .env", err=True)
        raise typer.Exit(1)

    client = googlemaps.Client(key=key)
    out_path = Path(output) if output else Path(f"backend/data/places_{city.lower().replace(' ', '_')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_places: dict[str, dict] = {}

    for query, category in SEARCHES:
        full_query = f"{query} in {city}"
        typer.echo(f"Searching: {full_query}")
        try:
            raw = client.places(query=full_query)
            for p in raw.get("results", [])[:limit]:
                pid = p["place_id"]
                if pid in all_places:
                    continue
                loc = p.get("geometry", {}).get("location", {})
                all_places[pid] = {
                    "place_id": pid,
                    "name": p.get("name", ""),
                    "address": p.get("formatted_address", p.get("vicinity", "")),
                    "lat": loc.get("lat", 0.0),
                    "lng": loc.get("lng", 0.0),
                    "rating": p.get("rating"),
                    "user_ratings_total": p.get("user_ratings_total"),
                    "price_level": p.get("price_level"),
                    "category": category,
                }
        except Exception as exc:
            typer.echo(f"  WARNING: {exc}", err=True)

    out_path.write_text(json.dumps(list(all_places.values()), indent=2))
    typer.echo(f"\nSaved {len(all_places)} places to {out_path}")


if __name__ == "__main__":
    app()
