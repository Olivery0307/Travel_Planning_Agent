# replanner_tracker.md — Voyager AI Re-Planner: Live Progress Tracker

> **For the coding agent:** Read this file alongside `planner_tracker.md` before writing any re-planning code. `plan.md` is the original architectural spec — treat it as background. `planner_tracker.md` tracks the *building* side; this file tracks the *adapting* side. The re-planner is the defensible moat — anyone can ask ChatGPT for an itinerary, but nothing else lets a travel advisor pivot a live trip in 30 seconds without losing locked commitments.

---

## Why the re-planner is the differentiator

A planner that builds a Day-1 itinerary is a commodity — ChatGPT does it for free. The re-planner is what an advisor *pays for*:

- **State preservation:** locked hotels, pre-paid tickets, group commitments survive every disruption.
- **Surgical edits:** only the affected days change; the rest of the itinerary is untouched.
- **Context reuse:** the candidate pool from the original plan is reused, so re-planning is faster and cheaper than starting over.
- **Mid-trip relevance:** disruptions happen *during* the trip, when the user has no time to re-research from scratch.

Grading axes this maps to: **business case strength** (advisor pays for re-planning, not the first plan), **class concepts** (state, context, plan-execute-adapt loop), **technical justification** (why a dedicated agent + delta output, not a full re-plan).

---

## Current Stack (locked — same as planner)

| Layer | Choice |
|---|---|
| Agent framework | OpenAI Agents SDK (`openai-agents`) |
| LLM | `vertex_ai/gemini-2.0-flash` (replanner specialist), orchestrator routes to it |
| Output type | `ItineraryDelta` (Pydantic, structured) |
| State carrier | `AppContext.itinerary_json` + `locked_slots` + `candidate_pool` + `disruptions` |
| Frontend | Vanilla HTML/JS (`static/index.html`) — disruption input + diff rendering |

---

## Phase R1 — Core Re-Planning Loop  ✅ DONE

- [x] `DisruptionEvent` Pydantic model — `day_number`, `period`, `description`, `reported_at`
- [x] `ItineraryDelta` Pydantic model — `disruption`, `affected_days`, `changed_slots`, `removed_slots`, `reasoning`, `new_daily_costs`
- [x] `RePlannerAgent` with `search_places`, `get_place_details`, `compute_route_matrix` tools
- [x] `replanner.md` system prompt — read locked slots, only touch affected days, explain changes
- [x] Orchestrator routes disruption messages → `RePlannerAgent` (re-plan mode detected)
- [x] `AppContext.itinerary_json` captured after solver run, fed to replanner on next turn
- [x] Frontend hint: "Re-plan mode detected…" when user types disruption-style input
- [x] Session continuity: same `session_id` carries itinerary + locked slots across turns
- [x] Input guardrails (`off_topic_guardrail`, `budget_sanity_guardrail`) protect the replanner endpoint too

---

## Phase R2 — Locked-Slot Integrity  🚧 PARTIAL

The replanner is told via prompt to never touch locked slots. It needs to be *enforced* in code, not trusted to the LLM.

- [x] Prompt rule: "Never change locked slots."
- [ ] **Lock toggle in the frontend** — per-slot padlock icon on each `SlotCard`. Click to lock/unlock.
- [ ] **Persist locked state** — `AppContext.locked_slots: set[SlotKey]` updated when user toggles in UI; sent on every `/chat` POST.
- [ ] **Post-generation validator** — after `RePlannerAgent` returns an `ItineraryDelta`, drop any `changed_slots` whose `(day, period)` is in `locked_slots`. Re-prompt the agent once with a "you violated lock X, try again" message if a locked slot was touched.
- [ ] **Visual lock state** — locked slots render with muted background + padlock icon; replanner output explicitly highlights "Day 3 morning is locked, kept as-is."
- [ ] **Auto-lock heuristics** — slots with `booking_url` resolved (e.g., pre-paid Colosseum tickets) auto-suggest a lock; advisor confirms.

---

## Phase R3 — Disruption Taxonomy  📋 TO DO

Right now `DisruptionEvent.description` is free text. A taxonomy unlocks targeted handling, better evals, and a click-driven UI.

- [ ] Add `DisruptionType` enum to `backend/models/disruption.py`:
  - `VENUE_CLOSED` — attraction/restaurant/hotel unexpectedly closed
  - `WEATHER` — rain/heat/snow makes outdoor activities unsuitable
  - `HEALTH` — sick day / fatigue → low-effort schedule
  - `TRANSIT_DELAY` — flight/train delay shifts arrival time
  - `GROUP_PREFERENCE_SHIFT` — "we want more food, less museums on Day 4"
  - `BUDGET_CHANGE` — daily budget cut/raised mid-trip
  - `SAFETY` — strike, protest, area closure
  - `OPPORTUNITY` — "tickets opened up for X tomorrow, can we squeeze it in?"
