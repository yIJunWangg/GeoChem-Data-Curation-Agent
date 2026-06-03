"""OpenAI-compatible provider (also works for DeepSeek, Moonshot, Qwen, custom)."""

from __future__ import annotations

import time
from typing import Any

from ..core.exceptions import APIKeyError, LLMError, ProviderConnectionError
from ..core.logging_config import get_logger
from ..core.models import LLMResponse
from .base import BaseProvider

logger = get_logger("providers.openai")


class OpenAIProvider(BaseProvider):
    """Provider using OpenAI-compatible API format.

    Works with: OpenAI, Azure OpenAI, DeepSeek, Moonshot, Qwen,
    LM Studio, and any OpenAI-compatible endpoint.
    """

    name = "openai"
    display_name = "OpenAI"
    api_format = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs):
        super().__init__(api_key, base_url, **kwargs)
        self._client = None
        self._default_headers: dict[str, str] = kwargs.get("default_headers", {})

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise LLMError("openai package not installed. Run: pip install openai")

            if not self.api_key:
                raise APIKeyError(f"API key is required for {self.display_name or 'OpenAI'} provider")

            client_kwargs: dict = {
                "api_key": self.api_key,
                "base_url": self.base_url,
            }
            if self._default_headers:
                client_kwargs["default_headers"] = self._default_headers

            self._client = OpenAI(**client_kwargs)
        return self._client

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        client = self._get_client()
        start_time = time.time()

        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        except Exception as e:
            error_msg = str(e)
            if "authentication" in error_msg.lower() or "api_key" in error_msg.lower():
                raise APIKeyError(f"Invalid API key: {error_msg}") from e
            if "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                raise ProviderConnectionError(f"Connection error: {error_msg}") from e
            raise LLMError(f"OpenAI API error: {error_msg}") from e

        latency_ms = int((time.time() - start_time) * 1000)

        usage = response.usage
        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=response.model,
            provider=self.name,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            finish_reason=response.choices[0].finish_reason or "",
            latency_ms=latency_ms,
            raw_response=response.model_dump() if hasattr(response, "model_dump") else {},
        )

    def validate_api_key(self) -> bool:
        try:
            client = self._get_client()
            client.models.list()
            return True
        except Exception as e:
            logger.warning(f"API key validation failed for {self.name}: {e}")
            return False

    def list_models(self) -> list[dict[str, Any]]:
        try:
            client = self._get_client()
            models = client.models.list()
            return [{"name": m.id, "owned_by": getattr(m, "owned_by", "")} for m in models.data]
        except Exception as e:
            logger.warning(f"Failed to list models for {self.name}: {e}")
            return []
