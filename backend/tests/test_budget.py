"""
Tests for budget parsing and cost breakdown logic.
Mirrors the JS _parseCostBreakdown / _budgetBarHtml logic in Python,
and tests the Python-side _extract_itinerary_text budget handling.
"""

from __future__ import annotations

import re
import pytest


# ── Cost breakdown parser (Python mirror of JS _parseCostBreakdown) ───────────

def parse_cost_breakdown(cost_text: str) -> dict | None:
    """
    Python mirror of the JS _parseCostBreakdown function in static/index.html.
    Parses strings like:
      "~$183/person (lodging $120 + activities $25 + dining $38)"
      "~$60/person (transport $35 + dinner $25)"
    Returns dict of {category: amount, total: sum} or None.
    """
    if not cost_text:
        return None
    inner = re.search(r'\(([^)]+)\)', cost_text)
    if not inner:
        return None

    SYNONYMS = {
        "dinner": "dining", "lunch": "dining", "food": "dining",
        "activities": "activities", "activity": "activities",
    }

    result = {}
    for part in inner.group(1).split('+'):
        part = part.strip()
        m = re.match(r'^(\w+)\s*\$?([\d,]+)', part, re.IGNORECASE)
        if m:
            key = m.group(1).lower()
            key = SYNONYMS.get(key, key)
            result[key] = int(m.group(2).replace(',', ''))

    total = sum(result.values())
    return {**result, "total": total} if total > 0 else None


@pytest.mark.unit
class TestCostBreakdown:
    def test_full_breakdown(self):
        bd = parse_cost_breakdown("~$183/person (lodging $120 + activities $25 + dining $38)")
        assert bd is not None
        assert bd["lodging"] == 120
        assert bd["activities"] == 25
        assert bd["dining"] == 38
        assert bd["total"] == 183

    def test_travel_day_breakdown(self):
        bd = parse_cost_breakdown("~$60/person (transport $35 + dinner $25)")
        assert bd is not None
        assert bd["transport"] == 35
        assert bd["dining"] == 25  # dinner → dining

    def test_lunch_synonym(self):
        bd = parse_cost_breakdown("~$50/person (lodging $30 + lunch $20)")
        assert bd["dining"] == 20

    def test_no_parens_returns_none(self):
        assert parse_cost_breakdown("~$83/person") is None

    def test_empty_returns_none(self):
        assert parse_cost_breakdown("") is None

    def test_total_correct(self):
        bd = parse_cost_breakdown("(lodging $100 + activities $50 + dining $40)")
        assert bd["total"] == 190

    def test_large_numbers(self):
        bd = parse_cost_breakdown("(lodging $1,200 + activities $300 + dining $200)")
        assert bd["lodging"] == 1200
        assert bd["total"] == 1700


# ── Budget header parsing ─────────────────────────────────────────────────────

@pytest.mark.unit
class TestBudgetHeaderParsing:
    """
    The solver emits a budget line like:
      Budget: $200/day | Lodging: ~$120/night | Activities+Dining: ~$80/day | Group: Couple
    The frontend splits on | and renders each as a chip.
    Verify the expected format is parseable.
    """

    def _split_chips(self, line: str) -> list[str]:
        return [s.strip() for s in line.split('|') if s.strip()]

    def test_standard_budget_line(self):
        line = "Budget: $200/day | Lodging: ~$120/night | Activities+Dining: ~$80/day | Group: Couple"
        chips = self._split_chips(line)
        assert len(chips) == 4
        assert chips[0].startswith("Budget:")
        assert "Lodging" in chips[1]
        assert "Group" in chips[3]

    def test_old_format_still_works(self):
        """Old format without lodging chip should still parse to ≥2 chips."""
        line = "Budget: $200/day | Group: Couple"
        chips = self._split_chips(line)
        assert len(chips) == 2

    def test_multi_city_cities_line(self):
        line = "Cities: Lisbon (4 nights) → Porto (3 nights)"
        chips = self._split_chips(line)
        assert len(chips) == 1
        assert "Lisbon" in chips[0]


# ── _country_code helper ──────────────────────────────────────────────────────

@pytest.mark.unit
class TestCountryCode:
    def test_known_cities(self):
        import os, sys
        os.environ.setdefault("ORCHESTRATOR_MODEL", "vertex_ai/gemini-2.5-flash")
        os.environ.setdefault("SPECIALIST_MODEL", "vertex_ai/gemini-2.0-flash")
        os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test-key")
        sys.path.insert(0, str(__file__).split("backend")[0])
        from main import _country_code
        assert _country_code("Rome") == "IT"
        assert _country_code("Tokyo") == "JP"
        assert _country_code("Bangkok") == "TH"
        assert _country_code("London") == "GB"

    def test_unknown_city_defaults_to_us(self):
        from main import _country_code
        assert _country_code("Atlantis") == "US"

    def test_case_insensitive(self):
        """_country_code normalises to lowercase before lookup."""
        from main import _country_code
        assert _country_code("ROME") == "IT"
        assert _country_code("Rome") == "IT"
        assert _country_code("rome") == "IT"
