---
paths:
- "main.py"
- "server.py"
- "routes.py"
---

# Agent and Web Server

We build our agents using FastAPI for the web server and OpenAI Agents SDK for managing the agent flow.

## Dotenv

Environment variables are loaded from a `.env` file using `python-dotenv`.


## Web Server

Our webserver exposes endpoints that render the user-facing front-end and chat interface.

Typically:

- `"/"`: user-facing front-end index.html
- `"/chat"`: POST endpoint

The server should be named `main.py` and, if simple enough, it should contain all of the routes and agent code.

You should run the server with:

```
uv run python main.py serve
```

## OpenAI Agents SDK

- We use the OpenAI Agents SDK for our agents.
- We ALWAYS use `LitellmModel`.
- To start, we should keep everything in-memory.
- To start, we should keep everything in a single `main.py` file.

This repo has skills that contain information on basic topics within the OpenAI Agents SDK.

| Topic | Skill |
|-------|-------|
| Sessions and conversation history | `./openai-agents-sdk-sessions` |
| Tool calling | `./openai-agents-sdk-tools` |
| Agent State and RunContext | `./openai-agents-sdk-state` |
| Guardrails | `./openai-agents-sdk-guardrails` |
| Multi-agent or Sub-Agents | `./openai-agents-sdk-multi-agent` |


## Arguments

In addition to running the agent runner within the `/chat` endpoint, we should support running `main.py` directly to test the Agent.

```python
import asyncio
import typer
import uvicorn

# ... [Your existing Agents, Tools, AppState, and FastAPI routes] ...

cli = typer.Typer(help="Agent API and CLI Utility")

@cli.command()
def ask(query: str, session_id: str = "cli_session") -> None:
    """Run a single query against the agent from the CLI."""
    async def _run() -> None:
        typer.echo(f"Session: {session_id}\n" + "-" * 40)
        session = InMemorySession(session_id)
        current_state = AppState.get_or_create(session_id)

        result = await Runner.run(agent, query, session=session, context=current_state)
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
