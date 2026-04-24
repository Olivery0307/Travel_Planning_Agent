"""AI Travel Itinerary Optimizer — FastAPI server and CLI entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
import uuid

import typer
import uvicorn
from agents import Runner
from agents.extensions.models.litellm_model import LitellmModel
from agents.items import TResponseInputItem
from agents.memory.session import SessionABC
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

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

    result = await Runner.run(
        agent,
        request.message,
        session=session,
        context=ctx,
        max_turns=20,
    )

    ctx.save()
    return ChatResponse(response=result.final_output, session_id=session_id)


@app.get("/debug/last-run")
async def debug_last_run() -> dict:
    """Return context state for the most recent session — useful during demos."""
    if not _context_store:
        return {"error": "no sessions yet"}
    last_session_id = list(_context_store.keys())[-1]
    ctx = _context_store[last_session_id]
    return {"session_id": last_session_id, "context": ctx.model_dump()}


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

        result = await Runner.run(agent, query, session=session, context=ctx, max_turns=20)
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
