"""RePlannerAgent — deterministic four-step replan pipeline."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.agents.disruption_parser import build_disruption_parser
from backend.tools.replan_tools import (
    apply_swap,
    find_candidates_parallel,
    parse_disruption,
    resolve_slots,
)
import backend.tools.replan_tools as _rt

_PROMPT = (Path(__file__).parent / "prompts" / "replanner.md").read_text()


def build_replanner_agent(model: LitellmModel) -> Agent:
    # Inject parser so parse_disruption tool can call it
    _rt._disruption_parser_agent = build_disruption_parser(model)

    return Agent(
        name="RePlannerAgent",
        model=model,
        instructions=_PROMPT,
        tools=[parse_disruption, resolve_slots, find_candidates_parallel, apply_swap],
    )
