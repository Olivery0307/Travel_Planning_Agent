"""IntakeAgent — parses free-text trip requests into TripRequest objects."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from agents import Agent, RunContextWrapper
from agents.extensions.models.litellm_model import LitellmModel

from backend.models.request import TripRequest  # noqa: F401 — used in prompt reference

_BASE_PROMPT = (Path(__file__).parent / "prompts" / "intake.md").read_text()


def _make_instructions(ctx: RunContextWrapper, agent: Agent) -> str:
    today = date.today()
    return f"Today's date is {today.strftime('%B %d, %Y')} ({today.isoformat()}). Use this to resolve relative dates like 'May 15' (→ {today.year}-05-15), 'next Monday', 'in 2 weeks', etc.\n\n" + _BASE_PROMPT


def build_intake_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="IntakeAgent",
        model=model,
        instructions=_make_instructions,
        output_type=TripRequest,
    )
