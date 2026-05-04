"""DisruptionParser — structured LLM extraction of what the user wants changed."""

from __future__ import annotations

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from backend.models.disruption import DisruptionRequest

_PROMPT = """You extract structured disruption information from a travel advisor's message.

## Your job
Read the user's message and the current itinerary (provided in context), then populate DisruptionRequest.

## Field guidance

**disruption_type** — pick the best fit:
- venue_closed: a place is closed, unavailable, or the user wants to swap it out
- weather: rain, storm, or weather makes outdoor plans unworkable
- health: someone is sick, injured, or physically limited today
- transit_delay: a train, flight, or transfer is delayed or cancelled
- opportunity: the user got access to something new (tickets, reservation) to insert
- budget_change: the user wants to spend less (or more) going forward
- group_preference_shift: the user simply wants something different (mood, interest change)
- safety: area is unsafe or an emergency has occurred

**affected_slots** — one entry per slot that needs to change.
- For "Day 3 morning Borghese Gallery is closed" → one entry: day=3, period=morning, venue_name="Borghese Gallery", category=activity
- For "sick all of Day 2" → three entries: day=2 morning/afternoon/evening, venue_name="" (whole day)
- For "heavy rain on Day 4" → entries for outdoor slots on Day 4 (morning and afternoon typically)
- For budget/preference changes across multiple days → one entry per affected slot
- For opportunity disruptions (e.g. "I want Statue of Liberty included", "got opera tickets"): EXACTLY ONE entry for the single most appropriate slot, with venue_name set to the NEW venue. Choose the period that best fits the venue (morning for outdoor landmarks, evening for shows/dinners). Never produce multiple slots for an opportunity — the venue is inserted once only.

**locked_slot_keys** — parse "Locked: day1_morning, day2_evening" style text. Format: "day{N}_{period}".

**new_budget_per_day** — only set for budget_change. Extract the number from "$120/day" → 120.0.

**special_instructions** — capture constraints like "indoor only", "max 1km walking", "no stairs", "vegetarian restaurants only".
For `opportunity` disruptions, set special_instructions to the name of the new venue being inserted (e.g. "Teatro dell'Opera") so apply_swap can find it.

**reasoning** — one sentence summary of what happened.

## Rules
- Always produce at least one affected_slot entry.
- If day number is ambiguous, default to day 1.
- If period is ambiguous for health/weather, produce entries for morning, afternoon, AND evening.
- For opportunity: ALWAYS exactly one slot entry, never three. The "whole day" rule does NOT apply to opportunity.
- Never refuse — always return a valid DisruptionRequest.
"""


def build_disruption_parser(model: LitellmModel) -> Agent:
    return Agent(
        name="DisruptionParser",
        model=model,
        instructions=_PROMPT,
        output_type=DisruptionRequest,
    )
