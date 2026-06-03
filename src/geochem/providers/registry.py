"""LLM provider registry: register, lookup, list providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.exceptions import ProviderNotFoundError
from ..core.logging_config import get_logger

if TYPE_CHECKING:
    from .base import BaseProvider

logger = get_logger("providers")


class ProviderRegistry:
    """Registry of available LLM providers."""

    def __init__(self):
        self._providers: dict[str, BaseProvider] = {}
        self._provider_classes: dict[str, type[BaseProvider]] = {}

    def register_class(self, name: str, cls: type[BaseProvider]) -> None:
        """Register a provider class (not yet instantiated)."""
        self._provider_classes[name] = cls

    def create_provider(self, name: str, api_key: str | None = None, base_url: str | None = None, **kwargs) -> BaseProvider:
        """Create and register a provider instance."""
        if name not in self._provider_classes:
            raise ProviderNotFoundError(f"Provider class not registered: {name}")

        cls = self._provider_classes[name]
        provider = cls(api_key=api_key, base_url=base_url, **kwargs)
        self._providers[name] = provider
        logger.info(f"Created provider: {name}")
        return provider

    def get(self, name: str) -> BaseProvider:
        """Get a registered provider by name."""
        if name not in self._providers:
            raise ProviderNotFoundError(f"Provider not found: {name}")
        return self._providers[name]

    def list_providers(self) -> list[str]:
        """List all registered provider names."""
        return list(self._providers.keys())

    def list_available(self) -> list[dict[str, str]]:
        """List all providers with their status."""
        result = []
        for name, provider in self._providers.items():
            result.append({
                "name": name,
                "display_name": provider.display_name or name,
                "api_format": provider.api_format,
            })
        return result

    def is_registered(self, name: str) -> bool:
        """Check if a provider class is registered or an instance exists."""
        return name in self._providers or name in self._provider_classes


# Global registry instance
_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """Get the global provider registry."""
    return _registry
