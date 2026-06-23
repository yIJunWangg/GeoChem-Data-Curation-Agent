"""Lightweight workflow orchestration for CLI and UI callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Callable

import requests

from .core.config import load_config
from .core.exceptions import DuplicateFileError
from .core.memory import MemoryStore
from .core.models import ResourceType, ReviewStatus, RiskLevel
from .core.project import ProjectManager
from .core.schema_manager import SchemaManager
from .curation import (
    AuditPackageBuilder,
    CostReporter,
    MappingEngine,
    ReviewManager,
    RuleApplicationEngine,
    StandardizationPipeline,
    TeachingManager,
    TraceService,
)
from .extractors import CsvReader, ExcelReader, PdfReader, ResultPersistence
from .ingestion.browser_service import BrowserService
from .ingestion.doi_service import ArticleMetadata, DOIService
from .ingestion.file_importer import FileImporter
from .providers.llm_client import LLMClient


@dataclass
class WorkflowEvent:
    """A UI-friendly workflow event."""

    event_type: str
    message: str
    progress: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class EventBus:
    """Small in-process pub/sub used by the desktop UI."""

    def __init__(self):
        self._subscribers: list[Callable[[WorkflowEvent], None]] = []

    def subscribe(self, callback: Callable[[WorkflowEvent], None]) -> None:
        self._subscribers.append(callback)

    def emit(self, event_type: str, message: str, progress: float = 0.0, **details) -> WorkflowEvent:
        event = WorkflowEvent(event_type=event_type, message=message, progress=progress, details=details)
        for callback in list(self._subscribers):
            callback(event)
        return event


class WorkflowRunner:
    """Deterministic workflow wrapper around existing curation services."""

    def __init__(self, project_manager: ProjectManager | None = None, event_bus: EventBus | None = None):
        self.pm = project_manager or ProjectManager()
        self.events = event_bus or EventBus()

    def discover_resources(self, project_id: str, resource_id: str) -> dict[str, Any]:
        """Run full resource discovery on a PDF/HTML resource.

        Extracts tables, figures, links, and key sections from the resource
        and saves them as table_assets and evidence entries.
        """
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            resource = db.fetch_one(
                "SELECT r.*, a.article_id FROM resources r JOIN articles a ON r.article_id = a.article_id WHERE r.resource_id = ?",
                (resource_id,),
            )
            if not resource:
                raise ValueError(f"Resource not found: {resource_id}")

            file_path = project_dir / resource["local_path"] if resource["local_path"] else None
            if not file_path or not file_path.exists():
                raise FileNotFoundError(f"File not found: {resource['local_path']}")

            self.events.emit("discover", f"发现资源: {resource['file_name']}", 0.1, resource_id=resource_id)

            # Clear old evidence for this resource
            db.execute("DELETE FROM article_evidence WHERE resource_id = ?", (resource_id,))
            db.execute("DELETE FROM table_assets WHERE resource_id = ?", (resource_id,))
            db.commit()

            article_id = resource["article_id"]
            count = 0

            if resource["resource_type"] in ("main_pdf", "supplementary_pdf"):
                self.events.emit("discover", "PDF 资源发现中...", 0.3)
                count = self._discover_evidence_from_pdf(db, project_id, article_id, resource_id, file_path)
            elif resource["resource_type"] == "html_page":
                self.events.emit("discover", "HTML 资源发现中...", 0.3)
                content = file_path.read_text(encoding="utf-8", errors="replace") if file_path.exists() else ""
                count = self._discover_evidence_from_text(db, project_id, article_id, resource_id, content)

            # Update resource status
            db.execute("UPDATE resources SET status = 'discovered' WHERE resource_id = ?", (resource_id,))
            db.commit()

            self.events.emit("discover", f"发现 {count} 个资源元素", 1.0, count=count)
            return {"resource_id": resource_id, "discovered": count}
        finally:
            db.close()

    def extract_resource(self, project_id: str, resource_id: str) -> dict[str, Any]:
        """Read a resource and save full candidate tables without LLM row extraction."""
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            resource = db.fetch_one(
                "SELECT r.*, a.article_id FROM resources r JOIN articles a ON r.article_id = a.article_id WHERE r.resource_id = ?",
                (resource_id,),
            )
            if not resource:
                raise ValueError(f"Resource not found: {resource_id}")
            file_path = project_dir / resource["local_path"] if resource["local_path"] else Path(resource["file_name"])
            if not file_path.exists():
                file_path = Path(resource["local_path"] or "")
            if not file_path.exists():
                raise FileNotFoundError(resource["local_path"] or resource["file_name"])
            reader_cls = {
                "supplementary_excel": ExcelReader,
                "supplementary_csv": CsvReader,
                "main_pdf": PdfReader,
                "supplementary_pdf": PdfReader,
                "html_page": CsvReader,
            }.get(resource["resource_type"], ExcelReader)
            self.events.emit("extract", f"Reading {resource['file_name']}", 0.2, resource_id=resource_id)
            contents = reader_cls().read(file_path)
            persistence = ResultPersistence()
            table_ids = []
            for content in contents:
                display_headers = content.raw_headers or content.headers
                rows = []
                for raw_row in content.raw_rows:
                    row = {}
                    for i, header in enumerate(display_headers):
                        if header is not None and str(header).strip():
                            row[str(header)] = raw_row[i] if i < len(raw_row) else None
                    rows.append(row)
                if not rows:
                    continue
                table_id = persistence.save(
                    db,
                    resource["article_id"],
                    resource_id,
                    content.text,
                    rows,
                    display_headers,
                    {},
                    header_units=content.header_units,
                    source_type=content.source_type,
                    sheet_name=content.sheet_name,
                    page_number=content.page_number,
                    confidence=1.0,
                    extract_method="reader",
                )
                table_ids.append(table_id)
            self.events.emit("extract", f"Extracted {len(table_ids)} table(s)", 1.0, tables=table_ids)
            return {"table_ids": table_ids}
        finally:
            db.close()

    def select_extraction_items(self, project_id: str, selected: list[tuple[str, str]]) -> dict[str, Any]:
        """Persist the resource-list selection that should feed data extraction."""
        db = self.pm.get_database(project_id)
        try:
            table_ids = [item_id for item_type, item_id in selected if item_type == "table" and item_id]
            figure_ids = [item_id for item_type, item_id in selected if item_type == "figure" and item_id]
            text_ids = [item_id for item_type, item_id in selected if item_type == "text" and item_id]

            db.execute(
                """UPDATE article_evidence
                   SET status = 'candidate'
                   WHERE project_id = ? AND status = 'selected_for_extraction'""",
                (project_id,),
            )
            db.execute(
                """UPDATE table_assets
                   SET status = 'pending'
                   WHERE status = 'selected_for_extraction'
                     AND article_id IN (SELECT article_id FROM articles WHERE project_id = ?)""",
                (project_id,),
            )

            if table_ids:
                placeholders = ",".join("?" for _ in table_ids)
                db.execute(
                    f"""UPDATE table_assets
                        SET status = 'selected_for_extraction'
                        WHERE asset_id IN ({placeholders})
                          AND article_id IN (SELECT article_id FROM articles WHERE project_id = ?)""",
                    tuple(table_ids + [project_id]),
                )
            evidence_ids = figure_ids + text_ids
            if evidence_ids:
                placeholders = ",".join("?" for _ in evidence_ids)
                db.execute(
                    f"""UPDATE article_evidence
                        SET status = 'selected_for_extraction'
                        WHERE evidence_id IN ({placeholders}) AND project_id = ?""",
                    tuple(evidence_ids + [project_id]),
                )
            db.commit()
            result = {
                "tables": len(table_ids),
                "figures": len(figure_ids),
                "texts": len(text_ids),
                "total": len(table_ids) + len(figure_ids) + len(text_ids),
            }
            self.events.emit(
                "extract",
                f"Queued {result['total']} selected source item(s) for extraction",
                1.0,
                **result,
            )
            return result
        finally:
            db.close()

    def add_manual_pdf_evidence(
        self,
        project_id: str,
        article_id: str,
        resource_id: str,
        page_number: int,
        evidence_type: str = "paragraph",
        note: str = "",
        bbox_norm: str = "",
        select_for_extraction: bool = True,
    ) -> dict[str, Any]:
        """Create a user-selected PDF page evidence item for the extraction queue."""
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            resource = db.fetch_one(
                """SELECT r.*, a.article_id
                   FROM resources r
                   JOIN articles a ON r.article_id = a.article_id
                   WHERE r.resource_id = ? AND a.project_id = ? AND a.article_id = ?""",
                (resource_id, project_id, article_id),
            )
            if not resource:
                raise ValueError(f"Resource not found for article: {resource_id}")
            if resource["resource_type"] not in ("main_pdf", "supplementary_pdf"):
                raise ValueError("Manual PDF evidence can only be created from PDF resources")

            page_number = max(1, int(page_number or 1))
            text = note.strip() or f"User selected page {page_number} for AI extraction"
            text = f"{text}\n[USER_SELECTED_PAGE]{page_number}"
            crop_path = ""
            if bbox_norm:
                text = f"{text}\n[BBOX_NORM]{bbox_norm}"
                crop_path = self._render_manual_pdf_crop(
                    project_dir,
                    resource["local_path"] or "",
                    page_number,
                    bbox_norm,
                    resource_id,
                )
            evidence_id = self._insert_evidence(
                db,
                project_id,
                article_id,
                resource_id,
                evidence_type,
                text[:1200],
                f"page {page_number}",
                extra_path=crop_path,
                local_path=crop_path,
                status="selected_for_extraction" if select_for_extraction else "candidate",
                confidence=0.85,
            )
            self.events.emit(
                "extract",
                f"用户选择 PDF 第 {page_number} 页作为抽取线索 {evidence_id}",
                1.0,
                evidence_id=evidence_id,
                page_number=page_number,
            )
            return {
                "evidence_id": evidence_id,
                "article_id": article_id,
                "resource_id": resource_id,
                "page_number": page_number,
                "bbox_norm": bbox_norm,
                "crop_path": crop_path,
                "status": "selected_for_extraction" if select_for_extraction else "candidate",
            }
        finally:
            db.close()

    def extract_evidence_candidates(
        self,
        project_id: str,
        evidence_id: str,
        use_llm: bool = True,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Extract candidate field/value pairs from a selected evidence item."""
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            evidence = db.fetch_one(
                """SELECT e.*, r.local_path as resource_local_path, r.file_name as resource_file_name
                   FROM article_evidence e
                   LEFT JOIN resources r ON e.resource_id = r.resource_id
                   WHERE e.project_id = ? AND e.evidence_id = ?""",
                (project_id, evidence_id),
            )
            if not evidence:
                raise ValueError(f"Evidence not found: {evidence_id}")
            article_id = evidence["article_id"]
            targets = self._target_headers_for_article(db, project_id, article_id)
            context_text = self._evidence_context_text(project_dir, dict(evidence))
            suggestions: list[dict[str, Any]] = []
            if use_llm:
                try:
                    suggestions = self._llm_extract_candidates(
                        config,
                        db,
                        project_id,
                        article_id,
                        dict(evidence),
                        context_text,
                        targets,
                        provider,
                        model,
                    )
                except Exception as exc:
                    self.events.emit("extract", f"LLM 候选抽取失败，使用本地规则兜底: {exc}", 0.75, evidence_id=evidence_id)
            if not suggestions:
                suggestions = self._rule_extract_candidates(context_text, targets)
            db.execute("DELETE FROM extraction_candidates WHERE evidence_id = ?", (evidence_id,))
            saved = [self._save_extraction_candidate(db, project_id, article_id, evidence_id, evidence["evidence_type"], item) for item in suggestions]
            db.commit()
            self.events.emit("extract", f"生成 {len(saved)} 个候选字段值", 1.0, evidence_id=evidence_id, candidates=len(saved))
            return {"evidence_id": evidence_id, "candidates": len(saved), "candidate_ids": saved}
        finally:
            db.close()

    def _target_headers_for_article(self, db, project_id: str, article_id: str) -> list[dict[str, str]]:
        row = db.fetch_one(
            """SELECT hc.headers_json
               FROM article_header_assignments aha
               JOIN header_configs hc ON aha.config_id = hc.config_id
               WHERE aha.project_id = ? AND aha.article_id = ? AND aha.status = 'confirmed'
               ORDER BY aha.created_at DESC LIMIT 1""",
            (project_id, article_id),
        )
        if not row:
            row = db.fetch_one(
                """SELECT headers_json FROM header_configs
                   WHERE project_id = ? AND status != 'deleted'
                   ORDER BY updated_at DESC LIMIT 1""",
                (project_id,),
            )
        if not row:
            return []
        try:
            rows = json.loads(row["headers_json"] or "[]")
        except Exception:
            return []
        targets = []
        for item in rows:
            display = item.get("字段名") or item.get("display_header") or item.get("header") or ""
            canonical = item.get("canonical_field") or display
            unit = item.get("默认单位") or item.get("target_unit") or ""
            desc = item.get("description") or item.get("字段说明") or ""
            if display or canonical:
                targets.append({
                    "target_header": display,
                    "target_field": canonical,
                    "target_unit": "" if unit == "—" else unit,
                    "description": desc,
                })
        return targets

    def _evidence_context_text(self, project_dir: Path, evidence: dict[str, Any]) -> str:
        text = evidence.get("evidence_text") or ""
        page_num = self._tag_number(text, "USER_SELECTED_PAGE") or self._page_number(evidence.get("page_or_section") or "")
        bbox = self._tag_text(text, "BBOX_NORM")
        resource_local = evidence.get("resource_local_path") or ""
        pdf_path = Path(resource_local)
        if resource_local and not pdf_path.is_absolute():
            pdf_path = project_dir / resource_local
        if pdf_path.exists() and pdf_path.suffix.lower() == ".pdf" and page_num:
            pdf_text = self._pdf_text_for_region(pdf_path, page_num, bbox)
            if pdf_text:
                return pdf_text
        return self._clean_internal_tags(text)

    def _pdf_text_for_region(self, pdf_path: Path, page_number: int, bbox_norm: str = "") -> str:
        try:
            import fitz

            with fitz.open(str(pdf_path)) as doc:
                page_index = page_number - 1
                if page_index < 0 or page_index >= len(doc):
                    return ""
                page = doc[page_index]
                clip = None
                if bbox_norm:
                    parts = [float(p.strip()) for p in bbox_norm.split(",")]
                    if len(parts) == 4:
                        x0, y0, x1, y1 = [max(0.0, min(1.0, p)) for p in parts]
                        rect = page.rect
                        clip = fitz.Rect(
                            rect.x0 + rect.width * x0,
                            rect.y0 + rect.height * y0,
                            rect.x0 + rect.width * x1,
                            rect.y0 + rect.height * y1,
                        )
                return " ".join((page.get_text("text", clip=clip) or "").split())
        except Exception:
            return ""

    def _llm_extract_candidates(
        self,
        config,
        db,
        project_id: str,
        article_id: str,
        evidence: dict[str, Any],
        context_text: str,
        targets: list[dict[str, str]],
        provider: str | None,
        model: str | None,
    ) -> list[dict[str, Any]]:
        if not context_text.strip() or not targets:
            return []
        compact_targets = targets[:180]
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract geochemical data candidate values from article evidence. "
                    "Use only the provided evidence text and target headers. "
                    "Return strict JSON list. Do not invent values."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "evidence_id": evidence.get("evidence_id"),
                    "page_or_section": evidence.get("page_or_section"),
                    "target_headers": compact_targets,
                    "evidence_text": context_text[:8000],
                    "return_schema": [
                        "target_header", "target_field", "target_unit", "value",
                        "source_unit", "confidence", "risk_level", "reason",
                    ],
                }, ensure_ascii=False),
            },
        ]
        response = LLMClient(config, db=db).chat(
            messages,
            task_name="evidence_candidate_extraction",
            provider_override=provider,
            model_override=model,
            temperature_override=0.0,
            max_tokens_override=3000,
            project_id=project_id,
            article_id=article_id,
            agent_name="agent_workbench",
            skill_name="evidence_candidate_extraction",
            use_cache=True,
        )
        try:
            parsed = json.loads(response.content)
        except Exception:
            match = re.search(r"\[[\s\S]*\]", response.content or "")
            parsed = json.loads(match.group(0)) if match else []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict) and item.get("value") not in (None, "")]

    def _rule_extract_candidates(self, context_text: str, targets: list[dict[str, str]]) -> list[dict[str, Any]]:
        if not context_text.strip():
            return []
        target_by_key: dict[str, dict[str, str]] = {}
        for target in targets:
            for key in (target.get("target_field", ""), target.get("target_header", "")):
                norm = re.sub(r"[^a-z0-9δ]+", "", key.lower())
                if norm:
                    target_by_key[norm] = target
        pattern = re.compile(
            r"(?P<name>δ?\d{0,2}[A-Z][A-Za-z]?\d?O?\d?|[A-Z][a-z]?(?:/[A-Z][a-z]?)+|TOC|TN|TS|CIA|REE_total|LREE|HREE)"
            r"\s*(?:=|:)?\s*(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>wt%|ppm|ppb|‰|%|mg/kg|ug/g)?",
            re.IGNORECASE,
        )
        candidates = []
        seen = set()
        for match in pattern.finditer(context_text):
            raw_name = match.group("name")
            value = match.group("value")
            norm = re.sub(r"[^a-z0-9δ]+", "", raw_name.lower())
            target = target_by_key.get(norm)
            if not target:
                target = next((t for k, t in target_by_key.items() if norm == k or norm in k or k in norm), None)
            if not target:
                continue
            key = (target.get("target_field"), value)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "target_header": target.get("target_header", ""),
                "target_field": target.get("target_field", ""),
                "target_unit": target.get("target_unit", ""),
                "value": value,
                "source_unit": match.group("unit") or target.get("target_unit", ""),
                "confidence": 0.62,
                "risk_level": "medium",
                "reason": f"本地规则从证据文本识别 {raw_name}={value}",
            })
        return candidates[:80]

    def _save_extraction_candidate(self, db, project_id: str, article_id: str, evidence_id: str, source_type: str, item: dict[str, Any]) -> str:
        row = db.fetch_one(
            "SELECT MAX(CAST(SUBSTR(candidate_id, 6) AS INTEGER)) as max_id FROM extraction_candidates WHERE candidate_id LIKE 'XCAN_%'"
        )
        candidate_id = f"XCAN_{(row['max_id'] or 0) + 1:06d}"
        confidence = float(item.get("confidence") or 0.5)
        risk = item.get("risk_level") or ("low" if confidence >= 0.85 else "medium")
        db.execute(
            """INSERT INTO extraction_candidates
               (candidate_id, project_id, article_id, evidence_id, source_type, target_header,
                target_field, target_unit, value, source_unit, confidence, risk_level, status, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate_id,
                project_id,
                article_id,
                evidence_id,
                source_type or "",
                item.get("target_header") or "",
                item.get("target_field") or "",
                item.get("target_unit") or "",
                str(item.get("value") or ""),
                item.get("source_unit") or "",
                confidence,
                risk,
                item.get("status") or "pending",
                item.get("reason") or "",
                datetime.now().isoformat(),
            ),
        )
        return candidate_id

    def confirm_extraction_candidate(
        self,
        project_id: str,
        candidate_id: str,
        target_header: str | None = None,
        target_field: str | None = None,
        target_unit: str | None = None,
        value: str | None = None,
        source_unit: str | None = None,
        sample_id: str | None = None,
        save_as_rule: bool = False,
    ) -> dict[str, Any]:
        """Confirm an AI-extracted evidence candidate and turn it into a patch.

        Evidence-level candidates do not always belong to a parsed candidate
        table, while the downstream standardization/export path is table-based.
        We therefore keep the original evidence immutable and attach confirmed
        values to a lightweight per-article "Agent extraction" table.
        """
        db = self.pm.get_database(project_id)
        try:
            candidate = db.fetch_one(
                """SELECT c.*, e.resource_id, e.page_or_section, e.evidence_text
                   FROM extraction_candidates c
                   JOIN article_evidence e ON c.evidence_id = e.evidence_id
                   WHERE c.project_id = ? AND c.candidate_id = ?""",
                (project_id, candidate_id),
            )
            if not candidate:
                raise ValueError(f"Extraction candidate not found: {candidate_id}")
            candidate_data = dict(candidate)
            article_id = candidate_data["article_id"]
            resolved_header = target_header if target_header is not None else candidate_data.get("target_header", "")
            resolved_field = target_field if target_field is not None else candidate_data.get("target_field", "")
            resolved_unit = target_unit if target_unit is not None else candidate_data.get("target_unit", "")
            resolved_value = value if value is not None else candidate_data.get("value", "")
            resolved_source_unit = source_unit if source_unit is not None else candidate_data.get("source_unit", "")
            if not resolved_field and resolved_header:
                resolved_field = resolved_header
            if resolved_value in (None, ""):
                raise ValueError("Cannot confirm an empty extraction candidate value")
            if not resolved_field:
                raise ValueError("Cannot confirm candidate without a target field/header")

            resource_id = candidate_data.get("resource_id") or self._ensure_agent_evidence_resource(db, article_id)
            table_id = self._ensure_agent_extraction_table(db, article_id, resource_id)
            rule_id = self._save_candidate_rule(db, project_id, article_id, candidate_data, {
                "target_header": resolved_header,
                "target_field": resolved_field,
                "target_unit": resolved_unit,
                "value": resolved_value,
                "source_unit": resolved_source_unit,
            }) if save_as_rule else None
            patch_id = TeachingManager().add_patch(
                db=db,
                project_id=project_id,
                article_id=article_id,
                table_id=table_id,
                sample_id=sample_id,
                target_field=str(resolved_field),
                target_header=str(resolved_header or resolved_field),
                target_unit=str(resolved_unit or ""),
                value=str(resolved_value),
                source_unit=str(resolved_source_unit or ""),
                source_type=f"extraction_candidate:{candidate_data.get('source_type') or 'evidence'}",
                evidence_id=None,
                learned_rule_id=rule_id,
                confidence=float(candidate_data.get("confidence") or 0.0),
                risk_level=candidate_data.get("risk_level") or RiskLevel.MEDIUM.value,
                review_status=ReviewStatus.CONFIRMED.value,
                reason=(
                    f"User confirmed extraction candidate {candidate_id} "
                    f"from article evidence {candidate_data.get('evidence_id')}"
                ),
            )
            db.execute(
                """UPDATE extraction_candidates
                   SET target_header = ?, target_field = ?, target_unit = ?, value = ?,
                       source_unit = ?, status = 'confirmed'
                   WHERE candidate_id = ?""",
                (
                    resolved_header,
                    resolved_field,
                    resolved_unit,
                    str(resolved_value),
                    resolved_source_unit,
                    candidate_id,
                ),
            )
            db.commit()
            self.events.emit(
                "extract",
                f"Confirmed extraction candidate {candidate_id}",
                1.0,
                candidate_id=candidate_id,
                patch_id=patch_id,
                table_id=table_id,
                learned_rule_id=rule_id,
            )
            return {
                "candidate_id": candidate_id,
                "status": "confirmed",
                "patch_id": patch_id,
                "table_id": table_id,
                "learned_rule_id": rule_id,
            }
        finally:
            db.close()

    def reject_extraction_candidate(self, project_id: str, candidate_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            db.execute(
                "UPDATE extraction_candidates SET status = 'rejected' WHERE project_id = ? AND candidate_id = ?",
                (project_id, candidate_id),
            )
            db.commit()
            self.events.emit("extract", f"Rejected extraction candidate {candidate_id}", 1.0, candidate_id=candidate_id)
            return {"candidate_id": candidate_id, "status": "rejected"}
        finally:
            db.close()

    def _ensure_agent_extraction_table(self, db, article_id: str, resource_id: str) -> str:
        existing = db.fetch_one(
            """SELECT table_id FROM candidate_tables
               WHERE article_id = ? AND resource_id = ? AND extract_method = 'agent_evidence'
               ORDER BY created_at LIMIT 1""",
            (article_id, resource_id),
        )
        if existing:
            return existing["table_id"]
        row = db.fetch_one(
            "SELECT MAX(CAST(SUBSTR(table_id, 5) AS INTEGER)) as max_id FROM candidate_tables WHERE table_id LIKE 'TBL_%'"
        )
        table_id = f"TBL_{(row['max_id'] or 0) + 1:03d}"
        db.execute(
            """INSERT INTO candidate_tables
               (table_id, article_id, resource_id, source_type, sheet_name, page_number,
                title, caption, row_count, col_count, extract_method, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                table_id,
                article_id,
                resource_id,
                "agent_evidence",
                "Agent Workbench",
                None,
                "Agent-confirmed evidence values",
                "Values confirmed from selected article resources in the Agent Workbench.",
                0,
                0,
                "agent_evidence",
                1.0,
                datetime.now().isoformat(),
            ),
        )
        return table_id

    def _ensure_agent_evidence_resource(self, db, article_id: str) -> str:
        existing = db.fetch_one(
            """SELECT resource_id FROM resources
               WHERE article_id = ? AND resource_type = ? AND file_name = ?
               ORDER BY created_at LIMIT 1""",
            (article_id, ResourceType.OTHER.value, "Agent Workbench Evidence"),
        )
        if existing:
            return existing["resource_id"]
        row = db.fetch_one(
            "SELECT MAX(CAST(SUBSTR(resource_id, 5) AS INTEGER)) as max_id FROM resources WHERE resource_id LIKE 'RES_%'"
        )
        resource_id = f"RES_{(row['max_id'] or 0) + 1:03d}"
        db.execute(
            """INSERT INTO resources
               (resource_id, article_id, resource_type, file_name, local_path, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                resource_id,
                article_id,
                ResourceType.OTHER.value,
                "Agent Workbench Evidence",
                "",
                "generated",
                datetime.now().isoformat(),
            ),
        )
        return resource_id

    def _save_candidate_rule(
        self,
        db,
        project_id: str,
        article_id: str,
        candidate: dict[str, Any],
        confirmed: dict[str, Any],
    ) -> str:
        pattern = (candidate.get("reason") or candidate.get("evidence_text") or "")[:600]
        if not pattern:
            pattern = f"{confirmed['target_header'] or confirmed['target_field']} -> {confirmed['value']}"
        existing = db.fetch_one(
            """SELECT rule_id FROM learned_extraction_rules
               WHERE project_id = ? AND article_id = ? AND target_field = ?
                 AND rule_type = 'user_confirmed_candidate'
                 AND source_type = ? AND pattern = ?""",
            (
                project_id,
                article_id,
                confirmed["target_field"],
                candidate.get("source_type") or "",
                pattern,
            ),
        )
        if existing:
            return existing["rule_id"]
        row = db.fetch_one(
            "SELECT MAX(CAST(SUBSTR(rule_id, 7) AS INTEGER)) as max_id FROM learned_extraction_rules WHERE rule_id LIKE 'LRULE_%'"
        )
        rule_id = f"LRULE_{(row['max_id'] or 0) + 1:03d}"
        db.execute(
            """INSERT INTO learned_extraction_rules
               (rule_id, project_id, article_id, target_field, target_header, target_unit,
                rule_type, source_type, pattern, evidence, conditions, confidence, risk_level,
                review_status, scope, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rule_id,
                project_id,
                article_id,
                confirmed["target_field"],
                confirmed["target_header"] or confirmed["target_field"],
                confirmed["target_unit"],
                "user_confirmed_candidate",
                candidate.get("source_type") or "",
                pattern,
                candidate.get("evidence_text") or "",
                json.dumps({
                    "candidate_id": candidate.get("candidate_id"),
                    "evidence_id": candidate.get("evidence_id"),
                    "source_unit": confirmed.get("source_unit") or "",
                }, ensure_ascii=False),
                float(candidate.get("confidence") or 1.0),
                candidate.get("risk_level") or RiskLevel.LOW.value,
                ReviewStatus.CONFIRMED.value,
                "project",
                datetime.now().isoformat(),
                "user",
            ),
        )
        return rule_id

    def _tag_text(self, text: str, tag: str) -> str:
        match = re.search(rf"\[{re.escape(tag)}\]([^\n]+)", text or "")
        return match.group(1).strip() if match else ""

    def _tag_number(self, text: str, tag: str) -> int | None:
        value = self._tag_text(text, tag)
        try:
            return int(value)
        except Exception:
            return None

    def _page_number(self, value: str) -> int | None:
        match = re.search(r"page\s+(\d+)", value or "", flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _clean_internal_tags(self, text: str) -> str:
        text = (text or "").split("\n[PATH]", 1)[0]
        text = re.sub(r"\n?\[USER_SELECTED_PAGE\]\d+", "", text)
        text = re.sub(r"\n?\[BBOX_NORM\][0-9., -]+", "", text)
        return text.strip()

    def _render_manual_pdf_crop(
        self,
        project_dir: Path,
        resource_local_path: str,
        page_number: int,
        bbox_norm: str,
        resource_id: str,
    ) -> str:
        """Render a user-selected normalized PDF region into an image file."""
        if not resource_local_path or not bbox_norm:
            return ""
        try:
            import fitz

            pdf_path = Path(resource_local_path)
            if not pdf_path.is_absolute():
                pdf_path = project_dir / resource_local_path
            if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
                return ""
            parts = [float(p.strip()) for p in bbox_norm.split(",")]
            if len(parts) != 4:
                return ""
            x0, y0, x1, y1 = [max(0.0, min(1.0, p)) for p in parts]
            if x1 <= x0 or y1 <= y0:
                return ""
            out_dir = pdf_path.parent / "assets" / "manual_crops"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{resource_id}_p{page_number}_{int(x0*10000)}_{int(y0*10000)}_{int(x1*10000)}_{int(y1*10000)}.png"
            with fitz.open(str(pdf_path)) as doc:
                page_index = page_number - 1
                if page_index < 0 or page_index >= len(doc):
                    return ""
                page = doc[page_index]
                rect = page.rect
                clip = fitz.Rect(
                    rect.x0 + rect.width * x0,
                    rect.y0 + rect.height * y0,
                    rect.x0 + rect.width * x1,
                    rect.y0 + rect.height * y1,
                )
                pix = page.get_pixmap(matrix=fitz.Matrix(2.4, 2.4), clip=clip, alpha=False)
                pix.save(str(out_path))
            return str(out_path)
        except Exception:
            return ""

    def import_files(
        self,
        project_id: str,
        file_paths: list[str | Path],
        article_id: str | None = None,
        resource_type: str | None = None,
    ) -> dict[str, Any]:
        """Import local PDF/Excel/CSV/ZIP/DOCX files into the project.

        If a file was already imported (duplicate hash), return the existing
        resource info instead of raising an error.
        """
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            importer = FileImporter()
            override = ResourceType(resource_type) if resource_type else None
            results = []
            for path in file_paths:
                file_name = Path(path).name
                self.events.emit("import", f"Importing {file_name}", 0.25, file=str(path))
                try:
                    result = importer.import_file(
                        db,
                        project_dir,
                        path,
                        article_id=article_id,
                        resource_type_override=override,
                    )
                    results.append({
                        "article_id": result.article_id,
                        "resource_id": result.resource_id,
                        "resource_type": result.resource_type.value if result.resource_type else "",
                        "file_name": result.file_name,
                        "status": result.status,
                    })
                    # Auto-discover resources for PDFs
                    if result.resource_type and result.resource_type.value in ("main_pdf", "supplementary_pdf"):
                        self.events.emit("import", f"Auto-discovering resources for {file_name}...", 0.7)
                        try:
                            self.discover_resources(project_id, result.resource_id)
                        except Exception as e:
                            self.events.emit("import", f"Discovery warning: {e}", 0.9)
                except DuplicateFileError:
                    # File already imported — find and return existing resource
                    self.events.emit("import", f"文件已导入: {file_name}，查找已有资源...", 0.5)
                    file_hash = importer._compute_hash(Path(path).resolve())
                    existing = importer._check_duplicate(db, file_hash)
                    if existing:
                        results.append({
                            "article_id": existing.get("article_id", ""),
                            "resource_id": existing["resource_id"],
                            "resource_type": existing.get("resource_type", ""),
                            "file_name": existing.get("file_name", file_name),
                            "status": "duplicate",
                        })
                        self.events.emit("import", f"已找到已有资源 {existing['resource_id']}", 0.8)
            self.events.emit("import", f"Imported {len(results)} file(s)", 1.0, count=len(results))
            return {"resources": results, "count": len(results)}
        finally:
            db.close()

    def open_source_in_browser(self, project_id: str, source: str) -> dict[str, Any]:
        """Open a DOI or URL in the system browser and register an article shell."""
        source = (source or "").strip()
        if not source:
            raise ValueError("DOI or URL is required")
        _config, _project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            self.events.emit("import", "Resolving DOI/URL metadata", 0.08, source=source)
            doi_service = DOIService()
            browser = BrowserService(doi_service=doi_service)
            if source.startswith(("http://", "https://")) and "doi.org/" not in source:
                url = source
                metadata = ArticleMetadata(doi="", title=source, url=url)
            else:
                try:
                    metadata = doi_service.resolve(source)
                    self.events.emit("import", "CrossRef metadata resolved", 0.25, doi=metadata.doi, title=metadata.title)
                except Exception:
                    doi = doi_service.normalize_doi(source)
                    metadata = ArticleMetadata(doi=doi, title=doi, url=doi_service.doi_to_url(doi))
                    self.events.emit("import", "CrossRef lookup unavailable; using DOI URL directly", 0.25, doi=doi)
                url = metadata.url or doi_service.doi_to_url(metadata.doi)
            self.events.emit("import", "Creating article record", 0.4, url=url)
            article_id = FileImporter().create_article_from_metadata(db, project_id, metadata)
            self.events.emit("import", "Opening publisher page in browser", 0.55, article_id=article_id, url=url)
            browser.open_url(url)
            self.events.emit("import", "Browser opened for article access confirmation", 0.6, article_id=article_id, url=url)
            return {
                "article_id": article_id,
                "url": url,
                "doi": metadata.doi,
                "title": metadata.title,
                "authors": ", ".join(metadata.authors),
                "year": metadata.year,
                "journal": metadata.journal,
                "status": "等待浏览器确认",
            }
        finally:
            db.close()

    def confirm_browser_access(self, project_id: str, article_id: str, url: str) -> dict[str, Any]:
        """Save a user-confirmed article URL, using fetched text when available.

        Browser cookies from the user's system browser are not available to the
        requests-based reader. For open-access pages this may still fetch enough
        text; for publisher pages that block scripted reads, we keep a confirmed
        evidence resource so a later LLM/browser-reading task can continue.
        """
        if not article_id or not url:
            raise ValueError("Missing article_id or URL for browser confirmation")
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            self.events.emit("import", "User confirmed browser access; starting journal-page read", 0.1, article_id=article_id, url=url)
            self.events.emit("import", "Trying to discover the real article PDF/body asset", 0.28, article_id=article_id, url=url)
            browser = BrowserService()
            payload = browser.read_payload_after_auth(url)
            pdf_url = payload.get("pdf_url", "")
            content = payload.get("text", "")
            fetched = bool(content and len(content.strip()) >= 200)
            if pdf_url:
                self.events.emit("import", "Article PDF candidate found; downloading PDF asset", 0.52, pdf_url=pdf_url)
                pdf_resource = self._download_and_import_pdf(db, project_dir, article_id, pdf_url)
                if pdf_resource:
                    self._discover_evidence_from_pdf(db, project_id, article_id, pdf_resource["resource_id"], project_dir / pdf_resource["local_path"])
                    self.events.emit(
                        "import",
                        f"Saved article PDF as {pdf_resource['resource_id']}; next step is PDF table/figure extraction",
                        1.0,
                        resource_id=pdf_resource["resource_id"],
                    )
                    return {
                        "article_id": article_id,
                        "resource_id": pdf_resource["resource_id"],
                        "content_length": pdf_resource.get("file_size", 0),
                        "fetched_content": True,
                        "status": "pending",
                        "resource_type": ResourceType.MAIN_PDF.value,
                        "pdf_url": pdf_url,
                    }
                self.events.emit("import", "PDF candidate could not be downloaded; falling back to confirmed URL handoff", 0.62, pdf_url=pdf_url)
            if not fetched:
                self.events.emit(
                    "import",
                    "Direct reader did not capture reliable article text; preparing LLM/browser-reader handoff",
                    0.55,
                    article_id=article_id,
                    url=url,
                )
                content = (
                    "<html><body>\n"
                    "<h1>GeoChem confirmed article access</h1>\n"
                    f"<p>Article URL: {url}</p>\n"
                    "<p>User confirmed that the article page is accessible in the browser. "
                    "Scripted fetching did not return readable content, so downstream LLM/browser "
                    "reading should use this URL and the user's confirmed access state.</p>\n"
                    "</body></html>\n"
                )
            else:
                self.events.emit("import", "Article body text captured for AI reading", 0.6, article_id=article_id, chars=len(content))
            self.events.emit("import", "Saving confirmed page evidence resource", 0.78, article_id=article_id)
            resource_id = FileImporter().create_web_resource(db, article_id, url, content, project_dir)
            db.execute(
                "UPDATE resources SET status = ? WHERE resource_id = ?",
                ("pending" if fetched else "auth_confirmed", resource_id),
            )
            db.commit()
            if fetched:
                self._discover_evidence_from_text(db, project_id, article_id, resource_id, content)
            if fetched:
                self.events.emit("import", f"Saved readable article page as {resource_id}; AI can proceed to extraction", 1.0, resource_id=resource_id)
            else:
                self.events.emit("import", f"Saved LLM/browser-reader entrypoint as {resource_id}; waiting for extraction task", 1.0, resource_id=resource_id)
            return {
                "article_id": article_id,
                "resource_id": resource_id,
                "content_length": len(content),
                "fetched_content": fetched,
                "status": "pending" if fetched else "auth_confirmed",
            }
        finally:
            db.close()

    def _download_and_import_pdf(self, db, project_dir: Path, article_id: str, pdf_url: str) -> dict[str, Any] | None:
        """Download a discovered PDF URL and import it as the main article resource."""
        try:
            response = requests.get(
                pdf_url,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
                    "Accept": "application/pdf,*/*",
                },
            )
            if response.status_code != 200:
                return None
            content_type = response.headers.get("content-type", "").lower()
            if "pdf" not in content_type and not response.content.startswith(b"%PDF"):
                return None
            target = project_dir / ".cache" / "downloads" / f"{article_id}_article.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
            result = FileImporter().import_file(
                db,
                project_dir,
                target,
                article_id=article_id,
                resource_type_override=ResourceType.MAIN_PDF,
            )
            db.execute("UPDATE resources SET source_url = ? WHERE resource_id = ?", (pdf_url, result.resource_id))
            db.commit()
            return {
                "resource_id": result.resource_id,
                "file_name": result.file_name,
                "local_path": result.local_path,
                "file_size": result.file_size,
            }
        except Exception:
            return None

    def _discover_evidence_from_text(self, db, project_id: str, article_id: str, resource_id: str, text: str) -> int:
        """Create first-pass paragraph/table/figure evidence from readable text."""
        created = 0
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            lowered = line.lower()
            evidence_type = ""
            if lowered.startswith("table ") or " table " in lowered[:80]:
                evidence_type = "table_caption"
            elif lowered.startswith("figure ") or lowered.startswith("fig. ") or " figure " in lowered[:80]:
                evidence_type = "figure_caption"
            elif any(term in lowered for term in ("geochem", "sample", "supplementary", "data", "ppm", "wt%")):
                evidence_type = "paragraph"
            if not evidence_type:
                continue
            self._insert_evidence(db, project_id, article_id, resource_id, evidence_type, line[:1200])
            created += 1
            if created >= 40:
                break
        return created

    def _discover_evidence_from_pdf(self, db, project_id: str, article_id: str, resource_id: str, pdf_path: Path) -> int:
        """Extract tables, figures, links, and key sections from a PDF."""
        try:
            import fitz
        except Exception:
            return 0
        created = 0
        doc = None
        try:
            doc = fitz.open(str(pdf_path))
            page_images: dict[int, str] = {}
            for page_index in range(len(doc)):
                page = doc[page_index]
                page_num = page_index + 1
                text = page.get_text("text") or ""

                # Links
                link_count = 0
                for link in page.get_links():
                    uri = link.get("uri", "")
                    if uri and uri.startswith("http"):
                        self._insert_evidence(db, project_id, article_id, resource_id, "link", uri[:1200], f"page {page_num}")
                        created += 1
                        link_count += 1

                # Images
                for img_idx, img in enumerate(page.get_images(full=True), start=1):
                    xref = img[0]
                    try:
                        pix = fitz.Pixmap(doc, xref)
                        if pix.n > 4:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        thumb_dir = pdf_path.parent / ".cache" / "images"
                        thumb_dir.mkdir(parents=True, exist_ok=True)
                        thumb_path = thumb_dir / f"{pdf_path.stem}_p{page_num}_img{img_idx}.png"
                        pix.save(str(thumb_path))
                        self._insert_evidence(db, project_id, article_id, resource_id, "figure",
                            f"Image {img_idx} (page {page_num})", f"page {page_num}", extra_path=str(thumb_path))
                        created += 1
                        pix = None
                    except Exception:
                        self._insert_evidence(db, project_id, article_id, resource_id, "figure",
                            f"Image {img_idx} (page {page_num})", f"page {page_num}")
                        created += 1

                # Tables via pdfplumber
                try:
                    import pdfplumber
                    with pdfplumber.open(str(pdf_path)) as pdf:
                        if page_index < len(pdf.pages):
                            for tbl_idx, table in enumerate(pdf.pages[page_index].extract_tables(), start=1):
                                if not table or len(table) < 2:
                                    continue
                                headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(table[0])]
                                data_rows = [[str(c).strip() if c else "" for c in row[:len(headers)]] for row in table[1:]]
                                data_rows = [r for r in data_rows if any(r)]
                                if data_rows:
                                    md = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
                                    for row in data_rows[:10]:
                                        md.append("| " + " | ".join(row) + " |")
                                    page_image = page_images.get(page_num)
                                    if page_image is None:
                                        page_image = self._render_pdf_page_image(doc, pdf_path, page_index, page_num)
                                        page_images[page_num] = page_image
                                    self._insert_table_asset(db, project_id, article_id, resource_id,
                                        f"Table {tbl_idx} (Page {page_num})", headers, len(data_rows), f"page {page_num}",
                                        preview="\n".join(md), raw_file_path=page_image)
                                    created += 1
                except Exception:
                    pass

                # Captions and paragraphs
                for line in text.splitlines():
                    cleaned = " ".join(line.split())
                    lowered = cleaned.lower()
                    if not cleaned or len(cleaned) < 10:
                        continue
                    etype = ""
                    if lowered.startswith("table ") or " table " in lowered[:80]:
                        etype = "table_caption"
                        page_image = page_images.get(page_num)
                        if page_image is None:
                            page_image = self._render_pdf_page_image(
                                doc,
                                pdf_path,
                                page_index,
                                page_num,
                                crop=(0.0, 0.0, 1.0, 0.82),
                                suffix="table",
                            )
                            page_images[page_num] = page_image
                        self._insert_table_asset(db, project_id, article_id, resource_id,
                            cleaned[:200], [], 0, f"page {page_num}",
                            preview=f"图片格式表格\n{cleaned[:300]}", raw_file_path=page_image)
                    elif lowered.startswith("figure ") or lowered.startswith("fig. ") or " figure " in lowered[:80]:
                        etype = "figure_caption"
                    elif any(t in lowered for t in ("geochem", "sample", "supplementary", "data", "ppm", "wt%", "element", "concentration")):
                        etype = "paragraph"
                    if etype:
                        self._insert_evidence(db, project_id, article_id, resource_id, etype, cleaned[:1200], f"page {page_num}")
                        created += 1
        except Exception as e:
            import sys
            print(f"DEBUG _discover_evidence_from_pdf error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
        finally:
            if doc:
                doc.close()
        return created

    def _insert_table_asset(
        self, db, project_id: str, article_id: str, resource_id: str,
        title: str, headers: list[str], row_count: int, page_or_sheet: str,
        preview: str = "",
        raw_file_path: str = "",
    ) -> str:
        row = db.fetch_one(
            "SELECT MAX(CAST(SUBSTR(asset_id, 6) AS INTEGER)) as max_id FROM table_assets WHERE asset_id LIKE 'TASS_%'"
        )
        asset_id = f"TASS_{(row['max_id'] or 0) + 1:06d}"
        db.execute(
            """INSERT INTO table_assets
               (asset_id, resource_id, article_id, table_number, title,
                caption, page_or_sheet, raw_file_path, extract_method, confidence, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                asset_id, resource_id, article_id,
                title.split("(")[0].strip(), title, preview[:2000] if preview else "", page_or_sheet,
                raw_file_path,
                "pdfplumber", 0.8, "pending",
                datetime.now().isoformat(),
            ),
        )
        db.commit()
        return asset_id

    def _render_pdf_page_image(
        self,
        doc,
        pdf_path: Path,
        page_index: int,
        page_num: int,
        crop: tuple[float, float, float, float] | None = None,
        suffix: str = "page",
    ) -> str:
        """Save a page preview image used by table/figure source cards."""
        try:
            import fitz

            page = doc[page_index]
            out_dir = pdf_path.parent / ".cache" / "pages"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{pdf_path.stem}_{suffix}_{page_num}.png"
            if not out_path.exists():
                clip = None
                if crop:
                    rect = page.rect
                    clip = fitz.Rect(
                        rect.x0 + rect.width * crop[0],
                        rect.y0 + rect.height * crop[1],
                        rect.x0 + rect.width * crop[2],
                        rect.y0 + rect.height * crop[3],
                    )
                pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False)
                pix.save(str(out_path))
            return str(out_path)
        except Exception:
            return ""

    def _insert_evidence(
        self,
        db,
        project_id: str,
        article_id: str,
        resource_id: str,
        evidence_type: str,
        evidence_text: str,
        page_or_section: str = "",
        extra_path: str = "",
        local_path: str = "",
        status: str = "candidate",
        confidence: float = 0.55,
    ) -> str:
        row = db.fetch_one(
            "SELECT MAX(CAST(SUBSTR(evidence_id, 5) AS INTEGER)) as max_id FROM article_evidence WHERE evidence_id LIKE 'EVD_%'"
        )
        evidence_id = f"EVD_{(row['max_id'] or 0) + 1:06d}"
        db.execute(
            """INSERT INTO article_evidence
               (evidence_id, project_id, article_id, resource_id, evidence_type,
                evidence_text, page_or_section, confidence, status, local_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_id,
                project_id,
                article_id,
                resource_id,
                evidence_type,
                evidence_text + (f"\n[PATH]{extra_path}" if extra_path else ""),
                page_or_section,
                confidence,
                status,
                local_path,
                datetime.now().isoformat(),
            ),
        )
        db.commit()
        return evidence_id

    def map_table(
        self,
        project_id: str,
        table_id: str,
        grouped: bool = True,
        use_llm: bool = False,
        header_descriptions: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            sm = SchemaManager()
            sm.load_from_file(project_dir / "schema" / "geochem_schema.yaml")
            memory = MemoryStore(project_dir / "memory" / "mapping_rules.yaml")
            memory.load()
            llm_client = LLMClient(load_config(), db=db) if use_llm else None
            self.events.emit("map", f"Mapping {table_id}", 0.35, table_id=table_id)
            suggestions = MappingEngine(
                sm,
                memory_store=memory,
                llm_client=llm_client,
                provider_override=provider,
                model_override=model,
            ).map_table(
                db,
                table_id,
                project_id=project_id,
                use_llm=use_llm,
                grouped=grouped,
                header_descriptions_path=header_descriptions,
            )
            reviews = ReviewManager().create_for_table(db, table_id)
            self.events.emit("map", f"Mapped {len(suggestions)} field(s)", 1.0, reviews=reviews)
            return {"mapped": len(suggestions), "review_items": reviews}
        finally:
            db.close()

    def standardize(self, project_id: str, table_id: str) -> dict[str, Any]:
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            self.events.emit("standardize", f"Standardizing {table_id}", 0.5, table_id=table_id)
            count = StandardizationPipeline().standardize_table(db, project_dir, table_id)
            self.events.emit("standardize", f"Standardized {count} record(s)", 1.0, records=count)
            return {"records": count}
        finally:
            db.close()

    def teach_value(self, project_id: str, article_id: str, **kwargs) -> dict[str, str]:
        db = self.pm.get_database(project_id)
        try:
            result = TeachingManager().add_event(db=db, project_id=project_id, article_id=article_id, **kwargs)
            self.events.emit("teach", f"Teaching saved {result['event_id']}", 1.0, **result)
            return result
        finally:
            db.close()

    def apply_teaching(self, project_id: str, table_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            count = RuleApplicationEngine().apply(db, project_id, table_id)
            self.events.emit("teach", f"Applied {count} patch(es)", 1.0, patches=count)
            return {"patches": count}
        finally:
            db.close()

    def export_audit_package(self, project_id: str, table_id: str, data_file: Path, export_format: str = "csv") -> dict[str, Any]:
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            package_dir = AuditPackageBuilder().create(db, project_id, project_dir, table_id, data_file, export_format)
            self.events.emit("export", f"Audit package created", 1.0, package=str(package_dir))
            return {"package_dir": str(package_dir)}
        finally:
            db.close()

    def trace_table(self, project_id: str, table_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            return TraceService().trace_table(db, table_id)
        finally:
            db.close()

    def cost_report(self, project_id: str, group_by: str = "model") -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            return CostReporter().report(db, project_id, group_by)
        finally:
            db.close()

    def decide_review(
        self,
        project_id: str,
        review_id: str,
        action: str,
        target_field: str | None = None,
        target_unit: str | None = None,
        formula: str | None = None,
        save_as_rule: bool = False,
        rule_scope: str = "project",
        notes: str = "",
    ) -> dict[str, Any]:
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            memory_path = project_dir / "memory" / "mapping_rules.yaml"
            result = ReviewManager().decide(
                db, review_id, action,
                target_field=target_field,
                target_unit=target_unit,
                formula=formula,
                save_as_rule=save_as_rule,
                rule_scope=rule_scope,
                notes=notes,
                memory_path=memory_path,
            )
            self.events.emit("review", f"Review {review_id}: {action}", 1.0, **result)
            return result
        finally:
            db.close()

    def confirm_mapping(
        self,
        project_id: str,
        mapping_id: str,
        target_field: str | None = None,
        target_unit: str | None = None,
        save_as_rule: bool = False,
    ) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            sets = ["requires_review = 0"]
            params: list[Any] = []
            if target_field:
                sets.append("target_field = ?")
                params.append(target_field)
            if target_unit:
                sets.append("target_unit = ?")
                params.append(target_unit)
            params.append(mapping_id)
            db.execute(f"UPDATE field_mappings SET {', '.join(sets)} WHERE mapping_id = ?", tuple(params))
            db.commit()
            self.events.emit("map", f"Confirmed mapping {mapping_id}", 1.0)
            return {"mapping_id": mapping_id, "status": "confirmed"}
        finally:
            db.close()

    def reject_mapping(self, project_id: str, mapping_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            db.execute("DELETE FROM field_mappings WHERE mapping_id = ?", (mapping_id,))
            db.commit()
            self.events.emit("map", f"Rejected mapping {mapping_id}", 1.0)
            return {"mapping_id": mapping_id, "status": "rejected"}
        finally:
            db.close()

    def delete_resource(self, project_id: str, resource_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            tables = db.fetch_all("SELECT table_id FROM candidate_tables WHERE resource_id = ?", (resource_id,))
            for t in tables:
                db.execute("DELETE FROM candidate_rows WHERE table_id = ?", (t["table_id"],))
                db.execute("DELETE FROM candidate_columns WHERE table_id = ?", (t["table_id"],))
            db.execute("DELETE FROM candidate_tables WHERE resource_id = ?", (resource_id,))
            db.execute("DELETE FROM resources WHERE resource_id = ?", (resource_id,))
            db.commit()
            self.events.emit("resource", f"Deleted resource {resource_id}", 1.0)
            return {"resource_id": resource_id, "status": "deleted"}
        finally:
            db.close()

    def toggle_rule(self, project_id: str, rule_id: str, rule_type: str, enabled: bool) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            status = "confirmed" if enabled else "rejected"
            table = "mapping_rules" if rule_type == "mapping" else "learned_extraction_rules"
            db.execute(f"UPDATE {table} SET review_status = ? WHERE rule_id = ?", (status, rule_id))
            db.commit()
            self.events.emit("rule", f"Rule {rule_id} {'enabled' if enabled else 'disabled'}", 1.0)
            return {"rule_id": rule_id, "status": status}
        finally:
            db.close()


class TaskService:
    """Synchronous task facade for UI buttons and tests.

    The PySide layer can run these methods in a QThread later; keeping this class
    UI-free makes the business flow easy to test.
    """

    def __init__(self, runner: WorkflowRunner | None = None):
        self.runner = runner or WorkflowRunner()

    def run(self, action: str, **kwargs) -> Any:
        if not hasattr(self.runner, action):
            raise ValueError(f"Unknown workflow action: {action}")
        method = getattr(self.runner, action)
        return method(**kwargs)
