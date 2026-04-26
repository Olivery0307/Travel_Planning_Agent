# planner_tracker.md — Voyager AI Travel Optimizer: Live Progress Tracker

> **For the coding agent:** Read this file before writing any code. It reflects the *current* state of the project. `plan.md` is the original architectural spec — treat it as background context. This file wins on conflicts about what's already done.

---

## Current Stack (locked — do not change)

| Layer | Choice |
|---|---|
| Agent framework | OpenAI Agents SDK (`openai-agents`) |
| LLM | `vertex_ai/gemini-2.0-flash` (specialists), `vertex_ai/gemini-2.5-flash` (orchestrator) |
| Backend | FastAPI (`main.py` at project root) |
| Frontend | Vanilla HTML/CSS/JS (`static/index.html`) — **not** the React/Vite plan from `plan.md` |
| Places data | Google Places API (New) + local JSON cache in `backend/data/` |
| Routing | Google Maps Routes API |
| Deploy | Google Cloud Run |

---

## Phase 1 — Core Planning Pipeline

- [x] Pydantic models: `TripRequest`, `ClientProfile`, `Slot`, `DayPlan`, `Itinerary`, `ItineraryDelta`, `DisruptionEvent`, `AppContext`, `CandidatePool`
- [x] `PlaceResult`, `LodgingOption`, `ActivityOption`, `DiningOption` models
- [x] Google Places tools: `search_places()`, `get_place_details()`, `get_opening_hours()`
- [x] Google Maps tools: `compute_route_matrix()`, `get_directions()`
- [x] `PlacesCache` — local JSON cache (`backend/data/`) + GCS sync
- [x] Seeded cache: `places_rome.json` (~1.7 MB), `places_florence.json`, `places_bangkok.json`
- [x] `IntakeAgent` — parses free-text → `TripRequest` structured output
- [x] `LodgingAgent` — finds hotels/B&Bs via `search_places()`
- [x] `ActivityAgent` — finds attractions via `search_places()` + `get_opening_hours()`
- [x] `DiningAgent` — finds restaurants via `search_places()`
- [x] `SolverAgent` — sequences candidates into day-by-day Markdown itinerary using `compute_route_matrix()`
- [x] `OrchestratorAgent` — manages sequential plan flow (intake → lodging → activity → dining → solver)
- [x] `ReplannerAgent` — re-optimizes affected days on disruption, respects locked slots
- [x] FastAPI `/chat` endpoint with session management (`InMemorySession` + `AppContext`)
- [x] CLI commands: `ask`, `serve`
- [x] Input guardrails: `off_topic_guardrail`, `budget_sanity_guardrail`

## Phase 2 — Frontend & UX Polish

- [x] Single-page frontend (`static/index.html`) — obsidian + gold design
- [x] Chat pane (left, 420px) + Itinerary pane (right)
- [x] Quick Chat mode (natural language textarea)
- [x] Trip Builder form mode (structured fields: destination, days, budget, interests pills, dietary, must-see, mobility)
- [x] Itinerary parser: regex scans `**Day N**` headers + emoji-marked slots from Markdown
- [x] Day cards: collapsible, animated, cost bar
- [x] Disruption detection hint ("Re-plan mode detected…")
- [x] Session persistence via `session_id`
- [x] Rate limiting fix (exponential backoff on 429/503)

## Phase 3 — Infra & Reliability

- [x] Dockerfile
- [x] `cloudbuild.yaml`
- [x] GCS cache sync (optional, env-gated)
- [x] LiteLLM retries: 3 retries, 5s/10s/20s backoff
- [x] `backend/tests/evals/golden_trips.json` + `run_eval.py`

---

## Phase 4 — Map Links & Booking Integration  🚧 IN PROGRESS

### 4A — Per-Day Google Maps Route URLs  ← implement first
- [x] `backend/tools/maps_links.py` — `build_maps_route_url()`, `build_place_maps_url()`, `qr_code_base64()`
- [x] Solver prompt updated: emit `[🗺 Navigate Day N on Google Maps](url)` + `[QR_DAY_N](url)` markers per day
- [x] `GET /qr?url=...` endpoint in `main.py` — returns `{ data_uri: "data:image/png;base64,..." }`
- [x] Frontend `parseItinerary()` captures `mapsUrl` + `qrUrl` per day from Markdown markers
- [x] Frontend renders "Navigate Day N" button + "QR Code" toggle button in a `.day-nav-bar` per day card
- [x] QR panel lazy-loads image via `/qr` endpoint on first open (toggleable)
- [x] Dependencies: `qrcode`, `Pillow` added to `pyproject.toml`

### 4B — Booking & Website Links Per Slot
- [x] Solver prompt updated: emit `[Book / Official Site](url)` for lodging, `[Book Tickets](url)` for activities, `[View on Google Maps](url)` for restaurants
- [x] Frontend `parseLine()` extracts `link: { label, url }` from slot Markdown
- [x] Frontend renders `.slot-link` anchor buttons per slot when link is present

### 4C — Link & Map Quality Fixes (done)
- [x] Maps route URL now uses real **addresses** (not place names) to avoid Google resolving ambiguous names to wrong countries
- [x] Restaurant Maps links use `https://www.google.com/maps/search/?api=1&query=ENCODED_ADDRESS` (always valid, no place_id needed)
- [x] Solver prompt: no restaurant may repeat across the itinerary; dining agent returns 3× days worth of options
- [x] Frontend link labels normalized: "Maps", "Book Tickets", "Book" — consistent across all slots

