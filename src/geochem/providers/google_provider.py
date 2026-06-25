"""Google Gemini provider using google-genai SDK."""

from __future__ import annotations

import time
from typing import Any

from ..core.exceptions import APIKeyError, LLMError, ProviderConnectionError
from ..core.logging_config import get_logger
from ..core.models import LLMResponse
from .base import BaseProvider

logger = get_logger("providers.google")


class GoogleProvider(BaseProvider):
    """Provider for Google Gemini models."""

    name = "google"
    display_name = "Google Gemini"
    api_format = "native"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs):
        super().__init__(api_key, base_url, **kwargs)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise LLMError("google-genai package not installed. Run: pip install google-genai")
            if not self.api_key:
                raise APIKeyError(f"API key is required for {self.display_name} provider")
            self._client = genai.Client(api_key=self.api_key)
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
            from google.genai import types

            # Convert messages: extract system, combine user/assistant into contents
            system_text = ""
            contents = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    system_text = content
                elif role == "user":
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=content)],
                    ))
                elif role == "assistant":
                    contents.append(types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=content)],
                    ))

            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            if system_text:
                config.system_instruction = system_text

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            latency = int((time.time() - start) * 1000)

            text = response.text or ""
            usage = response.usage_metadata
            input_tokens = usage.prompt_token_count if usage else 0
            output_tokens = usage.candidates_token_count if usage else 0

            return LLMResponse(
                content=text,
                model=model,
                provider=self.name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                finish_reason="stop",
                latency_ms=latency,
                raw_response={"model": model},
            )
        except Exception as e:
            raise LLMError(f"Google Gemini API error: {e}") from e

    def validate_api_key(self) -> bool:
        try:
            client = self._get_client()
            # Try listing models as a connectivity check
            for _ in client.models.list():
                break
            return True
        except Exception:
            return False

    def list_models(self) -> list[dict[str, Any]]:
        try:
            client = self._get_client()
            models = []
            for model in client.models.list():
                models.append({
                    "name": model.name.replace("models/", ""),
                    "display_name": model.display_name or model.name,
                    "supports_vision": "vision" in model.name.lower() or "flash" in model.name.lower(),
                })
            return models
        except Exception:
            return [
                {"name": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash", "supports_vision": True},
                {"name": "gemini-2.5-pro", "display_name": "Gemini 2.5 Pro", "supports_vision": True},
                {"name": "gemini-2.0-flash", "display_name": "Gemini 2.0 Flash", "supports_vision": True},
            ]
