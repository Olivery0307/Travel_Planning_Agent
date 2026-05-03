"""ConversationAgent — answers questions, analyses the itinerary, and proposes changes."""

from __future__ import annotations

import json
from pathlib import Path

from agents import Agent, RunContextWrapper
from agents.extensions.models.litellm_model import LitellmModel

from backend.tools.places import get_place_details, search_places
from backend.tools.routing import compute_route_matrix
from backend.tools.weather import get_weather_forecast

_BASE_PROMPT = (Path(__file__).parent / "prompts" / "conversation.md").read_text()


def _make_instructions(ctx: RunContextWrapper, agent: Agent) -> str:
    """Inject itinerary, weather, and trip context so the agent never has to ask."""
    prompt = _BASE_PROMPT

    if ctx.context and ctx.context.itinerary_json:
        try:
            itin = json.loads(ctx.context.itinerary_json)
            itin_text = itin.get("text", "")
            if itin_text:
                prompt += "\n\n## Current Itinerary\n" + itin_text
        except Exception:
            pass

    if ctx.context and ctx.context.weather_data:
        try:
            weather = json.loads(ctx.context.weather_data)
            if weather:
                prompt += "\n\n## Weather Forecast\n"
                for i, day in enumerate(weather, 1):
                    cond = day.get("condition", "")
                    temp = day.get("temp_high_c")
                    low = day.get("temp_low_c")
                    icon = day.get("icon", "")
                    rain = day.get("precipitation_probability", 0)
                    outdoor = "outdoor OK" if day.get("outdoor_suitable") else "prefer indoor"
                    temp_str = f", {temp}°C high / {low}°C low" if temp is not None else ""
                    prompt += f"- Day {i}: {icon} {cond}{temp_str}, rain {rain}%, {outdoor}\n"
        except Exception:
            pass

    if ctx.context and ctx.context.last_city:
        summary = f"\n\n## Current Trip Context\n- City: {ctx.context.last_city}"
        if ctx.context.last_country_code:
            summary += f" ({ctx.context.last_country_code})"
        if ctx.context.last_checkin:
            summary += f"\n- Start date: {ctx.context.last_checkin}"
        if ctx.context.last_nights:
            summary += f"\n- Duration: {ctx.context.last_nights} nights"
        prompt += summary

    return prompt


def build_conversation_agent(model: LitellmModel) -> Agent:
    return Agent(
        name="ConversationAgent",
        model=model,
        instructions=_make_instructions,
        tools=[get_weather_forecast, search_places, get_place_details, compute_route_matrix],
    )
