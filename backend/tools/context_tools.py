"""Context-writing tools available to the OrchestratorAgent."""

from __future__ import annotations

import json
import re

from agents import RunContextWrapper, function_tool


def _extract_outermost_json(text: str) -> str | None:
    """Return the first complete {...} object from text, correctly handling nesting."""
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


@function_tool
def store_delta(ctx: RunContextWrapper, replanner_output: str) -> str:
    """Store the ItineraryDelta from the replanner_agent output.
    Call this immediately after replanner_agent returns, passing its full text output.
    Extracts the JSON block automatically.
    """
    if ctx.context is None:
        return "No context available — delta not stored."

    json_str = _extract_outermost_json(replanner_output)
    if not json_str:
        return f"No JSON object found in replanner output. Raw output: {replanner_output[:200]}"

    try:
        json.loads(json_str)
    except Exception as e:
        return f"Failed to parse delta JSON: {e}. Raw output: {replanner_output[:200]}"

    # Don't overwrite a valid delta already written by the replanner
    if ctx.context.pending_delta:
        try:
            existing = json.loads(ctx.context.pending_delta)
            if existing.get("changed_slots") is not None:
                return "Delta already stored by replanner — skipping overwrite."
        except Exception:
            pass

    ctx.context.pending_delta = json_str
    ctx.context.save()
    return "Delta stored successfully."
