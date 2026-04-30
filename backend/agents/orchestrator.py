"""OrchestratorAgent — owns the conversation and delegates to specialists."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Agent, RunContextWrapper
from agents.extensions.models.litellm_model import LitellmModel

from backend.agents.activity import build_activity_agent
from backend.agents.dining import build_dining_agent
from backend.agents.intake import build_intake_agent
from backend.agents.lodging import build_lodging_agent
from backend.agents.replanner import build_replanner_agent
from backend.agents.solver import build_solver_agent
from backend.tools.context_tools import store_delta
from backend.tools.weather import get_weather_forecast

_BASE_PROMPT = (Path(__file__).parent / "prompts" / "orchestrator.md").read_text()


def _make_instructions(ctx: RunContextWrapper, agent: Agent) -> str:
    """Inject current itinerary and locked slots into the orchestrator system prompt each turn."""
    import json
    prompt = _BASE_PROMPT

    if ctx.context and ctx.context.itinerary_json:
        try:
            itin = json.loads(ctx.context.itinerary_json)
            itin_text = itin.get("text", "")
            if itin_text:
                prompt += (
                    "\n\n## Current Itinerary (pass this VERBATIM to replanner_agent)\n"
                    + itin_text
                )
        except Exception:
            pass

    if ctx.context and ctx.context.locked_slots:
        locked_list = "\n".join(f"- {s}" for s in ctx.context.locked_slots)
        prompt += (
            "\n\n## Advisor-Locked Slots (NEVER modify in re-planning)\n"
            "Pass this list verbatim to replanner_agent:\n"
            + locked_list
        )
    return prompt


def build_orchestrator(
    orchestrator_model: LitellmModel,
    specialist_model: LitellmModel,
    input_guardrails: list[Any] | None = None,
) -> Agent:
    intake = build_intake_agent(specialist_model)
    lodging = build_lodging_agent(specialist_model)
    activity = build_activity_agent(specialist_model)
    dining = build_dining_agent(specialist_model)
    solver = build_solver_agent(specialist_model)
    replanner = build_replanner_agent(specialist_model)

    return Agent(
        name="OrchestratorAgent",
        model=orchestrator_model,
        instructions=_make_instructions,
        input_guardrails=input_guardrails or [],
        tools=[
            store_delta,
            get_weather_forecast,
            intake.as_tool(
                tool_name="intake_agent",
                tool_description="Parse a free-text trip request into a structured TripRequest. Call first on any new planning request.",
            ),
            lodging.as_tool(
                tool_name="lodging_agent",
                tool_description="Find hotel/lodging options for the trip. Call after intake_agent.",
            ),
            activity.as_tool(
                tool_name="activity_agent",
                tool_description="Find attractions and activities matching trip interests. Call after intake_agent.",
            ),
            dining.as_tool(
                tool_name="dining_agent",
                tool_description="Find restaurants matching cuisine preferences and budget. Call after intake_agent.",
            ),
            solver.as_tool(
                tool_name="solver_agent",
                tool_description="Sequence all candidates into a valid day-by-day itinerary. Call after lodging, activity, and dining agents have returned results.",
            ),
            replanner.as_tool(
                tool_name="replanner_agent",
                tool_description="Re-optimize affected days after a disruption. Call when the advisor reports a mid-trip change. Pass the current itinerary and disruption description.",
            ),
        ],
    )
