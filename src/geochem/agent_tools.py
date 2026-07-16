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
from typing import Any, Callable, Literal
from uuid import uuid4

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .agent_models import PermissionLevel
from .core.project import ProjectManager


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


class StartProcessingToolInput(BaseModel):
    article_id: str = Field(default="", description="Current article id; blank uses the selected article")


class WorkbenchHandoffToolInput(BaseModel):
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


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    entity_type: str
    permission_level: PermissionLevel
    confirmation_required: bool
    scope: str
    description: str


class AgentToolRegistry:
    """A deliberately small tool surface for chat selection and inspection."""

    tools = (
        ToolDefinition("list_project_entities", "*", "read", False, "project", "List governed project objects."),
        ToolDefinition("select_project_entity", "*", "write", False, "thread", "Bind a verified project object to a chat thread."),
        ToolDefinition("inspect_provenance", "sample|field", "read", False, "article", "Read evidence-backed project provenance."),
        ToolDefinition("calculate_project_statistics", "sample|field", "read", False, "article", "Calculate local statistics without model arithmetic."),
        ToolDefinition("search_public_literature", "article", "read", False, "public", "Search Crossref and OpenAlex scholarly indexes."),
        ToolDefinition("request_article_import", "article", "write", True, "project", "Resolve a DOI or public URL after user confirmation."),
        ToolDefinition("start_curation_workflow", "article|header_config", "write", True, "article", "Start a curation workflow after confirmation."),
        ToolDefinition("open_expert_workbench", "article", "manual", True, "article", "Hand complex edits to the expert workbench."),
        ToolDefinition("request_curation_operation", "resource|sample|field", "manual", True, "article", "Open the exact workbench stage for a governed curation operation."),
        ToolDefinition("request_governance_operation", "review|standardized|export_job", "critical", True, "article", "Request review, finalization or export with explicit user confirmation."),
    )

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

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

        def start_processing(article_id: str = "") -> dict[str, Any]:
            chosen = article_id or self.thread_context(project_id, thread_id).get("article_id") or ""
            if not chosen:
                return {"answer": "请先选择要处理的文章。", "requires": "article"}
            return {
                "answer": "文章已就绪，需要确认目标表头后开始处理。",
                "agent_action": {"intent": "process", "arguments": {"article_id": chosen}},
                "confirmation_required": True,
            }

        def open_workbench(workbench_step: int = 2, workbench_stage: str = "", reason: str = "") -> dict[str, Any]:
            return {
                "answer": "该操作适合在智能体工作台中完成。",
                "agent_action": {
                    "intent": "workbench_handoff",
                    "arguments": {"workbench_step": workbench_step, "workbench_stage": workbench_stage, "reason": reason},
                },
                "confirmation_required": True,
            }

        def request_curation_operation(operation: str, target_id: str = "", reason: str = "") -> dict[str, Any]:
            stages = {
                "review_resources": (1, ""),
                "standardize_tables": (2, "standardize"),
                "inspect_mappings": (2, "mapping"),
                "extract_tables": (2, "tables"),
                "extract_paragraphs": (2, "paragraphs"),
                "edit_image_values": (2, "figures"),
                "edit_candidates": (2, "edit"),
            }
            step, stage = stages.get(operation, (2, "edit"))
            return {
                "answer": "该操作需要使用受控工作台界面完成，我已定位到对应步骤。",
                "agent_action": {
                    "intent": "workbench_handoff",
                    "arguments": {
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

        specs = [
            ("list_project_entities", "查询当前 GeoChem 工作区中的文章、表头、资源、样品、字段、规则或导出任务。必须使用它回答项目中实际有哪些对象。", ListEntitiesInput, list_entities, "read"),
            ("select_project_entity", "按精确 id 或名称选择当前会话对象。名称不能唯一匹配时会返回可点击候选，禁止猜测。", SelectEntityToolInput, select_entity, "write"),
            ("inspect_provenance", "查询已抽取/标准化数据的值、原文来源、页码、映射、换算与审核证据。", ProvenanceQueryInput, inspect_provenance, "read"),
            ("calculate_project_statistics", "由本地代码计算当前文章的记录数、字段统计和缺失情况。模型不得自行心算。", StatisticsQueryInput, calculate_statistics, "read"),
            ("search_public_literature", "检索 Crossref 与 OpenAlex 的公开学术元数据，可按最新或相关度排序。", LiteratureSearchToolInput, search_literature, "read"),
            ("request_article_import", "请求导入用户明确给出的 DOI 或公开 URL。只产生待确认动作，不绕过访问控制。", ImportArticleToolInput, request_import, "write"),
            ("start_curation_workflow", "请求处理当前已选择文章。只产生 LangGraph 工作流动作，正式流程仍有确认节点。", StartProcessingToolInput, start_processing, "write"),
            ("open_expert_workbench", "将复杂 PDF、表格、图像或批量候选编辑交接到精确工作台步骤。", WorkbenchHandoffToolInput, open_workbench, "manual"),
            ("request_curation_operation", "把资源校核、表格标准化、字段映射、表格/段落抽取、图像补值或候选编辑定位到精确专家工作台步骤。", CurationOperationInput, request_curation_operation, "manual"),
            ("request_governance_operation", "请求打开人工审核，或在用户明确确认后正式标准化/导出。不得声称未确认的操作已经完成。", GovernanceOperationInput, request_governance_operation, "critical"),
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
            call_id = f"ATC_{uuid4().hex[:20].upper()}"
            started = datetime.now().isoformat(timespec="milliseconds")
            safe_input = self._safe_summary(kwargs)
            idempotency = hashlib.sha256(json.dumps([run_id, name, safe_input], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
            db = self.pm.get_database(project_id)
            try:
                db.execute(
                    """INSERT INTO agent_tool_calls
                       (tool_call_id, run_id, thread_id, project_id, article_id, tool_name,
                        permission_level, input_summary_json, status, idempotency_key, started_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)""",
                    (call_id, run_id, thread_id, project_id, article_id, name, permission_level,
                     json.dumps(safe_input, ensure_ascii=False), idempotency, started),
                )
                db.commit()
            finally:
                db.close()
            try:
                result = func(**kwargs)
                db = self.pm.get_database(project_id)
                try:
                    db.execute(
                        """UPDATE agent_tool_calls SET status='completed', output_summary_json=?, finished_at=?
                           WHERE tool_call_id=?""",
                        (json.dumps(self._safe_summary(result), ensure_ascii=False), datetime.now().isoformat(timespec="milliseconds"), call_id),
                    )
                    db.commit()
                finally:
                    db.close()
                return result
            except Exception as exc:
                db = self.pm.get_database(project_id)
                try:
                    db.execute(
                        """UPDATE agent_tool_calls SET status='failed', error_message=?, finished_at=?
                           WHERE tool_call_id=?""",
                        (str(exc)[:500], datetime.now().isoformat(timespec="milliseconds"), call_id),
                    )
                    db.commit()
                finally:
                    db.close()
                raise

        return StructuredTool.from_function(
            func=audited,
            name=name,
            description=description,
            args_schema=args_schema,
        )

    @staticmethod
    def _safe_summary(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if any(term in str(key).lower() for term in ("api_key", "token", "secret", "password")):
                    result[str(key)] = "***"
                else:
                    result[str(key)] = AgentToolRegistry._safe_summary(item)
            return result
        if isinstance(value, list):
            return [AgentToolRegistry._safe_summary(item) for item in value[:30]]
        if isinstance(value, str):
            return value[:1200]
        return value

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
