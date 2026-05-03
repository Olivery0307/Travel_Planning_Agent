"""
Replanner eval — injects a starting itinerary into session state, sends a disruption
message, and scores the ItineraryDelta returned by the replanner.

Usage:
    uv run python backend/tests/evals/run_replan_eval.py
    uv run python backend/tests/evals/run_replan_eval.py --case venue_closed_no_locks
    uv run python backend/tests/evals/run_replan_eval.py --verbose

Requires: ORCHESTRATOR_MODEL, SPECIALIST_MODEL, GOOGLE_PLACES_API_KEY in .env

How it works:
  1. For each scenario, create a fresh AppContext and pre-populate itinerary_json
     with the scenario's starting_itinerary (or the shared default).
  2. Create a fresh InMemorySession with an initial assistant turn containing the
     itinerary (so the orchestrator sees it as an existing plan).
  3. Send the disruption_message as the user turn.
  4. Capture the ItineraryDelta from AppContext.pending_delta (or from the delta
     returned in ChatResponse if using the /chat endpoint directly).
  5. Score against the scenario's checks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from agents import Runner
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

ROOT = Path(__file__).parents[4]
sys.path.insert(0, str(ROOT))

# Import after sys.path is set
os.environ.setdefault("ORCHESTRATOR_MODEL", "vertex_ai/gemini-2.5-flash")
os.environ.setdefault("SPECIALIST_MODEL", "vertex_ai/gemini-2.0-flash")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "unused")

from backend.agents.orchestrator import build_orchestrator  # noqa: E402
from backend.guardrails.input_validation import (  # noqa: E402
    budget_sanity_guardrail,
    init_guardrail_agents,
    off_topic_guardrail,
)
from backend.models.disruption import ItineraryDelta  # noqa: E402

# Re-use main.py session management
sys.path.insert(0, str(ROOT))
import main as _main  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_disruptions.json"

# The shared default starting itinerary (Rome 5-day)
_DEFAULT_ITINERARY: str | None = None


def _get_default_itinerary(cases: list[dict]) -> str:
    """Find and return the shared starting_itinerary from the first comment entry."""
    for c in cases:
        if "_comment" in c and "starting_itinerary" in c and "id" not in c:
            return c["starting_itinerary"]
    raise ValueError("No shared starting_itinerary found in golden_disruptions.json")


# ── Session injection ──────────────────────────────────────────────────────────

def _inject_itinerary(session_id: str, itinerary_text: str, locked_slots: list[str]) -> "_main.AppContext":
    """
    Create an AppContext pre-loaded with the starting itinerary so the orchestrator
    sees an existing plan and routes to the replanner.
    """
    ctx = _main.AppContext(session_id=session_id)
    ctx.itinerary_json = json.dumps({"text": itinerary_text, "version": 1})
    ctx.locked_slots = locked_slots
    ctx.disruption_count = 1  # signals that a plan exists
    _main._context_store[session_id] = ctx
    return ctx


def _prime_session(session_id: str, itinerary_text: str) -> None:
    """
    Pre-populate the InMemorySession with an assistant message containing the itinerary.
    This makes the orchestrator's _make_instructions see it in conversation history.
    """
    from agents.items import TResponseInputItem
    _main._sessions_store[session_id] = [
        {
            "role": "user",
            "content": "Plan a 5-day Rome trip for a couple, $200/day, ancient history."
        },
        {
            "role": "assistant",
            "content": itinerary_text,
        },
    ]


# ── Scoring ────────────────────────────────────────────────────────────────────

def _score_delta(
    delta: ItineraryDelta | None,
    response: str,
    checks: dict,
    locked_slots: list[str],
) -> tuple[int, int, list[str]]:
    passed, total = 0, 0
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, total
        total += 1
        if ok:
            passed += 1
        else:
            failures.append(f"  FAIL [{name}]{': ' + detail if detail else ''}")

    # 1. Delta must be non-empty (replanner was triggered)
    if checks.get("delta_non_empty", True):
        check("delta_returned", delta is not None,
              "no ItineraryDelta found — replanner may not have fired")

    if delta is None:
        return passed, total, failures

    # 2. Reasoning must be non-empty
    if checks.get("reasoning_non_empty", True):
        check("reasoning", bool(delta.reasoning and delta.reasoning.strip()),
              "reasoning field is empty")

    # 3. changed_slots or removed_slots must be non-empty
    if checks.get("changed_or_removed_non_empty", True):
        has_changes = bool(delta.changed_slots or delta.removed_slots)
        check("has_changes", has_changes,
              f"changed={len(delta.changed_slots)} removed={len(delta.removed_slots)}")

    # 4. affected_days must contain expected days
    for day in checks.get("affected_days_contains", []):
        check(f"affected_day:{day}",
              day in delta.affected_days,
              f"Day {day} not in affected_days {delta.affected_days}")

    # 5. Lock integrity — locked slots must NOT appear in changed/removed
    all_changed_keys = {
        f"day{s.day_number}_{s.period}" for s in delta.changed_slots
    } | {
        f"day{s.day_number}_{s.period}" for s in delta.removed_slots
    }
    for locked in checks.get("locked_slots_not_touched", []):
        check(f"lock_integrity:{locked}",
              locked not in all_changed_keys,
              f"locked slot '{locked}' was modified by replanner")

    # 6. Hardcoded lock check from the scenario's actual locked_slots list
    for locked in locked_slots:
        check(f"lock_integrity:{locked}",
              locked not in all_changed_keys,
              f"locked slot '{locked}' was modified")

    # 7. Days not in affected_days should not appear in changed/removed
    not_touched_days_str = checks.get("must_not_appear_in_changed", [])
    for day_str in not_touched_days_str:
        day_m = re.search(r'Day (\d+)', day_str)
        if day_m:
            d = int(day_m.group(1))
            if d not in delta.affected_days:
                touched = any(s.day_number == d for s in delta.changed_slots + delta.removed_slots)
                check(f"surgical_edit:day{d}_untouched", not touched,
                      f"Day {d} was modified but not in affected_days")

    # 8. Response text checks
    for kw in checks.get("response_must_contain", []):
        check(f"response_contains:{kw}", kw.lower() in response.lower())

    return passed, total, failures


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

    cases_raw = json.loads(GOLDEN.read_text())
    default_itinerary = _get_default_itinerary(cases_raw)

    # Filter to actual scenario entries only
    cases = [c for c in cases_raw if "id" in c]

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
        print(f"Disruption: {case['disruption_message'][:90]}...")

        session_id = f"eval-replan-{uuid.uuid4().hex[:8]}"
        itinerary = case.get("starting_itinerary", default_itinerary)
        locked = case.get("locked_slots", [])

        # Inject state
        ctx = _inject_itinerary(session_id, itinerary, locked)
        _prime_session(session_id, itinerary)
        session = _main.InMemorySession(session_id)

        t0 = time.time()
        try:
            ctx.pending_delta = ""  # clear before run
            result = await Runner.run(
                agent,
                case["disruption_message"],
                session=session,
                context=ctx,
                max_turns=20,
            )
            response = result.final_output

            # Try to get delta from AppContext (set by store_delta tool)
            delta: ItineraryDelta | None = None
            if ctx.pending_delta:
                try:
                    delta = ItineraryDelta.model_validate_json(ctx.pending_delta)
                except Exception:
                    pass

            # Fallback: validate_delta from main
            if delta is None:
                validated = _main._validate_delta(ctx)
                if validated:
                    try:
                        delta = ItineraryDelta.model_validate(validated)
                    except Exception:
                        pass

        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append({"id": case["id"], "status": "error", "error": str(exc)})
            continue
        elapsed = time.time() - t0

        passed, total, failures = _score_delta(delta, response, case.get("checks", {}), locked)
        total_passed += passed
        total_checks += total

        delta_summary = (
            f"changed={len(delta.changed_slots)} removed={len(delta.removed_slots)} "
            f"affected_days={delta.affected_days}"
        ) if delta else "no delta"

        status = "PASS" if not failures else f"PARTIAL ({passed}/{total})"
        print(f"Status: {status}  |  Time: {elapsed:.1f}s")
        print(f"Delta: {delta_summary}")
        for f in failures:
            print(f)

        if verbose and delta:
            print(f"\n  Reasoning: {delta.reasoning[:200]}")
            print(f"  Response (200 chars): {response[:200]}")

        results.append({
            "id": case["id"],
            "status": "pass" if not failures else "partial",
            "passed": passed,
            "total": total,
            "elapsed_s": round(elapsed, 1),
            "failures": failures,
            "delta_summary": delta_summary,
        })

    print(f"\n{'='*60}")
    print(f"Overall: {total_passed}/{total_checks} checks passed "
          f"({100 * total_passed // max(total_checks, 1)}%)")

    lock_checks = [
        r for r in results
        for f in r.get("failures", [])
        if "lock_integrity" in f
    ]
    surgical_fails = [
        r for r in results
        for f in r.get("failures", [])
        if "surgical_edit" in f
    ]

    print(f"\nCases: {len(results)}  |  "
          f"Full pass: {sum(1 for r in results if r.get('status') == 'pass')}  |  "
          f"Partial: {sum(1 for r in results if r.get('status') == 'partial')}  |  "
          f"Error: {sum(1 for r in results if r.get('status') == 'error')}")
    print(f"Lock violations: {len(lock_checks)}  |  "
          f"Surgical edit failures: {len(surgical_fails)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run replanner evals")
    parser.add_argument("--case", help="Run only the case with this id")
    parser.add_argument("--verbose", action="store_true", help="Print delta reasoning + response")
    args = parser.parse_args()
    asyncio.run(run_evals(filter_id=args.case, verbose=args.verbose))
