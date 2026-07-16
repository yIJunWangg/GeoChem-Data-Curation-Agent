"""Configuration management for GeoChem."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .secrets import looks_like_plaintext_secret, secret_ref_for_provider, store_secret_for_provider


DEFAULT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "geochem-data"
DEFAULT_CONFIG_PATH = Path("config/settings.yaml")
LOCAL_CONFIG_PATH = Path("config/settings.local.yaml")


class ModelPricing(BaseModel):
    """Pricing per 1K tokens for a model."""
    input_price: float = 0.0
    output_price: float = 0.0
    cached_input_price: float = 0.0


class ModelConfig(BaseModel):
    """Configuration for a single model."""
    name: str
    display_name: str = ""
    max_tokens: int = 4096
    supports_streaming: bool = True
    supports_vision: bool = False
    supports_json_mode: bool = False
    supports_structured_output: bool = False
    supports_reasoning_toggle: bool = False
    pricing: ModelPricing = Field(default_factory=ModelPricing)


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider."""
    name: str
    display_name: str = ""
    api_key: str | None = None
    base_url: str | None = None
    api_format: str = "native"
    enabled: bool = True
    models: list[ModelConfig] = Field(default_factory=list)
    default_headers: dict[str, str] = Field(default_factory=dict)
    is_custom: bool = False
    auth_type: str = "bearer"
    api_key_header: str = "Authorization"
    provider_kind: str = "preset"


class TaskModelConfig(BaseModel):
    """Which model to use for a specific task."""
    provider: str
    model: str
    temperature: float = 0.1
    max_tokens: int = 4096


class AppConfig(BaseModel):
    """Top-level application configuration."""
    default_project_dir: str = str(DEFAULT_PROJECT_DIR)
    providers: list[ProviderConfig] = Field(default_factory=list)
    task_models: dict[str, TaskModelConfig] = Field(default_factory=dict)
    fallback_chain: list[dict[str, str]] = Field(default_factory=list)
    log_level: str = "INFO"
    log_dir: str = "logs"
    ui_preferences: dict[str, Any] = Field(default_factory=dict)

    def get_provider(self, name: str) -> ProviderConfig | None:
        for p in self.providers:
            if p.name == name:
                return p
        return None

    def get_task_model(self, task_name: str) -> TaskModelConfig | None:
        if task_name in self.task_models:
            return self.task_models[task_name]
        if "_default" in self.task_models:
            return self.task_models["_default"]
        return None


def _model(name: str, display_name: str = "", max_tokens: int = 8192, supports_vision: bool = False) -> ModelConfig:
    return ModelConfig(name=name, display_name=display_name or name, max_tokens=max_tokens, supports_vision=supports_vision)


