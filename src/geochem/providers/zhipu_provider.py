"""Zhipu GLM provider using zhipuai SDK."""

from __future__ import annotations

import time
from typing import Any

from ..core.exceptions import APIKeyError, LLMError, ProviderConnectionError
from ..core.logging_config import get_logger
from ..core.models import LLMResponse
from .base import BaseProvider

logger = get_logger("providers.zhipu")


class ZhipuProvider(BaseProvider):
    """Provider for Zhipu GLM models."""

    name = "zhipu"
    display_name = "智谱 GLM"
    api_format = "native"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs):
        super().__init__(api_key, base_url, **kwargs)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from zhipuai import ZhipuAI
            except ImportError:
                raise LLMError("zhipuai package not installed. Run: pip install zhipuai")
            if not self.api_key:
                raise APIKeyError(f"API key is required for {self.display_name} provider")
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = ZhipuAI(**kwargs)
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
        start = time.time()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency = int((time.time() - start) * 1000)

            content = response.choices[0].message.content if response.choices else ""
            usage = response.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0

            return LLMResponse(
                content=content,
                model=model,
                provider=self.name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                finish_reason=response.choices[0].finish_reason if response.choices else "",
                latency_ms=latency,
                raw_response={"model": model},
            )
        except Exception as e:
            raise LLMError(f"Zhipu API error: {e}") from e

    def validate_api_key(self) -> bool:
        try:
            client = self._get_client()
            client.chat.completions.create(
                model="glm-4-flash",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
            )
            return True
        except Exception:
            return False

    def list_models(self) -> list[dict[str, Any]]:
        return [
            {"name": "glm-4-plus", "display_name": "GLM-4 Plus", "supports_vision": False},
            {"name": "glm-4", "display_name": "GLM-4", "supports_vision": False},
            {"name": "glm-4-flash", "display_name": "GLM-4 Flash", "supports_vision": False},
            {"name": "glm-4v", "display_name": "GLM-4V (Vision)", "supports_vision": True},
            {"name": "glm-4-long", "display_name": "GLM-4 Long", "supports_vision": False},
        ]
