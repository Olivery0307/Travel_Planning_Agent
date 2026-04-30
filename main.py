"""AI Travel Itinerary Optimizer — FastAPI server and CLI entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import traceback
import uuid

import typer
import uvicorn
from agents import InputGuardrailTripwireTriggered, Runner
from agents.extensions.models.litellm_model import LitellmModel
from agents.items import TResponseInputItem
from agents.memory.session import SessionABC
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Global LiteLLM retry on 429/503 — fires before the error reaches the SDK.
# 3 retries with exponential backoff: waits 5s, 10s, 20s before each retry.
import litellm  # noqa: E402
litellm.num_retries = 3
litellm.retry_after = 5
litellm.request_timeout = 90   # kill silent Vertex AI hangs; LiteLLM retries automatically

_orchestrator_model_str = os.environ.get("ORCHESTRATOR_MODEL") or os.environ.get("MODEL")
_specialist_model_str = os.environ.get("SPECIALIST_MODEL") or os.environ.get("MODEL")

if not _orchestrator_model_str or not _specialist_model_str:
    sys.exit("ERROR: ORCHESTRATOR_MODEL and SPECIALIST_MODEL (or MODEL) must be set in .env")


# Session & State --------------------------------------------------------------

_sessions_store: dict[str, list[TResponseInputItem]] = {}
_context_store: dict[str, "AppContext"] = {}


class InMemorySession(SessionABC):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        if self.session_id not in _sessions_store:
            _sessions_store[self.session_id] = []

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        items = _sessions_store[self.session_id]
        return items[-limit:] if limit is not None else list(items)

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        _sessions_store[self.session_id].extend(items)

    async def pop_item(self) -> TResponseInputItem | None:
        if _sessions_store[self.session_id]:
            return _sessions_store[self.session_id].pop()
        return None

    async def clear_session(self) -> None:
        _sessions_store[self.session_id] = []


class AppContext(BaseModel):
    """Serializable session state passed to the agent runner."""
    session_id: str
    advisor_notes: str = ""
    itinerary_json: str = ""    # JSON-serialized current Itinerary (avoids circular imports)
    locked_slots: list[str] = []   # ["day1_morning", "day2_evening", ...]
    disruption_count: int = 0
    pending_delta: str = ""     # JSON-serialized ItineraryDelta set by store_delta tool
    weather_data: str = ""      # JSON-serialized list of per-day weather from get_weather_forecast
    # Keys: "lodging" | "activities" | "dining" → list of PlaceResult-like dicts
    candidate_pool: dict[str, list[dict]] = Field(
        default_factory=lambda: {"lodging": [], "activities": [], "dining": []}
    )

    @classmethod
    def get_or_create(cls, session_id: str) -> "AppContext":
        if session_id not in _context_store:
            _context_store[session_id] = cls(session_id=session_id)
        return _context_store[session_id]

    def save(self) -> None:
        _context_store[self.session_id] = self


# Itinerary capture ------------------------------------------------------------

def _looks_like_itinerary(text: str) -> bool:
    """Return True if the response text contains a full itinerary (not just a re-plan description)."""
    return bool(
        len(text) > 1500 and
        re.search(r"\*\*Day \d", text) and
        re.search(r"(morning|afternoon|evening)", text, re.IGNORECASE) and
        re.search(r"💰|Day total", text)
    )


def _guardrail_message(user_input: str) -> str:
    """Return a friendly rejection message based on what the user sent."""
    lower = user_input.lower()
    if re.search(r"\$\s*\d+", lower):
        return (
            "That budget doesn't look right for a travel plan — "
            "I can help with trips in the $20–$10,000/day range per person. "
            "What's your daily budget?"
        )
    return (
        "I'm a travel planning assistant — I can help you build itineraries, "
        "find places, and re-plan when things go wrong. "
        "What trip are you planning?"
    )


def _capture_itinerary(ctx: "AppContext", response: str) -> None:
    """If the response contains an itinerary, store it in AppContext for the re-planner."""
    if _looks_like_itinerary(response):
        ctx.itinerary_json = json.dumps({
            "text": response,
            "version": ctx.disruption_count + 1,
        })
        logger.info("itinerary captured in AppContext (session=%s, %d chars)",
                    ctx.session_id, len(response))


# Models & Agents --------------------------------------------------------------

_orchestrator_model = LitellmModel(model=_orchestrator_model_str, api_key="unused")
_specialist_model = LitellmModel(model=_specialist_model_str, api_key="unused")

# Import here (after models are defined) to avoid circular issues at module load
from backend.agents.orchestrator import build_orchestrator  # noqa: E402
from backend.guardrails.input_validation import (  # noqa: E402
    budget_sanity_guardrail,
    init_guardrail_agents,
    off_topic_guardrail,
)

init_guardrail_agents(_specialist_model)

agent = build_orchestrator(
    orchestrator_model=_orchestrator_model,
    specialist_model=_specialist_model,
    input_guardrails=[off_topic_guardrail, budget_sanity_guardrail],
)


# Server -----------------------------------------------------------------------

app = FastAPI(title="Travel Optimizer")


async def _ping_vertex() -> None:
    """Single keep-alive ping to Vertex AI."""
    try:
        await litellm.acompletion(
            model=_orchestrator_model_str,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        logger.info("Vertex AI keep-alive ping succeeded")
    except Exception as e:
        logger.warning("Vertex AI keep-alive ping failed: %s", e)


async def _keepalive_loop() -> None:
    """Ping Vertex AI every 3 minutes to prevent cold connection hangs."""
    while True:
        await asyncio.sleep(180)
        await _ping_vertex()


@app.on_event("startup")
async def _warmup_vertex() -> None:
    """Warm up Vertex AI at startup and start keep-alive loop."""
    await _ping_vertex()
    asyncio.create_task(_keepalive_loop())


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s", traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": traceback.format_exc()})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "orchestrator_model": _orchestrator_model_str}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    locked_slots: list[str] = []   # ["day2_morning", "day3_evening", ...]


class ChatResponse(BaseModel):
    response: str
    session_id: str
    delta: dict | None = None      # Serialized ItineraryDelta when re-planning occurred
    weather: list | None = None    # Per-day weather data for badge rendering


def _looks_like_replan(text: str) -> bool:
    """Return True if the response describes a re-plan rather than a full itinerary."""
    return bool(
        re.search(r"\b(replac|remov|adjust|sick day|disruption|re.?plan|changed|closed)\b", text, re.IGNORECASE) and
        re.search(r"\bDay\s+\d+\b", text, re.IGNORECASE) and
        not _looks_like_itinerary(text)
    )


def _parse_prose_to_delta(response: str) -> str | None:
    """Parse a prose re-plan response into an ItineraryDelta JSON string without an LLM call."""

    # Extract day number
    day_m = re.search(r'\bDay\s+(\d+)\b', response, re.IGNORECASE)
    if not day_m:
        return None
    day_num = int(day_m.group(1))

    def _clean_name(s: str) -> str:
        return re.sub(r'^(the|a|an)\s+', '', s.strip().rstrip(',.'), flags=re.IGNORECASE).strip()

    def _find_period(text: str, pos: int) -> str:
        window = text[max(0, pos - 250):pos]
        pm = re.search(r'\b(morning|afternoon|evening|lunch|dinner)\b', window, re.IGNORECASE)
        if not pm:
            return "morning"
        raw = pm.group(1).lower()
        return "afternoon" if raw in ("lunch",) else "evening" if raw == "dinner" else raw

    removed, changed = [], []

    # Pattern: "X has been removed" / "X, has been removed" / "removing X"
    for m in re.finditer(
        r'([\w][\w\s&\'\-\/,]+?),?\s+(?:has been|have been|is being|are being)\s+removed',
        response, re.IGNORECASE
    ):
        name = _clean_name(m.group(1))
        if len(name) > 3 and not re.search(
            r'\b(morning|afternoon|evening|day|slot|activity|schedule|visit|entire)\b', name, re.IGNORECASE
        ):
            removed.append({
                "day_number": day_num, "period": _find_period(response, m.start()),
                "place_name": name, "cost_usd": 0.0, "notes": "",
                "place_id": "", "category": "activity", "address": "", "booking_url": "", "duration_minutes": 90,
            })

    # Pattern: "X replaced with Y"
    for m in re.finditer(
        r'([\w][\w\s&\'\-\/,]+?)\s+(?:\([^)]*\)\s*)?(?:has been |have been |is |are )?replaced with\s+'
        r'(?:a\s+)?(?:shorter[^,\.]*?visit to\s+)?([\w][\w\s&\'\-\/,]+?)(?:\s*[\(\.,]|$)',
        response, re.IGNORECASE
    ):
        old_name = _clean_name(m.group(1))
        new_name = _clean_name(m.group(2))
        period = _find_period(response, m.start())
        if len(old_name) > 3:
            removed.append({
                "day_number": day_num, "period": period, "place_name": old_name,
                "cost_usd": 0.0, "notes": "", "place_id": "", "category": "activity",
                "address": "", "booking_url": "", "duration_minutes": 90,
            })
        if len(new_name) > 3:
            changed.append({
                "day_number": day_num, "period": period, "place_name": new_name,
                "cost_usd": 0.0, "notes": "replacement", "place_id": "", "category": "activity",
                "address": "", "booking_url": "", "duration_minutes": 90,
            })

    if not removed and not changed:
        return None

    # Extract cost
    cost_m = re.search(r'Day\s*\d+\s*(?:estimated\s+)?cost[:\s]+~?\$\s*([\d,]+)', response, re.IGNORECASE)
    cost_usd = float(cost_m.group(1).replace(',', '')) if cost_m else 0.0

    reasoning = re.sub(r'[*•]\s*', '', response).replace('\n', ' ').strip()[:400]

    delta = {
        "disruption": {"day_number": day_num, "period": "", "description": reasoning[:100], "disruption_type": ""},
        "affected_days": [day_num],
        "changed_slots": changed,
        "removed_slots": removed,
        "reasoning": reasoning,
        "new_daily_costs": [{"day": day_num, "cost_usd": cost_usd}] if cost_usd else [],
    }
    result = json.dumps(delta)
    logger.info("delta parsed from prose: %d removed, %d changed", len(removed), len(changed))
    return result


def _validate_delta(ctx: "AppContext") -> dict | None:
    """Parse and validate the pending ItineraryDelta, dropping any locked slots."""
    if not ctx.pending_delta:
        return None

    from backend.models.disruption import ItineraryDelta
    try:
        delta = ItineraryDelta.model_validate_json(ctx.pending_delta)
    except Exception:
        logger.warning("Failed to parse pending_delta — skipping validation")
        return None

    locked = set(ctx.locked_slots)
    if not locked:
        return delta.model_dump()

    violations: list[str] = []

    def _is_locked(slot) -> bool:
        key = f"day{slot.day_number}_{slot.period}"
        return slot.day_number > 0 and key in locked

    orig_changed = delta.changed_slots
    orig_removed = delta.removed_slots
    delta.changed_slots = [s for s in orig_changed if not _is_locked(s)]
    delta.removed_slots = [s for s in orig_removed if not _is_locked(s)]

    dropped = (len(orig_changed) - len(delta.changed_slots)) + (len(orig_removed) - len(delta.removed_slots))
    if dropped:
        violations = [
            f"day{s.day_number}_{s.period.value}"
            for s in orig_changed + orig_removed
            if _is_locked(s)
        ]
        delta.reasoning += (
            f" Note: {dropped} locked slot(s) were protected and excluded from re-planning "
            f"({', '.join(violations)})."
        )
        logger.info("validator dropped %d locked slot(s): %s", dropped, violations)

    return delta.model_dump()


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id or str(uuid.uuid4())
    session = InMemorySession(session_id)
    ctx = AppContext.get_or_create(session_id)

    # Sync locked slots sent by the frontend on every request
    if request.locked_slots is not None:
        ctx.locked_slots = request.locked_slots
    ctx.pending_delta = ""   # clear any previous delta before this run

    logger.info("chat session=%s message=%r locked=%s", session_id, request.message[:80], ctx.locked_slots)

    try:
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                request.message,
                session=session,
                context=ctx,
                max_turns=25,
            ),
            timeout=480,  # 8 minutes max per request
        )
    except asyncio.TimeoutError:
        logger.warning("chat timeout session=%s", session_id)
        return ChatResponse(
            response="The request took too long — please try again.",
            session_id=session_id,
        )
    except InputGuardrailTripwireTriggered as e:
        guardrail_name = type(e).__name__
        logger.info("guardrail triggered session=%s guardrail=%s", session_id, guardrail_name)
        msg = _guardrail_message(request.message)
        return ChatResponse(response=msg, session_id=session_id)
    except Exception as e:
        err = str(e)
        if any(k in err.lower() for k in ("timeout", "timed out", "read timeout", "connect")):
            logger.warning("LLM call timeout/connection error session=%s: %s", session_id, err[:120])
            return ChatResponse(
                response="A model call timed out — Vertex AI is slow to respond. Please try again.",
                session_id=session_id,
            )
        raise

    _capture_itinerary(ctx, result.final_output)

    # If store_delta was not called by the replanner, parse prose directly (no extra LLM call)
    if not ctx.pending_delta and ctx.itinerary_json and _looks_like_replan(result.final_output):
        ctx.pending_delta = _parse_prose_to_delta(result.final_output) or ""

    validated_delta = _validate_delta(ctx)
    ctx.pending_delta = ""   # consumed
    ctx.save()
    weather = None
    if ctx.weather_data:
        try:
            weather = json.loads(ctx.weather_data)
        except Exception:
            pass

    return ChatResponse(response=result.final_output, session_id=session_id, delta=validated_delta, weather=weather)


@app.get("/debug/last-run")
async def debug_last_run() -> dict:
    """Return context state for the most recent session — useful during demos."""
    if not _context_store:
        return {"error": "no sessions yet"}
    last_session_id = list(_context_store.keys())[-1]
    ctx = _context_store[last_session_id]
    return {"session_id": last_session_id, "context": ctx.model_dump()}


@app.get("/qr")
async def qr_endpoint(url: str) -> JSONResponse:
    """Generate a QR code PNG for *url* and return it as a base64 data URI."""
    from backend.tools.maps_links import qr_code_base64
    data_uri = qr_code_base64(url)
    if not data_uri:
        return JSONResponse(status_code=500, content={"error": "qr generation failed"})
    return JSONResponse(content={"data_uri": data_uri})


app.mount("/", StaticFiles(directory="static", html=True), name="static")


# CLI --------------------------------------------------------------------------

cli = typer.Typer(help="Travel Optimizer CLI")


@cli.command()
def ask(query: str, session_id: str = "cli_session") -> None:
    """Run a single query against the agent from the CLI."""
    async def _run() -> None:
        typer.echo(f"Session: {session_id}\n" + "-" * 60)
        session = InMemorySession(session_id)
        ctx = AppContext.get_or_create(session_id)

        try:
            result = await Runner.run(agent, query, session=session, context=ctx, max_turns=50)
        except InputGuardrailTripwireTriggered:
            typer.echo(_guardrail_message(query))
            return

        _capture_itinerary(ctx, result.final_output)
        ctx.save()
        typer.echo(f"Agent:\n{result.final_output}\n" + "-" * 60)

    asyncio.run(_run())


@cli.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the FastAPI server."""
    typer.echo(f"Starting Travel Optimizer on http://{host}:{port} ...")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