- [ ] `DisruptionEvent.disruption_type: DisruptionType | None` — IntakeAgent (or a new `DisruptionIntakeAgent`) classifies free-text into the enum.
- [ ] **Type-specific replanner prompt branches:**
  - Weather → prefer indoor alternatives, reuse same neighborhood.
  - Health → cut total walking distance, push intense activities later, add buffer slots.
  - Transit delay → cascade scheduling: shift everything in the affected day forward, drop overflow.
  - Opportunity → not a "fix" but an insertion; ask which existing slot to displace.
- [ ] **Quick-action UI buttons** — "Venue closed", "Sick day", "Bad weather", "Flight delayed" — preset disruption types, one-click trigger.

---

## Phase R4 — Cascade & Multi-Day Re-Planning  📋 TO DO

A morning delay on Day 2 may bleed into the afternoon and possibly Day 3. Replanner currently handles one day at a time per prompt.

- [ ] **Cascade detection** — replanner checks if a changed slot's new end-time conflicts with the next slot's start-time or opening hours. If yes, mark the next slot as also affected.
- [ ] **`affected_days` accuracy** — verify the field actually reflects all touched days, not just the disruption's day.
- [ ] **Multi-day delta rendering** — frontend shows a "Days 2-3 changed" banner with collapsible per-day diff.
- [ ] **Cascade budget rebalance** — if Day 2 went over budget, replanner can offer to shave Day 3 to compensate; advisor approves.
- [ ] **End-of-trip absorption** — if a disruption can't be absorbed in the remaining days, replanner explicitly says so ("you'll lose the Pantheon visit; here's the closest alternative").

---

## Phase R5 — Diff UI  📋 TO DO

Right now the frontend shows the full re-planned Markdown. Advisors need to *see what changed*.

- [ ] **`ItineraryDelta` rendering pane** — replaces the full itinerary view in re-plan mode.
  - Removed slots: red strikethrough card with "Why removed" hover.
  - New slots: green-bordered card with "Why chosen" reasoning.
  - Unchanged slots: collapsed/dimmed, click to expand.
- [ ] **"Show diff" / "Show full plan" toggle** — advisor can switch between delta-only and full-itinerary views.
- [ ] **Reasoning surfaced inline** — `ItineraryDelta.reasoning` shown as a banner above the diff, not buried in chat.
- [ ] **Cost delta** — "+$28 today" or "−$15 today" badge per affected day, color-coded.
- [ ] **Undo last re-plan** — keep the previous `Itinerary` version in `AppContext.itinerary_history: list[Itinerary]`. One-click revert.
- [ ] **Re-plan history log** — sidebar showing all disruptions handled this session ("Day 2 afternoon: Borghese closed → swapped to Capitoline Museums").

---

## Phase R6 — Candidate Pool Reuse  🚧 PARTIAL

Re-planning should be ~50% cheaper than original planning by reusing already-fetched places. Verify and instrument.

- [x] `CandidatePool` exists in `AppContext` and is populated during initial planning.
- [ ] **Replanner prompt explicitly references pool** — "Before calling search_places, check the candidate_pool tool/context for already-fetched options matching the disruption."
- [ ] **Pool surfaced as a tool** — `get_candidates_from_pool(category, near_lat_lng, exclude_place_ids)` → returns shortlist from `AppContext.candidate_pool`. No API cost.
- [ ] **Cache hit metric** — log per-replan: `places_api_calls_saved`. Surface in `/debug/last-run` for the demo.
- [ ] **Pool freshness check** — pool entries older than 24h re-fetch opening hours (cheap, just `get_opening_hours`).
- [ ] **Pool expansion on miss** — if no pool candidate fits, fall back to `search_places` and add new results back to the pool.

---

## Phase R7 — Proactive Disruption Detection  📋 TO DO (BONUS)

Move from reactive ("user reports a disruption") to proactive ("system warns the advisor").

- [ ] **Opening hours pre-check** — when itinerary is generated, replanner-as-validator scans every slot's opening hours against the trip dates. Flags closures (e.g., "Borghese closed Mondays, Day 3 is a Monday").
- [ ] **Weather-aware pre-check** — pull Open-Meteo forecast for trip dates; flag outdoor slots on rain days. Suggests indoor swaps.
- [ ] **Holiday/strike calendar** — static lookup for major destination cities (Italian public holidays, French strike days). Flags affected dates.
- [ ] **Booking-required warnings** — slots tagged `booking_required: true` without a `booking_url` get an "advisor: confirm booking" badge.
- [ ] **Travel-time sanity check** — flag slot transitions exceeding `walking_max_km` from `ClientProfile.mobility_notes`.

---

## Phase R8 — Conversational Re-Planning  📋 TO DO

Disruption → fix is one-shot today. Real advisors negotiate: "what if instead of Capitoline, we did Trastevere walk?"

