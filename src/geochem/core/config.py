"""Configuration management for GeoChem."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


DEFAULT_PROJECT_DIR = Path.home() / "GeoChem_Projects"


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


def _default_config() -> AppConfig:
    """Create default configuration with common providers."""
    return AppConfig(
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
            "unit_suggestion": TaskModelConfig(provider="deepseek", model="deepseek-chat", temperature=0.0),
            "data_extraction": TaskModelConfig(provider="anthropic", model="claude-sonnet-4-20250514", temperature=0.0, max_tokens=8192),
        },
        fallback_chain=[
            {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            {"provider": "openai", "model": "gpt-4o"},
            {"provider": "deepseek", "model": "deepseek-chat"},
        ],
    )


def load_config(config_path: Path | str | None = None) -> AppConfig:
    """Load configuration from YAML file, falling back to defaults."""
    if config_path is None:
        config_path = Path(os.environ.get("GEOCHEM_CONFIG", "config/settings.yaml"))

    config_path = Path(config_path)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data:
            return AppConfig(**data)

    return _default_config()


def save_config(config: AppConfig, config_path: Path | str) -> None:
    """Save configuration to YAML file."""
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)
