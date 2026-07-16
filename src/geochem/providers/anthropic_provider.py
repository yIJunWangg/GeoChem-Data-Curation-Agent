"""Anthropic Claude provider."""

from __future__ import annotations

import time
from typing import Any

from ..core.exceptions import APIKeyError, LLMError, ProviderConnectionError
from ..core.logging_config import get_logger
from ..core.models import LLMResponse
from .base import BaseProvider

logger = get_logger("providers.anthropic")


class AnthropicProvider(BaseProvider):
    """Provider for Anthropic Claude models."""

    name = "anthropic"
    display_name = "Anthropic"
    api_format = "native"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs):
        super().__init__(api_key, base_url, **kwargs)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise LLMError("anthropic package not installed. Run: pip install anthropic")

            if not self.api_key:
                raise APIKeyError(f"API key is required for {self.display_name or 'Anthropic'} provider")

            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def _convert_messages(self, messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Convert OpenAI-style messages to Anthropic format.

        Returns (system_prompt, messages).
        """
        system = ""
        converted: list[dict[str, Any]] = []
        for msg in messages:
            if msg["role"] == "system":
                system = str(msg.get("content") or "")
            elif msg["role"] == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": str(msg.get("tool_call_id") or ""),
                        "content": str(msg.get("content") or ""),
                    }],
                })
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": str(msg["content"])})
                for call in msg.get("tool_calls") or []:
                    function = call.get("function") or {}
                    arguments = function.get("arguments") or call.get("args") or {}
                    if isinstance(arguments, str):
                        try:
                            import json
                            arguments = json.loads(arguments)
                        except Exception:
                            arguments = {"_raw_arguments": arguments}
                    blocks.append({
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(function.get("name") or call.get("name") or ""),
                        "input": arguments,
                    })
                converted.append({"role": "assistant", "content": blocks})
            else:
                converted.append({"role": msg["role"], "content": msg.get("content") or ""})
        return system, converted

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        client = self._get_client()
        start_time = time.time()

        system, msgs = self._convert_messages(messages)

        try:
            request_kwargs = {
                "model": model,
                "messages": msgs,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system:
                request_kwargs["system"] = system

            tools = kwargs.pop("tools", None)
            if tools:
                request_kwargs["tools"] = [
                    {
                        "name": str((tool.get("function") or {}).get("name") or tool.get("name") or ""),
                        "description": str((tool.get("function") or {}).get("description") or tool.get("description") or ""),
                        "input_schema": (tool.get("function") or {}).get("parameters") or tool.get("input_schema") or {"type": "object", "properties": {}},
                    }
                    for tool in tools
                ]
            tool_choice = kwargs.pop("tool_choice", None)
            if tool_choice and tool_choice != "auto":
                request_kwargs["tool_choice"] = {"type": "any"} if tool_choice in {"required", "any"} else {"type": "tool", "name": str(tool_choice)}

            response = client.messages.create(**request_kwargs, **kwargs)
        except Exception as e:
            error_msg = str(e)
            if "authentication" in error_msg.lower() or "api_key" in error_msg.lower():
                raise APIKeyError(f"Invalid API key: {error_msg}") from e
            if "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                raise ProviderConnectionError(f"Connection error: {error_msg}") from e
            raise LLMError(f"Anthropic API error: {error_msg}") from e

        latency_ms = int((time.time() - start_time) * 1000)

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content or []:
            if getattr(block, "type", "") == "text" and hasattr(block, "text"):
                text_parts.append(block.text)
            elif getattr(block, "type", "") == "tool_use":
                tool_calls.append({
                    "id": str(getattr(block, "id", "")),
                    "name": str(getattr(block, "name", "")),
                    "args": getattr(block, "input", {}) or {},
                })
        content = "\n".join(part for part in text_parts if part)

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cached_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0

        return LLMResponse(
            content=content,
            model=response.model,
            provider=self.name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            total_tokens=input_tokens + output_tokens,
            finish_reason=response.stop_reason or "",
            latency_ms=latency_ms,
            tool_calls=tool_calls,
            raw_response=response.model_dump() if hasattr(response, "model_dump") else {},
        )

    def validate_api_key(self) -> bool:
        try:
            client = self._get_client()
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            return True
        except Exception as e:
            err = str(e).lower()
            # Authentication errors = key is invalid
            if "authentication" in err or "invalid" in err or "401" in err or "api_key" in err or "permission" in err:
                logger.warning(f"API key invalid: {e}")
                return False
            # Other errors (model not found, rate limit, timeout) = key likely works
            logger.info(f"Key validation non-fatal error (key likely valid): {e}")
            return True

    def list_models(self) -> list[dict[str, Any]]:
        # Anthropic doesn't have a list models API, return known models
        return [
            {"name": "claude-opus-4-7", "display_name": "Claude Opus 4.7"},
            {"name": "claude-sonnet-4-20250514", "display_name": "Claude Sonnet 4"},
            {"name": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5"},
        ]
