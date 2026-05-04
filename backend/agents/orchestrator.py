"""OrchestratorAgent — owns the conversation and delegates to specialists."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Agent, RunContextWrapper
from agents.extensions.models.litellm_model import LitellmModel

from backend.agents.activity import build_activity_agent
from backend.agents.conversation import build_conversation_agent
from backend.agents.dining import build_dining_agent
from backend.agents.intake import build_intake_agent
from backend.agents.lodging import build_lodging_agent
from backend.agents.replanner import build_replanner_agent
from backend.agents.solver import build_solver_agent
from backend.agents.transport import build_transport_agent
from backend.tools.context_tools import store_delta
from backend.tools.weather import get_weather_forecast

_BASE_PROMPT = (Path(__file__).parent / "prompts" / "orchestrator.md").read_text()


def _make_instructions(ctx: RunContextWrapper, agent: Agent) -> str:
    """Inject current itinerary, weather, and locked slots into the orchestrator prompt each turn."""
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

    # Inject weather so the orchestrator can answer weather questions without a tool call
    if ctx.context and ctx.context.weather_data:
        try:
            weather = json.loads(ctx.context.weather_data)
            if weather:
                prompt += "\n\n## Weather Forecast (already fetched — use this to answer weather questions directly)\n"
                for i, day in enumerate(weather, 1):
                    cond = day.get("condition", "")
                    temp = day.get("temp_high_c")
                    icon = day.get("icon", "")
                    temp_str = f", {temp}°C high" if temp is not None else ""
                    prompt += f"- Day {i}: {icon} {cond}{temp_str}\n"
        except Exception:
            pass

    # Inject trip summary so the orchestrator knows city/dates for follow-up questions
    if ctx.context and ctx.context.last_city:
        summary = f"\n\n## Current Trip Context\n- City: {ctx.context.last_city}"
        if ctx.context.last_country_code:
            summary += f" ({ctx.context.last_country_code})"
        if ctx.context.last_checkin:
            summary += f"\n- Start date: {ctx.context.last_checkin}"
        if ctx.context.last_nights:
            summary += f"\n- Duration: {ctx.context.last_nights} nights"
        prompt += summary

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
    replanner_model: LitellmModel | None = None,
    solver_model: LitellmModel | None = None,
) -> Agent:
    intake = build_intake_agent(specialist_model)
    lodging = build_lodging_agent(specialist_model)
    activity = build_activity_agent(specialist_model)
    dining = build_dining_agent(specialist_model)
    solver = build_solver_agent(solver_model or orchestrator_model)
    replanner = build_replanner_agent(replanner_model or orchestrator_model)
    transport = build_transport_agent(specialist_model)
    conversation = build_conversation_agent(specialist_model)
    return Agent(
        name="OrchestratorAgent",
        model=orchestrator_model,
        instructions=_make_instructions,
        input_guardrails=input_guardrails or [],
        tools=[
            get_weather_forecast,
            store_delta,
            conversation.as_tool(
                tool_name="conversation_agent",
                tool_description=(
                    "Answer questions about the existing itinerary, analyse pacing/budget/weather conflicts, "
                    "and propose targeted improvements. Call this when the user asks a question, wants analysis, "
                    "or is confused — NOT when they are requesting a concrete change to the plan."
                ),
            ),
            intake.as_tool(
                tool_name="intake_agent",
                tool_description="Parse a free-text trip request into a structured TripRequest. Call first on any new planning request.",
            ),
            lodging.as_tool(
                tool_name="lodging_agent",
                tool_description="Find hotel/lodging options for a single city. For multi-city trips, call once per city leg.",
            ),
            activity.as_tool(
                tool_name="activity_agent",
                tool_description="Find attractions and activities for a single city. For multi-city trips, call once per city leg.",
            ),
            dining.as_tool(
                tool_name="dining_agent",
                tool_description="Find restaurants for a single city. For multi-city trips, call once per city leg.",
            ),
            transport.as_tool(
                tool_name="transport_agent",
                tool_description=(
                    "Get inter-city transport options (train, bus, flight, ferry) between two consecutive cities. "
                    "Call for each city transition in a multi-city trip to get travel time and cost for the travel day."
                ),
            ),
            solver.as_tool(
                tool_name="solver_agent",
                tool_description="Sequence all candidates into a valid day-by-day itinerary. Call once after all city candidates are gathered.",
            ),
            replanner.as_tool(
                tool_name="replanner_agent",
                tool_description="Re-optimize affected days after a disruption. Call when the advisor reports a mid-trip change. Pass the current itinerary and disruption description.",
            ),
        ],
    )
