# Voyager — Capstone Wrap-Up

## What Was Built

### Agent Architecture (9 agents)

| Agent | Role | Key Detail |
|---|---|---|
| **OrchestratorAgent** | Owns every conversation turn, routes to sub-agents | Dynamic system prompt injects itinerary + weather + locked slots per turn. Gemini 2.5 Flash. |
| **IntakeAgent** | Parses free-text → `TripRequest` | Today's date injected so relative dates ("May 15") resolve to the correct year. Structured output via Pydantic. |
| **LodgingAgent** | Hotels/B&Bs per city leg | Parallel with Activity + Dining. `search_places` only — no extra detail calls. |
| **ActivityAgent** | Attractions per city leg | Parallel. Respects `mobility_notes`, `must_include`. |
| **DiningAgent** | Restaurants per city leg | Returns 3× trip days worth of options so no repeats across the itinerary. |
| **TransportAgent** | Inter-city options | Static lookup table for 25+ routes (Europe, Japan, SEA, Americas) + Tavily fallback. |
| **SolverAgent** | Sequences all candidates → Markdown itinerary | Weather-aware scheduling, multi-city travel days, route URLs, QR markers, booking links. Gemini 2.0 Flash. |
| **ReplannerAgent** | Re-optimises affected days after a disruption | JSON delta output, locked slots never touched, candidate pool reuse. |
| **ConversationAgent** | Q&A, itinerary analysis, targeted suggestions | Read-only. Dynamic prompt auto-injects itinerary + weather + trip context from `AppContext`. Routes confirmed suggestions to replanner. |

### External APIs Wired

| API | Used For |
|---|---|
| Vertex AI Gemini 2.5 Flash | Orchestrator LLM |
| Vertex AI Gemini 2.0 Flash | All specialist agents |
| Google Places API (New) | `search_places`, `get_place_details`, `get_opening_hours` |
| Google Maps Routes API | `compute_route_matrix` (travel times between stops) |
| Google Maps Geocoding | Coordinate resolution for route URLs |
| Open-Meteo (free) | Weather forecast (≤16 days) + historical averages (beyond 16 days) |
| Tavily Search | Booking link enrichment post-plan |
| LiteAPI | Hotel nightly rate enrichment post-plan |
| Google Cloud Storage | Optional shared places cache across deployments |

### Frontend Features (single `static/index.html`)

- **Chat pane** (left) + **Itinerary pane** (right)
- **Trip Builder** form: destination, days, budget, start date, interests pills, dietary, must-see, mobility
- **SSE streaming** progress indicators (e.g. "Finding attractions in Rome…")
- **Day cards**: collapsible, weather badges, budget bar, slot count, cost tooltip
- **Slot actions**: Replace / Broken / Note (hover menu), Lock 🔒/🔓 toggle
- **Batch edit queue**: multiple slot actions composed into one message before sending
- **Lodging carry-forward**: hotel slot shown on all nights at 50% opacity
- **ConversationAgent routing**: post-itinerary questions and analysis without re-planning
- **Auto-apply delta**: replanner result applied immediately to itinerary view
- **Export PDF**: clean popup window, strips all UI chrome
- **Export .ics Calendar**: one VEVENT per slot, compatible with Apple / Google / Outlook
- **Multi-city**: city banners between day cards, "Multi-city · Lisbon → Porto" eyebrow

---

## Future Steps (Post-Capstone)

### High Impact / Low Effort
- **Dedicated BookingAgent** — real Booking.com/Airbnb/Viator/GetYourGuide deep links. Needs affiliate API keys. Currently Tavily covers ~85% of cases.
- **Auto-lock heuristics** — slots with a resolved `booking_url` auto-suggest a lock badge ("pre-booked — lock?").
- **Copy as Markdown** — clipboard button on the itinerary pane.
- **TripAdvisor / Google review links** — one URL per slot for advisor reference.

### Medium Effort
- **Opening-hours pre-check** — after planning, scan every slot against trip dates. Flag "Borghese closed Mondays, Day 3 is Monday."
- **Weather-aware pre-check** — flag outdoor slots on forecasted rain days, suggest indoor swaps proactively.
- **Top-N alternatives in replanner** — return 2–3 candidate replacements per disrupted slot, advisor picks.
- **Multi-turn replanner** — stay "active" after delta; next user message refines ("but cheaper", "but indoors") rather than being a new disruption.
- **Rome2Rio API** — real inter-city routes + prices instead of static table.
- **Golden disruption eval set** — `golden_disruptions.json` (15 scenarios) + `run_replan_eval.py` CI gate.