def provider_presets() -> list[ProviderConfig]:
    """Built-in provider presets that are always available in settings."""
    return [
        ProviderConfig(
            name="deepseek-openai",
            display_name="DeepSeek (OpenAI-compatible)",
            api_format="openai",
            base_url="https://api.deepseek.com",
            api_key="${DEEPSEEK_API_KEY}",
            models=[
                _model("deepseek-v4-flash", "DeepSeek V4 Flash"),
                _model("deepseek-v4-pro", "DeepSeek V4 Pro"),
                _model("deepseek-chat", "DeepSeek Chat (deprecated 2026-07-24)"),
                _model("deepseek-reasoner", "DeepSeek Reasoner (deprecated 2026-07-24)"),
            ],
        ),
        ProviderConfig(
            name="deepseek-anthropic",
            display_name="DeepSeek (Anthropic-compatible)",
            api_format="anthropic",
            base_url="https://api.deepseek.com/anthropic",
            api_key="${DEEPSEEK_API_KEY}",
            models=[_model("deepseek-v4-flash", "DeepSeek V4 Flash"), _model("deepseek-v4-pro", "DeepSeek V4 Pro")],
        ),
        ProviderConfig(
            name="opencode-go-openai",
            display_name="OpenCode Go (OpenAI-compatible)",
            api_format="openai",
            base_url="https://opencode.ai/zen/go/v1",
            api_key="${OPENCODE_GO_API_KEY}",
            models=[
                _model("glm-5.2", "GLM-5.2"),
                _model("glm-5.1", "GLM-5.1"),
                _model("kimi-k2.7-code", "Kimi K2.7 Code"),
                _model("kimi-k2.6", "Kimi K2.6"),
                _model("deepseek-v4-pro", "DeepSeek V4 Pro"),
                _model("deepseek-v4-flash", "DeepSeek V4 Flash"),
                _model("mimo-v2.5", "MiMo V2.5"),
                _model("mimo-v2.5-pro", "MiMo V2.5 Pro"),
            ],
        ),
        ProviderConfig(
            name="opencode-go-anthropic",
            display_name="OpenCode Go (Anthropic-compatible)",
            api_format="anthropic",
            base_url="https://opencode.ai/zen/go/v1",
            api_key="${OPENCODE_GO_API_KEY}",
            models=[
                _model("minimax-m3", "MiniMax M3"),
                _model("minimax-m2.7", "MiniMax M2.7"),
                _model("minimax-m2.5", "MiniMax M2.5"),
                _model("qwen3.7-max", "Qwen3.7 Max"),
                _model("qwen3.7-plus", "Qwen3.7 Plus"),
                _model("qwen3.6-plus", "Qwen3.6 Plus"),
            ],
        ),
        ProviderConfig(
            name="openrouter",
            display_name="OpenRouter",
            api_format="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key="${OPENROUTER_API_KEY}",
            default_headers={"HTTP-Referer": "https://github.com/geochem-agent", "X-Title": "GeoChem Data Curation Agent"},
            models=[
                _model("openai/gpt-4o-mini", "OpenAI GPT-4o Mini"),
                _model("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet"),
                _model("deepseek/deepseek-chat", "DeepSeek Chat"),
                _model("qwen/qwen-2.5-72b-instruct", "Qwen 2.5 72B Instruct"),
            ],
        ),
        ProviderConfig(
            name="xiaomi",
            display_name="Xiaomi MiMo (OpenAI-compatible)",
            api_format="openai",
            base_url="https://token-plan-cn.xiaomimimo.com/v1",
            api_key="${MIMO_API_KEY}",
            models=[_model("mimo-v2.5-pro", "MiMo V2.5 Pro"), _model("mimo-v2.5", "MiMo V2.5"), _model("mimo-v2-pro", "MiMo V2 Pro")],
        ),
        ProviderConfig(
            name="xiaomi-anthropic",
            display_name="Xiaomi MiMo (Anthropic-compatible)",
            api_format="anthropic",
            base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
            api_key="${MIMO_API_KEY}",
            models=[_model("mimo-v2.5-pro", "MiMo V2.5 Pro"), _model("mimo-v2.5", "MiMo V2.5")],
        ),
        ProviderConfig(
            name="openai",
            display_name="OpenAI",
            api_format="openai",
            base_url="https://api.openai.com/v1",
            api_key="${OPENAI_API_KEY}",
            models=[_model("gpt-4o", "GPT-4o", 16384, True), _model("gpt-4o-mini", "GPT-4o Mini", 16384, True)],
        ),
        ProviderConfig(
            name="anthropic",
            display_name="Anthropic",
            api_format="anthropic",
            api_key="${ANTHROPIC_API_KEY}",
            models=[_model("claude-sonnet-4-20250514", "Claude Sonnet 4", 8192, True), _model("claude-haiku-4-5-20251001", "Claude Haiku 4.5", 8192, True)],
        ),
    ]


