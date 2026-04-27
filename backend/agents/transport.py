"""TransportAgent — finds inter-city transport options between consecutive city legs."""

from __future__ import annotations

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.tools.transport import get_intercity_transport

_PROMPT = """# Transport Agent

You find the best inter-city transport options between two cities.

## Process
1. Call get_intercity_transport with the origin and destination city names.
2. Return the result as a concise summary for the solver to use.

## Output format
Return a short summary like:
"[Origin] → [Destination]: recommended [mode], ~[duration]h, ~$[low]-$[high]/person. [Notes]."

Include all available options if there are multiple, ordered by recommendation.
"""


def build_transport_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="TransportAgent",
        model=model,
        instructions=_PROMPT,
        tools=[get_intercity_transport],
    )
