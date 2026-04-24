"""RePlannerAgent — re-optimizes affected days after a disruption."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.models.disruption import ItineraryDelta
from backend.tools.places import get_place_details, search_places
from backend.tools.routing import compute_route_matrix

_PROMPT = (Path(__file__).parent / "prompts" / "replanner.md").read_text()


def build_replanner_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="RePlannerAgent",
        model=model,
        instructions=_PROMPT,
        tools=[search_places, get_place_details, compute_route_matrix],
        output_type=ItineraryDelta,
    )
