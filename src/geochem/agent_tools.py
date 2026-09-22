"""Controlled project-object tools used by the conversational GeoChem agent.

The chat model never receives a database connection, SQL text, or arbitrary local
paths.  This registry is the narrow bridge between natural-language selection and
the governed project data model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import re
import time
from typing import Any, Callable, Literal
from uuid import uuid4

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .agent_models import PermissionLevel
from .content_security import ContentSecurityGateway
from .core.project import ProjectManager
from .workbench_service import WorkbenchService


EntityType = Literal["article", "header_config", "resource", "sample", "field", "rule", "export_job"]
ENTITY_TYPES: tuple[EntityType, ...] = ("article", "header_config", "resource", "sample", "field", "rule", "export_job")


class EntitySelectionInput(BaseModel):
    entity_type: str
    entity_id: str = ""
    query: str = ""


class ListEntitiesInput(BaseModel):
    entity_type: str = Field(default="article", description="article, header_config, resource, sample, field, rule or export_job")
    query: str = Field(default="", description="Optional title, DOI, identifier, sample name or field text")


class SelectEntityToolInput(BaseModel):
    entity_type: str = Field(description="The governed entity type")
    entity_id: str = Field(default="", description="Exact entity id when known")
    query: str = Field(default="", description="Natural-language name used only when entity_id is not known")


class ProvenanceQueryInput(BaseModel):
    question: str = Field(description="Question about a value, sample, field, source or calculation")


class StatisticsQueryInput(BaseModel):
    field: str = Field(default="", description="Target header to summarize; blank means record counts and completeness")
    operation: Literal["summary", "count", "missing"] = "summary"


class LiteratureSearchToolInput(BaseModel):
    query: str = Field(description="Scholarly topic, title words or geochemistry keywords")
    sort_mode: Literal["relevance", "latest"] = "relevance"
    limit: int = Field(default=8, ge=1, le=20)


class ImportArticleToolInput(BaseModel):
    source: str = Field(description="DOI or public URL supplied by the user")


class HeaderImportToolInput(BaseModel):
    suggested_name: str = Field(
        default="",
        description="Optional display name for the header configuration. The user still chooses the CSV/XLSX/XLS file in the UI.",
    )


class StartProcessingToolInput(BaseModel):
    article_id: str = Field(default="", description="Current article id; blank uses the selected article")


class WorkbenchHandoffToolInput(BaseModel):
    workbench_view: Literal["resources", "extract", "quality"] | None = Field(
        default=None,
        description="Stable workbench view. Prefer this over the legacy numeric step.",
    )
    workbench_step: int = Field(default=2, ge=1, le=4)
    workbench_stage: str = Field(default="", description="Optional exact inner workbench stage")
    reason: str = Field(default="", description="Why expert manual work is useful")


class CurationOperationInput(BaseModel):
    operation: Literal[
        "review_resources",
        "standardize_tables",
        "inspect_mappings",
        "extract_tables",
        "extract_paragraphs",
        "edit_image_values",
        "edit_candidates",
    ] = Field(description="The exact expert operation requested by the user")
    target_id: str = Field(default="", description="Optional resource, record, cell or table identifier")
    reason: str = Field(default="", description="Short explanation shown in the workbench handoff")


class GovernanceOperationInput(BaseModel):
    operation: Literal["open_review", "finalize_standardized", "export"]
    format: Literal["csv", "xlsx"] = "csv"
    output_dir: str = Field(default="", description="Optional configured GeoChem export directory; arbitrary paths are rejected")


class CurationStatusInput(BaseModel):
    article_id: str = Field(default="", description="Article id; blank uses the current selected article")


class ResourceInspectionInput(BaseModel):
    element_type: Literal["", "table", "paragraph", "figure"] = ""
    selected_only: bool = False
    limit: int = Field(default=30, ge=1, le=100)


class MappingInspectionInput(BaseModel):
    source_header: str = Field(default="", description="Optional source field to locate")


class CandidateInspectionInput(BaseModel):
    sample_id: str = ""
    target_header: str = ""
    limit: int = Field(default=20, ge=1, le=100)


class ResourceSelectionRequestInput(BaseModel):
    element_ids: list[str] = Field(default_factory=list)
    action: Literal["include", "exclude"] = "include"
    reason: str = ""


class MappingChangeRequestInput(BaseModel):
    source_header: str
    target_header: str
    target_unit: str = ""
    conversion_formula: str = ""
    scope: Literal["article", "project"] = "article"


class CandidateChangeRequestInput(BaseModel):
    operation: Literal["edit_cell", "add_row", "delete_row", "merge_rows", "clear_cell"]
    target_ids: list[str] = Field(default_factory=list)
    target_header: str = ""
    value: str = ""
    reason: str = ""


class TaskControlRequestInput(BaseModel):
    operation: Literal["status", "cancel", "retry"]
    task_id: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    entity_type: str
    permission_level: PermissionLevel
    confirmation_required: bool
    scope: str
    description: str


TOOL_ACTIVITY_LABELS = {
    "list_project_entities": "正在查询项目对象",
    "select_project_entity": "正在选择项目对象",
    "inspect_provenance": "正在检索数据溯源",
    "calculate_project_statistics": "正在计算数据统计",
    "search_public_literature": "正在检索公开文献",
    "request_article_import": "正在准备文献导入",
    "request_header_config_import": "正在准备表头导入",
    "start_curation_workflow": "正在准备文章处理",
    "get_curation_status": "正在读取处理状态",
    "inspect_article_resources": "正在检查文章资源",
    "inspect_table_mappings": "正在检查字段映射",
    "inspect_candidate_data": "正在检查候选数据",
    "request_resource_selection": "正在准备资源选择修改",
    "request_mapping_change": "正在准备映射规则修改",
    "request_candidate_change": "正在准备候选数据修改",
    "request_task_control": "正在检查任务",
    "open_expert_workbench": "正在准备工作台交接",
    "request_curation_operation": "正在定位工作台操作",
    "request_governance_operation": "正在准备治理操作",
    "discover_article_resources": "正在发现 PDF 资源",
    "sync_article_retrieval": "正在同步检索索引",
    "save_resource_selection": "正在保存资源选择",
    "standardize_article_tables": "正在标准化表格",
    "apply_table_mapping_rules": "正在保存字段映射",
    "extract_table_resources": "正在抽取表格资源",
    "extract_paragraph_resources": "正在抽取段落资源",
    "merge_candidate_records": "正在合并候选记录",
    "validate_candidate_evidence": "正在校验证据完整性",
}


class AuditedToolExecutor:
    """Run GeoChem tools through one policy, audit and activity-event path."""

    def __init__(
        self,
        project_manager: ProjectManager,
        security: ContentSecurityGateway | None = None,
    ):
        self.pm = project_manager
        self.security = security or ContentSecurityGateway()

    def execute(
        self,
        *,
        project_id: str,
        run_id: str,
        thread_id: str,
        article_id: str,
        tool_name: str,
        func: Callable[[], Any],
        arguments: dict[str, Any] | None = None,
        permission_level: PermissionLevel = "read",
        trust_source: str = "trusted_system",
        confirmation_required: bool = False,
        label: str = "",
    ) -> Any:
        safe_arguments = self.security.safe_summary(arguments or {})
        allowed, policy_reason = self.security.validate_tool_request(
            tool_name=tool_name,
            permission_level=permission_level,
            arguments=arguments or {},
            trust_source=trust_source,
            expected_article_id=article_id,
        )
        call_id = f"ATC_{uuid4().hex[:20].upper()}"
        started = datetime.now().isoformat(timespec="milliseconds")
        started_clock = time.perf_counter()
        idempotency = hashlib.sha256(
            json.dumps(
                [run_id, tool_name, safe_arguments, started],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()[:24]
        confirmation_status = "required" if confirmation_required else "not_required"
        db = self.pm.get_database(project_id)
        try:
            db.execute(
                """INSERT INTO agent_tool_calls
                   (tool_call_id, run_id, thread_id, project_id, article_id, tool_name,
                    permission_level, input_summary_json, status, trust_source,
                    policy_decision, confirmation_status, rejection_reason,
                    idempotency_key, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    call_id,
                    run_id,
                    thread_id,
                    project_id,
                    article_id,
                    tool_name,
                    permission_level,
                    json.dumps(safe_arguments, ensure_ascii=False),
                    "running" if allowed else "rejected",
                    trust_source,
                    "allowed" if allowed else "denied",
                    confirmation_status,
                    "" if allowed else policy_reason,
                    idempotency,
                    started,
                ),
            )
            self._activity(
                db,
                run_id,
                tool_call_id=call_id,
                tool_name=tool_name,
                label=label,
                status="running" if allowed else "failed",
                safe_input=safe_arguments,
                error="" if allowed else policy_reason,
            )
            db.commit()
        finally:
            db.close()
        if not allowed:
            raise ValueError(policy_reason)

        try:
            result = func()
            duration_ms = int((time.perf_counter() - started_clock) * 1000)
            safe_output = self.security.safe_summary(result)
            db = self.pm.get_database(project_id)
            try:
                db.execute(
                    """UPDATE agent_tool_calls
                       SET status='completed', output_summary_json=?, duration_ms=?, finished_at=?
                       WHERE tool_call_id=?""",
                    (
                        json.dumps(safe_output, ensure_ascii=False),
                        duration_ms,
                        datetime.now().isoformat(timespec="milliseconds"),
                        call_id,
                    ),
                )
                self._activity(
                    db,
                    run_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    label=label,
                    status="completed",
                    safe_input=safe_arguments,
                    safe_output=safe_output,
                    duration_ms=duration_ms,
                )
                db.commit()
            finally:
                db.close()
            return result
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_clock) * 1000)
            error = str(exc)[:500]
            db = self.pm.get_database(project_id)
            try:
                db.execute(
                    """UPDATE agent_tool_calls
                       SET status='failed', error_message=?, duration_ms=?, finished_at=?
                       WHERE tool_call_id=?""",
                    (
                        error,
                        duration_ms,
                        datetime.now().isoformat(timespec="milliseconds"),
                        call_id,
                    ),
                )
                self._activity(
                    db,
                    run_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    label=label,
                    status="failed",
                    safe_input=safe_arguments,
                    duration_ms=duration_ms,
                    error=error,
                )
                db.commit()
            finally:
                db.close()
            raise

    @staticmethod
    def _activity(
        db: Any,
        run_id: str,
        *,
        tool_call_id: str,
        tool_name: str,
        status: str,
        safe_input: Any,
        label: str = "",
        safe_output: Any = None,
        duration_ms: int = 0,
        error: str = "",
    ) -> None:
        resolved_label = label or TOOL_ACTIVITY_LABELS.get(tool_name, f"正在执行 {tool_name}")
        details = {
            "tool_call_id": tool_call_id,
            "event_type": "tool",
            "tool_name": tool_name,
            "label": resolved_label,
            "status": status,
            "safe_input_summary": safe_input,
            "safe_output_summary": safe_output if safe_output is not None else {},
            "duration_ms": duration_ms,
            "checkpoint": "",
            "error": error,
        }
        db.execute(
            """INSERT INTO agent_run_events
               (run_id, level, message, details_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                run_id,
                "ERROR" if status == "failed" else "INFO",
                resolved_label,
                json.dumps(details, ensure_ascii=False),
                datetime.now().isoformat(timespec="milliseconds"),
            ),
        )


class AgentToolRegistry:
    """A deliberately small tool surface for chat selection and inspection."""

    tools = (
        ToolDefinition("list_project_entities", "*", "read", False, "project", "List governed project objects."),
        ToolDefinition("select_project_entity", "*", "write", False, "thread", "Bind a verified project object to a chat thread."),
        ToolDefinition("inspect_provenance", "sample|field", "read", False, "article", "Read evidence-backed project provenance."),
        ToolDefinition("calculate_project_statistics", "sample|field", "read", False, "article", "Calculate local statistics without model arithmetic."),
        ToolDefinition("search_public_literature", "article", "read", False, "public", "Search Crossref and OpenAlex scholarly indexes."),
        ToolDefinition("request_article_import", "article", "write", True, "project", "Resolve a DOI or public URL after user confirmation."),
        ToolDefinition("request_header_config_import", "header_config", "manual", False, "project", "Open the governed CSV/XLSX/XLS header configuration importer."),
        ToolDefinition("start_curation_workflow", "article|header_config", "write", True, "article", "Start a curation workflow after confirmation."),
        ToolDefinition("open_expert_workbench", "article", "manual", True, "article", "Hand complex edits to the expert workbench."),
        ToolDefinition("request_curation_operation", "resource|sample|field", "manual", True, "article", "Open the exact workbench stage for a governed curation operation."),
        ToolDefinition("request_governance_operation", "review|standardized|export_job", "critical", True, "article", "Request review, finalization or export with explicit user confirmation."),
        ToolDefinition("get_curation_status", "article", "read", False, "article", "Read the current article processing status."),
        ToolDefinition("inspect_article_resources", "resource", "read", False, "article", "Inspect discovered and selected resources."),
        ToolDefinition("inspect_table_mappings", "field|rule", "read", False, "article", "Inspect table header mapping suggestions and memory."),
        ToolDefinition("inspect_candidate_data", "sample|field", "read", False, "article", "Inspect candidate rows, cells, conflicts and evidence status."),
        ToolDefinition("request_resource_selection", "resource", "write", True, "article", "Request a governed resource selection change."),
        ToolDefinition("request_mapping_change", "field|rule", "write", True, "article", "Request a governed mapping rule change."),
        ToolDefinition("request_candidate_change", "sample|field", "write", True, "article", "Request a governed candidate data change."),
        ToolDefinition("request_task_control", "task", "write", True, "thread", "Request status, cancellation or retry of a task."),
    )

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager
        self.workbench = WorkbenchService(project_manager)
        self.security = ContentSecurityGateway()
        self.executor = AuditedToolExecutor(project_manager, self.security)

    def definitions(self) -> list[dict[str, Any]]:
        return [asdict(tool) for tool in self.tools]

    def build_langchain_tools(
        self,
        *,
        project_id: str,
        thread_id: str,
        run_id: str,
        article_id: str,
        provenance_handler: Callable[[str], dict[str, Any]],
        statistics_handler: Callable[[str, str], dict[str, Any]],
        literature_handler: Callable[[str, str, int], dict[str, Any]],
    ) -> list[StructuredTool]:
        """Create a request-scoped, audited tool surface for LangChain.

        Write/critical tools return an action request. They never perform the
        governed operation inside the model loop; LangGraph owns confirmation
        and execution of those actions.
        """

        def list_entities(entity_type: str = "article", query: str = "") -> dict[str, Any]:
            entities = self.entities(project_id, entity_type, query, thread_id)
            return {
                "answer": f"找到 {len(entities)} 个{self._entity_label(entity_type)}。",
                "entities": entities,
                "ui_payload": {"kind": "entity_cards", "entities": entities},
                "citations": [self._entity_citation(entity) for entity in entities[:20]],
            }

        def select_entity(entity_type: str, entity_id: str = "", query: str = "") -> dict[str, Any]:
            resolved_id = entity_id
            if not resolved_id:
                resolved = self.resolve(project_id, entity_type, query, thread_id)
                if resolved.get("status") != "resolved":
                    return {
                        "status": resolved.get("status", "not_found"),
                        "answer": "无法唯一确定要选择的对象，请点击候选卡片确认。",
                        "entities": resolved.get("matches", []),
                        "ui_payload": {"kind": "entity_cards", "entities": resolved.get("matches", [])},
                    }
                resolved_id = str(resolved["entity"]["entity_id"])
            selected = self.select(project_id, thread_id, EntitySelectionInput(entity_type=entity_type, entity_id=resolved_id))
            entity = (selected.get("selection") or {}).get(entity_type) or {}
            return {
                "status": "selected",
                "answer": f"已选择{self._entity_label(entity_type)}：{entity.get('title') or resolved_id}。",
                "entity": entity,
                "article_id": selected.get("article_id", ""),
                "ui_payload": {"kind": "selected_entity", "entities": [entity]},
                "citations": [self._entity_citation(entity)] if entity else [],
            }

        def inspect_provenance(question: str) -> dict[str, Any]:
            if not article_id:
                return {"answer": "请先选择一篇文章，再查询数据溯源。", "citations": [], "requires": "article"}
            return provenance_handler(question)

        def calculate_statistics(field: str = "", operation: str = "summary") -> dict[str, Any]:
            if not article_id:
                return {"answer": "请先选择一篇文章，再计算数据统计。", "citations": [], "requires": "article"}
            return statistics_handler(field, operation)

        def search_literature(query: str, sort_mode: str = "relevance", limit: int = 8) -> dict[str, Any]:
            return literature_handler(query, sort_mode, limit)

        def request_import(source: str) -> dict[str, Any]:
            return {
                "answer": "已识别到 DOI 或公开 URL，需要确认后检查公开 PDF。",
                "agent_action": {"intent": "import", "arguments": {"source": source}},
                "confirmation_required": True,
            }

        def request_header_import(suggested_name: str = "") -> dict[str, Any]:
            return {
                "answer": "可以导入新的表头配置。请选择 CSV、XLSX 或 XLS 文件；导入后可以独立编辑、保存，并分配给任意文章。",
                "ui_payload": {
                    "kind": "header_import_action",
                    "suggested_name": suggested_name.strip(),
                    "accepted_formats": [".csv", ".xlsx", ".xls"],
                },
                "citations": [],
            }

        def start_processing(article_id: str = "") -> dict[str, Any]:
            chosen = article_id or self.thread_context(project_id, thread_id).get("article_id") or ""
            if not chosen:
                return {"answer": "请先选择要处理的文章。", "requires": "article"}
            return {
                "answer": "文章已就绪，需要确认目标表头后开始处理。",
                "agent_action": {"intent": "process", "arguments": {"article_id": chosen}},
                "confirmation_required": True,
            }

        def open_workbench(workbench_view: str | None = None, workbench_step: int = 2, workbench_stage: str = "", reason: str = "") -> dict[str, Any]:
            stable_view = workbench_view or ("resources" if workbench_step <= 1 else "extract" if workbench_step == 2 else "quality")
            return {
                "answer": "该操作适合在智能体工作台中完成。",
                "agent_action": {
                    "intent": "workbench_handoff",
                    "arguments": {"workbench_view": stable_view, "workbench_step": workbench_step, "workbench_stage": workbench_stage, "reason": reason},
                },
                "confirmation_required": True,
            }

        def request_curation_operation(operation: str, target_id: str = "", reason: str = "") -> dict[str, Any]:
            stages = {
                "review_resources": ("resources", 1, ""),
                "standardize_tables": ("extract", 2, "standardize"),
                "inspect_mappings": ("extract", 2, "mapping"),
                "extract_tables": ("extract", 2, "tables"),
                "extract_paragraphs": ("extract", 2, "paragraphs"),
                "edit_image_values": ("extract", 2, "figures"),
                "edit_candidates": ("extract", 2, "edit"),
            }
            view, step, stage = stages.get(operation, ("extract", 2, "edit"))
            return {
                "answer": "该操作需要使用受控工作台界面完成，我已定位到对应步骤。",
                "agent_action": {
                    "intent": "workbench_handoff",
                    "arguments": {
                        "workbench_view": view,
                        "workbench_step": step,
                        "workbench_stage": stage,
                        "target_id": target_id,
                        "reason": reason or operation,
                    },
                },
                "confirmation_required": True,
            }

        def request_governance_operation(operation: str, format: str = "csv", output_dir: str = "") -> dict[str, Any]:
            if not article_id:
                return {"answer": "请先选择一篇文章。", "requires": "article"}
            if operation == "open_review":
                return {
                    "answer": "人工审核需要在审核页逐项确认，我已准备打开对应文章。",
                    "agent_action": {
                        "intent": "navigate",
                        "arguments": {"path": "/review", "article_id": article_id},
                    },
                    "confirmation_required": True,
                }
            return {
                "answer": "这是正式数据治理操作，只有你在确认卡中明确批准后才会执行。",
                "agent_action": {
                    "intent": "critical_action",
                    "arguments": {
                        "operation": operation,
                        "article_id": article_id,
                        "format": format,
                        "output_dir": output_dir,
                    },
                },
                "confirmation_required": True,
            }

        def current_article(requested_article_id: str = "") -> str:
            chosen = requested_article_id or article_id or str(
                self.thread_context(project_id, thread_id).get("article_id") or ""
            )
            if not chosen:
                raise ValueError("请先选择一篇文章。")
            return chosen

        def get_curation_status(article_id: str = "") -> dict[str, Any]:
            chosen = current_article(article_id)
            db = self.pm.get_database(project_id)
            try:
                article = db.fetch_one(
                    "SELECT article_id, title, status FROM articles WHERE project_id=? AND article_id=?",
                    (project_id, chosen),
                )
                if not article:
                    raise ValueError("当前工作区中不存在该文章。")
                header = db.fetch_one(
                    """SELECT h.config_id, h.name
                       FROM article_header_assignments a
                       JOIN header_configs h ON h.config_id=a.config_id
                       WHERE a.project_id=? AND a.article_id=? AND a.status='confirmed'
                       ORDER BY a.created_at DESC LIMIT 1""",
                    (project_id, chosen),
                )
                session = db.fetch_one(
                    "SELECT session_id, current_step, discovery_status, active_batch_id FROM workbench_sessions WHERE project_id=? AND article_id=?",
                    (project_id, chosen),
                )
                resource_count = db.fetch_one(
                    "SELECT COUNT(*) AS count FROM document_elements WHERE project_id=? AND article_id=? AND status!='stale'",
                    (project_id, chosen),
                )
                selected_count = db.fetch_one(
                    """SELECT COUNT(*) AS count
                       FROM element_selections s
                       JOIN workbench_sessions w ON w.session_id=s.session_id
                       WHERE w.project_id=? AND w.article_id=? AND s.status='selected'""",
                    (project_id, chosen),
                )
                batch = db.fetch_one(
                    """SELECT batch_id, status, record_count, updated_at
                       FROM extraction_batches
                       WHERE project_id=? AND article_id=?
                       ORDER BY created_at DESC LIMIT 1""",
                    (project_id, chosen),
                )
                unresolved = db.fetch_one(
                    """SELECT COUNT(*) AS count
                       FROM candidate_cells c
                       JOIN candidate_records r ON r.candidate_record_id=c.candidate_record_id
                       WHERE r.article_id=? AND (
                         c.mapping_status!='confirmed' OR c.evidence_status='insufficient'
                         OR c.review_status='pending'
                       )""",
                    (chosen,),
                )
                task = db.fetch_one(
                    """SELECT task_id, task_type, status, progress, message, error_message
                       FROM workflow_tasks
                       WHERE project_id=? AND article_id=?
                       ORDER BY created_at DESC LIMIT 1""",
                    (project_id, chosen),
                )
                status = {
                    "article_id": chosen,
                    "title": article["title"] or "未命名文章",
                    "article_status": article["status"] or "",
                    "header_config": dict(header) if header else None,
                    "workbench": dict(session) if session else None,
                    "resource_count": int(resource_count["count"] or 0) if resource_count else 0,
                    "selected_resource_count": int(selected_count["count"] or 0) if selected_count else 0,
                    "latest_batch": dict(batch) if batch else None,
                    "unresolved_cell_count": int(unresolved["count"] or 0) if unresolved else 0,
                    "latest_task": dict(task) if task else None,
                }
                return {
                    "answer": (
                        f"文章《{status['title']}》已发现 {status['resource_count']} 个资源，"
                        f"已选择 {status['selected_resource_count']} 个；"
                        f"当前有 {status['unresolved_cell_count']} 个待确认或证据不足的单元格。"
                    ),
                    "curation_status": status,
                    "citations": [{
                        "document_id": f"ARTICLE_STATUS_{chosen}",
                        "article_id": chosen,
                        "label": status["title"],
                        "element_type": "article_status",
                        "content": "GeoChem 项目数据库中的实时处理状态。",
                    }],
                }
            finally:
                db.close()

        def inspect_resources(
            element_type: str = "",
            selected_only: bool = False,
            limit: int = 30,
        ) -> dict[str, Any]:
            chosen = current_article()
            elements = self.workbench.list_elements(project_id, chosen)
            filtered = [
                item for item in elements
                if (not element_type or item.get("element_type") == element_type)
                and (not selected_only or bool(item.get("selected")))
            ][:limit]
            summaries = [{
                "element_id": item.get("element_id", ""),
                "resource_id": item.get("resource_id", ""),
                "element_type": item.get("element_type", ""),
                "page_number": item.get("page_number"),
                "page_spans": item.get("page_spans", []),
                "caption": str(item.get("caption") or "")[:240],
                "text_preview": str(item.get("text_content") or "")[:240],
                "relevance_score": item.get("relevance_score", 0),
                "score_reasons": item.get("score_reasons", [])[:8],
                "matched_headers": item.get("matched_headers", [])[:12],
                "selected": bool(item.get("selected")),
                "source_backend": item.get("source_backend", ""),
            } for item in filtered]
            return {
                "answer": f"找到 {len(summaries)} 个符合条件的文章资源。",
                "resources": summaries,
                "ui_payload": {"kind": "resource_summaries", "resources": summaries},
                "citations": [{
                    "document_id": f"ELEMENT_{item['element_id']}",
                    "article_id": chosen,
                    "element_id": item["element_id"],
                    "resource_id": item["resource_id"],
                    "page_number": item["page_number"],
                    "element_type": item["element_type"],
                    "label": item["caption"] or item["text_preview"] or item["element_id"],
                    "content": item["text_preview"],
                } for item in summaries],
            }

        def inspect_mappings(source_header: str = "") -> dict[str, Any]:
            chosen = current_article()
            preflight = self.workbench.table_rule_preflight(project_id, chosen)
            suggestions = list(preflight.get("suggestions") or [])
            if source_header:
                suggestions = [
                    item for item in suggestions
                    if self._matches(source_header, str(item.get("source_header") or ""))
                ]
            memory = self.workbench.rule_memory(project_id, chosen)
            return {
                "answer": (
                    f"当前有 {len(suggestions)} 条表格字段映射建议，"
                    f"规则记忆中有 {len(memory.get('extraction') or [])} 条抽取规则。"
                ),
                "mapping_suggestions": suggestions[:80],
                "rules": (memory.get("extraction") or [])[:80],
                "ui_payload": {
                    "kind": "mapping_summaries",
                    "mappings": suggestions[:80],
                    "rules": (memory.get("extraction") or [])[:40],
                },
            }

        def inspect_candidates(
            sample_id: str = "",
            target_header: str = "",
            limit: int = 20,
        ) -> dict[str, Any]:
            chosen = current_article()
            result = self.workbench.article_candidate_records(project_id, chosen)
            records: list[dict[str, Any]] = []
            for record in result.get("records") or []:
                if sample_id and not self._matches(sample_id, str(record.get("sample_id") or "")):
                    continue
                cells = record.get("cells") or {}
                if target_header:
                    cells = {
                        key: value for key, value in cells.items()
                        if self._matches(target_header, str(key), str(value.get("target_field") or ""))
                    }
                    if not cells:
                        continue
                records.append({
                    "candidate_record_id": record.get("candidate_record_id", ""),
                    "sample_id": record.get("sample_id", ""),
                    "quality_grade": record.get("quality_grade", ""),
                    "merge_status": record.get("merge_status", ""),
                    "cells": {
                        key: {
                            "cell_id": cell.get("cell_id", ""),
                            "value": cell.get("value", ""),
                            "target_unit": cell.get("target_unit", ""),
                            "original_field": cell.get("original_field", ""),
                            "original_value": cell.get("original_value", ""),
                            "confidence": cell.get("confidence", 0),
                            "evidence_status": cell.get("evidence_status", ""),
                            "review_status": cell.get("review_status", ""),
                            "element_id": cell.get("element_id", ""),
                            "page_number": cell.get("page_number"),
                        } for key, cell in list(cells.items())[:40]
                    },
                })
                if len(records) >= limit:
                    break
            return {
                "answer": f"找到 {len(records)} 条符合条件的候选样品记录。",
                "headers": result.get("headers", []),
                "records": records,
                "ui_payload": {"kind": "candidate_summaries", "records": records},
            }

        def request_resource_selection(
            element_ids: list[str] | None = None,
            action: str = "include",
            reason: str = "",
        ) -> dict[str, Any]:
            chosen = current_article()
            ids = list(dict.fromkeys(str(value) for value in (element_ids or []) if str(value)))
            if not ids:
                raise ValueError("请选择至少一个资源。")
            known = {
                str(item.get("element_id")) for item in self.workbench.list_elements(project_id, chosen)
            }
            unknown = [value for value in ids if value not in known]
            if unknown:
                raise ValueError("部分资源不属于当前文章。")
            return {
                "answer": f"准备{('加入' if action == 'include' else '移出')} {len(ids)} 个资源，确认后才会修改抽取队列。",
                "agent_action": {
                    "intent": "resource_selection_change",
                    "arguments": {
                        "article_id": chosen,
                        "element_ids": ids,
                        "action": action,
                        "reason": reason,
                    },
                },
                "confirmation_required": True,
            }

        def request_mapping_change(
            source_header: str,
            target_header: str,
            target_unit: str = "",
            conversion_formula: str = "",
            scope: str = "article",
        ) -> dict[str, Any]:
            chosen = current_article()
            if not source_header.strip() or not target_header.strip():
                raise ValueError("原始字段和目标表头不能为空。")
            valid_headers = {
                str(item.get("display_header") or "")
                for item in self.workbench.target_headers(project_id, chosen)
            }
            if target_header not in valid_headers:
                raise ValueError("目标表头不属于当前文章绑定的表头配置。")
            return {
                "answer": f"准备新增或修改映射：{source_header} → {target_header}，确认后写入规则记忆。",
                "agent_action": {
                    "intent": "mapping_change",
                    "arguments": {
                        "article_id": chosen,
                        "source_header": source_header,
                        "target_header": target_header,
                        "target_unit": target_unit,
                        "conversion_formula": conversion_formula,
                        "scope": scope,
                    },
                },
                "confirmation_required": True,
            }

        def request_candidate_change(
            operation: str,
            target_ids: list[str] | None = None,
            target_header: str = "",
            value: str = "",
            reason: str = "",
        ) -> dict[str, Any]:
            chosen = current_article()
            ids = list(dict.fromkeys(str(item) for item in (target_ids or []) if str(item)))
            if operation != "add_row" and not ids:
                raise ValueError("请选择要修改的候选记录或单元格。")
            return {
                "answer": "候选数据修改已整理为待确认操作，确认前不会写入数据库。",
                "agent_action": {
                    "intent": "candidate_change",
                    "arguments": {
                        "article_id": chosen,
                        "operation": operation,
                        "target_ids": ids,
                        "target_header": target_header,
                        "value": value,
                        "reason": reason,
                    },
                },
                "confirmation_required": True,
            }

        def request_task_control(
            operation: str,
            task_id: str = "",
            run_id: str = "",
        ) -> dict[str, Any]:
            db = self.pm.get_database(project_id)
            try:
                if task_id:
                    task = db.fetch_one(
                        "SELECT * FROM workflow_tasks WHERE project_id=? AND task_id=?",
                        (project_id, task_id),
                    )
                elif run_id:
                    task = db.fetch_one(
                        "SELECT * FROM agent_runs WHERE project_id=? AND run_id=?",
                        (project_id, run_id),
                    )
                else:
                    task = db.fetch_one(
                        """SELECT * FROM workflow_tasks
                           WHERE project_id=? ORDER BY created_at DESC LIMIT 1""",
                        (project_id,),
                    )
                if not task:
                    raise ValueError("没有找到可操作的任务。")
                safe_task = self.security.safe_summary(dict(task))
                if operation == "status":
                    return {
                        "answer": f"任务当前状态为 {task['status']}。",
                        "task": safe_task,
                    }
                return {
                    "answer": f"准备{('取消' if operation == 'cancel' else '重试')}该任务，确认后执行。",
                    "agent_action": {
                        "intent": "task_control",
                        "arguments": {
                            "operation": operation,
                            "task_id": task_id,
                            "run_id": run_id,
                        },
                    },
                    "confirmation_required": True,
                }
            finally:
                db.close()

        specs = [
            ("list_project_entities", "查询当前 GeoChem 工作区中的文章、表头、资源、样品、字段、规则或导出任务。必须使用它回答项目中实际有哪些对象。", ListEntitiesInput, list_entities, "read"),
            ("select_project_entity", "按精确 id 或名称选择当前会话对象。名称不能唯一匹配时会返回可点击候选，禁止猜测。", SelectEntityToolInput, select_entity, "write"),
            ("inspect_provenance", "查询已抽取/标准化数据的值、原文来源、页码、映射、换算与审核证据。", ProvenanceQueryInput, inspect_provenance, "read"),
            ("calculate_project_statistics", "由本地代码计算当前文章的记录数、字段统计和缺失情况。模型不得自行心算。", StatisticsQueryInput, calculate_statistics, "read"),
            ("search_public_literature", "检索 Crossref 与 OpenAlex 的公开学术元数据，可按最新或相关度排序。", LiteratureSearchToolInput, search_literature, "read"),
            ("request_article_import", "请求导入用户明确给出的 DOI 或公开 URL。只产生待确认动作，不绕过访问控制。", ImportArticleToolInput, request_import, "write"),
            ("request_header_config_import", "当用户询问能否导入、上传、新建表头配置时，打开独立的 CSV/XLSX/XLS 表头导入操作。表头配置不是从文章中自动生成的。", HeaderImportToolInput, request_header_import, "manual"),
            ("start_curation_workflow", "请求处理当前已选择文章。只产生 LangGraph 工作流动作，正式流程仍有确认节点。", StartProcessingToolInput, start_processing, "write"),
            ("open_expert_workbench", "将复杂 PDF、表格、图像或批量候选编辑交接到精确工作台步骤。", WorkbenchHandoffToolInput, open_workbench, "manual"),
            ("request_curation_operation", "把资源校核、表格标准化、字段映射、表格/段落抽取、图像补值或候选编辑定位到精确专家工作台步骤。", CurationOperationInput, request_curation_operation, "manual"),
            ("request_governance_operation", "请求打开人工审核，或在用户明确确认后正式标准化/导出。不得声称未确认的操作已经完成。", GovernanceOperationInput, request_governance_operation, "critical"),
            ("get_curation_status", "读取当前文章的表头绑定、资源选择、抽取批次、未解决单元格和最新任务状态。", CurationStatusInput, get_curation_status, "read"),
            ("inspect_article_resources", "读取当前文章已发现资源的安全摘要。不会把整篇 PDF 或不可信文档指令交给控制 Agent。", ResourceInspectionInput, inspect_resources, "read"),
            ("inspect_table_mappings", "读取当前文章的表格字段映射建议和已确认规则记忆。", MappingInspectionInput, inspect_mappings, "read"),
            ("inspect_candidate_data", "读取当前文章候选样品、单元格、冲突与证据状态。", CandidateInspectionInput, inspect_candidates, "read"),
            ("request_resource_selection", "请求将已验证的资源加入或移出抽取队列。确认前不修改选择。", ResourceSelectionRequestInput, request_resource_selection, "write"),
            ("request_mapping_change", "请求新增或修改原始字段到目标表头的映射规则。确认前不写入规则记忆。", MappingChangeRequestInput, request_mapping_change, "write"),
            ("request_candidate_change", "请求编辑、增删、合并候选样品或清空单元格。确认前不修改候选数据。", CandidateChangeRequestInput, request_candidate_change, "write"),
            ("request_task_control", "读取任务状态，或请求取消/重试当前任务。取消和重试需要确认。", TaskControlRequestInput, request_task_control, "write"),
        ]
        return [
            self._structured_tool(
                name=name,
                description=description,
                args_schema=args_schema,
                func=func,
                permission_level=permission,
                project_id=project_id,
                thread_id=thread_id,
                run_id=run_id,
                article_id=article_id,
            )
            for name, description, args_schema, func, permission in specs
        ]

    def _structured_tool(
        self,
        *,
        name: str,
        description: str,
        args_schema: type[BaseModel],
        func: Callable[..., dict[str, Any]],
        permission_level: PermissionLevel,
        project_id: str,
        thread_id: str,
        run_id: str,
        article_id: str,
    ) -> StructuredTool:
        def audited(**kwargs: Any) -> dict[str, Any]:
            definition = next((item for item in self.tools if item.name == name), None)
            return self.executor.execute(
                project_id=project_id,
                run_id=run_id,
                thread_id=thread_id,
                article_id=article_id,
                tool_name=name,
                func=lambda: func(**kwargs),
                arguments=kwargs,
                permission_level=permission_level,
                trust_source="trusted_user",
                confirmation_required=bool(definition and definition.confirmation_required),
            )

        return StructuredTool.from_function(
            func=audited,
            name=name,
            description=description,
            args_schema=args_schema,
        )

    @staticmethod
    def _entity_label(entity_type: str) -> str:
        return {
            "article": "文章", "header_config": "表头配置", "resource": "资源",
            "sample": "样品", "field": "字段", "rule": "规则", "export_job": "导出任务",
        }.get(entity_type, "项目对象")

    @staticmethod
    def _entity_citation(entity: dict[str, Any]) -> dict[str, Any]:
        return {
            "document_id": f"{str(entity.get('entity_type') or 'entity').upper()}_{entity.get('entity_id', '')}",
            "article_id": entity.get("article_id", ""),
            "record_id": entity.get("record_id", ""),
            "element_id": entity.get("element_id", ""),
            "resource_id": entity.get("resource_id", ""),
            "label": entity.get("title", ""),
            "element_type": entity.get("entity_type", ""),
            "content": entity.get("subtitle", ""),
            "entity": entity,
        }

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").casefold())

    @classmethod
    def _matches(cls, query: str, *values: str) -> bool:
        q = cls._norm(query)
        if not q:
            return True
        return any(q in cls._norm(value) or cls._norm(value) in q for value in values if value)

    def entities(self, project_id: str, entity_type: str = "", query: str = "", context_id: str = "") -> list[dict[str, Any]]:
        if entity_type and entity_type not in ENTITY_TYPES:
            raise ValueError("不支持的对象类型。")
        context = self.thread_context(project_id, context_id) if context_id else {}
        article_id = str(context.get("article_id") or "")
        requested = (entity_type,) if entity_type else ENTITY_TYPES
        results: list[dict[str, Any]] = []
        for current in requested:
            results.extend(getattr(self, f"_list_{current}")(project_id, query, article_id))
        return results[:80]

    def resolve(self, project_id: str, entity_type: str, query: str, context_id: str = "") -> dict[str, Any]:
        matches = self.entities(project_id, entity_type, query, context_id)
        if not matches:
            return {"status": "not_found", "matches": []}
        exact = [item for item in matches if self._norm(query) in {self._norm(str(item.get(key, ""))) for key in ("entity_id", "title", "alias", "doi", "ordinal")}]
        candidates = exact or matches
        if len(candidates) != 1:
            return {"status": "ambiguous", "matches": candidates[:12]}
        return {"status": "resolved", "entity": candidates[0]}

    def select(self, project_id: str, thread_id: str, selection: EntitySelectionInput) -> dict[str, Any]:
        if selection.entity_type == "clear":
            return self._save_context(project_id, thread_id, {}, None, {"action": "clear_selection"})
        if selection.entity_type not in ENTITY_TYPES:
            raise ValueError("请选择文章、表头、资源、样品、字段、规则或导出任务。")
        candidates = self.entities(project_id, selection.entity_type, selection.entity_id or selection.query, thread_id)
        entity = next((item for item in candidates if item["entity_id"] == selection.entity_id), None)
        if not entity:
            raise ValueError("该对象不属于当前工作区，或已被删除。")
        context = self.thread_context(project_id, thread_id)
        context[selection.entity_type] = entity
        if selection.entity_type == "article":
            context["article_id"] = entity["entity_id"]
            for key in ("resource", "sample", "field"):
                context.pop(key, None)
        elif entity.get("article_id"):
            context["article_id"] = str(entity["article_id"])
        if selection.entity_type == "header_config":
            context["header_config_id"] = entity["entity_id"]
        return self._save_context(project_id, thread_id, context, context.get("article_id"), {"action": "select", "entity_type": selection.entity_type, "entity_id": entity["entity_id"]})

    def thread_context(self, project_id: str, thread_id: str) -> dict[str, Any]:
        if not thread_id:
            return {}
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one("SELECT article_id, selection_context_json FROM chat_threads WHERE project_id=? AND thread_id=?", (project_id, thread_id))
            if not row:
                return {}
            try:
                context = json.loads(row["selection_context_json"] or "{}")
            except json.JSONDecodeError:
                context = {}
            if row["article_id"]:
                context["article_id"] = row["article_id"]
            return context
        finally:
            db.close()

    def _save_context(self, project_id: str, thread_id: str, context: dict[str, Any], article_id: str | None, event: dict[str, Any]) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            thread = db.fetch_one("SELECT thread_id FROM chat_threads WHERE project_id=? AND thread_id=?", (project_id, thread_id))
            if not thread:
                raise ValueError("对话不存在或已被删除。")
            db.execute("UPDATE chat_threads SET article_id=?, selection_context_json=?, updated_at=datetime('now') WHERE project_id=? AND thread_id=?", (article_id, json.dumps(context, ensure_ascii=False), project_id, thread_id))
            active = db.fetch_one("SELECT run_id FROM agent_runs WHERE project_id=? AND thread_id=? AND status IN ('pending','running','waiting_user','waiting_workbench') ORDER BY created_at DESC LIMIT 1", (project_id, thread_id))
            if active:
                db.execute("UPDATE agent_runs SET article_id=?, selection_context_json=?, updated_at=datetime('now') WHERE run_id=?", (article_id, json.dumps(context, ensure_ascii=False), active["run_id"]))
                db.execute("INSERT INTO agent_run_events (run_id, level, message, details_json, created_at) VALUES (?, 'INFO', ?, ?, datetime('now'))", (active["run_id"], "已更新会话对象选择", json.dumps(event, ensure_ascii=False)))
            db.commit()
            return {"selection": context, "article_id": article_id or ""}
        finally:
            db.close()

    def _list_article(self, project_id: str, query: str, _article_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all("""SELECT a.article_id, a.title, a.doi, a.journal, a.year, a.status,
                                 (SELECT COUNT(*) FROM resources r WHERE r.article_id=a.article_id) AS resource_count
                                 FROM articles a WHERE a.project_id=? ORDER BY a.created_at DESC""", (project_id,))
            return [self._entity("article", row["article_id"], row["title"] or "未命名文章", f"{row['doi'] or '未登记 DOI'} · {int(row['resource_count'] or 0)} 项资源", article_id=row["article_id"], alias=row["title"] or "", doi=row["doi"] or "", ordinal=str(index + 1), metadata={"journal": row["journal"] or "", "year": row["year"], "status": row["status"] or ""}) for index, row in enumerate(rows) if self._matches(query, row["article_id"], row["title"] or "", row["doi"] or "", str(index + 1))]
        finally:
            db.close()

    def _list_header_config(self, project_id: str, query: str, _article_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all("SELECT * FROM header_configs WHERE project_id=? AND status!='deleted' ORDER BY updated_at DESC", (project_id,))
            result = []
            for row in rows:
                headers = self._json(row["headers_json"], [])
                if self._matches(query, row["config_id"], row["name"], row["description"] or ""):
                    result.append(self._entity("header_config", row["config_id"], row["name"], f"{len(headers)} 个目标字段", alias=row["name"], metadata={"headers": headers[:20], "description": row["description"] or ""}))
            return result
        finally:
            db.close()

    def _list_resource(self, project_id: str, query: str, article_id: str) -> list[dict[str, Any]]:
        if not article_id:
            return []
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all("""SELECT e.element_id, e.resource_id, e.element_type, e.page_number, e.caption, e.text_content, e.relevance_score
                                 FROM document_elements e WHERE e.project_id=? AND e.article_id=? AND e.status!='stale'
                                 ORDER BY e.relevance_score DESC, e.page_number""", (project_id, article_id))
            return [self._entity("resource", row["element_id"], row["caption"] or f"{row['element_type']} p{row['page_number'] or '?'}", f"{row['element_type']} · p{row['page_number'] or '?'} · {int((row['relevance_score'] or 0) * 100)}%", article_id=article_id, resource_id=row["resource_id"] or "", element_id=row["element_id"], alias=(row["caption"] or "") + " " + (row["text_content"] or "")[:200], metadata={"element_type": row["element_type"], "page_number": row["page_number"]}) for row in rows if self._matches(query, row["element_id"], row["caption"] or "", row["text_content"] or "")]
        finally:
            db.close()

    def _list_sample(self, project_id: str, query: str, article_id: str) -> list[dict[str, Any]]:
        if not article_id:
            return []
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all("SELECT candidate_record_id, sample_id, created_at FROM candidate_records WHERE article_id=? ORDER BY sample_id", (article_id,))
            return [self._entity("sample", row["candidate_record_id"], row["sample_id"] or "未命名样品", "候选样品记录", article_id=article_id, record_id=row["candidate_record_id"], alias=row["sample_id"] or "") for row in rows if self._matches(query, row["candidate_record_id"], row["sample_id"] or "")]
        finally:
            db.close()

    def _list_field(self, project_id: str, query: str, article_id: str) -> list[dict[str, Any]]:
        if not article_id:
            return []
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one("""SELECT h.headers_json FROM article_header_assignments a JOIN header_configs h ON h.config_id=a.config_id
                                WHERE a.project_id=? AND a.article_id=? AND a.status='confirmed' ORDER BY a.created_at DESC LIMIT 1""", (project_id, article_id))
            headers = self._json(row["headers_json"], []) if row else []
            results = []
            for index, header in enumerate(headers):
                display = str(header.get("display_header") or header.get("name") or header) if isinstance(header, dict) else str(header)
                if self._matches(query, display, str(index + 1)):
                    results.append(self._entity("field", f"FIELD_{index}", display, "当前文章目标表头", article_id=article_id, alias=display, ordinal=str(index + 1), metadata={"header": header}))
            return results
        finally:
            db.close()

    def _list_rule(self, project_id: str, query: str, article_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all("""SELECT rule_id, pattern, target_header, rule_type, scope FROM learned_extraction_rules
                                WHERE project_id=? AND (article_id=? OR scope='project') ORDER BY created_at DESC""", (project_id, article_id))
            return [self._entity("rule", row["rule_id"], f"{row['pattern']} → {row['target_header']}", f"{row['rule_type']} · {row['scope']}", article_id=article_id, alias=f"{row['pattern']} {row['target_header']}") for row in rows if self._matches(query, row["rule_id"], row["pattern"], row["target_header"])]
        finally:
            db.close()

    def _list_export_job(self, project_id: str, query: str, article_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            sql = "SELECT job_id, article_id, format, output_path, status, created_at FROM export_jobs WHERE project_id=?"
            params: tuple[Any, ...] = (project_id,)
            if article_id:
                sql += " AND article_id=?"; params += (article_id,)
            rows = db.fetch_all(sql + " ORDER BY created_at DESC", params)
            return [self._entity("export_job", row["job_id"], f"{row['format'].upper()} 导出", row["status"] or "", article_id=row["article_id"] or "", alias=row["output_path"] or "", metadata={"created_at": row["created_at"], "status": row["status"] or ""}) for row in rows if self._matches(query, row["job_id"], row["format"], row["output_path"] or "")]
        finally:
            db.close()

    @staticmethod
    def _entity(entity_type: str, entity_id: str, title: str, subtitle: str, **extra: Any) -> dict[str, Any]:
        return {"entity_type": entity_type, "entity_id": entity_id, "title": title, "subtitle": subtitle, **extra}

    @staticmethod
    def _json(value: Any, default: Any) -> Any:
        try:
            return json.loads(value) if isinstance(value, str) else value
        except (json.JSONDecodeError, TypeError):
            return default
