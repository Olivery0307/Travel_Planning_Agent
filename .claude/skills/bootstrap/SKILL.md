---
name: bootstrap
description: Bootstrap an OpenAI Agents SDK + FastAPI agent. This should ONLY be run when starting a new project.
---

# OpenAI Agents SDK: Bootstrap

## Overview

This is the standard boilerplate for initializing a new OpenAI Agents SDK project in this repository.

It provides a unified architecture that handles:
1. **Ephemeral Chat Memory:** `InMemorySession` (via `SessionABC`).
2. **Business State Injection:** `AppState` (via `RunContext`).
3. **Model via `.env`:** `LitellmModel` driven by a `MODEL` env var (e.g. `vertex_ai/gemini-2.5-flash`).
4. **Error visibility:** Unhandled exceptions return full tracebacks as JSON; the frontend displays them.
5. **Dual Interfaces:** FastAPI for REST, and Typer for CLI execution.

your_project/
├── main.py
├── .env
└── static/
    └── index.html

## Setup

```bash
uv init --no-readme
uv add openai-agents fastapi uvicorn typer pydantic python-dotenv "litellm[google]"
uv add --dev ruff
mkdir -p static
```

## .env

```env
MODEL=vertex_ai/gemini-2.5-flash
```

## main.py

```python
"""Your agent description here."""

import asyncio
import logging
import os
import sys
import traceback
import uuid

import typer
import uvicorn
from agents import Agent, RunContextWrapper, Runner, function_tool
from agents.extensions.models.litellm_model import LitellmModel
from agents.items import TResponseInputItem
from agents.memory.session import SessionABC
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")

_model_str = os.environ.get("MODEL")
if not _model_str:
    sys.exit("ERROR: MODEL is not set in .env")


# Session & State --------------------------------------------------------------

_sessions_store: dict[str, list[TResponseInputItem]] = {}
_app_state_store: dict[str, "AppState"] = {}


class InMemorySession(SessionABC):
    """Custom session storing ephemeral chat history in a Python dict."""

    def __init__(self, session_id: str) -> None:
        """Initialize session, creating storage slot if needed."""
        self.session_id = session_id
        if self.session_id not in _sessions_store:
            _sessions_store[self.session_id] = []

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        """Return stored items, optionally limited to the last N."""
        items = _sessions_store[self.session_id]
        return items[-limit:] if limit is not None else list(items)

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Append items to the session."""
        _sessions_store[self.session_id].extend(items)

    async def pop_item(self) -> TResponseInputItem | None:
        """Remove and return the last item."""
        if _sessions_store[self.session_id]:
            return _sessions_store[self.session_id].pop()
        return None

    async def clear_session(self) -> None:
        """Clear all items from the session."""
        _sessions_store[self.session_id] = []


class AppState(BaseModel):
    """Business logic state injected into the agent run."""

    session_id: str

    @classmethod
    def get_or_create(cls, session_id: str) -> "AppState":
        """Return existing state or create new for the given session."""
        if session_id not in _app_state_store:
            _app_state_store[session_id] = cls(session_id=session_id)
        return _app_state_store[session_id]

    def save(self) -> None:
        """Persist state back to the store."""
        _app_state_store[self.session_id] = self


AppContext = RunContextWrapper[AppState]


# Tools -----------------------------------------------------------------------


class EchoRequest(BaseModel):
    """Request model for the echo tool."""

    message: str = Field(description="The message to echo back.")


@function_tool
def echo_tool(ctx: AppContext, request: EchoRequest) -> str:
    """Echo a message back with the session ID.

    Args:
        ctx: Injected by SDK.
        request: The structured message request.

    """
    return f"Session {ctx.context.session_id} says: {request.message}"


# Agent ------------------------------------------------------------------------

_model = LitellmModel(model=_model_str, api_key="unused")

agent = Agent(
    name="BootstrapAgent",
    model=_model,
    instructions="You are a helpful assistant. Use the echo tool if asked.",
    tools=[echo_tool],
)


# Server -----------------------------------------------------------------------

app = FastAPI(title="Agent Service")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": traceback.format_exc()})


class ChatRequest(BaseModel):
    """Incoming chat message."""

    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Agent response."""

    response: str
    session_id: str


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """REST endpoint for agent interaction."""
    session_id = request.session_id or str(uuid.uuid4())

    chat_session = InMemorySession(session_id)
    current_state = AppState.get_or_create(session_id)

    result = await Runner.run(
        agent,
        request.message,
        session=chat_session,
        context=current_state,
    )

    current_state.save()

    return ChatResponse(response=result.final_output, session_id=session_id)


app.mount("/", StaticFiles(directory="static", html=True), name="static")


# CLI --------------------------------------------------------------------------

cli = typer.Typer(help="Agent Service CLI")


@cli.command()
def ask(query: str, session_id: str = "cli_session") -> None:
    """Run a single query against the agent from the CLI."""

    async def _run() -> None:
        typer.echo(f"Session: {session_id}\n" + "-" * 40)

        chat_session = InMemorySession(session_id)
        current_state = AppState.get_or_create(session_id)

        result = await Runner.run(agent, query, session=chat_session, context=current_state)
        current_state.save()

        typer.echo(f"Agent: {result.final_output}\n" + "-" * 40)

    asyncio.run(_run())


@cli.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the FastAPI server."""
    typer.echo(f"Starting server on http://{host}:{port} ...")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
```


