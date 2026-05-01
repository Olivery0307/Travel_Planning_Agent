"""LodgingAgent — finds hotels/B&Bs matching budget and location."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.tools.places import get_place_details, search_places
from backend.tools.tavily_search import search_booking_url

_PROMPT = (Path(__file__).parent / "prompts" / "lodging.md").read_text()


def build_lodging_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="LodgingAgent",
        model=model,
        instructions=_PROMPT,
        tools=[search_places, get_place_details, search_booking_url],
    )
