---
paths:
- "*.py"
---

# Pydantic for Structured Data

Any structured data passing into or out of an Agent or that gets serialized **must** be defined using Pydantic `BaseModel` classes. 

Do not use raw Python dictionaries, loose JSON strings, or undocumented `**kwargs`.

## Why?

The OpenAI Agents SDK natively inspects Pydantic models to automatically generate strict, accurate JSON schemas for the LLM. It guarantees type safety, automatic runtime validation, and keeps your FastAPI routes and Agent tools perfectly synced.

## The Pattern

```python
from pydantic import BaseModel, Field
from agents import function_tool

# YES: Explicit Pydantic Model
class CustomerRecord(BaseModel):
    customer_id: str = Field(description="The unique alphanumeric customer ID.")
    lifetime_value: float

@function_tool
def update_customer(record: CustomerRecord) -> CustomerRecord:
    """Updates the customer record."""
    # Logic here
    return record

# NO: Raw Dicts or loose strings
@function_tool
def update_customer_bad(record: dict) -> str:
    """Updates the customer record."""
    pass
```
