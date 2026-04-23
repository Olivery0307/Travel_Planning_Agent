---
name: openai-agents-sdk-tools
description: Explains how write and use tools in the OpenAI Agents SDK.
---

# OpenAI Agents SDK: Tool Writing Standards

## Core Mandates

In this repository, we enforce strict typing, structured outputs, and explicit documentation for all tools. The Agent SDK uses these elements to automatically generate the JSON schema for the LLM. 

**Follow these rules for every tool:**
1. **Always use `@function_tool`.**
2. **Always use Google-style docstrings.** The SDK parses this directly to describe the tool and its arguments to the LLM. 
3. **Always use strict type hints.**
4. **Favor Pydantic `BaseModel` for complex inputs and ALL return types.** This guarantees structured data flows between the LLM and our application.
5. **Never expose `RunContextWrapper` to the docstring.** The LLM does not see it; it is injected by the Runner.

---

## Standard Tool Implementation

For basic inputs, use standard Python types. For the output, define a Pydantic `BaseModel`. The SDK automatically serializes the Pydantic model into a JSON string for the LLM.

```python
from agents import function_tool
from pydantic import BaseModel

class WeatherResponse(BaseModel):
    location: str
    temperature: float
    unit: str

@function_tool
async def fetch_weather(location: str, unit: str = "celsius") -> WeatherResponse:
    """Fetches the current weather for a specified location.

    Args:
        location: The city and state (e.g., 'San Francisco, CA').
        unit: The temperature unit ('celsius' or 'fahrenheit').
        
    Returns:
        A structured weather report.
    """
    # ... execution logic ...
    return WeatherResponse(location=location, temperature=22.5, unit=unit)
```

---

## Advanced: Complex Inputs & Pydantic Validation

When a tool requires nested data, strict validation (like regex or min/max values), or complex objects, use Pydantic `BaseModel` for the inputs. For single constrained arguments, use `typing.Annotated` with `pydantic.Field`.

```python
from typing import Annotated, Any
from agents import function_tool, RunContextWrapper
from pydantic import BaseModel, Field

class UserProfile(BaseModel):
    name: str
    age: int = Field(ge=18, description="User must be at least 18 years old.")
    interests: list[str]

class ProfileResponse(BaseModel):
    status: str
    profile_id: str

@function_tool
def create_user_profile(
    ctx: RunContextWrapper[Any], 
    profile: UserProfile,
    request_id: Annotated[str, Field(..., pattern=r"^req_[0-9]+$")]
) -> ProfileResponse:
    """Creates a new user profile in the database.

    Args:
        ctx: The runtime context. (Ignored by the LLM).
        profile: The validated user profile data.
        request_id: A unique, strictly formatted request identifier.
        
    Returns:
        The execution status and the newly created profile ID.
    """
    # 1. Access injected backend dependencies
    db = ctx.context.db_client
    
    # 2. Execute business logic
    new_id = db.insert_profile(profile.model_dump(), request_id)
    
    # 3. Return a strictly typed Pydantic object
    return ProfileResponse(status="success", profile_id=new_id)
```
