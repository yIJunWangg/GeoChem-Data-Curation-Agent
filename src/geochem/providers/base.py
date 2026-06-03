"""Base LLM provider abstract class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from ..core.models import LLMResponse


class BaseProvider(ABC):
    """Abstract base class for all LLM providers."""

    name: str = ""
    display_name: str = ""
    api_format: str = "native"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs):
        self.api_key = api_key
        self.base_url = base_url
        self._extra = kwargs

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request and return a standardized response."""
        ...

    @abstractmethod
    def validate_api_key(self) -> bool:
        """Validate that the API key is working."""
        ...

    @abstractmethod
    def list_models(self) -> list[dict[str, Any]]:
        """List available models from this provider."""
        ...

    def get_model_info(self, model_name: str) -> dict[str, Any] | None:
        """Get info about a specific model."""
        for m in self.list_models():
            if m.get("name") == model_name:
                return m
        return None
