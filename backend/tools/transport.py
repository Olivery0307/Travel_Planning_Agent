"""Inter-city transport tool — static lookup table for common routes with live fallback."""

from __future__ import annotations

from pydantic import BaseModel, Field
from agents import function_tool


class TransportOption(BaseModel):
    mode: str = Field(description="Transport mode: 'train', 'bus', 'flight', or 'drive'")
    duration_hours: float = Field(description="Approximate travel time in hours")
    price_usd_low: int = Field(description="Approximate low-end ticket price in USD per person")
    price_usd_high: int = Field(description="Approximate high-end ticket price in USD per person")
    notes: str = Field(default="", description="Booking tips, frequency, or operator name")


class InterCityRouteResult(BaseModel):
    origin: str
    destination: str
    options: list[TransportOption]
    recommended: str = Field(description="Recommended mode for typical travelers")


# Static lookup for common inter-city routes.
# Keys are (origin_lower, destination_lower) — bidirectional: always check both orderings.
_ROUTES: dict[tuple[str, str], list[dict]] = {
    # Portugal
    ("lisbon", "porto"): [
        {"mode": "train", "duration_hours": 3.0, "price_usd_low": 25, "price_usd_high": 55, "notes": "CP Alfa Pendular, ~9 daily departures from Oriente station"},
        {"mode": "bus", "duration_hours": 3.5, "price_usd_low": 12, "price_usd_high": 20, "notes": "Rede Expressos, very frequent"},
    ],
    ("lisbon", "algarve"): [
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 20, "price_usd_high": 40, "notes": "CP Intercidades to Faro, change at Tunes"},
        {"mode": "bus", "duration_hours": 4.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "Eva/Rede Expressos to Faro or Lagos"},
    ],
    ("porto", "algarve"): [
        {"mode": "train", "duration_hours": 5.5, "price_usd_low": 30, "price_usd_high": 60, "notes": "Change in Lisbon or Tunes"},
        {"mode": "flight", "duration_hours": 1.0, "price_usd_low": 40, "price_usd_high": 100, "notes": "TAP/Ryanair Porto→Faro, book ahead"},
    ],
    # Italy
    ("rome", "florence"): [
        {"mode": "train", "duration_hours": 1.5, "price_usd_low": 20, "price_usd_high": 60, "notes": "Trenitalia Frecciarossa, frequent departures"},
        {"mode": "bus", "duration_hours": 3.5, "price_usd_low": 10, "price_usd_high": 20, "notes": "FlixBus, slower but cheap"},
    ],
    ("florence", "venice"): [
        {"mode": "train", "duration_hours": 2.0, "price_usd_low": 15, "price_usd_high": 50, "notes": "Trenitalia Frecciargento or Italo"},
    ],
    ("rome", "venice"): [
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 30, "price_usd_high": 80, "notes": "Frecciarossa direct, also change at Florence"},
    ],
    ("florence", "milan"): [
        {"mode": "train", "duration_hours": 1.75, "price_usd_low": 20, "price_usd_high": 55, "notes": "Frecciarossa or Italo, very frequent"},
    ],
    ("venice", "milan"): [
        {"mode": "train", "duration_hours": 2.5, "price_usd_low": 15, "price_usd_high": 50, "notes": "Frecciarossa or regional"},
    ],
    ("rome", "milan"): [
        {"mode": "train", "duration_hours": 3.0, "price_usd_low": 35, "price_usd_high": 90, "notes": "Frecciarossa, up to 1 per hour"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 40, "price_usd_high": 120, "notes": "Ryanair/easyJet from Fiumicino"},
    ],
    # Spain
    ("barcelona", "madrid"): [
        {"mode": "train", "duration_hours": 2.5, "price_usd_low": 30, "price_usd_high": 100, "notes": "Renfe AVE, very frequent"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 30, "price_usd_high": 90, "notes": "Vueling/Iberia, multiple daily"},
    ],
    ("madrid", "seville"): [
        {"mode": "train", "duration_hours": 2.5, "price_usd_low": 30, "price_usd_high": 90, "notes": "Renfe AVE from Atocha"},
    ],
    ("seville", "granada"): [
        {"mode": "bus", "duration_hours": 3.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "ALSA, most convenient option"},
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 20, "price_usd_high": 40, "notes": "Change at Antequera"},
    ],
    # France
    ("paris", "lyon"): [
        {"mode": "train", "duration_hours": 2.0, "price_usd_low": 30, "price_usd_high": 90, "notes": "TGV from Gare de Lyon, very frequent"},
    ],
    ("lyon", "nice"): [
        {"mode": "train", "duration_hours": 3.5, "price_usd_low": 25, "price_usd_high": 70, "notes": "TGV or Intercités"},
    ],
    ("paris", "nice"): [
        {"mode": "train", "duration_hours": 5.5, "price_usd_low": 40, "price_usd_high": 120, "notes": "TGV direct or change at Lyon"},
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 40, "price_usd_high": 150, "notes": "Air France/easyJet from CDG or Orly"},
    ],
    # Japan
    ("tokyo", "kyoto"): [
        {"mode": "train", "duration_hours": 2.25, "price_usd_low": 80, "price_usd_high": 100, "notes": "Shinkansen Nozomi, covered by JR Pass"},
    ],
    ("kyoto", "osaka"): [
        {"mode": "train", "duration_hours": 0.25, "price_usd_low": 4, "price_usd_high": 8, "notes": "JR Shinkansen (15 min) or Hankyu (30 min, cheaper)"},
    ],
    ("osaka", "hiroshima"): [
        {"mode": "train", "duration_hours": 1.25, "price_usd_low": 60, "price_usd_high": 75, "notes": "Shinkansen Nozomi, JR Pass valid"},
    ],
    ("tokyo", "osaka"): [
        {"mode": "train", "duration_hours": 2.5, "price_usd_low": 100, "price_usd_high": 130, "notes": "Shinkansen Nozomi direct, JR Pass valid"},
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 60, "price_usd_high": 150, "notes": "ANA/JAL Haneda→Itami, can be cheaper than Shinkansen"},
    ],
    # UK
    ("london", "edinburgh"): [
        {"mode": "train", "duration_hours": 4.5, "price_usd_low": 30, "price_usd_high": 120, "notes": "LNER Azuma, book early for best prices"},
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 30, "price_usd_high": 100, "notes": "British Airways/easyJet, multiple daily"},
    ],
    # Ireland
    ("dublin", "galway"): [
        {"mode": "bus", "duration_hours": 2.5, "price_usd_low": 12, "price_usd_high": 20, "notes": "Citylink or Bus Éireann, very frequent"},
        {"mode": "train", "duration_hours": 2.25, "price_usd_low": 20, "price_usd_high": 40, "notes": "Irish Rail from Heuston"},
    ],
    ("galway", "cork"): [
        {"mode": "bus", "duration_hours": 3.5, "price_usd_low": 15, "price_usd_high": 25, "notes": "Bus Éireann"},
    ],
    # Scandinavia
    ("copenhagen", "stockholm"): [
        {"mode": "train", "duration_hours": 5.0, "price_usd_low": 40, "price_usd_high": 120, "notes": "SJ/DSB, scenic coastal route via Malmö"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 40, "price_usd_high": 120, "notes": "SAS/Norwegian, frequent"},
    ],
    ("stockholm", "oslo"): [
        {"mode": "train", "duration_hours": 6.0, "price_usd_low": 40, "price_usd_high": 100, "notes": "SJ, scenic journey"},
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 40, "price_usd_high": 120, "notes": "SAS/Norwegian"},
    ],
    # Southeast Asia
    ("bangkok", "chiang mai"): [
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 20, "price_usd_high": 70, "notes": "AirAsia/Thai Lion, frequent from Suvarnabhumi"},
        {"mode": "train", "duration_hours": 12.0, "price_usd_low": 10, "price_usd_high": 30, "notes": "Overnight sleeper, scenic and comfortable"},
    ],
    ("chiang mai", "phuket"): [
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 30, "price_usd_high": 90, "notes": "AirAsia direct or via Bangkok"},
    ],
    # Vietnam
    ("hanoi", "hoi an"): [
        {"mode": "flight", "duration_hours": 1.5, "price_usd_low": 25, "price_usd_high": 80, "notes": "VietJet/Bamboo to Da Nang, then 30min taxi to Hoi An"},
        {"mode": "train", "duration_hours": 16.0, "price_usd_low": 20, "price_usd_high": 50, "notes": "Overnight SE train Hanoi→Da Nang, scenic"},
    ],
    ("hoi an", "ho chi minh city"): [
        {"mode": "flight", "duration_hours": 1.25, "price_usd_low": 25, "price_usd_high": 70, "notes": "VietJet/Bamboo from Da Nang airport"},
    ],
    # Greece
    ("athens", "santorini"): [
        {"mode": "flight", "duration_hours": 0.75, "price_usd_low": 50, "price_usd_high": 150, "notes": "Sky Express/Aegean, multiple daily from ATH"},
        {"mode": "ferry", "duration_hours": 7.5, "price_usd_low": 40, "price_usd_high": 80, "notes": "Hellenic Seaways or Blue Star from Piraeus"},
    ],
    ("santorini", "mykonos"): [
        {"mode": "ferry", "duration_hours": 2.5, "price_usd_low": 35, "price_usd_high": 60, "notes": "High-speed ferry, book ahead in summer"},
        {"mode": "flight", "duration_hours": 0.5, "price_usd_low": 60, "price_usd_high": 150, "notes": "Sky Express, limited schedule"},
    ],
    # Croatia
    ("dubrovnik", "split"): [
        {"mode": "bus", "duration_hours": 4.5, "price_usd_low": 15, "price_usd_high": 25, "notes": "Flixbus or Croatian coach, scenic coastal"},
        {"mode": "ferry", "duration_hours": 4.0, "price_usd_low": 20, "price_usd_high": 40, "notes": "Krilo catamaran, seasonal"},
    ],
    ("split", "zagreb"): [
        {"mode": "bus", "duration_hours": 5.0, "price_usd_low": 15, "price_usd_high": 25, "notes": "Flixbus or Croatian coach"},
        {"mode": "train", "duration_hours": 5.5, "price_usd_low": 15, "price_usd_high": 30, "notes": "Croatian Railways, scenic but slower"},
    ],
}


