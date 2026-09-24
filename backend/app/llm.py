"""LLM provider abstraction. Default: local Ollama (OpenAI-free, no key needed)."""

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


class MockProvider:
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
