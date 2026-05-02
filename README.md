# Voyager — AI Travel Itinerary Optimizer

A multi-agent system that builds constraint-valid, day-by-day travel itineraries from a single natural language request — and re-optimizes them instantly when plans change.

Built for the Columbia IEOR Agentic AI for Analytics capstone.

---

## Features

### Trip Planning
- **Natural language input** — describe a trip in plain text or use the structured Trip Builder form
- **Single-city & multi-city** — automatically detects country/region requests ("Portugal 10 days") and splits nights across canonical city combinations (Lisbon 4n → Porto 3n → Algarve 3n)
- **Constraint-aware scheduling** — respects daily budget, opening hours, dietary restrictions, mobility needs, and must-see/must-exclude places
- **Transit-optimized routing** — uses Google Maps Route Matrix to minimize daily walking/transit time between stops
- **Three slots per day** — morning · afternoon · evening, with a lunch and dinner each day

### Multi-City
- Intake agent detects 20+ country/region patterns and applies canonical night splits
- Per-city hotel, activities, and dining searched independently
- Inter-city transport options (train / flight / bus / ferry) with duration and price range via a static lookup table covering 25+ major routes (Europe, Japan, SE Asia, Americas)
- Travel day slots inserted automatically at city transitions with the right transport emoji

### Navigation & Booking
- **Per-day Google Maps route** — "Navigate Day N" button opens a multi-stop route in Google Maps using real street addresses
- **QR codes** — scannable QR for each day's route, lazy-loaded on demand
- **Booking links** — hotel official sites, activity ticket links, and Maps search links per slot
- All links use real address-based URLs — never placeholder place IDs

### Re-planning
- Report a disruption ("Day 2: Borghese Gallery is closed") and the system re-optimizes affected days
- Locked slots are never moved by the re-planner
- Pulls from the already-fetched candidate pool — no redundant API calls

### Guardrails
- Off-topic requests rejected with a friendly message
- Impossible budgets (<$20 or >$10k/day) caught before planning starts

---

## Architecture

See [`docs/pipeline.md`](docs/pipeline.md) for the full Mermaid pipeline diagram.

```
User
 └─► Guardrails (off-topic · budget sanity)
      └─► OrchestratorAgent  [gemini-2.5-flash]
           ├─► IntakeAgent         → TripRequest + CityLeg list
           ├─► LodgingAgent ×city  → hotel candidates
           ├─► ActivityAgent ×city → attraction candidates
           ├─► DiningAgent ×city   → restaurant candidates
           ├─► TransportAgent      → inter-city options
           └─► SolverAgent         → Markdown itinerary
                                        ↓
                               ReplannerAgent (on disruption)
```

**Agent framework:** OpenAI Agents SDK (`openai-agents`) — specialists exposed via `agent.as_tool()`  
**Models:** `vertex_ai/gemini-2.5-flash` (orchestrator) · `vertex_ai/gemini-2.0-flash` (specialists)  
**Backend:** FastAPI  
**Frontend:** Vanilla HTML/CSS/JS (`static/index.html`)  
**Places data:** Google Places API (New) + local JSON cache (`backend/data/`)  
**Routing:** Google Maps Routes API  

---

## Setup

### Prerequisites
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) package manager
- Google Cloud project with Vertex AI enabled
- Google Maps API key (Places + Routes)

### Install

```bash
git clone https://github.com/Olivery0307/Travel_Planning_Agent.git
cd Travel_Planning_Agent
uv sync
```

### Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
# LLM via Vertex AI
ORCHESTRATOR_MODEL=vertex_ai/gemini-2.5-flash
SPECIALIST_MODEL=vertex_ai/gemini-2.0-flash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1

# Google Maps & Places (one key works for both)
GOOGLE_MAPS_API_KEY=your-key
GOOGLE_PLACES_API_KEY=your-key

# Optional: GCS bucket for shared places cache
PLACES_CACHE_BUCKET=your-bucket-name
```

### Run

```bash
uv run python main.py serve
```

Open [http://localhost:8000](http://localhost:8000).

### CLI (quick test without the UI)

```bash
uv run python main.py ask "5-day Rome trip, couple, $200/day, ancient history"
```

---

## Project Structure

```
.
├── main.py                          # FastAPI server + CLI entry point
├── pyproject.toml
├── .env.example
├── static/
│   └── index.html                   # Single-page frontend
├── docs/
│   └── pipeline.md                  # Mermaid pipeline diagram
├── planner_tracker.md               # Living feature checklist
├── backend/
│   ├── agents/
│   │   ├── orchestrator.py
│   │   ├── intake.py
│   │   ├── lodging.py
│   │   ├── activity.py
│   │   ├── dining.py
│   │   ├── solver.py
│   │   ├── transport.py
│   │   ├── replanner.py
│   │   └── prompts/                 # Markdown system prompts per agent
│   ├── tools/
│   │   ├── places.py                # Google Places API + cache
│   │   ├── routing.py               # Google Maps Routes API
│   │   ├── transport.py             # Inter-city transport lookup
│   │   ├── maps_links.py            # Maps URL builder + QR generator
│   │   ├── geocoding.py
│   │   └── cache.py                 # PlacesCache (local JSON + GCS)
│   ├── models/
│   │   ├── request.py               # TripRequest, ClientProfile, CityLeg
│   │   ├── itinerary.py             # Itinerary, DayPlan, Slot
│   │   ├── places.py                # PlaceResult, LodgingOption, etc.
│   │   ├── disruption.py
│   │   └── context.py               # AppContext, CandidatePool
│   ├── guardrails/
│   │   └── input_validation.py
│   └── data/
│       ├── places_rome.json         # Pre-seeded places cache
│       ├── places_florence.json
│       └── places_bangkok.json
└── scripts/
    └── seed_places_cache.py         # Pre-fetch places for a city
```

---

## Seeding the Places Cache

The system uses a local JSON cache to avoid Google Places API calls during development. Seed a new city:

```bash
uv run python scripts/seed_places_cache.py --city florence --country Italy
```

Rome is pre-seeded and included in the repo.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ORCHESTRATOR_MODEL` | Yes | LiteLLM model string for the orchestrator (e.g. `vertex_ai/gemini-2.5-flash`) |
| `SPECIALIST_MODEL` | Yes | LiteLLM model string for specialist agents |
| `GOOGLE_APPLICATION_CREDENTIALS` | Yes* | Path to GCP service account JSON |
| `GOOGLE_CLOUD_PROJECT` | Yes* | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | Yes* | GCP region (e.g. `us-central1`) |
| `GOOGLE_MAPS_API_KEY` | Yes | Google Maps Routes API key |
| `GOOGLE_PLACES_API_KEY` | Yes | Google Places API key (can be same as Maps key) |
| `PLACES_CACHE_BUCKET` | No | GCS bucket name for shared places cache |

*Either `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION` is required depending on your auth method.
