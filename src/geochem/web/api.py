"""FastAPI application exposing the GeoChem local workspace."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..core.project import ProjectManager
from ..core.runtime import RuntimeSettings, load_runtime_settings
from ..core.config import ModelConfig, ModelPricing, ProviderConfig, TaskModelConfig, load_config, provider_presets, save_config
from ..core.secrets import parse_secret_ref, resolve_secret, secret_ref_for_provider, store_secret_for_provider
from ..curation.header_config_importer import HeaderConfigImporter
from ..curation.target_headers import TargetHeaderBuilder
from ..ingestion.file_importer import FileImporter
from ..ingestion.literature_search import LiteratureSearchService
from ..ingestion.open_access_resolver import OpenAccessResolver
from ..mapping_knowledge import MappingKnowledgeService
from ..workflow import WorkflowRunner
from ..workbench_service import WorkbenchService
from ..agent_service import ArticleCurationAgent, RetrievalService
from ..agent_tools import EntitySelectionInput
from ..background_tasks import TaskDispatcher
from ..services.admin_governance import AdminGovernanceService
from ..services.rule_governance import RuleGovernanceService
from ..services.workspace_access import WorkspaceAccessService
from .auth import ApiAuditLogger, AuthenticationMiddleware, GEOCHEM_ROLES
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .rate_limit import ApiRateLimiter
from .upload_security import (
    ARTICLE_EXTENSIONS,
    HEADER_EXTENSIONS,
    PDF_EXTENSIONS,
    validate_uploaded_file,
)


UPLOAD_CHUNK_BYTES = 1024 * 1024


def _safe_upload_name(filename: str | None, fallback: str) -> str:
    """Keep a human-readable basename without accepting a client-side path."""

    candidate = Path(filename or fallback).name or fallback
    cleaned = "".join(
        character if character.isalnum() or character in {".", "-", "_", " "} else "_"
        for character in candidate
    ).strip(" .")
    return (cleaned or fallback)[:180]


def _temporary_upload_path(filename: str | None, fallback: str) -> Path:
    suffix = Path(_safe_upload_name(filename, fallback)).suffix[:16]
    descriptor, name = tempfile.mkstemp(prefix="geochem-upload-", suffix=suffix)
    os.close(descriptor)
    return Path(name)


async def _persist_upload(
    upload: UploadFile,
    destination: Path,
    max_upload_mb: int,
) -> int:
    """Stream one upload to disk with a strict process-level size limit."""

    maximum = max_upload_mb * 1024 * 1024
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > maximum:
                    raise HTTPException(
                        status_code=413,
                        detail=f"上传文件超过服务器限制（{max_upload_mb} MB）。",
                    )
                handle.write(chunk)
        return written
    except Exception:
        destination.unlink(missing_ok=True)
        raise


class SelectionRequest(BaseModel):
    element_ids: list[str] = Field(default_factory=list)


class ManualElementRequest(BaseModel):
    resource_id: str
    page_number: int
    bbox: list[float]
    element_type: str = "paragraph"
    note: str = ""


class ElementUpdateRequest(BaseModel):
    text_content: str | None = None
    bbox: list[float] | None = None
    element_type: str | None = None
    caption: str | None = None
    raw_table: dict[str, Any] | None = None


class ElementTeachRequest(BaseModel):
    label: str
    note: str = ""


class PreExtractionRuleRequest(BaseModel):
    source_alias: str = ""
    pattern: str = ""
    target_header: str = ""
    target_field: str = ""
    target_unit: str = ""
    rule_type: str = "source_alias"
    source_type: str = "table_header"
    evidence: str = ""
    conditions: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.9
    scope: str = "article"
    enabled: bool = True
    element_id: str = ""


class RuleMemoryPatchRequest(BaseModel):
    enabled: bool | None = None
    scope: str | None = None
    target_header: str | None = None
    target_field: str | None = None
    target_unit: str | None = None
    pattern: str | None = None
    rule_type: str | None = None
    source_type: str | None = None
    evidence: str | None = None
    conditions: dict[str, Any] | None = None


class RuleSubmissionRequest(BaseModel):
    source_term: str
    target_canonical_field: str
    target_header: str = ""
    source_unit: str = ""
    target_unit: str = ""
    chemical_form: str = ""
    context: str = ""
    conversion_formula: str = ""
    evidence: str = ""
    notes: str = ""
    scope: Literal["article", "project", "organization"] = "organization"
    article_id: str = ""
    confidence: float = 0.95
    supersedes_rule_id: str = ""


class RuleSubmissionPatchRequest(BaseModel):
    source_term: str | None = None
    target_canonical_field: str | None = None
    target_header: str | None = None
    source_unit: str | None = None
    target_unit: str | None = None
    chemical_form: str | None = None
    context: str | None = None
    conversion_formula: str | None = None
    evidence: str | None = None
    notes: str | None = None
    scope: Literal["article", "project", "organization"] | None = None
    article_id: str | None = None
    confidence: float | None = None
    supersedes_rule_id: str | None = None


class RuleReviewRequest(BaseModel):
    comment: str = ""


class ElementHitTestRequest(BaseModel):
    resource_id: str
    page_number: int
    bbox: list[float]


class ElementMergeSelectionRequest(BaseModel):
    page_number: int
    bbox: list[float]


class ExtractionRequest(BaseModel):
    project_id: str
    article_id: str
    use_llm: bool = True


class TableStandardizeRequest(BaseModel):
    project_id: str
    article_id: str
    use_llm: bool = False


class StandardTableUpdateRequest(BaseModel):
    project_id: str
    headers: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    edit_reason: str = ""


class RestandardizeTableRequest(BaseModel):
    project_id: str
    mode: str = "source_headers"


class TableRuleConfirmRequest(BaseModel):
    project_id: str
    rules: list[dict[str, Any]] = Field(default_factory=list)
    scope: str = "article"


class TableRuleAssistRequest(BaseModel):
    project_id: str
    source_headers: list[str] = Field(default_factory=list)


class ParagraphExtractionRequest(BaseModel):
    project_id: str
    element_ids: list[str] = Field(default_factory=list)
    use_llm: bool = True


class ReapplyRulesRequest(BaseModel):
    project_id: str


class CellUpdateRequest(BaseModel):
    value: str | None = None
    target_header: str | None = None
    target_field: str | None = None
    target_unit: str | None = None
    mapping_status: str | None = None
    review_status: str | None = None
    risk_level: str | None = None


class MergeRequest(BaseModel):
    project_id: str
    record_ids: list[str]


class ManualCandidateRecordRequest(BaseModel):
    project_id: str
    article_id: str
    batch_id: str = ""
    sample_id: str = ""
    values: dict[str, Any] = Field(default_factory=dict)


class CellConfirmRequest(BaseModel):
    target_field: str | None = None
    target_unit: str | None = None
    formula: str | None = None
    conversion_factor: float | None = None


class BatchApproveRequest(BaseModel):
    project_id: str
    record_ids: list[str]


class ManualFillRequest(BaseModel):
    resource_id: str
    page_number: int
    bbox: list[float]
    target_header: str
    value: str
    unit: str = ""
    explanation: str = ""


class ManualImageCellRequest(BaseModel):
    project_id: str
    candidate_record_id: str
    element_id: str
    target_header: str
    value: str
    evidence_note: str = ""


class FinalizeRequest(BaseModel):
    project_id: str
    article_id: str


class BatchConfirmRequest(BaseModel):
    project_id: str
    article_id: str
    confirmations: list[dict[str, Any]]


class HeaderAssignmentRequest(BaseModel):
    project_id: str
    config_id: str


class ImportSourceRequest(BaseModel):
    project_id: str
    source: str


class ConfirmAccessRequest(BaseModel):
    project_id: str
    article_id: str
    url: str


class ProviderTestRequest(BaseModel):
    api_key: str = ""
    provider: str = ""
    model: str = ""
    display_name: str = ""
    api_format: str = "openai"
    base_url: str = ""
    api_key_ref: str = ""
    default_headers: dict[str, str] = Field(default_factory=dict)
    auth_type: str = "bearer"
    api_key_header: str = "Authorization"


class ModelSetupRequest(BaseModel):
    provider_preset: str
    model_id: str
    api_key: str = ""
    base_url: str = ""


class ProviderModelRequest(BaseModel):
    name: str
    display_name: str = ""
    max_tokens: int = 4096
    supports_vision: bool = False

class SettingsUpdateRequest(BaseModel):
    default_provider: str
    default_model: str
    vision_provider: str = ""
    vision_model: str = ""
    api_keys: dict[str, str] = Field(default_factory=dict)
    task_models: dict[str, dict[str, Any]] = Field(default_factory=dict)
    providers: list[dict[str, Any]] = Field(default_factory=list)


class ExportDirectoryRequest(BaseModel):
    path: str = ""


class ExportArticleRequest(BaseModel):
    project_id: str
    format: str = "csv"
    output_dir: str = ""


class ChatThreadRequest(BaseModel):
    project_id: str
    article_id: str = ""
    title: str = ""
    scope: str = "article"


class ChatMessageRequest(BaseModel):
    project_id: str
    article_id: str = ""
    content: str


class ChatEntitySelectionRequest(BaseModel):
    project_id: str
    entity_type: str
    entity_id: str = ""
    query: str = ""


class ChatActionRequest(BaseModel):
    project_id: str
    action: str
    content: str = ""
    article_id: str = ""
    run_id: str = ""
    entity_type: str = ""
    entity_id: str = ""
    query: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRunRequest(BaseModel):
    project_id: str
    thread_id: str
    article_id: str = ""
    message: str


class AgentResumeRequest(BaseModel):
    project_id: str
    response: dict[str, Any] = Field(default_factory=dict)


class ArticleSourceSearchRequest(BaseModel):
    project_id: str
    source: str


class LiteratureSearchRequest(BaseModel):
    project_id: str = "DEFAULT_WORKSPACE"
    thread_id: str = ""
    query: str
    limit: int = 8
    sort_mode: str = "relevance"


class LiteratureImportRequest(BaseModel):
    project_id: str
    thread_id: str = ""


class RagQueryRequest(BaseModel):
    project_id: str
    article_id: str
    question: str
    mode: str = "answer"
    field: str = ""
    operation: str = "summary"


class ArticleSourceConfirmRequest(BaseModel):
    project_id: str


class AgentHandoffRequest(BaseModel):
    project_id: str
    presentation: Literal["inline", "full"] = "full"
    workbench_view: Literal["resources", "extract", "quality"] | None = None
    workbench_stage: str | None = None


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    enabled: bool = True
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    password: str = ""
    temporary_password: bool = True


class AdminUserUpdateRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    enabled: bool | None = None


class AdminRoleUpdateRequest(BaseModel):
    roles: list[str] = Field(default_factory=list)


class AdminPasswordResetRequest(BaseModel):
    password: str = Field(min_length=10, max_length=256)
    temporary: bool = True


class AdminCredentialRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    provider: str = Field(min_length=1, max_length=80)
    api_format: str = "openai"
    base_url: str = ""
    model_id: str = Field(min_length=1, max_length=160)
    api_key: str = ""
    secret_ref: str = ""
    enabled: bool = True


class AdminCredentialUpdateRequest(BaseModel):
    name: str | None = None
    provider: str | None = None
    api_format: str | None = None
    base_url: str | None = None
    model_id: str | None = None
    api_key: str | None = None
    secret_ref: str | None = None
    enabled: bool | None = None


class AdminModelAllocationRequest(BaseModel):
    credential_id: str
    enabled: bool = True
    monthly_token_limit: int = Field(default=0, ge=0)
    monthly_cost_limit: float = Field(default=0.0, ge=0)


class AdminStorageQuotaRequest(BaseModel):
    quota_bytes: int = Field(ge=0)


class WorkspaceMemberRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=160)
    role: str = Field(default="viewer", pattern="^(owner|curator|reviewer|viewer)$")
    status: str = Field(default="active", pattern="^(active|disabled)$")


class WorkspaceMemberPatchRequest(BaseModel):
    role: str | None = Field(default=None, pattern="^(owner|curator|reviewer|viewer)$")
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class WebRepository:
    """Read models needed by the React pages without importing Qt."""

    def __init__(self, pm: ProjectManager):
        self.pm = pm

    def workspace(self) -> dict[str, Any]:
        config, path = self.pm.ensure_default_workspace()
        return {**config.model_dump(), "path": str(path)}

    def articles(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT a.*,
                    (SELECT COUNT(*) FROM resources r WHERE r.article_id = a.article_id) AS resource_count,
                    (SELECT COUNT(*) FROM document_elements e WHERE e.article_id = a.article_id) AS element_count,
                    (SELECT config_id FROM article_header_assignments h WHERE h.article_id = a.article_id AND h.status='confirmed' ORDER BY h.created_at DESC LIMIT 1) AS header_config_id
                   FROM articles a WHERE a.project_id = ? ORDER BY a.created_at DESC""",
                (project_id,),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def header_configs(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all("SELECT * FROM header_configs WHERE project_id = ? AND status != 'deleted' ORDER BY updated_at DESC", (project_id,))
            result = []
            for row in rows:
                data = dict(row)
                data["headers"] = json.loads(data.pop("headers_json") or "[]")
                data["field_count"] = len(data["headers"])
                result.append(data)
            return result
        finally:
            db.close()

    def dashboard(self, project_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            def count(table: str, where: str = "", params: tuple = ()) -> int:
                sql = f"SELECT COUNT(*) AS n FROM {table}" + (f" WHERE {where}" if where else "")
                row = db.fetch_one(sql, params)
                return int(row["n"] or 0)
            article_ids = "SELECT article_id FROM articles WHERE project_id = ?"
            return {
                "articles": count("articles", "project_id = ?", (project_id,)),
                "headers": count("header_configs", "project_id = ? AND status != 'deleted'", (project_id,)),
                "elements": count("document_elements", f"article_id IN ({article_ids})", (project_id,)),
                "reviews": count("review_items", f"article_id IN ({article_ids}) AND status = 'pending'", (project_id,)),
                "records": count("standardized_records", f"article_id IN ({article_ids})", (project_id,)),
                "rules": count("learned_extraction_rules", "project_id = ? AND review_status = 'confirmed'", (project_id,)),
                "llm_calls": count("llm_calls", "project_id = ?", (project_id,)),
            }
        finally:
            db.close()

    def resources(self, project_id: str, article_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT r.* FROM resources r JOIN articles a ON a.article_id = r.article_id
                   WHERE a.project_id = ? AND r.article_id = ? ORDER BY r.created_at""",
                (project_id, article_id),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def reviews(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT r.*, a.title AS article_title FROM review_items r JOIN articles a ON a.article_id = r.article_id
                   WHERE a.project_id = ? ORDER BY CASE r.risk_level WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, r.created_at DESC""",
                (project_id,),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def rules(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        db = self.pm.get_database(project_id)
        try:
            return {
                "mapping": [dict(row) for row in db.fetch_all("SELECT * FROM mapping_rules ORDER BY created_at DESC")],
                "extraction": [dict(row) for row in db.fetch_all("SELECT * FROM learned_extraction_rules WHERE project_id = ? ORDER BY created_at DESC", (project_id,))],
            }
        finally:
            db.close()

    def standardized(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            result = []
            for row in db.fetch_all(
                """SELECT s.*, a.title AS article_title FROM standardized_records s JOIN articles a ON a.article_id=s.article_id
                   WHERE a.project_id = ? ORDER BY s.processed_at DESC""",
                (project_id,),
            ):
                data = dict(row)
                data["data"] = json.loads(data["data"] or "{}")
                result.append(data)
            return result
        finally:
            db.close()

    def costs(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT model_provider, model_name, agent_name, skill_name, COUNT(*) AS calls,
                          SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                          SUM(total_tokens) AS total_tokens, SUM(estimated_cost) AS estimated_cost,
                          SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors
                   FROM llm_calls WHERE project_id = ?
                   GROUP BY model_provider, model_name, agent_name, skill_name ORDER BY total_tokens DESC""",
                (project_id,),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()


TASK_ROUTE_META = {
    "_default": "默认文本模型",
    "field_mapping": "字段映射",
    "document_record_extraction": "文献/段落抽取",
    "unit_suggestion": "单位换算建议",
    "rule_learning": "规则学习",
    "data_extraction": "数据抽取",
    "chat_assistant": "对话助手",
    "figure_extraction": "图像处理",
}


def _api_key_from_ref(value: str) -> str:
    secret, _source, _env_name = resolve_secret(value)
    return secret


def _save_provider_secret(config, provider_name: str, api_key: str) -> str:
    """Store an API key outside config YAML and apply the reference."""
    provider = config.get_provider(provider_name)
    env_name = parse_secret_ref(provider.api_key if provider else "") or None
    ref, _source = store_secret_for_provider(provider_name, api_key, env_name)
    if provider:
        provider.api_key = ref
    return ref


def _key_status_label(source: str) -> str:
    if source == "environment":
        return "环境变量已配置"
    if source == "keychain":
        return "系统钥匙串已配置"
    if source == "inline":
        return "配置文件含明文 Key，需迁移"
    return "未配置"


def _model_from_payload(data: dict[str, Any]) -> ModelConfig:
    return ModelConfig(
        name=str(data.get("name") or "").strip(),
        display_name=str(data.get("display_name") or data.get("name") or "").strip(),
        max_tokens=int(data.get("max_tokens") or 4096),
        supports_vision=bool(data.get("supports_vision") or False),
        pricing=ModelPricing(**(data.get("pricing") or {})),
    )


def _upsert_model(provider: ProviderConfig, model: ModelConfig) -> None:
    if not model.name:
        return
    for index, existing in enumerate(provider.models):
        if existing.name == model.name:
            provider.models[index] = model
            return
    provider.models.append(model)


OPENCODE_OPENAI_MODELS = {
    "glm-5.2", "glm-5.1", "kimi-k2.7-code", "kimi-k2.6",
    "deepseek-v4-pro", "deepseek-v4-flash", "mimo-v2.5", "mimo-v2.5-pro",
}
OPENCODE_ANTHROPIC_MODELS = {
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus",
}

MODEL_SETUP_PRESETS = [
    {"id": "opencode-go", "label": "OpenCode Go", "providers": ["opencode-go-openai", "opencode-go-anthropic"]},
    {"id": "deepseek", "label": "DeepSeek", "providers": ["deepseek-openai", "deepseek-anthropic"]},
    {"id": "xiaomi", "label": "Xiaomi MiMo", "providers": ["xiaomi", "xiaomi-anthropic"]},
    {"id": "openrouter", "label": "OpenRouter", "providers": ["openrouter"]},
    {"id": "openai", "label": "OpenAI", "providers": ["openai"]},
    {"id": "anthropic", "label": "Anthropic", "providers": ["anthropic"]},
    {"id": "custom-openai", "label": "自定义 OpenAI-compatible", "providers": ["custom-openai-compatible"]},
    {"id": "custom-anthropic", "label": "自定义 Anthropic-compatible", "providers": ["custom-anthropic-compatible"]},
]

TEXT_TASK_ROUTES = (
    "_default",
    "field_mapping",
    "document_record_extraction",
    "unit_suggestion",
    "rule_learning",
    "data_extraction",
    "chat_assistant",
)


def _normalize_base_url(base_url: str, api_format: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if api_format == "openai" and url.endswith("/chat/completions"):
        return url[: -len("/chat/completions")]
    if api_format == "anthropic" and url.endswith("/messages"):
        return url[: -len("/messages")]
    return url


def _normalize_provider_model(provider_name: str, model_name: str) -> tuple[str, str]:
    provider = (provider_name or "").strip()
    model = (model_name or "").strip()
    if model.startswith("opencode-go/"):
        model = model.split("/", 1)[1]
        provider = "opencode-go-anthropic" if model in OPENCODE_ANTHROPIC_MODELS else "opencode-go-openai"
    elif provider in {"opencode-go", "opencode"}:
        provider = "opencode-go-anthropic" if model in OPENCODE_ANTHROPIC_MODELS else "opencode-go-openai"
    return provider, model


def _setup_preset_for_provider(provider_name: str) -> str:
    for preset in MODEL_SETUP_PRESETS:
        if provider_name in preset["providers"]:
            return str(preset["id"])
    return provider_name or ""


def _resolve_model_setup_provider(config, provider_preset: str, model_id: str, base_url: str = "") -> tuple[ProviderConfig, str, str]:
    preset = (provider_preset or "").strip()
    model = (model_id or "").strip()
    if not preset:
        raise ValueError("请选择服务商")
    if not model:
        raise ValueError("请输入模型 ID")

    provider_name = preset
    if preset == "opencode-go":
        provider_name = "opencode-go-anthropic" if model in OPENCODE_ANTHROPIC_MODELS or model.startswith("qwen3.") or model.startswith("minimax-") else "opencode-go-openai"
    elif preset == "deepseek":
        provider_name = "deepseek-openai"
    elif preset == "xiaomi":
        provider_name = "xiaomi"
    elif preset == "custom-openai":
        provider_name = "custom-openai-compatible"
    elif preset == "custom-anthropic":
        provider_name = "custom-anthropic-compatible"
    provider_name, model = _normalize_provider_model(provider_name, model)

    provider = config.get_provider(provider_name)
    if not provider and preset.startswith("custom-"):
        api_format = "anthropic" if preset == "custom-anthropic" else "openai"
        provider = ProviderConfig(
            name=provider_name,
            display_name="自定义 Anthropic-compatible" if api_format == "anthropic" else "自定义 OpenAI-compatible",
            api_format=api_format,
            base_url=_normalize_base_url(base_url, api_format),
            is_custom=True,
            provider_kind="custom",
            models=[ModelConfig(name=model, display_name=model)],
        )
        config.providers.append(provider)
    if not provider:
        raise ValueError(f"未知服务商：{provider_name}")
    if base_url and provider.is_custom:
        provider.base_url = _normalize_base_url(base_url, provider.api_format)
    _ensure_route_model(config, provider.name, model)
    return provider, provider.name, model


def _apply_single_model_routes(config, provider_name: str, model_name: str) -> None:
    current = config.get_task_model("_default")
    routes = dict(config.task_models)
    for task_name in TEXT_TASK_ROUTES:
        routes[task_name] = TaskModelConfig(
            provider=provider_name,
            model=model_name,
            temperature=current.temperature if current else 0.1,
            max_tokens=current.max_tokens if current else 4096,
        )
    routes.pop("figure_extraction", None)
    config.task_models = routes


def _model_setup_status(config) -> dict[str, Any]:
    default = config.get_task_model("_default")
    provider = config.get_provider(default.provider) if default else None
    text_routes = {name: config.task_models.get(name) for name in TEXT_TASK_ROUTES}
    applied = bool(default) and all(route and route.provider == default.provider and route.model == default.model for route in text_routes.values())
    public_provider = _provider_public(provider) if provider else {}
    prefs = config.ui_preferences or {}
    setup_state = prefs.get("model_setup", {})
    key_status = public_provider.get("key_status", "not_configured")
    return {
        "configured": bool(provider and default and key_status == "configured"),
        "provider": default.provider if default else "",
        "provider_preset": _setup_preset_for_provider(default.provider if default else ""),
        "provider_label": public_provider.get("display_name", ""),
        "model": default.model if default else "",
        "key_status": key_status,
        "key_status_label": public_provider.get("key_status_label", _key_status_label(key_status)),
        "key_source": public_provider.get("key_source", ""),
        "api_key_ref": public_provider.get("api_key_ref", ""),
        "env_name": public_provider.get("env_name", ""),
        "all_text_tasks_use_same_model": applied,
        "last_test": setup_state,
        "presets": MODEL_SETUP_PRESETS,
    }


def _ensure_route_model(config, provider_name: str, model_name: str) -> None:
    provider_name, model_name = _normalize_provider_model(provider_name, model_name)
    provider = config.get_provider(provider_name)
    if provider and model_name and model_name not in {model.name for model in provider.models}:
        provider.models.append(ModelConfig(name=model_name, display_name=model_name))


def _provider_public(provider: ProviderConfig) -> dict[str, Any]:
    key_ref = provider.api_key or ""
    _secret, source, env_name = resolve_secret(key_ref)
    safe_ref = key_ref if env_name else secret_ref_for_provider(provider.name) if source == "inline" else key_ref
    return {
        "name": provider.name,
        "display_name": provider.display_name or provider.name,
        "base_url": provider.base_url or "",
        "api_format": provider.api_format,
        "enabled": provider.enabled,
        "is_custom": provider.is_custom,
        "provider_kind": provider.provider_kind,
        "auth_type": provider.auth_type,
        "api_key_header": provider.api_key_header,
        "default_headers": provider.default_headers,
        "api_key_ref": safe_ref,
        "key_status": "configured" if source in {"environment", "keychain"} else "inline" if source == "inline" else "missing" if env_name else "not_configured",
        "key_source": source,
        "key_status_label": _key_status_label(source),
        "env_name": env_name,
        "models": [
            {
                "name": model.name,
                "display_name": model.display_name or model.name,
                "supports_vision": model.supports_vision,
                "max_tokens": model.max_tokens,
                "pricing": model.pricing.model_dump(),
            }
            for model in provider.models
        ],
    }


def _provider_from_payload(data: dict[str, Any], existing: ProviderConfig | None = None) -> ProviderConfig:
    name = str(data.get("name") or (existing.name if existing else "")).strip()
    api_format = data.get("api_format") or (existing.api_format if existing else "openai")
    api_key_ref = data.get("api_key_ref") or (existing.api_key if existing else None)
    if data.get("api_key") and parse_secret_ref(str(data.get("api_key"))):
        api_key_ref = data.get("api_key")
    if not api_key_ref:
        api_key_ref = secret_ref_for_provider(name)
    provider = ProviderConfig(
        name=name,
        display_name=str(data.get("display_name") or (existing.display_name if existing else "") or name).strip(),
        api_key=api_key_ref,
        base_url=_normalize_base_url(data.get("base_url") or (existing.base_url if existing else None) or "", api_format) or None,
        api_format=api_format,
        enabled=bool(data.get("enabled", existing.enabled if existing else True)),
        default_headers=data.get("default_headers") or (existing.default_headers if existing else {}),
        is_custom=bool(data.get("is_custom", existing.is_custom if existing else True)),
        auth_type=data.get("auth_type") or (existing.auth_type if existing else "bearer"),
        api_key_header=data.get("api_key_header") or (existing.api_key_header if existing else "Authorization"),
        provider_kind=data.get("provider_kind") or (existing.provider_kind if existing else ("custom" if data.get("is_custom", True) else "preset")),
        models=[],
    )
    for model_data in data.get("models") or []:
        model = _model_from_payload(model_data)
        _provider_name, model.name = _normalize_provider_model(provider.name, model.name)
        if model.name:
            _upsert_model(provider, model)
    if existing and not provider.models:
        provider.models = existing.models
    return provider


def _test_provider_config(provider: ProviderConfig, api_key: str = "", model_name: str = "") -> dict[str, Any]:
    from ..providers.openai_provider import OpenAIProvider
    from ..providers.anthropic_provider import AnthropicProvider
    from ..providers.google_provider import GoogleProvider
    from ..providers.zhipu_provider import ZhipuProvider
    from ..providers.ollama_provider import OllamaProvider

    key = api_key or _api_key_from_ref(provider.api_key or "")
    default_headers = dict(provider.default_headers or {})
    if provider.auth_type == "api-key-header" and key:
        default_headers[provider.api_key_header or "Authorization"] = key
        key = "not-used"
    elif provider.auth_type == "none" and not key:
        key = "not-used"
    kwargs = {"api_key": key, "base_url": provider.base_url}
    if default_headers:
        kwargs["default_headers"] = default_headers
    provider_map = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "google": GoogleProvider,
        "zhipu": ZhipuProvider,
        "ollama": OllamaProvider,
    }
    cls = provider_map.get(provider.name) or (AnthropicProvider if provider.api_format == "anthropic" else OpenAIProvider)
    instance = cls(**kwargs)
    instance.name = provider.name
    instance.display_name = provider.display_name or provider.name
    if model_name:
        response = instance.chat_completion([{"role": "user", "content": "ping"}], model=model_name, max_tokens=1, temperature=0)
        normalized = [{"name": model_name, "display_name": model_name, "supports_vision": False, "max_tokens": 4096}]
        return {
            "success": True,
            "models": normalized,
            "provider": provider.name,
            "model": response.model or model_name,
            "message": "模型调用成功",
            "last_tested_at": datetime.now().isoformat(timespec="seconds"),
        }
    valid = instance.validate_api_key()
    models = instance.list_models() if valid else []
    if not valid and model_name:
        instance.chat_completion([{"role": "user", "content": "ping"}], model=model_name, max_tokens=1, temperature=0)
        valid = True
    normalized = [
        {
            "name": item.get("name") or item.get("id") or "",
            "display_name": item.get("display_name") or item.get("name") or item.get("id") or "",
            "supports_vision": bool(item.get("supports_vision") or False),
            "max_tokens": int(item.get("max_tokens") or 4096),
        }
        for item in models
        if item.get("name") or item.get("id")
    ]
    return {"success": valid, "models": normalized, "provider": provider.name, "model": model_name, "message": "连接测试成功" if valid else "连接测试失败", "last_tested_at": datetime.now().isoformat(timespec="seconds")}


def create_app(
    project_manager: ProjectManager | None = None,
    runtime_settings: RuntimeSettings | None = None,
    keycloak_admin_client: KeycloakAdminClient | None = None,
) -> FastAPI:
    runtime = runtime_settings or load_runtime_settings()
    pm = project_manager or ProjectManager(base_dir=runtime.storage_root)
    service = WorkbenchService(pm)
    rule_governance = RuleGovernanceService(pm)
    workflow = WorkflowRunner(project_manager=pm)
    repo = WebRepository(pm)
    tasks = TaskDispatcher(pm, runtime)
    agent = ArticleCurationAgent(pm, runtime_settings=runtime)
    keycloak_admin = keycloak_admin_client or KeycloakAdminClient(runtime)
    governance = AdminGovernanceService(pm, runtime)
    workspace_access = WorkspaceAccessService(pm)
    workspace_access.bootstrap_default_organization()
    rate_limiter = ApiRateLimiter(runtime)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            tasks.close()
            agent.close()
            keycloak_admin.close()

    documentation_urls = (
        {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
        if runtime.enable_api_docs
        else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    )
    app = FastAPI(
        title="GeoChem API",
        version="1.1.0",
        lifespan=lifespan,
        **documentation_urls,
    )
    app.state.runtime_settings = runtime
    app.state.task_dispatcher = tasks
    app.state.keycloak_admin = keycloak_admin
    app.state.workspace_access = workspace_access
    app.state.rate_limiter = rate_limiter
    if runtime.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=runtime.allowed_hosts)
    if runtime.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=runtime.cors_origins,
            allow_credentials=runtime.auth_mode == "oidc",
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(
        AuthenticationMiddleware,
        settings=runtime,
        audit_logger=ApiAuditLogger(pm),
        workspace_access=workspace_access,
        rate_limiter=rate_limiter,
    )

    if not runtime.enable_api_docs:
        @app.get("/docs", include_in_schema=False)
        @app.get("/redoc", include_in_schema=False)
        @app.get("/openapi.json", include_in_schema=False)
        def disabled_api_documentation():
            raise HTTPException(status_code=404, detail="API documentation is disabled")

    @app.exception_handler(ValueError)
    async def value_error_handler(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PermissionError)
    async def permission_error_handler(request, exc):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "profile": runtime.profile.value,
            "build_id": runtime.build_id or "unversioned",
            "time": datetime.now().isoformat(),
        }

    @app.get("/api/v1/ready")
    def ready():
        db = pm.get_default_database()
        try:
            db.fetch_one("SELECT 1 AS ready")
        finally:
            db.close()
        dependencies = {"database": "ready"}
        if runtime.task_backend == "celery":
            try:
                from redis import Redis

                redis_client = Redis.from_url(runtime.redis_url, socket_connect_timeout=2, socket_timeout=2)
                redis_client.ping()
                redis_client.close()
                dependencies["redis"] = "ready"
            except Exception as exc:
                raise HTTPException(503, f"Redis is not ready: {exc}") from exc
        try:
            runtime.storage_root.mkdir(parents=True, exist_ok=True)
            if not os.access(runtime.storage_root, os.W_OK):
                raise OSError("storage root is not writable")
            dependencies["storage"] = "ready"
        except OSError as exc:
            raise HTTPException(503, f"Storage is not ready: {exc}") from exc
        return {
            "status": "ready",
            "profile": runtime.profile.value,
            "database": "postgresql" if runtime.database_url else "sqlite",
            "dependencies": dependencies,
            "time": datetime.now().isoformat(),
        }

    @app.get("/api/v1/auth/config")
    def auth_config():
        return {
            "enabled": runtime.auth_mode == "oidc",
            "issuer_url": runtime.oidc_issuer_url,
            "client_id": runtime.oidc_client_id,
            "audience": runtime.oidc_audience,
            "scopes": runtime.oidc_scopes,
        }

    @app.get("/api/v1/auth/me")
    def auth_me(request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "尚未登录。")
        governance.ensure_user_profile(user.subject, user.username, user.email, user.username)
        workspaces = workspace_access.list_workspaces(user.subject, user.roles)
        current_workspace = workspaces[0] if workspaces else None
        return {
            **user.public_dict(),
            "policy": governance.user_policy(user.subject),
            "workspaces": workspaces,
            "current_workspace": current_workspace,
            "workspace_role": current_workspace.get("workspace_role", "") if current_workspace else "",
        }

    def actor_id(request: Request) -> str:
        user = getattr(request.state, "user", None)
        return str(getattr(user, "subject", "") or "system")

    def actor_roles(request: Request) -> frozenset[str]:
        user = getattr(request.state, "user", None)
        return frozenset(getattr(user, "roles", ()) or ())

    def actor_workspace_role(request: Request) -> str:
        role = str(getattr(request.state, "workspace_role", "") or "")
        if role:
            return role
        return "owner" if runtime.auth_mode != "oidc" else "viewer"

    @app.get("/api/v1/workspaces")
    def workspaces(request: Request):
        return {
            "workspaces": workspace_access.list_workspaces(
                actor_id(request), actor_roles(request)
            )
        }

    def storage_owner(request: Request, article_id: str = "") -> str:
        """Resolve the stable account charged for a project upload."""

        return governance.article_storage_owner(article_id, actor_id(request))

    def require_storage_capacity(user_id: str, incoming_bytes: int) -> None:
        try:
            governance.ensure_storage_available(user_id, incoming_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

    def register_imported_storage(
        user_id: str,
        project_id: str,
        result,
        object_type: str = "article_source",
    ) -> None:
        governance.register_storage_object(
            user_id,
            project_id,
            str(result.article_id or ""),
            object_type,
            str(result.local_path or ""),
            int(result.file_size or 0),
            str(result.file_hash or ""),
        )

    def require_user_admin() -> KeycloakAdminClient:
        if runtime.auth_mode != "oidc":
            raise HTTPException(
                409,
                "本地开发模式未启用身份服务；请在局域网生产配置中使用 Keycloak 管理真实账号。",
            )
        if not keycloak_admin.available:
            raise HTTPException(503, "Keycloak 用户管理服务账号尚未配置。")
        return keycloak_admin

    def call_user_admin(operation):
        try:
            return operation()
        except KeycloakAdminError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/v1/admin/status")
    def admin_status():
        return {
            "profile": runtime.profile.value,
            "build_id": runtime.build_id or "unversioned",
            "auth_mode": runtime.auth_mode,
            "database": "postgresql" if runtime.database_url else "sqlite",
            "task_backend": runtime.task_backend,
            "credential_vault_ready": governance.vault.available,
            "user_management": {
                "mode": "keycloak" if runtime.auth_mode == "oidc" else "local-preview",
                "available": keycloak_admin.available if runtime.auth_mode == "oidc" else False,
                "realm": runtime.keycloak_realm if runtime.auth_mode == "oidc" else "",
                "message": (
                    "Keycloak 用户与角色管理已连接。"
                    if keycloak_admin.available
                    else "本地开发预览未启用登录；生产部署后在此管理真实账号。"
                ),
            },
        }

    @app.get("/api/v1/admin/users")
    def admin_users(q: str = "", first: int = 0, limit: int = 100):
        if runtime.auth_mode != "oidc":
            governance.ensure_user_profile("local-development", "Local Developer")
            return {
                "users": [{
                    "id": "local-development",
                    "username": "Local Developer",
                    "email": "",
                    "first_name": "Local",
                    "last_name": "Developer",
                    "enabled": True,
                    "email_verified": False,
                    "created_at": 0,
                    "roles": sorted(GEOCHEM_ROLES),
                }],
                "total": 1,
                "first": 0,
                "limit": limit,
                "preview": True,
            }
        client = require_user_admin()
        result = call_user_admin(lambda: client.list_users(q, first, limit))
        for item in result.get("users", []):
            governance.ensure_user_profile(
                str(item.get("id") or ""),
                str(item.get("username") or ""),
                str(item.get("email") or ""),
                " ".join(
                    part for part in (
                        str(item.get("first_name") or "").strip(),
                        str(item.get("last_name") or "").strip(),
                    ) if part
                ),
            )
        return result

    @app.get("/api/v1/admin/workspaces/{project_id}/members")
    def admin_workspace_members(project_id: str, request: Request):
        workspace_access.require(
            project_id, actor_id(request), actor_roles(request), "manage"
        )
        return {"members": workspace_access.list_members(project_id)}

    @app.post("/api/v1/admin/workspaces/{project_id}/members")
    def add_admin_workspace_member(
        project_id: str,
        payload: WorkspaceMemberRequest,
        request: Request,
    ):
        workspace_access.require(
            project_id, actor_id(request), actor_roles(request), "manage"
        )
        return workspace_access.upsert_member(
            project_id,
            payload.user_id,
            payload.role,
            actor_id(request),
            status=payload.status,
        )

    @app.patch("/api/v1/admin/workspaces/{project_id}/members/{user_id}")
    def update_admin_workspace_member(
        project_id: str,
        user_id: str,
        payload: WorkspaceMemberPatchRequest,
        request: Request,
    ):
        workspace_access.require(
            project_id, actor_id(request), actor_roles(request), "manage"
        )
        existing = next(
            (
                item for item in workspace_access.list_members(project_id)
                if item.get("user_id") == user_id
            ),
            None,
        )
        if not existing:
            raise ValueError("工作区成员不存在。")
        return workspace_access.upsert_member(
            project_id,
            user_id,
            payload.role or str(existing.get("role") or "viewer"),
            actor_id(request),
            status=payload.status or str(existing.get("status") or "active"),
        )

    @app.delete("/api/v1/admin/workspaces/{project_id}/members/{user_id}")
    def delete_admin_workspace_member(
        project_id: str,
        user_id: str,
        request: Request,
    ):
        workspace_access.require(
            project_id, actor_id(request), actor_roles(request), "manage"
        )
        workspace_access.remove_member(project_id, user_id)
        return {"status": "deleted", "project_id": project_id, "user_id": user_id}

    @app.get("/api/v1/admin/overview")
    def admin_overview():
        result = governance.overview()
        if runtime.auth_mode == "oidc" and keycloak_admin.available:
            users = call_user_admin(lambda: keycloak_admin.list_users("", 0, 1000))
            result["users_total"] = int(users.get("total") or len(users.get("users", [])))
            result["users_enabled"] = sum(1 for item in users.get("users", []) if item.get("enabled"))
        else:
            result["users_total"] = 1
            result["users_enabled"] = 1
        return result

    @app.get("/api/v1/admin/model-credentials")
    def admin_model_credentials():
        return {
            "vault_ready": governance.vault.available,
            "credentials": governance.list_credentials(),
        }

    @app.post("/api/v1/admin/model-credentials")
    def create_admin_model_credential(payload: AdminCredentialRequest, request: Request):
        return governance.create_credential(payload.model_dump(), actor_id(request))

    @app.patch("/api/v1/admin/model-credentials/{credential_id}")
    def update_admin_model_credential(
        credential_id: str,
        payload: AdminCredentialUpdateRequest,
        request: Request,
    ):
        return governance.update_credential(
            credential_id,
            payload.model_dump(exclude_none=True),
            actor_id(request),
        )

    @app.delete("/api/v1/admin/model-credentials/{credential_id}")
    def delete_admin_model_credential(credential_id: str):
        governance.delete_credential(credential_id)
        return {"status": "deleted", "credential_id": credential_id}

    @app.get("/api/v1/admin/users/{user_id}/policy")
    def admin_user_policy(user_id: str):
        return governance.user_policy(user_id)

    @app.put("/api/v1/admin/users/{user_id}/model-allocation")
    def update_admin_model_allocation(
        user_id: str,
        payload: AdminModelAllocationRequest,
        request: Request,
    ):
        return governance.set_model_allocation(
            user_id,
            payload.credential_id,
            payload.enabled,
            payload.monthly_token_limit,
            payload.monthly_cost_limit,
            actor_id(request),
        )

    @app.put("/api/v1/admin/users/{user_id}/storage-quota")
    def update_admin_storage_quota(
        user_id: str,
        payload: AdminStorageQuotaRequest,
        request: Request,
    ):
        return governance.set_storage_quota(user_id, payload.quota_bytes, actor_id(request))

    @app.get("/api/v1/admin/tasks")
    def admin_tasks(limit: int = Query(default=200, ge=1, le=1000)):
        return {"tasks": governance.tasks(limit)}

    @app.get("/api/v1/admin/audit-events")
    def admin_audit_events(
        limit: int = Query(default=200, ge=1, le=1000),
        user_id: str = "",
    ):
        return {"events": governance.audit_events(limit, user_id)}

    @app.get("/api/v1/me/usage")
    def my_usage(request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "尚未登录。")
        return governance.user_policy(user.subject)

    @app.post("/api/v1/admin/users")
    def create_admin_user(request: AdminUserCreateRequest):
        client = require_user_admin()
        return call_user_admin(lambda: client.create_user(
            username=request.username.strip(),
            email=request.email.strip(),
            first_name=request.first_name.strip(),
            last_name=request.last_name.strip(),
            enabled=request.enabled,
            roles=request.roles,
            password=request.password,
            temporary_password=request.temporary_password,
        ))

    @app.patch("/api/v1/admin/users/{user_id}")
    def update_admin_user(user_id: str, request: AdminUserUpdateRequest):
        client = require_user_admin()
        values = request.model_dump(exclude_none=True)
        return call_user_admin(lambda: client.update_user(user_id, values))

    @app.put("/api/v1/admin/users/{user_id}/roles")
    def update_admin_user_roles(user_id: str, request: AdminRoleUpdateRequest):
        client = require_user_admin()
        return call_user_admin(lambda: client.replace_roles(user_id, request.roles))

    @app.post("/api/v1/admin/users/{user_id}/reset-password")
    def reset_admin_user_password(user_id: str, request: AdminPasswordResetRequest):
        client = require_user_admin()
        call_user_admin(lambda: client.reset_password(user_id, request.password, request.temporary))
        return {"status": "password_reset", "user_id": user_id, "temporary": request.temporary}

    @app.post("/api/v1/admin/users/{user_id}/logout")
    def logout_admin_user(user_id: str):
        client = require_user_admin()
        call_user_admin(lambda: client.logout_user(user_id))
        return {"status": "sessions_revoked", "user_id": user_id}

    @app.delete("/api/v1/admin/users/{user_id}")
    def delete_admin_user(user_id: str):
        client = require_user_admin()
        call_user_admin(lambda: client.delete_user(user_id))
        return {"status": "deleted", "user_id": user_id}

    @app.get("/api/v1/chat/threads")
    def chat_threads(project_id: str, article_id: str = ""):
        return agent.threads(project_id, article_id)

    @app.post("/api/v1/chat/threads")
    def create_chat_thread(request: ChatThreadRequest):
        return agent.create_thread(request.project_id, request.article_id, request.title, request.scope)

    @app.get("/api/v1/chat/threads/{thread_id}")
    def chat_thread(thread_id: str, project_id: str):
        return agent.thread(project_id, thread_id)

    @app.get("/api/v1/chat/threads/{thread_id}/state")
    def chat_thread_state(thread_id: str, project_id: str):
        return agent.thread_state(project_id, thread_id)

    @app.delete("/api/v1/chat/threads/{thread_id}")
    def delete_chat_thread(thread_id: str, project_id: str, cancel_waiting: bool = False):
        return agent.delete_thread(project_id, thread_id, cancel_waiting)

    @app.post("/api/v1/chat/threads/{thread_id}/messages")
    def create_chat_message(thread_id: str, request: ChatMessageRequest):
        return agent.start(
            request.project_id,
            thread_id,
            request.content,
            request.article_id,
            require_model=True,
        )

    @app.post("/api/v1/chat/threads/{thread_id}/actions")
    def chat_action(thread_id: str, request: ChatActionRequest):
        if request.action == "message":
            return agent.start(
                request.project_id,
                thread_id,
                request.content,
                request.article_id,
                require_model=True,
            )
        if request.action in {"select", "clear_selection"}:
            entity_type = "clear" if request.action == "clear_selection" else request.entity_type
            return agent.select_chat_entity(
                request.project_id, thread_id,
                EntitySelectionInput(entity_type=entity_type, entity_id=request.entity_id, query=request.query),
            )
        if request.action == "resume":
            if not request.run_id:
                raise ValueError("缺少要恢复的 Agent run_id。")
            return agent.resume(request.project_id, request.run_id, request.payload, require_model=True)
        if request.action == "handoff":
            if not request.run_id:
                raise ValueError("缺少要交接的 Agent run_id。")
            return agent.handoff(request.project_id, request.run_id)
        if request.action == "start_processing":
            return agent.start(
                request.project_id,
                thread_id,
                request.content or "开始处理当前文章",
                request.article_id,
                require_model=True,
            )
        if request.action == "execute_confirmed":
            if not request.run_id:
                raise ValueError("缺少提出该操作的 Agent run_id。")
            operation = str(request.payload.get("operation") or "")
            if not operation:
                raise ValueError("缺少要确认执行的正式操作。")
            return agent.execute_confirmed_action(
                request.project_id,
                thread_id,
                request.run_id,
                operation,
                request.payload,
            )
        raise ValueError("不支持的对话操作。")

    @app.get("/api/v1/agent/entities")
    def agent_entities(project_id: str, type: str = "", q: str = "", context_id: str = ""):
        return agent.tools.entities(project_id, type, q, context_id)

    @app.post("/api/v1/chat/threads/{thread_id}/select")
    def select_chat_entity(thread_id: str, request: ChatEntitySelectionRequest):
        return agent.select_chat_entity(
            request.project_id,
            thread_id,
            EntitySelectionInput(entity_type=request.entity_type, entity_id=request.entity_id, query=request.query),
        )

    @app.post("/api/v1/article-sources/search")
    def search_article_sources(request: ArticleSourceSearchRequest):
        return OpenAccessResolver(pm).search(request.project_id, request.source)

    @app.post("/api/v1/literature/search")
    def search_literature(request: LiteratureSearchRequest):
        return agent.search_literature(request.project_id, request.thread_id, request.query, request.sort_mode, request.limit)

    @app.post("/api/v1/literature/results/{result_id}/import")
    def import_literature_result(result_id: str, request: LiteratureImportRequest):
        return agent.import_literature_result(request.project_id, request.thread_id, result_id)

    @app.post("/api/v1/article-sources/{source_id}/confirm")
    def confirm_article_source(source_id: str, request: ArticleSourceConfirmRequest):
        return OpenAccessResolver(pm).confirm(request.project_id, source_id)

    @app.post("/api/v1/chat/threads/{thread_id}/upload")
    async def upload_chat_article(
        thread_id: str,
        request: Request,
        project_id: str,
        file: UploadFile = File(...),
    ):
        _config, project_dir = pm.load_project(project_id)
        # Preserve the user-visible filename in the article source folder; a
        # NamedTemporaryFile would otherwise turn `main.pdf` into `tmpXXXX.pdf`.
        safe_name = _safe_upload_name(file.filename, "article.pdf")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / safe_name
            written = await _persist_upload(file, temp_path, runtime.max_upload_mb)
            validate_uploaded_file(
                temp_path,
                safe_name,
                file.content_type,
                allowed_extensions=PDF_EXTENSIONS,
            )
            owner_id = storage_owner(request)
            require_storage_capacity(owner_id, written)
            db = pm.get_database(project_id)
            try:
                result = FileImporter().import_file(db, project_dir, temp_path)
                db.commit()
            finally:
                db.close()
        register_imported_storage(owner_id, project_id, result)
        selected = agent.select_chat_entity(
            project_id,
            thread_id,
            EntitySelectionInput(entity_type="article", entity_id=result.article_id),
        )
        return {
            "article_id": result.article_id,
            "resource_id": result.resource_id,
            "file_name": result.file_name,
            "status": "selected",
            "message": "PDF 已导入并选择为当前文章，请确认是否开始数据提取。",
            "selection": selected.get("selection", {}),
        }

    @app.post("/api/v1/agent-runs")
    def create_agent_run(request: AgentRunRequest):
        return agent.start(
            request.project_id,
            request.thread_id,
            request.message,
            request.article_id,
            require_model=True,
        )

    @app.get("/api/v1/agent-runs/{run_id}")
    def agent_run(run_id: str, project_id: str):
        return agent.run(project_id, run_id)

    @app.post("/api/v1/agent-runs/{run_id}/resume")
    def resume_agent_run(run_id: str, request: AgentResumeRequest):
        return agent.resume(request.project_id, run_id, request.response, require_model=True)

    @app.post("/api/v1/agent-runs/{run_id}/cancel")
    def cancel_agent_run(run_id: str, project_id: str):
        return agent.cancel(project_id, run_id)

    @app.post("/api/v1/agent-runs/{run_id}/handoff")
    def handoff_agent_run(run_id: str, request: AgentHandoffRequest):
        return agent.handoff(
            request.project_id,
            run_id,
            presentation=request.presentation,
            workbench_view=request.workbench_view,
            workbench_stage=request.workbench_stage,
        )

    @app.post("/api/v1/agent-runs/{run_id}/resume-from-workbench")
    def resume_agent_from_workbench(run_id: str, request: AgentHandoffRequest):
        return agent.resume_from_workbench(request.project_id, run_id)

    @app.post("/api/v1/agent-runs/{run_id}/return-from-workbench")
    def return_agent_from_workbench(run_id: str, request: AgentHandoffRequest):
        return agent.resume_from_workbench(request.project_id, run_id)

    @app.get("/api/v1/agent-runs/{run_id}/workbench-diff")
    def agent_workbench_diff(run_id: str, project_id: str):
        return agent.workbench_diff(project_id, run_id)

    @app.get("/api/v1/agent-runs/{run_id}/events")
    async def agent_run_events(run_id: str, project_id: str, after: int = Query(0, ge=0)):
        async def stream():
            cursor = after
            while True:
                events = agent.events(project_id, run_id, cursor)
                for event in events:
                    cursor = max(cursor, int(event["event_id"]))
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                run = agent.run(project_id, run_id)
                if run["status"] in {"completed", "failed", "cancelled"}:
                    yield f"data: {json.dumps({'event_id': cursor, 'level': 'INFO', 'message': run['status'], 'done': True}, ensure_ascii=False)}\n\n"
                    break
                await asyncio.sleep(0.6)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/v1/chat/messages/{message_id}/citations")
    def chat_citations(message_id: str, project_id: str):
        return agent.citations(project_id, message_id)

    @app.get("/api/v1/rag/status")
    def rag_status(project_id: str, article_id: str = ""):
        return RetrievalService(pm).status(project_id, article_id)

    @app.post("/api/v1/rag/reindex")
    def rag_reindex(project_id: str, article_id: str):
        return RetrievalService(pm).sync_article(project_id, article_id)

    @app.post("/api/v1/rag/query")
    def rag_query(request: RagQueryRequest):
        retrieval = RetrievalService(pm)
        if request.mode == "statistics":
            return retrieval.statistics(request.project_id, request.article_id, request.field, request.operation)
        return retrieval.answer(request.project_id, request.article_id, request.question)

    @app.post("/api/v1/providers/{name}/test")
    def test_provider(name: str, request: ProviderTestRequest):
        cfg = load_config()
        provider_name, model_name = _normalize_provider_model(name, request.model)
        prov_cfg = cfg.get_provider(provider_name)
        if not prov_cfg:
            return {"success": False, "models": [], "error": f"Unknown provider: {provider_name}"}
        try:
            return _test_provider_config(prov_cfg, request.api_key, model_name)
        except Exception as e:
            return {"success": False, "models": [], "error": str(e)}

    @app.post("/api/v1/providers/test")
    def test_custom_provider(request: ProviderTestRequest):
        cfg = load_config()
        existing = cfg.get_provider(request.provider)
        provider = _provider_from_payload({
            "name": request.provider or "custom-test",
            "display_name": request.display_name or request.provider or "Custom Provider",
            "api_format": request.api_format or "openai",
            "base_url": request.base_url,
            "api_key_ref": request.api_key_ref,
            "default_headers": request.default_headers,
            "auth_type": request.auth_type,
            "api_key_header": request.api_key_header,
            "is_custom": True,
        }, existing)
        try:
            _provider_name, model_name = _normalize_provider_model(provider.name, request.model)
            return _test_provider_config(provider, request.api_key, model_name)
        except Exception as e:
            return {"success": False, "models": [], "error": str(e)}

    @app.get("/api/v1/workspace")
    def workspace():
        return repo.workspace()

    @app.get("/api/v1/dashboard")
    def dashboard(project_id: str):
        return repo.dashboard(project_id)

    @app.get("/api/v1/articles")
    def articles(project_id: str):
        return repo.articles(project_id)

    @app.get("/api/v1/header-configs")
    def header_configs(project_id: str):
        return repo.header_configs(project_id)

    @app.post("/api/v1/header-configs/import")
    async def import_header_config(
        request: Request,
        project_id: str,
        name: str = "",
        file: UploadFile = File(...),
    ):
        _config, project_dir = pm.load_project(project_id)
        temp_path = _temporary_upload_path(file.filename, "headers.csv")
        written = await _persist_upload(file, temp_path, runtime.max_upload_mb)
        validate_uploaded_file(
            temp_path,
            file.filename or "headers.csv",
            file.content_type,
            allowed_extensions=HEADER_EXTENSIONS,
        )
        owner_id = storage_owner(request)
        require_storage_capacity(owner_id, written)
        try:
            source_headers, _samples = HeaderConfigImporter().read_headers(temp_path)
            if not source_headers:
                raise HTTPException(400, "No header row found")
            targets = TargetHeaderBuilder().build(source_headers)
            rows = [
                {
                    "header_id": f"pending:{index}",
                    "字段名": target.display_header,
                    "display_header": target.display_header,
                    "source_header": target.source_header,
                    "canonical_field": target.canonical_field,
                    "默认单位": target.target_unit,
                    "target_unit": target.target_unit,
                    "description": target.description,
                    "field_group": target.field_group,
                }
                for index, target in enumerate(targets)
            ]
            db = pm.get_database(project_id)
            try:
                row = db.fetch_one("SELECT config_id FROM header_configs WHERE config_id LIKE 'HCFG_%' ORDER BY config_id DESC LIMIT 1")
                number = int(row["config_id"].split("_")[-1]) + 1 if row else 1
                config_id = f"HCFG_{number:04d}"
                for index, item in enumerate(rows):
                    item["header_id"] = f"{config_id}:{index}"
                now = datetime.now().isoformat()
                db.execute(
                    """INSERT INTO header_configs
                       (config_id, project_id, name, source_file, description, headers_json, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, '', ?, 'active', ?, ?)""",
                    (config_id, project_id, name.strip() or Path(file.filename or "headers").stem, file.filename or "", json.dumps(rows, ensure_ascii=False), now, now),
                )
                db.commit()
            finally:
                db.close()
            backup = project_dir / "headers" / f"{config_id}.json"
            backup.write_text(json.dumps({"config_id": config_id, "name": name, "headers": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
            governance.register_storage_object(
                owner_id,
                project_id,
                "",
                "header_config",
                str(backup.relative_to(project_dir)),
                backup.stat().st_size,
            )
            return {"config_id": config_id, "field_count": len(rows), "headers": rows}
        finally:
            temp_path.unlink(missing_ok=True)

    @app.post("/api/v1/articles/{article_id}/header-config")
    def assign_header(article_id: str, request: HeaderAssignmentRequest):
        db = pm.get_database(request.project_id)
        try:
            db.execute("UPDATE article_header_assignments SET status='superseded' WHERE project_id=? AND article_id=? AND status='confirmed'", (request.project_id, article_id))
            row = db.fetch_one("SELECT assignment_id FROM article_header_assignments ORDER BY assignment_id DESC LIMIT 1")
            number = int(row["assignment_id"].split("_")[-1]) + 1 if row else 1
            assignment_id = f"HASSIGN_{number:06d}"
            db.execute(
                """INSERT INTO article_header_assignments
                   (assignment_id, project_id, article_id, config_id, suggested_by, confidence, status, reason, created_at)
                   VALUES (?, ?, ?, ?, 'user', 1, 'confirmed', 'Selected in Web UI', ?)""",
                (assignment_id, request.project_id, article_id, request.config_id, datetime.now().isoformat()),
            )
            db.commit()
            return {"assignment_id": assignment_id, "status": "confirmed"}
        finally:
            db.close()

    @app.post("/api/v1/import/open-source")
    def open_source(request: ImportSourceRequest):
        return workflow.open_source_in_browser(request.project_id, request.source)

    @app.post("/api/v1/import/confirm-access")
    def confirm_access(request: ConfirmAccessRequest):
        task_id = tasks.submit(
            request.project_id, request.article_id, "confirm_access",
            "confirm_access", {"url": request.url},
        )
        return {"task_id": task_id}

    @app.post("/api/v1/articles/{article_id}/upload")
    async def upload_file(
        article_id: str,
        request: Request,
        project_id: str,
        file: UploadFile = File(...),
    ):
        _config, project_dir = pm.load_project(project_id)
        temp_path = _temporary_upload_path(file.filename, "upload.bin")
        written = await _persist_upload(file, temp_path, runtime.max_upload_mb)
        validate_uploaded_file(
            temp_path,
            file.filename or "upload.bin",
            file.content_type,
            allowed_extensions=ARTICLE_EXTENSIONS,
        )
        owner_id = storage_owner(request, article_id)
        require_storage_capacity(owner_id, written)
        db = pm.get_database(project_id)
        try:
            result = FileImporter().import_file(db, project_dir, temp_path, article_id=article_id)
            db.commit()
        finally:
            db.close()
            temp_path.unlink(missing_ok=True)
        register_imported_storage(owner_id, project_id, result)
        return result.__dict__

    @app.post("/api/v1/import/file")
    async def import_new_article_file(
        request: Request,
        project_id: str,
        file: UploadFile = File(...),
    ):
        _config, project_dir = pm.load_project(project_id)
        temp_path = _temporary_upload_path(file.filename, "article.pdf")
        written = await _persist_upload(file, temp_path, runtime.max_upload_mb)
        validate_uploaded_file(
            temp_path,
            file.filename or "article.pdf",
            file.content_type,
            allowed_extensions=ARTICLE_EXTENSIONS,
        )
        owner_id = storage_owner(request)
        require_storage_capacity(owner_id, written)
        db = pm.get_database(project_id)
        try:
            result = FileImporter().import_file(db, project_dir, temp_path, article_id=None)
            db.commit()
        finally:
            db.close()
            temp_path.unlink(missing_ok=True)
        register_imported_storage(owner_id, project_id, result)
        return {
            "article_id": result.article_id,
            "resource_id": result.resource_id,
            "resource_type": result.resource_type.value if result.resource_type else "",
            "file_name": result.file_name,
            "status": result.status,
        }

    @app.get("/api/v1/articles/{article_id}/session")
    def get_session(article_id: str, project_id: str):
        return service.ensure_session(project_id, article_id)

    @app.post("/api/v1/articles/{article_id}/discover")
    def discover(article_id: str, project_id: str):
        task_id = tasks.submit(
            project_id, article_id, "resource_discovery",
            "resource_discovery",
        )
        return {"task_id": task_id}

    @app.post("/api/v1/articles/{article_id}/rediscover-with-rules")
    def rediscover_with_rules(article_id: str, project_id: str):
        task_id = tasks.submit(
            project_id, article_id, "resource_discovery",
            "resource_discovery",
        )
        return {"task_id": task_id}

    @app.get("/api/v1/articles/{article_id}/elements")
    def elements(article_id: str, project_id: str):
        return service.list_elements(project_id, article_id)

    @app.get("/api/v1/articles/{article_id}/resource-score-explanations")
    def resource_score_explanations(article_id: str, project_id: str):
        return service.score_explanations(project_id, article_id)

    @app.get("/api/v1/articles/{article_id}/pre-extraction-rules")
    def pre_extraction_rules(article_id: str, project_id: str):
        return service.pre_extraction_rules(project_id, article_id)

    @app.post("/api/v1/articles/{article_id}/pre-extraction-rules")
    def add_pre_extraction_rule(article_id: str, request: PreExtractionRuleRequest, project_id: str):
        return service.add_pre_extraction_rule(project_id, article_id, request.model_dump())

    @app.get("/api/v1/articles/{article_id}/table-rule-preflight")
    def table_rule_preflight(article_id: str, project_id: str):
        return service.table_rule_preflight(project_id, article_id)

    @app.post("/api/v1/articles/{article_id}/table-rule-preflight/assist")
    def assist_table_rule_preflight(article_id: str, request: TableRuleAssistRequest):
        return service.assist_table_rule_preflight(request.project_id, article_id, request.source_headers)

    @app.get("/api/v1/model-capabilities")
    def model_capabilities():
        return service.model_capabilities()

    @app.get("/api/v1/mapping-knowledge/search")
    def mapping_knowledge_search(project_id: str, q: str, limit: int = Query(20, ge=1, le=100)):
        db = pm.get_database(project_id)
        try:
            return MappingKnowledgeService(db).search(q, limit)
        finally:
            db.close()

    @app.get("/api/v1/mapping-knowledge/releases/current")
    def current_mapping_knowledge_release(project_id: str):
        db = pm.get_database(project_id)
        try:
            return MappingKnowledgeService(db).current_release()
        finally:
            db.close()

    @app.post("/api/v1/admin/mapping-knowledge/sync")
    def sync_mapping_knowledge(project_id: str, request: Request):
        db = pm.get_database(project_id)
        try:
            return MappingKnowledgeService(db).stage_builtin_sync(actor_id(request))
        finally:
            db.close()

    @app.get("/api/v1/admin/mapping-knowledge/imports/{import_id}/diff")
    def mapping_knowledge_import_diff(import_id: str, project_id: str):
        db = pm.get_database(project_id)
        try:
            return MappingKnowledgeService(db).import_diff(import_id)
        finally:
            db.close()

    @app.post("/api/v1/admin/mapping-knowledge/imports/{import_id}/publish")
    def publish_mapping_knowledge_import(import_id: str, project_id: str):
        db = pm.get_database(project_id)
        try:
            return MappingKnowledgeService(db).publish_import(import_id)
        finally:
            db.close()

    @app.post("/api/v1/model-benchmarks/header-mapping")
    def header_mapping_benchmark(project_id: str):
        task_id = tasks.submit(
            project_id, None, "header_mapping_benchmark",
            "header_mapping_benchmark",
        )
        return {"task_id": task_id}

    @app.post("/api/v1/articles/{article_id}/table-rule-preflight/confirm")
    def confirm_table_rule_preflight(article_id: str, request: TableRuleConfirmRequest):
        return service.confirm_table_rule_preflight(request.project_id, article_id, request.rules, request.scope)

    @app.get("/api/v1/articles/{article_id}/target-headers")
    def article_target_headers(article_id: str, project_id: str):
        return service.target_headers(project_id, article_id)

    @app.post("/api/v1/articles/{article_id}/standardize-tables")
    def standardize_tables(article_id: str, request: TableStandardizeRequest):
        task_id = tasks.submit(
            request.project_id, article_id, "table_standardization",
            "table_standardization", {"use_llm": request.use_llm},
        )
        return {"task_id": task_id}

    @app.get("/api/v1/articles/{article_id}/paragraph-cues")
    def paragraph_cues(article_id: str, project_id: str):
        return service.paragraph_cues(project_id, article_id)

    @app.post("/api/v1/articles/{article_id}/paragraph-cues/refresh")
    def refresh_paragraph_cues(article_id: str, project_id: str):
        return service.refresh_paragraph_cues(project_id, article_id)

    @app.post("/api/v1/articles/{article_id}/selections")
    def selections(article_id: str, request: SelectionRequest, project_id: str):
        return service.set_selections(project_id, article_id, request.element_ids)

    @app.post("/api/v1/articles/{article_id}/elements/manual")
    def manual_element(article_id: str, request: ManualElementRequest, project_id: str):
        return service.add_manual_element(project_id, article_id, request.resource_id, request.page_number, request.bbox, request.element_type, request.note)

    @app.post("/api/v1/articles/{article_id}/elements/hit-test")
    def hit_test_elements(article_id: str, request: ElementHitTestRequest, project_id: str):
        return service.hit_test_elements(project_id, article_id, request.resource_id, request.page_number, request.bbox)

    @app.get("/api/v1/articles/{article_id}/resources")
    def resources(article_id: str, project_id: str):
        return repo.resources(project_id, article_id)

    def resolve_managed_project_file(
        project_id: str,
        stored_path: str,
        *,
        missing_detail: str,
    ) -> Path:
        """Resolve one database path without allowing reads outside its workspace."""

        _config, project_dir = pm.load_project(project_id)
        project_root = project_dir.expanduser().resolve()
        raw_path = Path(stored_path or "").expanduser()
        path = (raw_path if raw_path.is_absolute() else project_root / raw_path).resolve()
        if path != project_root and project_root not in path.parents:
            raise HTTPException(403, "Resource path is outside the managed project directory")
        if not path.is_file():
            raise HTTPException(404, missing_detail)
        return path

    def resolve_resource_file(project_id: str, resource_id: str) -> Path:
        db = pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT r.local_path FROM resources r JOIN articles a ON a.article_id=r.article_id
                   WHERE a.project_id=? AND r.resource_id=?""", (project_id, resource_id),
            )
            if not row:
                raise HTTPException(404, "Resource not found")
            return resolve_managed_project_file(
                project_id,
                str(row["local_path"] or ""),
                missing_detail="Resource file missing",
            )
        finally:
            db.close()

    @app.get("/api/v1/resources/{resource_id}/pdf")
    def resource_pdf(resource_id: str, project_id: str):
        path = resolve_resource_file(project_id, resource_id)
        if path.suffix.lower() != ".pdf":
            raise HTTPException(400, "Resource is not a PDF")
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    @app.get("/api/v1/elements/{element_id}/preview")
    def element_preview(element_id: str, project_id: str):
        db = pm.get_database(project_id)
        try:
            row = db.fetch_one("SELECT preview_path FROM document_elements WHERE project_id=? AND element_id=?", (project_id, element_id))
            if not row or not row["preview_path"]:
                raise HTTPException(404, "Preview not found")
            path = resolve_managed_project_file(
                project_id,
                str(row["preview_path"]),
                missing_detail="Preview file missing",
            )
            return FileResponse(path)
        finally:
            db.close()

    @app.patch("/api/v1/elements/{element_id}")
    def update_element(element_id: str, request: ElementUpdateRequest, project_id: str):
        return service.update_element(project_id, element_id, request.model_dump(exclude_none=True))

    @app.patch("/api/v1/elements/{element_id}/standard-table")
    def update_standard_table(element_id: str, request: StandardTableUpdateRequest):
        return service.update_standard_table(
            request.project_id,
            element_id,
            request.headers,
            request.rows,
            request.edit_reason,
        )

    @app.post("/api/v1/elements/{element_id}/restandardize-table")
    def restandardize_table(element_id: str, request: RestandardizeTableRequest):
        return service.restandardize_table(request.project_id, element_id, request.mode)

    @app.post("/api/v1/elements/{element_id}/teach")
    def teach_element(element_id: str, request: ElementTeachRequest, project_id: str):
        return service.teach_element(project_id, element_id, request.label, request.note)

    @app.post("/api/v1/elements/{element_id}/merge-selection")
    def merge_selection(element_id: str, request: ElementMergeSelectionRequest, project_id: str):
        return service.merge_selection(project_id, element_id, request.page_number, request.bbox)

    @app.delete("/api/v1/elements/{element_id}")
    def delete_element(element_id: str, project_id: str):
        return service.delete_element(project_id, element_id)

    @app.post("/api/v1/extraction-batches")
    def create_batch(request: ExtractionRequest):
        task_id = tasks.submit(
            request.project_id, request.article_id, "candidate_extraction",
            "candidate_extraction", {"use_llm": request.use_llm},
        )
        return {"task_id": task_id}

    @app.post("/api/v1/articles/{article_id}/extract-tables")
    def extract_tables(article_id: str, request: ExtractionRequest):
        task_id = tasks.submit(
            request.project_id, article_id, "table_extraction",
            "table_extraction", {"use_llm": request.use_llm},
        )
        return {"task_id": task_id}

    @app.post("/api/v1/articles/{article_id}/extract-paragraphs")
    def extract_paragraphs(article_id: str, request: ParagraphExtractionRequest):
        task_id = tasks.submit(
            request.project_id, article_id, "paragraph_extraction",
            "paragraph_extraction",
            {"element_ids": request.element_ids, "use_llm": request.use_llm},
        )
        return {"task_id": task_id}

    @app.post("/api/v1/articles/{article_id}/merge-candidates")
    def merge_candidates(article_id: str, request: ExtractionRequest):
        return service.merge_candidates(request.project_id, article_id)

    @app.post("/api/v1/extraction-batches/{batch_id}/reapply-rules")
    def reapply_rules(batch_id: str, request: ReapplyRulesRequest):
        return service.reapply_rules(request.project_id, batch_id)

    @app.post("/api/v1/extraction-batches/{batch_id}/validate-evidence")
    def validate_evidence(batch_id: str, request: ReapplyRulesRequest):
        return service.validate_batch_evidence(request.project_id, batch_id)

    @app.get("/api/v1/extraction-batches/{batch_id}/field-mappings")
    def batch_field_mappings(batch_id: str, project_id: str):
        return service.batch_field_mappings(project_id, batch_id)

    @app.get("/api/v1/extraction-batches/{batch_id}/quality-slices")
    def quality_slices(batch_id: str, project_id: str, source_type: str = ""):
        return service.quality_slice(project_id, batch_id, source_type)

    @app.post("/api/v1/articles/{article_id}/elements/{element_id}/reextract")
    def reextract_element(article_id: str, element_id: str, request: ParagraphExtractionRequest):
        task_id = tasks.submit(
            request.project_id, article_id, "element_reextract",
            "element_reextract",
            {"element_id": element_id, "use_llm": request.use_llm},
        )
        return {"task_id": task_id}

    @app.get("/api/v1/extraction-batches/{batch_id}/records")
    def batch_records(batch_id: str, project_id: str):
        return service.batch_records(project_id, batch_id)

    @app.patch("/api/v1/candidate-cells/{cell_id}")
    def update_cell(cell_id: str, request: CellUpdateRequest, project_id: str):
        return service.update_cell(project_id, cell_id, request.model_dump(exclude_none=True))

    @app.post("/api/v1/candidate-cells/manual-image")
    def manual_image_cell(request: ManualImageCellRequest):
        return service.upsert_manual_image_cell(
            request.project_id,
            request.candidate_record_id,
            request.element_id,
            request.target_header,
            request.value,
            request.evidence_note,
        )

    @app.post("/api/v1/candidate-records/merge")
    def merge_records(request: MergeRequest):
        return service.merge_records(request.project_id, request.record_ids)

    @app.post("/api/v1/candidate-records/manual")
    def manual_candidate_record(request: ManualCandidateRecordRequest):
        return service.create_manual_candidate_record(
            request.project_id,
            request.article_id,
            request.batch_id,
            request.sample_id,
            request.values,
        )

    @app.delete("/api/v1/candidate-records/{record_id}")
    def delete_candidate_record(record_id: str, project_id: str):
        return service.delete_candidate_record(project_id, record_id)

    @app.delete("/api/v1/candidate-cells/{cell_id}")
    def delete_candidate_cell(cell_id: str, project_id: str):
        return service.delete_candidate_cell(project_id, cell_id)

    @app.post("/api/v1/candidate-cells/{cell_id}/confirm")
    def confirm_cell(cell_id: str, request: CellConfirmRequest, project_id: str):
        return service.confirm_cell_mapping(
            project_id, cell_id,
            target_field=request.target_field, target_unit=request.target_unit,
            formula=request.formula, conversion_factor=request.conversion_factor,
        )

    @app.post("/api/v1/candidate-cells/{cell_id}/suggest-conversion")
    def suggest_conversion(cell_id: str, project_id: str):
        return service.suggest_conversion(project_id, cell_id)

    @app.get("/api/v1/articles/{article_id}/candidate-records")
    def article_records(article_id: str, project_id: str):
        return service.article_candidate_records(project_id, article_id)

    @app.post("/api/v1/candidate-records/{record_id}/approve")
    def approve_record(record_id: str, project_id: str):
        return service.approve_record(project_id, record_id)

    @app.post("/api/v1/candidate-records/{record_id}/reject")
    def reject_record(record_id: str, project_id: str):
        return service.reject_record(project_id, record_id)

    @app.post("/api/v1/articles/{article_id}/batch-approve")
    def batch_approve(article_id: str, request: BatchApproveRequest):
        return service.batch_approve_records(request.project_id, request.record_ids)

    @app.post("/api/v1/articles/{article_id}/batch-confirm")
    def batch_confirm_mappings(article_id: str, request: BatchConfirmRequest):
        return service.batch_confirm_mappings(request.project_id, request.article_id, request.confirmations)

    @app.delete("/api/v1/rules/{rule_id}")
    def delete_rule(rule_id: str, project_id: str, rule_type: str = "mapping"):
        return service.delete_rule(project_id, rule_id, rule_type)

    @app.post("/api/v1/articles/{article_id}/manual-fill")
    def manual_fill(article_id: str, request: ManualFillRequest, project_id: str):
        return service.manual_fill(
            project_id, article_id,
            request.resource_id, request.page_number, request.bbox,
            request.target_header, request.value, request.unit, request.explanation,
        )

    @app.post("/api/v1/articles/{article_id}/finalize")
    def finalize(article_id: str, request: FinalizeRequest):
        return service.finalize_standardized(request.project_id, request.article_id)

    @app.get("/api/v1/trace-records")
    def trace_records(
        project_id: str,
        article_id: str | None = None,
        q: str = "",
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        return service.trace_records(project_id, article_id, q, limit, offset)

    @app.get("/api/v1/trace-records/{record_id}")
    def trace_record_detail(record_id: str, project_id: str):
        try:
            return service.trace_record_detail(project_id, record_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/v1/articles/{article_id}/trace")
    def article_trace(article_id: str, project_id: str):
        return service.article_trace(project_id, article_id)

    @app.get("/api/v1/articles/{article_id}/export")
    def export_article_legacy(article_id: str, project_id: str, format: str = "csv", output_dir: str | None = None):
        """Compatibility endpoint; new clients use the audited POST variant."""

        return service.export_article(project_id, article_id, format, output_dir)

    @app.post("/api/v1/articles/{article_id}/export")
    def export_article(article_id: str, request: ExportArticleRequest):
        return service.export_article(
            request.project_id,
            article_id,
            request.format,
            request.output_dir or None,
        )

    @app.get("/api/v1/export-jobs")
    def export_jobs(
        project_id: str,
        article_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        return service.export_jobs(project_id, article_id, limit)

    @app.get("/api/v1/export-jobs/{job_id}/download")
    def download_export(job_id: str, project_id: str):
        try:
            job = service.export_job(project_id, job_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        if job.get("status") != "completed":
            raise HTTPException(409, "导出任务尚未完成，当前文件不可下载。")
        path = Path(str(job.get("output_path") or "")).expanduser().resolve()
        if runtime.profile.value != "development":
            root = runtime.effective_export_root.expanduser().resolve()
            if path != root and root not in path.parents:
                raise HTTPException(403, "导出文件不在服务器受管目录内。")
        if not path.is_file():
            raise HTTPException(404, "导出文件已不存在，请重新生成。")
        media_types = {
            ".csv": "text/csv; charset=utf-8",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        media_type = media_types.get(path.suffix.lower())
        if media_type is None:
            raise HTTPException(415, "该导出文件格式不允许通过浏览器下载。")
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/api/v1/reviews")
    def reviews(project_id: str):
        return repo.reviews(project_id)

    @app.get("/api/v1/rules")
    def rules(
        project_id: str,
        q: str = "",
        scope: str = "",
        status: str = "confirmed",
        chemical_form: str = "",
        submitter: str = "",
        limit: int = 500,
    ):
        return rule_governance.list_rules(
            project_id,
            q=q,
            scope=scope,
            status=status,
            chemical_form=chemical_form,
            submitter=submitter,
            limit=limit,
        )

    @app.get("/api/v1/rules/{rule_id}")
    def rule_detail(rule_id: str, project_id: str):
        return rule_governance.rule(project_id, rule_id)

    @app.get("/api/v1/rules/{rule_id}/history")
    def rule_history(rule_id: str, project_id: str):
        return {"items": rule_governance.rule_history(project_id, rule_id)}

    @app.get("/api/v1/rule-submissions")
    def rule_submissions(
        request: Request,
        project_id: str,
        status: str = "",
        mine: bool = False,
        q: str = "",
        limit: int = 500,
    ):
        if not rule_governance.can_approve(actor_workspace_role(request), actor_roles(request)):
            mine = True
        return rule_governance.list_submissions(
            project_id,
            actor_id(request),
            status=status,
            mine=mine,
            q=q,
            limit=limit,
        )

    @app.post("/api/v1/rule-submissions")
    def create_rule_submission(request: Request, payload: RuleSubmissionRequest, project_id: str):
        return rule_governance.create_submission(
            project_id,
            actor_id(request),
            actor_workspace_role(request),
            actor_roles(request),
            payload.model_dump(),
        )

    @app.patch("/api/v1/rule-submissions/{submission_id}")
    def update_rule_submission(
        submission_id: str,
        request: Request,
        payload: RuleSubmissionPatchRequest,
        project_id: str,
    ):
        return rule_governance.update_submission(
            project_id,
            submission_id,
            actor_id(request),
            payload.model_dump(exclude_none=True),
        )

    @app.post("/api/v1/rule-submissions/{submission_id}/submit")
    def submit_rule_submission(submission_id: str, request: Request, project_id: str):
        return rule_governance.submit(project_id, submission_id, actor_id(request))

    @app.post("/api/v1/rule-imports")
    async def import_rules(
        request: Request,
        project_id: str,
        file: UploadFile = File(...),
    ):
        safe_name = _safe_upload_name(file.filename, "mapping-rules.csv")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / safe_name
            await _persist_upload(file, temp_path, runtime.max_upload_mb)
            validate_uploaded_file(
                temp_path,
                safe_name,
                file.content_type,
                allowed_extensions=HEADER_EXTENSIONS,
            )
            return rule_governance.import_file(
                project_id,
                actor_id(request),
                actor_workspace_role(request),
                actor_roles(request),
                temp_path,
                safe_name,
            )

    @app.get("/api/v1/rule-imports/{import_id}/preview")
    def rule_import_preview(import_id: str, request: Request, project_id: str):
        return rule_governance.import_preview(
            project_id,
            import_id,
            actor_id=actor_id(request),
            can_view_all=rule_governance.can_approve(
                actor_workspace_role(request),
                actor_roles(request),
            ),
        )

    @app.get("/api/v1/admin/rule-submissions")
    def admin_rule_submissions(
        request: Request,
        project_id: str,
        status: str = "pending",
        q: str = "",
        limit: int = 500,
    ):
        if not rule_governance.can_approve(actor_workspace_role(request), actor_roles(request)):
            raise PermissionError("只有组织 Owner 或平台管理员可以查看审批队列。")
        return rule_governance.list_submissions(
            project_id,
            actor_id(request),
            status=status,
            q=q,
            limit=limit,
        )

    @app.post("/api/v1/admin/rule-submissions/{submission_id}/approve")
    def approve_rule_submission(
        submission_id: str,
        request: Request,
        payload: RuleReviewRequest,
        project_id: str,
    ):
        return rule_governance.approve(
            project_id,
            submission_id,
            actor_id(request),
            actor_workspace_role(request),
            actor_roles(request),
            payload.comment,
        )

    @app.post("/api/v1/admin/rule-submissions/{submission_id}/reject")
    def reject_rule_submission(
        submission_id: str,
        request: Request,
        payload: RuleReviewRequest,
        project_id: str,
    ):
        return rule_governance.reject(
            project_id,
            submission_id,
            actor_id(request),
            actor_workspace_role(request),
            actor_roles(request),
            payload.comment,
        )

    @app.get("/api/v1/rule-memory")
    def rule_memory(project_id: str, article_id: str | None = None):
        return service.rule_memory(project_id, article_id)

    @app.patch("/api/v1/rule-memory/{rule_id}")
    def update_rule_memory(rule_id: str, request: RuleMemoryPatchRequest, project_id: str):
        return service.update_rule_memory(project_id, rule_id, request.model_dump(exclude_none=True))

    @app.delete("/api/v1/rule-memory/{rule_id}")
    def delete_rule_memory(rule_id: str, project_id: str):
        return service.delete_rule(project_id, rule_id, "extraction")

    @app.get("/api/v1/standardized-records")
    def standardized(project_id: str):
        return repo.standardized(project_id)

    @app.get("/api/v1/costs")
    def costs(project_id: str):
        return repo.costs(project_id)

    @app.get("/api/v1/model-setup")
    def model_setup():
        return _model_setup_status(load_config())

    @app.post("/api/v1/model-setup/test")
    def test_model_setup(request: ModelSetupRequest):
        config = load_config()
        try:
            provider, provider_name, model_name = _resolve_model_setup_provider(config, request.provider_preset, request.model_id, request.base_url)
            result = _test_provider_config(provider, request.api_key, model_name)
            result.update({
                "success": True,
                "provider": provider_name,
                "provider_preset": _setup_preset_for_provider(provider_name),
                "model": model_name,
                "message": result.get("message") or "模型调用成功，尚未保存",
            })
            return result
        except Exception as e:
            return {
                "success": False,
                "provider": "",
                "provider_preset": request.provider_preset,
                "model": request.model_id,
                "models": [],
                "message": str(e),
                "last_tested_at": datetime.now().isoformat(timespec="seconds"),
            }

    @app.put("/api/v1/model-setup")
    def save_model_setup(request: ModelSetupRequest):
        config = load_config()
        provider, provider_name, model_name = _resolve_model_setup_provider(config, request.provider_preset, request.model_id, request.base_url)
        test_result = _test_provider_config(provider, request.api_key, model_name)
        if not test_result.get("success"):
            raise HTTPException(400, test_result.get("message") or "模型测试失败，未保存")

        if request.api_key:
            if request.provider_preset == "opencode-go":
                sibling_names = ("opencode-go-openai", "opencode-go-anthropic")
            elif request.provider_preset == "deepseek":
                sibling_names = ("deepseek-openai", "deepseek-anthropic")
            elif request.provider_preset == "xiaomi":
                sibling_names = ("xiaomi", "xiaomi-anthropic")
            else:
                sibling_names = (provider_name,)
            for sibling_name in sibling_names:
                if config.get_provider(sibling_name):
                    _save_provider_secret(config, sibling_name, request.api_key)

        _apply_single_model_routes(config, provider_name, model_name)
        config.ui_preferences = dict(config.ui_preferences or {})
        config.ui_preferences["model_setup"] = {
            "success": True,
            "provider": provider_name,
            "provider_preset": _setup_preset_for_provider(provider_name),
            "model": model_name,
            "message": "已保存并应用到全部智能体文本任务",
            "last_tested_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_config(config)
        return _model_setup_status(config)

    @app.get("/api/v1/settings")
    def settings():
        config = load_config()
        default = config.get_task_model("_default")
        vision = config.task_models.get("figure_extraction")
        providers = [_provider_public(provider) for provider in config.providers]
        if default:
            providers.sort(key=lambda item: 0 if item["name"] == default.provider else 1)
        return {
            "default_provider": default.provider if default else "",
            "default_model": default.model if default else "",
            "vision_provider": vision.provider if vision else "",
            "vision_model": vision.model if vision else "",
            "export_dir": (
                str(runtime.effective_export_root)
                if runtime.profile.value != "development"
                else config.ui_preferences.get("export_dir", "")
            ),
            "task_models": {
                name: {
                    "provider": task.provider,
                    "model": task.model,
                    "temperature": task.temperature,
                    "max_tokens": task.max_tokens,
                    "label": TASK_ROUTE_META.get(name, name),
                }
                for name, task in config.task_models.items()
            },
            "task_route_meta": TASK_ROUTE_META,
            "providers": providers,
        }

    @app.get("/api/v1/provider-presets")
    def provider_preset_list():
        return [_provider_public(provider) for provider in provider_presets()]

    @app.put("/api/v1/settings")
    def update_settings(request: SettingsUpdateRequest):
        config = load_config()
        if request.providers:
            built_in = {provider.name for provider in config.providers if not provider.is_custom}
            updated: list[ProviderConfig] = []
            seen: set[str] = set()
            existing_by_name = {provider.name: provider for provider in config.providers}
            for item in request.providers:
                provider = _provider_from_payload(item, existing_by_name.get(str(item.get("name") or "")))
                if not provider.name:
                    continue
                if provider.name in built_in:
                    provider.is_custom = False
                updated.append(provider)
                seen.add(provider.name)
            for provider in config.providers:
                if provider.name not in seen and not provider.is_custom:
                    updated.append(provider)
            config.providers = updated

        default_provider_name, default_model_name = _normalize_provider_model(request.default_provider, request.default_model)
        default_provider_config = config.get_provider(default_provider_name)
        if not default_provider_config:
            raise HTTPException(400, "Unknown default provider/model")
        _ensure_route_model(config, default_provider_name, default_model_name)
        if request.vision_provider or request.vision_model:
            vision_provider_name, vision_model_name = _normalize_provider_model(request.vision_provider, request.vision_model)
            vision_provider_config = config.get_provider(vision_provider_name)
            _ensure_route_model(config, vision_provider_name, vision_model_name)
            vision_model_config = next(
                (model for model in vision_provider_config.models if model.name == vision_model_name),
                None,
            ) if vision_provider_config else None
            if not vision_model_config or not vision_model_config.supports_vision:
                raise HTTPException(400, "The selected figure-extraction model does not support vision")
        current = config.get_task_model("_default")
        next_task_models = dict(config.task_models)
        next_task_models["_default"] = TaskModelConfig(
            provider=default_provider_name, model=default_model_name,
            temperature=current.temperature if current else 0.1, max_tokens=current.max_tokens if current else 4096,
        )
        for name, task_data in request.task_models.items():
            provider_name, model_name = _normalize_provider_model(str(task_data.get("provider") or ""), str(task_data.get("model") or ""))
            if not provider_name or not model_name:
                continue
            if not config.get_provider(provider_name):
                raise HTTPException(400, f"Unknown provider for task {name}: {provider_name}")
            _ensure_route_model(config, provider_name, model_name)
            next_task_models[name] = TaskModelConfig(
                provider=provider_name,
                model=model_name,
                temperature=float(task_data.get("temperature", next_task_models.get(name, current).temperature if next_task_models.get(name, current) else 0.1)),
                max_tokens=int(task_data.get("max_tokens", next_task_models.get(name, current).max_tokens if next_task_models.get(name, current) else 4096)),
            )
        if request.vision_provider and request.vision_model:
            vision_provider_name, vision_model_name = _normalize_provider_model(request.vision_provider, request.vision_model)
            next_task_models["figure_extraction"] = TaskModelConfig(
                provider=vision_provider_name,
                model=vision_model_name,
                temperature=0.0,
                max_tokens=5000,
            )
        else:
            next_task_models.pop("figure_extraction", None)
        config.task_models = next_task_models
        # Save API keys
        if request.api_keys:
            for provider_name, api_key in request.api_keys.items():
                if api_key and config.get_provider(provider_name):
                    _save_provider_secret(config, provider_name, api_key)
        save_config(config)
        return {"status": "saved"}

    @app.post("/api/v1/providers/{name}/models")
    def add_provider_model(name: str, request: ProviderModelRequest):
        config = load_config()
        provider_name, model_name = _normalize_provider_model(name, request.name)
        provider = config.get_provider(provider_name)
        if not provider:
            raise HTTPException(404, "Provider not found")
        _upsert_model(provider, ModelConfig(
            name=model_name,
            display_name=request.display_name or model_name,
            max_tokens=request.max_tokens,
            supports_vision=request.supports_vision,
        ))
        save_config(config)
        return _provider_public(provider)

    @app.delete("/api/v1/providers/{name}")
    def delete_provider(name: str):
        config = load_config()
        provider = config.get_provider(name)
        if not provider:
            raise HTTPException(404, "Provider not found")
        if not provider.is_custom:
            raise HTTPException(400, "Only custom providers can be deleted")
        config.providers = [item for item in config.providers if item.name != name]
        for task_name, task in list(config.task_models.items()):
            if task.provider == name:
                config.task_models.pop(task_name, None)
        save_config(config)
        return {"status": "deleted"}

    @app.get("/api/v1/export-directory")
    def export_directory(project_id: str):
        if runtime.profile.value != "development":
            return {
                "path": str(runtime.effective_export_root),
                "is_default": True,
                "managed": True,
            }
        config = load_config()
        configured = config.ui_preferences.get("export_dir", "") if config.ui_preferences else ""
        if configured:
            path = Path(configured).expanduser()
            is_default = False
        else:
            _project_config, project_dir = pm.load_project(project_id)
            path = project_dir / "output"
            is_default = True
        return {"path": str(path), "is_default": is_default}

    @app.put("/api/v1/export-directory")
    def update_export_directory(request: ExportDirectoryRequest):
        if runtime.profile.value != "development":
            requested = Path(request.path).expanduser().resolve() if request.path.strip() else runtime.effective_export_root.resolve()
            root = runtime.effective_export_root.expanduser().resolve()
            if requested != root:
                raise HTTPException(409, "服务器导出目录由管理员统一配置，浏览器不能修改。")
            return {"path": str(root), "status": "managed"}
        config = load_config()
        config.ui_preferences = dict(config.ui_preferences or {})
        if request.path.strip():
            config.ui_preferences["export_dir"] = request.path.strip()
        else:
            config.ui_preferences.pop("export_dir", None)
        save_config(config)
        return {"path": config.ui_preferences.get("export_dir", ""), "status": "saved"}

    @app.post("/api/v1/export-directory/open")
    def open_export_directory(project_id: str, request: ExportDirectoryRequest):
        if runtime.profile.value != "development":
            raise HTTPException(409, "服务器目录不能从浏览器直接打开，请通过导出记录下载文件。")
        path_text = request.path.strip()
        if not path_text:
            _project_config, project_dir = pm.load_project(project_id)
            path = project_dir / "output"
        else:
            path = Path(path_text).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            import platform
            import subprocess
            system = platform.system()
            if system == "Darwin":
                subprocess.Popen(["open", str(path)])
            elif system == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            raise ValueError(f"Cannot open export directory: {exc}") from exc
        return {"path": str(path), "status": "opened"}

    @app.get("/api/v1/tasks/{task_id}")
    def task(task_id: str, project_id: str):
        db = pm.get_database(project_id)
        try:
            row = db.fetch_one("SELECT * FROM workflow_tasks WHERE project_id=? AND task_id=?", (project_id, task_id))
            if not row:
                raise HTTPException(404, "Task not found")
            data = dict(row)
            data["result"] = json.loads(data.pop("result_json") or "{}")
            return data
        finally:
            db.close()

    @app.get("/api/v1/tasks/{task_id}/events")
    async def task_events(task_id: str, project_id: str, after: int = Query(0, ge=0)):
        async def stream():
            cursor = after
            while True:
                db = pm.get_database(project_id)
                try:
                    events = db.fetch_all(
                        "SELECT * FROM workflow_task_events WHERE task_id=? AND event_id>? ORDER BY event_id",
                        (task_id, cursor),
                    )
                    task_row = db.fetch_one("SELECT status FROM workflow_tasks WHERE task_id=?", (task_id,))
                finally:
                    db.close()
                for event_row in events:
                    event = dict(event_row)
                    cursor = event["event_id"]
                    event["details"] = json.loads(event.pop("details_json") or "{}")
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if task_row and task_row["status"] in {"completed", "failed"}:
                    break
                yield ": keepalive\n\n"
                await asyncio.sleep(0.35)
        return StreamingResponse(stream(), media_type="text/event-stream")

    # Source checkouts keep the Vite bundle under ``web/dist`` while the
    # production image copies it to a stable, read-only application path.
    web_dist = Path(
        os.environ.get(
            "GEOCHEM_WEB_DIST",
            str(Path(__file__).resolve().parents[3] / "web" / "dist"),
        )
    ).expanduser()
    if web_dist.exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")

        @app.get("/{path:path}")
        def spa(path: str):
            candidate = (web_dist / path).resolve()
            if path and web_dist.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html")

    return app


app = create_app()
