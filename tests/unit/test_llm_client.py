"""Tests for LLM client (no real API calls)."""

import pytest

from geochem.core.config import AppConfig, ProviderConfig, ModelConfig, TaskModelConfig, _default_config
from geochem.providers.llm_client import LLMClient
from geochem.providers.openai_provider import OpenAIProvider
from geochem.providers.anthropic_provider import AnthropicProvider
from geochem.providers.ollama_provider import OllamaProvider
from geochem.providers.registry import ProviderRegistry, get_registry


@pytest.fixture
def config():
    return _default_config()


class TestProviderRegistry:
    def test_register_and_get(self):
        registry = ProviderRegistry()
        registry.register_class("test", OpenAIProvider)
        provider = registry.create_provider("test", api_key="sk-test")
        assert isinstance(provider, OpenAIProvider)
        assert registry.get("test") is provider

    def test_get_not_found(self):
        registry = ProviderRegistry()
        with pytest.raises(Exception):
            registry.get("nonexistent")

    def test_list_providers(self):
        registry = ProviderRegistry()
        registry.register_class("a", OpenAIProvider)
        registry.register_class("b", AnthropicProvider)
        registry.create_provider("a", api_key="sk-a")
        registry.create_provider("b", api_key="sk-b")
        names = registry.list_providers()
        assert "a" in names
        assert "b" in names

    def test_is_registered_class_only(self):
        registry = ProviderRegistry()
        registry.register_class("test", OpenAIProvider)
        assert registry.is_registered("test")
        assert not registry.is_registered("other")

    def test_is_registered_instance(self):
        registry = ProviderRegistry()
        registry.register_class("test", OpenAIProvider)
        registry.create_provider("test", api_key="sk-test")
        assert registry.is_registered("test")


class TestLLMClient:
    def test_init_registers_providers(self, config):
        client = LLMClient(config)
        assert "openai" in client.registry._provider_classes
        assert "anthropic" in client.registry._provider_classes
        assert "ollama" in client.registry._provider_classes

    def test_get_task_config_default(self, config):
        client = LLMClient(config)
        tc = client._get_task_config("field_mapping")
        assert tc.provider == "anthropic"
        assert tc.model == "claude-sonnet-4-20250514"

    def test_get_task_config_fallback_to_default(self, config):
        client = LLMClient(config)
        tc = client._get_task_config("unknown_task")
        assert tc.provider == "anthropic"

    def test_prompt_hash_deterministic(self, config):
        client = LLMClient(config)
        messages = [{"role": "user", "content": "hello"}]
        h1 = client._compute_prompt_hash(messages, "model-a")
        h2 = client._compute_prompt_hash(messages, "model-a")
        assert h1 == h2

    def test_prompt_hash_different_for_different_input(self, config):
        client = LLMClient(config)
        h1 = client._compute_prompt_hash([{"role": "user", "content": "a"}], "m")
        h2 = client._compute_prompt_hash([{"role": "user", "content": "b"}], "m")
        assert h1 != h2

    def test_list_all_models(self, config):
        client = LLMClient(config)
        models = client.list_all_models()
        assert len(models) >= 4
        model_names = {m["name"] for m in models}
        assert "claude-sonnet-4-20250514" in model_names
        assert "gpt-4o" in model_names

    def test_check_providers_returns_all(self, config):
        client = LLMClient(config)
        results = client.check_providers()
        names = {r["name"] for r in results}
        assert "anthropic" in names
        assert "openai" in names
        assert "deepseek" in names
        assert "ollama" in names


class TestProviderClasses:
    def test_openai_provider_init(self):
        p = OpenAIProvider(api_key="sk-test")
        assert p.name == "openai"
        assert p.api_format == "openai"

    def test_anthropic_provider_init(self):
        p = AnthropicProvider(api_key="sk-test")
        assert p.name == "anthropic"
        assert p.api_format == "native"

    def test_ollama_provider_init(self):
        p = OllamaProvider()
        assert p.name == "ollama"
        assert p.base_url == "http://localhost:11434"

    def test_openai_no_api_key_raises(self):
        p = OpenAIProvider(api_key=None)
        with pytest.raises(Exception):
            p.chat_completion([{"role": "user", "content": "hi"}], "gpt-4o")

    def test_anthropic_convert_messages(self):
        p = AnthropicProvider(api_key="sk-test")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        system, converted = p._convert_messages(messages)
        assert system == "You are helpful."
        assert len(converted) == 2
        assert converted[0]["role"] == "user"
        assert converted[1]["role"] == "assistant"
