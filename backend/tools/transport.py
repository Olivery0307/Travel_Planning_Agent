"""Inter-city transport tool — static lookup table with Tavily web fallback."""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field
from agents import RunContextWrapper, function_tool

logger = logging.getLogger(__name__)


class TransportOption(BaseModel):
    mode: str = Field(description="Transport mode: 'train', 'bus', 'flight', 'ferry', or 'drive'")
    duration_hours: float = Field(description="Approximate travel time in hours")
    price_usd_low: int = Field(description="Approximate low-end ticket price in USD per person")
    price_usd_high: int = Field(description="Approximate high-end ticket price in USD per person")
    notes: str = Field(default="", description="Booking tips, frequency, or operator name")


class InterCityRouteResult(BaseModel):
    origin: str
    destination: str
    options: list[TransportOption]
    recommended: str = Field(description="Recommended mode for typical travelers")
    cost_per_person_usd: int = Field(
        description="Midpoint price of the recommended option per person in USD. Use this for daily budget calculations on the travel day."
    )
    source: str = Field(
        default="static",
        description="'static' = curated table, 'web' = Tavily estimate, 'estimate' = driving fallback"
    )


# ---------------------------------------------------------------------------
# Static lookup — (origin_lower, destination_lower), bidirectional
# ---------------------------------------------------------------------------
_ROUTES: dict[tuple[str, str], list[dict]] = {
    # Portugal
    ("lisbon", "porto"): [
        {"mode": "train", "duration_hours": 3.0, "price_usd_low": 25, "price_usd_high": 55, "notes": "CP Alfa Pendular, ~9 daily from Oriente"},
        {"mode": "bus",   "duration_hours": 3.5, "price_usd_low": 12, "price_usd_high": 20, "notes": "Rede Expressos, very frequent"},
    ],
    ("lisbon", "algarve"): [
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 20, "price_usd_high": 40, "notes": "CP Intercidades to Faro, change at Tunes"},
        {"mode": "bus",   "duration_hours": 4.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "Eva/Rede Expressos to Faro or Lagos"},
    ],
    ("porto", "algarve"): [
        {"mode": "train",  "duration_hours": 5.5, "price_usd_low": 30, "price_usd_high": 60,  "notes": "Change in Lisbon or Tunes"},
        {"mode": "flight", "duration_hours": 1.0, "price_usd_low": 40, "price_usd_high": 100, "notes": "TAP/Ryanair Porto→Faro, book ahead"},
    ],
    # Italy
    ("rome", "florence"): [
        {"mode": "train", "duration_hours": 1.5, "price_usd_low": 20, "price_usd_high": 60, "notes": "Trenitalia Frecciarossa, frequent"},
        {"mode": "bus",   "duration_hours": 3.5, "price_usd_low": 10, "price_usd_high": 20, "notes": "FlixBus, slower but cheap"},
    ],
    ("florence", "venice"): [
        {"mode": "train", "duration_hours": 2.0, "price_usd_low": 15, "price_usd_high": 50, "notes": "Trenitalia Frecciargento or Italo"},
    ],
    ("rome", "venice"): [
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 30, "price_usd_high": 80, "notes": "Frecciarossa direct or change at Florence"},
    ],
    ("florence", "milan"): [
        {"mode": "train", "duration_hours": 1.75, "price_usd_low": 20, "price_usd_high": 55, "notes": "Frecciarossa or Italo, very frequent"},
    ],
    ("venice", "milan"): [
        {"mode": "train", "duration_hours": 2.5, "price_usd_low": 15, "price_usd_high": 50, "notes": "Frecciarossa or regional"},
    ],
    ("rome", "milan"): [
        {"mode": "train",  "duration_hours": 3.0,  "price_usd_low": 35, "price_usd_high": 90,  "notes": "Frecciarossa, up to 1/hour"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 40, "price_usd_high": 120, "notes": "Ryanair/easyJet from Fiumicino"},
    ],
    ("florence", "cinque terre"): [
        {"mode": "train", "duration_hours": 1.5, "price_usd_low": 10, "price_usd_high": 20, "notes": "Regional to La Spezia, then Cinque Terre Express"},
    ],
    ("rome", "naples"): [
        {"mode": "train", "duration_hours": 1.25, "price_usd_low": 15, "price_usd_high": 45, "notes": "Frecciarossa from Roma Termini"},
        {"mode": "bus",   "duration_hours": 2.5,  "price_usd_low": 8,  "price_usd_high": 15, "notes": "FlixBus or Marino, multiple daily"},
    ],
    ("naples", "amalfi coast"): [
        {"mode": "ferry", "duration_hours": 1.5, "price_usd_low": 15, "price_usd_high": 25, "notes": "Alilauro hydrofoil from Molo Beverello"},
        {"mode": "bus",   "duration_hours": 2.0, "price_usd_low": 5,  "price_usd_high": 10, "notes": "SITA bus, scenic coastal road"},
    ],
    # Spain
    ("barcelona", "madrid"): [
        {"mode": "train",  "duration_hours": 2.5,  "price_usd_low": 30, "price_usd_high": 100, "notes": "Renfe AVE, very frequent"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 30, "price_usd_high": 90,  "notes": "Vueling/Iberia, multiple daily"},
    ],
    ("madrid", "seville"): [
        {"mode": "train", "duration_hours": 2.5, "price_usd_low": 30, "price_usd_high": 90, "notes": "Renfe AVE from Atocha"},
    ],
    ("seville", "granada"): [
        {"mode": "bus",   "duration_hours": 3.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "ALSA, most convenient"},
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 20, "price_usd_high": 40, "notes": "Change at Antequera"},
    ],
    ("barcelona", "valencia"): [
        {"mode": "train", "duration_hours": 3.0, "price_usd_low": 20, "price_usd_high": 60, "notes": "Renfe Euromed or AVE"},
    ],
    ("madrid", "granada"): [
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 25, "price_usd_high": 70, "notes": "Renfe Avant via Antequera"},
        {"mode": "bus",   "duration_hours": 5.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "ALSA direct"},
    ],
    # France
    ("paris", "lyon"): [
        {"mode": "train", "duration_hours": 2.0, "price_usd_low": 30, "price_usd_high": 90, "notes": "TGV from Gare de Lyon, very frequent"},
    ],
    ("lyon", "nice"): [
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 25, "price_usd_high": 70, "notes": "TGV or Intercités"},
    ],
    ("paris", "nice"): [
        {"mode": "train",  "duration_hours": 5.5, "price_usd_low": 40,  "price_usd_high": 120, "notes": "TGV direct or change at Lyon"},
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 40,  "price_usd_high": 150, "notes": "Air France/easyJet from CDG or Orly"},
    ],
    ("paris", "bordeaux"): [
        {"mode": "train", "duration_hours": 2.25, "price_usd_low": 25, "price_usd_high": 80, "notes": "TGV from Gare Montparnasse"},
    ],
    ("paris", "amsterdam"): [
        {"mode": "train",  "duration_hours": 3.5,  "price_usd_low": 40, "price_usd_high": 120, "notes": "Thalys/Eurostar direct"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 30, "price_usd_high": 100, "notes": "Air France/KLM/easyJet"},
    ],
    # Germany
    ("berlin", "munich"): [
        {"mode": "train",  "duration_hours": 4.0,  "price_usd_low": 30, "price_usd_high": 100, "notes": "ICE direct from Hbf"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 30, "price_usd_high": 90,  "notes": "Lufthansa/Ryanair, multiple daily"},
    ],
    ("munich", "hamburg"): [
        {"mode": "train",  "duration_hours": 5.5, "price_usd_low": 40, "price_usd_high": 120, "notes": "ICE direct, book early"},
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 35, "price_usd_high": 100, "notes": "Lufthansa/easyJet"},
    ],
    ("berlin", "hamburg"): [
        {"mode": "train", "duration_hours": 1.75, "price_usd_low": 20, "price_usd_high": 80, "notes": "ICE direct, very frequent"},
    ],
    # Netherlands
    ("amsterdam", "rotterdam"): [
        {"mode": "train", "duration_hours": 0.75, "price_usd_low": 12, "price_usd_high": 18, "notes": "NS Intercity direct, multiple per hour"},
    ],
    ("rotterdam", "the hague"): [
        {"mode": "train", "duration_hours": 0.33, "price_usd_low": 5, "price_usd_high": 8, "notes": "NS Intercity, ~6/hour"},
    ],
    # UK
    ("london", "edinburgh"): [
        {"mode": "train",  "duration_hours": 4.5, "price_usd_low": 30, "price_usd_high": 120, "notes": "LNER Azuma, book early"},
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 30, "price_usd_high": 100, "notes": "BA/easyJet, multiple daily"},
    ],
    ("london", "manchester"): [
        {"mode": "train", "duration_hours": 2.25, "price_usd_low": 20, "price_usd_high": 80, "notes": "Avanti West Coast from Euston"},
    ],
    ("london", "bath"): [
        {"mode": "train", "duration_hours": 1.5, "price_usd_low": 15, "price_usd_high": 50, "notes": "GWR direct from Paddington"},
    ],
    # Ireland
    ("dublin", "galway"): [
        {"mode": "bus",   "duration_hours": 2.5,  "price_usd_low": 12, "price_usd_high": 20, "notes": "Citylink or Bus Éireann, frequent"},
        {"mode": "train", "duration_hours": 2.25, "price_usd_low": 20, "price_usd_high": 40, "notes": "Irish Rail from Heuston"},
    ],
    ("galway", "cork"): [
        {"mode": "bus", "duration_hours": 3.5, "price_usd_low": 15, "price_usd_high": 25, "notes": "Bus Éireann"},
    ],
    # Scandinavia
    ("copenhagen", "stockholm"): [
        {"mode": "train",  "duration_hours": 5.0,  "price_usd_low": 40, "price_usd_high": 120, "notes": "SJ/DSB, scenic coastal route via Malmö"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 40, "price_usd_high": 120, "notes": "SAS/Norwegian, frequent"},
    ],
    ("stockholm", "oslo"): [
        {"mode": "train",  "duration_hours": 6.0,  "price_usd_low": 40, "price_usd_high": 100, "notes": "SJ, scenic"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 40, "price_usd_high": 120, "notes": "SAS/Norwegian"},
    ],
    ("oslo", "bergen"): [
        {"mode": "train", "duration_hours": 6.5, "price_usd_low": 35, "price_usd_high": 80, "notes": "Bergen Line, one of Europe's most scenic rail journeys"},
    ],
    # Japan
    ("tokyo", "kyoto"): [
        {"mode": "train", "duration_hours": 2.25, "price_usd_low": 80, "price_usd_high": 100, "notes": "Shinkansen Nozomi, JR Pass valid"},
    ],
    ("kyoto", "osaka"): [
        {"mode": "train", "duration_hours": 0.25, "price_usd_low": 4, "price_usd_high": 8, "notes": "JR Shinkansen (15 min) or Hankyu (30 min, cheaper)"},
    ],
    ("osaka", "hiroshima"): [
        {"mode": "train", "duration_hours": 1.25, "price_usd_low": 60, "price_usd_high": 75, "notes": "Shinkansen Nozomi, JR Pass valid"},
    ],
    ("tokyo", "osaka"): [
        {"mode": "train",  "duration_hours": 2.5, "price_usd_low": 100, "price_usd_high": 130, "notes": "Shinkansen Nozomi, JR Pass valid"},
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 60,  "price_usd_high": 150, "notes": "ANA/JAL Haneda→Itami"},
    ],
    ("tokyo", "hiroshima"): [
        {"mode": "train", "duration_hours": 4.0, "price_usd_low": 130, "price_usd_high": 160, "notes": "Shinkansen Nozomi, JR Pass valid"},
    ],
    # Southeast Asia
    ("bangkok", "chiang mai"): [
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 20, "price_usd_high": 70,  "notes": "AirAsia/Thai Lion, frequent"},
        {"mode": "train",  "duration_hours": 12.0, "price_usd_low": 10, "price_usd_high": 30, "notes": "Overnight sleeper, scenic"},
    ],
    ("chiang mai", "phuket"): [
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 30, "price_usd_high": 90, "notes": "AirAsia direct or via Bangkok"},
    ],
    ("bangkok", "phuket"): [
        {"mode": "flight", "duration_hours": 1.5,  "price_usd_low": 25, "price_usd_high": 80, "notes": "AirAsia/Thai Lion, frequent"},
        {"mode": "bus",    "duration_hours": 12.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "Overnight VIP bus, budget option"},
    ],
    # Vietnam
    ("hanoi", "hoi an"): [
        {"mode": "flight", "duration_hours": 1.5,  "price_usd_low": 25, "price_usd_high": 80, "notes": "VietJet/Bamboo to Da Nang, then 30min taxi"},
        {"mode": "train",  "duration_hours": 16.0, "price_usd_low": 20, "price_usd_high": 50, "notes": "Overnight SE train to Da Nang, scenic"},
    ],
    ("hoi an", "ho chi minh city"): [
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 25, "price_usd_high": 70, "notes": "VietJet/Bamboo from Da Nang airport"},
    ],
    ("hanoi", "ho chi minh city"): [
        {"mode": "flight", "duration_hours": 2.0, "price_usd_low": 30, "price_usd_high": 100, "notes": "Vietnam Airlines/VietJet, multiple daily"},
    ],
    # Greece
    ("athens", "santorini"): [
        {"mode": "flight", "duration_hours": 0.75, "price_usd_low": 50,  "price_usd_high": 150, "notes": "Sky Express/Aegean, multiple daily"},
        {"mode": "ferry",  "duration_hours": 7.5,  "price_usd_low": 40,  "price_usd_high": 80,  "notes": "Hellenic Seaways or Blue Star from Piraeus"},
    ],
    ("santorini", "mykonos"): [
        {"mode": "ferry",  "duration_hours": 2.5, "price_usd_low": 35, "price_usd_high": 60,  "notes": "High-speed ferry, book ahead in summer"},
        {"mode": "flight", "duration_hours": 0.5, "price_usd_low": 60, "price_usd_high": 150, "notes": "Sky Express, limited schedule"},
    ],
    ("athens", "mykonos"): [
        {"mode": "ferry",  "duration_hours": 4.5, "price_usd_low": 35, "price_usd_high": 70,  "notes": "Hellenic Seaways from Piraeus"},
        {"mode": "flight", "duration_hours": 0.5, "price_usd_low": 50, "price_usd_high": 130, "notes": "Aegean/Sky Express"},
    ],
    # Croatia
    ("dubrovnik", "split"): [
        {"mode": "bus",   "duration_hours": 4.5, "price_usd_low": 15, "price_usd_high": 25, "notes": "FlixBus or Croatian coach, scenic coastal"},
        {"mode": "ferry", "duration_hours": 4.0, "price_usd_low": 20, "price_usd_high": 40, "notes": "Krilo catamaran, seasonal"},
    ],
    ("split", "zagreb"): [
        {"mode": "bus",   "duration_hours": 5.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "FlixBus or Croatian coach"},
        {"mode": "train", "duration_hours": 5.5, "price_usd_low": 15, "price_usd_high": 30, "notes": "Croatian Railways, scenic"},
    ],
    # Morocco
    ("marrakech", "fes"): [
        {"mode": "train", "duration_hours": 7.5, "price_usd_low": 20, "price_usd_high": 40, "notes": "ONCF via Casablanca, book ahead"},
        {"mode": "bus",   "duration_hours": 8.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "CTM or Supratours, direct"},
    ],
    ("fes", "casablanca"): [
        {"mode": "train", "duration_hours": 3.75, "price_usd_low": 15, "price_usd_high": 30, "notes": "ONCF direct, several daily"},
    ],
    # Turkey
    ("istanbul", "cappadocia"): [
        {"mode": "flight", "duration_hours": 1.5,  "price_usd_low": 40, "price_usd_high": 100, "notes": "Turkish Airlines/Pegasus to Kayseri or Nevşehir"},
        {"mode": "bus",    "duration_hours": 10.0, "price_usd_low": 20, "price_usd_high": 35,  "notes": "Overnight bus, comfortable sleeper"},
    ],
    # Americas
    ("new york", "washington dc"): [
        {"mode": "train", "duration_hours": 3.0, "price_usd_low": 30,  "price_usd_high": 150, "notes": "Amtrak Acela or Northeast Regional from Penn Station"},
        {"mode": "bus",   "duration_hours": 4.0, "price_usd_low": 15,  "price_usd_high": 40,  "notes": "FlixBus/Megabus, cheap option"},
    ],
    ("washington dc", "boston"): [
        {"mode": "train", "duration_hours": 6.5, "price_usd_low": 40, "price_usd_high": 180, "notes": "Amtrak Acela or Northeast Regional"},
        {"mode": "bus",   "duration_hours": 8.0, "price_usd_low": 20, "price_usd_high": 50,  "notes": "FlixBus/Greyhound"},
    ],
    ("los angeles", "san francisco"): [
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 40, "price_usd_high": 150, "notes": "Southwest/United/Delta, very frequent"},
        {"mode": "bus",    "duration_hours": 8.0, "price_usd_low": 20, "price_usd_high": 50,  "notes": "FlixBus or Greyhound along US-101"},
    ],
    ("san francisco", "las vegas"): [
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 50, "price_usd_high": 150, "notes": "Southwest/Spirit/Frontier, frequent"},
        {"mode": "drive",  "duration_hours": 9.0, "price_usd_low": 40, "price_usd_high": 80,  "notes": "Scenic US-395 or I-15"},
    ],
}