### Longer-Term / Stretch
- **Real-time flight monitoring** — FlightAware/AviationStack → auto-trigger re-plan before advisor asks.
- **Persistent user accounts** — saved itineraries across sessions (currently in-memory only).
- **Group dynamics** — separate preference profiles per traveler, merged into one plan.
- **Accessibility scoring** — wheelchair/cane score per venue beyond the free-text `mobility_notes` field.
- **Re-plan undo stack** — full version tree, not just "last." Branch and compare scenarios.
- **Push notifications** — email/SMS advisor when proactive disruption detected (strike, weather alert).

---

## GCP Deployment Instructions

### Prerequisites

```bash
# Ensure these are set in .env (or GCP Secret Manager for production)
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_MAPS_API_KEY=...
TAVILY_API_KEY=...
LITEAPI_KEY=...
PLACES_CACHE_BUCKET=voyager-places-cache   # optional GCS bucket name
```

---

### 1. Upload the Places Cache to GCS

The local `backend/data/` directory contains seeded JSON cache files for Rome, Florence, Bangkok, Lisbon, Porto, Algarve. Upload them so Cloud Run doesn't need to hit Google Places API cold on every city.

```bash
# Create bucket (one-time)
gcloud storage buckets create gs://voyager-places-cache \
  --project=your-project-id \
  --location=us-central1 \
  --uniform-bucket-level-access

# Upload all cache files
gcloud storage cp backend/data/places_*.json gs://voyager-places-cache/
gcloud storage cp backend/data/hotel_rates_*.json gs://voyager-places-cache/
gcloud storage cp backend/data/tavily_url_cache.json gs://voyager-places-cache/

# Verify
gcloud storage ls gs://voyager-places-cache/
```

Set `PLACES_CACHE_BUCKET=voyager-places-cache` in your Cloud Run environment — the app will sync from GCS on startup and write new cache hits back automatically.

---

### 2. Build and Push the Container

```bash
# From project root
gcloud builds submit \
  --config cloudbuild.yaml \
  --project your-project-id
```

Or manually:

```bash
docker build -t gcr.io/your-project-id/voyager:latest .
docker push gcr.io/your-project-id/voyager:latest
```

---

### 3. Deploy to Cloud Run

```bash
gcloud run deploy voyager \
  --image gcr.io/your-project-id/voyager:latest \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 600 \
  --concurrency 10 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=your-project-id,GOOGLE_CLOUD_LOCATION=us-central1,PLACES_CACHE_BUCKET=voyager-places-cache" \
  --set-secrets "GOOGLE_MAPS_API_KEY=maps-api-key:latest,TAVILY_API_KEY=tavily-key:latest,LITEAPI_KEY=liteapi-key:latest"
```

Key flags:
- `--timeout 600` — itinerary generation can take up to 3–4 minutes for multi-city trips
- `--memory 2Gi` — the places cache JSON files are loaded into memory on startup (~50 MB)
- `--concurrency 10` — each request holds a long-lived SSE connection; keep this low

---

### 4. Store API Keys in Secret Manager (recommended)

```bash
# Create secrets (one-time)
echo -n "your-maps-key"   | gcloud secrets create maps-api-key   --data-file=-
echo -n "your-tavily-key" | gcloud secrets create tavily-key     --data-file=-
echo -n "your-liteapi-key"| gcloud secrets create liteapi-key    --data-file=-

# Grant Cloud Run SA access
gcloud secrets add-iam-policy-binding maps-api-key \
  --member="serviceAccount:your-sa@your-project-id.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
# (repeat for each secret)
```

---

### 5. Seed Additional City Caches

If you want to add more cities without hitting the Places API live, run the seeder locally first:

```bash
uv run python scripts/seed_places_cache.py --city "Barcelona" --country "Spain"
# Then upload the new file
gcloud storage cp backend/data/places_barcelona.json gs://voyager-places-cache/
```

---

### 6. Health Check

```bash
# After deploy, verify the service is up
curl https://your-service-url.run.app/health
# → {"status": "ok", "model": "vertex_ai/gemini-2.5-flash"}
```

---

### 7. Continuous Deployment (optional)

The `cloudbuild.yaml` is already wired for Cloud Build triggers. Connect your repo in the GCP Console under **Cloud Build → Triggers** → push to `main` → auto-deploy.
