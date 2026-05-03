"""
Tests for FastAPI endpoints using TestClient — no live LLM calls.
The Runner.run call is mocked to return a deterministic itinerary string.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("ORCHESTRATOR_MODEL", "vertex_ai/gemini-2.5-flash")
os.environ.setdefault("SPECIALIST_MODEL", "vertex_ai/gemini-2.0-flash")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test-key")

sys.path.insert(0, str(__file__).split("backend")[0])


MOCK_ITINERARY = """\
**5-Day Rome Itinerary**
Budget: $200/day | Group: Couple

**Day 1 — Vatican**
- 🏨 Hotel Roma (all nights)
- 🌅 Morning: Vatican Museums (3h, $25/person)
- 🍽️ Lunch: La Piazzetta (~$18/person)
- 🌆 Evening: Dinner at Tonnarello (~$20/person)
- 💰 Day total: ~$63/person

**Day 2 — Ancient Rome**
- 🌅 Morning: Colosseum (2h, $18/person)
- 🍽️ Lunch: Osteria (~$15/person)
- 🌆 Evening: Dinner at Roma (~$22/person)
- 💰 Day total: ~$55/person

**Total estimated cost: $590 for 2 people over 5 days**
"""


def _mock_runner_result(text: str = MOCK_ITINERARY) -> MagicMock:
    result = MagicMock()
    result.final_output = text
    return result


# ── /health ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_health_endpoint():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"


# ── /chat ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestChatEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        import main
        return TestClient(main.app)

    def _post(self, client, message: str, session_id: str = "test-session") -> dict:
        with patch("main.Runner.run", new=AsyncMock(return_value=_mock_runner_result())):
            resp = client.post("/chat", json={"message": message, "session_id": session_id})
        assert resp.status_code == 200
        return resp.json()

    def test_returns_session_id(self, client):
        data = self._post(client, "5-day Rome trip, couple, $200/day")
        assert "session_id" in data
        assert data["session_id"] == "test-session"

    def test_returns_response_field(self, client):
        data = self._post(client, "5-day Rome trip, couple, $200/day")
        assert "response" in data
        assert len(data["response"]) > 0

    def test_itinerary_detected_in_response(self, client):
        data = self._post(client, "5-day Rome trip, couple, $200/day")
        assert "**Day 1" in data["response"]

    def test_generates_new_session_id_when_absent(self, client):
        with patch("main.Runner.run", new=AsyncMock(return_value=_mock_runner_result())):
            resp = client.post("/chat", json={"message": "Plan a trip"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert len(data["session_id"]) > 0

    def test_locked_slots_accepted(self, client):
        with patch("main.Runner.run", new=AsyncMock(return_value=_mock_runner_result())):
            resp = client.post("/chat", json={
                "message": "Vatican closed",
                "session_id": "lock-test",
                "locked_slots": ["day1_morning", "day2_evening"],
            })
        assert resp.status_code == 200

    def test_off_topic_message_handled(self, client):
        """Off-topic messages should return a graceful response, not 500."""
        from agents import InputGuardrailTripwireTriggered
        from unittest.mock import MagicMock
        # InputGuardrailTripwireTriggered takes a single InputGuardrailResult arg
        fake_result = MagicMock()
        with patch("main.Runner.run", side_effect=InputGuardrailTripwireTriggered(fake_result)):
            resp = client.post("/chat", json={"message": "What is the capital of France?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data


# ── /qr ───────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestQrEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        import main
        return TestClient(main.app)

    def test_valid_url_returns_data_uri(self, client):
        url = "https://www.google.com/maps/dir/Rome/Florence/"
        resp = client.get(f"/qr?url={url}")
        assert resp.status_code == 200
        data = resp.json()
        assert "data_uri" in data
        assert data["data_uri"].startswith("data:image/png;base64,")

    def test_missing_url_returns_422(self, client):
        resp = client.get("/qr")
        assert resp.status_code == 422


# ── /debug/last-run ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_debug_last_run_returns_dict():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    resp = client.get("/debug/last-run")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
