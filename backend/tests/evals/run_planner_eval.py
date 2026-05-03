"""
Full planner eval — runs the orchestrator end-to-end and scores the itinerary output.

Usage:
    uv run python backend/tests/evals/run_planner_eval.py
    uv run python backend/tests/evals/run_planner_eval.py --case rome_single_city_cached
    uv run python backend/tests/evals/run_planner_eval.py --verbose

Requires: ORCHESTRATOR_MODEL, SPECIALIST_MODEL, GOOGLE_PLACES_API_KEY in .env
Skips: cases whose required cities have no cached data (avoids live Places API costs).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

from agents import Runner
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parents[4]
sys.path.insert(0, str(ROOT))

from backend.agents.orchestrator import build_orchestrator  # noqa: E402
from backend.guardrails.input_validation import (  # noqa: E402
    budget_sanity_guardrail,
    init_guardrail_agents,
    off_topic_guardrail,
)

GOLDEN = Path(__file__).parent / "golden_trips_full.json"

# Cities with seeded cache data — safe to test without live Places API
CACHED_CITIES = {"rome", "florence", "bangkok", "lisbon", "porto", "algarve"}


# ── Scoring helpers ────────────────────────────────────────────────────────────

def _count_day_headers(text: str) -> int:
    return len(re.findall(r'^\*\*Day\s+\d+', text, re.MULTILINE))


def _count_cost_lines(text: str) -> int:
    return len(re.findall(r'💰', text))


def _has_maps_links(text: str) -> bool:
    return bool(re.search(r'\[🗺 Navigate Day', text))


def _has_city_banners(text: str) -> bool:
    return bool(re.search(r'🏙.*\*\*.*\*\*.*Days?', text))


def _has_transport_day(text: str) -> bool:
    return bool(re.search(r'[🚆✈️🚌⛴🚗].*Travel', text))


def _has_weather_badges(text: str) -> bool:
    return bool(re.search(r'[☀️🌤⛅☁🌦🌧🌨❄⛈🌫].*°C', text))


def _restaurant_names(text: str) -> list[str]:
    """Extract restaurant names from lunch/dinner lines."""
    names = []
    for line in text.splitlines():
        m = re.search(
            r'(?:Lunch|Dinner|🍽)[:\s–—]+(?:at\s+)?([^(~\[]+?)(?:\s*[(\[~]|$)',
            line, re.IGNORECASE
        )
        if m:
            name = m.group(1).strip().rstrip(',.')
            if len(name) > 2:
                names.append(name.lower())
    return names


def _score_case(response: str, checks: dict) -> tuple[int, int, list[str]]:
    passed, total = 0, 0
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, total
        total += 1
        if ok:
            passed += 1
        else:
            failures.append(f"  FAIL [{name}]{': ' + detail if detail else ''}")

    struct = checks.get("structure", {})

    # Day count
    if "min_days" in struct or "max_days" in struct:
        n = _count_day_headers(response)
        min_d = struct.get("min_days", 0)
        max_d = struct.get("max_days", 999)
        check("day_count", min_d <= n <= max_d,
              f"expected {min_d}–{max_d} days, got {n}")

    # Cost lines
    if struct.get("requires_cost_lines"):
        n = _count_cost_lines(response)
        check("cost_lines", n >= struct.get("min_days", 1),
              f"only {n} 💰 lines found")

    # Maps links
    if struct.get("requires_maps_links"):
        check("maps_links", _has_maps_links(response), "no Navigate Day links found")

    # City banners (multi-city)
    if struct.get("requires_city_banners"):
        check("city_banners", _has_city_banners(response), "no 🏙 city headers found")

    # Transport day
    if struct.get("requires_transport_day"):
        check("transport_day", _has_transport_day(response), "no travel emoji + Travel line")

    # Must-include places
    for place in checks.get("must_include_places", []):
        check(f"place:{place}", place.lower() in response.lower(),
              f"'{place}' not found in itinerary")

    # No restaurant repeats
    if checks.get("must_not_repeat_restaurants"):
        names = _restaurant_names(response)
        seen: set[str] = set()
        dupes = []
        for n in names:
            if n in seen:
                dupes.append(n)
            seen.add(n)
        check("no_restaurant_repeats", len(dupes) == 0,
              f"repeated: {dupes[:3]}")

    # Expected cities (multi-city)
    for city in checks.get("expected_cities", []):
        check(f"city:{city}", city.lower() in response.lower(),
              f"'{city}' not mentioned in itinerary")

    # Response must contain
    for kw in checks.get("response_must_contain", []):
        check(f"contains:{kw}", kw.lower() in response.lower())

    # Response must NOT contain
    for kw in checks.get("response_must_not_contain", []):
        check(f"not_contains:{kw}", kw.lower() not in response.lower())

    # Weather badges
    if checks.get("weather_badge_expected") is True:
        check("weather_badges", _has_weather_badges(response),
              "no weather icon + °C found in day headers")
    elif checks.get("weather_badge_expected") is False:
        check("no_weather_badges", not _has_weather_badges(response),
              "unexpected weather badges found (no date given)")

    return passed, total, failures


def _cities_in_input(text: str) -> list[str]:
    """Rough extraction of city names from input string."""
    words = re.findall(r'\b([A-Z][a-z]+)\b', text)
    return [w.lower() for w in words]


def _uses_cached_cities_only(input_text: str) -> bool:
    """Return True if all likely destination cities in the input have cached data."""
    found = _cities_in_input(input_text)
    destinations = [w for w in found if w in CACHED_CITIES]
    return len(destinations) > 0


# ── Main runner ────────────────────────────────────────────────────────────────

async def run_evals(filter_id: str | None = None, verbose: bool = False) -> None:
    orch_model_str = os.environ.get("ORCHESTRATOR_MODEL", "vertex_ai/gemini-2.5-flash")
    spec_model_str = os.environ.get("SPECIALIST_MODEL", "vertex_ai/gemini-2.0-flash")

    orch_model = LitellmModel(model=orch_model_str, api_key="unused")
    spec_model  = LitellmModel(model=spec_model_str, api_key="unused")

    init_guardrail_agents(spec_model)
    agent = build_orchestrator(
        orch_model, spec_model,
        input_guardrails=[off_topic_guardrail, budget_sanity_guardrail],
    )

    cases = json.loads(GOLDEN.read_text())
    # Strip comment entries
    cases = [c for c in cases if "id" in c]

    if filter_id:
        cases = [c for c in cases if c["id"] == filter_id]
        if not cases:
            print(f"No case with id={filter_id!r}")
            return

    total_passed = 0
    total_checks = 0
    results = []

    for case in cases:
        print(f"\n{'='*60}")
        print(f"Case: {case['id']}")
        print(f"Input: {case['input'][:90]}...")

        if not _uses_cached_cities_only(case["input"]):
            print("SKIP — no cached data for destination (would incur live API cost)")
            continue

        t0 = time.time()
        try:
            result = await Runner.run(agent, case["input"], max_turns=30)
            response = result.final_output
        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append({"id": case["id"], "status": "error", "error": str(exc)})
            continue
        elapsed = time.time() - t0

        passed, total, failures = _score_case(response, case.get("checks", {}))
        total_passed += passed
        total_checks += total

        status = "PASS" if not failures else f"PARTIAL ({passed}/{total})"
        print(f"Status: {status}  |  Time: {elapsed:.1f}s")
        for f in failures:
            print(f)

        if verbose:
            print("\n--- Response (first 600 chars) ---")
            print(response[:600])
            print("---")

        results.append({
            "id": case["id"],
            "status": "pass" if not failures else "partial",
            "passed": passed,
            "total": total,
            "elapsed_s": round(elapsed, 1),
            "failures": failures,
        })

    print(f"\n{'='*60}")
    print(f"Overall: {total_passed}/{total_checks} checks passed "
          f"({100 * total_passed // max(total_checks, 1)}%)")
    print(f"Cases run: {len(results)}  |  "
          f"Full pass: {sum(1 for r in results if r.get('status') == 'pass')}  |  "
          f"Partial: {sum(1 for r in results if r.get('status') == 'partial')}  |  "
          f"Error: {sum(1 for r in results if r.get('status') == 'error')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run full planner evals")
    parser.add_argument("--case", help="Run only the case with this id")
    parser.add_argument("--verbose", action="store_true", help="Print first 600 chars of each response")
    args = parser.parse_args()
    asyncio.run(run_evals(filter_id=args.case, verbose=args.verbose))
