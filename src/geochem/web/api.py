"""FastAPI application exposing the GeoChem local workspace."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..core.project import ProjectManager
from ..core.config import TaskModelConfig, load_config, save_config
from ..curation.header_config_importer import HeaderConfigImporter
from ..curation.target_headers import TargetHeaderBuilder
from ..ingestion.file_importer import FileImporter
from ..workflow import WorkflowRunner
from ..workbench_service import WorkbenchService


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


class ExtractionRequest(BaseModel):
    project_id: str
    article_id: str
    use_llm: bool = True


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

class SettingsUpdateRequest(BaseModel):
    default_provider: str
    default_model: str
    vision_provider: str = ""
    vision_model: str = ""
    api_keys: dict[str, str] = Field(default_factory=dict)


class ExportDirectoryRequest(BaseModel):
    path: str = ""


class TaskManager:
    """Small persistent thread-backed task runner for one local workspace."""

    def __init__(self, pm: ProjectManager):
        self.pm = pm
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="geochem-web")

    def submit(
        self,
        project_id: str,
        article_id: str | None,
        task_type: str,
        operation: Callable[[Callable[[str, float, dict[str, Any]], None]], Any],
    ) -> str:
        db = self.pm.get_database(project_id)
        try:
            active = db.fetch_one(
                """SELECT task_id FROM workflow_tasks
                   WHERE project_id = ? AND COALESCE(article_id, '') = COALESCE(?, '')
                     AND task_type = ? AND status IN ('pending', 'running')
                   ORDER BY created_at DESC LIMIT 1""",
                (project_id, article_id, task_type),
            )
            if active:
                return active["task_id"]
            task_id = self._next_task_id(db)
            now = datetime.now().isoformat()
            db.execute(
                """INSERT INTO workflow_tasks
                   (task_id, project_id, article_id, task_type, status, progress, message, created_at)
                   VALUES (?, ?, ?, ?, 'pending', 0, '等待执行', ?)""",
                (task_id, project_id, article_id, task_type, now),
            )
            db.commit()
        finally:
            db.close()
        self.pool.submit(self._run, task_id, project_id, operation)
        return task_id

    def _run(self, task_id: str, project_id: str, operation) -> None:
        self._update(project_id, task_id, "running", 0.01, "任务已启动")

        def progress(message: str, value: float, details: dict[str, Any] | None = None) -> None:
            self._update(project_id, task_id, "running", value, message, details or {})

        try:
            result = operation(progress)
            self._update(project_id, task_id, "completed", 1.0, "任务完成", result=result)
        except Exception as exc:
            self._update(project_id, task_id, "failed", 1.0, f"任务失败: {exc}", error=str(exc))

    def _update(
        self, project_id: str, task_id: str, status: str, progress: float, message: str,
        details: dict[str, Any] | None = None, result: Any = None, error: str = "",
    ) -> None:
        db = self.pm.get_database(project_id)
        try:
            now = datetime.now().isoformat()
            db.execute(
                """UPDATE workflow_tasks SET status = ?, progress = ?, message = ?,
                   result_json = ?, error_message = ?, started_at = COALESCE(started_at, ?),
                   finished_at = CASE WHEN ? IN ('completed','failed') THEN ? ELSE finished_at END
                   WHERE task_id = ?""",
                (status, max(0.0, min(1.0, progress)), message, json.dumps(result or {}, ensure_ascii=False), error, now, status, now, task_id),
            )
            db.execute(
                """INSERT INTO workflow_task_events
                   (task_id, level, message, progress, details_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task_id, "ERROR" if status == "failed" else "INFO", message, progress, json.dumps(details or {}, ensure_ascii=False), now),
            )
            db.commit()
        finally:
            db.close()

    def _next_task_id(self, db) -> str:
        row = db.fetch_one("SELECT task_id FROM workflow_tasks WHERE task_id LIKE 'TASK_%' ORDER BY task_id DESC LIMIT 1")
        current = int(row["task_id"].split("_")[-1]) if row else 0
        return f"TASK_{current + 1:07d}"


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


