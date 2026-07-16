"""Tests for configuration management."""

import pytest
import yaml

from geochem.core.config import AppConfig, load_config, save_config, ModelPricing, ProviderConfig, _default_config


def test_default_config():
    config = _default_config()
    assert len(config.providers) >= 3
    assert "_default" in config.task_models


def test_get_provider():
    config = _default_config()
    prov = config.get_provider("anthropic")
    assert prov is not None
    assert prov.name == "anthropic"

    assert config.get_provider("nonexistent") is None


def test_get_task_model():
    config = _default_config()
    tm = config.get_task_model("field_mapping")
    assert tm is not None
    assert tm.provider == "anthropic"

    # Fallback to default
    tm2 = config.get_task_model("unknown_task")
    assert tm2 is not None
    assert tm2.provider == "anthropic"


def test_save_and_load_config(tmp_path):
    config_path = tmp_path / "settings.yaml"
    config = _default_config()
    config.log_level = "DEBUG"
    save_config(config, config_path)

    loaded = load_config(config_path)
    assert loaded.log_level == "DEBUG"
    assert len(loaded.providers) >= 3


def test_load_nonexistent_config():
    config = load_config("/nonexistent/path/settings.yaml")
    # Should fall back to defaults
    assert len(config.providers) >= 3


def test_save_config_sanitizes_plaintext_provider_secret(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    monkeypatch.delenv("CUSTOM_PROVIDER_API_KEY", raising=False)
    config = AppConfig(providers=[
        ProviderConfig(name="custom-provider", api_format="openai", api_key="sk-realistic-test-secret-123456")
    ])

    save_config(config, config_path)
    saved = config_path.read_text(encoding="utf-8")
    loaded = load_config(config_path)

    assert "sk-realistic-test-secret-123456" not in saved
    assert "${CUSTOM_PROVIDER_API_KEY}" in saved
    assert loaded.get_provider("custom-provider").api_key == "${CUSTOM_PROVIDER_API_KEY}"


def test_model_pricing():
    pricing = ModelPricing(input_price=0.003, output_price=0.015)
    assert pricing.input_price == 0.003
    assert pricing.output_price == 0.015


def test_provider_config_models():
    config = _default_config()
    anthropic = config.get_provider("anthropic")
    assert len(anthropic.models) == 2
    model_names = {m.name for m in anthropic.models}
    assert "claude-sonnet-4-20250514" in model_names
    assert "claude-haiku-4-5-20251001" in model_names