def merge_provider_presets(config: AppConfig) -> AppConfig:
    """Merge provider presets without losing user-edited providers or models."""
    existing = {provider.name: provider for provider in config.providers}
    merged: list[ProviderConfig] = []
    seen: set[str] = set()
    for preset in provider_presets():
        current = existing.get(preset.name)
        if current:
            provider = preset.model_copy(deep=True)
            for field in ("display_name", "api_key", "base_url", "api_format", "enabled", "default_headers", "auth_type", "api_key_header"):
                value = getattr(current, field)
                if value not in (None, "", {}, []):
                    setattr(provider, field, value)
            provider.is_custom = bool(current.is_custom)
            provider.provider_kind = current.provider_kind or "preset"
            names = {model.name for model in provider.models}
            for model in current.models:
                if model.name not in names:
                    provider.models.append(model)
                    names.add(model.name)
            merged.append(provider)
        else:
            merged.append(preset)
        seen.add(preset.name)
    for provider in config.providers:
        if provider.name not in seen:
            provider.provider_kind = provider.provider_kind or ("custom" if provider.is_custom else "preset")
            merged.append(provider)
    config.providers = merged
    return config


def _config_read_path(config_path: Path | str | None = None) -> Path:
    if config_path is not None:
        return Path(config_path)
    if os.environ.get("GEOCHEM_CONFIG"):
        return Path(os.environ["GEOCHEM_CONFIG"])
    if LOCAL_CONFIG_PATH.exists():
        return LOCAL_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def _config_write_path(config_path: Path | str | None = None) -> Path:
    if config_path is not None:
        path = Path(config_path)
        if not os.environ.get("GEOCHEM_CONFIG") and path == DEFAULT_CONFIG_PATH:
            return LOCAL_CONFIG_PATH
        return path
    if os.environ.get("GEOCHEM_CONFIG"):
        return Path(os.environ["GEOCHEM_CONFIG"])
    return LOCAL_CONFIG_PATH


def sanitize_config_secrets(config: AppConfig) -> AppConfig:
    """Move plaintext-looking provider secrets out of YAML fields."""
    for provider in config.providers:
        if looks_like_plaintext_secret(provider.api_key):
            ref, _source = store_secret_for_provider(provider.name, provider.api_key or "")
            provider.api_key = ref
        elif not provider.api_key and provider.name not in {"ollama"}:
            provider.api_key = secret_ref_for_provider(provider.name)
    return config


