"""Administrative policy and encrypted credential tests."""

from datetime import datetime

import pytest

from geochem.core.project import ProjectManager
from geochem.core.runtime import RuntimeSettings
from geochem.services.admin_governance import (
    AdminGovernanceService,
    DEFAULT_STORAGE_QUOTA_BYTES,
)


def _service(tmp_path):
    manager = ProjectManager(tmp_path / "workspaces")
    manager.ensure_default_workspace()
    runtime = RuntimeSettings(credential_master_key="11" * 32)
    return AdminGovernanceService(manager, runtime), manager


def test_model_credential_is_encrypted_and_never_returned(tmp_path):
    service, manager = _service(tmp_path)
    secret = "sk-test-secret-that-must-not-be-returned"

    created = service.create_credential(
        {
            "name": "Shared mapping model",
            "provider": "openai-compatible",
            "api_format": "openai",
            "base_url": "https://models.example.test/v1",
            "model_id": "mapping-model",
            "api_key": secret,
            "enabled": True,
        },
        "admin-user",
    )

    assert created["secret_configured"] is True
    assert created["key_fingerprint"]
    assert secret not in repr(created)
    db = manager.get_default_database()
    try:
        row = db.fetch_one(
            "SELECT encrypted_secret, secret_nonce FROM model_credentials WHERE credential_id = ?",
            (created["credential_id"],),
        )
        assert row["encrypted_secret"]
        assert row["secret_nonce"]
        assert secret not in row["encrypted_secret"]
        assert service.vault.decrypt(row["encrypted_secret"], row["secret_nonce"]) == secret
    finally:
        db.close()


def test_user_policy_defaults_and_admin_allocations(tmp_path):
    service, _manager = _service(tmp_path)
    service.ensure_user_profile("user-1", "curator.one", "one@example.test")
    policy = service.user_policy("user-1")
    assert policy["storage"]["quota_bytes"] == DEFAULT_STORAGE_QUOTA_BYTES
    assert policy["model_allocations"] == []

    credential = service.create_credential(
        {
            "name": "Extraction model",
            "provider": "anthropic-compatible",
            "api_format": "anthropic",
            "base_url": "https://models.example.test",
            "model_id": "extractor",
            "secret_ref": "EXTRACTION_API_KEY",
            "enabled": True,
        },
        "admin-user",
    )
    service.set_storage_quota("user-1", 25 * 1024**3, "admin-user")
    updated = service.set_model_allocation(
        "user-1",
        credential["credential_id"],
        True,
        2_000_000,
        20.5,
        "admin-user",
    )

    assert updated["storage"]["quota_bytes"] == 25 * 1024**3
    assert len(updated["model_allocations"]) == 1
    assert updated["model_allocations"][0]["monthly_token_limit"] == 2_000_000
    assert updated["model_allocations"][0]["monthly_cost_limit"] == 20.5


def test_credential_creation_requires_encrypted_secret_or_reference(tmp_path):
    service, _manager = _service(tmp_path)

    try:
        service.create_credential(
            {
                "name": "Missing key",
                "provider": "openai-compatible",
                "model_id": "model",
            },
            "admin-user",
        )
    except ValueError as exc:
        assert "API Key" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("credential without a secret must be rejected")


def test_storage_usage_is_reconciled_from_owned_article_directory(tmp_path):
    service, manager = _service(tmp_path)
    config, project_dir = manager.ensure_default_workspace()
    service.ensure_user_profile("curator-1", "curator.one")
    article_relative = "articles/quota-test"
    article_dir = project_dir / article_relative
    article_dir.mkdir(parents=True)
    (article_dir / "source.pdf").write_bytes(b"x" * 128)

    db = manager.get_default_database()
    try:
        db.execute(
            """INSERT INTO articles
               (article_id, project_id, title, article_dir, owner_user_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'imported', ?)""",
            (
                "ART_QUOTA",
                config.project_id,
                "Quota test",
                article_relative,
                "curator-1",
                datetime.now().isoformat(),
            ),
        )
        db.commit()
    finally:
        db.close()

    assert service.refresh_storage_usage("curator-1") == 128
    service.set_storage_quota("curator-1", 130, "admin-user")
    with pytest.raises(ValueError, match="存储配额不足"):
        service.ensure_storage_available("curator-1", 3)


def test_register_storage_object_assigns_new_article_owner(tmp_path):
    service, manager = _service(tmp_path)
    config, project_dir = manager.ensure_default_workspace()
    relative_path = "articles/owned/source/paper.pdf"
    target = project_dir / relative_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"paper")

    db = manager.get_default_database()
    try:
        db.execute(
            """INSERT INTO articles
               (article_id, project_id, title, article_dir, status, created_at)
               VALUES (?, ?, ?, ?, 'imported', ?)""",
            (
                "ART_OWNED",
                config.project_id,
                "Owned article",
                "articles/owned",
                datetime.now().isoformat(),
            ),
        )
        db.commit()
    finally:
        db.close()

    service.register_storage_object(
        "curator-2",
        config.project_id,
        "ART_OWNED",
        "article_source",
        relative_path,
        5,
        "checksum",
    )

    db = manager.get_default_database()
    try:
        article = db.fetch_one(
            "SELECT owner_user_id FROM articles WHERE article_id = ?",
            ("ART_OWNED",),
        )
        assert article["owner_user_id"] == "curator-2"
    finally:
        db.close()
    assert service.user_policy("curator-2")["storage"]["used_bytes"] == 5