### 4D — Convenience Ideas (future checkpoints)
- [ ] **Dedicated BookingAgent** — separate agent to find real booking URLs for hotels (Booking.com/Airbnb deep links) and activity tickets (Viator/GetYourGuide). Needs affiliate API keys. Currently website field from Google Places covers ~85% of cases. Add to Phase 5 or post-capstone.
- [ ] **Viator/GetYourGuide deep links** — ticket booking for activities: `https://www.viator.com/search/CITY+ATTRACTION` (no API key for basic search links).
- [ ] **TripAdvisor review links** — `https://www.tripadvisor.com/Search?q=PLACE_NAME` per slot.
- [ ] **Export to `.ics` calendar** — one calendar event per slot with location and notes; downloadable from itinerary pane.
- [ ] **Copy itinerary as Markdown** — "Copy to clipboard" button; wire it up.
- [ ] **Share link / PDF export** — generate a shareable snapshot URL or PDF of the itinerary.
- [ ] **Weather forecast widget** — embed Open-Meteo (free, no key) forecast for destination dates on each day card.

---

## Phase 5 — Multi-City Planning  📋 TO DO

### 5A — IntakeAgent: multi-city detection & splitting
- [ ] Extend `TripRequest` with `destinations: list[CityLeg]` where `CityLeg` has `city: str`, `country: str`, `nights: int`.
  - Keep `destination_city` / `destination_country` as a single-city shortcut (populated from `destinations[0]` for backward compat).
- [ ] Add `CityLeg` Pydantic model to `backend/models/request.py`.
- [ ] Update `IntakeAgent` prompt (`backend/agents/prompts/intake.md`) to:
  - Detect multi-city intent: country names, region phrases ("Portugal trip", "Northern Italy", "Scandinavia 10 days").
  - Apply canonical city-split heuristics for top destinations (Portugal → Lisbon + Porto; Japan → Tokyo + Kyoto + Osaka; etc.).
  - Allocate nights proportionally by city importance and total duration.
  - Populate `destinations` list; set `duration_days` = sum of all nights.

### 5B — OrchestratorAgent: per-city planning loop
- [ ] Update `OrchestratorAgent` to iterate over `destinations` list:
  - For each `CityLeg`, run the full Lodging → Activity → Dining pipeline scoped to that city.
  - Accumulate `CandidatePool` per city.
  - Pass all city pools to Solver together.
- [ ] Cache keying already supports multi-city (`(city, category)` key) — no change needed.

### 5C — Unified Solver: city-transition awareness
- [ ] Update `SolverAgent` prompt to:
  - Accept multi-city candidate pool.
  - Assign days to cities based on `CityLeg.nights`.
  - Insert a **Travel Day** slot at city transitions (e.g., Day 3: "Travel Lisbon → Porto").
  - Travel day slots: category = `"transit"`, duration = travel time, `cost_usd` = estimated transport cost.
  - Use a new `get_intercity_transport()` tool (see 5D) for travel time + cost between cities.
  - Per-city hotel: `lodging` assigned per city cluster, not per trip.

### 5D — TransportAgent: real inter-city options
- [ ] Add `TransportAgent` (`backend/agents/transport.py`) with:
  - Tool: `get_intercity_transport(origin_city, destination_city, date)` → returns list of options (train, bus, flight) with price range and duration.
  - Initial implementation: static lookup table for top routes (Lisbon→Porto, Rome→Florence, Paris→Amsterdam, etc.) — no external API needed for MVP.
  - Phase 2: integrate Trainline API or Rome2Rio API for live pricing (add to future checkpoints below).
- [ ] `TransportAgent` exposed as tool on `OrchestratorAgent`.
- [ ] Solver inserts the best transport option as a `transit` slot on city-change days.

### 5E — Future transport checkpoints
- [ ] **Rome2Rio API** integration for real inter-city routes + prices (free tier available).
- [ ] **Flight search** via Skyscanner/Amadeus API for city pairs requiring flights.
- [ ] **Seat61 / Trainline** static data for European train routes.
- [ ] **Multi-city budget tracking** — per-city daily budget breakdown + inter-city transport budget line.

---

## Phase 6 — Stretch Goals (post-capstone)

- [ ] Real-time flight disruption monitoring → auto-trigger re-plan
- [ ] Group dynamics: separate preference profiles per traveler, merged into one plan
- [ ] Accessibility rating layer: wheelchair/cane scoring per venue beyond the notes field
- [ ] Weather-aware scheduling: reschedule outdoor activities on rain days (Open-Meteo)
- [ ] Review aggregation: pull TripAdvisor + Google reviews, sentiment summary per venue
- [ ] Persistent user accounts with saved itineraries across sessions

---

## Key File Map (for quick navigation)

| What | Where |
|---|---|
| FastAPI server + session mgmt | `main.py` |
| Orchestrator | `backend/agents/orchestrator.py` |
| Intake agent | `backend/agents/intake.py` + `backend/agents/prompts/intake.md` |
| Solver agent | `backend/agents/solver.py` + `backend/agents/prompts/solver.md` |
| Replanner | `backend/agents/replanner.py` |
| Places tools | `backend/tools/places.py` |
| Routing tools | `backend/tools/routing.py` |
| Cache | `backend/tools/cache.py` |
| Data models | `backend/models/` (request, itinerary, places, context, disruption) |
| Guardrails | `backend/guardrails/input_validation.py` |
| Frontend | `static/index.html` |
| Seeded data | `backend/data/places_rome.json`, `places_florence.json`, `places_bangkok.json` |
| Evals | `backend/tests/evals/golden_trips.json` + `run_eval.py` |