- [ ] **Multi-turn replanner session** — replanner stays "active" after a delta is produced; next user message is interpreted as a refinement of that delta, not a new disruption.
- [ ] **"Try again with X" support** — advisor can constrain the re-plan: "but cheaper", "but indoors", "but closer to the hotel".
- [ ] **Top-N alternatives** — replanner returns 2-3 candidate replacements per disrupted slot, advisor picks. Not just one.
- [ ] **Approve/reject per slot** — checkbox UI on the diff view; rejected slots trigger a new search, approved slots commit to the itinerary.
- [ ] **Replanner ↔ advisor handoff** — if the disruption is ambiguous (e.g., "things aren't working"), replanner asks a clarifying question instead of guessing.

---

## Phase R9 — Re-Plan Evaluation  📋 TO DO

Class concept hook: golden disruption dataset. Mirrors the planner's `golden_trips.json`.

- [ ] `backend/tests/evals/golden_disruptions.json` — 15 disruption scenarios, each with:
  - Starting itinerary (fixed)
  - `DisruptionEvent` payload
  - `locked_slots` set
  - Expected: which days should change, which should NOT, budget delta range
- [ ] **`run_replan_eval.py`** — runs each scenario, scores:
  - Lock integrity: 0 locked slots touched
  - Surgical edit: only `affected_days` changed
  - Budget: `new_daily_costs` within ±15% of original daily budget
  - Coherence: no two slots overlap in time; opening hours respected
- [ ] **CI gate** — replan eval must pass before deploy. Fail = block merge.
- [ ] **Eval results in README** — "Re-planner: 14/15 scenarios pass lock integrity, 13/15 surgical-edit."

---

## Phase R10 — Observability for Re-Planning  📋 TO DO

The demo needs to *show* the re-planner doing its job. Tracing makes that legible.

- [ ] **Re-plan trace tag** — every replanner invocation tagged with `mode=replan` in OpenAI Agents SDK trace.
- [ ] **`/debug/last-replan` endpoint** — returns: original itinerary, disruption, delta, locked slots respected, places API calls (saved vs. made), reasoning.
- [ ] **Demo overlay** — toggle in the UI showing "Re-plan took 8.2s, 3 places API calls saved by cache, 0 locked slots touched."
- [ ] **Token cost per re-plan** — logged + surfaced for the business one-pager's economics section.

---

## Phase R11 — Stretch Goals (Bonus / post-capstone)

- [ ] **Real-time flight monitoring** — integrate FlightAware/AviationStack; auto-detect delay → propose re-plan before advisor asks.
- [ ] **Push notification to advisor** — when a proactive disruption is detected (e.g., strike announced), email/SMS the advisor.
- [ ] **Per-traveler re-plan** — group with split preferences: replanner can re-plan only one traveler's day (e.g., one rests, others tour).
- [ ] **Re-plan undo stack** — full version tree of itinerary, not just "last." Branch and compare scenarios.
- [ ] **"Why this not that?" explainer** — advisor clicks a rejected candidate and replanner explains why it wasn't chosen.
- [ ] **Cost-aware re-plan** — replanner accepts a hard budget ceiling and re-allocates remaining days to absorb overruns.
- [ ] **Re-plan with `must_include` preservation** — if `must_include` venues haven't been visited yet, replanner protects their slots from being dropped.
- [ ] **`.ics` calendar push on re-plan** — updated calendar invite auto-generated for each affected day.
- [ ] **Multi-city re-plan** (depends on planner Phase 5) — disruption in one city cascades to inter-city transport, possibly shifts city-leg boundaries.

---

## Key File Map (re-planner specific)

| What | Where |
|---|---|
| Replanner agent | `backend/agents/replanner.py` |
| Replanner prompt | `backend/agents/prompts/replanner.md` |
| Disruption + delta models | `backend/models/disruption.py` |
| Locked slots / context | `backend/models/context.py` (`AppContext.locked_slots`, `candidate_pool`) |
| Orchestrator re-plan routing | `backend/agents/orchestrator.py` |
| Frontend re-plan hint | `static/index.html` |
| Eval set (to be added) | `backend/tests/evals/golden_disruptions.json` |

---

## Open Questions (decide before building Phase R3+)

1. **Should disruption classification live in `IntakeAgent` or a new `DisruptionIntakeAgent`?** — Leaning new agent: keeps prompts focused, allows class-concept "specialist" framing.
2. **Lock granularity: per-slot or per-day?** — Per-slot is more flexible; per-day is faster to build. Recommend per-slot for the advisor moat.
3. **Should we store full itinerary versions or just deltas?** — Deltas are smaller but harder to render; full versions enable easy undo/branch. Recommend full versions in `AppContext`, capped at last 5.
4. **Does the replanner need its own session, or share the planner's?** — Share. State continuity is the whole point.
