"""Input guardrails — off-topic filter, budget sanity check, destination validation."""

from __future__ import annotations

import re

from agents import Agent, GuardrailFunctionOutput, RunContextWrapper, input_guardrail
from agents.extensions.models.litellm_model import LitellmModel
from pydantic import BaseModel


class RelevanceCheck(BaseModel):
    is_travel_related: bool
    reasoning: str


class BudgetCheck(BaseModel):
    budget_mentioned: bool
    budget_per_day_usd: float | None
    is_reasonable: bool
    reasoning: str


_relevance_agent: Agent | None = None
_budget_agent: Agent | None = None


def init_guardrail_agents(model: LitellmModel) -> None:
    global _relevance_agent, _budget_agent

    _relevance_agent = Agent(
        name="RelevanceGuardrail",
        model=model,
        instructions=(
            "Determine if the user's message is related to travel planning, trip itineraries, "
            "destinations, accommodations, activities, or disruption handling. "
            "Return is_travel_related=False for off-topic requests like coding help, jokes, math problems, etc."
        ),
        output_type=RelevanceCheck,
    )

    _budget_agent = Agent(
        name="BudgetGuardrail",
        model=model,
        instructions=(
            "If the user mentions a daily budget, extract it and check if it is reasonable for travel. "
            "Reject budgets below $20/day or above $10,000/day per person as unreasonable. "
            "If no budget is mentioned, set budget_mentioned=False and is_reasonable=True."
        ),
        output_type=BudgetCheck,
    )


_TRAVEL_KEYWORDS = re.compile(
    r"\b(trip|travel|itinerary|hotel|flight|city|day|night|visit|tour|plan|book|"
    r"restaurant|museum|budget|airport|train|bus|ferry|tickets?|activities|attractions?|"
    r"accommodation|hostel|resort|beach|hike|sightseeing|vacation|holiday|passport|visa|"
    r"morning|afternoon|evening|week|weekend|days?|nights?)\b",
    re.IGNORECASE,
)


def _input_as_text(input: str | list) -> str:
    """Normalize SDK input to a plain string for regex checks."""
    if isinstance(input, str):
        return input
    # list of message dicts — extract text content from the last user message
    for item in reversed(input):
        if isinstance(item, dict):
            content = item.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
    return ""


@input_guardrail
async def off_topic_guardrail(
    ctx: RunContextWrapper, agent: Agent, input: str
) -> GuardrailFunctionOutput:
    """Reject messages unrelated to travel planning."""
    text = _input_as_text(input)
    # Fast-pass: if the message contains obvious travel keywords, skip the LLM call
    if _TRAVEL_KEYWORDS.search(text):
        return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)

    if _relevance_agent is None:
        return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)
    from agents import Runner
    result = await Runner.run(_relevance_agent, input, context=ctx.context)
    check: RelevanceCheck = result.final_output
    return GuardrailFunctionOutput(
        output_info=check,
        tripwire_triggered=not check.is_travel_related,
    )


@input_guardrail
async def budget_sanity_guardrail(
    ctx: RunContextWrapper, agent: Agent, input: str
) -> GuardrailFunctionOutput:
    """Reject impossible budgets (<$20/day or >$10k/day).
    Skipped when an itinerary already exists — mid-trip budget changes are valid replan requests.
    """
    if _budget_agent is None:
        return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)
    # If an itinerary exists this is a replan/followup — don't second-guess the budget
    if ctx.context and getattr(ctx.context, "itinerary_json", None):
        return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)
    from agents import Runner
    result = await Runner.run(_budget_agent, input, context=ctx.context)
    check: BudgetCheck = result.final_output
    return GuardrailFunctionOutput(
        output_info=check,
        tripwire_triggered=not check.is_reasonable,
    )
