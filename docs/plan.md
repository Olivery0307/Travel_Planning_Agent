# plan.md — AI Travel Itinerary Optimizer (Capstone)

> Handoff doc for the coding agent. Read this top-to-bottom before writing code. Every section marked **CONTRACT** is a hard requirement. Every section marked **CHOICE** is a decision already made — do not re-litigate.

---

## 1. Objective

Build a deployed multi-agent system that helps independent travel advisors (and self-serve solo travelers) create and dynamically re-plan multi-day trip itineraries. The advisor describes the client and trip goals in natural language — destination, duration, budget, interests, physical constraints — and the system builds a full day-by-day itinerary: lodging, activities, dining, routing. Then — the killer feature — when disruptions happen mid-trip (flight delay, attraction closed, sick day), the advisor inputs the disruption and the system re-optimizes the remaining days, preserving locked commitments.

This is a **capstone for a Columbia IEOR course on Agentic AI for Analytics**. Grading is on five equal-weight axes: deployed and working, business case strength, class concepts used + complexity, technical choices justified by business case, and presentation. Build decisions throughout this doc are optimized for that rubric, not for generic "best software engineering."

### Personas

**Primary — Independent Travel Advisor (B2B)**
> "Marco runs a boutique Italy travel advisory out of Brooklyn. He charges clients $300–500 per trip plan. He's getting squeezed by ChatGPT and needs to produce better plans faster. He has 8 active clients, builds 2–3 itineraries/week, and each one takes him 3–4 hours of research, tab-switching, and spreadsheet wrangling."

- Pays $49–99/month subscription.
- Cares about: client-ready output, constraint handling (dietary, mobility, budget), fast re-planning when something breaks.
- Will use the advisor UI: can set client preferences, lock/unlock days, approve before sharing.

**Secondary — Solo Traveler (B2C self-serve)**
- Pays $9–19/month or per-trip fee.
- Cares about: ease of use, no setup, good defaults.
- Uses a simplified version of the same interface without client management features.

### Success criteria (what "done" means)

1. Deployed at a public URL on Google Cloud Run. Judges can click a link and use it.
2. Advisor types `"5-day Rome trip for a couple, $200/day budget, obsessed with ancient history, one partner has mobility issues, must include Vatican and Colosseum"` and gets a full day-by-day itinerary with morning/afternoon/evening slots, real places (Google Places data), estimated transit times, and a daily budget breakdown — within ~45 seconds.
3. Advisor then inputs `"Day 2 afternoon: Borghese Gallery is closed unexpectedly"` and the system re-plans Day 2 afternoon and evening (and cascading effects on Day 3 if needed) without touching locked commitments.
4. The `README.md` explicitly calls out ≥3 class concepts used, with file/line references.
5. Business one-pager (separate doc) defends unit economics with real token math.

### Non-goals (do NOT build these)

- Actual booking / payment processing. Links to booking sites is enough.
- User accounts with persistent history across sessions. Session state is in-memory, resets on reload.
- Offline maps or downloadable PDFs (stretch goal only).
- Coverage outside Europe + major US cities for the demo. Pick one destination (Rome) as the golden demo path.
- Real-time flight data. Disruptions are manually entered by the advisor.

---

## 2. Stack (CHOICE — do not change)

| Layer | Tech | Why |
|---|---|---|
| Agent framework | **OpenAI Agents SDK** (`openai-agents`) | Rubric requires a framework. Gives us `Agent`, `Runner`, `@function_tool`, `handoffs`, `as_tool`, sessions, tracing — all maps cleanly to class concepts. |
| LLM | `gemini-2.0-flash` for specialist agents; `gemini-2.5-flash` for the orchestrator | Cost/quality balance. Flash is plenty for slot-filling and constraint checking. |
| Backend | **FastAPI** | Clean async, easy Cloud Run deploy, WebSocket support for streaming. |
| Frontend | **React + Vite + Tailwind**, served as static files from FastAPI | More polished than Streamlit for the demo; itinerary-as-artifact needs a real UI. |
| Places data | **Google Places API (New)** — Text Search, Place Details, Nearby Search | Real hours, ratings, photos, coordinates. No scraping. Single API key. |
| Routing | **Google Maps Routes API** — Compute Route Matrix, Directions | Transit times between stops. Batched for cost control. |
| Geocoding | **Google Maps Geocoding API** | Address → lat/lng for places not in Places API. |
| Deploy | **Google Cloud Run** (container) | Matches "Google Cloud" requirement, generous free tier, public URL out of the box. |
| Secrets | Google Secret Manager | Standard for Cloud Run. |
| Observability | OpenAI Agents SDK built-in tracing → OpenAI dashboard | Free, zero-config, directly showcases class concept. |

