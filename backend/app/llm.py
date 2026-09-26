"""LLM provider abstraction. Default: OpenRouter (key required) or local Ollama."""

from __future__ import annotations

from typing import Protocol

import httpx


class LLMError(Exception):
    """Raised when the LLM provider fails or returns an unusable response."""


class LLMProvider(Protocol):
    model_name: str

    def complete(self, system: str, user: str) -> str:
        """Return the raw model response text."""
        ...


class OllamaProvider:
    source_name = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen3:8b",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model_name,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 500},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError(f"Ollama returned an unexpected payload: {exc}") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMError("Ollama returned an empty response")
        return content


class OpenRouterProvider:
    """OpenAI-compatible chat completions via OpenRouter (https://openrouter.ai)."""

    source_name = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str = "nvidia/nemotron-3-super-120b-a12b:free",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 90.0,
        max_tokens: int = 1000,
    ):
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError("OpenRouterProvider requires an API key")
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model_name,
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "SwasthiQ EOD Billing",
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise LLMError(
                f"OpenRouter request failed ({exc.response.status_code}): {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenRouter request failed: {exc}") from exc

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise LLMError(f"OpenRouter returned an unexpected payload: {exc}") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMError("OpenRouter returned an empty response")
        return content


class MockProvider:
    """Test double: canned content. Stands in for the live Ollama path."""

    source_name = "ollama"
    """Deterministic provider for tests: returns a fixed JSON narrative."""

    def __init__(self, response: str | Exception = ""):
        self.response = response
        self.model_name = "mock"
        self.calls: list[dict] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response