def _lookup(origin: str, destination: str) -> list[dict] | None:
    o, d = origin.strip().lower(), destination.strip().lower()
    return _ROUTES.get((o, d)) or _ROUTES.get((d, o))


def _driving_fallback(origin: str, destination: str) -> list[dict]:
    return [{"mode": "drive", "duration_hours": 3.0, "price_usd_low": 30, "price_usd_high": 80,
             "notes": f"Estimated drive {origin}→{destination}. Verify on Google Maps."}]


def _tavily_fallback(origin: str, destination: str) -> list[dict] | None:
    """Web search fallback for routes not in the static table.
    Results are cached in tavily_transport_cache.json so each route is only searched once.
    Returns None on rate-limit or missing key (caller uses driving estimate instead).
    """
    from backend.tools.tavily_search import _transport_cache, _save_json_cache, _TRANSPORT_CACHE_PATH

    ckey = f"{origin.lower()}___{destination.lower()}"
    rev_key = f"{destination.lower()}___{origin.lower()}"

    # Cache hit (either direction)
    for k in (ckey, rev_key):
        if k in _transport_cache:
            logger.info("Tavily transport cache HIT %s→%s", origin, destination)
            return _transport_cache[k] or None  # None means "no result, use driving"

    try:
        from backend.tools.tavily_search import _tavily_client, _tavily_search
        client = _tavily_client()
        if not client:
            return None

        query = f"train bus transport {origin} to {destination} ticket price USD 2024 2025"
        results = _tavily_search(client, query, max_results=3)
        if results is None:          # rate-limited — don't cache
            return None
        if not results:
            _transport_cache[ckey] = []
            _save_json_cache(_TRANSPORT_CACHE_PATH, _transport_cache)
            return None

        content = " ".join(r.get("content", "") for r in results)

        # Extract prices $5–$2000
        raw_prices = re.findall(r"\$\s*(\d{1,4})|\b(\d{1,4})\s*USD\b", content)
        prices = sorted({int(a or b) for a, b in raw_prices if 5 <= int(a or b) <= 2000})

        # Detect mode
        mode, lower = "train", content.lower()
        if "flight" in lower or "airport" in lower or "airline" in lower:
            mode = "flight"
        elif "ferry" in lower or "boat" in lower:
            mode = "ferry"
        elif "bus" in lower and "train" not in lower:
            mode = "bus"

        h_match = re.search(r"(\d+(?:\.\d+)?)\s*hour", content, re.IGNORECASE)
        duration = float(h_match.group(1)) if h_match else 3.0

        low  = prices[0]  if prices else 20
        high = prices[-1] if len(prices) > 1 else low * 2

        result = [{"mode": mode, "duration_hours": duration,
                   "price_usd_low": low, "price_usd_high": high,
                   "notes": "Web estimate — verify current prices before booking"}]

        _transport_cache[ckey] = result
        _save_json_cache(_TRANSPORT_CACHE_PATH, _transport_cache)
        logger.info("Tavily transport %s→%s: mode=%s $%d–$%d (cached)", origin, destination, mode, low, high)
        return result

    except Exception as exc:
        logger.warning("Tavily transport fallback failed %s→%s: %s", origin, destination, exc)
        return None


