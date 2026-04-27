# Voyager — Itinerary Generation Pipeline

> Render this file in any Mermaid-compatible viewer (GitHub, VS Code Mermaid Preview, mermaid.live) to see the diagram.
> To export as PNG: paste the code block at https://mermaid.live → click Download PNG.

```mermaid
flowchart TD
    U([👤 User / Advisor]) -->|Natural language request\nor Trip Builder form| GUARD

    subgraph GUARD["Input Guardrails"]
        G1[OffTopicGuardrail\nis this a travel request?]
        G2[BudgetSanityGuardrail\n$20–$10k/day range?]
        G1 --> G2
    end

    GUARD -->|Blocked| ERR([❌ Friendly rejection])
    GUARD -->|Passes| ORCH

    subgraph ORCH["OrchestratorAgent  ·  gemini-2.5-flash"]
        direction TB
        O1[Receive user message\n+ session history]
        O1 --> O2{Single-city\nor multi-city?}
    end

    O2 -->|destinations has 1 city| SC
    O2 -->|destinations has 2+ cities| MC

    subgraph SC["Single-City Flow"]
        direction TB
        SC1[intake_agent\nparse → TripRequest]
        SC2[lodging_agent\nsearch hotels]
        SC3[activity_agent\nsearch attractions]
        SC4[dining_agent\nsearch restaurants]
        SC1 --> SC2 & SC3 & SC4
    end

    subgraph MC["Multi-City Flow  (per city leg)"]
        direction TB
        MC1[intake_agent\nparse → TripRequest\nwith destinations list]
        MC2["For each CityLeg\n① lodging_agent\n② activity_agent\n③ dining_agent"]
        MC3["For each city transition\ntransport_agent\ntrain / flight / bus / ferry"]
        MC1 --> MC2 --> MC3
    end

    subgraph TOOLS["Google APIs  ·  PlacesCache"]
        T1[(Local JSON Cache\nplaces_rome.json\nplaces_florence.json …)]
        T2[Google Places API\nText Search · Place Details\nOpening Hours]
        T3[Google Maps Routes API\nRoute Matrix · Directions]
        T4[Static Transport Table\n25+ inter-city routes]
        T1 -. cache miss .-> T2
    end

    SC2 & SC3 & SC4 <-->|search_places\nget_place_details\nget_opening_hours| TOOLS
    MC2 <-->|search_places\nget_place_details| TOOLS
    MC3 <-->|get_intercity_transport| T4

    SC2 & SC3 & SC4 --> SOLVER
    MC2 & MC3 --> SOLVER

    subgraph SOLVER["SolverAgent  ·  gemini-2.0-flash"]
        direction TB
        S1[Receive all city candidates\n+ transport summaries]
        S2[Assign days to city legs\nby nights count]
        S3[Sequence stops per day\nmorning · afternoon · evening]
        S4[compute_route_matrix\nminimize transit time]
        S5[Enforce constraints\nbudget · hours · no-repeat restaurants]
        S6[Emit Markdown itinerary\n+ Maps URLs · QR markers · booking links]
        S1 --> S2 --> S3 --> S4 --> S5 --> S6
    end

    S4 <-->|lat/lng pairs| T3

    SOLVER -->|Markdown itinerary text| POST

    subgraph POST["Post-processing  ·  main.py"]
        P1[Capture itinerary_json\ninto AppContext]
        P2[GET /qr?url=...\nGenerate QR PNG via Pillow]
    end

    POST --> FE

    subgraph FE["Frontend  ·  static/index.html"]
        direction TB
        F1[looksLikeItinerary?\nparse Markdown]
        F2[parseItinerary\nday headers · city banners · slots]
        F3[parseLine\nperiod · name · cost · link · transport]
        F4[renderItinerary\nday cards · city banners\nNavigate button · QR toggle\nbooking links]
        F1 --> F2 --> F3 --> F4
    end

    FE -->|Rendered itinerary| U2([👤 User sees day cards])

    U2 -->|Reports disruption\nDay 2 closed…| REPLAN

    subgraph REPLAN["Re-planning Flow"]
        R1[OrchestratorAgent\ndetects disruption]
        R2[replanner_agent\nread itinerary_json from AppContext\nrespect locked slots]
        R3[ItineraryDelta\nchanged slots + reasoning]
        R1 --> R2 --> R3
    end

    R2 <-->|search_places\ncompute_route_matrix| TOOLS
    REPLAN --> FE
```

## Component summary

| Component | Model / Tech | Role |
|---|---|---|
| OrchestratorAgent | gemini-2.5-flash | Owns conversation; routes single vs multi-city; triggers re-plan |
| IntakeAgent | gemini-2.0-flash | Parses free text → `TripRequest` with `CityLeg` list |
| LodgingAgent | gemini-2.0-flash | Finds hotels per city via Google Places |
| ActivityAgent | gemini-2.0-flash | Finds attractions per city; flags booking-required |
| DiningAgent | gemini-2.0-flash | Finds restaurants (3× trip days to prevent repeats) |
| TransportAgent | gemini-2.0-flash | Inter-city transport via static lookup + fallback |
| SolverAgent | gemini-2.0-flash | Sequences all candidates into day-by-day Markdown itinerary |
| ReplannerAgent | gemini-2.0-flash | Re-optimizes disrupted days; respects locked slots |
| PlacesCache | Python / GCS | Local JSON cache → Google Places API on miss |
| Input Guardrails | gemini-2.0-flash | Off-topic filter + budget sanity check |
| Frontend parser | Vanilla JS | Parses Markdown into structured day cards |
