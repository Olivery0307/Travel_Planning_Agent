"""ConversationAgent — answers questions, analyses the itinerary, and proposes changes."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.tools.places import get_place_details, search_places
from backend.tools.routing import compute_route_matrix
from backend.tools.weather import get_weather_forecast

_PROMPT = (Path(__file__).parent / "prompts" / "conversation.md").read_text()


def build_conversation_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="ConversationAgent",
        model=model,
        instructions=_PROMPT,
        tools=[get_weather_forecast, search_places, get_place_details, compute_route_matrix],
    )
