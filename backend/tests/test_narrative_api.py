"""API tests for /api/narrative/* and unit tests for OllamaProvider."""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm import LLMError, MockProvider, OllamaProvider

GOOD_NARRATIVE = (
    "Good evening! Here's today's summary for Mehta Clinic (27 Jul).\n"
    "₹3,190 billed across 18 visits, ₹3,172 collected (99%).\n"
    "₹18 is still outstanding across 3 visits.\n"
    "Busiest hour: 1pm-2pm, with ₹760 in revenue.\n"
    "Top mover by quantity: OMEPRAZOLE (18 units).\n"
    "Top by revenue: ATORVASTATIN (₹1,200).\n"
    "Note: cost data wasn't available, so this is revenue, not profit — "
    "flagging rather than estimating."
)


def _install_mock_provider(app, narrative_text: str = GOOD_NARRATIVE) -> MockProvider:
    provider = MockProvider(json.dumps({"narrative": narrative_text}))
    app.state.llm_provider = provider
    app.state.llm_max_retries = 1
    return provider


# --- /api/narrative/* -------------------------------------------------------


def test_get_narrative_404_when_not_generated(seeded_client):
    resp = seeded_client.get("/api/narrative/2026-07-27")
    assert resp.status_code == 404
    assert "No narrative generated" in resp.json()["detail"]["message"]


def test_post_narrative_generates_and_persists(seeded_client):
    from app.main import app

    provider = _install_mock_provider(app)
    resp = seeded_client.post("/api/narrative/2026-07-27")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "ollama"
    assert body["grounded"] is True
    assert body["date"] == "2026-07-27"
    assert body["narrative"] == GOOD_NARRATIVE
    assert len(provider.calls) == 1
    assert body["traced_figures"]

    # persisted → subsequent GET returns the cached narrative
    cached = seeded_client.get("/api/narrative/2026-07-27")
    assert cached.status_code == 200
    assert cached.json()["narrative"] == GOOD_NARRATIVE


def test_post_narrative_404_for_unknown_day(seeded_client):
    resp = seeded_client.post("/api/narrative/2026-01-01")
    assert resp.status_code == 404
    assert "No billing log" in resp.json()["detail"]["message"]


def test_post_narrative_falls_back_on_llm_error(seeded_client):
    from app.main import app

    app.state.llm_provider = MockProvider(LLMError("connection refused"))
    app.state.llm_max_retries = 1
    resp = seeded_client.post("/api/narrative/2026-07-27")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert body["grounded"] is True
    assert "connection refused" in (body["llm_error"] or "")
    assert "not profit" in body["narrative"]


def test_narrative_context_report(seeded_client):
    resp = seeded_client.get("/api/narrative/2026-07-27/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["date"] == "2026-07-27"
    assert body["reconciliation"]["total_billed_paise"] == 319000


def test_narrative_context_report_404(seeded_client):
    assert seeded_client.get("/api/narrative/2026-01-01/report").status_code == 404


# --- OllamaProvider ---------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_ollama_provider_success(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse({"message": {"content": '{"narrative":"hi"}'}}),
    )
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="test-model")
    out = provider.complete("system", "user")
    assert out == '{"narrative":"hi"}'
    assert provider.model_name == "test-model"


def test_ollama_provider_connection_error(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _boom)
    provider = OllamaProvider()
    with pytest.raises(LLMError, match="Ollama request failed"):
        provider.complete("s", "u")


def test_ollama_provider_bad_payload(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse({"nope": True}))
    provider = OllamaProvider()
    with pytest.raises(LLMError, match="unexpected payload"):
        provider.complete("s", "u")


def test_ollama_provider_empty_content(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse({"message": {"content": "   "}}),
    )
    provider = OllamaProvider()
    with pytest.raises(LLMError, match="empty response"):
        provider.complete("s", "u")
