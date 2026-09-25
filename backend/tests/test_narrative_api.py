"""API tests for /api/narrative/* and unit tests for the LLM providers."""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm import LLMError, MockProvider, OllamaProvider, OpenRouterProvider

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


def test_post_narrative_reports_openrouter_source(seeded_client, monkeypatch):
    from app.main import app

    app.state.llm_provider = OpenRouterProvider(api_key="sk-test")
    app.state.llm_max_retries = 1
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            {"choices": [{"message": {"content": json.dumps({"narrative": GOOD_NARRATIVE})}}]}
        ),
    )
    resp = seeded_client.post("/api/narrative/2026-07-27")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "openrouter"
    assert body["model"] == "google/gemini-2.0-flash-001"
    assert body["grounded"] is True


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
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://openrouter.test/v1/chat/completions")
            raise httpx.HTTPStatusError(
                f"Error {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, json=self._payload, request=request),
            )

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


# --- OpenRouterProvider ------------------------------------------------------


def test_openrouter_provider_success(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            {"choices": [{"message": {"content": '{"narrative":"hi"}'}}]}
        ),
    )
    provider = OpenRouterProvider(api_key="sk-test", model="test/model")
    assert provider.complete("system", "user") == '{"narrative":"hi"}'
    assert provider.model_name == "test/model"


def test_openrouter_provider_requires_key():
    with pytest.raises(ValueError, match="API key"):
        OpenRouterProvider(api_key="  ")


def test_openrouter_provider_sends_auth_header(monkeypatch):
    seen: dict = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        seen["payload"] = kwargs.get("json") or {}
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenRouterProvider(api_key="sk-secret", model="m", max_tokens=400)
    provider.complete("s", "u")
    assert seen["url"].endswith("/chat/completions")
    assert seen["headers"]["Authorization"] == "Bearer sk-secret"
    assert seen["payload"]["max_tokens"] == 400
    assert seen["payload"]["temperature"] == 0.1


def test_openrouter_provider_http_error(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse({"error": {"message": "invalid key"}}, status_code=401),
    )
    provider = OpenRouterProvider(api_key="sk-bad")
    with pytest.raises(LLMError, match="401"):
        provider.complete("s", "u")


def test_openrouter_provider_connection_error(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(LLMError, match="OpenRouter request failed"):
        OpenRouterProvider(api_key="sk-test").complete("s", "u")


def test_openrouter_provider_bad_payload(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse({"nope": True}))
    with pytest.raises(LLMError, match="unexpected payload"):
        OpenRouterProvider(api_key="sk-test").complete("s", "u")


def test_openrouter_provider_empty_content(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse({"choices": [{"message": {"content": "  "}}]}),
    )
    with pytest.raises(LLMError, match="empty response"):
        OpenRouterProvider(api_key="sk-test").complete("s", "u")


# --- provider selection (build_llm_provider) ---------------------------------


def test_provider_auto_uses_openrouter_when_key_set(monkeypatch):
    from app.main import build_llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    provider = build_llm_provider()
    assert isinstance(provider, OpenRouterProvider)


def test_provider_auto_falls_back_to_ollama(monkeypatch):
    from app.main import build_llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    provider = build_llm_provider()
    assert isinstance(provider, OllamaProvider)


def test_provider_openrouter_without_key_fails_fast(monkeypatch):
    from app.main import build_llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        build_llm_provider()


def test_provider_explicit_ollama_wins_over_key(monkeypatch):
    from app.main import build_llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    provider = build_llm_provider()
    assert isinstance(provider, OllamaProvider)


def test_provider_unknown_kind_rejected(monkeypatch):
    from app.main import build_llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        build_llm_provider()
