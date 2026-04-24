"""Evaluation runner — scores IntakeAgent against golden_trips.json."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from agents import Runner
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parents[4]))

from backend.agents.intake import build_intake_agent  # noqa: E402
from backend.models.request import TripRequest  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_trips.json"


def _check(actual: dict, expected: dict) -> tuple[int, int, list[str]]:
    passed = 0
    total = 0
    failures = []
    for key, expected_val in expected.items():
        if key == "disruption":
            continue
        total += 1
        keys = key.split(".")
        val = actual
        for k in keys:
            val = val.get(k) if isinstance(val, dict) else getattr(val, k, None)
        if isinstance(expected_val, list):
            if val and all(ev.lower() in str(val).lower() for ev in expected_val):
                passed += 1
            else:
                failures.append(f"  FAIL {key}: expected {expected_val!r}, got {val!r}")
        elif isinstance(expected_val, str):
            if val and expected_val.lower() in str(val).lower():
                passed += 1
            else:
                failures.append(f"  FAIL {key}: expected {expected_val!r}, got {val!r}")
        else:
            if val == expected_val:
                passed += 1
            else:
                failures.append(f"  FAIL {key}: expected {expected_val!r}, got {val!r}")
    return passed, total, failures


async def run_evals() -> None:
    model_str = os.environ.get("SPECIALIST_MODEL", os.environ.get("MODEL", "vertex_ai/gemini-2.0-flash"))
    model = LitellmModel(model=model_str, api_key="unused")
    agent = build_intake_agent(model)

    cases = json.loads(GOLDEN.read_text())
    total_passed = 0
    total_checks = 0

    for case in cases:
        if case.get("expected_criteria", {}).get("disruption"):
            print(f"SKIP (disruption case): {case['id']}")
            continue

        print(f"\n{'='*50}")
        print(f"Case: {case['id']}")
        print(f"Input: {case['input'][:80]}...")

        result = await Runner.run(agent, case["input"])
        actual: TripRequest = result.final_output
        actual_dict = actual.model_dump()

        passed, total, failures = _check(actual_dict, case["expected_criteria"])
        total_passed += passed
        total_checks += total

        status = "PASS" if not failures else "PARTIAL"
        print(f"Result: {status} ({passed}/{total})")
        for f in failures:
            print(f)

    print(f"\n{'='*50}")
    print(f"Overall: {total_passed}/{total_checks} checks passed ({100*total_passed//max(total_checks,1)}%)")


if __name__ == "__main__":
    asyncio.run(run_evals())
