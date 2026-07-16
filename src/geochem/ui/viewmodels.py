"""Qt view models and repository helpers for the desktop UI."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..core.config import AppConfig, load_config, save_config
from ..core.project import ProjectManager
from ..curation.header_config_importer import HeaderConfigImporter
from ..curation.target_headers import TargetHeaderBuilder
from ..curation import CostReporter, TraceService


class DataFrameTableModel(QAbstractTableModel):
    """Small generic table model for SQLite row dictionaries."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        columns: list[str] | None = None,
        editable: bool = False,
    ):
        super().__init__()
        self.rows = rows or []
        self.columns = columns or self._infer_columns(self.rows)
        self.editable = editable

    def set_rows(self, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
        self.beginResetModel()
        self.rows = rows
        self.columns = columns or self._infer_columns(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        value = self.rows[index.row()].get(self.columns[index.column()], "")
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return "" if value is None else str(value)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section]
        if orientation == Qt.Vertical:
            return str(section + 1)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if self.editable and index.isValid():
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if not self.editable or not index.isValid() or role != Qt.EditRole:
            return False
        column = self.columns[index.column()]
        self.rows[index.row()][column] = value
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def _infer_columns(self, rows: list[dict[str, Any]]) -> list[str]:
        if not rows:
            return []
        seen = []
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.append(key)
        return seen


class ProjectRepository:
    """Read-only UI repository over project SQLite databases."""

    def __init__(self, project_manager: ProjectManager | None = None):
        self.pm = project_manager or ProjectManager()

    def load_config(self) -> AppConfig:
        """Load the current application config."""
        config_path = Path(os.environ.get("GEOCHEM_CONFIG", "config/settings.yaml"))
        return load_config(config_path)

    def list_projects(self) -> list[dict[str, Any]]:
        return self.pm.list_projects()

    def ensure_default_workspace(self) -> dict[str, Any]:
        config, path = self.pm.ensure_default_workspace()
        return {"project_id": config.project_id, "project_name": config.project_name, "path": str(path)}

    def dashboard_metrics(self, project_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            scalar = lambda sql, params=(): db.fetch_one(sql, params)["cnt"]
            token_row = db.fetch_one(
                "SELECT COALESCE(SUM(total_tokens), 0) as tokens, COALESCE(SUM(estimated_cost), 0) as cost FROM llm_calls WHERE project_id = ?",
                (project_id,),
            )
            return {
                "articles": scalar("SELECT COUNT(*) as cnt FROM articles"),
                "resources": scalar("SELECT COUNT(*) as cnt FROM resources"),
                "candidate_tables": scalar("SELECT COUNT(*) as cnt FROM candidate_tables"),
                "pending_reviews": scalar("SELECT COUNT(*) as cnt FROM review_items WHERE status = 'pending'"),
                "mapping_rules": scalar("SELECT COUNT(*) as cnt FROM mapping_rules"),
                "learned_rules": scalar("SELECT COUNT(*) as cnt FROM learned_extraction_rules WHERE project_id = ?", (project_id,)),
                "standardized_records": scalar("SELECT COUNT(*) as cnt FROM standardized_records"),
                "total_tokens": token_row["tokens"],
                "estimated_cost": token_row["cost"],
            }
        finally:
            db.close()

    def recent_activity(self, project_id: str, limit: int = 20) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                "SELECT event_type, agent_name, skill_name, message, created_at FROM processing_events WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def resources(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT r.resource_id, r.resource_type, r.file_name, r.status, r.local_path,
                          r.source_url, a.article_id, a.title, a.doi
                   FROM resources r JOIN articles a ON r.article_id = a.article_id
                   ORDER BY r.created_at DESC"""
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def resource_discovery_rows(self, project_id: str) -> list[dict[str, Any]]:
        """Return LLM/reader-discovered candidate tables and figure/table assets.

        The import page should not list raw imported files here. Raw files live
        in the Resource List page; this panel starts empty and fills only after
        article/PDF parsing discovers actual candidate tables or figures.
        """
        db = self.pm.get_database(project_id)
        try:
            rows = []
            evidence = db.fetch_all(
                """SELECT e.evidence_id, e.article_id, e.resource_id, e.target_header, e.target_field,
                          e.evidence_type, e.evidence_text, e.page_or_section, e.asset_id,
                          e.confidence, e.status, a.title as article_title, a.doi
                   FROM article_evidence e JOIN articles a ON e.article_id = a.article_id
                   ORDER BY e.created_at DESC"""
            )
            for row in evidence:
                data = dict(row)
                rows.append({
                    **data,
                    "discovery_kind": "evidence",
                    "discovery_name": data.get("target_header") or data.get("target_field") or data.get("evidence_type"),
                    "discovery_type": data.get("evidence_type", "evidence"),
                    "source": data.get("doi") or data.get("article_title") or "",
                    "access_status": data.get("status", "candidate"),
                    "next_step": "抽取候选值",
                })
            assets = db.fetch_all(
                """SELECT ta.asset_id, ta.resource_id, ta.article_id, ta.table_number, ta.title,
                          ta.caption, ta.page_or_sheet, ta.extract_method, ta.confidence,
                          ta.status, a.title as article_title, a.doi
                   FROM table_assets ta JOIN articles a ON ta.article_id = a.article_id
                   ORDER BY ta.created_at DESC"""
            )
            for row in assets:
                data = dict(row)
                rows.append({
                    **data,
                    "discovery_kind": "table_asset",
                    "discovery_name": data.get("table_number") or data.get("title") or data.get("asset_id"),
                    "discovery_type": "图片/表格资产",
                    "source": data.get("doi") or data.get("article_title") or "",
                    "access_status": data.get("status", "pending"),
                    "next_step": "抽取表格" if data.get("status") == "pending" else data.get("status", ""),
                })
            tables = db.fetch_all(
                """SELECT t.table_id, t.article_id, t.resource_id, t.source_type, t.sheet_name,
                          t.page_number, t.title, t.row_count, t.col_count, t.confidence,
                          a.title as article_title, a.doi
                   FROM candidate_tables t JOIN articles a ON t.article_id = a.article_id
                   ORDER BY t.created_at DESC"""
            )
            for row in tables:
                data = dict(row)
                label = data.get("title") or data.get("sheet_name") or data.get("table_id")
                rows.append({
                    **data,
                    "discovery_kind": "candidate_table",
                    "discovery_name": label,
                    "discovery_type": f"表格 {data.get('row_count', 0)}x{data.get('col_count', 0)}",
                    "source": data.get("doi") or data.get("article_title") or "",
                    "access_status": "已解析",
                    "next_step": "字段映射",
                })
            return rows
        finally:
            db.close()

    def resource_summary(self, project_id: str) -> dict[str, Any]:
        """Return resource counters for the document import page."""
        rows = self.resources(project_id)
        return {
            "imported_files": len([r for r in rows if r.get("local_path")]),
            "public_links": len([r for r in rows if r.get("source_url") or r.get("resource_type") == "html_page"]),
            "needs_login": len([r for r in rows if r.get("status") in {"needs_login", "auth_required"}]),
            "pending_parse": len([r for r in rows if r.get("status") == "pending"]),
        }

    def latest_article_info(self, project_id: str) -> dict[str, Any] | None:
        """Return the newest article metadata for the right-side import panel."""
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT article_id, title, authors, year, journal, doi, url, status, created_at
                   FROM articles ORDER BY created_at DESC LIMIT 1"""
            )
            if not row:
                return None
            data = dict(row)
            try:
                authors = json.loads(data.get("authors") or "[]")
                data["authors"] = ", ".join(authors) if isinstance(authors, list) else str(authors)
            except Exception:
                data["authors"] = data.get("authors") or "--"
            return data
        finally:
            db.close()

    def list_header_configs(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT config_id, name, source_file, description, status, created_at, updated_at,
                          headers_json
                   FROM header_configs
                   WHERE project_id = ? AND status != 'deleted'
                   ORDER BY updated_at DESC""",
                (project_id,),
            )
            result = []
            for row in rows:
                data = dict(row)
                try:
                    data["field_count"] = len(json.loads(data.pop("headers_json") or "[]"))
                except Exception:
                    data["field_count"] = 0
                result.append(data)
            return result
        finally:
            db.close()

    def load_header_config(self, project_id: str, config_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one("SELECT * FROM header_configs WHERE project_id = ? AND config_id = ?", (project_id, config_id))
            if not row:
                return [], {}
            rows = json.loads(row["headers_json"] or "[]")
            summary = self._header_summary(rows, source_file=row["source_file"], name=row["name"])
            return rows, summary
        finally:
            db.close()

    def save_header_config(
        self,
        project_id: str,
        name: str,
        rows: list[dict[str, Any]],
        config_id: str | None = None,
        source_file: str = "",
        description: str = "",
    ) -> str:
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            from datetime import datetime

            now = datetime.now().isoformat()
            if config_id:
                db.execute(
                    """UPDATE header_configs
                       SET name = ?, source_file = ?, description = ?, headers_json = ?, updated_at = ?
                       WHERE project_id = ? AND config_id = ?""",
                    (name, source_file, description, json.dumps(rows, ensure_ascii=False), now, project_id, config_id),
                )
            else:
                config_id = self._generate_id(db, "HCFG", "header_configs", "config_id")
                db.execute(
                    """INSERT INTO header_configs
                       (config_id, project_id, name, source_file, description, headers_json, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (config_id, project_id, name, source_file, description, json.dumps(rows, ensure_ascii=False), "active", now, now),
                )
            db.commit()
            self._write_header_backup(project_dir, config_id, name, rows, source_file, description)
            return config_id
        finally:
            db.close()

    def suggest_header_config_for_article(self, project_id: str, article_id: str | None = None) -> dict[str, Any] | None:
        """Heuristic placeholder for later LLM-based header preset selection."""
        configs = self.list_header_configs(project_id)
        if not configs:
            return None
        chosen = configs[0]
        reason = "暂用最近更新的表头配置；后续会由 LLM 根据文章主题、资源类型和字段覆盖率预选择。"
        return {**chosen, "confidence": 0.65, "reason": reason, "suggested_by": "heuristic"}

    def assign_header_config_to_article(
        self,
        project_id: str,
        article_id: str,
        config_id: str,
        suggested_by: str = "user",
        confidence: float = 1.0,
        reason: str = "",
    ) -> str:
        db = self.pm.get_database(project_id)
        try:
            from datetime import datetime

            assignment_id = self._generate_id(db, "HASN", "article_header_assignments", "assignment_id")
            db.execute(
                """INSERT INTO article_header_assignments
                   (assignment_id, project_id, article_id, config_id, suggested_by, confidence, status, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (assignment_id, project_id, article_id, config_id, suggested_by, confidence, "confirmed", reason, datetime.now().isoformat()),
            )
            db.commit()
            return assignment_id
        finally:
            db.close()

    def header_config_from_file(
        self,
        file_path: str | Path,
        descriptions_path: str | Path | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Parse a CSV/Excel header row into UI-ready target header definitions."""
        path = Path(file_path)
        headers, sample_rows = HeaderConfigImporter().read_headers(path)
        if not headers:
            return [], {
                "field_count": 0,
                "required_count": 0,
                "risk_count": 0,
                "unconfigured_count": 0,
                "health_percent": 0,
                "advice": "未读取到可用表头。",
            }
        targets = TargetHeaderBuilder().build(headers, descriptions_path)
        required_fields = {"SampleID", "Sample_ID", "DOI", "Reference"}
        risk_groups = {"major_elements", "trace_elements", "ree", "iron_speciation", "isotope_organic"}
        rows = []
        for idx, target in enumerate(targets, start=1):
            samples = []
            for raw_row in sample_rows[:8]:
                if idx - 1 < len(raw_row) and raw_row[idx - 1] not in (None, ""):
                    samples.append(raw_row[idx - 1])
            is_required = target.canonical_field in required_fields
            needs_review = bool(target.target_unit and target.field_group in risk_groups)
            rows.append({
                "序号": idx,
                "字段名": target.display_header,
                "类型": self._infer_header_dtype(target.field_group, target.target_unit),
                "默认单位": target.target_unit or "—",
                "别名数量": 0,
                "审核策略": "单位/换算需审核" if needs_review else "自动校验",
                "必填": "是" if is_required else "—",
                "状态": "配置中" if needs_review else "已完成",
                "字段组": target.field_group,
                "canonical_field": target.canonical_field,
                "description": target.description,
                "aliases": target.source_header if target.source_header != target.display_header else "—",
                "chemistry": self._infer_chemistry(target.canonical_field, target.target_unit),
                "risk_reason": "涉及目标单位或化学形态，首次换算会进入人工审核。" if needs_review else "该字段可按规则自动校验。",
                "sample_values": samples,
            })
        risk_count = len([r for r in rows if r["状态"] == "配置中"])
        required_count = len([r for r in rows if r["必填"] == "是"])
        unconfigured = len([r for r in rows if not r.get("description")])
        health = int(round((len(rows) - unconfigured) / max(len(rows), 1) * 100))
        summary = {
            "field_count": len(rows),
            "required_count": required_count,
            "risk_count": risk_count,
            "unconfigured_count": unconfigured,
            "health_percent": health,
            "advice": f"已读取 {len(rows)} 个目标表头。导出会保持 Excel 表头顺序和单位；{risk_count} 个带单位/形态风险字段建议首次审核。",
        }
        return rows, summary

    def header_config_from_excel(
        self,
        file_path: str | Path,
        descriptions_path: str | Path | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Backward-compatible wrapper for tests and older callers."""
        return self.header_config_from_file(file_path, descriptions_path)

    def _header_summary(self, rows: list[dict[str, Any]], source_file: str = "", name: str = "") -> dict[str, Any]:
        risk_count = len([r for r in rows if r.get("状态") == "配置中" or "审核" in str(r.get("审核策略", ""))])
        required_count = len([r for r in rows if r.get("必填") == "是"])
        unconfigured = len([r for r in rows if not r.get("description")])
        health = int(round((len(rows) - unconfigured) / max(len(rows), 1) * 100))
        return {
            "field_count": len(rows),
            "required_count": required_count,
            "risk_count": risk_count,
            "unconfigured_count": unconfigured,
            "health_percent": health,
            "source_file": source_file,
            "name": name,
            "advice": f"已载入表头配置「{name or '未命名'}」，共 {len(rows)} 个字段。可直接编辑表格或右侧字段详情，然后保存为预设。",
        }

    def candidate_tables(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT table_id, article_id, resource_id, sheet_name, page_number, row_count,
                          col_count, extract_method, confidence, created_at
                   FROM candidate_tables ORDER BY created_at DESC"""
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def candidate_rows(self, project_id: str, table_id: str, limit: int = 200) -> tuple[list[dict[str, Any]], list[str]]:
        db = self.pm.get_database(project_id)
        try:
            columns = [row["raw_name"] for row in db.fetch_all("SELECT raw_name FROM candidate_columns WHERE table_id = ? ORDER BY column_id", (table_id,))]
            rows = []
            for row in db.fetch_all("SELECT raw_data FROM candidate_rows WHERE table_id = ? ORDER BY row_index LIMIT ?", (table_id, limit)):
                rows.append(json.loads(row["raw_data"]))
            return rows, columns
        finally:
            db.close()

    def mappings(self, project_id: str, table_id: str | None = None) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            if table_id:
                rows = db.fetch_all("SELECT * FROM field_mappings WHERE table_id = ? ORDER BY risk_level DESC, source_field", (table_id,))
            else:
                rows = db.fetch_all("SELECT * FROM field_mappings ORDER BY created_at DESC")
            return [dict(row) for row in rows]
        finally:
            db.close()

    def extraction_candidates(self, project_id: str, article_id: str | None = None) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            params: list[Any] = [project_id]
            where = "WHERE project_id = ?"
            if article_id:
                where += " AND article_id = ?"
                params.append(article_id)
            rows = db.fetch_all(
                f"""SELECT * FROM extraction_candidates
                    {where}
                    ORDER BY created_at DESC, candidate_id""",
                tuple(params),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def review_items(
        self,
        project_id: str,
        status: str | None = "pending",
        risk_level: str | None = None,
        item_type: str | None = None,
    ) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            where: list[str] = []
            params: list[Any] = []
            if status and status != "all":
                where.append("status = ?")
                params.append(status)
            if risk_level and risk_level != "all":
                where.append("risk_level = ?")
                params.append(risk_level)
            if item_type and item_type != "all":
                where.append("item_type = ?")
                params.append(item_type)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            rows = db.fetch_all(
                f"""SELECT * FROM review_items {clause}
                   ORDER BY CASE risk_level WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at""",
                tuple(params),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def review_decisions(self, project_id: str, review_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                "SELECT * FROM review_decisions WHERE review_id = ? ORDER BY decided_at",
                (review_id,),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def standardized_records(self, project_id: str, table_id: str | None = None, limit: int = 300,
                              quality_grade: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        db = self.pm.get_database(project_id)
        try:
            where: list[str] = []
            params: list[Any] = []
            if table_id:
                where.append("table_id = ?")
                params.append(table_id)
            if quality_grade and quality_grade != "all":
                where.append("quality_grade = ?")
                params.append(quality_grade)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            records = db.fetch_all(
                f"SELECT * FROM standardized_records {clause} ORDER BY source_row LIMIT ?",
                tuple(params) + (limit,),
            )
            rows = []
            fields = []
            for record in records:
                data = json.loads(record["data"])
                data["Quality_Grade"] = record["quality_grade"]
                data["Source_File"] = record["source_file"] or ""
                data["Source_Table"] = record["source_table"] or ""
                data["Source_Row"] = record["source_row"]
                data["DOI"] = record["doi"] or ""
                data["Record_ID"] = record["record_id"]
                rows.append(data)
                for key in data:
                    if key not in fields:
                        fields.append(key)
            return rows, fields
        finally:
            db.close()

    def standardized_record_context(self, project_id: str, record_id: str) -> dict[str, Any]:
        """Return source context for a standardized row, including PDF page preview when possible."""
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT sr.*, ct.page_number, ct.title as table_title, ct.caption as table_caption,
                          ct.sheet_name, r.local_path as resource_local_path, r.file_name as resource_file_name,
                          a.title as article_title, a.doi as article_doi
                   FROM standardized_records sr
                   JOIN candidate_tables ct ON sr.table_id = ct.table_id
                   JOIN resources r ON ct.resource_id = r.resource_id
                   JOIN articles a ON sr.article_id = a.article_id
                   WHERE sr.record_id = ? AND a.project_id = ?""",
                (record_id, project_id),
            )
            if not row:
                return {}
            data = dict(row)
            raw_row = db.fetch_one(
                "SELECT row_index, raw_data FROM candidate_rows WHERE row_id = ?",
                (data.get("row_id"),),
            )
            if raw_row:
                data["candidate_row"] = json.loads(raw_row["raw_data"] or "{}")

            _config, project_dir = self.pm.load_project(project_id)
            local_path = data.get("resource_local_path") or ""
            pdf_path = Path(local_path)
            if local_path and not pdf_path.is_absolute():
                pdf_path = project_dir / local_path
            data["resource_abs_path"] = str(pdf_path) if local_path else ""
            data["page_preview_path"] = ""
            if pdf_path.exists() and pdf_path.suffix.lower() == ".pdf" and data.get("page_number"):
                data["page_preview_path"] = self._render_pdf_page_preview(pdf_path, int(data["page_number"]))
            return data
        finally:
            db.close()

    def trace_rows(self, project_id: str, table_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            return TraceService().trace_table(db, table_id)
        finally:
            db.close()

    def cost_rows(self, project_id: str, group_by: str = "model") -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            return CostReporter().report(db, project_id, group_by)
        finally:
            db.close()

    def articles_for_filter(self, project_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT article_id, title, doi, year, journal, status, created_at
                   FROM articles
                   WHERE project_id = ?
                   ORDER BY created_at DESC""",
                (project_id,),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def pdf_resources_for_article(self, project_id: str, article_id: str | None) -> list[dict[str, Any]]:
        """Return PDF resources for the selected article with absolute paths and page counts."""
        if not article_id:
            return []
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT r.resource_id, r.article_id, r.resource_type, r.file_name, r.local_path,
                          r.source_url, r.status, a.title as article_title, a.doi
                   FROM resources r
                   JOIN articles a ON r.article_id = a.article_id
                   WHERE a.project_id = ?
                     AND a.article_id = ?
                     AND r.resource_type IN ('main_pdf', 'supplementary_pdf')
                   ORDER BY CASE r.resource_type WHEN 'main_pdf' THEN 0 ELSE 1 END, r.created_at""",
                (project_id, article_id),
            )
            _config, project_dir = self.pm.load_project(project_id)
            result: list[dict[str, Any]] = []
            for row in rows:
                data = dict(row)
                local_path = data.get("local_path") or ""
                path = Path(local_path)
                if local_path and not path.is_absolute():
                    path = project_dir / local_path
                data["resource_abs_path"] = str(path) if local_path else ""
                data["page_count"] = self._pdf_page_count(path)
                result.append(data)
            return result
        finally:
            db.close()

    def _pdf_page_count(self, pdf_path: Path) -> int:
        if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
            return 0
        try:
            import fitz

            with fitz.open(str(pdf_path)) as doc:
                return len(doc)
        except Exception:
            return 0

    def _infer_header_dtype(self, group: str, unit: str) -> str:
        if group in {"basic_info", "sample_context"}:
            return "文本"
        if group == "location":
            return "坐标"
        if group == "stratigraphy_age":
            return "年代/深度"
        return "数值"

    def _infer_chemistry(self, canonical_field: str, unit: str) -> str:
        if canonical_field.endswith("O") or "2O" in canonical_field or "O3" in canonical_field or "O5" in canonical_field:
            return "oxide（氧化物）"
        if unit:
            return "element/ratio（按目标单位确认）"
        return "无单位字段"

    def _resource_access_status(self, row: dict[str, Any]) -> str:
        if row.get("status") == "auth_confirmed":
            return "已确认可访问"
        if row.get("resource_type") == "html_page":
            return "已获取网页证据"
        if row.get("source_url"):
            return "可访问"
        return "已导入" if row.get("status") else "待处理"

    def _write_header_backup(
        self,
        project_dir: Path,
        config_id: str,
        name: str,
        rows: list[dict[str, Any]],
        source_file: str,
        description: str,
    ) -> None:
        out_dir = project_dir / "headers"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "config_id": config_id,
            "name": name,
            "source_file": source_file,
            "description": description,
            "headers": rows,
        }
        (out_dir / f"{config_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _generate_id(self, db, prefix: str, table: str, column: str) -> str:
        row = db.fetch_one(
            f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id FROM {table} WHERE {column} LIKE ?",
            (f"{prefix}_%",),
        )
        return f"{prefix}_{(row['max_id'] or 0) + 1:06d}"

    def token_stats(self, project_id: str, article_id: str | None = None, model_name: str | None = None) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            where = ["project_id = ?"]
            params: list[Any] = [project_id]
            if article_id:
                where.append("article_id = ?")
                params.append(article_id)
            if model_name:
                where.append("model_name = ?")
                params.append(model_name)
            clause = " AND ".join(where)
            summary = db.fetch_one(
                f"""SELECT COUNT(*) as calls,
                           COALESCE(SUM(input_tokens), 0) as input_tokens,
                           COALESCE(SUM(output_tokens), 0) as output_tokens,
                           COALESCE(SUM(total_tokens), 0) as total_tokens,
                           COALESCE(SUM(estimated_cost), 0) as estimated_cost,
                           COALESCE(AVG(CASE WHEN total_tokens > 0 THEN estimated_cost ELSE NULL END), 0) as avg_cost
                    FROM llm_calls WHERE {clause}""",
                tuple(params),
            )
            daily = db.fetch_all(
                f"""SELECT SUBSTR(started_at, 1, 10) as day,
                           COALESCE(SUM(total_tokens), 0) as total_tokens,
                           COALESCE(SUM(estimated_cost), 0) as estimated_cost,
                           COUNT(*) as calls
                    FROM llm_calls WHERE {clause}
                    GROUP BY day ORDER BY day""",
                tuple(params),
            )
            agent_rows = db.fetch_all(
                f"""SELECT COALESCE(NULLIF(agent_name, ''), 'Unknown') as agent_name,
                           COALESCE(NULLIF(skill_name, ''), 'Unknown') as skill_name,
                           COALESCE(SUM(total_tokens), 0) as total_tokens
                    FROM llm_calls WHERE {clause}
                    GROUP BY agent_name, skill_name ORDER BY total_tokens DESC""",
                tuple(params),
            )
            model_rows = db.fetch_all(
                f"""SELECT COALESCE(NULLIF(model_provider, ''), 'unknown') as model_provider,
                           COALESCE(NULLIF(model_name, ''), 'unknown') as model_name,
                           COALESCE(SUM(total_tokens), 0) as total_tokens,
                           COALESCE(SUM(estimated_cost), 0) as estimated_cost,
                           COUNT(*) as calls
                    FROM llm_calls WHERE {clause}
                    GROUP BY model_provider, model_name ORDER BY total_tokens DESC""",
                tuple(params),
            )
            calls = db.fetch_all(
                f"""SELECT started_at, agent_name, skill_name, model_provider, model_name,
                           input_tokens, output_tokens, total_tokens, estimated_cost,
                           latency_ms, status, retry_count
                    FROM llm_calls WHERE {clause}
                    ORDER BY started_at DESC LIMIT 500""",
                tuple(params),
            )
            anomaly_threshold = max(50000, (summary["total_tokens"] or 0) / max(summary["calls"] or 1, 1) * 2.5)
            anomalies = db.fetch_all(
                f"""SELECT started_at, agent_name, model_name, total_tokens, estimated_cost,
                           status, retry_count
                    FROM llm_calls
                    WHERE {clause} AND (total_tokens >= ? OR retry_count > 0 OR status != 'success')
                    ORDER BY total_tokens DESC LIMIT 20""",
                tuple(params + [anomaly_threshold]),
            )
            models = db.fetch_all(
                "SELECT DISTINCT model_name FROM llm_calls WHERE project_id = ? AND model_name != '' ORDER BY model_name",
                (project_id,),
            )
            return {
                "summary": dict(summary),
                "daily": [dict(row) for row in daily],
                "agents": [dict(row) for row in agent_rows],
                "models": [dict(row) for row in model_rows],
                "calls": [dict(row) for row in calls],
                "anomalies": [dict(row) for row in anomalies],
                "model_options": [row["model_name"] for row in models],
            }
        finally:
            db.close()

    def project_path(self, project_id: str) -> Path:
        return self.pm.load_project(project_id)[1]

    def settings_data(self, project_id: str | None = None) -> dict[str, Any]:
        config_path = Path(os.environ.get("GEOCHEM_CONFIG", "config/settings.yaml"))
        config = load_config(config_path)
        default_task = config.get_task_model("_default")
        providers = []
        for provider in config.providers:
            env_name = ""
            key_status = "not_configured"
            if provider.api_key and provider.api_key.startswith("${") and provider.api_key.endswith("}"):
                env_name = provider.api_key[2:-1]
                key_status = "configured" if os.environ.get(env_name) else "missing"
            elif provider.api_key:
                key_status = "inline"
            providers.append({
                "name": provider.name,
                "display_name": provider.display_name or provider.name,
                "base_url": provider.base_url or "",
                "api_format": provider.api_format,
                "enabled": provider.enabled,
                "api_key_ref": provider.api_key or "",
                "api_key_mask": self._mask_key(provider.api_key or ""),
                "env_name": env_name,
                "key_status": key_status,
                "models": [m.name for m in provider.models],
            })
        if default_task:
            providers.sort(key=lambda item: 0 if item["name"] == default_task.provider else 1)
        project_path = ""
        project_name = ""
        if project_id:
            try:
                config_obj, path = self.pm.load_project(project_id)
                project_path = str(path)
                project_name = config_obj.project_name
            except Exception:
                pass
        prefs = config.ui_preferences or {}
        return {
            "config_path": str(config_path),
            "default_project_dir": config.default_project_dir,
            "log_level": config.log_level,
            "log_dir": config.log_dir,
            "providers": providers,
            "default_provider": default_task.provider if default_task else "",
            "default_model": default_task.model if default_task else "",
            "temperature": default_task.temperature if default_task else 0.1,
            "max_tokens": default_task.max_tokens if default_task else 4096,
            "fallback_chain": config.fallback_chain,
            "project_path": project_path,
            "project_name": project_name,
            "cache_size": self._dir_size(Path(project_path) / "calculations") if project_path else 0,
            # UI preferences
            "timeout": prefs.get("timeout", 60),
            "max_retries": prefs.get("max_retries", 3),
            "max_parallel": prefs.get("max_parallel", 5),
            "enable_cache": prefs.get("enable_cache", True),
            "enable_token_tracking": prefs.get("enable_token_tracking", True),
            "enable_cost_estimation": prefs.get("enable_cost_estimation", True),
            "temp_dir": prefs.get("temp_dir", str(Path.home() / "GeoChem_Temp")),
            "autosave_interval": prefs.get("autosave_interval", 5),
            "restore_last_project": prefs.get("restore_last_project", True),
            "enable_local_cache": prefs.get("enable_local_cache", True),
            "export_format": prefs.get("export_format", "Excel (.xlsx)"),
            "auto_trace": prefs.get("auto_trace", True),
            "auto_audit": prefs.get("auto_audit", True),
            "calc_archive_format": prefs.get("calc_archive_format", "Markdown + JSONL"),
            "high_risk_force_review": prefs.get("high_risk_force_review", True),
            "auto_apply_rules": prefs.get("auto_apply_rules", True),
            "review_threshold": prefs.get("review_threshold", 0.75),
            "teaching_enabled": prefs.get("teaching_enabled", True),
            "compact_mode": prefs.get("compact_mode", False),
            "notifications": prefs.get("notifications", True),
            "tested_models": prefs.get("tested_models", []),
        }

    def save_settings(self, updates: dict[str, Any]) -> None:
        config_path = Path(os.environ.get("GEOCHEM_CONFIG", "config/settings.yaml"))
        config = load_config(config_path)

        # --- Core LLM settings ---
        if "default_project_dir" in updates:
            config.default_project_dir = updates["default_project_dir"]

        if "default_provider" in updates or "default_model" in updates or "temperature" in updates:
            task = config.get_task_model("_default")
            if task:
                task.provider = updates.get("default_provider", task.provider)
                task.model = updates.get("default_model", task.model)
                task.temperature = float(updates.get("temperature", task.temperature))
                config.task_models["_default"] = task

        # --- Update provider API keys / base URLs if user edited them in the UI ---
        if "providers" in updates:
            provider_updates = {p["name"]: p for p in updates["providers"]}
            for provider in config.providers:
                if provider.name in provider_updates:
                    pu = provider_updates[provider.name]
                    # Update api_key if present (including empty string = cleared)
                    if "api_key" in pu:
                        new_key = pu["api_key"]
                        if new_key is not None:
                            provider.api_key = new_key or None
                    # Update base_url if the user changed it
                    if "base_url" in pu:
                        new_url = pu["base_url"]
                        if new_url is not None:
                            provider.base_url = new_url or None

        # --- UI preferences ---
        ui_keys = (
            "timeout", "max_retries", "max_parallel",
            "enable_cache", "enable_token_tracking", "enable_cost_estimation",
            "temp_dir", "autosave_interval", "restore_last_project", "enable_local_cache",
            "export_format", "auto_trace", "auto_audit", "calc_archive_format",
            "high_risk_force_review", "auto_apply_rules", "review_threshold", "teaching_enabled",
            "compact_mode", "notifications", "tested_models",
        )
        for key in ui_keys:
            if key in updates:
                config.ui_preferences[key] = updates[key]

        save_config(config, config_path)

    def test_provider_connection(
        self,
        provider_name: str,
        model: str,
        api_key: str = "",
        base_url: str = "",
    ) -> dict[str, Any]:
        """Test a provider connection. Returns {success, message, latency_ms}.

        If *api_key* or *base_url* are given they temporarily override the
        config values so the user can test before saving.  The fallback chain
        is disabled so errors from the *specific* provider are reported.
        """
        from ..core.exceptions import (
            APIKeyError,
            LLMError,
            ProviderConnectionError,
            ProviderNotFoundError,
        )
        from ..providers.llm_client import LLMClient

        config_path = Path(os.environ.get("GEOCHEM_CONFIG", "config/settings.yaml"))
        config = load_config(config_path)

        # Temporarily patch the target provider with user-supplied values
        if api_key or base_url:
            for prov in config.providers:
                if prov.name == provider_name:
                    if api_key:
                        prov.api_key = api_key
                    if base_url:
                        prov.base_url = base_url
                    break

        # Disable fallback chain so we only test the requested provider
        config.fallback_chain = []

        try:
            client = LLMClient(config)

            # Verify the provider was actually initialized
            from ..core.exceptions import ProviderNotFoundError as PNF
            try:
                client.registry.get(provider_name)
            except PNF:
                return {
                    "success": False,
                    "message": f"服务商「{provider_name}」未初始化，请检查配置或安装对应 SDK（pip install openai / anthropic）",
                }

            import time

            start = time.time()
            response = client.chat(
                messages=[{"role": "user", "content": "Reply with exactly: pong"}],
                provider_override=provider_name,
                model_override=model,
                max_tokens_override=32,
                use_cache=False,
            )
            latency = int((time.time() - start) * 1000)
            snippet = (response.content or "")[:60].replace("\n", " ")
            return {
                "success": True,
                "message": f"连接成功 ✓  延迟 {latency}ms  回复: {snippet}",
                "latency_ms": latency,
            }
        except APIKeyError as e:
            return {"success": False, "message": f"API Key 无效或未配置: {e}"}
        except ProviderNotFoundError:
            return {"success": False, "message": f"服务商「{provider_name}」未初始化，请检查配置或安装对应 SDK"}
        except ProviderConnectionError as e:
            return {"success": False, "message": f"无法连接到服务器: {e}"}
        except LLMError as e:
            msg = str(e)
            # When fallback chain is empty, "All providers failed" wraps the real error
            if "All providers failed" in msg and "Last error:" in msg:
                real_error = msg.split("Last error:")[-1].strip()
                if real_error and real_error != "None":
                    return {"success": False, "message": real_error}
            return {"success": False, "message": f"LLM 调用失败: {e}"}
        except Exception as e:
            return {"success": False, "message": f"未知错误: {e}"}

    def _mask_key(self, key: str) -> str:
        if not key:
            return "未配置"
        if key.startswith("${") and key.endswith("}"):
            env_name = key[2:-1]
            value = os.environ.get(env_name, "")
            if not value:
                return f"{key}（未检测到）"
            return f"{env_name}: {value[:6]}{'*' * 14}{value[-4:] if len(value) > 10 else ''}"
        return f"{key[:6]}{'*' * 14}{key[-4:] if len(key) > 10 else ''}"

    def _hydrate_table_asset_preview(
        self,
        db,
        project_id: str,
        row: dict[str, Any],
        id_key: str = "asset_id",
    ) -> dict[str, Any]:
        """Backfill preview images for table assets created before previews existed."""
        existing = row.get("raw_file_path") or ""
        if existing and Path(existing).exists():
            return row
        page_num = self._page_number(row.get("page_or_sheet", ""))
        if not page_num:
            return row
        try:
            _config, project_dir = self.pm.load_project(project_id)
            local_path = row.get("resource_local_path") or ""
            if not local_path:
                return row
            pdf_path = Path(local_path)
            if not pdf_path.is_absolute():
                pdf_path = project_dir / local_path
            if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
                return row
            preview_path = self._render_pdf_table_preview(pdf_path, page_num)
            if preview_path:
                row["raw_file_path"] = preview_path
                asset_id = row.get(id_key) or row.get("asset_id")
                if asset_id:
                    db.execute("UPDATE table_assets SET raw_file_path = ? WHERE asset_id = ?", (preview_path, asset_id))
                    db.commit()
        except Exception:
            return row
        return row

    def _page_number(self, value: str) -> int | None:
        match = re.search(r"page\s+(\d+)", value or "", flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _render_pdf_table_preview(self, pdf_path: Path, page_num: int) -> str:
        try:
            import fitz

            out_dir = pdf_path.parent / ".cache" / "pages"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{pdf_path.stem}_table_{page_num}.png"
            if out_path.exists():
                return str(out_path)
            with fitz.open(str(pdf_path)) as doc:
                page_index = page_num - 1
                if page_index < 0 or page_index >= len(doc):
                    return ""
                page = doc[page_index]
                rect = page.rect
                clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.82)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False)
                pix.save(str(out_path))
            return str(out_path)
        except Exception:
            return ""

    def _render_pdf_page_preview(self, pdf_path: Path, page_num: int) -> str:
        try:
            import fitz

            out_dir = pdf_path.parent / ".cache" / "pages"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{pdf_path.stem}_page_{page_num}.png"
            if out_path.exists():
                return str(out_path)
            with fitz.open(str(pdf_path)) as doc:
                page_index = page_num - 1
                if page_index < 0 or page_index >= len(doc):
                    return ""
                page = doc[page_index]
                pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
                pix.save(str(out_path))
            return str(out_path)
        except Exception:
            return ""

    def _hydrate_evidence_context(self, project_id: str, row: dict[str, Any]) -> dict[str, Any]:
        """Expand short PDF evidence lines into fuller page context for extraction preview."""
        if row.get("item_type") != "text":
            return row
        original = (row.get("evidence_text") or "").split("\n[PATH]", 1)[0].strip()
        if len(original) >= 220:
            return row
        page_num = self._page_number(row.get("page_or_section", ""))
        if not page_num or not original:
            return row
        try:
            _config, project_dir = self.pm.load_project(project_id)
            local_path = row.get("resource_local_path") or ""
            if not local_path:
                return row
            pdf_path = Path(local_path)
            if not pdf_path.is_absolute():
                pdf_path = project_dir / local_path
            if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
                return row
            context = self._pdf_context_for_line(pdf_path, page_num, original)
            if context and len(context) > len(original):
                row["evidence_text"] = context
        except Exception:
            return row
        return row

    def _pdf_context_for_line(self, pdf_path: Path, page_num: int, needle: str) -> str:
        try:
            import fitz

            with fitz.open(str(pdf_path)) as doc:
                page_index = page_num - 1
                if page_index < 0 or page_index >= len(doc):
                    return ""
                text = doc[page_index].get_text("text") or ""
            lines = [" ".join(line.split()) for line in text.splitlines()]
            lines = [line for line in lines if line]
            if not lines:
                return ""
            compact_needle = " ".join(needle.split()).lower()
            hit = -1
            for idx, line in enumerate(lines):
                lowered = line.lower()
                if compact_needle in lowered or lowered in compact_needle:
                    hit = idx
                    break
            if hit < 0:
                return ""
            start = hit
            while start > 0 and len(" ".join(lines[start:hit + 1])) < 550:
                candidate = lines[start - 1]
                if self._looks_like_heading(candidate):
                    break
                start -= 1
            end = hit
            while end + 1 < len(lines) and len(" ".join(lines[start:end + 2])) < 900:
                candidate = lines[end + 1]
                if self._looks_like_heading(candidate):
                    break
                end += 1
            return " ".join(lines[start:end + 1])[:1200]
        except Exception:
            return ""

    def _looks_like_heading(self, value: str) -> bool:
        text = value.strip()
        if len(text) < 4:
            return True
        if len(text) < 80 and text.upper() == text and any(ch.isalpha() for ch in text):
            return True
        return bool(re.match(r"^\d+(\.\d+)*\s+[A-Z]", text))

    def discovered_elements(
        self,
        project_id: str,
        resource_id: str | None = None,
        article_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return categorized discovered elements: tables, figures, links, text."""
        db = self.pm.get_database(project_id)
        try:
            table_filters = ["a.project_id = ?"]
            params_list: list[Any] = [project_id]
            if resource_id:
                table_filters.append("ta.resource_id = ?")
                params_list.append(resource_id)
            if article_id:
                table_filters.append("ta.article_id = ?")
                params_list.append(article_id)
            table_where = "WHERE " + " AND ".join(table_filters)
            params = tuple(params_list)
            tables = db.fetch_all(
                f"""SELECT ta.asset_id, ta.article_id, ta.title, ta.page_or_sheet, ta.confidence, ta.status,
                           ta.resource_id, ta.caption, a.title as article_title,
                           ta.raw_file_path, r.local_path as resource_local_path,
                           ct.table_id as candidate_table_id,
                           COALESCE(ct.row_count, 0) as row_count,
                           COALESCE(ct.col_count, 0) as col_count
                    FROM table_assets ta
                    JOIN articles a ON ta.article_id = a.article_id
                    JOIN resources r ON ta.resource_id = r.resource_id
                    LEFT JOIN candidate_tables ct ON ct.asset_id = ta.asset_id
                    {table_where}
                    ORDER BY ta.created_at""",
                params,
            )

            evidence_filters = ["e.project_id = ?"]
            evidence_params: list[Any] = [project_id]
            if resource_id:
                evidence_filters.append("e.resource_id = ?")
                evidence_params.append(resource_id)
            if article_id:
                evidence_filters.append("e.article_id = ?")
                evidence_params.append(article_id)
            evidence_where = " AND ".join(evidence_filters)
            evidence_tuple = tuple(evidence_params)
            figures = db.fetch_all(
                f"""SELECT e.evidence_id, e.article_id, e.evidence_type, e.evidence_text, e.page_or_section,
                           e.confidence, e.status, e.resource_id, e.target_header, e.target_field,
                           e.local_path, r.local_path as resource_local_path
                    FROM article_evidence e
                    LEFT JOIN resources r ON e.resource_id = r.resource_id
                    WHERE e.evidence_type IN ('figure', 'figure_caption') AND {evidence_where}
                    ORDER BY e.page_or_section""",
                evidence_tuple,
            )

            links = db.fetch_all(
                f"""SELECT e.evidence_id, e.article_id, e.evidence_text as url, e.page_or_section, e.resource_id,
                           e.local_path, r.local_path as resource_local_path
                    FROM article_evidence e
                    LEFT JOIN resources r ON e.resource_id = r.resource_id
                    WHERE e.evidence_type = 'link' AND {evidence_where}
                    ORDER BY e.page_or_section""",
                evidence_tuple,
            )

            text_sections = db.fetch_all(
                f"""SELECT e.evidence_id, e.article_id, e.evidence_type, e.evidence_text, e.page_or_section,
                           e.resource_id, e.target_header, e.target_field, e.confidence, e.status,
                           e.local_path, r.local_path as resource_local_path
                    FROM article_evidence e
                    LEFT JOIN resources r ON e.resource_id = r.resource_id
                    WHERE e.evidence_type IN ('paragraph', 'table_caption') AND {evidence_where}
                    ORDER BY e.page_or_section""",
                evidence_tuple,
            )

            return {
                "tables": [
                    self._with_resource_abs_path(project_id, self._hydrate_table_asset_preview(db, project_id, dict(r)))
                    for r in tables
                ],
                "figures": [self._with_resource_abs_path(project_id, dict(r)) for r in figures],
                "links": [self._with_resource_abs_path(project_id, dict(r)) for r in links],
                "text": [self._with_resource_abs_path(project_id, dict(r)) for r in text_sections],
            }
        finally:
            db.close()

    def _with_resource_abs_path(self, project_id: str, row: dict[str, Any]) -> dict[str, Any]:
        local_path = row.get("resource_local_path") or ""
        if not local_path:
            return row
        path = Path(local_path)
        if path.is_absolute():
            row["resource_abs_path"] = str(path)
            return row
        try:
            _config, project_dir = self.pm.load_project(project_id)
            row["resource_abs_path"] = str(project_dir / local_path)
        except Exception:
            row["resource_abs_path"] = local_path
        return row

    def selected_extraction_items(self, project_id: str) -> list[dict[str, Any]]:
        """Return user-selected evidence/assets queued for the extraction step."""
        db = self.pm.get_database(project_id)
        try:
            items: list[dict[str, Any]] = []
            table_rows = db.fetch_all(
                """SELECT ta.asset_id as item_id, 'table' as item_type, ta.title, ta.caption,
                          ta.page_or_sheet, ta.confidence, ta.status, ta.resource_id, ta.raw_file_path,
                          r.local_path as resource_local_path,
                          COALESCE(ct.row_count, 0) as row_count, COALESCE(ct.col_count, 0) as col_count,
                          a.article_id, a.title as article_title, a.doi
                   FROM table_assets ta
                   JOIN articles a ON ta.article_id = a.article_id
                   JOIN resources r ON ta.resource_id = r.resource_id
                   LEFT JOIN candidate_tables ct ON ct.asset_id = ta.asset_id
                   WHERE a.project_id = ? AND ta.status = 'selected_for_extraction'
                   ORDER BY ta.created_at""",
                (project_id,),
            )
            for row in table_rows:
                data = self._hydrate_table_asset_preview(db, project_id, dict(row), id_key="item_id")
                data["display_text"] = data.get("title") or data.get("caption") or data.get("item_id")
                items.append(data)

            evidence_rows = db.fetch_all(
                """SELECT e.evidence_id as item_id,
                          CASE
                            WHEN e.evidence_type IN ('figure', 'figure_caption') THEN 'figure'
                            ELSE 'text'
                          END as item_type,
                          e.evidence_type, e.evidence_text, e.page_or_section,
                          e.confidence, e.status, e.resource_id, e.target_header, e.target_field,
                          e.local_path, r.local_path as resource_local_path,
                          a.article_id, a.title as article_title, a.doi
                   FROM article_evidence e
                   JOIN articles a ON e.article_id = a.article_id
                   JOIN resources r ON e.resource_id = r.resource_id
                   WHERE e.project_id = ? AND e.status = 'selected_for_extraction'
                   ORDER BY e.created_at""",
                (project_id,),
            )
            for row in evidence_rows:
                data = self._hydrate_evidence_context(project_id, dict(row))
                text = (data.get("evidence_text") or "").split("\n[PATH]", 1)[0]
                text = re.sub(r"\n?\[USER_SELECTED_PAGE\]\d+", "", text)
                text = re.sub(r"\n?\[BBOX_NORM\][0-9., -]+", "", text)
                data["display_text"] = text.strip()[:260]
                items.append(data)
            return items
        finally:
            db.close()

    def selected_source_summary(self, project_id: str, table_id: str | None = None) -> dict[str, Any]:
        """Return compact counters for the selected extraction source set."""
        items = self.selected_extraction_items(project_id)
        counts = {"table": 0, "figure": 0, "text": 0}
        articles: set[str] = set()
        for item in items:
            item_type = item.get("item_type", "")
            if item_type in counts:
                counts[item_type] += 1
            if item.get("article_id"):
                articles.add(item["article_id"])
        return {
            "total": len(items),
            "tables": counts["table"],
            "figures": counts["figure"],
            "texts": counts["text"],
            "articles": len(articles),
            "table_id": table_id or "",
        }

    def _dir_size(self, path: Path) -> int:
        if not path.exists():
            return 0
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())

    def quality_distribution(self, project_id: str, table_id: str | None = None) -> dict[str, int]:
        db = self.pm.get_database(project_id)
        try:
            where = "WHERE table_id = ?" if table_id else ""
            params = (table_id,) if table_id else ()
            rows = db.fetch_all(
                f"SELECT quality_grade, COUNT(*) as cnt FROM standardized_records {where} GROUP BY quality_grade",
                params,
            )
            dist = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
            for row in rows:
                g = row["quality_grade"]
                if g in dist:
                    dist[g] = row["cnt"]
            return dist
        finally:
            db.close()

    def mapping_rules(self, project_id: str, status: str | None = None,
                      scope: str | None = None) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            where: list[str] = []
            params: list[Any] = []
            if status and status != "all":
                where.append("review_status = ?")
                params.append(status)
            if scope and scope != "all":
                where.append("scope = ?")
                params.append(scope)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            rows = db.fetch_all(
                f"SELECT * FROM mapping_rules {clause} ORDER BY created_at DESC",
                tuple(params),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def learned_rules(self, project_id: str, status: str | None = None,
                      scope: str | None = None) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            where: list[str] = ["project_id = ?"]
            params: list[Any] = [project_id]
            if status and status != "all":
                where.append("review_status = ?")
                params.append(status)
            if scope and scope != "all":
                where.append("scope = ?")
                params.append(scope)
            clause = "WHERE " + " AND ".join(where)
            rows = db.fetch_all(
                f"SELECT * FROM learned_extraction_rules {clause} ORDER BY created_at DESC",
                tuple(params),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def trace_row_detail(self, project_id: str, record_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            record = db.fetch_one("SELECT * FROM standardized_records WHERE record_id = ?", (record_id,))
            if not record:
                return {}
            result = dict(record)
            result["data_parsed"] = json.loads(record["data"]) if record["data"] else {}
            result["original_fields_parsed"] = json.loads(record["original_fields"]) if record.get("original_fields") else {}
            patches = db.fetch_all(
                "SELECT * FROM record_patches WHERE record_id = ? ORDER BY created_at",
                (record_id,),
            )
            result["patches"] = [dict(p) for p in patches]
            calculations = db.fetch_all(
                "SELECT * FROM calculation_records WHERE record_id = ? ORDER BY created_at",
                (record_id,),
            )
            result["calculations"] = [dict(c) for c in calculations]
            reviews = db.fetch_all(
                "SELECT * FROM review_items WHERE table_id = ? ORDER BY created_at",
                (record.get("table_id", ""),),
            )
            result["reviews"] = [dict(r) for r in reviews]
            return result
        finally:
            db.close()

    def trace_llm_calls(self, project_id: str, article_id: str | None = None) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            if article_id:
                rows = db.fetch_all(
                    "SELECT * FROM llm_calls WHERE project_id = ? AND article_id = ? ORDER BY started_at DESC LIMIT 100",
                    (project_id, article_id),
                )
            else:
                rows = db.fetch_all(
                    "SELECT * FROM llm_calls WHERE project_id = ? ORDER BY started_at DESC LIMIT 100",
                    (project_id,),
                )
            return [dict(r) for r in rows]
        finally:
            db.close()
