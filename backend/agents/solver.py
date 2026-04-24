"""SolverAgent — sequences candidates into a valid day-by-day itinerary."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.models.itinerary import Itinerary
from backend.tools.routing import compute_route_matrix

_PROMPT = (Path(__file__).parent / "prompts" / "solver.md").read_text()


def build_solver_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="SolverAgent",
        model=model,
        instructions=_PROMPT,
        tools=[compute_route_matrix],
        output_type=Itinerary,
    )
