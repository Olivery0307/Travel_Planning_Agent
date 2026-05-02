"""Context-writing tools available to the OrchestratorAgent."""

from __future__ import annotations

import json
import re

from agents import RunContextWrapper, function_tool


@function_tool
def store_delta(ctx: RunContextWrapper, replanner_output: str) -> str:
    """Store the ItineraryDelta from the replanner_agent output.
    Call this immediately after replanner_agent returns, passing its full text output.
    Extracts the JSON block automatically.
    """
    if ctx.context is None:
        return "No context available — delta not stored."

    # Extract JSON from a ```json ... ``` code block or bare JSON object
    json_str = replanner_output
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", replanner_output, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        # Try to find a bare JSON object
        obj_match = re.search(r"\{.*\}", replanner_output, re.DOTALL)
        if obj_match:
            json_str = obj_match.group(0)

    # Validate it parses as JSON
    try:
        json.loads(json_str)
    except Exception as e:
        return f"Failed to parse delta JSON: {e}. Raw output: {replanner_output[:200]}"

    ctx.context.pending_delta = json_str
    ctx.context.save()
    return "Delta stored successfully."