def create_app(project_manager: ProjectManager | None = None) -> FastAPI:
    pm = project_manager or ProjectManager()
    service = WorkbenchService(pm)
    workflow = WorkflowRunner(project_manager=pm)
    repo = WebRepository(pm)
    tasks = TaskManager(pm)
    app = FastAPI(title="GeoChem Local API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "time": datetime.now().isoformat()}

    @app.post("/api/v1/providers/{name}/test")
    def test_provider(name: str, request: ProviderTestRequest):
        from ..providers.openai_provider import OpenAIProvider
        from ..providers.anthropic_provider import AnthropicProvider
        from ..providers.google_provider import GoogleProvider
        from ..providers.zhipu_provider import ZhipuProvider
        from ..providers.ollama_provider import OllamaProvider

        # Load base_url from config for this provider
        cfg = load_config()
        prov_cfg = cfg.get_provider(name)
        base_url = prov_cfg.base_url if prov_cfg else None

        provider_map = {
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
            "xiaomi-anthropic": AnthropicProvider,
            "google": GoogleProvider,
            "zhipu": ZhipuProvider,
            "ollama": OllamaProvider,
        }
        cls = provider_map.get(name, OpenAIProvider)
        try:
            kwargs = {"api_key": request.api_key}
            if base_url:
                kwargs["base_url"] = base_url
            provider = cls(**kwargs)
            valid = provider.validate_api_key()
            models = provider.list_models() if valid else []
            return {"success": valid, "models": models}
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
    async def import_header_config(project_id: str, name: str = "", file: UploadFile = File(...)):
        _config, project_dir = pm.load_project(project_id)
        suffix = Path(file.filename or "headers.csv").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(await file.read())
            temp_path = Path(handle.name)
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
            lambda progress: workflow.confirm_browser_access(request.project_id, request.article_id, request.url),
        )
        return {"task_id": task_id}

    @app.post("/api/v1/articles/{article_id}/upload")
    async def upload_file(article_id: str, project_id: str, file: UploadFile = File(...)):
        _config, project_dir = pm.load_project(project_id)
        suffix = Path(file.filename or "upload.bin").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(await file.read())
            temp_path = Path(handle.name)
        db = pm.get_database(project_id)
        try:
            result = FileImporter().import_file(db, project_dir, temp_path, article_id=article_id)
            return result.__dict__
        finally:
            db.close()
            temp_path.unlink(missing_ok=True)

    @app.post("/api/v1/import/file")
    async def import_new_article_file(project_id: str, file: UploadFile = File(...)):
        _config, project_dir = pm.load_project(project_id)
        suffix = Path(file.filename or "article.pdf").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(await file.read())
            temp_path = Path(handle.name)
        db = pm.get_database(project_id)
        try:
            result = FileImporter().import_file(db, project_dir, temp_path, article_id=None)
            return {
                "article_id": result.article_id,
                "resource_id": result.resource_id,
                "resource_type": result.resource_type.value if result.resource_type else "",
                "file_name": result.file_name,
                "status": result.status,
            }
        finally:
            db.close()
            temp_path.unlink(missing_ok=True)

    @app.get("/api/v1/articles/{article_id}/session")
    def get_session(article_id: str, project_id: str):
        return service.ensure_session(project_id, article_id)

    @app.post("/api/v1/articles/{article_id}/discover")
    def discover(article_id: str, project_id: str):
        task_id = tasks.submit(
            project_id, article_id, "resource_discovery",
            lambda progress: service.discover_article(project_id, article_id, progress),
        )
        return {"task_id": task_id}

    @app.get("/api/v1/articles/{article_id}/elements")
    def elements(article_id: str, project_id: str):
        return service.list_elements(project_id, article_id)

    @app.post("/api/v1/articles/{article_id}/selections")
    def selections(article_id: str, request: SelectionRequest, project_id: str):
        return service.set_selections(project_id, article_id, request.element_ids)

    @app.post("/api/v1/articles/{article_id}/elements/manual")
    def manual_element(article_id: str, request: ManualElementRequest, project_id: str):
        return service.add_manual_element(project_id, article_id, request.resource_id, request.page_number, request.bbox, request.element_type, request.note)

    @app.get("/api/v1/articles/{article_id}/resources")
    def resources(article_id: str, project_id: str):
        return repo.resources(project_id, article_id)

    def resolve_resource_file(project_id: str, resource_id: str) -> Path:
        _config, project_dir = pm.load_project(project_id)
        db = pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT r.local_path FROM resources r JOIN articles a ON a.article_id=r.article_id
                   WHERE a.project_id=? AND r.resource_id=?""", (project_id, resource_id),
            )
            if not row:
                raise HTTPException(404, "Resource not found")
            path = Path(row["local_path"] or "")
            path = path if path.is_absolute() else project_dir / path
            if not path.exists():
                raise HTTPException(404, "Resource file missing")
            return path
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
            path = Path(row["preview_path"])
            if not path.exists():
                raise HTTPException(404, "Preview file missing")
            return FileResponse(path)
        finally:
            db.close()

    @app.patch("/api/v1/elements/{element_id}")
    def update_element(element_id: str, request: ElementUpdateRequest, project_id: str):
        return service.update_element(project_id, element_id, request.model_dump(exclude_none=True))

    @app.delete("/api/v1/elements/{element_id}")
    def delete_element(element_id: str, project_id: str):
        return service.delete_element(project_id, element_id)

    @app.post("/api/v1/extraction-batches")
    def create_batch(request: ExtractionRequest):
        task_id = tasks.submit(
            request.project_id, request.article_id, "candidate_extraction",
            lambda progress: service.create_extraction_batch(request.project_id, request.article_id, request.use_llm, progress),
        )
        return {"task_id": task_id}

    @app.get("/api/v1/extraction-batches/{batch_id}/records")
    def batch_records(batch_id: str, project_id: str):
        return service.batch_records(project_id, batch_id)

    @app.patch("/api/v1/candidate-cells/{cell_id}")
    def update_cell(cell_id: str, request: CellUpdateRequest, project_id: str):
        return service.update_cell(project_id, cell_id, request.model_dump(exclude_none=True))

    @app.post("/api/v1/candidate-records/merge")
    def merge_records(request: MergeRequest):
        return service.merge_records(request.project_id, request.record_ids)

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
    def export_article(article_id: str, project_id: str, format: str = "csv", output_dir: str | None = None):
        return service.export_article(project_id, article_id, format, output_dir)

    @app.get("/api/v1/reviews")
    def reviews(project_id: str):
        return repo.reviews(project_id)

    @app.get("/api/v1/rules")
    def rules(project_id: str):
        return repo.rules(project_id)

    @app.get("/api/v1/standardized-records")
    def standardized(project_id: str):
        return repo.standardized(project_id)

    @app.get("/api/v1/costs")
    def costs(project_id: str):
        return repo.costs(project_id)

    @app.get("/api/v1/settings")
    def settings():
        config = load_config()
        default = config.get_task_model("_default")
        vision = config.task_models.get("figure_extraction")
        providers = []
        for provider in config.providers:
            key_ref = provider.api_key or ""
            env_name = key_ref[2:-1] if key_ref.startswith("${") and key_ref.endswith("}") else ""
            providers.append({
                "name": provider.name,
                "display_name": provider.display_name or provider.name,
                "key_status": "configured" if env_name and os.environ.get(env_name) else "missing" if env_name else "inline" if key_ref else "not_configured",
                "env_name": env_name,
                "models": [{"name": model.name, "display_name": model.display_name or model.name, "supports_vision": model.supports_vision} for model in provider.models],
            })
        return {
            "default_provider": default.provider if default else "",
            "default_model": default.model if default else "",
            "vision_provider": vision.provider if vision else "",
            "vision_model": vision.model if vision else "",
            "export_dir": config.ui_preferences.get("export_dir", ""),
            "providers": providers,
        }

    @app.put("/api/v1/settings")
    def update_settings(request: SettingsUpdateRequest):
        config = load_config()
        default_provider_config = config.get_provider(request.default_provider)
        if not default_provider_config or request.default_model not in {model.name for model in default_provider_config.models}:
            raise HTTPException(400, "Unknown default provider/model")
        if request.vision_provider or request.vision_model:
            vision_provider_config = config.get_provider(request.vision_provider)
            vision_model_config = next(
                (model for model in vision_provider_config.models if model.name == request.vision_model),
                None,
            ) if vision_provider_config else None
            if not vision_model_config or not vision_model_config.supports_vision:
                raise HTTPException(400, "The selected figure-extraction model does not support vision")
        current = config.get_task_model("_default")
        config.task_models["_default"] = TaskModelConfig(
            provider=request.default_provider,
            model=request.default_model,
            temperature=current.temperature if current else 0.1,
            max_tokens=current.max_tokens if current else 4096,
        )
        if request.vision_provider and request.vision_model:
            config.task_models["figure_extraction"] = TaskModelConfig(
                provider=request.vision_provider,
                model=request.vision_model,
                temperature=0.0,
                max_tokens=5000,
            )
        else:
            config.task_models.pop("figure_extraction", None)
        # Save API keys
        if request.api_keys:
            for provider_name, api_key in request.api_keys.items():
                for provider in config.providers:
                    if provider.name == provider_name:
                        provider.api_key = api_key if api_key else None
                        break
        save_config(config, Path(os.environ.get("GEOCHEM_CONFIG", "config/settings.yaml")))
        return {"status": "saved"}

    @app.get("/api/v1/export-directory")
    def export_directory(project_id: str):
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
        config_path = Path(os.environ.get("GEOCHEM_CONFIG", "config/settings.yaml"))
        config = load_config(config_path)
        config.ui_preferences = dict(config.ui_preferences or {})
        if request.path.strip():
            config.ui_preferences["export_dir"] = request.path.strip()
        else:
            config.ui_preferences.pop("export_dir", None)
        save_config(config, config_path)
        return {"path": config.ui_preferences.get("export_dir", ""), "status": "saved"}

    @app.post("/api/v1/export-directory/open")
    def open_export_directory(project_id: str, request: ExportDirectoryRequest):
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

    web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
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
