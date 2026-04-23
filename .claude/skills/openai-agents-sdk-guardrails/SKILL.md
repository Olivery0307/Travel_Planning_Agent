---
name: openai-agents-sdk-guardrails
description: Explains how to implement OpenAI Agents SDK guardrails in inputs, outputs, and tool calls.
---

# OpenAI Agents SDK: Guardrails

## Overview

**Guardrails** perform automated validation on inputs and outputs. They are used to block malicious prompts (e.g., jailbreaks), enforce domain boundaries, or redact sensitive data. If a guardrail "trips," it stops execution and raises an exception.

There are three types of guardrails:
1. **Input Guardrails:** Run *before* the first agent executes.
2. **Output Guardrails:** Run *after* the final agent completes.
3. **Tool Guardrails:** Run *before* and *after* specific function tools.

---

## Agent-Level Input Guardrails

Input guardrails check the user's initial prompt. We strictly use secondary (cheaper/faster) `Agent` instances wrapped in an `@input_guardrail` decorator to evaluate the input. 

**Execution Modes:**
* `run_in_parallel=True` (Default): Guardrail runs concurrently with the main agent (low latency, but costs tokens if it fails late).
* `run_in_parallel=False`: Blocks the main agent until the check passes (higher latency, safest for cost control). 

```python
from pydantic import BaseModel
from agents import (
    Agent, GuardrailFunctionOutput, InputGuardrailTripwireTriggered,
    RunContextWrapper, Runner, TResponseInputItem, input_guardrail
)

# 1. Define structured output for the guardrail
class PolicyCheckOutput(BaseModel):
    is_violation: bool
    reason: str

# 2. Create the fast evaluation agent
guardrail_agent = Agent(
    name="InputValidator",
    instructions="Determine if the user is asking for medical advice. We do not provide medical advice.",
    output_type=PolicyCheckOutput,
)

# 3. Create the guardrail function
@input_guardrail(run_in_parallel=False)  # Block execution entirely if failed
async def enforce_no_medical_advice(
    ctx: RunContextWrapper[None], 
    agent: Agent, 
    input: str | list[TResponseInputItem]
) -> GuardrailFunctionOutput:
    """Checks input against medical advice policies."""
    
    result = await Runner.run(guardrail_agent, input, context=ctx.context)
    
    return GuardrailFunctionOutput(
        output_info=result.final_output,
        tripwire_triggered=result.final_output.is_violation,
    )

# 4. Attach to your main agent
main_agent = Agent(
    name="GeneralAssistant",
    instructions="You are a helpful assistant.",
    input_guardrails=[enforce_no_medical_advice],
)

# Usage catching the tripwire:
# try:
#     await Runner.run(main_agent, "What should I take for a headache?")
# except InputGuardrailTripwireTriggered:
#     # Handle rejection gracefully
```

---

## Agent-Level Output Guardrails

Output guardrails validate the final response before it is sent to the user. They *always* run after the agent completes (no parallel execution). The implementation is nearly identical to input guardrails, but uses `@output_guardrail` and takes the typed output of your main agent.

```python
from agents import output_guardrail, OutputGuardrailTripwireTriggered

class MainAgentResponse(BaseModel):
    response: str

@output_guardrail
async def block_competitor_mentions(
    ctx: RunContextWrapper[None], 
    agent: Agent, 
    output: MainAgentResponse # Receives the structured output from the main agent
) -> GuardrailFunctionOutput:
    """Checks output for mentions of competitors."""
    
    is_clean = "competitor_name" not in output.response.lower()
    return GuardrailFunctionOutput(
        output_info=output,
        tripwire_triggered=not is_clean, # Trip if NOT clean
    )
```

---

## Tool Guardrails

Tool guardrails wrap specific `@function_tool`s. They don't require an LLM to evaluate; they are usually fast, deterministic Python checks (like regex scanning for secrets or blocking specific arguments).

Use `ToolGuardrailFunctionOutput.allow()` or `.reject_content("reason")`.

```python
import json
from agents import (
    function_tool, tool_input_guardrail, tool_output_guardrail, ToolGuardrailFunctionOutput
)

@tool_input_guardrail
def block_admin_user(data) -> ToolGuardrailFunctionOutput:
    """Prevents tools from being run on the 'admin' account."""
    
    # data.context.tool_arguments is a raw JSON string
    args = json.loads(data.context.tool_arguments or "{}")
    
    if args.get("username") == "admin":
        return ToolGuardrailFunctionOutput.reject_content("Cannot target the admin user.")
    return ToolGuardrailFunctionOutput.allow()

@tool_output_guardrail
def redact_api_keys(data) -> ToolGuardrailFunctionOutput:
    """Scans tool output to prevent API keys from reaching the LLM."""
    
    text = str(data.output or "")
    if "sk-" in text:
        return ToolGuardrailFunctionOutput.reject_content("Output redacted due to sensitive API keys.")
    return ToolGuardrailFunctionOutput.allow()

@function_tool(
    tool_input_guardrails=[block_admin_user],
    tool_output_guardrails=[redact_api_keys],
)
def fetch_user_data(username: str) -> str:
    """Fetches diagnostic data for a user."""
    return f"Data for {username}"
```
