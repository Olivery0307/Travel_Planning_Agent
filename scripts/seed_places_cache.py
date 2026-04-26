"""One-shot script: pre-fetch Google Places data for a city and save to JSON cache.

Fetches search results + full place details (opening hours, photos, coordinates)
so the planner never needs to make live API calls for seeded cities.

Usage:
    uv run python scripts/seed_places_cache.py --city rome
    uv run python scripts/seed_places_cache.py --city paris
    uv run python scripts/seed_places_cache.py --city rome --upload-gcs --bucket my-bucket
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import googlemaps
import typer
from dotenv import load_dotenv

load_dotenv()

# Searches to run per city. More queries = better variety and neighborhood coverage.
SEARCHES: list[tuple[str, str]] = [
    # Attractions — broad to narrow
    ("top historical attractions",              "attraction"),
    ("best museums art galleries",              "attraction"),
    ("ancient ruins monuments",                 "attraction"),
    ("UNESCO world heritage sites",             "attraction"),
    ("famous churches cathedrals",              "attraction"),
    ("palaces castles",                         "attraction"),
    ("archaeological sites",                    "attraction"),
    ("public squares fountains",                "attraction"),
    ("scenic viewpoints panoramas",             "attraction"),
    ("parks gardens nature",                    "attraction"),
    ("contemporary art galleries",              "attraction"),
    ("neighborhood walking tours",              "attraction"),
    # Restaurants — by meal type and style
    ("top restaurants local cuisine",           "restaurant"),
    ("breakfast cafes coffee shops",            "restaurant"),
    ("dinner fine dining",                      "restaurant"),
    ("casual lunch trattoria",                  "restaurant"),
    ("street food markets",                     "restaurant"),
    ("vegetarian vegan restaurants",            "restaurant"),
    ("seafood restaurants",                     "restaurant"),
    ("wine bars enoteca",                       "restaurant"),
    ("gelato dessert shops",                    "restaurant"),
    ("rooftop restaurants bars",                "restaurant"),
    ("family friendly restaurants",             "restaurant"),
    ("late night dining",                       "restaurant"),
    # Lodging — by type and price
    ("mid-range hotels central",                "lodging"),
    ("boutique hotels",                         "lodging"),
    ("budget hotels hostels",                   "lodging"),
    ("luxury 5 star hotels",                    "lodging"),
    ("bed and breakfast guesthouse",            "lodging"),
    ("apartment hotels extended stay",          "lodging"),
]

DETAIL_FIELDS = [
    "place_id", "name", "formatted_address", "geometry",
    "rating", "user_ratings_total", "price_level",
    "opening_hours", "photo", "website",
    "formatted_phone_number", "editorial_summary",
    "type", "business_status",
]

app = typer.Typer()


def _fetch_details(client: googlemaps.Client, place_id: str, api_key: str) -> dict:
    """Fetch full place details and return a serializable dict."""
    try:
        raw = client.place(place_id, fields=DETAIL_FIELDS).get("result", {})
        loc = raw.get("geometry", {}).get("location", {})
        hours = raw.get("opening_hours", {})

        photo_urls: list[str] = []
        for photo in raw.get("photos", [])[:3]:
            ref = photo.get("photo_reference")
            if ref:
                # Store without the API key — never embed credentials in cached data.
                photo_urls.append(
                    f"https://maps.googleapis.com/maps/api/place/photo"
                    f"?maxwidth=800&photoreference={ref}"
                )

        return {
            "place_id":            raw.get("place_id", place_id),
            "name":                raw.get("name", ""),
            "address":             raw.get("formatted_address", ""),
            "lat":                 loc.get("lat", 0.0),
            "lng":                 loc.get("lng", 0.0),
            "rating":              raw.get("rating"),
            "user_ratings_total":  raw.get("user_ratings_total"),
            "price_level":         raw.get("price_level"),
            "website":             raw.get("website"),
            "phone":               raw.get("formatted_phone_number"),
            "editorial_summary":   raw.get("editorial_summary", {}).get("overview"),
            "photo_urls":          photo_urls,
            "opening_hours": {
                "open_now":    hours.get("open_now"),
                "weekday_text": hours.get("weekday_text", []),
                "periods":     hours.get("periods", []),
            },
            "types":               raw.get("types", []),
            "business_status":     raw.get("business_status", "OPERATIONAL"),
        }
    except Exception as exc:
        typer.echo(f"  WARNING detail fetch failed for {place_id}: {exc}", err=True)
        return {}


def _upload_to_gcs(local_path: Path, bucket_name: str, city: str) -> None:
    """Upload cache file to GCS bucket at places/{city}.json."""
    try:
        from google.cloud import storage  # type: ignore
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(f"places/{city.lower().replace(' ', '_')}.json")
        blob.upload_from_filename(str(local_path), content_type="application/json")
        typer.echo(f"Uploaded to gs://{bucket_name}/places/{city.lower().replace(' ', '_')}.json")
    except Exception as exc:
        typer.echo(f"WARNING: GCS upload failed: {exc}", err=True)


@app.command()
def main(
    city: str = typer.Option(..., help="City to seed, e.g. 'rome'"),
    output: str = typer.Option("", help="Output JSON path. Defaults to backend/data/places_{city}.json"),
    limit: int = typer.Option(10, help="Max search results per query"),
    delay: float = typer.Option(0.3, help="Seconds between API calls to avoid rate limits"),
    upload_gcs: bool = typer.Option(False, "--upload-gcs", help="Upload result to GCS after seeding"),
    bucket: str = typer.Option("", help="GCS bucket name (required with --upload-gcs)"),
    skip_details: bool = typer.Option(False, "--skip-details", help="Skip detail fetches (faster, less data)"),
) -> None:
    key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        typer.echo("ERROR: GOOGLE_PLACES_API_KEY not set in .env", err=True)
        raise typer.Exit(1)

    client = googlemaps.Client(key=key)
    out_path = Path(output) if output else Path(f"backend/data/places_{city.lower().replace(' ', '_')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing cache to avoid re-fetching
    existing: dict[str, dict] = {}
    if out_path.exists():
        try:
            existing = {p["place_id"]: p for p in json.loads(out_path.read_text())}
            typer.echo(f"Loaded {len(existing)} existing places from {out_path}")
        except Exception:
            pass

    all_places: dict[str, dict] = dict(existing)

    # Pass 1: search queries → collect place_ids + stub data
    typer.echo(f"\n── Pass 1: searching for places in {city} ──")
    for query, category in SEARCHES:
        full_query = f"{query} in {city}"
        typer.echo(f"  {full_query}")
        try:
            raw = client.places(query=full_query)
            for p in raw.get("results", [])[:limit]:
                pid = p["place_id"]
                if pid in all_places and all_places[pid].get("opening_hours"):
                    continue  # already have full details
                loc = p.get("geometry", {}).get("location", {})
                all_places[pid] = {
                    "place_id":           pid,
                    "name":               p.get("name", ""),
                    "address":            p.get("formatted_address", p.get("vicinity", "")),
                    "lat":                loc.get("lat", 0.0),
                    "lng":                loc.get("lng", 0.0),
                    "rating":             p.get("rating"),
                    "user_ratings_total": p.get("user_ratings_total"),
                    "price_level":        p.get("price_level"),
                    "category":           category,
                    "city":               city.lower(),
                }
            time.sleep(delay)
        except Exception as exc:
            typer.echo(f"  WARNING: {exc}", err=True)

    typer.echo(f"\nFound {len(all_places)} unique places")

    # Pass 2: fetch full details for each place
    if not skip_details:
        typer.echo(f"\n── Pass 2: fetching full details ({len(all_places)} places) ──")
        for i, (pid, stub) in enumerate(list(all_places.items())):
            if stub.get("opening_hours"):
                continue  # already detailed
            typer.echo(f"  [{i+1}/{len(all_places)}] {stub.get('name', pid)}")
            details = _fetch_details(client, pid, key)
            if details:
                # merge: keep category/city from stub, fill rest from details
                details["category"] = stub.get("category", "attraction")
                details["city"]     = stub.get("city", city.lower())
                all_places[pid] = details
            time.sleep(delay)

    # Write output
    out_path.write_text(json.dumps(list(all_places.values()), indent=2))
    typer.echo(f"\n✓ Saved {len(all_places)} places to {out_path}")

    # Optional GCS upload
    if upload_gcs:
        if not bucket:
            typer.echo("ERROR: --bucket required with --upload-gcs", err=True)
            raise typer.Exit(1)
        _upload_to_gcs(out_path, bucket, city)


if __name__ == "__main__":
    app()
