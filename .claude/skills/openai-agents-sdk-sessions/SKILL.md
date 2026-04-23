---
name: openai-agents-sdk-sessions
description: Explains how to handle agent sessions (history, context) in an OpenAI Agents SDK agent (usually wrapped in a FastAPI server).
---

# OpenAI Agents SDK: Sessions

## Overview

**Sessions** automatically maintain conversation history across multiple agent runs. This eliminates the need to manually capture state via `result.to_input_list()` between turns. 

When a session is passed to `Runner.run()`, the SDK:
1. Retrieves previous history and prepends it to the new input.
2. Automatically stores the new generated items (user input, tool calls, assistant responses) back into the session.

## Basic Usage

The SDK provides several out-of-the-box session backends (e.g., `SQLiteSession`, `RedisSession`, `SQLAlchemySession`).

```python
from agents import Agent, Runner, SQLiteSession

agent = Agent(name="Assistant")
session = SQLiteSession("conversation_123", "optional_db_path.sqlite")

# Turn 1
await Runner.run(agent, "Hi, my name is Alice.", session=session)

# Turn 2 - Agent remembers Turn 1 automatically
result = await Runner.run(agent, "What is my name?", session=session)
print(result.final_output) # "Alice"
```

## Built-in Session Types

| Session type | Best for | Notes |
| :--- | :--- | :--- |
| **SQLiteSession** | Local development and simple apps | Built-in, lightweight, file-backed or in-memory |
| **AsyncSQLiteSession** | Async SQLite with `aiosqlite` | Extension backend with async driver support |
| **RedisSession** | Shared memory across workers/services | Good for low-latency distributed deployments |
| **SQLAlchemySession** | Production apps with existing databases | Works with SQLAlchemy-supported databases |
| **DaprSession** | Cloud-native deployments with Dapr sidecars | Supports multiple state stores plus TTL and consistency controls |
| **AdvancedSQLiteSession** | SQLite plus branching/analytics | Heavier feature set; see dedicated page |
| **EncryptedSession** | Encryption + TTL on top of another session | Wrapper; choose an underlying backend first |

## Core Memory Operations

You can manually manage the stored history within a session:

```python
# Get all history
items = await session.get_items()

# Add items manually
await session.add_items([{"role": "user", "content": "Hello"}])

# Remove and return the most recent item (useful for "undo" actions)
last_item = await session.pop_item()

# Clear all items
await session.clear_session()
```

## Custom Implementation: `InMemorySession`

You can create custom session backends by inheriting from `SessionABC`. Here is a short and sweet implementation wrapping a global Python dictionary for ephemeral, in-memory storage:

```python
from agents.memory.session import SessionABC
from agents.items import TResponseInputItem
from typing import List

# Global in-memory store
_sessions_store: dict[str, List[TResponseInputItem]] = {}

class InMemorySession(SessionABC):
    """Custom session storing history in a Python dict."""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        if self.session_id not in _sessions_store:
            _sessions_store[self.session_id] = []

    async def get_items(self, limit: int | None = None) -> List[TResponseInputItem]:
        items = _sessions_store[self.session_id]
        return items[-limit:] if limit is not None else list(items)

    async def add_items(self, items: List[TResponseInputItem]) -> None:
        _sessions_store[self.session_id].extend(items)

    async def pop_item(self) -> TResponseInputItem | None:
        if _sessions_store[self.session_id]:
            return _sessions_store[self.session_id].pop()
        return None

    async def clear_session(self) -> None:
        _sessions_store[self.session_id] = []
```

### Usage with the Custom Session

```python
session = InMemorySession("session_abc_123")
result = await Runner.run(agent, "Process this input.", session=session)
```

## Advanced Controls

* **Limit retrieval size:** Use `RunConfig(session_settings=SessionSettings(limit=50))` to cap the history fetched before a run.
* **Filter/Reorder inputs:** Use `RunConfig.session_input_callback` to safely mutate or filter history before it reaches the model, without affecting the persisted session state.
