"""Ollama local model provider."""

from __future__ import annotations

import time
from typing import Any

import requests

from ..core.exceptions import LLMError, ProviderConnectionError
from ..core.logging_config import get_logger
from ..core.models import LLMResponse
from .base import BaseProvider

logger = get_logger("providers.ollama")


class OllamaProvider(BaseProvider):
    """Provider for Ollama local models."""

    name = "ollama"
    display_name = "Ollama (Local)"
    api_format = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs):
        super().__init__(api_key, base_url or "http://localhost:11434", **kwargs)

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        base = self.base_url.rstrip("/")
        start_time = time.time()

        try:
            # Use Ollama's OpenAI-compatible endpoint
            resp = requests.post(
                f"{base}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=300,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.ConnectionError:
            raise ProviderConnectionError(
                f"Cannot connect to Ollama at {base}. Is Ollama running? Start with: ollama serve"
            )
        except Exception as e:
            raise LLMError(f"Ollama API error: {e}") from e

        latency_ms = int((time.time() - start_time) * 1000)

        usage = data.get("usage", {})
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            provider=self.name,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cached_tokens=0,
            total_tokens=usage.get("total_tokens", 0),
            finish_reason=data.get("choices", [{}])[0].get("finish_reason", ""),
            latency_ms=latency_ms,
            raw_response=data,
        )

    def validate_api_key(self) -> bool:
        return self.check_connection()

    def check_connection(self) -> bool:
        """Check if Ollama is running."""
        base = self.base_url.rstrip("/")
        try:
            resp = requests.get(f"{base}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[dict[str, Any]]:
        base = self.base_url.rstrip("/")
        try:
            resp = requests.get(f"{base}/api/tags", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            return [
                {
                    "name": m["name"],
                    "display_name": m["name"],
                    "size": m.get("size", 0),
                    "modified_at": m.get("modified_at", ""),
                }
                for m in models
            ]
        except Exception as e:
            logger.warning(f"Failed to list Ollama models: {e}")
            return []