## static/index.html

```html
<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Agent Chat</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, sans-serif; height: 100vh; display: flex; justify-content: center; align-items: center; padding: 3rem 1rem; }
        #container { width: 100%; max-width: 900px; display: flex; flex-direction: column; border: 1px solid #e0e0e0; }
        #messages { overflow-y: auto; padding: 2rem; display: flex; flex-direction: column; gap: 1.5rem; height: 60vh; }
        .msg { line-height: 1.5; max-width: 80%; }
        .msg.user { align-self: flex-end; text-align: right; }
        .msg .role { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #999; margin-bottom: 0.25rem; }
        .msg.loading .content { color: #999; }
        #input-area { display: flex; gap: 1rem; padding: 1.5rem; border-top: 1px solid #e0e0e0; }
        #user-input { flex: 1; padding: 0.75rem 0; border: none; border-bottom: 1px solid #e0e0e0; font-size: 1rem; outline: none; }
        #user-input:focus { border-bottom-color: #000; }
        button { padding: 0.75rem 1.5rem; background: #000; color: #fff; border: none; font-size: 0.75rem; text-transform: uppercase; cursor: pointer; }
        button:disabled { opacity: 0.3; }
    </style>
</head>
<body>
    <div id="container">
        <div id="messages"></div>
        <div id="input-area">
            <input type="text" id="user-input" placeholder="Type a message..." autocomplete="off" />
            <button id="send-btn">Send</button>
        </div>
    </div>

    <script>
        const msgs = document.getElementById("messages");
        const input = document.getElementById("user-input");
        const btn = document.getElementById("send-btn");
        let sessionId = null;

        function addMsg(role, text, isLoading = false) {
            const div = document.createElement("div");
            div.className = `msg ${role === "you" ? "user" : "assistant"} ${isLoading ? "loading" : ""}`;
            div.innerHTML = `<div class="role">${role}</div><div class="content">${text}</div>`;
            msgs.appendChild(div);
            msgs.scrollTop = msgs.scrollHeight;
            return div;
        }

        async function send() {
            const text = input.value.trim();
            if (!text) return;

            input.value = "";
            btn.disabled = true;
            addMsg("you", text);
            const loading = addMsg("assistant", "Thinking...", true);

            try {
                const res = await fetch("/chat", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ message: text, session_id: sessionId }),
                });
                const body = await res.text();
                if (!res.ok) {
                    let detail = body;
                    try { detail = JSON.parse(body).detail ?? body; } catch (_) {}
                    loading.querySelector(".content").textContent = `HTTP ${res.status}\n\n${detail}`;
                    loading.querySelector(".content").style.whiteSpace = "pre-wrap";
                    return;
                }
                const data = JSON.parse(body);
                sessionId = data.session_id;
                loading.querySelector(".content").textContent = data.response;
            } catch (e) {
                loading.querySelector(".content").textContent = e.stack || e.message;
                loading.querySelector(".content").style.whiteSpace = "pre-wrap";
            }

            loading.classList.remove("loading");
            btn.disabled = false;
            input.focus();
        }

        btn.addEventListener("click", send);
        input.addEventListener("keydown", e => { if (e.key === "Enter") send(); });
        input.focus();
    </script>
</body>
</html>
```


## CLI Usage

Start the API Server:
```bash
uv run python main.py serve --port 8000
```

Query the Agent directly:
```bash
uv run python main.py ask "Please echo the word Hello"
```
