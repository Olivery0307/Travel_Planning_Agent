"""OrchestratorAgent — owns the conversation and delegates to specialists."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.agents.activity import build_activity_agent
from backend.agents.dining import build_dining_agent
from backend.agents.intake import build_intake_agent
from backend.agents.lodging import build_lodging_agent
from backend.agents.replanner import build_replanner_agent
from backend.agents.solver import build_solver_agent
from backend.agents.transport import build_transport_agent

_PROMPT = (Path(__file__).parent / "prompts" / "orchestrator.md").read_text()


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
    transport = build_transport_agent(specialist_model)

    return Agent(
        name="OrchestratorAgent",
        model=orchestrator_model,
        instructions=_PROMPT,
        input_guardrails=input_guardrails or [],
        tools=[
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
