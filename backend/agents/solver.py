"""SolverAgent — sequences candidates into a valid day-by-day itinerary."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

_PROMPT = (Path(__file__).parent / "prompts" / "solver.md").read_text()


def build_solver_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="SolverAgent",
        model=model,
        instructions=_PROMPT,
        tools=[],
        # No output_type: return a formatted text itinerary.
        # Structured Itinerary storage wired in Week 2 once the demo flow works.
    )
