# Todo — Next Session

---

## 1. Feature Decisions (decide before building)

### 1a. Panic Mode
**Proposal:** When an itinerary exists and the user types urgency-style input ("Vatican closed today", "now", "fix it"), trigger a visually distinct "Panic Mode" — a red banner, pre-filled disruption form, and fast-path directly to `replanner_agent` bypassing the normal chat UX.

**Trigger heuristic:**
- `AppContext.disruption_count > 0` OR urgency keywords in message (`today`, `now`, `closed`, `cancelled`, `sick`, `delayed`, `broken`)
- Frontend detects this and renders the panic UI; backend routes identically to normal replan

**Work needed:**
- Frontend: red animated banner, pre-filled input with detected venue/day, "Fix Now" button
- Backend: nothing new — replanner already handles it; just tighten the orchestrator routing heuristic
- Decision point: is the UX value worth the frontend complexity? Or is the existing "Re-plan mode detected" hint sufficient?

### 1b. Transport Checkpoints (Phase 5E)
**Proposal:** Real inter-city transport data via Rome2Rio or Skyscanner.

**Options:**
- Rome2Rio API (free tier, ~1000 req/day) — real routes + prices for train/bus/flight/ferry
- Skyscanner/Amadeus — flight search only, needs API key approval
- Current state: static lookup table covers 25+ common routes; works for the demo

**Decision point:** Is the static table good enough for the capstone demo, or does real pricing add meaningful differentiation? Rome2Rio is low-effort to add.

### 1c. Stricter Budget Checking
**Proposal:** Include hotel cost and transportation cost in the daily budget cap enforced by the solver and tracked in the cost bar.

**Current state:** Solver caps activities + dining per day. Hotel nightly rate is shown but not deducted from the daily budget. Inter-city transport costs are shown on travel days but not summed against budget.

**Work needed:**
- Update solver prompt: deduct lodging_cost_per_night from budget_per_day before allocating activities/dining
- Update `ItineraryDelta.new_daily_costs` to include lodging + transport line items
- Frontend cost bar: show lodging as a distinct color segment
- Decision point: this changes how the solver allocates — may shrink activity budget significantly for budget travelers

---

## 2. Weather Forecasting — Verify Correct Implementation

**Questions to answer:**
- Does `start_date` actually get parsed from natural language queries now (after today's fix)?
- Does `get_weather_forecast` get called by the orchestrator when `start_date` is present?
- Does the weather badge render on day card headers in the frontend (`_weatherBadge()` in `static/index.html`)?
- Is `ctx.weather_data` being set and returned in the `done` SSE event's `weather` field?
- Historical mode (>16 days): does `is_forecast: false` correctly show `~` prefix on badges?

**Steps:**
1. Run a query with explicit date within 16 days → check SSE `done` event for `weather` field
2. Run a query with date 3 months out → verify historical mode activates (`is_forecast: false`)
3. Confirm weather badges appear on day card headers in the rendered itinerary
4. Check that `outdoor_suitable` flag actually influences solver activity selection (needs a rainy-day test)

---

## 3. Eval Sets — Creation and Testing

### 3a. Planner evals (`golden_trips.json` — already exists)
- Review existing golden trips: are they still valid given the parallel-specialists + no-detail-calls changes?
- Run `run_eval.py` and check pass rate
- Add 2–3 multi-city scenarios (Lisbon→Porto, Tokyo→Kyoto→Osaka)
- Add 1 mobility scenario (cane/wheelchair user)

### 3b. Replanner evals (`golden_disruptions.json` — does NOT exist yet)
Create `backend/tests/evals/golden_disruptions.json` with ~8 scenarios:

| # | Disruption type | Starting trip | Locked slots | Expected outcome |
|---|---|---|---|---|
| 1 | Venue closed | 5-day Rome | None | Day 3 morning swapped |
| 2 | Sick day | 5-day Rome | hotel locked | Day 2 lightened, hotel kept |
| 3 | Bad weather | 3-day Florence | None | Outdoor slots → indoor |
| 4 | Budget cut | 5-day Rome | None | Cheaper alternatives |
| 5 | Opportunity | 5-day Rome | Day 1 morning locked | New slot inserted, locked preserved |
| 6 | Transit delay | 7-day Portugal multi-city | transport locked | Day shifted forward |
| 7 | Group preference shift | 5-day Bangkok | dinner slot locked | More food, fewer museums |
| 8 | Multi-slot disruption | 5-day Rome | 2 slots locked | Only unlocked slots change |

Write `run_replan_eval.py` scoring:
- Lock integrity: 0 locked slots touched (hard pass/fail)
- Surgical edit: only `affected_days` changed
- Budget: `new_daily_costs` within ±20% of original
- Delta structure: `changed_slots` or `removed_slots` non-empty

---

## Notes / Context from Today

- **Performance:** Parallel specialist calls now work (confirmed via SSE log). Estimated ~40–50s saved on cached trips.
- **Itinerary rendering bug:** Gemini wraps solver output in preamble + ` ```text ``` ` fence. Fixed via `_extract_itinerary_text()` in `main.py` — strips fence and preamble before detection/storage/SSE. **Needs restart to take effect.**
- **Date parsing:** `start_date` is now optional — intake infers it, never asks. Orchestrator skips weather silently if null. **Needs restart.**
- **Active branch:** `feature/backend` (server). `combined` branch has all merged features but isn't the running branch.
- **Prompt files are read at import time** — any prompt change requires server restart.
