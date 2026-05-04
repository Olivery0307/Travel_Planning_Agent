"""ActivityAgent — finds attractions and experiences matching trip interests."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.tools.places import get_opening_hours, get_place_details, search_places

_PROMPT = (Path(__file__).parent / "prompts" / "activity.md").read_text()


def build_activity_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="ActivityAgent",
        model=model,
        instructions=_PROMPT,
        tools=[search_places, get_place_details, get_opening_hours],
    )