def _default_config() -> AppConfig:
    """Create default configuration with common providers."""
    config = AppConfig(
        providers=[
            ProviderConfig(
                name="anthropic",
                display_name="Anthropic",
                api_format="native",
                models=[
                    ModelConfig(
                        name="claude-sonnet-4-20250514",
                        display_name="Claude Sonnet 4",
                        max_tokens=8192,
                        supports_vision=True,
                        pricing=ModelPricing(input_price=0.003, output_price=0.015),
                    ),
                    ModelConfig(
                        name="claude-haiku-4-5-20251001",
                        display_name="Claude Haiku 4.5",
                        max_tokens=8192,
                        supports_vision=True,
                        pricing=ModelPricing(input_price=0.0008, output_price=0.004),
                    ),
                ],
            ),
            ProviderConfig(
                name="openai",
                display_name="OpenAI",
                api_format="openai",
                models=[
                    ModelConfig(
                        name="gpt-4o",
                        display_name="GPT-4o",
                        max_tokens=16384,
                        supports_vision=True,
                        pricing=ModelPricing(input_price=0.0025, output_price=0.01),
                    ),
                    ModelConfig(
                        name="gpt-4o-mini",
                        display_name="GPT-4o Mini",
                        max_tokens=16384,
                        supports_vision=True,
                        pricing=ModelPricing(input_price=0.00015, output_price=0.0006),
                    ),
                ],
            ),
            ProviderConfig(
                name="deepseek",
                display_name="DeepSeek",
                api_format="openai",
                base_url="https://api.deepseek.com/v1",
                models=[
                    ModelConfig(
                        name="deepseek-chat",
                        display_name="DeepSeek V3",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.00014, output_price=0.00028),
                    ),
                    ModelConfig(
                        name="deepseek-reasoner",
                        display_name="DeepSeek R1",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.00055, output_price=0.00219),
                    ),
                ],
            ),
            ProviderConfig(
                name="xiaomi",
                display_name="Xiaomi MiMo (OpenAI)",
                api_format="openai",
                base_url="https://token-plan-cn.xiaomimimo.com/v1",
                api_key="${MIMO_API_KEY}",
                models=[
                    ModelConfig(
                        name="mimo-v2.5-pro",
                        display_name="MiMo V2.5 Pro",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                    ModelConfig(
                        name="mimo-v2.5",
                        display_name="MiMo V2.5",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                    ModelConfig(
                        name="mimo-v2-pro",
                        display_name="MiMo V2 Pro",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                ],
            ),
            ProviderConfig(
                name="xiaomi-anthropic",
                display_name="Xiaomi MiMo (Anthropic)",
                api_format="anthropic",
                base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
                api_key="${MIMO_API_KEY}",
                models=[
                    ModelConfig(
                        name="mimo-v2.5-pro",
                        display_name="MiMo V2.5 Pro",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                    ModelConfig(
                        name="mimo-v2.5",
                        display_name="MiMo V2.5",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                ],
            ),
            ProviderConfig(
                name="openrouter",
                display_name="OpenRouter",
                api_format="openai",
                base_url="https://openrouter.ai/api/v1",
                api_key="${OPENROUTER_API_KEY}",
                default_headers={
                    "HTTP-Referer": "https://github.com/geochem-agent",
                    "X-Title": "GeoChem Data Curation Agent",
                },
                models=[
                    ModelConfig(
                        name="meta-llama/llama-3.1-8b-instruct:free",
                        display_name="Llama 3.1 8B (Free)",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                    ModelConfig(
                        name="google/gemma-2-9b-it:free",
                        display_name="Gemma 2 9B (Free)",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                    ModelConfig(
                        name="mistralai/mistral-7b-instruct:free",
                        display_name="Mistral 7B (Free)",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                    ModelConfig(
                        name="qwen/qwen-2.5-7b-instruct:free",
                        display_name="Qwen 2.5 7B (Free)",
                        max_tokens=8192,
                        pricing=ModelPricing(input_price=0.0, output_price=0.0),
                    ),
                ],
            ),
            # --- Chinese LLM providers (all OpenAI-compatible) ---
            ProviderConfig(
                name="qwen",
                display_name="Alibaba Qwen (DashScope)",
                api_format="openai",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                models=[
                    ModelConfig(name="qwen-max", display_name="Qwen Max", max_tokens=8192),
                    ModelConfig(name="qwen-plus", display_name="Qwen Plus", max_tokens=8192),
                    ModelConfig(name="qwen-turbo", display_name="Qwen Turbo", max_tokens=8192),
                    ModelConfig(name="qwen-long", display_name="Qwen Long", max_tokens=10000),
                ],
            ),
            ProviderConfig(
                name="zhipu",
                display_name="Zhipu AI (GLM)",
                api_format="openai",
                base_url="https://open.bigmodel.cn/api/paas/v4/",
                models=[
                    ModelConfig(name="glm-4-plus", display_name="GLM-4 Plus", max_tokens=4096),
                    ModelConfig(name="glm-4-flash", display_name="GLM-4 Flash", max_tokens=4096),
                    ModelConfig(name="glm-4-long", display_name="GLM-4 Long", max_tokens=4096),
                ],
            ),
            ProviderConfig(
                name="minimax",
                display_name="MiniMax",
                api_format="openai",
                base_url="https://api.minimax.chat/v1",
                models=[
                    ModelConfig(name="MiniMax-Text-01", display_name="MiniMax Text 01", max_tokens=4096),
                ],
            ),
            ProviderConfig(
                name="moonshot",
                display_name="Moonshot (Kimi)",
                api_format="openai",
                base_url="https://api.moonshot.cn/v1",
                models=[
                    ModelConfig(name="moonshot-v1-8k", display_name="Moonshot V1 8K", max_tokens=4096),
                    ModelConfig(name="moonshot-v1-32k", display_name="Moonshot V1 32K", max_tokens=4096),
                    ModelConfig(name="moonshot-v1-128k", display_name="Moonshot V1 128K", max_tokens=4096),
                ],
            ),
            ProviderConfig(
                name="doubao",
                display_name="ByteDance Doubao",
                api_format="openai",
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                models=[
                    ModelConfig(name="doubao-pro-32k", display_name="Doubao Pro 32K", max_tokens=4096),
                    ModelConfig(name="doubao-lite-32k", display_name="Doubao Lite 32K", max_tokens=4096),
                ],
            ),
            ProviderConfig(
                name="baichuan",
                display_name="Baichuan AI",
                api_format="openai",
                base_url="https://api.baichuan-ai.com/v1",
                models=[
                    ModelConfig(name="Baichuan2-Turbo", display_name="Baichuan2 Turbo", max_tokens=4096),
                    ModelConfig(name="Baichuan2-Turbo-192k", display_name="Baichuan2 Turbo 192K", max_tokens=4096),
                ],
            ),
            ProviderConfig(
                name="hunyuan",
                display_name="Tencent Hunyuan",
                api_format="openai",
                base_url="https://api.hunyuan.cloud.tencent.com/v1",
                models=[
                    ModelConfig(name="hunyuan-turbos-latest", display_name="Hunyuan Turbo", max_tokens=4096),
                    ModelConfig(name="hunyuan-pro", display_name="Hunyuan Pro", max_tokens=4096),
                    ModelConfig(name="hunyuan-lite", display_name="Hunyuan Lite", max_tokens=4096),
                ],
            ),
            ProviderConfig(
                name="yi",
                display_name="01.AI Yi",
                api_format="openai",
                base_url="https://api.lingyiwanwu.com/v1",
                models=[
                    ModelConfig(name="yi-large", display_name="Yi Large", max_tokens=4096),
                    ModelConfig(name="yi-medium", display_name="Yi Medium", max_tokens=4096),
                ],
            ),
            ProviderConfig(
                name="stepfun",
                display_name="StepFun",
                api_format="openai",
                base_url="https://api.stepfun.com/v1",
                models=[
                    ModelConfig(name="step-1-8k", display_name="Step-1 8K", max_tokens=4096),
                    ModelConfig(name="step-1-32k", display_name="Step-1 32K", max_tokens=4096),
                ],
            ),
            ProviderConfig(
                name="ollama",
                display_name="Ollama (Local)",
                api_format="openai",
                base_url="http://localhost:11434/v1",
                api_key="ollama",
                models=[],
            ),
        ],
        task_models={
            "_default": TaskModelConfig(provider="anthropic", model="claude-sonnet-4-20250514"),
            "field_mapping": TaskModelConfig(provider="anthropic", model="claude-sonnet-4-20250514", temperature=0.1),
            "relevance_judge": TaskModelConfig(provider="openai", model="gpt-4o-mini", temperature=0.0),
            "unit_suggestion": TaskModelConfig(provider="deepseek-openai", model="deepseek-v4-flash", temperature=0.0),
            "data_extraction": TaskModelConfig(provider="anthropic", model="claude-sonnet-4-20250514", temperature=0.0, max_tokens=8192),
        },
        fallback_chain=[
            {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            {"provider": "openai", "model": "gpt-4o"},
            {"provider": "deepseek-openai", "model": "deepseek-v4-flash"},
        ],
    )
    return merge_provider_presets(config)


def load_config(config_path: Path | str | None = None) -> AppConfig:
    """Load configuration from YAML file, falling back to defaults."""
    config_path = _config_read_path(config_path)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data:
            return sanitize_config_secrets(merge_provider_presets(AppConfig(**data)))

    return sanitize_config_secrets(merge_provider_presets(_default_config()))


def save_config(config: AppConfig, config_path: Path | str | None = None) -> None:
    """Save configuration to YAML file."""
    config_path = _config_write_path(config_path)
    config = sanitize_config_secrets(config)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)
