"""Article workbench domain services shared by the Web UI and CLI."""

from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from .core.config import load_config
from .core.project import ProjectManager
from .providers.llm_client import LLMClient


ProgressCallback = Callable[[str, float, dict[str, Any]], None]


class WorkbenchService:
    """Durable, schema-driven article resource and candidate-record workflow."""

    PARSER_VERSION = "layout-v2"

    def __init__(self, project_manager: ProjectManager | None = None):
        self.pm = project_manager or ProjectManager()

    def ensure_session(self, project_id: str, article_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                "SELECT * FROM workbench_sessions WHERE project_id = ? AND article_id = ?",
                (project_id, article_id),
            )
            assignment = db.fetch_one(
                """SELECT config_id FROM article_header_assignments
                   WHERE project_id = ? AND article_id = ? AND status = 'confirmed'
                   ORDER BY created_at DESC LIMIT 1""",
                (project_id, article_id),
            )
            config_id = assignment["config_id"] if assignment else None
            now = datetime.now().isoformat()
            if not row:
                session_id = self._next_id(db, "workbench_sessions", "session_id", "WBS", 6)
                db.execute(
                    """INSERT OR IGNORE INTO workbench_sessions
                       (session_id, project_id, article_id, header_config_id, current_step,
                        discovery_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'discovery', 'pending', ?, ?)""",
                    (session_id, project_id, article_id, config_id, now, now),
                )
                db.commit()
                row = db.fetch_one(
                    "SELECT * FROM workbench_sessions WHERE project_id = ? AND article_id = ?",
                    (project_id, article_id),
                )
            elif config_id and row["header_config_id"] != config_id:
                db.execute(
                    "UPDATE workbench_sessions SET header_config_id = ?, discovery_status = 'stale', updated_at = ? WHERE session_id = ?",
                    (config_id, now, row["session_id"]),
                )
                db.commit()
                row = db.fetch_one("SELECT * FROM workbench_sessions WHERE session_id = ?", (row["session_id"],))
            return dict(row)
        finally:
            db.close()

    def target_headers(self, project_id: str, article_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT hc.config_id, hc.headers_json
                   FROM article_header_assignments a
                   JOIN header_configs hc ON hc.config_id = a.config_id
                   WHERE a.project_id = ? AND a.article_id = ? AND a.status = 'confirmed'
                   ORDER BY a.created_at DESC LIMIT 1""",
                (project_id, article_id),
            )
            if not row:
                row = db.fetch_one(
                    "SELECT config_id, headers_json FROM header_configs WHERE project_id = ? AND status != 'deleted' ORDER BY updated_at DESC LIMIT 1",
                    (project_id,),
                )
            if not row:
                return []
            raw = json.loads(row["headers_json"] or "[]")
            result = []
            for index, item in enumerate(raw):
                display = item.get("字段名") or item.get("display_header") or item.get("header") or ""
                canonical = item.get("canonical_field") or display
                unit = item.get("默认单位") or item.get("target_unit") or ""
                description = item.get("字段说明") or item.get("description") or ""
                if not display:
                    continue
                result.append({
                    "header_id": item.get("header_id") or f"{row['config_id']}:{index}",
                    "display_header": display,
                    "canonical_field": canonical,
                    "target_unit": "" if unit == "—" else unit,
                    "description": description,
                    "order": index,
                })
            return result
        finally:
            db.close()

    def discover_article(
        self,
        project_id: str,
        article_id: str,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Discover page-positioned tables, figures and relevant paragraphs."""
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            session = self.ensure_session(project_id, article_id)
            targets = self.target_headers(project_id, article_id)
            resources = db.fetch_all(
                """SELECT * FROM resources WHERE article_id = ?
                   AND resource_type IN ('main_pdf', 'supplementary_pdf') ORDER BY created_at""",
                (article_id,),
            )
            now = datetime.now().isoformat()
            db.execute(
                "UPDATE workbench_sessions SET discovery_status = 'running', updated_at = ? WHERE session_id = ?",
                (now, session["session_id"]),
            )
            db.commit()
            totals = {"table": 0, "figure": 0, "paragraph": 0}
            if progress:
                progress("正在分析 PDF 页面布局", 0.05, {"resource_count": len(resources)})
            for resource_index, resource_row in enumerate(resources):
                resource = dict(resource_row)
                pdf_path = Path(resource.get("local_path") or "")
                if not pdf_path.is_absolute():
                    pdf_path = project_dir / pdf_path
                if not pdf_path.exists():
                    continue
                discovered = self._discover_pdf(db, project_id, article_id, resource, pdf_path, targets, progress)
                for key in totals:
                    totals[key] += discovered[key]
                if progress:
                    progress(
                        f"已完成 {resource.get('file_name') or resource['resource_id']}",
                        0.1 + 0.8 * ((resource_index + 1) / max(1, len(resources))),
                        discovered,
                    )
            digest = hashlib.sha256(
                json.dumps({"resources": [r["file_hash"] for r in resources], "headers": targets}, sort_keys=True).encode()
            ).hexdigest()[:16]
            db.execute(
                """UPDATE workbench_sessions SET discovery_status = 'completed', discovery_hash = ?,
                   current_step = 'discovery', updated_at = ? WHERE session_id = ?""",
                (digest, datetime.now().isoformat(), session["session_id"]),
            )
            db.commit()
            if progress:
                progress("资源发现完成", 1.0, totals)
            return {"session_id": session["session_id"], **totals, "total": sum(totals.values())}
        except Exception:
            try:
                db.execute(
                    "UPDATE workbench_sessions SET discovery_status = 'failed', updated_at = ? WHERE project_id = ? AND article_id = ?",
                    (datetime.now().isoformat(), project_id, article_id),
                )
                db.commit()
            except Exception:
                pass
            raise
        finally:
            db.close()

    def _discover_pdf(
        self,
        db,
        project_id: str,
        article_id: str,
        resource: dict[str, Any],
        pdf_path: Path,
        targets: list[dict[str, Any]],
        progress: ProgressCallback | None,
    ) -> dict[str, int]:
        import fitz

        # Keep referenced evidence available for old candidate-cell trace records.
        # Rediscovery reactivates matching elements and leaves removed ones stale.
        db.execute(
            "UPDATE document_elements SET status = 'stale', updated_at = ? "
            "WHERE resource_id = ? AND parser_version != 'manual'",
            (datetime.now().isoformat(), resource["resource_id"]),
        )
        totals = {"table": 0, "figure": 0, "paragraph": 0}
        with fitz.open(str(pdf_path)) as doc:
            for page_index, page in enumerate(doc):
                page_number = page_index + 1
                blocks = [b for b in page.get_text("blocks") if len(b) >= 7 and int(b[6]) == 0 and str(b[4]).strip()]
                page_rect = page.rect
                tables = self._table_regions(blocks, page_rect)
                table_boxes: list[tuple[float, float, float, float]] = []
                for table in tables:
                    table_boxes.append(table["bbox"])
                    matched = self._matched_headers(table["text"], targets)
                    preview = self._render_region(pdf_path, page_index, table["bbox"], "tables")
                    self._upsert_element(
                        db, project_id, article_id, resource["resource_id"], "table", page_number,
                        self._normalize_bbox(table["bbox"], page_rect), table["text"], table["text"],
                        table["caption"], preview, {"headers": table["headers"], "body_text": table["body_text"]},
                        matched, max(0.6, self._relevance(matched, table["text"])),
                    )
                    totals["table"] += 1

                for image_index, image in enumerate(page.get_images(full=True), start=1):
                    rects = page.get_image_rects(image[0])
                    for rect in rects[:1]:
                        if rect.width < 35 or rect.height < 35:
                            continue
                        bbox = (rect.x0, rect.y0, rect.x1, rect.y1)
                        caption = self._nearby_caption(blocks, bbox, "figure")
                        matched = self._matched_headers(caption, targets)
                        preview = self._render_region(pdf_path, page_index, bbox, "figures")
                        self._upsert_element(
                            db, project_id, article_id, resource["resource_id"], "figure", page_number,
                            self._normalize_bbox(bbox, page_rect), caption or f"Figure image {image_index}",
                            caption, caption, preview, {}, matched,
                            max(0.35, self._relevance(matched, caption)),
                        )
                        totals["figure"] += 1

                for block in blocks:
                    bbox = tuple(float(v) for v in block[:4])
                    text = " ".join(str(block[4]).split())
                    if len(text) < 35 or any(self._iou(bbox, box) > 0.45 for box in table_boxes):
                        continue
                    matched = self._matched_headers(text, targets)
                    keywords = re.search(r"\b(geochem\w*|sample\w*|ppm|wt\s*%|isotope\w*|concentration\w*|formation|depth|age)\b", text, re.I)
                    if not matched and not keywords:
                        continue
                    context = self._paragraph_context(blocks, block)
                    preview = self._render_region(pdf_path, page_index, bbox, "paragraphs")
                    self._upsert_element(
                        db, project_id, article_id, resource["resource_id"], "paragraph", page_number,
                        self._normalize_bbox(bbox, page_rect), text, context, "", preview, {}, matched,
                        self._relevance(matched, text),
                    )
                    totals["paragraph"] += 1
                # Release the write transaction before the task logger writes
                # progress through its own SQLite connection.
                db.commit()
                if progress:
                    progress(
                        f"正在解析第 {page_number}/{len(doc)} 页",
                        0.1 + 0.75 * (page_number / max(1, len(doc))),
                        {"page": page_number, "pages": len(doc)},
                    )
        db.commit()
        return totals

    def list_elements(self, project_id: str, article_id: str) -> list[dict[str, Any]]:
        session = self.ensure_session(project_id, article_id)
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT e.*, CASE WHEN s.selection_id IS NULL THEN 0 ELSE 1 END AS selected
                   FROM document_elements e
                   LEFT JOIN element_selections s ON s.element_id = e.element_id
                    AND s.session_id = ? AND s.status = 'selected'
                   WHERE e.project_id = ? AND e.article_id = ? AND e.status != 'stale'
                   ORDER BY e.page_number, CASE e.element_type WHEN 'table' THEN 1 WHEN 'figure' THEN 2 ELSE 3 END,
                            e.relevance_score DESC""",
                (session["session_id"], project_id, article_id),
            )
            return [self._decode_element(dict(row)) for row in rows]
        finally:
            db.close()

    def set_selections(self, project_id: str, article_id: str, element_ids: list[str]) -> dict[str, Any]:
        session = self.ensure_session(project_id, article_id)
        db = self.pm.get_database(project_id)
        try:
            db.execute("DELETE FROM element_selections WHERE session_id = ?", (session["session_id"],))
            now = datetime.now().isoformat()
            for element_id in dict.fromkeys(element_ids):
                selection_id = self._next_id(db, "element_selections", "selection_id", "SEL", 6)
                db.execute(
                    """INSERT INTO element_selections
                       (selection_id, session_id, element_id, selected_by, status, created_at)
                       VALUES (?, ?, ?, 'user', 'selected', ?)""",
                    (selection_id, session["session_id"], element_id, now),
                )
            db.commit()
            return {"session_id": session["session_id"], "selected": len(set(element_ids))}
        finally:
            db.close()

    def add_manual_element(
        self,
        project_id: str,
        article_id: str,
        resource_id: str,
        page_number: int,
        bbox: list[float],
        element_type: str,
        note: str = "",
    ) -> dict[str, Any]:
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            resource = db.fetch_one(
                "SELECT * FROM resources WHERE resource_id = ? AND article_id = ?",
                (resource_id, article_id),
            )
            if not resource:
                raise ValueError("PDF resource not found")
            path = Path(resource["local_path"] or "")
            if not path.is_absolute():
                path = project_dir / path
            import fitz
            with fitz.open(str(path)) as doc:
                page = doc[int(page_number) - 1]
                norm = self._clamp_bbox(bbox)
                rect = fitz.Rect(
                    page.rect.x0 + page.rect.width * norm[0],
                    page.rect.y0 + page.rect.height * norm[1],
                    page.rect.x0 + page.rect.width * norm[2],
                    page.rect.y0 + page.rect.height * norm[3],
                )
                text = " ".join((page.get_text("text", clip=rect) or "").split())
                preview = self._render_region(path, page_number - 1, tuple(rect), element_type + "s")
            targets = self.target_headers(project_id, article_id)
            matched = self._matched_headers(f"{note} {text}", targets)
            element_id = self._upsert_element(
                db, project_id, article_id, resource_id, element_type, page_number, norm,
                text or note, text or note, note, preview, {}, matched, 1.0,
                parser_version="manual", force_new=True,
            )
            db.commit()
            return self._decode_element(dict(db.fetch_one(
                "SELECT * FROM document_elements WHERE element_id = ?", (element_id,)
            )))
        finally:
            db.close()

    def update_element(self, project_id: str, element_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {"text_content", "bbox", "element_type", "caption"}
        values = {k: v for k, v in updates.items() if k in allowed and v is not None}
        if not values:
            raise ValueError("No editable element fields supplied")
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                "SELECT * FROM document_elements WHERE project_id = ? AND element_id = ?",
                (project_id, element_id),
            )
            if not row:
                raise ValueError("Element not found")
            sets: list[str] = []
            params: list[Any] = []
            if "text_content" in values:
                sets.append("text_content = ?")
                params.append(values["text_content"][:12000])
                # Re-match headers after text change
                article_id = row["article_id"]
                targets = self.target_headers(project_id, article_id)
                matched = self._matched_headers(values["text_content"], targets)
                sets.append("matched_headers_json = ?")
                params.append(json.dumps(matched, ensure_ascii=False))
                sets.append("relevance_score = ?")
                params.append(self._relevance(matched, values["text_content"]))
            if "bbox" in values:
                sets.append("bbox_json = ?")
                params.append(json.dumps(self._clamp_bbox(values["bbox"])))
            if "element_type" in values:
                sets.append("element_type = ?")
                params.append(values["element_type"])
            if "caption" in values:
                sets.append("caption = ?")
                params.append(values["caption"][:3000])
            sets.append("updated_at = ?")
            params.append(datetime.now().isoformat())
            params.append(element_id)
            db.execute(f"UPDATE document_elements SET {', '.join(sets)} WHERE element_id = ?", tuple(params))
            db.commit()
            return self._decode_element(dict(db.fetch_one("SELECT * FROM document_elements WHERE element_id = ?", (element_id,))))
        finally:
            db.close()

    def delete_element(self, project_id: str, element_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                "SELECT parser_version, status FROM document_elements WHERE project_id = ? AND element_id = ?",
                (project_id, element_id),
            )
            if not row:
                raise ValueError("Element not found")
            if row["parser_version"] != "manual":
                raise ValueError("Only manual elements can be deleted")
            db.execute("DELETE FROM element_selections WHERE element_id = ?", (element_id,))
            db.execute("DELETE FROM document_elements WHERE element_id = ?", (element_id,))
            db.commit()
            return {"element_id": element_id, "status": "deleted"}
        finally:
            db.close()

    # ── Mapping confirmation with conditional rule persistence ──

    def confirm_cell_mapping(
        self, project_id: str, cell_id: str,
        target_field: str | None = None,
        target_unit: str | None = None,
        formula: str | None = None,
        conversion_factor: float | None = None,
        llm_suggestion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Confirm a candidate cell mapping. Only writes rules when user modifies LLM suggestion."""
        db = self.pm.get_database(project_id)
        try:
            cell = db.fetch_one(
                """SELECT c.*, r.article_id FROM candidate_cells c JOIN candidate_records r ON r.candidate_record_id = c.candidate_record_id
                   JOIN extraction_batches b ON b.batch_id = r.batch_id WHERE b.project_id = ? AND c.cell_id = ?""",
                (project_id, cell_id),
            )
            if not cell:
                raise ValueError("Candidate cell not found")
            cell = dict(cell)

            # F1: Validate target_field against header config
            if target_field:
                headers = self.target_headers(project_id, cell["article_id"])
                valid_fields = {h["display_header"] for h in headers} | {h["canonical_field"] for h in headers}
                if target_field not in valid_fields:
                    raise ValueError(f"目标字段 '{target_field}' 不在当前表头配置中")

            # B3: Check for duplicate target_field in same record
            if target_field:
                conflict = db.fetch_one(
                    "SELECT cell_id FROM candidate_cells WHERE candidate_record_id = ? AND target_header = ? AND cell_id != ?",
                    (cell["candidate_record_id"], target_field, cell_id),
                )
                if conflict:
                    raise ValueError(f"该记录中已有字段 '{target_field}'，不能重复映射")

            now = datetime.now().isoformat()
            sets = ["mapping_status = 'confirmed'", "review_status = 'confirmed'", "updated_at = ?"]
            params: list[Any] = [now]
            if target_field:
                sets.append("target_field = ?")
                params.append(target_field)
                sets.append("target_header = ?")
                params.append(target_field)
            if target_unit:
                sets.append("target_unit = ?")
                params.append(target_unit)
            params.append(cell_id)
            db.execute(f"UPDATE candidate_cells SET {', '.join(sets)} WHERE cell_id = ?", tuple(params))
            db.commit()

            # Only write rules when user modified the LLM suggestion
            user_modified = False
            if llm_suggestion:
                if target_field and target_field != llm_suggestion.get("target_field"):
                    user_modified = True
                if target_unit and target_unit != llm_suggestion.get("target_unit"):
                    user_modified = True
                if formula and formula != llm_suggestion.get("formula"):
                    user_modified = True
            elif target_field or target_unit or formula:
                user_modified = True

            if user_modified:
                self._save_mapping_rule(db, project_id, cell, target_field, target_unit, formula, conversion_factor)

            # Apply unit conversion if needed
            if formula and conversion_factor:
                self._apply_conversion(db, project_id, cell, formula, conversion_factor)

            return dict(db.fetch_one("SELECT * FROM candidate_cells WHERE cell_id = ?", (cell_id,)))
        finally:
            db.close()

    def _save_mapping_rule(
        self, db, project_id: str, cell: dict[str, Any],
        target_field: str | None, target_unit: str | None,
        formula: str | None, conversion_factor: float | None,
    ) -> str:
        source_field = cell["original_field"] or cell["target_header"]
        final_target = target_field or cell["target_field"]
        final_unit = target_unit or cell["target_unit"]
        mapping_type = "unit_conversion" if formula else "user_override"
        now = datetime.now().isoformat()

        # F3: Get config_id for this article
        config_id = ""
        article_id = cell.get("article_id", "")
        if article_id:
            assignment = db.fetch_one(
                "SELECT config_id FROM article_header_assignments WHERE project_id = ? AND article_id = ? AND status = 'confirmed' ORDER BY created_at DESC LIMIT 1",
                (project_id, article_id),
            )
            if assignment:
                config_id = assignment["config_id"]

        # B4: Check for existing rule with same source→target
        existing = db.fetch_one(
            "SELECT rule_id FROM mapping_rules WHERE source_field = ? AND target_field = ? AND (config_id = ? OR config_id = '')",
            (source_field, final_target, config_id),
        )
        if existing:
            db.execute(
                "UPDATE mapping_rules SET target_unit = ?, mapping_type = ?, formula = ?, conversion_factor = ?, review_status = 'confirmed', created_by = 'user' WHERE rule_id = ?",
                (final_unit, mapping_type, formula, conversion_factor, existing["rule_id"]),
            )
            db.commit()
            self._write_rules_md(project_id)
            return existing["rule_id"]

        rule_id = self._next_id(db, "mapping_rules", "rule_id", "RULE", 6)
        db.execute(
            """INSERT INTO mapping_rules
               (rule_id, source_field, target_field, source_unit, target_unit,
                mapping_type, formula, conversion_factor, review_status, scope, version,
                created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 'project', 1, ?, 'user')""",
            (rule_id, source_field, final_target, cell["original_unit"], final_unit,
             mapping_type, formula, conversion_factor, now),
        )
        db.commit()
        self._write_rules_md(project_id)
        return rule_id

    def _write_rules_md(self, project_id: str) -> None:
        """Write mapping_rules to memory/rules.md for agent reading."""
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            rules = db.fetch_all("SELECT * FROM mapping_rules WHERE review_status = 'confirmed' ORDER BY created_at DESC")
            lines = ["# Mapping Rules Memory", "", "Agent 抽取和资源发现时读取此文件作为上下文。", ""]
            for rule in [dict(r) for r in rules]:
                lines.extend([
                    f"## {rule['rule_id']}",
                    f"- 来源: {'用户修改' if rule.get('created_by') == 'user' else '系统内置'}",
                    f"- 原始字段: {rule['source_field']}",
                    f"- 目标字段: {rule['target_field']}",
                    f"- 原始单位: {rule.get('source_unit') or '—'}",
                    f"- 目标单位: {rule.get('target_unit') or '—'}",
                    f"- 类型: {rule['mapping_type']}",
                    f"- 公式: {rule.get('formula') or '—'}",
                    f"- 因子: {rule.get('conversion_factor') or '—'}",
                    f"- 创建: {rule['created_at']}",
                    "",
                ])
            md_path = project_dir / "memory" / "rules.md"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text("\n".join(lines), encoding="utf-8")
        finally:
            db.close()

    def _apply_conversion(
        self, db, project_id: str, cell: dict[str, Any],
        formula: str, factor: float,
    ) -> None:
        """Apply unit conversion and archive the calculation."""
        try:
            original_value = float(cell["original_value"])
        except (ValueError, TypeError):
            return
        result = original_value * factor
        now = datetime.now().isoformat()
        calc_id = self._next_id(db, "calculation_records", "calc_id", "CALC", 7)
        db.execute(
            """INSERT INTO calculation_records
               (calc_id, article_id, table_id, row_id, target_field, source_field,
                source_value, source_unit, target_unit, formula, substitution, result,
                review_status, candidate_record_id, cell_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?)""",
            (calc_id, cell.get("article_id", ""), "", cell.get("candidate_record_id", ""), cell["target_field"], cell["original_field"],
             original_value, cell["original_unit"], cell["target_unit"], formula,
             f"{original_value} × {factor}", result, cell.get("candidate_record_id"), cell.get("cell_id"), now),
        )
        db.execute(
            "UPDATE candidate_cells SET value = ?, updated_at = ? WHERE cell_id = ?",
            (f"{result:.6g}", now, cell["cell_id"]),
        )
        db.commit()

    def _auto_apply_rules(self, db, project_id: str, cell: dict[str, Any]) -> bool:
        """Check mapping_rules for auto-applicable rules. Returns True if a rule was applied."""
        rules = db.fetch_all(
            "SELECT * FROM mapping_rules WHERE review_status = 'confirmed' AND source_field = ?",
            (cell["original_field"] or cell["target_header"],),
        )
        for rule_row in [dict(r) for r in rules]:
            rule = rule_row
            if rule.get("formula") and rule.get("conversion_factor"):
                self._apply_conversion(db, project_id, cell, rule["formula"], float(rule["conversion_factor"]))
            if rule["target_field"] != cell["target_field"]:
                db.execute(
                    "UPDATE candidate_cells SET target_field = ?, mapping_status = 'auto_applied', updated_at = ? WHERE cell_id = ?",
                    (rule["target_field"], datetime.now().isoformat(), cell["cell_id"]),
                )
            db.execute(
                "UPDATE candidate_cells SET mapping_status = 'auto_applied', review_status = 'confirmed', updated_at = ? WHERE cell_id = ?",
                (datetime.now().isoformat(), cell["cell_id"]),
            )
            db.commit()
            return True
        return False

    def suggest_conversion(self, project_id: str, cell_id: str) -> dict[str, Any]:
        """Use LLM to suggest a unit conversion formula for a cell."""
        db = self.pm.get_database(project_id)
        try:
            cell = db.fetch_one(
                """SELECT c.* FROM candidate_cells c JOIN candidate_records r ON r.candidate_record_id = c.candidate_record_id
                   JOIN extraction_batches b ON b.batch_id = r.batch_id WHERE b.project_id = ? AND c.cell_id = ?""",
                (project_id, cell_id),
            )
            if not cell:
                raise ValueError("Cell not found")
            config = load_config()
            client = LLMClient(config, db=db)
            prompt = (
                f"Geochemical unit conversion suggestion.\n"
                f"Source field: {cell['original_field']} (unit: {cell['original_unit']})\n"
                f"Target field: {cell['target_field']} (unit: {cell['target_unit']})\n"
                f"Sample value: {cell['original_value']}\n\n"
                f"Return JSON: {{\"formula\": \"Na = Na2O × 0.741857\", \"factor\": 0.741857, \"explanation\": \"...\"}}\n"
                f"If no conversion is needed, return {{\"formula\": null, \"factor\": 1.0, \"explanation\": \"same unit\"}}"
            )
            response = client.chat(
                [{"role": "system", "content": "You are a geochemistry unit conversion expert. Return only JSON."},
                 {"role": "user", "content": prompt}],
                task_name="unit_suggestion", project_id=project_id,
                article_id=cell.get("article_id", ""),
                agent_name="workbench", skill_name="unit_suggestion",
                temperature_override=0.0, max_tokens_override=500, use_cache=True,
            )
            match = re.search(r"\{[\s\S]*\}", response.content or "")
            if match:
                return json.loads(match.group(0))
            return {"formula": None, "factor": 1.0, "explanation": "Could not parse LLM response"}
        finally:
            db.close()

    # ── Row-level review ──

    def approve_record(self, project_id: str, record_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            now = datetime.now().isoformat()
            db.execute(
                "UPDATE candidate_cells SET review_status = 'approved', updated_at = ? WHERE candidate_record_id = ? AND mapping_status = 'confirmed'",
                (now, record_id),
            )
            db.execute(
                "UPDATE candidate_records SET quality_grade = 'B', updated_at = ? WHERE candidate_record_id = ?",
                (now, record_id),
            )
            db.commit()
            return {"candidate_record_id": record_id, "status": "approved"}
        finally:
            db.close()

    def reject_record(self, project_id: str, record_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            now = datetime.now().isoformat()
            db.execute(
                "UPDATE candidate_cells SET review_status = 'rejected', updated_at = ? WHERE candidate_record_id = ?",
                (now, record_id),
            )
            db.execute(
                "UPDATE candidate_records SET quality_grade = 'E', updated_at = ? WHERE candidate_record_id = ?",
                (now, record_id),
            )
            db.commit()
            return {"candidate_record_id": record_id, "status": "rejected"}
        finally:
            db.close()

    def batch_approve_records(self, project_id: str, record_ids: list[str]) -> dict[str, Any]:
        for record_id in record_ids:
            self.approve_record(project_id, record_id)
        return {"approved": len(record_ids)}

    def article_candidate_records(self, project_id: str, article_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            headers = self.target_headers(project_id, article_id)
            # B5: Only return records from the latest completed batch
            latest_batch = db.fetch_one(
                "SELECT batch_id FROM extraction_batches WHERE project_id = ? AND article_id = ? AND status = 'completed' ORDER BY created_at DESC LIMIT 1",
                (project_id, article_id),
            )
            if not latest_batch:
                return {"headers": headers, "records": []}
            records = []
            for row in db.fetch_all(
                "SELECT * FROM candidate_records WHERE batch_id = ? ORDER BY row_index",
                (latest_batch["batch_id"],),
            ):
                record = dict(row)
                data = {h["display_header"]: "" for h in headers}
                cells: dict[str, Any] = {}
                for cell_row in db.fetch_all(
                    "SELECT * FROM candidate_cells WHERE candidate_record_id = ?",
                    (record["candidate_record_id"],),
                ):
                    cell = dict(cell_row)
                    cell["bbox"] = json.loads(cell.pop("bbox_json") or "[]")
                    cell["alternatives"] = json.loads(cell.pop("alternatives_json") or "[]")
                    data[cell["target_header"]] = cell["value"]
                    cells[cell["target_header"]] = cell
                records.append({**record, "data": data, "cells": cells})
            return {"headers": headers, "records": records}
        finally:
            db.close()

    def batch_confirm_mappings(self, project_id: str, article_id: str, confirmations: list[dict[str, Any]]) -> dict[str, Any]:
        """Batch confirm mappings. Only writes rules for user-modified items."""
        results = []
        errors = []
        for item in confirmations:
            try:
                result = self.confirm_cell_mapping(
                    project_id, item["cell_id"],
                    target_field=item.get("target_field"),
                    target_unit=item.get("target_unit"),
                    formula=item.get("formula"),
                    conversion_factor=item.get("conversion_factor"),
                    llm_suggestion=item.get("llm_suggestion"),
                )
                results.append({"cell_id": item["cell_id"], "status": "confirmed"})
            except Exception as e:
                errors.append({"cell_id": item["cell_id"], "error": str(e)})
        return {"confirmed": len(results), "errors": errors}

    def delete_rule(self, project_id: str, rule_id: str, rule_type: str = "mapping") -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            table = "mapping_rules" if rule_type == "mapping" else "learned_extraction_rules"
            db.execute(f"DELETE FROM {table} WHERE rule_id = ?", (rule_id,))
            db.commit()
            if rule_type == "mapping":
                self._write_rules_md(project_id)
            else:
                self._write_extraction_rules_md(project_id)
            return {"rule_id": rule_id, "status": "deleted"}
        finally:
            db.close()

    # ── Manual OCR fill from PDF ──

    def manual_fill(
        self, project_id: str, article_id: str,
        resource_id: str, page_number: int, bbox: list[float],
        target_header: str, value: str, unit: str, explanation: str,
    ) -> dict[str, Any]:
        """User draws a box on PDF, OCR reads, fills into candidate table, and saves extraction rule."""
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            # OCR the region
            resource = db.fetch_one("SELECT * FROM resources WHERE resource_id = ?", (resource_id,))
            if not resource:
                raise ValueError("Resource not found")
            pdf_path = Path(resource["local_path"] or "")
            if not pdf_path.is_absolute():
                pdf_path = project_dir / pdf_path
            import fitz
            with fitz.open(str(pdf_path)) as doc:
                page = doc[int(page_number) - 1]
                norm = self._clamp_bbox(bbox)
                rect = fitz.Rect(
                    page.rect.x0 + page.rect.width * norm[0],
                    page.rect.y0 + page.rect.height * norm[1],
                    page.rect.x0 + page.rect.width * norm[2],
                    page.rect.y0 + page.rect.height * norm[3],
                )
                ocr_text = " ".join((page.get_text("text", clip=rect) or "").split())
                preview = self._render_region(pdf_path, page_number - 1, tuple(rect), "manual")

            # Find or create the candidate record for this article
            session = self.ensure_session(project_id, article_id)
            batch = db.fetch_one(
                "SELECT batch_id FROM extraction_batches WHERE project_id = ? AND article_id = ? AND status = 'completed' ORDER BY created_at DESC LIMIT 1",
                (project_id, article_id),
            )
            if not batch:
                raise ValueError("No extraction batch found. Run extraction first.")

            # Find the target header
            headers = self.target_headers(project_id, article_id)
            target = next((h for h in headers if h["display_header"] == target_header), None)
            if not target:
                raise ValueError(f"Target header '{target_header}' not found")

            # Create or find a record (use a manual-fill specific sample key)
            sample_key = f"manual:{page_number}:{target_header}"
            record = db.fetch_one(
                "SELECT * FROM candidate_records WHERE batch_id = ? AND sample_key = ?",
                (batch["batch_id"], sample_key),
            )
            now = datetime.now().isoformat()
            if record:
                record_id = record["candidate_record_id"]
            else:
                record_id = self._next_id(db, "candidate_records", "candidate_record_id", "CREC", 7)
                db.execute(
                    """INSERT INTO candidate_records
                       (candidate_record_id, batch_id, article_id, sample_key, sample_id, row_index,
                        merge_status, quality_grade, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'manual_fill', 'C', ?, ?)""",
                    (record_id, batch["batch_id"], article_id, sample_key, f"manual_{page_number}",
                     self._next_row_index(db, batch["batch_id"]), now, now),
                )

            # Upsert cell
            existing_cell = db.fetch_one(
                "SELECT * FROM candidate_cells WHERE candidate_record_id = ? AND target_header = ?",
                (record_id, target_header),
            )
            cell_id = existing_cell["cell_id"] if existing_cell else self._next_id(db, "candidate_cells", "cell_id", "CELL", 8)
            if existing_cell:
                db.execute(
                    "UPDATE candidate_cells SET value = ?, original_value = ?, mapping_status = 'confirmed', review_status = 'approved', element_id = ?, page_number = ?, bbox_json = ?, updated_at = ? WHERE cell_id = ?",
                    (value, value, None, page_number, json.dumps(norm), now, cell_id),
                )
            else:
                db.execute(
                    """INSERT INTO candidate_cells
                       (cell_id, candidate_record_id, header_id, target_header, target_field, target_unit,
                        value, original_value, original_field, original_unit, confidence, risk_level,
                        mapping_status, element_id, page_number, bbox_json, alternatives_json,
                        review_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, '[]', 'approved', ?, ?)""",
                    (cell_id, record_id, target["header_id"], target_header, target["canonical_field"], unit,
                     value, value, f"manual_p{page_number}", unit, 1.0, "low",
                     None, page_number, json.dumps(norm), now, now),
                )
            db.commit()

            # Save extraction rule if explanation provided
            if explanation:
                rule_id = self._next_id(db, "learned_extraction_rules", "rule_id", "ERULE", 6)
                db.execute(
                    """INSERT INTO learned_extraction_rules
                       (rule_id, project_id, target_field, target_header, rule_type, pattern,
                        confidence, risk_level, review_status, scope, created_at)
                       VALUES (?, ?, ?, ?, 'extraction_hint', ?, 0.9, 'low', 'confirmed', 'project', ?)""",
                    (rule_id, project_id, target["canonical_field"], target_header,
                     json.dumps({"explanation": explanation, "ocr_text": ocr_text[:500], "page": page_number}, ensure_ascii=False),
                     now),
                )
                db.commit()
                self._write_extraction_rules_md(project_id)

            return {"record_id": record_id, "cell_id": cell_id, "ocr_text": ocr_text, "value": value}
        finally:
            db.close()

    def _write_extraction_rules_md(self, project_id: str) -> None:
        """Write extraction rules to memory/extraction_rules.md for agent reading."""
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            rules = db.fetch_all(
                "SELECT * FROM learned_extraction_rules WHERE project_id = ? AND review_status = 'confirmed' ORDER BY created_at DESC",
                (project_id,),
            )
            lines = ["# Extraction Rules Memory", "", "Agent 资源发现和抽取时读取此文件。", ""]
            for rule in [dict(r) for r in rules]:
                pattern = json.loads(rule.get("pattern") or "{}")
                lines.extend([
                    f"## {rule['rule_id']}",
                    f"- 目标字段: {rule['target_header']} ({rule['target_field']})",
                    f"- 类型: {rule['rule_type']}",
                    f"- 说明: {pattern.get('explanation', '—')}",
                    f"- 识别规则: {pattern.get('ocr_text', '—')[:100]}",
                    f"- 创建: {rule['created_at']}",
                    "",
                ])
            md_path = project_dir / "memory" / "extraction_rules.md"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text("\n".join(lines), encoding="utf-8")
        finally:
            db.close()

    # ── Standardization & Trace ──

    def finalize_standardized(self, project_id: str, article_id: str) -> dict[str, Any]:
        """Generate standardized_records from approved candidate records."""
        db = self.pm.get_database(project_id)
        try:
            headers = self.target_headers(project_id, article_id)
            latest_batch = db.fetch_one(
                """SELECT batch_id FROM extraction_batches
                   WHERE project_id = ? AND article_id = ? AND status = 'completed'
                   ORDER BY created_at DESC LIMIT 1""",
                (project_id, article_id),
            )
            if not latest_batch:
                return {"article_id": article_id, "records": 0}
            approved_records = db.fetch_all(
                """SELECT * FROM candidate_records
                   WHERE batch_id = ? AND quality_grade != 'E' ORDER BY row_index""",
                (latest_batch["batch_id"],),
            )
            now = datetime.now().isoformat()
            count = 0
            for rec_row in approved_records:
                rec = dict(rec_row)
                cells = db.fetch_all(
                    "SELECT * FROM candidate_cells WHERE candidate_record_id = ? AND review_status != 'rejected'",
                    (rec["candidate_record_id"],),
                )
                # F5: Initialize data with ALL headers from config, then fill from cells
                data: dict[str, str] = {h["display_header"]: "" for h in headers}
                original_fields: dict[str, str] = {h["display_header"]: "" for h in headers}
                original_values: dict[str, str] = {h["display_header"]: "" for h in headers}
                confidences: dict[str, float] = {h["display_header"]: 0.0 for h in headers}
                review_statuses: dict[str, str] = {h["display_header"]: "missing" for h in headers}
                quality = "A"
                for cell_row in [dict(c) for c in cells]:
                    header = cell_row["target_header"]
                    data[header] = cell_row["value"]
                    original_fields[header] = cell_row["original_field"]
                    original_values[header] = cell_row["original_value"]
                    confidences[header] = cell_row["confidence"]
                    review_statuses[header] = cell_row["review_status"]
                    if cell_row["mapping_status"] == "auto_applied":
                        quality = "B" if quality == "A" else quality
                    elif cell_row.get("formula"):
                        quality = "C"

                record_id = self._next_id(db, "standardized_records", "record_id", "STD", 7)
                # Find source info from first cell
                first_cell = dict(cells[0]) if cells else {}
                db.execute(
                    """INSERT INTO standardized_records
                       (record_id, article_id, table_id, row_id, data, source_file, source_table,
                        source_row, original_fields, original_units, original_values, mapped_fields,
                        mapped_units, mapping_rule_ids, calculation_ids, review_statuses,
                        confidence_scores, quality_grade, processed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, ?, ?)""",
                    (record_id, article_id, "", rec["candidate_record_id"],
                     json.dumps(data, ensure_ascii=False),
                     "", "", rec.get("row_index"),
                     json.dumps(original_fields, ensure_ascii=False),
                     json.dumps({h: "" for h in data}, ensure_ascii=False),
                     json.dumps(original_values, ensure_ascii=False),
                     json.dumps({h: h for h in data}, ensure_ascii=False),
                     json.dumps({h: "" for h in data}, ensure_ascii=False),
                     json.dumps(review_statuses, ensure_ascii=False),
                     json.dumps(confidences, ensure_ascii=False),
                     quality, now),
                )
                self._snapshot_standardized_provenance(
                    db, record_id, rec, [dict(cell) for cell in cells], now,
                )
                count += 1
            db.commit()
            return {"article_id": article_id, "records": count}
        finally:
            db.close()

    def _snapshot_standardized_provenance(
        self,
        db,
        record_id: str,
        candidate_record: dict[str, Any],
        cells: list[dict[str, Any]],
        created_at: str,
    ) -> None:
        """Freeze cell-level source details when a standardized row is created."""
        for cell in cells:
            if cell.get("value") in (None, ""):
                continue
            element = None
            resource = None
            if cell.get("element_id"):
                element = db.fetch_one(
                    """SELECT element_id, resource_id, element_type, page_number, bbox_json,
                              caption, context_text
                       FROM document_elements WHERE element_id = ?""",
                    (cell["element_id"],),
                )
            if element and element["resource_id"]:
                resource = db.fetch_one(
                    "SELECT resource_id, file_name FROM resources WHERE resource_id = ?",
                    (element["resource_id"],),
                )
            calculation = db.fetch_one(
                """SELECT calc_id, formula, substitution FROM calculation_records
                   WHERE cell_id = ? OR (cell_id IS NULL AND candidate_record_id = ? AND target_field = ?)
                   ORDER BY created_at DESC LIMIT 1""",
                (cell["cell_id"], candidate_record["candidate_record_id"], cell.get("target_field", "")),
            )
            provenance_id = self._next_id(
                db, "standardized_cell_provenance", "provenance_id", "PROV", 8,
            )
            bbox_json = (
                element["bbox_json"] if element and element["bbox_json"]
                else cell.get("bbox_json") or "[]"
            )
            source_complete = int(bool(element and element["page_number"] and bbox_json != "[]"))
            db.execute(
                """INSERT INTO standardized_cell_provenance
                   (provenance_id, record_id, target_header, standardized_value, target_unit,
                    original_field, original_value, original_unit, resource_id, resource_name,
                    element_id, element_type, page_number, bbox_json, source_caption,
                    source_context, mapping_status, mapping_rule_id, calculation_id,
                    calculation_formula, calculation_substitution, review_status, confidence,
                    source_complete, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provenance_id, record_id, cell["target_header"], str(cell["value"]),
                    cell.get("target_unit") or "", cell.get("original_field") or "",
                    cell.get("original_value") or "", cell.get("original_unit") or "",
                    resource["resource_id"] if resource else None,
                    resource["file_name"] if resource else "",
                    element["element_id"] if element else cell.get("element_id"),
                    element["element_type"] if element else "",
                    element["page_number"] if element else cell.get("page_number"), bbox_json,
                    element["caption"] if element else "",
                    element["context_text"] if element else "",
                    cell.get("mapping_status") or "", cell.get("mapping_id"),
                    calculation["calc_id"] if calculation else None,
                    calculation["formula"] if calculation else "",
                    calculation["substitution"] if calculation else "",
                    cell.get("review_status") or "", float(cell.get("confidence") or 0),
                    source_complete, created_at,
                ),
            )

    def article_trace(self, project_id: str, article_id: str) -> list[dict[str, Any]]:
        """Return trace data for all approved cells in an article."""
        db = self.pm.get_database(project_id)
        try:
            records = []
            for rec_row in db.fetch_all(
                "SELECT * FROM standardized_records WHERE article_id = ? ORDER BY processed_at DESC",
                (article_id,),
            ):
                rec = dict(rec_row)
                rec["data"] = json.loads(rec.get("data") or "{}")
                rec["original_fields"] = json.loads(rec.get("original_fields") or "{}")
                rec["original_values"] = json.loads(rec.get("original_values") or "{}")
                rec["review_statuses"] = json.loads(rec.get("review_statuses") or "{}")
                rec["confidence_scores"] = json.loads(rec.get("confidence_scores") or "{}")
                # Find source element info
                source_cell = db.fetch_one(
                    "SELECT element_id, page_number, bbox_json FROM candidate_cells WHERE candidate_record_id = ? AND element_id IS NOT NULL LIMIT 1",
                    (rec.get("row_id", ""),),
                )
                if source_cell:
                    rec["source_page"] = source_cell["page_number"]
                    rec["source_bbox"] = json.loads(source_cell["bbox_json"] or "[]")
                    rec["source_element_id"] = source_cell["element_id"]
                    element = db.fetch_one(
                        "SELECT preview_path FROM document_elements WHERE element_id = ?",
                        (source_cell["element_id"],),
                    )
                    rec["source_preview"] = element["preview_path"] if element else ""
                records.append(rec)
            return records
        finally:
            db.close()

    def trace_records(
        self,
        project_id: str,
        article_id: str | None = None,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return latest standardized rows across the workspace with source summaries."""
        db = self.pm.get_database(project_id)
        try:
            params: list[Any] = [project_id]
            article_clause = ""
            if article_id:
                article_clause = " AND s.article_id = ?"
                params.append(article_id)
            rows = db.fetch_all(
                f"""SELECT s.*, a.title AS article_title, a.doi,
                           cr.sample_id AS candidate_sample_id
                    FROM standardized_records s
                    JOIN articles a ON a.article_id = s.article_id
                    LEFT JOIN candidate_records cr ON cr.candidate_record_id = s.row_id
                    WHERE a.project_id = ?{article_clause}
                    ORDER BY s.processed_at DESC, s.record_id DESC""",
                tuple(params),
            )
            latest: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            needle = query.strip().lower()
            for row in rows:
                record = dict(row)
                key = (record["article_id"], record["row_id"])
                if key in seen:
                    continue
                seen.add(key)
                data = json.loads(record.get("data") or "{}")
                sample_id = record.get("candidate_sample_id") or self._sample_from_data(data)
                searchable = " ".join([
                    record.get("record_id") or "", record.get("article_title") or "",
                    record.get("doi") or "", sample_id,
                    " ".join(f"{field} {value}" for field, value in data.items() if value not in (None, "")),
                ]).lower()
                if needle and needle not in searchable:
                    continue
                sources = self._trace_source_summaries(db, record["record_id"], record["row_id"])
                latest.append({
                    "record_id": record["record_id"],
                    "article_id": record["article_id"],
                    "article_title": record.get("article_title") or record["article_id"],
                    "doi": record.get("doi") or "",
                    "sample_id": sample_id,
                    "processed_at": record["processed_at"],
                    "nonempty_count": sum(value not in (None, "") for value in data.values()),
                    "sources": sources,
                    "source_complete": bool(sources) and all(source["source_complete"] for source in sources),
                })
            total = len(latest)
            return {"items": latest[offset:offset + limit], "total": total, "limit": limit, "offset": offset}
        finally:
            db.close()

    def trace_record_detail(self, project_id: str, record_id: str) -> dict[str, Any]:
        """Return a standardized row and field-level source, mapping and calculation details."""
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT s.*, a.project_id, a.title AS article_title, a.doi,
                          cr.sample_id AS candidate_sample_id
                   FROM standardized_records s
                   JOIN articles a ON a.article_id = s.article_id
                   LEFT JOIN candidate_records cr ON cr.candidate_record_id = s.row_id
                   WHERE a.project_id = ? AND s.record_id = ?""",
                (project_id, record_id),
            )
            if not row:
                raise ValueError("Trace record not found")
            record = dict(row)
            data = json.loads(record.get("data") or "{}")
            original_fields = json.loads(record.get("original_fields") or "{}")
            original_units = json.loads(record.get("original_units") or "{}")
            original_values = json.loads(record.get("original_values") or "{}")
            mapped_units = json.loads(record.get("mapped_units") or "{}")
            review_statuses = json.loads(record.get("review_statuses") or "{}")
            confidences = json.loads(record.get("confidence_scores") or "{}")
            snapshots = {
                item["target_header"]: dict(item)
                for item in db.fetch_all(
                    "SELECT * FROM standardized_cell_provenance WHERE record_id = ?",
                    (record_id,),
                )
            }
            fallback = {
                item["target_header"]: dict(item)
                for item in db.fetch_all(
                    """SELECT c.*, e.resource_id, e.element_type,
                              COALESCE(e.page_number, c.page_number) AS source_page_number,
                              COALESCE(e.bbox_json, c.bbox_json) AS source_bbox_json,
                              e.caption AS source_caption, e.context_text AS source_context,
                              r.file_name AS resource_name
                       FROM candidate_cells c
                       LEFT JOIN document_elements e ON e.element_id = c.element_id
                       LEFT JOIN resources r ON r.resource_id = e.resource_id
                       WHERE c.candidate_record_id = ?""",
                    (record["row_id"],),
                )
            }
            fields = []
            for target_header, value in data.items():
                if value in (None, ""):
                    continue
                snapshot = snapshots.get(target_header)
                old_cell = fallback.get(target_header)
                source = self._trace_field_source(snapshot, old_cell)
                mapping_rule_id = (snapshot or {}).get("mapping_rule_id") or (old_cell or {}).get("mapping_id")
                mapping_rule = db.fetch_one(
                    "SELECT rule_id, mapping_type, formula FROM mapping_rules WHERE rule_id = ?",
                    (mapping_rule_id,),
                ) if mapping_rule_id else None
                calculation_id = (snapshot or {}).get("calculation_id")
                calculation = db.fetch_one(
                    """SELECT calc_id, formula, substitution, result, source_unit, target_unit
                       FROM calculation_records WHERE calc_id = ?""",
                    (calculation_id,),
                ) if calculation_id else None
                if not calculation and old_cell:
                    calculation = db.fetch_one(
                        """SELECT calc_id, formula, substitution, result, source_unit, target_unit
                           FROM calculation_records WHERE cell_id = ? ORDER BY created_at DESC LIMIT 1""",
                        (old_cell["cell_id"],),
                    )
                fields.append({
                    "target_header": target_header,
                    "value": str(value),
                    "target_unit": (snapshot or {}).get("target_unit") or (old_cell or {}).get("target_unit") or mapped_units.get(target_header, ""),
                    "original_field": (snapshot or {}).get("original_field") or (old_cell or {}).get("original_field") or original_fields.get(target_header, ""),
                    "original_value": (snapshot or {}).get("original_value") or (old_cell or {}).get("original_value") or original_values.get(target_header, ""),
                    "original_unit": (snapshot or {}).get("original_unit") or (old_cell or {}).get("original_unit") or original_units.get(target_header, ""),
                    "review_status": (snapshot or {}).get("review_status") or (old_cell or {}).get("review_status") or review_statuses.get(target_header, ""),
                    "confidence": float((snapshot or {}).get("confidence") or (old_cell or {}).get("confidence") or confidences.get(target_header, 0)),
                    "mapping": dict(mapping_rule) if mapping_rule else {
                        "rule_id": mapping_rule_id,
                        "mapping_type": (snapshot or {}).get("mapping_status") or (old_cell or {}).get("mapping_status") or "",
                        "formula": "",
                    },
                    "calculation": dict(calculation) if calculation else None,
                    "source": source,
                    "source_complete": bool(source and source.get("page_number") and source.get("bbox")),
                })
            resources = [
                dict(item) for item in db.fetch_all(
                    """SELECT resource_id, resource_type, file_name, status
                       FROM resources WHERE article_id = ? AND resource_type LIKE '%pdf%'
                       ORDER BY created_at""",
                    (record["article_id"],),
                )
            ]
            return {
                "record_id": record_id,
                "article_id": record["article_id"],
                "article_title": record.get("article_title") or record["article_id"],
                "doi": record.get("doi") or "",
                "sample_id": record.get("candidate_sample_id") or self._sample_from_data(data),
                "processed_at": record["processed_at"],
                "fields": fields,
                "sources": self._trace_source_summaries(db, record_id, record["row_id"]),
                "resources": resources,
            }
        finally:
            db.close()

    def _sample_from_data(self, data: dict[str, Any]) -> str:
        for field, value in data.items():
            if re.search(r"sample\s*[_-]?\s*(?:id|name)|样品", field, re.I):
                return str(value or "")
        return ""

    def _trace_source_summaries(self, db, record_id: str, candidate_record_id: str) -> list[dict[str, Any]]:
        rows = [dict(item) for item in db.fetch_all(
            """SELECT resource_id, resource_name, element_id, element_type, page_number,
                      bbox_json, source_complete, target_header
               FROM standardized_cell_provenance WHERE record_id = ? ORDER BY page_number""",
            (record_id,),
        )]
        if not rows:
            rows = [dict(item) for item in db.fetch_all(
                """SELECT e.resource_id, r.file_name AS resource_name, e.element_id,
                          e.element_type, COALESCE(e.page_number, c.page_number) AS page_number,
                          COALESCE(e.bbox_json, c.bbox_json) AS bbox_json,
                          CASE WHEN e.element_id IS NULL THEN 0 ELSE 1 END AS source_complete,
                          c.target_header
                   FROM candidate_cells c
                   LEFT JOIN document_elements e ON e.element_id = c.element_id
                   LEFT JOIN resources r ON r.resource_id = e.resource_id
                   WHERE c.candidate_record_id = ? AND c.value != '' ORDER BY page_number""",
                (candidate_record_id,),
            )]
        grouped: dict[str, dict[str, Any]] = {}
        for item in rows:
            key = item.get("element_id") or f"missing:{item.get('page_number')}"
            if key not in grouped:
                grouped[key] = {
                    "resource_id": item.get("resource_id"),
                    "resource_name": item.get("resource_name") or "",
                    "element_id": item.get("element_id"),
                    "element_type": item.get("element_type") or "paragraph",
                    "page_number": item.get("page_number"),
                    "bbox": json.loads(item.get("bbox_json") or "[]"),
                    "target_headers": [],
                    "source_complete": bool(item.get("source_complete")),
                }
            grouped[key]["target_headers"].append(item.get("target_header") or "")
        return list(grouped.values())

    def _trace_field_source(
        self,
        snapshot: dict[str, Any] | None,
        fallback: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        item = snapshot or fallback
        if not item:
            return None
        bbox_json = (
            item.get("bbox_json") if snapshot else item.get("source_bbox_json")
        ) or "[]"
        return {
            "resource_id": item.get("resource_id"),
            "resource_name": item.get("resource_name") or "",
            "element_id": item.get("element_id"),
            "element_type": item.get("element_type") or "paragraph",
            "page_number": item.get("page_number") if snapshot else item.get("source_page_number"),
            "bbox": json.loads(bbox_json),
            "caption": item.get("source_caption") or "",
            "context": item.get("source_context") or "",
        }

    def export_article(self, project_id: str, article_id: str, format: str = "csv") -> dict[str, Any]:
        """Export standardized records for an article."""
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            headers = self.target_headers(project_id, article_id)
            records = db.fetch_all(
                "SELECT * FROM standardized_records WHERE article_id = ? ORDER BY processed_at",
                (article_id,),
            )
            if not records:
                raise ValueError("No standardized records. Run finalize first.")
            out_dir = project_dir / "output"
            out_dir.mkdir(parents=True, exist_ok=True)

            rows = []
            for rec in [dict(r) for r in records]:
                data = json.loads(rec.get("data") or "{}")
                row = {h["display_header"]: data.get(h["display_header"], "") for h in headers}
                row["Quality_Grade"] = rec["quality_grade"]
                row["Record_ID"] = rec["record_id"]
                rows.append(row)

            if format == "csv":
                import csv
                path = out_dir / f"standardized_{article_id}.csv"
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
            elif format == "xlsx":
                import pandas as pd
                path = out_dir / f"standardized_{article_id}.xlsx"
                pd.DataFrame(rows).to_excel(path, index=False)
            else:
                raise ValueError(f"Unsupported format: {format}")

            return {"path": str(path), "records": len(rows), "format": format}
        finally:
            db.close()

    def create_extraction_batch(
        self,
        project_id: str,
        article_id: str,
        use_llm: bool = True,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        session = self.ensure_session(project_id, article_id)
        headers = self.target_headers(project_id, article_id)
        db = self.pm.get_database(project_id)
        try:
            elements = [item for item in self.list_elements(project_id, article_id) if item["selected"]]
            now = datetime.now().isoformat()
            batch_id = self._next_id(db, "extraction_batches", "batch_id", "BAT", 6)
            db.execute(
                """INSERT INTO extraction_batches
                   (batch_id, project_id, article_id, session_id, header_config_id, status,
                    record_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'running', 0, ?, ?)""",
                (batch_id, project_id, article_id, session["session_id"], session.get("header_config_id"), now, now),
            )
            db.commit()
            if progress:
                progress("正在按目标表头构建样品记录", 0.05, {"elements": len(elements)})
            extracted: list[dict[str, Any]] = []
            for index, element in enumerate(elements):
                records = self._local_records(element, headers)
                if use_llm and (not records or element["element_type"] in {"paragraph", "figure"}):
                    try:
                        records.extend(self._llm_records(db, project_id, article_id, element, headers))
                    except Exception:
                        if element["element_type"] == "figure":
                            db.execute("UPDATE document_elements SET status = 'waiting_for_vision_model' WHERE element_id = ?", (element["element_id"],))
                extracted.extend({"element": element, "record": record} for record in records)
                if progress:
                    progress(
                        f"已处理 {index + 1}/{len(elements)} 个资源",
                        0.1 + 0.75 * ((index + 1) / max(1, len(elements))),
                        {"element_id": element["element_id"], "records": len(records)},
                    )
            for item in extracted:
                self._merge_record(db, batch_id, article_id, item["element"], item["record"], headers)
            count_row = db.fetch_one("SELECT COUNT(*) AS n FROM candidate_records WHERE batch_id = ?", (batch_id,))
            count = int(count_row["n"] or 0)
            db.execute(
                "UPDATE extraction_batches SET status = 'completed', record_count = ?, updated_at = ? WHERE batch_id = ?",
                (count, datetime.now().isoformat(), batch_id),
            )
            db.execute(
                "UPDATE workbench_sessions SET active_batch_id = ?, current_step = 'extraction', updated_at = ? WHERE session_id = ?",
                (batch_id, datetime.now().isoformat(), session["session_id"]),
            )
            db.commit()
            if progress:
                progress("样品级候选表已生成", 1.0, {"batch_id": batch_id, "records": count})
            return {"batch_id": batch_id, "record_count": count, "header_count": len(headers)}
        except Exception:
            try:
                db.execute("UPDATE extraction_batches SET status = 'failed', updated_at = ? WHERE batch_id = ?", (datetime.now().isoformat(), batch_id))
                db.commit()
            except Exception:
                pass
            raise
        finally:
            db.close()

    def batch_records(self, project_id: str, batch_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            batch = db.fetch_one("SELECT * FROM extraction_batches WHERE project_id = ? AND batch_id = ?", (project_id, batch_id))
            if not batch:
                raise ValueError("Extraction batch not found")
            headers = self.target_headers(project_id, batch["article_id"])
            records = []
            for row in db.fetch_all("SELECT * FROM candidate_records WHERE batch_id = ? ORDER BY row_index", (batch_id,)):
                record = dict(row)
                data = {header["display_header"]: "" for header in headers}
                cells: dict[str, Any] = {}
                for cell_row in db.fetch_all("SELECT * FROM candidate_cells WHERE candidate_record_id = ?", (record["candidate_record_id"],)):
                    cell = dict(cell_row)
                    cell["bbox"] = json.loads(cell.pop("bbox_json") or "[]")
                    cell["alternatives"] = json.loads(cell.pop("alternatives_json") or "[]")
                    data[cell["target_header"]] = cell["value"]
                    cells[cell["target_header"]] = cell
                records.append({**record, "data": data, "cells": cells})
            return {"batch": dict(batch), "headers": headers, "records": records}
        finally:
            db.close()

    def update_cell(self, project_id: str, cell_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {"value", "target_header", "target_field", "target_unit", "mapping_status", "review_status", "risk_level"}
        values = {key: value for key, value in updates.items() if key in allowed}
        if not values:
            raise ValueError("No editable cell fields supplied")
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT c.* FROM candidate_cells c JOIN candidate_records r ON r.candidate_record_id = c.candidate_record_id
                   JOIN extraction_batches b ON b.batch_id = r.batch_id WHERE b.project_id = ? AND c.cell_id = ?""",
                (project_id, cell_id),
            )
            if not row:
                raise ValueError("Candidate cell not found")
            values["updated_at"] = datetime.now().isoformat()
            assignments = ", ".join(f"{key} = ?" for key in values)
            db.execute(f"UPDATE candidate_cells SET {assignments} WHERE cell_id = ?", tuple(values.values()) + (cell_id,))
            db.commit()
            return dict(db.fetch_one("SELECT * FROM candidate_cells WHERE cell_id = ?", (cell_id,)))
        finally:
            db.close()

    def merge_records(self, project_id: str, record_ids: list[str]) -> dict[str, Any]:
        if len(record_ids) < 2:
            raise ValueError("At least two records are required")
        db = self.pm.get_database(project_id)
        try:
            target_id = record_ids[0]
            target = db.fetch_one("SELECT * FROM candidate_records WHERE candidate_record_id = ?", (target_id,))
            if not target:
                raise ValueError("Target record not found")
            for source_id in record_ids[1:]:
                for source_cell in db.fetch_all("SELECT * FROM candidate_cells WHERE candidate_record_id = ?", (source_id,)):
                    existing = db.fetch_one(
                        "SELECT * FROM candidate_cells WHERE candidate_record_id = ? AND target_header = ?",
                        (target_id, source_cell["target_header"]),
                    )
                    if not existing:
                        db.execute("UPDATE candidate_cells SET candidate_record_id = ? WHERE cell_id = ?", (target_id, source_cell["cell_id"]))
                    elif existing["value"] != source_cell["value"]:
                        alternatives = json.loads(existing["alternatives_json"] or "[]")
                        alternatives.append({"value": source_cell["value"], "cell_id": source_cell["cell_id"], "element_id": source_cell["element_id"]})
                        db.execute(
                            "UPDATE candidate_cells SET alternatives_json = ?, mapping_status = 'conflict', review_status = 'pending', risk_level = 'high', updated_at = ? WHERE cell_id = ?",
                            (json.dumps(alternatives, ensure_ascii=False), datetime.now().isoformat(), existing["cell_id"]),
                        )
                db.execute("DELETE FROM candidate_cells WHERE candidate_record_id = ?", (source_id,))
                db.execute("DELETE FROM candidate_records WHERE candidate_record_id = ?", (source_id,))
            db.execute("UPDATE candidate_records SET merge_status = 'user_merged', updated_at = ? WHERE candidate_record_id = ?", (datetime.now().isoformat(), target_id))
            db.commit()
            return {"candidate_record_id": target_id, "merged": len(record_ids)}
        finally:
            db.close()

    def _local_records(self, element: dict[str, Any], headers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if element["element_type"] != "table":
            return []
        raw = element.get("raw_table") or {}
        if raw.get("rows"):
            return [{"sample_id": str(row.get("SampleID") or row.get("Sample ID") or ""), "values": row, "_source_method": "local_table"} for row in raw["rows"]]
        source_headers = raw.get("headers") or []
        body = raw.get("body_text") or element.get("text_content") or ""
        sample_pattern = re.compile(r"\b([A-Za-z]{1,8}\d{0,3}[-_][A-Za-z0-9-]{1,12})\b")
        matches = list(sample_pattern.finditer(body))
        if not matches:
            return []
        mapped_headers = self._map_source_headers(source_headers, headers)
        group_labels = {"stage", "formation", "samples", "sample", "sampleid", "sample id", "major", "element", "elements", "trace", "wc", "well"}
        value_headers = [item for item in mapped_headers if item[0].lower() not in group_labels]
        records = []
        for index, match in enumerate(matches):
            chunk = body[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(body)]
            numbers = re.findall(r"(?<![A-Za-z])[-−]?\d+(?:\.\d+)?", chunk)
            values: dict[str, Any] = {}
            for position, (_source, target) in enumerate(value_headers):
                if position >= len(numbers):
                    break
                values[target] = numbers[position].replace("−", "-")
            values[self._sample_header(headers)] = match.group(1)
            records.append({"sample_id": match.group(1), "values": values, "_source_method": "local_table"})
        return records

    def _llm_records(self, db, project_id: str, article_id: str, element: dict[str, Any], headers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        config = load_config()
        task_name = "figure_extraction" if element["element_type"] == "figure" else "document_record_extraction"
        task_config = config.get_task_model(task_name)
        if element["element_type"] == "figure":
            if not task_config:
                return []
            provider = config.get_provider(task_config.provider)
            model_config = next((model for model in provider.models if model.name == task_config.model), None) if provider else None
            if not model_config or not model_config.supports_vision or not element.get("preview_path"):
                return []
        compact_headers = [{
            "header": h["display_header"], "field": h["canonical_field"],
            "unit": h["target_unit"], "description": h["description"],
        } for h in headers]
        payload: Any = json.dumps({
            "instruction": "Return one object per sample. Use the exact target header strings as keys. Missing fields must be omitted. Never invent values.",
            "target_headers": compact_headers,
            "source_type": element["element_type"],
            "source_text": element.get("context_text") or element.get("text_content") or "",
            "return_schema": {"records": [{"sample_id": "string or empty", "values": {"exact target header": "value"}}]},
        }, ensure_ascii=False)
        user_content: Any = payload
        if element["element_type"] == "figure":
            path = Path(element["preview_path"])
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode()
            provider_config = config.get_provider(task_config.provider) if task_config else None
            if provider_config and provider_config.api_format == "anthropic":
                user_content = [
                    {"type": "text", "text": payload},
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": encoded}},
                ]
            else:
                user_content = [
                    {"type": "text", "text": payload},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                ]
        response = LLMClient(config, db=db).chat(
            [{"role": "system", "content": "You are a conservative geochemical sample-table extractor."}, {"role": "user", "content": user_content}],
            task_name=task_name, project_id=project_id, article_id=article_id,
            agent_name="web_workbench", skill_name=task_name, temperature_override=0.0,
            max_tokens_override=5000, use_cache=True,
        )
        match = re.search(r"\{[\s\S]*\}", response.content or "")
        parsed = json.loads(match.group(0) if match else response.content)
        records = parsed.get("records", []) if isinstance(parsed, dict) else parsed
        return [
            {**record, "_source_method": "llm"}
            for record in records
            if isinstance(record, dict) and isinstance(record.get("values"), dict)
        ]

    def _merge_record(self, db, batch_id: str, article_id: str, element: dict[str, Any], record: dict[str, Any], headers: list[dict[str, Any]]) -> None:
        sample_id = str(record.get("sample_id") or record.get("values", {}).get(self._sample_header(headers)) or "").strip()
        sample_key = self._normalize_sample(sample_id) if sample_id else f"unmatched:{element['element_id']}:{self._next_row_index(db, batch_id)}"
        existing = db.fetch_one("SELECT * FROM candidate_records WHERE batch_id = ? AND sample_key = ?", (batch_id, sample_key))
        now = datetime.now().isoformat()
        if existing:
            record_id = existing["candidate_record_id"]
            db.execute("UPDATE candidate_records SET merge_status = 'auto_merged', updated_at = ? WHERE candidate_record_id = ?", (now, record_id))
        else:
            record_id = self._next_id(db, "candidate_records", "candidate_record_id", "CREC", 7)
            db.execute(
                """INSERT INTO candidate_records
                   (candidate_record_id, batch_id, article_id, sample_key, sample_id, row_index,
                    merge_status, quality_grade, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'D', ?, ?)""",
                (record_id, batch_id, article_id, sample_key, sample_id, self._next_row_index(db, batch_id), "matched" if sample_id else "unmatched", now, now),
            )
        header_lookup = {h["display_header"]: h for h in headers}
        canonical_lookup = {self._norm(h["canonical_field"]): h for h in headers}
        for raw_header, raw_value in record.get("values", {}).items():
            target = header_lookup.get(raw_header) or canonical_lookup.get(self._norm(raw_header))
            if not target or raw_value in (None, ""):
                continue
            existing_cell = db.fetch_one(
                "SELECT * FROM candidate_cells WHERE candidate_record_id = ? AND target_header = ?",
                (record_id, target["display_header"]),
            )
            if existing_cell:
                if str(existing_cell["value"]) != str(raw_value):
                    alternatives = json.loads(existing_cell["alternatives_json"] or "[]")
                    alternatives.append({"value": str(raw_value), "element_id": element["element_id"], "page_number": element.get("page_number")})
                    db.execute(
                        "UPDATE candidate_cells SET alternatives_json = ?, mapping_status = 'conflict', review_status = 'pending', risk_level = 'high', updated_at = ? WHERE cell_id = ?",
                        (json.dumps(alternatives, ensure_ascii=False), now, existing_cell["cell_id"]),
                    )
                continue
            cell_id = self._next_id(db, "candidate_cells", "cell_id", "CELL", 8)
            confidence = float(element.get("relevance_score") or 0.6)
            deterministic_match = record.get("_source_method") == "local_table" and confidence >= 0.85
            db.execute(
                """INSERT INTO candidate_cells
                   (cell_id, candidate_record_id, header_id, target_header, target_field, target_unit,
                    value, original_value, original_field, original_unit, confidence, risk_level,
                    mapping_status, element_id, page_number, bbox_json, alternatives_json,
                    review_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)""",
                (
                    cell_id, record_id, target["header_id"], target["display_header"], target["canonical_field"], target["target_unit"],
                    str(raw_value), str(raw_value), str(raw_header), target["target_unit"], confidence,
                    "low" if confidence >= 0.85 else "medium", "confirmed" if deterministic_match else "suggested",
                    element["element_id"], element.get("page_number"), json.dumps(element.get("bbox") or []),
                    "confirmed" if deterministic_match else "pending", now, now,
                ),
            )
            # Auto-apply existing rules for non-deterministic cells
            if not deterministic_match:
                new_cell = db.fetch_one("SELECT * FROM candidate_cells WHERE cell_id = ?", (cell_id,))
                if new_cell:
                    self._auto_apply_rules(db, project_id, dict(new_cell))
        db.commit()

    def _table_regions(self, blocks: list[tuple], page_rect) -> list[dict[str, Any]]:
        results = []
        caption_indices = [i for i, block in enumerate(blocks) if re.match(r"^\s*(TABLE|Table)\s+\w+", " ".join(str(block[4]).split()))]
        for caption_index in caption_indices:
            caption_block = blocks[caption_index]
            caption = " ".join(str(caption_block[4]).split())
            candidates = [caption_block]
            dense_seen = False
            for block in sorted(blocks, key=lambda b: (b[1], b[0])):
                if block is caption_block or block[1] < caption_block[1]:
                    continue
                table_limit = page_rect.height * (0.76 if caption_block[1] < page_rect.height * 0.22 else 0.9)
                if block[1] > table_limit:
                    break
                text = " ".join(str(block[4]).split())
                if re.match(r"^\s*(TABLE|Table)\s+\w+", text) and block is not caption_block:
                    break
                numeric_ratio = len(re.findall(r"[-−]?\d+(?:\.\d+)?", text)) / max(1, len(text.split()))
                is_header = block[1] - caption_block[3] < 45
                if numeric_ratio >= 0.16:
                    dense_seen = True
                if dense_seen and numeric_ratio < 0.05 and len(text) > 120:
                    break
                if is_header or numeric_ratio >= 0.10 or dense_seen:
                    candidates.append(block)
            if len(candidates) < 3 or not dense_seen:
                continue
            bbox = (
                min(float(b[0]) for b in candidates), min(float(b[1]) for b in candidates),
                max(float(b[2]) for b in candidates), max(float(b[3]) for b in candidates),
            )
            header_blocks = [
                b for b in candidates[1:]
                if len(re.findall(r"(?<![A-Za-z])[-−]?\d+(?:\.\d+)?(?![A-Za-z])", str(b[4]))) < 3
            ]
            body_blocks = [b for b in candidates[1:] if b not in header_blocks]
            header_text = " ".join(" ".join(str(b[4]).split()) for b in header_blocks)
            results.append({
                "bbox": bbox,
                "caption": caption,
                "text": " ".join(" ".join(str(b[4]).split()) for b in candidates),
                "headers": self._source_header_tokens(header_text),
                "body_text": " ".join(" ".join(str(b[4]).split()) for b in body_blocks),
            })
        return results

    def _source_header_tokens(self, text: str) -> list[str]:
        units = {"stage", "formation", "samples", "sample", "depth", "toc", "tn", "ts", "cia", "ree_total", "lree", "hree"}
        tokens = re.findall(r"δ?\d{0,2}[A-Z][A-Za-z0-9]*(?:/[A-Z][A-Za-z0-9]*)?|[A-Z][a-z]?\d*O?\d*|[A-Za-z_]+", text)
        result = []
        for token in tokens:
            if token.lower() in units or re.match(r"^(?:δ?\d{0,2}[A-Z]|[A-Z][a-z]?\d*O?\d*)", token):
                if token not in result:
                    result.append(token)
        return result

    def _map_source_headers(self, source_headers: list[str], targets: list[dict[str, Any]]) -> list[tuple[str, str]]:
        mapped = []
        for source in source_headers:
            norm = self._norm(source)
            target = next((h for h in targets if norm in {self._norm(h["canonical_field"]), self._norm(h["display_header"])}), None)
            mapped.append((source, target["display_header"] if target else source))
        return mapped

    def _sample_header(self, headers: list[dict[str, Any]]) -> str:
        for header in headers:
            if self._norm(header["canonical_field"]) in {"sampleid", "sample", "samplename"}:
                return header["display_header"]
        return headers[0]["display_header"] if headers else "SampleID"

    def _matched_headers(self, text: str, targets: list[dict[str, Any]]) -> list[str]:
        lower = text.lower()
        normalized = self._norm(text)
        matched = []
        for target in targets:
            candidates = [target["display_header"], target["canonical_field"]]
            hit = False
            for candidate in candidates:
                token = self._norm(candidate)
                literal = re.sub(r"\s+(?:ppm|ppb|wt\s*%|%|‰|mg/kg|ug/g|m)$", "", str(candidate), flags=re.I).strip()
                short_symbol = bool(re.fullmatch(r"[A-Z][a-z]?", literal))
                if short_symbol:
                    # A bare short symbol is too ambiguous (for example sentence-initial
                    # "In this study"). Require nearby measurement semantics as well.
                    symbol_match = re.search(
                        rf"(?<![A-Za-z0-9]){re.escape(literal)}(?![A-Za-z0-9])",
                        text,
                    )
                    following = text[symbol_match.end():].lstrip().split(maxsplit=1)[0].lower() if symbol_match and text[symbol_match.end():].strip() else ""
                    prose_followers = {
                        "this", "the", "our", "addition", "contrast", "order", "general", "shown",
                        "detail", "conclusion", "summary", "particular", "turn", "response", "cases", "terms",
                    }
                    measurement_context = bool(re.search(
                        r"\b(?:ppm|ppb|concentrations?|contents?|abundances?)\b|wt\s*%|%|‰",
                        text,
                        flags=re.I,
                    ))
                    boundary_hit = bool(symbol_match and following not in prose_followers and measurement_context)
                else:
                    boundary_hit = bool(literal and re.search(
                        rf"(?<![A-Za-z0-9]){re.escape(literal)}(?![A-Za-z0-9])",
                        text,
                        flags=re.I,
                    ))
                if len(token) >= 2 and (
                    boundary_hit
                    or (len(token) >= 6 and token in normalized)
                    or (len(str(candidate).strip()) >= 6 and str(candidate).lower() in lower)
                ):
                    hit = True
                    break
            if hit:
                matched.append(target["display_header"])
        return matched[:40]

    def _relevance(self, matched: list[str], text: str) -> float:
        keyword = bool(re.search(r"\b(ppm|wt\s*%|geochem\w*|isotope\w*|concentration\w*|sample\w*)\b", text, re.I))
        return min(0.98, 0.35 + min(0.5, len(matched) * 0.08) + (0.12 if keyword else 0.0))

    def _paragraph_context(self, blocks: list[tuple], selected: tuple) -> str:
        text = " ".join(str(selected[4]).split())
        candidates = [text]
        for block in blocks:
            if block is selected:
                continue
            same_column = abs(float(block[0]) - float(selected[0])) < 35
            close = abs(float(block[1]) - float(selected[3])) < 60 or abs(float(selected[1]) - float(block[3])) < 60
            if same_column and close:
                candidates.append(" ".join(str(block[4]).split()))
        return " ".join(candidates)[:5000]

    def _nearby_caption(self, blocks: list[tuple], bbox: tuple[float, float, float, float], kind: str) -> str:
        candidates = []
        for block in blocks:
            text = " ".join(str(block[4]).split())
            if kind == "figure" and not re.match(r"^(FIGURE|Figure|Fig\.)\s*\d+", text):
                continue
            distance = min(abs(float(block[1]) - bbox[3]), abs(float(block[3]) - bbox[1]))
            if distance < 90:
                candidates.append((distance, text))
        return min(candidates, default=(0, ""))[1]

    def _upsert_element(
        self, db, project_id: str, article_id: str, resource_id: str, element_type: str,
        page_number: int, bbox: list[float], text: str, context: str, caption: str,
        preview_path: str, raw_table: dict[str, Any], matched: list[str], relevance: float,
        parser_version: str | None = None, force_new: bool = False,
    ) -> str:
        digest = hashlib.sha256(f"{resource_id}|{element_type}|{page_number}|{bbox}|{text[:600]}".encode()).hexdigest()[:20]
        existing = None if force_new else db.fetch_one(
            "SELECT element_id FROM document_elements WHERE resource_id = ? AND content_hash = ?",
            (resource_id, digest),
        )
        now = datetime.now().isoformat()
        if existing:
            db.execute(
                """UPDATE document_elements SET element_type = ?, page_number = ?, bbox_json = ?,
                   text_content = ?, context_text = ?, caption = ?, preview_path = ?,
                   raw_table_json = ?, matched_headers_json = ?, relevance_score = ?,
                   parser_version = ?, status = 'candidate', updated_at = ?
                   WHERE element_id = ?""",
                (
                    element_type, page_number, json.dumps(bbox), text[:12000], context[:20000],
                    caption[:3000], preview_path, json.dumps(raw_table, ensure_ascii=False),
                    json.dumps(matched, ensure_ascii=False), relevance,
                    parser_version or self.PARSER_VERSION, now, existing["element_id"],
                ),
            )
            return existing["element_id"]
        element_id = self._next_id(db, "document_elements", "element_id", "ELM", 7)
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                bbox_json, text_content, context_text, caption, preview_path, raw_table_json,
                matched_headers_json, relevance_score, content_hash, parser_version, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)""",
            (
                element_id, project_id, article_id, resource_id, element_type, page_number,
                json.dumps(bbox), text[:12000], context[:20000], caption[:3000], preview_path,
                json.dumps(raw_table, ensure_ascii=False), json.dumps(matched, ensure_ascii=False),
                relevance, digest, parser_version or self.PARSER_VERSION, now, now,
            ),
        )
        return element_id

    def _render_region(self, pdf_path: Path, page_index: int, bbox: tuple[float, float, float, float], bucket: str) -> str:
        try:
            import fitz
            out_dir = pdf_path.parent / "assets" / bucket
            out_dir.mkdir(parents=True, exist_ok=True)
            key = hashlib.md5(f"{pdf_path}:{page_index}:{bbox}".encode()).hexdigest()[:10]
            out_path = out_dir / f"page_{page_index + 1}_{key}.png"
            if not out_path.exists():
                with fitz.open(str(pdf_path)) as doc:
                    page = doc[page_index]
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(*bbox), alpha=False)
                    pix.save(str(out_path))
            return str(out_path)
        except Exception:
            return ""

    def _decode_element(self, row: dict[str, Any]) -> dict[str, Any]:
        row["bbox"] = json.loads(row.pop("bbox_json") or "[]")
        row["raw_table"] = json.loads(row.pop("raw_table_json") or "{}")
        row["matched_headers"] = json.loads(row.pop("matched_headers_json") or "[]")
        row["selected"] = bool(row.get("selected"))
        return row

    def _normalize_bbox(self, bbox: tuple[float, float, float, float], rect) -> list[float]:
        return self._clamp_bbox([
            (bbox[0] - rect.x0) / rect.width, (bbox[1] - rect.y0) / rect.height,
            (bbox[2] - rect.x0) / rect.width, (bbox[3] - rect.y0) / rect.height,
        ])

    def _clamp_bbox(self, bbox: list[float]) -> list[float]:
        values = [max(0.0, min(1.0, float(value))) for value in bbox]
        if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
            raise ValueError("bbox must be [x0, y0, x1, y1] in normalized page coordinates")
        return values

    def _iou(self, a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
        x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
        if x1 <= x0 or y1 <= y0:
            return 0.0
        intersection = (x1 - x0) * (y1 - y0)
        return intersection / max(1.0, (a[2] - a[0]) * (a[3] - a[1]))

    def _normalize_sample(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    def _norm(self, value: str) -> str:
        return re.sub(r"[^a-z0-9δ]+", "", str(value).lower())

    def _next_row_index(self, db, batch_id: str) -> int:
        row = db.fetch_one("SELECT MAX(row_index) AS n FROM candidate_records WHERE batch_id = ?", (batch_id,))
        return int(row["n"] if row and row["n"] is not None else -1) + 1

    def _next_id(self, db, table: str, column: str, prefix: str, width: int) -> str:
        row = db.fetch_one(f"SELECT {column} FROM {table} WHERE {column} LIKE ? ORDER BY {column} DESC LIMIT 1", (f"{prefix}_%",))
        current = 0
        if row:
            try:
                current = int(str(row[column]).rsplit("_", 1)[1])
            except (ValueError, IndexError):
                current = 0
        return f"{prefix}_{current + 1:0{width}d}"