Python 3.12+. Use `uv` for dependency management (course system requirement).

---

## 3. Architecture

### 3.1 Agent topology (manager + specialists as tools pattern)

```
Advisor / Traveler
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  OrchestratorAgent  (gemini-2.5-flash)                   │
│  - Owns the conversation                                 │
│  - Holds session state (client profile, full itinerary,  │
│    locked slots, disruption log)                         │
│  - Decides: plan mode vs. re-plan mode                   │
│  - Calls specialists as tools                            │
└──────────────────────────────────────────────────────────┘
   │           │            │            │           │
   ▼           ▼            ▼            ▼           ▼
Intake      Lodging     Activity      Dining      Solver
Agent       Agent        Agent        Agent       Agent
(tool)      (tool)       (tool)       (tool)      (tool)
                                                    │
                                               Re-Planner
                                               Agent (tool)
```

**Why manager-as-tools over handoffs:** the orchestrator must maintain itinerary state across planning *and* re-planning turns. The advisor is always talking to the orchestrator. Handoffs would lose the accumulated state and locked-commitment awareness.

**Plan-Execute pattern (Class concept):** The orchestrator first calls Intake to build a `TripPlan` struct (the Plan phase), then sequentially calls Lodging → Activity → Dining → Solver to populate it (the Execute phase). On disruption, it calls Re-Planner directly with the existing plan as context.

### 3.2 Specialists

