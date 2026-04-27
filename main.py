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
from collections import defaultdict

import typer
import uvicorn
from agents import InputGuardrailTripwireTriggered, Runner
from agents.extensions.models.litellm_model import LitellmModel
from agents.items import TResponseInputItem
from agents.memory.session import SessionABC
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Suppress noisy third-party loggers that obscure agent workflow visibility.
# "LiteLLM completion() model=..." comes from the LiteLLM logger.
# "OPENAI_API_KEY is not set, skipping trace export" comes from openai.agents.
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
logging.getLogger("litellm").setLevel(logging.ERROR)
logging.getLogger("openai.agents").setLevel(logging.ERROR)

# ── Per-session progress queues ──────────────────────────────────────────────
# Maps session_id → asyncio.Queue of status-message strings.
# SSE stream reads from these; the agent run pushes into them.
_progress_queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

# Human-readable labels for each sub-agent tool call
_TOOL_LABELS: dict[str, str] = {
    "intake_agent":    "Understanding your trip request…",
    "lodging_agent":   "Searching for hotels & lodging…",
    "activity_agent":  "Finding attractions & activities…",
    "dining_agent":    "Discovering restaurants & dining…",
    "transport_agent": "Planning transportation routes…",
    "solver_agent":    "Assembling your day-by-day itinerary…",
    "replanner_agent": "Re-optimizing after disruption…",
}


class _ProgressHandler(logging.Handler):
    """Captures 'Invoking tool <name>' DEBUG lines from openai.agents and pushes status events."""

    # openai.agents logs: "Invoking tool intake_agent" or "Invoking tool intake_agent with input ..."
    _TOOL_RE = re.compile(r"Invoking tool (\w+)", re.IGNORECASE)

    def __init__(self, session_id: str) -> None:
        super().__init__(level=logging.DEBUG)
        self.session_id = session_id

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        m = self._TOOL_RE.search(msg)
        if m:
            tool = m.group(1)
            label = _TOOL_LABELS.get(tool, f"Running {tool}…")
            try:
                _progress_queues[self.session_id].put_nowait({"type": "status", "text": label})
            except Exception:
                pass

# Global LiteLLM retry on 429/503 — fires before the error reaches the SDK.
# 3 retries with exponential backoff: waits 5s, 10s, 20s before each retry.
import litellm  # noqa: E402
litellm.num_retries = 3
litellm.retry_after = 5
litellm.suppress_debug_info = True

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

    @classmethod
    def get_or_create(cls, session_id: str) -> "AppContext":
        if session_id not in _context_store:
            _context_store[session_id] = cls(session_id=session_id)
        return _context_store[session_id]

    def save(self) -> None:
        _context_store[self.session_id] = self


# Itinerary capture ------------------------------------------------------------

def _looks_like_itinerary(text: str) -> bool:
    """Return True if the response text contains a full itinerary."""
    return bool(
        re.search(r"\*\*Day \d", text) and
        re.search(r"(morning|afternoon|evening)", text, re.IGNORECASE)
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


class ChatResponse(BaseModel):
    response: str
    session_id: str


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id or str(uuid.uuid4())
    session = InMemorySession(session_id)
    ctx = AppContext.get_or_create(session_id)

    logger.info("chat session=%s message=%r", session_id, request.message[:80])

    try:
        result = await Runner.run(
            agent,
            request.message,
            session=session,
            context=ctx,
            max_turns=50,
        )
    except InputGuardrailTripwireTriggered as e:
        # Return a clean user-facing message instead of a 500
        guardrail_name = type(e).__name__
        logger.info("guardrail triggered session=%s guardrail=%s", session_id, guardrail_name)
        msg = _guardrail_message(request.message)
        return ChatResponse(response=msg, session_id=session_id)

    _capture_itinerary(ctx, result.final_output)
    ctx.save()
    return ChatResponse(response=result.final_output, session_id=session_id)


@app.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest) -> StreamingResponse:
    """SSE endpoint: emits status events while the agent runs, then a final 'done' event."""
    session_id = request.session_id or str(uuid.uuid4())

    async def event_generator():
        # Fresh queue for this request
        queue: asyncio.Queue = asyncio.Queue()
        _progress_queues[session_id] = queue

        # Attach handler to the openai.agents logger at DEBUG so we capture tool invocations.
        # We temporarily lower its level from ERROR (set above) to DEBUG just for this handler.
        agents_logger = logging.getLogger("openai.agents")
        handler = _ProgressHandler(session_id)
        agents_logger.addHandler(handler)
        prev_level = agents_logger.level
        agents_logger.setLevel(logging.DEBUG)

        yield f"data: {json.dumps({'type': 'status', 'text': 'Connecting to Voyager AI…'})}\n\n"

        async def _run_agent():
            session = InMemorySession(session_id)
            ctx = AppContext.get_or_create(session_id)
            logger.info("chat/stream session=%s message=%r", session_id, request.message[:80])
            try:
                result = await Runner.run(
                    agent,
                    request.message,
                    session=session,
                    context=ctx,
                    max_turns=50,
                )
            except InputGuardrailTripwireTriggered:
                msg = _guardrail_message(request.message)
                queue.put_nowait({"type": "done", "response": msg, "session_id": session_id})
                return
            except Exception as exc:
                logger.error("chat/stream error: %s", traceback.format_exc())
                queue.put_nowait({"type": "error", "detail": str(exc), "session_id": session_id})
                return

            _capture_itinerary(ctx, result.final_output)
            ctx.save()
            queue.put_nowait({"type": "done", "response": result.final_output, "session_id": session_id})

        task = asyncio.create_task(_run_agent())

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.4)
                    yield f"data: {json.dumps(msg)}\n\n"
                    if msg.get("type") in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    if task.done():
                        break
        finally:
            agents_logger.removeHandler(handler)
            agents_logger.setLevel(prev_level)
            _progress_queues.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