def _pick_recommended(options: list[TransportOption]) -> tuple[str, int]:
    """Return (recommended_mode, cost_per_person midpoint)."""
    trains  = [o for o in options if o.mode == "train"  and o.duration_hours <= 4.0]
    flights = [o for o in options if o.mode == "flight"]
    ferries = [o for o in options if o.mode == "ferry"]
    best = (trains or flights or ferries or options)[0]
    return best.mode, (best.price_usd_low + best.price_usd_high) // 2


@function_tool
def get_intercity_transport(
    ctx: RunContextWrapper,
    origin_city: str,
    destination_city: str,
) -> InterCityRouteResult:
    """Get inter-city transport options (train, bus, flight, ferry) between two cities.

    Returns options with mode, duration, price range, and cost_per_person_usd
    (midpoint of the recommended option) for daily budget calculations.
    Call once per consecutive city pair in a multi-city trip.
    """
    raw = _lookup(origin_city, destination_city)
    source = "static"

    if not raw:
        raw = _tavily_fallback(origin_city, destination_city)
        source = "web" if raw else "estimate"

    if not raw:
        raw = _driving_fallback(origin_city, destination_city)

    options = [TransportOption(**r) for r in raw]
    recommended, cost_per_person = _pick_recommended(options)

    logger.info("transport %s→%s source=%s recommended=%s cost=$%d/person",
                origin_city, destination_city, source, recommended, cost_per_person)

    return InterCityRouteResult(
        origin=origin_city,
        destination=destination_city,
        options=options,
        recommended=recommended,
        cost_per_person_usd=cost_per_person,
        source=source,
    )
