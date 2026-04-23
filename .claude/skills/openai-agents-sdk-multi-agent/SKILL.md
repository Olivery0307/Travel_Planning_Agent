---
name: openai-agents-sdk-multi-agent
description: Explains how to add support for multiple agents / sub-agents in an OpenAI Agents SDK agent.
---

# OpenAI Agents SDK: Multi-Agent Orchestration

## Overview

There are two primary ways to orchestrate multiple agents in the SDK:
1. **Agents as Tools (Delegation):** A "Manager" agent calls a "Worker" agent like a function tool, waits for the worker to finish, and gets the result back. The Manager retains control.
2. **Handoffs (Transfer):** An agent completely transfers control of the conversation to a "Specialist" agent. The new agent takes over and the original agent is removed from the loop.

## Pattern 1: Agents as Tools (Delegation)

Use this when a central Orchestrator agent needs to query specialized agents but must remain in control to synthesize the final answer.

We enforce structured inputs for sub-agents using Pydantic `BaseModel`s via the `parameters` argument.

```python
from pydantic import BaseModel, Field
from agents import Agent, Runner

# 1. Define the input schema for the sub-agent
class TranslationInput(BaseModel):
    text: str = Field(description="The text to translate.")
    target_language: str = Field(description="The language to translate into.")

# 2. Create the specialized sub-agent
translator_agent = Agent(
    name="Translator",
    instructions="You are a precise translator. Only output the translated text.",
)

# 3. Create the Orchestrator and provide the sub-agent as a tool
orchestrator_agent = Agent(
    name="Orchestrator",
    instructions="You are a translation manager. Use your tools to translate the user's request.",
    tools=[
        translator_agent.as_tool(
            tool_name="translate_text",
            tool_description="Translates text into a specific language.",
            parameters=TranslationInput,
            include_input_schema=True, # Recommended for complex inputs
        )
    ],
)

# Usage:
# result = await Runner.run(orchestrator_agent, "Translate 'Hello' to French and Spanish.")
```

## Pattern 2: Handoffs (Transfer of Control)

Use this for routing user intents to specialists (e.g., Triage -> Billing / Refund). 

When an agent hands off, the new agent receives the **entire conversation history** by default. We use the `handoff()` helper function to configure these transfers, often passing structured metadata (like a reason for escalation) via `input_type`.

```python
from pydantic import BaseModel, Field
from agents import Agent, handoff, RunContextWrapper

# 1. Define metadata the routing agent must provide during the handoff
class EscalationData(BaseModel):
    reason: str = Field(description="The reason for escalation.")
    priority: str = Field(description="Either 'high' or 'low'.")

# 2. Define the callback that runs the moment the handoff triggers
async def on_escalation(ctx: RunContextWrapper[None], input_data: EscalationData):
    """Callback executed during handoff to log metadata or update state."""
    # (In a real app, you might save this to your DB or RunContext here)
    print(f"Escalating with priority {input_data.priority}: {input_data.reason}")

# 3. Create the receiving agent
specialist_agent = Agent(
    name="EscalationAgent",
    instructions="You handle complex user escalations. Review the history and assist.",
)

# 4. Create the routing agent, providing the handoff configuration
triage_agent = Agent(
    name="TriageAgent",
    instructions=(
        "You route user requests. If the user is angry or the request is complex, "
        "transfer them to the Escalation Agent."
    ),
    handoffs=[
        handoff(
            agent=specialist_agent,
            on_handoff=on_escalation,
            input_type=EscalationData,
            tool_name_override="transfer_to_escalation",
            tool_description_override="Transfers the user to a human escalation specialist."
        )
    ]
)

# Usage:
# result = await Runner.run(triage_agent, "I demand to speak to a manager right now!")
```

### Key Differences

- **Control Flow:** `as_tool` = Call and Return. `handoff` = One-way Ticket.
- **State/Memory:** * `as_tool`: The sub-agent does *not* see the orchestrator's chat history automatically; it only sees the structured `parameters` passed to it.
    - `handoff`: The receiving agent *does* see the full conversation transcript up to that point.
- **Typing:** Always use Pydantic `BaseModel`s for `parameters` in `as_tool` and `input_type` in `handoff`. Do not rely on loose strings.
