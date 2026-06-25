"""Unified LLM client: task routing, fallback, caching, token tracking."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any

from ..core.config import AppConfig, TaskModelConfig
from ..core.database import Database
from ..core.exceptions import LLMError, ProviderNotFoundError
from ..core.logging_config import get_logger
from ..core.models import LLMCallRecord, LLMResponse
from .anthropic_provider import AnthropicProvider
from .google_provider import GoogleProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider
from .registry import ProviderRegistry, get_registry
from .zhipu_provider import ZhipuProvider

logger = get_logger("llm_client")


class LLMClient:
    """Unified LLM client with multi-provider support, task routing, and fallback."""

    def __init__(self, config: AppConfig, db: Database | None = None):
        self.config = config
        self.db = db
        self.registry = get_registry()
        self._cache: dict[str, LLMResponse] = {}
        self._call_counter = 0

        self._register_built_in_providers()
        self._init_providers_from_config()

    def _register_built_in_providers(self) -> None:
        """Register built-in provider classes."""
        self.registry.register_class("openai", OpenAIProvider)
        self.registry.register_class("anthropic", AnthropicProvider)
        self.registry.register_class("google", GoogleProvider)
        self.registry.register_class("zhipu", ZhipuProvider)
        # OpenAI-compatible providers
        for name in ("deepseek", "moonshot", "qwen", "xiaomi", "openrouter",
                     "minimax", "doubao", "baichuan", "hunyuan", "yi", "stepfun"):
            self.registry.register_class(name, OpenAIProvider)
        self.registry.register_class("xiaomi-anthropic", AnthropicProvider)
        self.registry.register_class("ollama", OllamaProvider)

    def _init_providers_from_config(self) -> None:
        """Initialize providers from configuration."""
        import os

        for prov_config in self.config.providers:
            if not prov_config.enabled:
                continue

            api_key = prov_config.api_key
            if api_key and api_key.startswith("${") and api_key.endswith("}"):
                env_var = api_key[2:-1]
                api_key = os.environ.get(env_var)

            base_url = prov_config.base_url
            provider_name = prov_config.name

            # Build kwargs for provider creation
            create_kwargs: dict = {"api_key": api_key, "base_url": base_url}
            if prov_config.default_headers:
                create_kwargs["default_headers"] = prov_config.default_headers

            # Determine which class to use
            class_name = provider_name
            # Prefer native provider class if registered (google, zhipu, anthropic, ollama)
            native_classes = {"google", "zhipu", "anthropic", "ollama"}
            if provider_name in native_classes and self.registry.is_registered(provider_name):
                class_name = provider_name
            elif prov_config.api_format == "openai" and provider_name not in ("openai", "ollama"):
                class_name = "openai"  # Use OpenAI-compatible class

            if self.registry.is_registered(class_name) or class_name in self.registry._provider_classes:
                try:
                    provider = self.registry.create_provider(provider_name, **create_kwargs)
                    # Set instance-level display name for error messages
                    provider.display_name = prov_config.display_name or provider_name
                    provider.name = provider_name
                except Exception as e:
                    logger.warning(f"Failed to initialize provider {provider_name}: {e}")

    def _generate_call_id(self) -> str:
        if self.db and self._call_counter == 0:
            try:
                row = self.db.fetch_one(
                    "SELECT MAX(CAST(SUBSTR(call_id, 5) AS INTEGER)) as max_id "
                    "FROM llm_calls WHERE call_id LIKE 'LLM_%'"
                )
                self._call_counter = row["max_id"] if row and row["max_id"] else 0
            except Exception:
                self._call_counter = 0
        self._call_counter += 1
        return f"LLM_{self._call_counter:06d}"

    def _compute_prompt_hash(self, messages: list[dict[str, str]], model: str) -> str:
        content = json.dumps({"messages": messages, "model": model}, sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _get_task_config(self, task_name: str) -> TaskModelConfig:
        """Get model config for a task, falling back to default."""
        if task_name in self.config.task_models:
            return self.config.task_models[task_name]
        if "_default" in self.config.task_models:
            return self.config.task_models["_default"]
        return TaskModelConfig(provider="anthropic", model="claude-sonnet-4-20250514")

    def chat(
        self,
        messages: list[dict[str, str]],
        task_name: str = "_default",
        model_override: str | None = None,
        provider_override: str | None = None,
        temperature_override: float | None = None,
        max_tokens_override: int | None = None,
        project_id: str = "",
        article_id: str = "",
        agent_name: str = "",
        skill_name: str = "",
        use_cache: bool = True,
    ) -> LLMResponse:
        """Send a chat completion request with task routing and fallback.

        Args:
            messages: Chat messages in OpenAI format.
            task_name: Task type for model selection.
            model_override: Override the model name.
            provider_override: Override the provider name.
            temperature_override: Override temperature.
            max_tokens_override: Override max tokens.
            project_id: Project ID for tracking.
            article_id: Article ID for tracking.
            agent_name: Agent name for tracking.
            skill_name: Skill name for tracking.
            use_cache: Whether to use cached responses.
        """
        task_config = self._get_task_config(task_name)
        provider_name = provider_override or task_config.provider
        model = model_override or task_config.model
        temperature = temperature_override if temperature_override is not None else task_config.temperature
        max_tokens = max_tokens_override or task_config.max_tokens

        # Check cache
        prompt_hash = self._compute_prompt_hash(messages, model)
        if use_cache and prompt_hash in self._cache:
            logger.debug(f"Cache hit for prompt hash {prompt_hash}")
            return self._cache[prompt_hash]

        # Build fallback chain: try requested provider first, then fallbacks
        providers_to_try = [(provider_name, model)]
        for fb in self.config.fallback_chain:
            pair = (fb["provider"], fb["model"])
            if pair not in providers_to_try:
                providers_to_try.append(pair)

        last_error = None
        for prov_name, mdl in providers_to_try:
            try:
                provider = self.registry.get(prov_name)
            except ProviderNotFoundError:
                continue

            # Retry loop for rate limit errors
            max_retries = 3
            for attempt in range(max_retries):
                call_id = self._generate_call_id()
                start_time = time.time()

                try:
                    response = provider.chat_completion(
                        messages=messages,
                        model=mdl,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )

                    # Track the call
                    call_record = LLMCallRecord(
                        call_id=call_id,
                        project_id=project_id,
                        article_id=article_id,
                        agent_name=agent_name,
                        skill_name=skill_name,
                        model_provider=prov_name,
                        model_name=mdl,
                        prompt_hash=prompt_hash,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        cached_tokens=response.cached_tokens,
                        total_tokens=response.total_tokens,
                        started_at=datetime.fromtimestamp(start_time),
                        ended_at=datetime.now(),
                        latency_ms=response.latency_ms,
                        status="success",
                        request_summary=f"Task: {task_name}, Messages: {len(messages)}",
                        response_summary=response.content[:200] if response.content else "",
                    )
                    self._record_call(call_record)

                    # Cache the response
                    if use_cache:
                        self._cache[prompt_hash] = response

                    return response

                except Exception as e:
                    last_error = e
                    elapsed = int((time.time() - start_time) * 1000)
                    logger.warning(f"Provider {prov_name}/{mdl} failed: {e}")

                    # Record failed call
                    call_record = LLMCallRecord(
                        call_id=call_id,
                        project_id=project_id,
                        article_id=article_id,
                        agent_name=agent_name,
                        skill_name=skill_name,
                        model_provider=prov_name,
                        model_name=mdl,
                        prompt_hash=prompt_hash,
                        started_at=datetime.fromtimestamp(start_time),
                        ended_at=datetime.now(),
                        latency_ms=elapsed,
                        status="error",
                        error_message=str(e),
                        request_summary=f"Task: {task_name}, Messages: {len(messages)}",
                    )
                    self._record_call(call_record)

                    # Retry on rate limit (429)
                    is_rate_limit = "429" in str(e) or "rate limit" in str(e).lower()
                    if is_rate_limit and attempt < max_retries - 1:
                        wait = (attempt + 1) * 15  # 15s, 30s, 45s
                        logger.info(f"Rate limited, retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    else:
                        break  # Move to next provider

        raise LLMError(f"All providers failed. Last error: {last_error}")

    def _record_call(self, record: LLMCallRecord) -> None:
        """Record an LLM call to the database."""
        if not self.db:
            return

        try:
            self.db.execute(
                """INSERT INTO llm_calls
                (call_id, project_id, article_id, agent_name, skill_name,
                 model_provider, model_name, prompt_version, prompt_hash,
                 input_tokens, output_tokens, cached_tokens, total_tokens,
                 estimated_cost, started_at, ended_at, latency_ms,
                 status, error_message, retry_count, request_summary, response_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.call_id, record.project_id, record.article_id,
                    record.agent_name, record.skill_name,
                    record.model_provider, record.model_name,
                    record.prompt_version, record.prompt_hash,
                    record.input_tokens, record.output_tokens, record.cached_tokens,
                    record.total_tokens, record.estimated_cost,
                    record.started_at.isoformat() if record.started_at else None,
                    record.ended_at.isoformat() if record.ended_at else None,
                    record.latency_ms, record.status, record.error_message,
                    record.retry_count, record.request_summary, record.response_summary,
                ),
            )
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to record LLM call: {e}")

    def check_providers(self) -> list[dict[str, Any]]:
        """Check connectivity of all configured providers."""
        results = []
        for prov_config in self.config.providers:
            name = prov_config.name
            result = {
                "name": name,
                "display_name": prov_config.display_name or name,
                "status": "unknown",
                "models": [],
            }
            try:
                provider = self.registry.get(name)
                valid = provider.validate_api_key()
                result["status"] = "connected" if valid else "auth_failed"
                if valid:
                    api_models = [m["name"] for m in provider.list_models()]
                    # For providers with custom base_url, prefer configured models
                    # (API may return models from the upstream provider, not the actual service)
                    if prov_config.base_url and prov_config.models:
                        result["models"] = [m.name for m in prov_config.models]
                    else:
                        result["models"] = api_models
            except ProviderNotFoundError:
                result["status"] = "not_configured"
            except Exception as e:
                result["status"] = f"error: {e}"

            results.append(result)
        return results

    def list_all_models(self) -> list[dict[str, Any]]:
        """List all available models across all providers."""
        all_models = []
        for prov_config in self.config.providers:
            name = prov_config.name
            try:
                provider = self.registry.get(name)
                for model_cfg in prov_config.models:
                    all_models.append({
                        "provider": name,
                        "name": model_cfg.name,
                        "display_name": model_cfg.display_name or model_cfg.name,
                        "max_tokens": model_cfg.max_tokens,
                        "supports_streaming": model_cfg.supports_streaming,
                        "supports_vision": model_cfg.supports_vision,
                    })
            except Exception:
                pass
        return all_models