def _lookup(origin: str, destination: str) -> list[dict] | None:
    """Check static table in both directions."""
    o, d = origin.strip().lower(), destination.strip().lower()
    return _ROUTES.get((o, d)) or _ROUTES.get((d, o))


def _driving_estimate(origin: str, destination: str) -> list[dict]:
    """Generic driving fallback when no static data exists."""
    return [{"mode": "drive", "duration_hours": 3.0, "price_usd_low": 30, "price_usd_high": 80,
             "notes": f"Estimated drive {origin}→{destination}. Check Google Maps for exact time."}]


@function_tool
def get_intercity_transport(origin_city: str, destination_city: str) -> InterCityRouteResult:
    """Get inter-city transport options (train, bus, flight, ferry) between two cities.

    Returns a list of options with mode, duration, price range, and booking notes.
    Use this when building a multi-city itinerary to determine travel time and cost
    between consecutive city legs, and to insert a travel day slot in the itinerary.
    """
    raw = _lookup(origin_city, destination_city)
    if not raw:
        raw = _driving_estimate(origin_city, destination_city)

    options = [TransportOption(**r) for r in raw]

    # Pick recommendation: prefer train if under 4h, flight otherwise
    trains = [o for o in options if o.mode == "train" and o.duration_hours <= 4.0]
    flights = [o for o in options if o.mode == "flight"]
    if trains:
        recommended = "train"
    elif flights:
        recommended = "flight"
    else:
        recommended = options[0].mode if options else "drive"

    return InterCityRouteResult(
        origin=origin_city,
        destination=destination_city,
        options=options,
        recommended=recommended,
    )