| Agent | Job | Tools it calls | Returns |
|---|---|---|---|
| `IntakeAgent` | Parse free-text trip request into structured `TripRequest`. Ask one clarifying question if critical fields (destination, duration, budget) are missing. | none (pure LLM) | `TripRequest` Pydantic object |
| `LodgingAgent` | Find hotels/B&Bs matching budget, location, accessibility needs. One lodging recommendation per night-cluster (don't move hotels every night unless asked). | `search_places`, `get_place_details` | `list[LodgingOption]` |
| `ActivityAgent` | Find attractions, museums, tours matching interests + constraints. Tag each with duration, cost, accessibility, booking-required flag, and time-of-day suitability. | `search_places`, `get_place_details`, `get_opening_hours` | `list[ActivityOption]` |
| `DiningAgent` | Find restaurants matching cuisine preferences, budget, dietary restrictions. One per meal slot needed. | `search_places`, `get_place_details` | `list[DiningOption]` |
| `SolverAgent` | Given lodging + activities + dining candidates, sequence them into day-by-day slots (morning/afternoon/evening) that minimize transit time, respect opening hours, and stay within daily budget. | `compute_route_matrix` | `Itinerary` (full structured plan) |
| `RePlannerAgent` | Given the current `Itinerary`, a disruption event, and the set of locked slots, re-optimize affected days only. Explain what changed and why. | `search_places`, `get_place_details`, `compute_route_matrix` | `ItineraryDelta` (changed slots + reasoning) |

All specialists exposed as tools on the orchestrator via `agent.as_tool(...)`.

### 3.3 Session state

Use OpenAI Agents SDK `Session` for conversation history. Plus a custom `RunContext` dataclass:

```python
@dataclass
class AppContext:
    trip_request: TripRequest | None
    itinerary: Itinerary | None           # full current plan
    locked_slots: set[SlotKey]            # (day, period) tuples the advisor locked
    disruptions: list[DisruptionEvent]    # log of all disruptions this session
    candidate_pool: CandidatePool         # all options fetched this session (for re-use)
    advisor_notes: str                    # freeform context from advisor
```

Passed to `Runner.run(..., context=app_context)`. Tools access it via `RunContextWrapper`. On disruption, `RePlannerAgent` reads `itinerary` and `locked_slots` from context — never re-fetches data already in `candidate_pool`.

---

## 4. Repository structure

```
travel-optimizer/
├── README.md                         # Overview, run instructions, deployed URL, class concepts w/ file refs
├── pyproject.toml                    # uv-managed deps
├── uv.lock
├── .env.example                      # OPENAI_API_KEY, GOOGLE_MAPS_API_KEY, GOOGLE_PLACES_API_KEY
├── .gitignore
├── Dockerfile                        # Cloud Run image
├── cloudbuild.yaml                   # One-command deploy
├── plan.md                           # This file
│
├── backend/
│   ├── __init__.py
│   ├── main.py                       # FastAPI app entrypoint, /chat WebSocket, /health
│   ├── config.py                     # Settings via pydantic-settings
│   ├── deps.py                       # Shared singletons (http clients, agents)
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── orchestrator.py           # OrchestratorAgent — plan vs. replan routing
│   │   ├── intake.py                 # IntakeAgent
│   │   ├── lodging.py                # LodgingAgent
│   │   ├── activity.py               # ActivityAgent
│   │   ├── dining.py                 # DiningAgent
│   │   ├── solver.py                 # SolverAgent
│   │   ├── replanner.py              # RePlannerAgent
│   │   └── prompts/                  # One .md file per agent
│   │       ├── orchestrator.md
│   │       ├── intake.md
│   │       ├── lodging.md
│   │       ├── activity.md
│   │       ├── dining.md
│   │       ├── solver.md
│   │       └── replanner.md
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── places.py                 # search_places, get_place_details, get_opening_hours
│   │   ├── routing.py                # compute_route_matrix, get_directions
│   │   └── geocoding.py              # geocode_address
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── request.py                # TripRequest, ClientProfile Pydantic models
│   │   ├── itinerary.py              # Itinerary, DayPlan, Slot, ItineraryDelta
│   │   ├── places.py                 # LodgingOption, ActivityOption, DiningOption, PlaceResult
│   │   ├── disruption.py             # DisruptionEvent model
│   │   └── context.py                # AppContext dataclass, CandidatePool, SlotKey
│   │
│   ├── guardrails/
│   │   ├── __init__.py
│   │   └── input_validation.py       # Off-topic filter, impossible-budget check, destination validation
│   │
│   └── tests/
│       ├── test_intake.py            # Does IntakeAgent extract TripRequest correctly?
│       ├── test_solver.py            # Does SolverAgent produce valid day sequences?
│       ├── test_replanner.py         # Does RePlannerAgent respect locked slots?
│       └── evals/
│           ├── golden_trips.json     # 10 golden trip requests → expected TripRequest + at least one expected place
│           └── run_eval.py           # Class concept: golden dataset eval
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── tailwind.config.js
│   └── src/
│       ├── main.tsx
│       ├── App.tsx                   # Chat pane (left) + itinerary artifact (right)
│       ├── components/
│       │   ├── ChatPane.tsx          # Advisor input, streaming response
│       │   ├── ItineraryView.tsx     # Full trip as scrollable day cards — the live artifact
│       │   ├── DayCard.tsx           # One day: morning/afternoon/evening slots, lock toggle
│       │   ├── SlotCard.tsx          # One slot: place name, photo, hours, transit badge, cost
│       │   ├── DisruptionInput.tsx   # "Something changed" panel — triggers replan
│       │   └── BudgetBar.tsx         # Daily and total budget visualization
│       ├── api/
│       │   └── websocket.ts
│       └── types.ts
│
└── scripts/
    ├── seed_places_cache.py          # One-shot: pre-fetch Google Places data for Rome demo, save to JSON
    ├── build_eval_set.py             # Generate golden_trips.json
    └── deploy.sh                     # gcloud run deploy wrapper
```

---

## 5. Data contracts (build these first, before agent code)

### `TripRequest`
```python
class ClientProfile(BaseModel):
    name: str = ""
    budget_per_day_usd: int                     # hard constraint
    interests: list[str] = []                   # e.g. ["ancient history", "food", "art"]
    dietary_restrictions: list[str] = []        # e.g. ["vegetarian", "gluten-free"]
    mobility_notes: str = ""                    # e.g. "one partner uses a cane, no more than 2km walking/day"
    travel_style: Literal["budget", "mid-range", "luxury"] = "mid-range"
    group_size: int = 2
    group_type: Literal["solo", "couple", "family", "friends"] = "couple"

class TripRequest(BaseModel):
    destination_city: str
    destination_country: str
    start_date: date | None = None              # None = flexible, use generic weekday schedule
    duration_days: int
    client: ClientProfile
    must_include: list[str] = []                # place names that MUST appear
    must_exclude: list[str] = []                # places or categories to skip
    lodging_preference: Literal["hotel", "bnb", "hostel", "any"] = "any"
    notes: str = ""
```

### `Slot` and `DayPlan`
```python
class SlotPeriod(str, Enum):
    MORNING = "morning"         # ~9am-12pm
    AFTERNOON = "afternoon"     # ~12pm-5pm
    EVENING = "evening"         # ~5pm-10pm

class Slot(BaseModel):
    period: SlotPeriod
    place_id: str               # Google Places ID
    place_name: str
    category: Literal["activity", "dining", "transit", "lodging", "free"]
    address: str
    lat: float
    lng: float
    duration_minutes: int
    cost_usd: float
    notes: str = ""             # e.g. "book tickets in advance", "accessible entrance on Via della Greca"
    booking_url: str | None = None
    locked: bool = False        # advisor has locked this slot — replan must not touch it

class DayPlan(BaseModel):
    day_number: int
    date: date | None = None
    slots: list[Slot]           # ordered morning → evening
    daily_cost_usd: float
    lodging: LodgingOption
    transit_summary: str        # e.g. "~4.2km walking, 1 metro leg"

class Itinerary(BaseModel):
    trip_id: str
    request: TripRequest
    days: list[DayPlan]
    total_cost_usd: float
    generated_at: datetime
    version: int = 1            # increments on each replan
```

### `ItineraryDelta` (re-planning output)
```python
class ItineraryDelta(BaseModel):
    disruption: DisruptionEvent
    affected_days: list[int]            # day numbers that changed
    changed_slots: list[Slot]           # new slots replacing the disrupted ones
    removed_slots: list[Slot]           # what was removed
    reasoning: str                      # 2-3 sentences: what changed and why
    new_daily_costs: dict[int, float]   # updated cost per affected day
```

### `DisruptionEvent`
```python
class DisruptionEvent(BaseModel):
    day_number: int
    period: SlotPeriod | None = None    # None = disrupts whole day
    description: str                    # free text: "Borghese Gallery closed", "sick day"
    reported_at: datetime = Field(default_factory=datetime.utcnow)
```

Define all models in `backend/models/` **before** writing any agent code.

---

## 6. Tool contracts (CONTRACT)

Every tool function:

1. Uses `@function_tool` from `openai-agents`.
2. Has a docstring the LLM will actually see — write it for the LLM, not the human.
3. Takes Pydantic-validated inputs.
4. Returns a JSON-serializable object (dict or Pydantic model).
5. Handles errors by returning `{"error": "human-readable message"}` — NEVER raises to the agent loop.
6. Logs tool calls with structured logging for tracing.

### Tools to implement

```python
# tools/places.py
@function_tool
def search_places(
    query: str,
    location: str,
    category: Literal["lodging", "activity", "restaurant", "attraction"],
    max_results: int = 10,
    min_rating: float = 3.5,
    open_now: bool = False,
) -> list[PlaceResult]:
    """Search Google Places for travel-relevant locations matching a query.
    Use category to narrow results. location should be a city name or lat,lng string.
    Returns up to max_results places with name, address, rating, price_level,
    opening_hours summary, and place_id. Always call this before get_place_details
    to get place_ids first."""

@function_tool
def get_place_details(place_id: str) -> PlaceResult:
    """Fetch full details for a specific place by its Google Places place_id.
    Returns name, address, lat/lng, phone, website, opening_hours (all days),
    rating, user_ratings_total, price_level, photos (first 3 URLs), editorial_summary.
    Use this after search_places to get complete data for shortlisted candidates."""

@function_tool
def get_opening_hours(place_id: str, day_of_week: int) -> dict:
    """Get opening hours for a specific place on a given day (0=Monday, 6=Sunday).
    Returns {'open': bool, 'hours': '9:00 AM - 6:00 PM', 'note': str}.
    Use this when you need to schedule a slot and must confirm the place is open."""

# tools/routing.py
@function_tool
def compute_route_matrix(
    origins: list[LatLng],
    destinations: list[LatLng],
    mode: Literal["transit", "walking", "driving"] = "transit",
) -> list[list[RouteResult]]:
    """Compute travel time and distance between multiple origins and destinations.
    Returns matrix[i][j] = RouteResult for origin i to destination j.
    Batch all pairs in one call — never call this in a loop per-pair.
    mode='walking' for <1km legs; 'transit' for cross-city hops."""

@function_tool
def get_directions(
    origin: LatLng,
    destination: LatLng,
    mode: Literal["transit", "walking", "driving"] = "transit",
) -> DirectionsResult:
    """Get turn-by-turn directions between two points. Use only for the final
    itinerary output where a human will follow the route. For sequencing/optimization,
    use compute_route_matrix instead (cheaper)."""

# tools/geocoding.py
@function_tool
def geocode_address(address: str) -> dict:
    """Convert a place name or street address to lat/lng coordinates.
    Returns {'lat': float, 'lng': float, 'formatted_address': str} or {'error': ...}.
    Use when a place_id is not available and you need coordinates."""
```

**Do not** expose API keys to the LLM. Tool wrappers call Google APIs; the LLM only sees structured results.

---

## 7. Build order (3 weeks)

### Week 1: Foundation — end-to-end planning works, ugly is fine

**Day 1-2: Scaffolding**
- `uv init`, add deps: `openai-agents`, `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `httpx`, `googlemaps`.
- Set up repo structure from §4.
- Write all Pydantic models in `backend/models/`.
- Write `.env.example`, `Dockerfile`, basic `main.py` with `/health`.

**Day 3: Google Places tools**
- Implement `search_places` and `get_place_details` against the real Google Places API (New).
- Write a standalone test script: `python scripts/seed_places_cache.py --city rome` — fetches top 50 attractions, restaurants, and hotels. Saves to a local JSON cache for offline dev.
- Verify the data shape matches `PlaceResult`.

**Day 4: IntakeAgent**
- Write `IntakeAgent` with structured output → `TripRequest`.
- CLI test: `python -m backend.cli "5-day Rome trip, $200/day, couple, ancient history focus"` → prints `TripRequest`.
- Verify all fields parse cleanly including nested `ClientProfile`.

**Day 5-6: Specialist agents + routing**
- Implement `compute_route_matrix` against Google Maps Routes API.
- Write `LodgingAgent`, `ActivityAgent`, `DiningAgent` as standalone agents with their tools.
- Write `SolverAgent` — hardest piece. System prompt must explain the sequencing logic: morning attractions first, lunch mid-day, afternoon activity, dinner, honor opening hours, minimize transit.
- CLI script that runs them sequentially and prints a full 3-day itinerary. No orchestrator yet.

**Day 7: Orchestrator + basic web UI + deploy**
- Assemble `OrchestratorAgent`, specialists via `agent.as_tool()`.
- FastAPI WebSocket endpoint `/chat` streaming agent events.
- Minimal React UI: chat input + raw itinerary JSON displayed as formatted text.
- Deploy to Cloud Run. **End of Week 1: live URL, full planning flow works.**

### Week 2: Re-planning + polish

**Day 8-9: RePlannerAgent**
- Write `RePlannerAgent`. This is the demo's killer feature — spend time on the prompt.
- System prompt must: read locked slots from context, only touch affected days, explain changes.
- CLI test: build a 5-day itinerary, inject a disruption on Day 2, verify locked Day 1 slots are untouched.
- Add `DisruptionInput` UI component.

**Day 10: Itinerary UI (the live artifact)**
- Build `ItineraryView`, `DayCard`, `SlotCard` components.
- Each slot shows: place name, photo (Google Places photo URL), opening hours, estimated cost, transit time badge to next slot.
- Lock toggle per slot. Locked slots visually distinct (padlock icon, muted background).
- Real-time updates as WebSocket streams partial results.

**Day 11: Evals (Class concept)**
- `tests/evals/golden_trips.json` — 10 diverse trip requests with expected `TripRequest` extraction + at least one must-appear place name per trip.
- `run_eval.py` scores IntakeAgent accuracy and SolverAgent constraint satisfaction.
- Run it. Fix prompts where it fails.

**Day 12: Guardrails (Class concept)**
- Input guardrail: reject off-topic queries ("write me a poem", "help me code").
- Input guardrail: reject impossible budgets (<$20/day or >$10k/day per person).
- Input guardrail: reject unsupported destination formats (non-city strings).
- Use `openai-agents` built-in guardrail primitives.

**Day 13: Tracing + observability**
- Enable OpenAI Agents SDK tracing.
- Structured logs for every tool call: duration, token count, cache hit/miss.
- `/debug/last-run` endpoint showing last session trace (useful during live demo).

**Day 14: Budget bar + advisor notes**
- `BudgetBar` component: daily cost vs. budget, color-coded (green/yellow/red).
- Advisor notes panel: freeform text the orchestrator reads as context.
- "Export itinerary" button → copies Markdown-formatted plan to clipboard.

### Week 3: Ship

**Day 15-16: Business document**
- Write `business-onepager.md` per §8.
- Run token-math against real traces.
- Get one outsider to try the deployed app (ideally someone who has planned a trip recently).

**Day 17: Demo script + reliability**
- 3 scripted scenarios: (1) basic Rome 5-day plan, (2) same trip with mobility constraints, (3) re-plan after a Day 3 disruption.
- Test each 5x on deployed URL.
- Confirm Places cache fallback works if Google API rate-limits mid-demo.
- Record 90-second backup demo video.

**Day 18: README**
- README per §9.
- File/line references for each class concept.

**Day 19: Presentation deck**
- 5 slides: problem (advisor pain), live demo, architecture, economics, ask.
- Do not read the slides. The live itinerary building IS the demo.

**Day 20: Buffer / dress rehearsal**
- Time the full demo. Must be ≤5 minutes.
- Q&A prep per §10.

---

## 8. Business one-pager — what to put in it

Submit as a separate document. Target 2 pages.

**Section 1: The user.**
> "Marco, 38, runs a boutique Italy travel advisory from Brooklyn. Charges $300–500 per trip plan. Builds 2–3 itineraries/week, each taking 3–4 hours. He's losing clients to people who just use ChatGPT and get 'good enough' plans for free. His edge used to be knowing which trattoria was worth the walk and which museum needed advance booking. Now he needs that edge to be faster too."
>
> Target segment: ~15,000 independent travel advisors in the US (ASTA member estimates). Secondary: ~8M US adults who plan their own international trips/year.

**Section 2: The problem.**
What advisors do today: Google + TripAdvisor research → Google Maps tab per venue → manually check hours → spreadsheet → Word doc → email to client → client asks for changes → repeat. No tool balances multi-constraint optimization (budget × routing × hours × client needs) with fast re-planning. ChatGPT gives a plausible-sounding itinerary but doesn't know the Borghese Gallery requires advance booking and is closed Mondays.

**Section 3: The economics.** *Actually do the math.*

One active advisor-month = 10 itineraries built, ~5 re-plans each, 3 refinement turns per itinerary.

| Item | Per itinerary | Per-month (10 itineraries) |
|---|---|---|
| LLM tokens (intake + 4 specialists + orchestrator + solver) | ~40k in / 8k out on gemini-flash | — |
| LLM cost (~$0.075/1M in, $0.30/1M out on gemini-2.0-flash) | ~$0.005 | $0.05 |
| Google Places API (Text Search $0.032/req × 20 searches + Details $0.017 × 30) | ~$1.15 | $11.50 |
| Google Routes API (matrix call, ~50 elements × $0.005) | $0.25 | $2.50 |
| Re-plan calls (5 × ~30% of above Places cost) | ~$0.42 | $4.20 |
| Cloud Run (negligible at this scale) | — | ~$0 |
| **Total cost to serve** | ~$1.82 | **~$18.25** |
| **Price (advisor tier)** | — | **$79/month** |
| **Gross margin** | — | **~77%** |

Solo traveler self-serve tier: fewer itineraries/month, same per-itinerary cost → price at $19/month, ~65% margin.

The biggest cost variable is **Google Places**, not LLM. Batch place details calls; cache results per session.

**Section 4: Why these technical choices?**

- **Manager-as-tools orchestration:** Advisor needs one coherent conversation across planning + re-planning. Handoffs lose state.
- **Plan-Execute pattern:** Separating intake/planning from execution allows the system to ask one clarifying question before committing to a 5-day structure. Prevents wasted API calls.
- **SolverAgent as a dedicated step:** Sequencing is a constraint satisfaction problem that benefits from a focused prompt, not an afterthought in the planning step.
- **Locked slots in context:** The re-planner must never touch pre-booked, non-refundable slots. Encoding this in `AppContext` rather than prompt-only is the reliable path.
- **Google Places over scraping:** Zero legal risk, structured data, reliable uptime. The cost is justified by reliability.
- **Places cache per session:** Candidate pool stored in `AppContext` — re-plan reuses already-fetched place data, halving API cost on disruptions.

---

## 9. README.md contract

Must contain, in order:

1. **One-line pitch.**
2. **Live URL.** (Cloud Run.)
3. **Demo video** (30-sec gif or Loom, backup in case live is down).
4. **Run locally.** `git clone`, `uv sync`, `cp .env.example .env`, `uv run uvicorn backend.main:app`, visit localhost.
5. **Architecture diagram.** Use a Mermaid block (renders in GitHub).
6. **Class concepts used — with file references.** Rubric-critical. Target ≥3, aim for 6.

Example entries:
- **Multi-agent orchestration (Class 10):** Manager-with-specialists pattern. `backend/agents/orchestrator.py:15-60`. Specialists exposed via `as_tool()` at `orchestrator.py:40-55`.
- **Plan-Execute pattern (Class 9):** OrchestratorAgent first builds `TripRequest` (Plan), then sequentially calls Lodging → Activity → Dining → Solver (Execute). On disruption, jumps directly to RePlannerAgent. `backend/agents/orchestrator.py:70-110`.
- **Tool calling with schema validation (Class 5):** All tools use `@function_tool` with Pydantic inputs. `backend/tools/places.py:20`, `backend/tools/routing.py:15`.
- **State and session memory (Class 8):** `AppContext` carries full itinerary, locked slots, disruption log, and candidate pool across turns. `backend/models/context.py` + `backend/main.py:80-120`.
- **Evaluation with golden datasets (Class 3):** `backend/tests/evals/golden_trips.json` + `run_eval.py`.
- **Guardrails (Class 5):** Input guardrails for off-topic, impossible-budget, and invalid-destination filtering. `backend/guardrails/input_validation.py`.

7. **Business one-pager** (link or inline).
8. **Limitations and what's next.**

---

## 10. Q&A prep

1. **"Why wouldn't an advisor just use ChatGPT?"** → ChatGPT gives plausible text. We give constraint-valid, routing-optimized, hours-verified plans backed by real Google Places data. And re-planning in one click vs. starting a new chat from scratch.
2. **"What's your data source? Is it reliable?"** → Google Places API. Same data Google Maps shows. Opening hours, ratings, and photos are live.
3. **"Why multi-agent? Couldn't one LLM do this?"** → Three reasons: (a) token efficiency — gemini-flash specialists are 10x cheaper than running everything through the orchestrator, (b) evaluability — we can eval IntakeAgent and SolverAgent independently, (c) the re-planner needs a focused prompt that only considers the disrupted days, not the full planning context.
4. **"How does re-planning work technically?"** → `RePlannerAgent` receives the current `Itinerary` + `locked_slots` + disruption description. It fetches replacement options from the `candidate_pool` (already in context) before calling Google Places again. It outputs an `ItineraryDelta` — only changed slots, not the full plan.
5. **"What if Google Places data is wrong or stale?"** → We surface the last-updated timestamp and link to the Google Maps listing. Production path would add a user-reported corrections layer.
6. **"How do you handle very different destinations? You only demo Rome."** → The agents are destination-agnostic; only the seeded places cache is Rome-specific. Any city with Google Places coverage works. Rome is the golden demo path for reliability.
7. **"What's defensible in a year when every AI tool does itineraries?"** → The defensibility is: (a) advisor-specific workflow (lock/re-plan, client profile, export), (b) constraint satisfaction that general chat doesn't do reliably, (c) the business model — we're advisor-aligned, not ad-supported.
8. **"What's the hardest part of the build?"** → SolverAgent: getting a consistent, opening-hours-valid, budget-valid day sequence from an LLM requires careful prompt engineering and output validation. Second hardest: re-planner respecting locked slots reliably.
9. **"Did you run evals? Show one."** → Pull up `golden_trips.json`, run one live against IntakeAgent.
10. **"Why OpenAI Agents SDK over LangGraph or ADK?"** → Primitives (`Agent`, `as_tool`, guardrails, tracing) map 1:1 to class concepts. ~100 lines of glue vs LangGraph's graph machinery. Code-first, small surface area, built-in tracing for observability demos.

---

## 11. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Google Places rate limit mid-demo | High | Seed a JSON cache for Rome during dev. `search_places` checks cache first. |
| SolverAgent produces invalid sequences (closed venue, over budget) | High | Output validation in `backend/models/itinerary.py` — re-prompt once on failure. |
| Re-planner touches locked slots | High | Locked slots injected into system prompt AND checked post-generation in code. |
| Cloud Run cold start kills first demo query | Medium | Set min_instances=1. Warm with synthetic request before presenting. |
| Agent loops forever | Medium | `Runner.run(..., max_turns=20)`. Return partial results + error message. |
| Google Places API cost overrun during dev | Medium | Dev uses seeded cache by default. Live API calls gated behind `USE_LIVE_PLACES=true` env flag. |
| Demo wifi fails | Low | Record backup video. Run backend locally with ngrok. |
| Solo traveler persona dilutes the pitch | Low | Mention as self-serve tier, don't demo it. Advisor flow is the demo. |

---

## 12. Deployment (Cloud Run)

### Dockerfile
```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/ backend/
COPY frontend/dist/ frontend/dist/
ENV PORT=8080
CMD uv run uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

### Deploy
```bash
gcloud builds submit --tag gcr.io/$PROJECT_ID/travel-optimizer
gcloud run deploy travel-optimizer \
  --image gcr.io/$PROJECT_ID/travel-optimizer \
  --region us-east4 \
  --allow-unauthenticated \
  --min-instances 1 \
  --set-env-vars "ENV=prod" \
  --set-secrets "OPENAI_API_KEY=openai-key:latest,GOOGLE_MAPS_API_KEY=gmaps-key:latest,GOOGLE_PLACES_API_KEY=places-key:latest"
```

### Frontend build
```bash
cd frontend && npm install && npm run build
# Output goes to frontend/dist/, served as static by FastAPI
```

---

## 13. Coding agent: start here

Execute in this order:

1. Read §1, §2, §3 fully. Confirm the stack choices.
2. Set up repo skeleton per §4. Don't skip files listed there.
3. Write all Pydantic models in `backend/models/` per §5. Write validation tests.
4. Write tool stubs in `backend/tools/` per §6 with dummy returns. Wire into a test agent to confirm `@function_tool` registration works.
5. Run `scripts/seed_places_cache.py --city rome` to build the offline dev cache. Commit the JSON.
6. Build agents bottom-up: `IntakeAgent` first (verify with CLI), then `LodgingAgent` + `ActivityAgent` + `DiningAgent`, then `SolverAgent`, then compose.
7. FastAPI + WebSocket + minimal UI. Deploy to Cloud Run by end of Week 1.
8. Only after end-to-end planning works: build `RePlannerAgent`, then add guardrails, evals, UI polish.

Do not write speculative code. Every file should have a caller before it's written. Keep PRs small.

If you hit an ambiguous decision not covered here, default to the simplest choice that satisfies the rubric, and leave a `# TODO(plan):` comment citing the specific unknowns.
