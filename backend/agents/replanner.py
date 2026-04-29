"""RePlannerAgent — re-optimizes affected days after a disruption."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.tools.context_tools import store_delta
from backend.tools.places import get_place_details, search_places
from backend.tools.pool import get_candidates_from_pool
from backend.tools.routing import compute_route_matrix

_PROMPT = (Path(__file__).parent / "prompts" / "replanner.md").read_text()


def build_replanner_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="RePlannerAgent",
        model=model,
        instructions=_PROMPT,
        tools=[get_candidates_from_pool, search_places, get_place_details, compute_route_matrix, store_delta],
        # No output_type — Gemini strict schema rejects complex nested models.
        # The replanner returns a JSON code block; store_delta parses it.
    )
