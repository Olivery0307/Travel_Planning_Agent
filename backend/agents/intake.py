"""IntakeAgent — parses free-text trip requests into TripRequest objects."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.models.request import TripRequest  # noqa: F401 — used in prompt reference

_PROMPT = (Path(__file__).parent / "prompts" / "intake.md").read_text()


def build_intake_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="IntakeAgent",
        model=model,
        instructions=_PROMPT,
        output_type=TripRequest,
    )
