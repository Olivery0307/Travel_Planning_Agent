---
name: openai-agents-sdk-state
description: Explains how to handle agent state in the OpenAI Agents SDK using RunContext.
---

# OpenAI Agents SDK: RunContext & Agent State

## Overview

**`RunContext`** is the SDK's mechanism for dependency injection and state management. Unlike a Session (which stores the serializable chat history for the LLM), `RunContext` holds your live Python objects (database connections, user IDs, business state, usage metrics) during a single execution. 

**Crucial distinctions:**
* **It is Ephemeral:** It is *not* saved to the Session. You must rebuild it before every call to `Runner.run()`.
* **It is Invisible to the LLM:** The LLM does not see the context object. It is only accessible to your Python code (tools, hooks, and dynamic prompts).
* **It is a Wrapper:** When you pass your state to the runner, the SDK wraps it in a `RunContextWrapper`. Your actual state lives inside `ctx.context`, but the wrapper exposes additional useful metadata.

## Basic Usage

Define a standard Python class or dataclass to hold your state, instantiate it, and pass it to the `Runner`. 

```python
from dataclasses import dataclass
from agents import Agent, Runner

# 1. Define your custom state
@dataclass
class AppState:
    user_id: str
    db_client: dict

# 2. Rehydrate the state for this specific run
my_state = AppState(user_id="user_123", db_client={"theme": "dark"})

# 3. Pass it to the Runner via the `context` parameter
result = await Runner.run(
    agent, 
    "Change my theme to light.", 
    context=my_state
)
```

## Using Context in Tools

Tools can access the context by adding a parameter typed as `RunContext[YourStateType]`. 

```python
from agents import function_tool, RunContext

@function_tool
def update_theme(theme_name: str, ctx: RunContext[AppState]) -> str:
    """Updates the user's theme preference."""
    # Access your custom state directly
    user = ctx.user_id 
    
    # Mutate the state or use dependencies
    ctx.db_client["theme"] = theme_name 
    
    return f"Theme for {user} updated to {theme_name}."
```

## Dynamic System Prompts

You can pass a callable to the `instructions` parameter of an Agent. The SDK will inject the `RunContext` into this function before every LLM call, allowing your system prompt to react to state changes in real-time.

```python
def generate_prompt(ctx: RunContext[AppState]) -> str:
    current_theme = ctx.db_client.get("theme")
    return f"You are an assistant. The user's current theme is {current_theme}."

agent = Agent(
    name="Assistant",
    instructions=generate_prompt,
    tools=[update_theme]
)
```

## The Wrapper API (`RunContextWrapper`)

While your custom state is what you use most, the `RunContext` object injected into your tools is actually a `RunContextWrapper` containing built-in SDK data:

* **`ctx.usage`**: Tracks the token usage of the agent run so far. 
* **`ctx.tool_input`**: Contains the raw structured input for the current tool.
* **`ctx.approve_tool()` / `ctx.reject_tool()`**: Methods to programmatically record approval decisions for human-in-the-loop workflows.
* **`ctx.get_rejection_message()`**: Retrieves a stored message explaining why a specific tool call was rejected.

*(Note: Because of `RunContextWrapper`'s dynamic fallback magic, you can usually access your custom attributes directly like `ctx.user_id` instead of having to type `ctx.context.user_id`.)*
