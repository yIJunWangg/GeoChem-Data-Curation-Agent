"""Runtime settings for local development and server deployments."""

from __future__ import annotations

from enum import Enum
import os
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, Field, model_validator


class RuntimeProfile(str, Enum):
    """Supported deployment profiles."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class RuntimeSettings(BaseModel):
    """Process-level settings kept separate from user-editable LLM settings."""

    profile: RuntimeProfile = RuntimeProfile.DEVELOPMENT
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    open_browser: bool = True
    reload: bool = False
    log_level: str = "info"
    web_workers: int = Field(default=1, ge=1, le=8)
    forwarded_allow_ips: str = "127.0.0.1"
    enable_api_docs: bool = True
    build_id: str = ""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])

    database_url: str = ""
    postgres_schema_mode: str = "auto"
    checkpoint_database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    task_backend: str = "local"

    vector_enabled: bool = False
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "geochem_evidence_v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_batch_size: int = Field(default=8, ge=1, le=128)
    embedding_max_tokens: int = Field(default=512, ge=64, le=8192)
    embedding_cache_dir: Path | None = None

    auth_mode: str = "disabled"
    oidc_issuer_url: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = "geochem-web"
    oidc_audience: str = "geochem-api"
    oidc_scopes: str = "openid profile email"
    keycloak_internal_url: str = ""
    keycloak_realm: str = "geochem"
    keycloak_admin_client_id: str = "geochem-admin-api"
    keycloak_admin_client_secret: str = ""
    credential_master_key: str = ""

    storage_root: Path = Field(default_factory=lambda: Path("geochem-data"))
    export_root: Path | None = None
    max_upload_mb: int = Field(default=512, ge=1)
    request_rate_per_minute: int = Field(default=300, ge=10)
    chat_rate_per_minute: int = Field(default=40, ge=1)
    upload_rate_per_hour: int = Field(default=20, ge=1)
    max_concurrent_tasks_per_user: int = Field(default=3, ge=1, le=50)

    @model_validator(mode="after")
    def validate_server_profile(self) -> "RuntimeSettings":
        if self.profile != RuntimeProfile.DEVELOPMENT:
            missing: list[str] = []
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                missing.append("GEOCHEM_DATABASE_URL")
            if not self.redis_url.startswith(("redis://", "rediss://")):
                missing.append("GEOCHEM_REDIS_URL")
            if self.auth_mode != "oidc":
                missing.append("GEOCHEM_AUTH_MODE=oidc")
            if not self.oidc_issuer_url:
                missing.append("GEOCHEM_OIDC_ISSUER_URL")
            if missing:
                raise ValueError(
                    f"{self.profile.value} profile is missing production settings: "
                    + ", ".join(missing)
                )
            if self.postgres_schema_mode != "alembic":
                raise ValueError(
                    "Staging and production require GEOCHEM_POSTGRES_SCHEMA_MODE=alembic"
                )
        if self.postgres_schema_mode not in {"auto", "alembic"}:
            raise ValueError("GEOCHEM_POSTGRES_SCHEMA_MODE must be 'auto' or 'alembic'")
        if self.auth_mode not in {"disabled", "oidc"}:
            raise ValueError("GEOCHEM_AUTH_MODE must be 'disabled' or 'oidc'")
        if self.task_backend not in {"local", "celery"}:
            raise ValueError("GEOCHEM_TASK_BACKEND must be 'local' or 'celery'")
        if self.profile != RuntimeProfile.DEVELOPMENT and self.task_backend != "celery":
            raise ValueError("Staging and production must use GEOCHEM_TASK_BACKEND=celery")
        if self.auth_mode == "oidc" and "*" in self.cors_origins:
            raise ValueError("Wildcard CORS is not allowed when OIDC authentication is enabled")
        return self

    @property
    def effective_checkpoint_database_url(self) -> str:
        return self.checkpoint_database_url or self.database_url

    @property
    def effective_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def effective_celery_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def effective_export_root(self) -> Path:
        return self.export_root or self.storage_root / "exports"


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _secret_value(env: Mapping[str, str], name: str) -> str:
    """Read one process secret from an env value or a Docker secret file."""

    value = env.get(name, "").strip()
    if value:
        return value
    file_value = env.get(f"{name}_FILE", "").strip()
    if not file_value:
        return ""
    path = Path(file_value).expanduser()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_runtime_settings(environ: Mapping[str, str] | None = None) -> RuntimeSettings:
    """Load deployment settings from environment variables.

    LLM/provider settings remain in ``settings.local.yaml``. These values describe
    how the process itself is exposed and where shared infrastructure lives.
    """

    env = environ or os.environ
    profile = RuntimeProfile(env.get("GEOCHEM_PROFILE", "development").strip().lower())
    server_profile = profile != RuntimeProfile.DEVELOPMENT
    default_host = "0.0.0.0" if server_profile else "127.0.0.1"
    default_origins = [] if server_profile else ["http://127.0.0.1:5173", "http://localhost:5173"]
    default_hosts = ["geochem.lan", "localhost", "127.0.0.1"] if server_profile else ["127.0.0.1", "localhost", "testserver"]
    storage_root = Path(env.get("GEOCHEM_STORAGE_ROOT", "geochem-data")).expanduser()
    export_value = env.get("GEOCHEM_EXPORT_ROOT", "").strip()

    return RuntimeSettings(
        profile=profile,
        host=env.get("GEOCHEM_HOST", default_host),
        port=int(env.get("GEOCHEM_PORT", "8765")),
        open_browser=_bool(env.get("GEOCHEM_OPEN_BROWSER"), not server_profile),
        reload=_bool(env.get("GEOCHEM_RELOAD"), False),
        log_level=env.get("GEOCHEM_LOG_LEVEL", "info").lower(),
        web_workers=int(env.get("GEOCHEM_WEB_WORKERS", "2" if server_profile else "1")),
        forwarded_allow_ips=env.get("GEOCHEM_FORWARDED_ALLOW_IPS", "127.0.0.1").strip(),
        enable_api_docs=_bool(env.get("GEOCHEM_ENABLE_API_DOCS"), not server_profile),
        build_id=env.get("GEOCHEM_BUILD_ID", "").strip(),
        cors_origins=_list(env.get("GEOCHEM_CORS_ORIGINS"), default_origins),
        allowed_hosts=_list(env.get("GEOCHEM_ALLOWED_HOSTS"), default_hosts),
        database_url=env.get("GEOCHEM_DATABASE_URL", "").strip(),
        postgres_schema_mode=env.get(
            "GEOCHEM_POSTGRES_SCHEMA_MODE", "alembic" if server_profile else "auto"
        ).strip().lower(),
        checkpoint_database_url=env.get("GEOCHEM_CHECKPOINT_DATABASE_URL", "").strip(),
        redis_url=env.get("GEOCHEM_REDIS_URL", "redis://127.0.0.1:6379/0").strip(),
        celery_broker_url=env.get("GEOCHEM_CELERY_BROKER_URL", "").strip(),
        celery_result_backend=env.get("GEOCHEM_CELERY_RESULT_BACKEND", "").strip(),
        task_backend=env.get(
            "GEOCHEM_TASK_BACKEND", "celery" if server_profile else "local"
        ).strip().lower(),
        vector_enabled=_bool(env.get("GEOCHEM_VECTOR_ENABLED"), False),
        qdrant_url=env.get("GEOCHEM_QDRANT_URL", "http://127.0.0.1:6333").strip(),
        qdrant_api_key=_secret_value(env, "GEOCHEM_QDRANT_API_KEY"),
        qdrant_collection=env.get("GEOCHEM_QDRANT_COLLECTION", "geochem_evidence_v1").strip(),
        embedding_model=env.get("GEOCHEM_EMBEDDING_MODEL", "BAAI/bge-m3").strip(),
        embedding_batch_size=int(env.get("GEOCHEM_EMBEDDING_BATCH_SIZE", "8")),
        embedding_max_tokens=int(env.get("GEOCHEM_EMBEDDING_MAX_TOKENS", "512")),
        embedding_cache_dir=(
            Path(env["GEOCHEM_EMBEDDING_CACHE_DIR"]).expanduser()
            if env.get("GEOCHEM_EMBEDDING_CACHE_DIR", "").strip()
            else None
        ),
        auth_mode=env.get("GEOCHEM_AUTH_MODE", "oidc" if server_profile else "disabled").strip().lower(),
        oidc_issuer_url=env.get("GEOCHEM_OIDC_ISSUER_URL", "").strip(),
        oidc_jwks_url=env.get("GEOCHEM_OIDC_JWKS_URL", "").strip(),
        oidc_client_id=env.get("GEOCHEM_OIDC_CLIENT_ID", "geochem-web").strip(),
        oidc_audience=env.get("GEOCHEM_OIDC_AUDIENCE", "geochem-api").strip(),
        oidc_scopes=env.get("GEOCHEM_OIDC_SCOPES", "openid profile email").strip(),
        keycloak_internal_url=env.get("GEOCHEM_KEYCLOAK_INTERNAL_URL", "").strip(),
        keycloak_realm=env.get("GEOCHEM_KEYCLOAK_REALM", "geochem").strip(),
        keycloak_admin_client_id=env.get(
            "GEOCHEM_KEYCLOAK_ADMIN_CLIENT_ID", "geochem-admin-api"
        ).strip(),
        keycloak_admin_client_secret=env.get(
            "GEOCHEM_KEYCLOAK_ADMIN_CLIENT_SECRET", ""
        ).strip(),
        credential_master_key=_secret_value(env, "GEOCHEM_CREDENTIAL_MASTER_KEY"),
        storage_root=storage_root,
        export_root=Path(export_value).expanduser() if export_value else None,
        max_upload_mb=int(env.get("GEOCHEM_MAX_UPLOAD_MB", "512")),
        request_rate_per_minute=int(env.get("GEOCHEM_REQUEST_RATE_PER_MINUTE", "300")),
        chat_rate_per_minute=int(env.get("GEOCHEM_CHAT_RATE_PER_MINUTE", "40")),
        upload_rate_per_hour=int(env.get("GEOCHEM_UPLOAD_RATE_PER_HOUR", "20")),
        max_concurrent_tasks_per_user=int(env.get("GEOCHEM_MAX_CONCURRENT_TASKS_PER_USER", "3")),
    )
