"""Administrator-managed model grants used by authenticated execution."""

from __future__ import annotations

import pytest

from geochem.core.config import AppConfig, TaskModelConfig
from geochem.core.models import LLMResponse
from geochem.core.project import ProjectManager
from geochem.core.runtime import RuntimeSettings
from geochem.providers.llm_client import LLMClient
from geochem.providers.openai_provider import OpenAIProvider
from geochem.services.admin_governance import AdminGovernanceService
from geochem.services.execution_context import user_execution_context
from geochem.services.model_access import (
    record_user_model_usage,
    resolve_user_model_grant,
)


def _managed_model(tmp_path, token_limit: int = 1000):
    manager = ProjectManager(tmp_path / "workspaces")
    manager.ensure_default_workspace()
    runtime = RuntimeSettings(auth_mode="oidc", credential_master_key="22" * 32)
    governance = AdminGovernanceService(manager, runtime)
    governance.ensure_user_profile("curator-1", "curator.one")
    credential = governance.create_credential(
        {
            "name": "OpenCode shared model",
            "provider": "opencode-go-openai",
            "api_format": "openai",
            "base_url": "https://models.example.test/v1",
            "model_id": "deepseek-v4-flash",
            "api_key": "sk-encrypted-test-secret",
            "enabled": True,
        },
        "admin-1",
    )
    governance.set_model_allocation(
        "curator-1",
        credential["credential_id"],
        True,
        token_limit,
        0.0,
        "admin-1",
    )
    return manager, runtime, credential


def test_managed_grant_decrypts_and_enforces_monthly_tokens(tmp_path):
    manager, runtime, credential = _managed_model(tmp_path, token_limit=10)
    db = manager.get_default_database()
    try:
        grant = resolve_user_model_grant(db, runtime, "curator-1")
        assert grant.credential_id == credential["credential_id"]
        assert grant.api_key == "sk-encrypted-test-secret"
        assert grant.model_id == "deepseek-v4-flash"

        record_user_model_usage(db, "curator-1", grant, 4, 6, 10)
        with pytest.raises(ValueError, match="Token 配额已用完"):
            resolve_user_model_grant(db, runtime, "curator-1")
    finally:
        db.close()


def test_llm_client_uses_only_the_authenticated_users_grant(tmp_path, monkeypatch):
    manager, runtime, credential = _managed_model(tmp_path)
    monkeypatch.setattr("geochem.providers.llm_client.load_runtime_settings", lambda: runtime)
    calls: list[dict[str, object]] = []

    def fake_completion(self, messages, model, temperature=0.1, max_tokens=4096, **kwargs):
        calls.append(
            {
                "api_key": self.api_key,
                "base_url": self.base_url,
                "model": model,
                "messages": messages,
            }
        )
        return LLMResponse(
            content="已完成",
            final_content="已完成",
            provider="openai",
            model=model,
            input_tokens=7,
            output_tokens=3,
            total_tokens=10,
        )

    monkeypatch.setattr(OpenAIProvider, "chat_completion", fake_completion)
    config = AppConfig(
        task_models={
            "_default": TaskModelConfig(provider="unused-global", model="unused-model")
        },
        fallback_chain=[{"provider": "must-not-run", "model": "fallback-model"}],
    )
    db = manager.get_default_database()
    try:
        with user_execution_context("curator-1"):
            response = LLMClient(config, db=db).chat(
                [{"role": "user", "content": "test"}],
                use_cache=False,
            )
        assert response.provider == "opencode-go-openai"
        assert response.model == "deepseek-v4-flash"
        assert calls == [
            {
                "api_key": "sk-encrypted-test-secret",
                "base_url": "https://models.example.test/v1",
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": "test"}],
            }
        ]
        usage = db.fetch_one(
            "SELECT total_tokens FROM user_ai_usage_monthly WHERE user_id=? AND credential_id=?",
            ("curator-1", credential["credential_id"]),
        )
        assert int(usage["total_tokens"]) == 10
    finally:
        db.close()


def test_oidc_model_call_without_allocation_is_rejected(tmp_path, monkeypatch):
    manager, runtime, _credential = _managed_model(tmp_path)
    monkeypatch.setattr("geochem.providers.llm_client.load_runtime_settings", lambda: runtime)
    config = AppConfig(
        task_models={"_default": TaskModelConfig(provider="openai", model="model")}
    )
    db = manager.get_default_database()
    try:
        with user_execution_context("viewer-without-grant"):
            with pytest.raises(Exception, match="管理员尚未为当前账号分配"):
                LLMClient(config, db=db).chat(
                    [{"role": "user", "content": "test"}],
                    use_cache=False,
                )
    finally:
        db.close()
