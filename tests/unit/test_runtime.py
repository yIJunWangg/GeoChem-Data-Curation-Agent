"""Tests for process-level deployment settings."""

import pytest

from geochem.core.runtime import RuntimeProfile, load_runtime_settings


def test_development_defaults_preserve_local_preview():
    settings = load_runtime_settings({})

    assert settings.profile == RuntimeProfile.DEVELOPMENT
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.open_browser is True
    assert settings.web_workers == 1
    assert settings.forwarded_allow_ips == "127.0.0.1"
    assert settings.enable_api_docs is True
    assert settings.auth_mode == "disabled"
    assert "http://127.0.0.1:5173" in settings.cors_origins


def test_development_environment_overrides():
    settings = load_runtime_settings({
        "GEOCHEM_PROFILE": "development",
        "GEOCHEM_PORT": "8877",
        "GEOCHEM_OPEN_BROWSER": "false",
        "GEOCHEM_CORS_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000",
        "GEOCHEM_STORAGE_ROOT": "/tmp/geochem-test-data",
        "GEOCHEM_BUILD_ID": "preview-a1b2c3",
    })

    assert settings.port == 8877
    assert settings.open_browser is False
    assert len(settings.cors_origins) == 2
    assert str(settings.storage_root) == "/tmp/geochem-test-data"
    assert settings.build_id == "preview-a1b2c3"


def test_vector_environment_overrides(tmp_path):
    cache_dir = tmp_path / "models"
    settings = load_runtime_settings({
        "GEOCHEM_VECTOR_ENABLED": "true",
        "GEOCHEM_QDRANT_URL": "http://qdrant.test:6333",
        "GEOCHEM_QDRANT_API_KEY": "vector-secret",
        "GEOCHEM_QDRANT_COLLECTION": "evidence_test",
        "GEOCHEM_EMBEDDING_MODEL": "BAAI/bge-m3-test",
        "GEOCHEM_EMBEDDING_BATCH_SIZE": "4",
        "GEOCHEM_EMBEDDING_MAX_TOKENS": "768",
        "GEOCHEM_EMBEDDING_CACHE_DIR": str(cache_dir),
    })

    assert settings.vector_enabled is True
    assert settings.qdrant_url == "http://qdrant.test:6333"
    assert settings.qdrant_api_key == "vector-secret"
    assert settings.qdrant_collection == "evidence_test"
    assert settings.embedding_model == "BAAI/bge-m3-test"
    assert settings.embedding_batch_size == 4
    assert settings.embedding_max_tokens == 768
    assert settings.embedding_cache_dir == cache_dir


def test_production_requires_shared_infrastructure_and_oidc():
    with pytest.raises(ValueError, match="GEOCHEM_DATABASE_URL"):
        load_runtime_settings({"GEOCHEM_PROFILE": "production"})


def test_production_profile_accepts_managed_services():
    settings = load_runtime_settings({
        "GEOCHEM_PROFILE": "production",
        "GEOCHEM_DATABASE_URL": "postgresql+psycopg://geochem:secret@postgres/geochem",
        "GEOCHEM_REDIS_URL": "redis://redis:6379/0",
        "GEOCHEM_AUTH_MODE": "oidc",
        "GEOCHEM_OIDC_ISSUER_URL": "https://auth.geochem.lan/realms/geochem",
        "GEOCHEM_CORS_ORIGINS": "https://geochem.lan",
        "GEOCHEM_ALLOWED_HOSTS": "geochem.lan",
    })

    assert settings.profile == RuntimeProfile.PRODUCTION
    assert settings.host == "0.0.0.0"
    assert settings.open_browser is False
    assert settings.web_workers == 2
    assert settings.forwarded_allow_ips == "127.0.0.1"
    assert settings.enable_api_docs is False
    assert settings.postgres_schema_mode == "alembic"
    assert settings.effective_checkpoint_database_url == settings.database_url


def test_production_rejects_runtime_postgres_ddl():
    with pytest.raises(ValueError, match="POSTGRES_SCHEMA_MODE=alembic"):
        load_runtime_settings({
            "GEOCHEM_PROFILE": "production",
            "GEOCHEM_DATABASE_URL": "postgresql://geochem:secret@postgres/geochem",
            "GEOCHEM_POSTGRES_SCHEMA_MODE": "auto",
            "GEOCHEM_REDIS_URL": "redis://redis:6379/0",
            "GEOCHEM_AUTH_MODE": "oidc",
            "GEOCHEM_OIDC_ISSUER_URL": "https://auth.geochem.lan/realms/geochem",
        })


def test_oidc_rejects_wildcard_cors():
    with pytest.raises(ValueError, match="Wildcard CORS"):
        load_runtime_settings({
            "GEOCHEM_PROFILE": "staging",
            "GEOCHEM_DATABASE_URL": "postgresql://geochem:secret@postgres/geochem",
            "GEOCHEM_REDIS_URL": "redis://redis:6379/0",
            "GEOCHEM_AUTH_MODE": "oidc",
            "GEOCHEM_OIDC_ISSUER_URL": "https://auth.geochem.lan/realms/geochem",
            "GEOCHEM_CORS_ORIGINS": "*",
        })


def test_server_proxy_and_api_docs_can_be_explicitly_configured():
    settings = load_runtime_settings({
        "GEOCHEM_PROFILE": "staging",
        "GEOCHEM_DATABASE_URL": "postgresql://geochem:secret@postgres/geochem",
        "GEOCHEM_REDIS_URL": "redis://redis:6379/0",
        "GEOCHEM_AUTH_MODE": "oidc",
        "GEOCHEM_OIDC_ISSUER_URL": "https://auth.geochem.lan/realms/geochem",
        "GEOCHEM_FORWARDED_ALLOW_IPS": "*",
        "GEOCHEM_ENABLE_API_DOCS": "true",
    })

    assert settings.forwarded_allow_ips == "*"
    assert settings.enable_api_docs is True
