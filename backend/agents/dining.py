"""DiningAgent — finds restaurants matching cuisine preferences and budget."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.tools.places import get_place_details, search_places

_PROMPT = (Path(__file__).parent / "prompts" / "dining.md").read_text()


def build_dining_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="DiningAgent",
        model=model,
        instructions=_PROMPT,
        tools=[search_places, get_place_details],
    )
