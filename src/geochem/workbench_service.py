"""Article workbench domain services shared by the Web UI and CLI."""

from __future__ import annotations

import base64
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from .core.config import load_config
from .core.project import ProjectManager
from .core.runtime import RuntimeProfile, load_runtime_settings
from .curation.resource_scoring import ResourceScoringEngine
from .curation.target_headers import classify_field
from .providers.llm_client import LLMClient


ProgressCallback = Callable[[str, float, dict[str, Any]], None]


class WorkbenchService:
    """Durable, schema-driven article resource and candidate-record workflow."""

    PARSER_VERSION = "layout-v2"

    def __init__(self, project_manager: ProjectManager | None = None):
        self.pm = project_manager or ProjectManager()
        self.scoring = ResourceScoringEngine()

    def _table_header_mapping_prompt(self, project_id: str) -> str:
        """Load the editable mapping task brief, with a workspace override."""
        _project, project_dir = self.pm.load_project(project_id)
        project_prompt = project_dir / "memory" / "table_header_mapping.md"
        default_prompt = Path(__file__).resolve().parents[2] / "config" / "prompts" / "table_header_mapping.md"
        for path in (project_prompt, default_prompt):
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8").strip()
                    if content:
                        return content
                except OSError:
                    continue
        return (
            "Map source table headers conservatively to the supplied target schema. "
            "Return valid JSON only and never invent targets or values."
        )

    def _parse_table_mapping_response(self, content: str) -> tuple[list[dict[str, Any]], str]:
        """Accept only a complete final JSON mapping response."""
        text = (content or "").strip()
        if not text:
            return [], "no_final_output"
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        candidate = fenced.group(1).strip() if fenced else text
        try:
            parsed = json.loads(candidate)
            mappings = parsed.get("mappings", []) if isinstance(parsed, dict) else parsed
            if isinstance(mappings, list):
                return [item for item in mappings if isinstance(item, dict)], "success"
            return [], "json_invalid"
        except json.JSONDecodeError:
            pass

        start_object = candidate.find("{")
        start_array = candidate.find("[")
        starts = [index for index in (start_object, start_array) if index >= 0]
        end_object = candidate.rfind("}")
        end_array = candidate.rfind("]")
        if starts and max(end_object, end_array) >= min(starts):
            try:
                parsed = json.loads(candidate[min(starts):max(end_object, end_array) + 1])
                mappings = parsed.get("mappings", []) if isinstance(parsed, dict) else parsed
                if isinstance(mappings, list):
                    return [item for item in mappings if isinstance(item, dict)], "success"
            except json.JSONDecodeError:
                pass
        return [], "json_invalid"

    def _llm_mapping_is_safe(self, source: str, target: dict[str, Any], source_context: dict[str, Any]) -> bool:
        """Keep LLM suggestions conservative when an obvious table role disagrees."""
        profile = self._source_header_profile(source)
        source_text = f"{source} {profile['field_token']}".lower()
        target_text = f"{target['display_header']} {target['canonical_field']}".lower()
        if re.search(r"sample\s*id|sampleid", target_text):
            # A serial-number column is not a sample identifier when the table
            # also has a dedicated sample/specimen column. Require explicit terms.
            if not re.search(r"sample|specimen|identifier|sample\s*no", source_text):
                return False
        if re.fullmatch(r"(?:no\.?|number|index|serial)", profile["field_token"].lower()):
            return False
        return True

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

    # Toggle: True = Docling, False = legacy pdfplumber+PyMuPDF
    USE_DOCLING = True

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
        if self.USE_DOCLING:
            try:
                return self._discover_pdf_docling(db, project_id, article_id, resource, pdf_path, targets, progress)
            except Exception as exc:
                if progress:
                    progress(f"Docling 解析失败，回退到 legacy PDF 解析: {exc}", 0.08, {"fallback": "legacy"})
        return self._discover_pdf_legacy(db, project_id, article_id, resource, pdf_path, targets, progress)

    def _discover_pdf_docling(
        self,
        db,
        project_id: str,
        article_id: str,
        resource: dict[str, Any],
        pdf_path: Path,
        targets: list[dict[str, Any]],
        progress: ProgressCallback | None,
    ) -> dict[str, int]:
        """Docling-based PDF discovery."""
        from .extractors.docling_reader import DoclingReader

        db.execute(
            "UPDATE document_elements SET status = 'stale', updated_at = ? "
            "WHERE resource_id = ? AND parser_version != 'manual'",
            (datetime.now().isoformat(), resource["resource_id"]),
        )
        db.commit()
        if progress:
            progress("Docling 正在解析 PDF...", 0.1)

        schema_fields = [t["display_header"] for t in targets]
        reader = DoclingReader()
        blocks = reader.read(pdf_path, schema_fields=schema_fields)

        totals = {"table": 0, "figure": 0, "paragraph": 0}
        for i, block in enumerate(blocks):
            self._upsert_element(
                db, project_id, article_id, resource["resource_id"],
                block.block_type, block.page_number, block.bbox,
                block.text[:12000], block.text[:20000], block.caption[:3000],
                block.preview_path, block.table_data or {}, block.matched_headers, block.relevance_score,
                parser_version="docling-v2",
                page_spans=block.page_spans,
                reading_order=block.reading_order,
                section_path=block.section_path,
                source_backend=block.source_backend,
                merge_reason=block.merge_reason,
                score_reasons=block.score_reasons,
            )
            totals[block.block_type] = totals.get(block.block_type, 0) + 1
            if (i + 1) % 20 == 0:
                db.commit()
                if progress:
                    progress(f"Docling 已处理 {i+1}/{len(blocks)} 个块", 0.1 + 0.8*((i+1)/max(1,len(blocks))))
        db.commit()
        if progress:
            progress(f"Docling 完成: {totals['table']} 表格, {totals['figure']} 图片, {totals['paragraph']} 段落", 1.0, totals)
        return totals

    def _discover_pdf_legacy(
        self,
        db,
        project_id: str,
        article_id: str,
        resource: dict[str, Any],
        pdf_path: Path,
        targets: list[dict[str, Any]],
        progress: ProgressCallback | None,
    ) -> dict[str, int]:
        """Legacy pdfplumber+PyMuPDF discovery."""
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
                    score = self._score_resource("table", table["text"], table["caption"], matched, {"headers": table["headers"], "body_text": table["body_text"]})
                    preview = self._render_region(pdf_path, page_index, table["bbox"], "tables")
                    self._upsert_element(
                        db, project_id, article_id, resource["resource_id"], "table", page_number,
                        self._normalize_bbox(table["bbox"], page_rect), table["text"], table["text"],
                        table["caption"], preview, {"headers": table["headers"], "body_text": table["body_text"]},
                        matched, score.score, score_reasons=score.reasons,
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
                        score = self._score_resource("figure", caption, caption, matched, {})
                        preview = self._render_region(pdf_path, page_index, bbox, "figures")
                        self._upsert_element(
                            db, project_id, article_id, resource["resource_id"], "figure", page_number,
                            self._normalize_bbox(bbox, page_rect), caption or f"Figure image {image_index}",
                            caption, caption, preview, {}, matched,
                            score.score, score_reasons=score.reasons,
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
                    score = self._score_resource("paragraph", context, "", matched, {})
                    self._upsert_element(
                        db, project_id, article_id, resource["resource_id"], "paragraph", page_number,
                        self._normalize_bbox(bbox, page_rect), text, context, "", preview, {}, matched,
                        score.score, score_reasons=score.reasons,
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

    def score_explanations(self, project_id: str, article_id: str) -> dict[str, Any]:
        return {"items": [
            {
                "element_id": item["element_id"],
                "relevance_score": item["relevance_score"],
                "score_reasons": item.get("score_reasons", []),
            }
            for item in self.list_elements(project_id, article_id)
        ]}

    def pre_extraction_rules(self, project_id: str, article_id: str) -> list[dict[str, Any]]:
        return self.rule_memory(project_id, article_id)["extraction"]

    def standardize_tables(
        self,
        project_id: str,
        article_id: str,
        use_llm: bool = False,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Normalize selected PDF table resources into flat headers + data rows.

        The first version is deterministic and intentionally preserves values as-is.
        Vision/LLM assistance can later plug into this method before the final
        validation/writeback step.
        """
        del use_llm  # Reserved for the later multimodal table-structure assistant.
        tables = [
            item for item in self.list_elements(project_id, article_id)
            if item.get("selected") and item.get("element_type") == "table"
        ]
        targets = self.target_headers(project_id, article_id)
        db = self.pm.get_database(project_id)
        updated = 0
        try:
            for index, element in enumerate(tables):
                raw = element.get("raw_table") or {}
                normalized = None
                if self._table_headers_are_malformed_flat(raw):
                    normalized = self._standardize_multilevel_table_from_pdf(db, project_id, element)
                if not normalized:
                    normalized = self._standardize_raw_table(raw)
                if self._needs_rotated_table_fallback(normalized):
                    rotated = self._standardize_rotated_table_from_pdf(db, project_id, element)
                    if rotated:
                        normalized = rotated
                if not normalized.get("headers") or not normalized.get("rows"):
                    if progress:
                        progress(
                            f"表格 {element['element_id']} 暂无可标准化结构",
                            0.1 + 0.8 * ((index + 1) / max(1, len(tables))),
                            {"element_id": element["element_id"], "status": "skipped"},
                        )
                    continue
                body_text = normalized.get("body_text") or self._table_body_text(
                    normalized.get("headers") or [],
                    normalized.get("rows") or [],
                )
                text = "\n".join(
                    part for part in [element.get("caption") or "", body_text] if part
                )[:12000]
                matched = self._matched_headers(text, targets)
                db.execute(
                    """UPDATE document_elements
                       SET raw_table_json = ?, text_content = ?, matched_headers_json = ?,
                           parser_version = ?, status = 'table_standardized', updated_at = ?
                       WHERE element_id = ?""",
                    (
                        json.dumps(normalized, ensure_ascii=False),
                        text,
                        json.dumps(matched, ensure_ascii=False),
                        f"{self.PARSER_VERSION}:table-standardized",
                        datetime.now().isoformat(),
                        element["element_id"],
                    ),
                )
                # Release the write transaction before task progress writes through
                # its own SQLite connection. Otherwise SQLite can report
                # "database is locked" even inside this single background task.
                db.commit()
                updated += 1
                if progress:
                    progress(
                        f"已标准化表格 {index + 1}/{len(tables)}",
                        0.1 + 0.8 * ((index + 1) / max(1, len(tables))),
                        {
                            "element_id": element["element_id"],
                            "headers": len(normalized.get("headers") or []),
                            "rows": len(normalized.get("rows") or []),
                        },
                    )
            db.commit()
            if progress:
                progress("表格资源标准化完成", 1.0, {"updated": updated, "tables": len(tables)})
            return {"article_id": article_id, "updated": updated, "tables": len(tables)}
        finally:
            db.close()

    def _standardize_raw_table(self, raw: dict[str, Any]) -> dict[str, Any]:
        source_headers, source_rows, source_name = self._standardization_source_table(raw)
        headers = [self._clean_table_cell(value) for value in source_headers]
        headers = self._repair_preflattened_header_gaps(headers)
        rows = [
            [self._clean_table_cell(value) for value in row]
            for row in source_rows
            if isinstance(row, list)
        ]
        body_text = str(raw.get("body_text") or "")
        if not rows and body_text:
            rows = [
                [self._clean_table_cell(cell) for cell in re.split(r"\t| {2,}|,\s*", line.strip()) if self._clean_table_cell(cell)]
                for line in body_text.splitlines()
                if line.strip()
            ]
        matrix: list[list[str]] = []
        if headers:
            matrix.append(headers)
        matrix.extend(rows)
        matrix = [row for row in matrix if any(cell for cell in row)]
        if not matrix:
            return {**raw, "headers": headers, "rows": rows, "table_standardized": False}
        width = max(len(row) for row in matrix)
        matrix = [row + [""] * (width - len(row)) for row in matrix]
        data_start = self._find_table_data_start(matrix)
        if data_start <= 0:
            data_start = 1 if headers and len(matrix) > 1 else 0
        header_rows = matrix[:data_start] if data_start else [headers or matrix[0]]
        data_rows = matrix[data_start:] if data_start else matrix[1:]
        header_rows = self._remove_empty_columns(header_rows, width)[0]
        flat_headers = self._flatten_header_rows(header_rows, width)
        if not flat_headers:
            flat_headers = [f"Column_{index + 1}" for index in range(width)]
        normalized_rows = []
        for row in data_rows:
            padded = (row + [""] * (len(flat_headers) - len(row)))[:len(flat_headers)]
            if self._row_looks_like_header(padded) and not self._row_looks_like_data(padded):
                continue
            if any(cell for cell in padded):
                normalized_rows.append(padded)
        result = dict(raw)
        result.update({
            "headers": flat_headers,
            "rows": normalized_rows,
            "body_text": self._table_body_text(flat_headers, normalized_rows),
            "source_headers": raw.get("source_headers") or raw.get("original_headers") or headers,
            "source_rows": raw.get("source_rows") or source_rows or rows,
            "original_headers": raw.get("original_headers") or raw.get("source_headers") or headers,
            "header_rows": header_rows,
            "data_start_row": data_start,
            "header_parse_method": "local_standardizer",
            "standardization_source": source_name,
            "user_edited": False,
            "header_confidence": 0.82 if normalized_rows else 0.45,
            "table_standardized": True,
            "standardization_warnings": [] if normalized_rows else ["未能稳定识别数据行"],
        })
        return result

    def _standardization_source_table(self, raw: dict[str, Any]) -> tuple[list[Any], list[Any], str]:
        headers = raw.get("headers") or []
        rows = raw.get("rows") or []
        source_headers = raw.get("source_headers") or []
        source_rows = raw.get("source_rows") or []
        original_headers = raw.get("original_headers") or []
        if source_headers:
            return source_headers, source_rows or rows, "source_headers"
        current_path_count = sum(1 for value in headers if self._looks_like_header_path(self._clean_table_cell(value)))
        original_path_count = sum(1 for value in original_headers if self._looks_like_header_path(self._clean_table_cell(value)))
        if original_path_count and original_path_count > current_path_count:
            return original_headers, rows, "original_headers"
        return headers, rows, "headers"

    def _standardization_source_headers(self, raw: dict[str, Any]) -> list[Any]:
        return self._standardization_source_table(raw)[0]

    def _repair_preflattened_header_gaps(self, headers: list[str]) -> list[str]:
        repaired: list[str] = []
        previous_parts: list[str] = []
        for header in headers:
            value = self._clean_table_cell(header)
            parts = value.split(".")
            if (
                ".." in value
                and len(previous_parts) >= 3
                and parts
                and previous_parts[0] == parts[0]
                and previous_parts[1]
            ):
                tail = [part for part in parts[1:] if part.strip()]
                value = ".".join([parts[0], previous_parts[1], *tail])
                parts = value.split(".")
            repaired.append(value)
            previous_parts = [part.strip() for part in parts]
        return repaired

    def _table_headers_are_malformed_flat(self, raw: dict[str, Any]) -> bool:
        header_sets = [
            raw.get("source_headers") or [],
            raw.get("original_headers") or [],
            raw.get("headers") or [],
        ]
        for headers in header_sets:
            cleaned = [self._clean_table_cell(header) for header in headers if self._clean_table_cell(header)]
            if len(cleaned) < 6:
                continue
            bases: dict[str, int] = {}
            suffixed = 0
            for header in cleaned:
                match = re.match(r"^(.*)_([2-9]\d*)$", header)
                base = match.group(1).strip() if match else header
                if match:
                    suffixed += 1
                norm_base = self._norm(base)
                if norm_base:
                    bases[norm_base] = bases.get(norm_base, 0) + 1
            repeated_bad = any(
                count >= 3 and re.search(r"clay|whole|rock|mineral|element|oxide|trace|major", base, re.I)
                for base, count in bases.items()
            )
            if suffixed >= max(2, int(len(cleaned) * 0.2)) and repeated_bad:
                return True
        return False

    def _standardize_multilevel_table_from_pdf(self, db, project_id: str, element: dict[str, Any]) -> dict[str, Any] | None:
        try:
            import fitz
        except Exception:
            return None
        path = self._element_resource_path(db, project_id, element)
        if not path:
            return None
        page_number = int(element.get("page_number") or 0)
        bbox = element.get("bbox") or []
        if page_number <= 0 or len(bbox) != 4:
            return None
        try:
            with fitz.open(str(path)) as doc:
                if page_number > len(doc):
                    return None
                page = doc[page_number - 1]
                rect = page.rect
                clip = fitz.Rect(
                    float(bbox[0]) * rect.width,
                    float(bbox[1]) * rect.height,
                    float(bbox[2]) * rect.width,
                    float(bbox[3]) * rect.height,
                )
                words = page.get_text("words", clip=clip)
        except Exception:
            return None
        normalized = self._multilevel_table_from_words(words)
        if not normalized:
            return None
        warnings = list(normalized.get("standardization_warnings") or [])
        warnings.append("原始表头已丢失层级，已从 PDF words 重建")
        normalized.update({
            "header_parse_method": "pdf_words_multilevel_reconstruction",
            "standardization_source": "pdf_words_multilevel_header",
            "header_confidence": 0.84,
            "table_standardized": True,
            "user_edited": False,
            "standardization_warnings": warnings,
        })
        return normalized

    def _multilevel_table_from_words(self, words: list[tuple]) -> dict[str, Any] | None:
        parsed = []
        for word in words:
            text = self._clean_table_cell(word[4] if len(word) > 4 else "")
            if not text:
                continue
            try:
                x0, y0, x1, y1 = map(float, word[:4])
            except (TypeError, ValueError):
                continue
            parsed.append({
                "text": text,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
            })
        if len(parsed) < 12:
            return None
        rows = self._cluster_words_by_y(parsed)
        if len(rows) < 3:
            return None
        data_start = next((index for index, row in enumerate(rows) if self._word_row_looks_like_data(row)), -1)
        if data_start <= 0:
            return None
        data_word_rows = rows[data_start:]
        column_centers = self._table_column_centers_from_word_rows(data_word_rows)
        if len(column_centers) < 4:
            return None
        header_word_rows = rows[:data_start]
        header_rows = self._header_matrix_from_word_rows(header_word_rows, column_centers)
        headers = self._flatten_header_rows(header_rows, len(column_centers))
        if not headers or headers.count("Column_1") == len(headers):
            return None
        body_rows = self._data_matrix_from_word_rows(data_word_rows, column_centers)
        body_rows = [row for row in body_rows if any(cell for cell in row)]
        if not body_rows:
            return None
        return {
            "headers": headers,
            "rows": body_rows,
            "source_headers": headers,
            "source_rows": body_rows,
            "source_header_rows": header_rows,
            "original_headers": headers,
            "header_rows": header_rows,
            "data_start_row": len(header_rows),
            "body_text": self._table_body_text(headers, body_rows),
            "standardization_warnings": [],
        }

    def _cluster_words_by_y(self, words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        sorted_words = sorted(words, key=lambda item: (item["cy"], item["cx"]))
        rows: list[list[dict[str, Any]]] = []
        centers: list[float] = []
        for word in sorted_words:
            best_index = -1
            best_distance = 999.0
            for index, center in enumerate(centers):
                distance = abs(word["cy"] - center)
                if distance < best_distance:
                    best_index = index
                    best_distance = distance
            if best_index >= 0 and best_distance <= 4.5:
                rows[best_index].append(word)
                centers[best_index] = sum(item["cy"] for item in rows[best_index]) / len(rows[best_index])
            else:
                rows.append([word])
                centers.append(word["cy"])
        return [sorted(row, key=lambda item: item["cx"]) for _center, row in sorted(zip(centers, rows), key=lambda pair: pair[0])]

    def _word_row_looks_like_data(self, row: list[dict[str, Any]]) -> bool:
        texts = [word["text"] for word in row]
        if len(texts) < 5:
            return False
        sample_like = any(re.match(r"^[A-Z]{1,8}[-–]?[A-Z]?\d+[A-Za-z0-9-]*$", text) for text in texts[:4])
        first_is_index = bool(re.match(r"^\d+$", texts[0] if texts else ""))
        numeric_count = sum(1 for text in texts if re.match(r"^[-−]?\d+(?:\.\d+)?$|^[/\\﹨]$", text))
        return (sample_like and numeric_count >= 3) or (first_is_index and numeric_count >= max(4, int(len(texts) * 0.45)))

    def _table_column_centers_from_word_rows(self, rows: list[list[dict[str, Any]]]) -> list[float]:
        candidates = [row for row in rows[:5] if self._word_row_looks_like_data(row)]
        if not candidates:
            return []
        base = max(candidates, key=len)
        centers = [float(word["cx"]) for word in base]
        return sorted(centers)

    def _header_matrix_from_word_rows(self, header_rows: list[list[dict[str, Any]]], column_centers: list[float]) -> list[list[str]]:
        matrix: list[list[str]] = []
        for row_index, row in enumerate(header_rows):
            clusters = self._header_clusters(row) if row_index == 0 else [
                {
                    "text": word["text"],
                    "x0": word["x0"],
                    "x1": word["x1"],
                    "cx": word["cx"],
                    "word_count": 1,
                }
                for word in row
            ]
            values = [""] * len(column_centers)
            for cluster in clusters:
                text = self._simplify_table_header_atom(cluster["text"])
                if not text:
                    continue
                if row_index == 0 and (cluster.get("word_count", 1) > 1 or self._is_known_parent_header(text)):
                    start, end = self._parent_header_span(cluster, clusters, column_centers)
                    for col_index, center in enumerate(column_centers):
                        if start <= center <= end:
                            values[col_index] = text
                else:
                    index = self._nearest_column_index(float(cluster["cx"]), column_centers)
                    if index is not None:
                        values[index] = text
            matrix.append(values)
        return matrix

    def _header_clusters(self, row: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not row:
            return []
        clusters: list[list[dict[str, Any]]] = [[row[0]]]
        for word in row[1:]:
            previous = clusters[-1][-1]
            gap = float(word["x0"]) - float(previous["x1"])
            if gap < 8.0:
                clusters[-1].append(word)
            else:
                clusters.append([word])
        result = []
        for cluster in clusters:
            result.append({
                "text": " ".join(word["text"] for word in cluster),
                "x0": min(float(word["x0"]) for word in cluster),
                "x1": max(float(word["x1"]) for word in cluster),
                "cx": sum(float(word["cx"]) for word in cluster) / len(cluster),
                "word_count": len(cluster),
            })
        return result

    def _is_known_parent_header(self, text: str) -> bool:
        lower = text.lower()
        return (
            "relative content of clay minerals" in lower
            or "quantitative analysis of the whole rock" in lower
        )

    def _parent_header_span(self, cluster: dict[str, Any], clusters: list[dict[str, Any]], column_centers: list[float]) -> tuple[float, float]:
        index = clusters.index(cluster)
        start = float(cluster["x0"]) - 8
        end = float(cluster["x1"]) + 8
        if index + 1 < len(clusters):
            next_cluster = clusters[index + 1]
            end = max(end, float(next_cluster["x0"]) - 8)
        else:
            end = max(end, max(column_centers) + 8)
        return start, end

    def _nearest_column_index(self, x: float, column_centers: list[float], tolerance: float = 13.0) -> int | None:
        if not column_centers:
            return None
        distances = [(abs(center - x), index) for index, center in enumerate(column_centers)]
        distance, index = min(distances)
        if distance <= tolerance:
            return index
        return None

    def _data_matrix_from_word_rows(self, rows: list[list[dict[str, Any]]], column_centers: list[float]) -> list[list[str]]:
        matrix: list[list[str]] = []
        for row in rows:
            values = [""] * len(column_centers)
            for word in row:
                index = self._nearest_column_index(float(word["cx"]), column_centers)
                if index is None:
                    continue
                values[index] = f"{values[index]} {word['text']}".strip() if values[index] else word["text"]
            matrix.append(values)
        return matrix

    def _needs_rotated_table_fallback(self, table: dict[str, Any]) -> bool:
        headers = [str(header or "") for header in table.get("headers") or []]
        rows = table.get("rows") or []
        text = " ".join(headers + [" ".join(str(cell) for cell in row) for row in rows[:8]])
        has_sample_row = bool(re.search(r"\b[A-Z]{1,8}[-–]?[A-Z]?\d+[A-Za-z0-9-]*\b", text))
        sample_header = any(self._norm(header) in {"sample", "sampleid", "sampleno"} for header in headers)
        numeric_headers = sum(1 for header in headers if re.search(r"^\s*[-−]?\d+(?:\.\d+)?(?:\s+[-−]?\d+(?:\.\d+)?)*\s*$", header))
        weak_headers = not headers or numeric_headers >= max(2, len(headers) // 2)
        return weak_headers and not (sample_header and has_sample_row)

    def _standardize_rotated_table_from_pdf(self, db, project_id: str, element: dict[str, Any]) -> dict[str, Any] | None:
        try:
            import fitz
        except Exception:
            return None
        path = self._element_resource_path(db, project_id, element)
        if not path:
            return None
        page_number = int(element.get("page_number") or 0)
        if page_number <= 0:
            return None
        try:
            with fitz.open(str(path)) as doc:
                if page_number > len(doc):
                    return None
                page = doc[page_number - 1]
                words = page.get_text("words")
        except Exception:
            return None
        rotated = self._rotated_sample_table_from_words(words)
        if not rotated:
            return None
        rotated.update({
            "header_parse_method": "rotated_word_reconstruction",
            "header_confidence": 0.78,
            "table_standardized": True,
            "standardization_warnings": ["检测到横置/旋转表格，已按 PDF words 坐标转置为样品行"],
        })
        return rotated

    def _element_resource_path(self, db, project_id: str, element: dict[str, Any]) -> Path | None:
        resource_id = str(element.get("resource_id") or "")
        if not resource_id:
            return None
        row = db.fetch_one(
            """SELECT local_path FROM resources
               WHERE resource_id = ? AND article_id = ?""",
            (resource_id, element.get("article_id")),
        )
        if not row:
            return None
        _config, project_dir = self.pm.load_project(project_id)
        path = Path(row["local_path"] or "")
        path = path if path.is_absolute() else project_dir / path
        return path if path.exists() else None

    def _rotated_sample_table_from_words(self, words: list[tuple]) -> dict[str, Any] | None:
        parsed = []
        for word in words:
            text = self._clean_table_cell(word[4] if len(word) > 4 else "")
            if not text:
                continue
            x0, y0, x1, y1 = map(float, word[:4])
            parsed.append({
                "text": text,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                "w": max(0.1, x1 - x0), "h": max(0.1, y1 - y0),
            })
        sample_id_re = re.compile(r"^[A-Z]{1,8}[-–]?[A-Z]?\d+[A-Za-z0-9-]*$")
        sample_words = [item for item in parsed if sample_id_re.match(item["text"])]
        if len(sample_words) < 4:
            return None
        sample_headers = []
        for item in parsed:
            if self._norm(item["text"]) != "sample":
                continue
            nearby_samples = [
                sample for sample in sample_words
                if abs(sample["cx"] - item["cx"]) < 14 and 20 < item["cy"] - sample["cy"] < 280
            ]
            if len(nearby_samples) >= 3:
                sample_headers.append(item)
        if not sample_headers:
            return None
        sample_headers = sorted(sample_headers, key=lambda item: item["cx"])
        all_rows: list[list[str]] = []
        final_headers: list[str] = []
        for group_index, sample_header in enumerate(sample_headers):
            next_x = sample_headers[group_index + 1]["cx"] if group_index + 1 < len(sample_headers) else float("inf")
            left = sample_header["cx"] - 12
            right = min(next_x - 12, max((word["cx"] for word in parsed if word["cx"] > sample_header["cx"]), default=sample_header["cx"]) + 18)
            group_samples = sorted(
                [
                    sample for sample in sample_words
                    if abs(sample["cx"] - sample_header["cx"]) < 14 and sample["cy"] < sample_header["cy"] - 6
                ],
                key=lambda item: item["cy"],
                reverse=True,
            )
            if len(group_samples) < 3:
                continue
            row_min_y = min(sample["cy"] for sample in group_samples) - 10
            row_max_y = max(sample["cy"] for sample in group_samples) + 10
            header_candidates = [
                word for word in parsed
                if sample_header["cx"] + 8 < word["cx"] < right
                and abs(word["cy"] - sample_header["cy"]) < 22
                and self._norm(word["text"]) != "sample"
                and re.search(r"[A-Za-z]", word["text"])
            ]
            headers = [self._normalize_rotated_header(word["text"]) for word in sorted(header_candidates, key=lambda item: item["cx"])]
            header_positions = [word["cx"] for word in sorted(header_candidates, key=lambda item: item["cx"])]
            if len(headers) < 3:
                continue
            if not final_headers:
                final_headers = ["Sample"] + headers
            for sample in group_samples:
                row = [sample["text"]]
                for hx in header_positions:
                    candidates = [
                        word for word in parsed
                        if left < word["cx"] < right
                        and row_min_y <= word["cy"] <= row_max_y
                        and abs(word["cy"] - sample["cy"]) < 9
                        and abs(word["cx"] - hx) < 6
                        and word["text"] != sample["text"]
                        and self._norm(word["text"]) != "sample"
                    ]
                    candidates = sorted(candidates, key=lambda word: (abs(word["cx"] - hx), abs(word["cy"] - sample["cy"])))
                    row.append(candidates[0]["text"] if candidates else "")
                all_rows.append(row)
        if not final_headers or len(all_rows) < 3:
            return None
        # Keep one row per sample and preserve first occurrence.
        seen_samples: set[str] = set()
        deduped_rows = []
        for row in all_rows:
            key = self._normalize_sample(row[0])
            if key in seen_samples:
                continue
            seen_samples.add(key)
            deduped_rows.append(row[:len(final_headers)] + [""] * max(0, len(final_headers) - len(row)))
        if len(deduped_rows) < 3:
            return None
        return {
            "headers": final_headers,
            "rows": deduped_rows,
            "body_text": self._table_body_text(final_headers, deduped_rows),
            "original_headers": [],
            "header_rows": [final_headers],
            "data_start_row": 1,
        }

    def _normalize_rotated_header(self, text: str) -> str:
        value = self._clean_table_cell(text)
        replacements = {
            "Tatal": "Total",
            "Tatal.": "Total",
            "TiO": "TiO2",
            "SiO": "SiO2",
        }
        return replacements.get(value, value)

    def _clean_table_cell(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").replace("\n", " ")).strip()

    def _table_body_text(self, headers: list[str], rows: list[list[Any]]) -> str:
        lines = ["\t".join(str(header or "") for header in headers)]
        lines.extend("\t".join(str(cell or "") for cell in row) for row in rows)
        return "\n".join(line for line in lines if line.strip())

    def _row_looks_like_data(self, row: list[str]) -> bool:
        sample_like = any(re.search(r"\b[A-Z]{1,8}[-–]?[A-Z]?\d+[A-Za-z0-9-]*\b", cell) for cell in row[:4])
        numeric_count = sum(1 for cell in row if re.search(r"^[-−]?\d+(?:\.\d+)?$|^[/\\]$", cell.strip()))
        return sample_like and numeric_count >= 2 or numeric_count >= max(4, int(len(row) * 0.45))

    def _row_looks_like_header(self, row: list[str]) -> bool:
        text_count = sum(1 for cell in row if re.search(r"[A-Za-z%％/()]", cell))
        numeric_count = sum(1 for cell in row if re.search(r"^[-−]?\d+(?:\.\d+)?$", cell.strip()))
        return text_count >= max(2, numeric_count + 1)

    def _find_table_data_start(self, matrix: list[list[str]]) -> int:
        for index, row in enumerate(matrix):
            if self._row_looks_like_data(row):
                return index
        for index, row in enumerate(matrix):
            numeric_count = sum(1 for cell in row if re.search(r"^[-−]?\d+(?:\.\d+)?$|^[/\\]$", cell.strip()))
            if numeric_count >= max(4, int(len(row) * 0.5)):
                return index
        return 1 if len(matrix) > 1 else 0

    def _remove_empty_columns(self, rows: list[list[str]], width: int) -> tuple[list[list[str]], list[int]]:
        keep = [index for index in range(width) if any(index < len(row) and row[index] for row in rows)]
        if not keep:
            keep = list(range(width))
        return [[row[index] if index < len(row) else "" for index in keep] for row in rows], keep

    def _flatten_header_rows(self, header_rows: list[list[str]], width: int) -> list[str]:
        if not header_rows:
            return []
        rows = [row + [""] * (width - len(row)) for row in header_rows]
        carried_rows: list[list[str]] = []
        for row_index, row in enumerate(rows):
            carried: list[str] = []
            last = ""
            for col_index, cell in enumerate(row):
                current = self._simplify_table_header_part(cell)
                if current:
                    last = current
                if row_index < len(rows) - 1 and not current and last and rows[row_index + 1][col_index]:
                    current = last
                carried.append(current)
            carried_rows.append(carried)
        headers: list[str] = []
        seen: dict[str, int] = {}
        for col_index in range(width):
            parts: list[str] = []
            for row in carried_rows:
                part = row[col_index]
                if not part:
                    continue
                if parts and self._norm(parts[-1]) == self._norm(part):
                    continue
                parts.append(part)
            label = self._compose_table_header(parts, col_index)
            norm = self._norm(label)
            seen[norm] = seen.get(norm, 0) + 1
            if seen[norm] > 1:
                label = f"{label}_{seen[norm]}"
            headers.append(label)
        return headers

    def _simplify_table_header_part(self, text: str) -> str:
        value = self._clean_table_cell(text)
        if not value:
            return ""
        if self._looks_like_header_path(value):
            return value
        return self._simplify_table_header_atom(value)

    def _looks_like_header_path(self, text: str) -> bool:
        parts = [part.strip() for part in text.split(".") if part.strip()]
        if len(parts) < 2:
            return False
        if any(re.search(r"[A-Za-z%％/()（）]", part) for part in parts):
            return True
        return False

    def _split_table_header_path(self, text: str) -> list[str]:
        value = self._clean_table_cell(text)
        if not value:
            return []
        if self._looks_like_header_path(value):
            return [
                simplified for simplified in
                (self._simplify_table_header_atom(part) for part in value.split("."))
                if simplified
            ]
        return [self._simplify_table_header_atom(value)]

    def _simplify_table_header_atom(self, text: str) -> str:
        value = self._clean_table_cell(text)
        lower = value.lower()
        if not value:
            return ""
        if "relative content of clay minerals" in lower:
            return "relative content of clay minerals(%)"
        if "quantitative analysis of the whole rock" in lower:
            return "quantitative analysis of the whole rock (%)"
        return value

    def _compose_table_header(self, parts: list[str], index: int) -> str:
        if not parts:
            return f"Column_{index + 1}"
        normalized_parts: list[str] = []
        for part in parts:
            for subpart in self._split_table_header_path(part):
                clean = subpart.strip()
                if not clean:
                    continue
                if clean and not any(self._norm(clean) == self._norm(existing) for existing in normalized_parts):
                    normalized_parts.append(clean)
        if len(normalized_parts) == 1:
            return normalized_parts[0]
        label = " ".join(normalized_parts).strip()
        return label or f"Column_{index + 1}"

    def table_rule_preflight(self, project_id: str, article_id: str) -> dict[str, Any]:
        """Suggest source table header -> target header rules before extraction."""
        headers = self.target_headers(project_id, article_id)
        alias_rules = self._pre_extraction_alias_rules(project_id, article_id, headers)
        alias_rule_ids = self._pre_extraction_alias_rule_ids(project_id, article_id, headers)
        elements = [
            item for item in self.list_elements(project_id, article_id)
            if item.get("selected") and item.get("element_type") == "table"
        ]
        suggestions: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        sample_header = self._sample_header(headers)
        for element in elements:
            raw = element.get("raw_table") or {}
            source_headers = [str(value).strip() for value in raw.get("headers") or [] if str(value).strip()]
            if not source_headers and raw.get("body_text"):
                source_headers = self._source_header_tokens(str(raw.get("body_text") or ""))[:40]
            mapped = self._map_source_headers(source_headers, headers, alias_rules)
            for source, target in mapped:
                key = (element["element_id"], self._norm(source))
                if key in seen:
                    continue
                seen.add(key)
                norm = self._norm(source)
                if norm == "specimen" and target == source:
                    target = sample_header
                matched_by_user_rule = norm in alias_rules
                matched_by_builtin_sample = target == sample_header and norm in {"sample", "sampleno", "samplenumber", "samplename", "sampleid", "specimen"}
                profile = self._source_header_profile(source)
                leaf_norm = self._norm(profile["field_token"])
                leaf_exact_header = next((
                    h for h in headers
                    if leaf_norm and (
                        self._norm(h["display_header"]) == leaf_norm
                        or self._norm(h["canonical_field"]) == leaf_norm
                    )
                ), None)
                exact_header = next((
                    h for h in headers
                    if self._norm(h["display_header"]) == norm or self._norm(h["canonical_field"]) == norm
                ), None)
                # `_map_source_headers` intentionally falls back to the raw
                # source label when it cannot find a target.  Do not use
                # `target != source` as the match signal here: a target display
                # header can legitimately be identical to its raw header
                # (SiO2 -> SiO2).  That used to clear a valid mapping in the UI.
                matched_target = next((h for h in headers if h["display_header"] == target), None)
                exact = bool(exact_header and target == exact_header["display_header"])
                confidence = 0.35
                reason = "未发现明确目标表头，需要人工确认"
                if matched_by_user_rule:
                    confidence = 0.96
                    reason = "命中已确认规则记忆"
                    mapping_source = "memory"
                elif matched_by_builtin_sample:
                    confidence = 0.93
                    reason = "命中内置样品编号别名"
                    mapping_source = "builtin"
                elif exact:
                    confidence = 0.9
                    reason = "原始表头与目标表头/规范字段一致"
                    mapping_source = "exact"
                elif leaf_exact_header and target == leaf_exact_header["display_header"]:
                    confidence = 0.89
                    unit_note = f"；父级检测到单位 {profile['detected_unit']}" if profile["detected_unit"] else ""
                    reason = f"多级表头叶子字段“{profile['field_token']}”精确匹配目标字段{unit_note}"
                    mapping_source = "leaf_exact"
                elif matched_target:
                    confidence = 0.78
                    reason = "基于目标表头名称相似度建议"
                    mapping_source = "similarity"
                else:
                    mapping_source = "unresolved"
                suggestions.append({
                    "element_id": element["element_id"],
                    "element_type": element["element_type"],
                    "table_label": element.get("caption") or element.get("text_content") or element["element_id"],
                    "page_number": element.get("page_number"),
                    "source_header": source,
                    "suggested_target_header": target if matched_target else "",
                    "confidence": round(confidence, 3),
                    "reason": reason,
                    "mapping_source": mapping_source,
                    "existing_rule_id": alias_rule_ids.get(norm, ""),
                })
        return {
            "article_id": article_id,
            "selected_table_count": len(elements),
            "target_header_count": len(headers),
            "items": suggestions,
        }

    def _mapping_config_version(self, config: Any) -> str:
        payload = {
            "field_mapping": config.get_task_model("field_mapping").model_dump() if config.get_task_model("field_mapping") else {},
            "providers": [{"name": p.name, "base_url": p.base_url, "api_format": p.api_format} for p in config.providers],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]

    def _mapping_model_capabilities(self, config: Any) -> dict[str, Any]:
        task = config.get_task_model("field_mapping")
        provider = config.get_provider(task.provider) if task else None
        model = next((item for item in (provider.models if provider else []) if item.name == task.model), None)
        return {
            "provider": task.provider if task else "",
            "model": task.model if task else "",
            "api_format": provider.api_format if provider else "",
            "supports_json_mode": bool(model and model.supports_json_mode),
            "supports_structured_output": bool(model and model.supports_structured_output),
            "supports_reasoning_toggle": bool(model and model.supports_reasoning_toggle),
            "config_version": self._mapping_config_version(config),
        }

    def _mapping_target_candidates(self, source: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Give the model a small, explainable target shortlist for one source header."""
        profile = source.get("field_token") or source.get("source_header") or ""
        source_text = f"{source.get('source_header', '')} {profile} {source.get('group_context', '')}".lower()
        aliases = {
            "total organic carbon": ("toc",), "organic carbon": ("toc",),
            "total nitrogen": ("tn",), "total sulfur": ("ts",),
            "sample no": ("sampleid",), "specimen": ("sampleid",),
        }
        scores: list[tuple[float, dict[str, Any]]] = []
        for target in targets:
            display = str(target["display_header"])
            canonical = str(target["canonical_field"])
            haystack = f"{display} {canonical} {target.get('description', '')}".lower()
            score = SequenceMatcher(None, self._norm(profile), self._norm(canonical)).ratio()
            for term in re.findall(r"[a-z0-9δ]+", source_text):
                if len(term) > 1 and term in haystack:
                    score += 0.18
            for phrase, target_terms in aliases.items():
                if phrase in source_text and any(term in self._norm(canonical) for term in target_terms):
                    score += 0.9
            if source.get("detected_unit") and str(target.get("target_unit") or "").lower() == str(source["detected_unit"]).lower():
                score += 0.08
            scores.append((score, target))
        scores.sort(key=lambda item: (-item[0], item[1]["order"]))
        return [
            {"display_header": item["display_header"], "canonical_field": item["canonical_field"], "unit": item["target_unit"], "description": item["description"]}
            for score, item in scores[:12] if score > 0
        ]

    def _run_mapping_batch(
        self, project_id: str, article_id: str, batch: list[dict[str, Any]], targets: list[dict[str, Any]], config: Any,
    ) -> dict[str, Any]:
        capability = self._mapping_model_capabilities(config)
        candidate_payload = []
        for source in batch:
            candidate_payload.append({
                **source,
                "candidate_targets": self._mapping_target_candidates(source, targets),
            })
        prompt = {
            "instruction": "Map each source header to one exact target display_header from its candidate_targets, or null. Return JSON only. Do not return reasoning or data values.",
            "source_headers": candidate_payload,
            "return_schema": {"mappings": [{"source_header": "exact input", "target_header": "candidate display_header or null", "confidence": 0.0, "reason": "brief"}]},
        }
        provider_kwargs = {"response_format": {"type": "json_object"}} if capability["supports_json_mode"] else None
        db = self.pm.get_database(project_id)
        try:
            response = LLMClient(config, db=db).chat(
                [
                    {"role": "system", "content": f"{self._table_header_mapping_prompt(project_id)}\n\nReturn only final JSON. Do not expose analysis or thinking."},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                task_name="field_mapping", project_id=project_id, article_id=article_id,
                agent_name="web_workbench", skill_name="table_header_mapping_assist",
                temperature_override=0.0, max_tokens_override=1400, use_cache=False,
                provider_kwargs=provider_kwargs,
            )
        finally:
            db.close()
        if not response.final_content:
            return {**capability, "status": "reasoning_only" if response.reasoning_present else "no_final_output", "mappings": [], "source_headers": [item["source_header"] for item in batch]}
        mappings, status = self._parse_table_mapping_response(response.final_content)
        return {**capability, "status": status, "mappings": mappings, "source_headers": [item["source_header"] for item in batch], "actual_provider": response.provider, "actual_model": response.model}

    def model_capabilities(self) -> dict[str, Any]:
        """Expose declared capabilities only; never infer them from a key test."""
        config = load_config()
        providers: list[dict[str, Any]] = []
        for provider in config.providers:
            providers.append({
                "name": provider.name,
                "display_name": provider.display_name or provider.name,
                "api_format": provider.api_format,
                "models": [{
                    "name": model.name,
                    "supports_json_mode": model.supports_json_mode,
                    "supports_structured_output": model.supports_structured_output,
                    "supports_reasoning_toggle": model.supports_reasoning_toggle,
                } for model in provider.models],
            })
        return {
            "field_mapping": self._mapping_model_capabilities(config),
            "providers": providers,
        }

    def header_mapping_benchmark(self, project_id: str, progress: ProgressCallback | None = None) -> dict[str, Any]:
        """Run a small, labelled header-only smoke benchmark for active routing.

        This deliberately contains no article values.  It measures whether the
        configured mapping model produces a final, valid and schema-safe JSON
        answer before users trust it with a paper.
        """
        config = load_config()
        capability = self._mapping_model_capabilities(config)
        cases = [
            ("Sample", "SampleID"), ("Sample No.", "SampleID"),
            ("TOC", "TOC %"), ("total organic carbon", "TOC %"),
            ("relative content of clay minerals(%) K", "K"),
            ("Number", None),
        ]
        targets = [
            {"display_header": "SampleID", "canonical_field": "SampleID", "target_unit": "", "description": "sample identifier", "order": 0},
            {"display_header": "TOC %", "canonical_field": "TOC", "target_unit": "%", "description": "total organic carbon", "order": 1},
            {"display_header": "K", "canonical_field": "K", "target_unit": "%", "description": "potassium", "order": 2},
        ]
        batch = [{"source_header": source, **self._source_header_profile(source), "table_labels": ["mapping benchmark"]} for source, _expected in cases]
        if progress:
            progress("正在运行表头映射结构化输出基准", 0.2, {"provider": capability["provider"], "model": capability["model"]})
        result = self._run_mapping_batch(project_id, "", batch, targets, config)
        expected = {self._norm(source): target for source, target in cases}
        accepted = 0
        correct = 0
        for mapping in result.get("mappings", []):
            source = self._norm(str(mapping.get("source_header") or ""))
            target = str(mapping.get("target_header") or "").strip() or None
            if source not in expected:
                continue
            accepted += 1
            if target == expected[source]:
                correct += 1
        metrics = {
            "final_json_rate": 1.0 if result.get("status") == "success" else 0.0,
            "legal_mapping_rate": accepted / len(cases),
            "accuracy": correct / len(cases),
            "status": result.get("status"),
            "reasoning_present": result.get("status") == "reasoning_only",
        }
        recommended = all((metrics["final_json_rate"] == 1.0, metrics["legal_mapping_rate"] >= 0.8, metrics["accuracy"] >= 0.8))
        db = self.pm.get_database(project_id)
        try:
            benchmark_id = self._next_id(db, "header_mapping_benchmarks", "benchmark_id", "HMB", 6)
            db.execute(
                """INSERT INTO header_mapping_benchmarks
                   (benchmark_id, project_id, provider, model, status, metrics_json, config_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (benchmark_id, project_id, capability["provider"], capability["model"], result.get("status", "unknown"),
                 json.dumps(metrics, ensure_ascii=False), capability["config_version"], datetime.now().isoformat()),
            )
            db.commit()
        finally:
            db.close()
        if progress:
            progress("表头映射基准完成", 1.0, {"recommended": recommended, **metrics})
        return {"benchmark_id": benchmark_id, "provider": capability["provider"], "model": capability["model"], "metrics": metrics, "recommended": recommended, "config_version": capability["config_version"]}

    def assist_table_rule_preflight(self, project_id: str, article_id: str, source_headers: list[str] | None = None) -> dict[str, Any]:
        """Run conservative, short-batch LLM mapping only for unresolved headers."""
        result = self.table_rule_preflight(project_id, article_id)
        requested = {self._norm(value) for value in (source_headers or []) if value}
        unresolved = [
            item for item in result["items"]
            if item.get("mapping_source") in {"unresolved", "similarity"}
            and not item.get("existing_rule_id")
            and (not requested or self._norm(str(item.get("source_header") or "")) in requested)
        ]
        if not unresolved:
            return {**result, "llm_assisted_count": 0, "batches": [], "failed_source_headers": []}
        targets = self.target_headers(project_id, article_id)
        if not targets:
            raise ValueError("当前文章没有绑定目标表头，无法进行 AI 辅助映射")
        source_context: dict[str, dict[str, Any]] = {}
        for item in unresolved:
            key = self._norm(str(item["source_header"]))
            context = source_context.setdefault(key, {"source_header": item["source_header"], **self._source_header_profile(str(item["source_header"])), "table_labels": []})
            label = str(item.get("table_label") or "").strip()
            if label and label not in context["table_labels"]:
                context["table_labels"].append(label[:180])
        config = load_config()
        contexts = list(source_context.values())
        chunks = [contexts[index:index + 10] for index in range(0, len(contexts), 10)]
        batch_results: list[dict[str, Any]] = []
        mappings: list[dict[str, Any]] = []
        failed: list[str] = []
        for chunk in chunks:
            batch = self._run_mapping_batch(project_id, article_id, chunk, targets, config)
            if batch["status"] != "success" and len(chunk) > 1:
                midpoint = max(1, len(chunk) // 2)
                retry_results = [self._run_mapping_batch(project_id, article_id, part, targets, config) for part in (chunk[:midpoint], chunk[midpoint:])]
                batch_results.extend([{**batch, "retry": True}, *retry_results])
                for retry in retry_results:
                    mappings.extend(retry["mappings"])
                    if retry["status"] != "success":
                        failed.extend(retry["source_headers"])
            else:
                batch_results.append(batch)
                mappings.extend(batch["mappings"])
                if batch["status"] != "success":
                    failed.extend(batch["source_headers"])
        target_by_name = {item["display_header"]: item for item in targets}
        target_by_norm = {self._norm(item["display_header"]): item for item in targets}
        target_by_norm.update({self._norm(item["canonical_field"]): item for item in targets})
        mapped_by_source: dict[str, dict[str, Any]] = {}
        rejected: list[dict[str, str]] = []
        for mapping in mappings:
            source_key = self._norm(str(mapping.get("source_header") or ""))
            target = target_by_name.get(str(mapping.get("target_header") or "").strip()) or target_by_norm.get(self._norm(str(mapping.get("target_header") or "")))
            context = source_context.get(source_key)
            if not context or not target:
                rejected.append({"source_header": str(mapping.get("source_header") or ""), "reason": "目标字段不属于当前文章绑定表头"})
                continue
            if not self._llm_mapping_is_safe(context["source_header"], target, context):
                rejected.append({"source_header": context["source_header"], "reason": "不安全的字段语义映射"})
                continue
            mapped_by_source[source_key] = {"target_header": target["display_header"], "confidence": max(0.0, min(float(mapping.get("confidence") or 0.75), 0.95)), "reason": str(mapping.get("reason") or "AI 建议")}
        assisted_count = 0
        for item in result["items"]:
            mapping = mapped_by_source.get(self._norm(str(item.get("source_header") or "")))
            if mapping:
                item.update({"suggested_target_header": mapping["target_header"], "confidence": round(mapping["confidence"], 3), "reason": f"AI 辅助：{mapping['reason']}", "mapping_source": "llm"})
                assisted_count += 1
        capability = self._mapping_model_capabilities(config)
        message = "" if assisted_count else "AI 未产出可验证的最终 JSON；已保留本地预检建议，失败字段可重试或手动选择。"
        return {**result, "llm_assisted_count": assisted_count, "llm_message": message, "batches": batch_results, "failed_source_headers": list(dict.fromkeys(failed)), "rejected_mappings": rejected, "actual_model": capability, "config_version": capability["config_version"]}

    def confirm_table_rule_preflight(self, project_id: str, article_id: str, rules: list[dict[str, Any]], scope: str = "article") -> dict[str, Any]:
        created = []
        for rule in rules:
            if rule.get("ignore"):
                continue
            source = str(rule.get("source_header") or rule.get("source_alias") or "").strip()
            target = str(rule.get("target_header") or rule.get("suggested_target_header") or "").strip()
            if not source or not target:
                continue
            created.append(self.add_pre_extraction_rule(project_id, article_id, {
                "source_alias": source,
                "pattern": source,
                "target_header": target,
                "target_unit": str(rule.get("target_unit") or ""),
                "rule_type": "source_alias",
                "source_type": "table_header",
                "element_id": str(rule.get("element_id") or ""),
                "evidence": str(rule.get("reason") or "表格规则预检确认"),
                "conditions": rule.get("conditions") or {},
                "confidence": float(rule.get("confidence") or 0.9),
                "scope": scope,
                "enabled": True,
            }))
        return {"created": len(created), "rules": created}

    def paragraph_cues(self, project_id: str, article_id: str, sample_ids: list[str] | None = None) -> dict[str, Any]:
        memory = self._extraction_memory(project_id, article_id)
        cues: list[dict[str, Any]] = []
        sample_terms = [term for term in (sample_ids or []) if term]
        for element in self.list_elements(project_id, article_id):
            if element.get("element_type") != "paragraph":
                continue
            text = " ".join([
                str(element.get("section_path") or ""),
                str(element.get("caption") or ""),
                str(element.get("text_content") or ""),
                str(element.get("context_text") or ""),
            ])
            if self._resource_excluded_by_memory(element, memory):
                bucket = "excluded"
                reason = "命中规则记忆中的排除规则"
            else:
                numbers = len(re.findall(r"[-−]?\d+(?:\.\d+)?", text))
                sample_hits = self._extract_sample_ids(text, self._sample_identifier_patterns(memory))
                table_result_hits = [sample for sample in sample_terms if sample and sample in text]
                geo_terms = re.findall(r"\b(?:ppm|ppb|wt\s*%|TOC|TN|TS|REE|CIA|isotope|formation|sample|samples)\b|‰|δ", text, flags=re.I)
                score = float(element.get("relevance_score") or 0)
                if element.get("selected"):
                    bucket = "selected"
                    reason = "已在抽取队列中"
                elif score >= 0.65 or (sample_hits and geo_terms):
                    bucket = "high"
                    reason = "样品编号和地化/单位术语同时出现"
                elif score >= 0.35 or numbers >= 3 or table_result_hits:
                    bucket = "possible_missed"
                    reason = "可能含有补充样品、地层、位置或数值线索"
                else:
                    bucket = "low"
                    reason = "相关性较低"
            cues.append({
                "element_id": element["element_id"],
                "element_type": "paragraph",
                "page_number": element.get("page_number"),
                "page_label": f"p{element.get('page_number') or '?'}",
                "text": element.get("text_content") or element.get("context_text") or "",
                "caption": element.get("caption") or "",
                "matched_headers": element.get("matched_headers") or [],
                "relevance_score": element.get("relevance_score") or 0,
                "selected": bool(element.get("selected")),
                "bucket": bucket,
                "reason": reason,
            })
        order = {"selected": 0, "high": 1, "possible_missed": 2, "low": 3, "excluded": 4}
        cues.sort(key=lambda item: (order.get(item["bucket"], 9), -float(item["relevance_score"] or 0), int(item.get("page_number") or 0)))
        return {"article_id": article_id, "cues": cues}

    def refresh_paragraph_cues(self, project_id: str, article_id: str) -> dict[str, Any]:
        sample_ids: list[str] = []
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT DISTINCT r.sample_id
                   FROM candidate_records r
                   JOIN extraction_batches b ON b.batch_id = r.batch_id
                   WHERE b.project_id = ? AND b.article_id = ? AND b.status = 'completed'
                   ORDER BY b.updated_at DESC LIMIT 200""",
                (project_id, article_id),
            )
            sample_ids = [str(row["sample_id"]) for row in rows if row["sample_id"]]
        finally:
            db.close()
        result = self.paragraph_cues(project_id, article_id, sample_ids)
        result["refreshed_from_samples"] = sample_ids[:50]
        return result

    def add_pre_extraction_rule(self, project_id: str, article_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            now = datetime.now().isoformat()
            rule_id = self._next_id(db, "learned_extraction_rules", "rule_id", "LRN", 6)
            source_alias = str(payload.get("source_alias") or payload.get("pattern") or "").strip()
            target_header = str(payload.get("target_header") or "").strip()
            target_norm = self._norm(target_header)
            target = next((
                h for h in self.target_headers(project_id, article_id)
                if h["display_header"] == target_header
                or h["canonical_field"] == target_header
                or self._norm(h["display_header"]) == target_norm
                or self._norm(h["canonical_field"]) == target_norm
            ), None)
            if target:
                target_header = target["display_header"]
            target_field = str(payload.get("target_field") or (target or {}).get("canonical_field") or target_header or "resource")
            scope = str(payload.get("scope") or "article")
            rule_article_id = None if scope == "project" else article_id
            db.execute(
                """INSERT INTO learned_extraction_rules
                   (rule_id, project_id, article_id, target_field, target_header, target_unit,
                    rule_type, source_type, pattern, evidence, conditions, confidence,
                    risk_level, review_status, scope, enabled, element_id, created_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'low', 'confirmed', ?, ?, ?, ?, 'user')""",
                (
                    rule_id, project_id, rule_article_id, target_field, target_header,
                    str(payload.get("target_unit") or (target or {}).get("target_unit") or ""),
                    str(payload.get("rule_type") or "source_alias"),
                    str(payload.get("source_type") or "table_header"),
                    source_alias,
                    str(payload.get("evidence") or ""),
                    json.dumps(payload.get("conditions") or {}, ensure_ascii=False),
                    float(payload.get("confidence") or 0.9),
                    scope,
                    1 if payload.get("enabled", True) else 0,
                    str(payload.get("element_id") or ""),
                    now,
                ),
            )
            db.commit()
            self._write_extraction_rules_md(project_id)
            return dict(db.fetch_one("SELECT * FROM learned_extraction_rules WHERE rule_id = ?", (rule_id,)))
        finally:
            db.close()

    def rule_memory(self, project_id: str, article_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        db = self.pm.get_database(project_id)
        try:
            mapping = [dict(row) for row in db.fetch_all(
                "SELECT * FROM mapping_rules ORDER BY created_at DESC"
            )]
            params: tuple[Any, ...]
            clause = "project_id = ?"
            params = (project_id,)
            if article_id:
                clause += " AND (article_id = ? OR article_id IS NULL OR article_id = '')"
                params = (project_id, article_id)
            rows = db.fetch_all(
                f"""SELECT * FROM learned_extraction_rules
                    WHERE {clause} AND review_status = 'confirmed'
                    ORDER BY created_at DESC""",
                params,
            )
            extraction = []
            for row in rows:
                item = dict(row)
                item["enabled"] = bool(item.get("enabled", 1))
                extraction.append(item)
            return {"mapping": mapping, "extraction": extraction}
        finally:
            db.close()

    def update_rule_memory(self, project_id: str, rule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            allowed = {"enabled", "scope", "target_header", "target_field", "target_unit", "pattern", "rule_type", "source_type", "evidence", "conditions"}
            sets = []
            values: list[Any] = []
            for key, value in payload.items():
                if key not in allowed:
                    continue
                if key == "enabled":
                    value = 1 if value else 0
                if key == "conditions" and not isinstance(value, str):
                    value = json.dumps(value or {}, ensure_ascii=False)
                sets.append(f"{key} = ?")
                values.append(value)
            if payload.get("scope") == "project":
                sets.append("article_id = NULL")
            if not sets:
                row = db.fetch_one("SELECT * FROM learned_extraction_rules WHERE project_id = ? AND rule_id = ?", (project_id, rule_id))
                if not row:
                    raise ValueError("Rule not found")
                return dict(row)
            values.extend([project_id, rule_id])
            db.execute(f"UPDATE learned_extraction_rules SET {', '.join(sets)} WHERE project_id = ? AND rule_id = ?", tuple(values))
            db.commit()
            self._write_extraction_rules_md(project_id)
            row = db.fetch_one("SELECT * FROM learned_extraction_rules WHERE project_id = ? AND rule_id = ?", (project_id, rule_id))
            if not row:
                raise ValueError("Rule not found")
            return dict(row)
        finally:
            db.close()

    def teach_element(self, project_id: str, element_id: str, label: str, note: str = "") -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            element = db.fetch_one("SELECT * FROM document_elements WHERE project_id = ? AND element_id = ?", (project_id, element_id))
            if not element:
                raise ValueError("Element not found")
            now = datetime.now().isoformat()
            event_id = self._next_id(db, "teaching_events", "event_id", "TEACH", 6)
            db.execute(
                """INSERT INTO teaching_events
                   (event_id, project_id, article_id, table_id, row_id, sample_id,
                    target_field, target_header, target_unit, value, source_type,
                    evidence, notes, status, created_at)
                   VALUES (?, ?, ?, '', ?, '', ?, ?, '', ?, 'document_element', ?, ?, 'confirmed', ?)""",
                (
                    event_id, project_id, element["article_id"], element_id,
                    f"label:{label}", label, label,
                    element["text_content"] or element["caption"] or element_id,
                    note,
                    now,
                ),
            )
            labels = self._element_labels(db, project_id, element["article_id"], element_id)
            matched = json.loads(element["matched_headers_json"] or "[]")
            raw_table = json.loads(element["raw_table_json"] or "{}")
            result = self.scoring.score(
                element_type=element["element_type"],
                text=element["context_text"] or element["text_content"] or "",
                caption=element["caption"] or "",
                section_path=element["section_path"] or "",
                matched_headers=matched,
                raw_table=raw_table,
                user_labels=labels,
            )
            db.execute(
                "UPDATE document_elements SET relevance_score = ?, score_reasons_json = ?, updated_at = ? WHERE element_id = ?",
                (result.score, json.dumps(result.reasons, ensure_ascii=False), now, element_id),
            )
            rule_type = {
                "包含数据": "resource_hint",
                "可能包含数据": "resource_hint",
                "不是数据": "resource_exclude",
                "参考文献/排除": "resource_exclude",
                "样品描述": "resource_hint",
                "方法描述": "resource_hint",
            }.get(label)
            rule_id = ""
            if rule_type:
                existing = db.fetch_one(
                    """SELECT rule_id FROM learned_extraction_rules
                       WHERE project_id = ? AND article_id = ? AND element_id = ? AND rule_type = ? AND pattern = ?""",
                    (project_id, element["article_id"], element_id, rule_type, label),
                )
                rule_id = existing["rule_id"] if existing else self._next_id(db, "learned_extraction_rules", "rule_id", "LRN", 6)
                conditions = {
                    "label": label,
                    "element_type": element["element_type"],
                    "section_path": element["section_path"] or "",
                    "page_number": element["page_number"],
                }
                if existing:
                    db.execute(
                        """UPDATE learned_extraction_rules
                           SET evidence = ?, conditions = ?, enabled = 1, created_at = ?
                           WHERE rule_id = ?""",
                        (
                            element["text_content"] or element["caption"] or element_id,
                            json.dumps(conditions, ensure_ascii=False),
                            now,
                            rule_id,
                        ),
                    )
                else:
                    db.execute(
                        """INSERT INTO learned_extraction_rules
                           (rule_id, project_id, article_id, target_field, target_header, target_unit,
                            rule_type, source_type, pattern, evidence, conditions, confidence,
                            risk_level, review_status, scope, enabled, element_id, created_at, created_by)
                           VALUES (?, ?, ?, ?, ?, '', ?, 'document_element', ?, ?, ?, 0.95,
                                   'low', 'confirmed', 'article', 1, ?, ?, 'user')""",
                        (
                            rule_id,
                            project_id,
                            element["article_id"],
                            label,
                            label,
                            rule_type,
                            label,
                            element["text_content"] or element["caption"] or element_id,
                            json.dumps(conditions, ensure_ascii=False),
                            element_id,
                            now,
                        ),
                    )
            db.commit()
            self._write_extraction_rules_md(project_id)
            return {"event_id": event_id, "rule_id": rule_id, "element_id": element_id, "label": label, "relevance_score": result.score, "score_reasons": result.reasons}
        finally:
            db.close()

    def hit_test_elements(self, project_id: str, article_id: str, resource_id: str, page_number: int, bbox: list[float]) -> dict[str, Any]:
        query_bbox = self._clamp_bbox(bbox)
        elements = self.list_elements(project_id, article_id)
        hits = []
        for element in elements:
            if element["resource_id"] != resource_id:
                continue
            spans = element.get("page_spans") or [{"page_number": element.get("page_number"), "bbox": element.get("bbox")}]
            score = 0.0
            for span in spans:
                if int(span.get("page_number") or 0) == int(page_number):
                    score = max(score, self._bbox_iou(query_bbox, span.get("bbox") or []))
            if score > 0:
                hits.append({"element": element, "overlap": score})
        hits.sort(key=lambda item: item["overlap"], reverse=True)
        action = "create"
        if hits and hits[0]["overlap"] >= 0.65:
            action = "use_existing"
        elif hits and hits[0]["overlap"] >= 0.2:
            action = "choose_or_merge"
        return {"suggested_action": action, "hits": hits[:8]}

    def merge_selection(self, project_id: str, element_id: str, page_number: int, bbox: list[float]) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one("SELECT * FROM document_elements WHERE project_id = ? AND element_id = ?", (project_id, element_id))
            if not row:
                raise ValueError("Element not found")
            spans = json.loads(row["page_spans_json"] or "[]")
            if not isinstance(spans, list):
                spans = []
            spans.append({"page_number": int(page_number), "bbox": self._clamp_bbox(bbox), "role": row["element_type"]})
            db.execute(
                "UPDATE document_elements SET page_spans_json = ?, merge_reason = ?, updated_at = ? WHERE element_id = ?",
                (json.dumps(spans, ensure_ascii=False), "manual_selection_merged", datetime.now().isoformat(), element_id),
            )
            db.commit()
            return self._decode_element(dict(db.fetch_one("SELECT * FROM document_elements WHERE element_id = ?", (element_id,))))
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
        allowed = {"text_content", "bbox", "element_type", "caption", "raw_table"}
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
                bbox = self._clamp_bbox(values["bbox"])
                sets.append("bbox_json = ?")
                params.append(json.dumps(bbox))
                sets.append("page_spans_json = ?")
                params.append(json.dumps([{"page_number": row["page_number"], "bbox": bbox, "role": row["element_type"]}], ensure_ascii=False))
            if "element_type" in values:
                sets.append("element_type = ?")
                params.append(values["element_type"])
            if "caption" in values:
                sets.append("caption = ?")
                params.append(values["caption"][:3000])
            if "raw_table" in values:
                raw_table = self._editable_raw_table(values["raw_table"])
                body_text = self._table_body_text(raw_table.get("headers") or [], raw_table.get("rows") or [])
                article_id = row["article_id"]
                text_for_match = "\n".join(part for part in [row["caption"] or "", body_text] if part)
                targets = self.target_headers(project_id, article_id)
                matched = self._matched_headers(text_for_match, targets)
                raw_table["body_text"] = body_text
                raw_table["table_standardized"] = True
                raw_table["header_parse_method"] = "user_edited_standard_table"
                sets.append("raw_table_json = ?")
                params.append(json.dumps(raw_table, ensure_ascii=False))
                sets.append("text_content = ?")
                params.append(text_for_match[:12000])
                sets.append("matched_headers_json = ?")
                params.append(json.dumps(matched, ensure_ascii=False))
                sets.append("status = ?")
                params.append("table_standardized")
            sets.append("updated_at = ?")
            params.append(datetime.now().isoformat())
            params.append(element_id)
            db.execute(f"UPDATE document_elements SET {', '.join(sets)} WHERE element_id = ?", tuple(params))
            db.commit()
            return self._decode_element(dict(db.fetch_one("SELECT * FROM document_elements WHERE element_id = ?", (element_id,))))
        finally:
            db.close()

    def update_standard_table(
        self,
        project_id: str,
        element_id: str,
        headers: list[Any],
        rows: list[list[Any]],
        edit_reason: str = "",
    ) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                "SELECT * FROM document_elements WHERE project_id = ? AND element_id = ?",
                (project_id, element_id),
            )
            if not row:
                raise ValueError("Element not found")
            raw = json.loads(row["raw_table_json"] or "{}")
            source_headers, source_rows, source_name = self._standardization_source_table(raw)
            editable = self._editable_raw_table({"headers": headers, "rows": rows})
            editable.update({
                "source_headers": raw.get("source_headers") or raw.get("original_headers") or source_headers,
                "source_rows": raw.get("source_rows") or source_rows,
                "original_headers": raw.get("original_headers") or raw.get("source_headers") or source_headers,
                "header_rows": raw.get("header_rows") or [],
                "body_text": self._table_body_text(editable.get("headers") or [], editable.get("rows") or []),
                "table_standardized": True,
                "user_edited": True,
                "header_parse_method": "user_edited_standard_table",
                "standardization_source": source_name,
                "edit_reason": edit_reason,
                "standardization_warnings": raw.get("standardization_warnings") or [],
            })
            article_id = row["article_id"]
            text_for_match = "\n".join(part for part in [row["caption"] or "", editable["body_text"]] if part)
            targets = self.target_headers(project_id, article_id)
            matched = self._matched_headers(text_for_match, targets)
            db.execute(
                """UPDATE document_elements
                   SET raw_table_json = ?, text_content = ?, matched_headers_json = ?,
                       status = 'table_standardized', updated_at = ?
                   WHERE project_id = ? AND element_id = ?""",
                (
                    json.dumps(editable, ensure_ascii=False),
                    text_for_match[:12000],
                    json.dumps(matched, ensure_ascii=False),
                    datetime.now().isoformat(),
                    project_id,
                    element_id,
                ),
            )
            db.commit()
            return self._decode_element(dict(db.fetch_one(
                "SELECT * FROM document_elements WHERE project_id = ? AND element_id = ?",
                (project_id, element_id),
            )))
        finally:
            db.close()

    def restandardize_table(self, project_id: str, element_id: str, mode: str = "source_headers") -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                "SELECT * FROM document_elements WHERE project_id = ? AND element_id = ?",
                (project_id, element_id),
            )
            if not row:
                raise ValueError("Element not found")
            raw = json.loads(row["raw_table_json"] or "{}")
            normalized: dict[str, Any] | None = None
            if mode == "pdf_words" or self._table_headers_are_malformed_flat(raw):
                element = self._decode_element(dict(row))
                normalized = self._standardize_multilevel_table_from_pdf(db, project_id, element)
                if not normalized and mode == "pdf_words":
                    normalized = self._standardize_rotated_table_from_pdf(db, project_id, element)
            if not normalized:
                source_headers, source_rows, source_name = self._standardization_source_table(raw)
                source_raw = dict(raw)
                source_raw.update({
                    "headers": source_headers,
                    "rows": source_rows,
                    "header_parse_method": source_name,
                    "user_edited": False,
                })
                normalized = self._standardize_raw_table(source_raw)
            normalized["user_edited"] = False
            normalized["header_parse_method"] = "local_standardizer"
            body_text = normalized.get("body_text") or self._table_body_text(
                normalized.get("headers") or [],
                normalized.get("rows") or [],
            )
            article_id = row["article_id"]
            text_for_match = "\n".join(part for part in [row["caption"] or "", body_text] if part)
            targets = self.target_headers(project_id, article_id)
            matched = self._matched_headers(text_for_match, targets)
            db.execute(
                """UPDATE document_elements
                   SET raw_table_json = ?, text_content = ?, matched_headers_json = ?,
                       status = 'table_standardized', updated_at = ?
                   WHERE project_id = ? AND element_id = ?""",
                (
                    json.dumps(normalized, ensure_ascii=False),
                    text_for_match[:12000],
                    json.dumps(matched, ensure_ascii=False),
                    datetime.now().isoformat(),
                    project_id,
                    element_id,
                ),
            )
            db.commit()
            return self._decode_element(dict(db.fetch_one(
                "SELECT * FROM document_elements WHERE project_id = ? AND element_id = ?",
                (project_id, element_id),
            )))
        finally:
            db.close()

    def _editable_raw_table(self, raw: dict[str, Any]) -> dict[str, Any]:
        headers = [self._clean_table_cell(value) for value in raw.get("headers") or []]
        rows = []
        for row in raw.get("rows") or []:
            if isinstance(row, list):
                rows.append([self._clean_table_cell(value) for value in row])
        width = max([len(headers), *(len(row) for row in rows)] or [0])
        if width:
            headers = (headers + [f"Column_{index + 1}" for index in range(len(headers), width)])[:width]
            rows = [(row + [""] * (width - len(row)))[:width] for row in rows]
        result = dict(raw)
        result["headers"] = headers
        result["rows"] = rows
        return result

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
            if cell.get("evidence_status") == "insufficient":
                raise ValueError("该候选值证据不足，不能直接确认；请先补充来源、修改或拒绝该值")

            # F1: Validate target_field against header config (only if user explicitly changed it)
            if target_field and target_field != cell.get("target_header"):
                headers = self.target_headers(project_id, cell["article_id"])
                valid_fields = {h["display_header"] for h in headers} | {h["canonical_field"] for h in headers}
                if target_field not in valid_fields:
                    raise ValueError(f"目标字段 '{target_field}' 不在当前表头配置中")

            # B3: Check for duplicate target_field in same record (only if user explicitly changed to a NEW field)
            if target_field and target_field != cell.get("target_header"):
                conflict = db.fetch_one(
                    "SELECT cell_id FROM candidate_cells WHERE candidate_record_id = ? AND target_header = ? AND cell_id != ? AND mapping_status != 'conflict'",
                    (cell["candidate_record_id"], target_field, cell_id),
                )
                if conflict:
                    raise ValueError(f"该记录中已有字段 '{target_field}'，不能重复映射")
            # For conflict cells being confirmed, merge alternatives into the primary cell
            if cell.get("mapping_status") == "conflict":
                primary = db.fetch_one(
                    "SELECT cell_id, alternatives_json FROM candidate_cells WHERE candidate_record_id = ? AND target_header = ? AND cell_id != ? AND mapping_status = 'confirmed'",
                    (cell["candidate_record_id"], cell.get("target_header"), cell_id),
                )
                if primary:
                    # Merge this cell's value as an alternative into the primary
                    alts = json.loads(primary["alternatives_json"] or "[]")
                    alts.append({"value": cell.get("value", ""), "cell_id": cell_id, "status": "confirmed"})
                    db.execute(
                        "UPDATE candidate_cells SET alternatives_json = ? WHERE cell_id = ?",
                        (json.dumps(alts, ensure_ascii=False), primary["cell_id"]),
                    )
                    # Remove the conflict cell
                    db.execute("DELETE FROM candidate_cells WHERE cell_id = ?", (cell_id,))
                    db.commit()
                    return {"cell_id": cell_id, "status": "merged_into_primary"}

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

        # B4: Check for existing rule with same source→target
        existing = db.fetch_one(
            "SELECT rule_id FROM mapping_rules WHERE source_field = ? AND target_field = ?",
            (source_field, final_target),
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
                raw_pattern = rule.get("pattern") or ""
                try:
                    pattern = json.loads(raw_pattern) if str(raw_pattern).strip().startswith(("{", "[")) else {"pattern": raw_pattern}
                except Exception:
                    pattern = {"pattern": raw_pattern}
                lines.extend([
                    f"## {rule['rule_id']}",
                    f"- 状态: {'启用' if rule.get('enabled', 1) else '停用'}",
                    f"- 范围: {rule.get('scope') or 'article'}",
                    f"- 目标字段: {rule['target_header']} ({rule['target_field']})",
                    f"- 类型: {rule['rule_type']}",
                    f"- 说明: {pattern.get('explanation') or rule.get('evidence') or '—'}",
                    f"- 识别规则: {pattern.get('ocr_text') or pattern.get('pattern') or '—'}",
                    f"- 来源资源: {rule.get('element_id') or '—'}",
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
            db.execute(
                """DELETE FROM standardized_cell_provenance
                   WHERE record_id IN (
                       SELECT s.record_id FROM standardized_records s
                       JOIN candidate_records cr ON cr.candidate_record_id = s.row_id
                       WHERE s.article_id = ? AND cr.batch_id = ?
                   )""",
                (article_id, latest_batch["batch_id"]),
            )
            db.execute(
                """DELETE FROM standardized_records
                   WHERE article_id = ? AND row_id IN (
                       SELECT candidate_record_id FROM candidate_records WHERE batch_id = ?
                   )""",
                (article_id, latest_batch["batch_id"]),
            )
            approved_records = db.fetch_all(
                """SELECT * FROM candidate_records
                   WHERE batch_id = ? AND quality_grade IN ('A', 'B', 'C') ORDER BY row_index""",
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

    def export_article(
        self,
        project_id: str,
        article_id: str,
        format: str = "csv",
        output_dir: str | None = None,
    ) -> dict[str, Any]:
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
                db.close()
                self.finalize_standardized(project_id, article_id)
                db = self.pm.get_database(project_id)
                records = db.fetch_all(
                    "SELECT * FROM standardized_records WHERE article_id = ? ORDER BY processed_at",
                    (article_id,),
                )
            if not records:
                raise ValueError("No standardized records. Run review/finalize first.")
            out_dir = self._export_directory(project_id, project_dir, output_dir)
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

            job_id = self._next_id(db, "export_jobs", "job_id", "EXP", 7)
            db.execute(
                """INSERT INTO export_jobs
                   (job_id, project_id, article_id, table_id, export_format, output_path,
                    record_count, status, created_at)
                   VALUES (?, ?, ?, '', ?, ?, ?, 'completed', ?)""",
                (job_id, project_id, article_id, format, str(path), len(rows), datetime.now().isoformat()),
            )
            db.commit()
            return {
                "job_id": job_id,
                "path": str(path),
                "records": len(rows),
                "format": format,
            }
        finally:
            db.close()

    def export_jobs(
        self,
        project_id: str,
        article_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List completed and failed exports without exposing unrelated projects."""

        db = self.pm.get_database(project_id)
        try:
            sql = (
                "SELECT job_id, project_id, article_id, export_format, output_path, "
                "record_count, status, created_at FROM export_jobs WHERE project_id = ?"
            )
            params: list[Any] = [project_id]
            if article_id:
                sql += " AND article_id = ?"
                params.append(article_id)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(max(1, min(int(limit), 200)))
            return [dict(row) for row in db.fetch_all(sql, tuple(params))]
        finally:
            db.close()

    def export_job(self, project_id: str, job_id: str) -> dict[str, Any]:
        """Resolve one export job inside its owning workspace."""

        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT job_id, project_id, article_id, export_format, output_path,
                          record_count, status, created_at
                   FROM export_jobs
                   WHERE project_id = ? AND job_id = ?""",
                (project_id, job_id),
            )
            if not row:
                raise ValueError("Export job not found")
            return dict(row)
        finally:
            db.close()

    def _export_directory(self, project_id: str, project_dir: Path, output_dir: str | None = None) -> Path:
        runtime = load_runtime_settings()
        if runtime.profile != RuntimeProfile.DEVELOPMENT:
            root = runtime.effective_export_root.expanduser().resolve()
            requested = Path(output_dir).expanduser().resolve() if output_dir else root
            if requested != root and root not in requested.parents:
                raise ValueError("服务器导出目录必须位于 GeoChem 受控导出根目录内。")
            return requested
        if output_dir:
            return Path(output_dir).expanduser()
        config = load_config()
        configured = config.ui_preferences.get("export_dir") if config.ui_preferences else None
        if configured:
            return Path(str(configured)).expanduser()
        return project_dir / "output"

    def create_extraction_batch(
        self,
        project_id: str,
        article_id: str,
        use_llm: bool = True,
        progress: ProgressCallback | None = None,
        element_types: list[str] | None = None,
        element_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        session = self.ensure_session(project_id, article_id)
        headers = self.target_headers(project_id, article_id)
        db = self.pm.get_database(project_id)
        try:
            selected_elements = [item for item in self.list_elements(project_id, article_id) if item["selected"]]
            if element_ids is not None:
                id_set = set(element_ids)
                selected_elements = [item for item in selected_elements if item["element_id"] in id_set]
            if element_types:
                type_set = set(element_types)
                selected_elements = [item for item in selected_elements if item["element_type"] in type_set]
            extraction_memory = self._extraction_memory(project_id, article_id)
            alias_rules = self._pre_extraction_alias_rules(project_id, article_id, headers)
            alias_rule_ids = self._pre_extraction_alias_rule_ids(project_id, article_id, headers)
            sample_patterns = self._sample_identifier_patterns(extraction_memory)
            selected_elements = [
                item for item in selected_elements
                if not self._resource_excluded_by_memory(item, extraction_memory)
            ]
            figure_count = sum(1 for item in selected_elements if item["element_type"] == "figure")
            elements = [item for item in selected_elements if item["element_type"] != "figure"]
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
                progress(
                    "正在按目标表头构建样品记录",
                    0.05,
                    {"elements": len(elements), "skipped_figures": figure_count},
                )
                if figure_count:
                    progress(
                        f"{figure_count} 个图像资源等待人工处理，表头数据抽取将跳过图像",
                        0.08,
                        {"skipped_figures": figure_count, "status": "manual_image_required"},
                    )
            extracted: list[dict[str, Any]] = []
            regular_elements = [
                element for element in elements
                if not (use_llm and element.get("element_type") == "paragraph")
            ]
            paragraph_elements = [
                element for element in elements
                if use_llm and element.get("element_type") == "paragraph"
            ]
            completed = 0
            for element in regular_elements:
                records = self._local_records(element, headers, alias_rules, alias_rule_ids, sample_patterns)
                if use_llm and not records:
                    try:
                        llm_records = self._llm_records(db, project_id, article_id, element, headers, extraction_memory)
                        records.extend(llm_records)
                    except Exception as exc:
                        db.commit()
                        if progress:
                            progress(
                                f"资源 {element['element_id']} 的 AI 抽取失败: {exc}",
                                0.1 + 0.75 * (completed / max(1, len(elements))),
                                {"element_id": element["element_id"], "error": str(exc)[:1000]},
                            )
                extracted.extend({"element": element, "record": record} for record in records)
                completed += 1
                if progress:
                    progress(
                        f"已处理 {completed}/{len(elements)} 个资源",
                        0.1 + 0.75 * (completed / max(1, len(elements))),
                        {
                            "element_id": element["element_id"],
                            "element_type": element["element_type"],
                            "records": len(records),
                            "completed": completed,
                            "total": len(elements),
                        },
                    )

            for start in range(0, len(paragraph_elements), 4):
                chunk = paragraph_elements[start:start + 4]
                local_by_id = {
                    element["element_id"]: self._local_records(
                        element, headers, alias_rules, alias_rule_ids, sample_patterns
                    )
                    for element in chunk
                }
                try:
                    llm_by_id = self._llm_paragraph_records_batch(
                        db, project_id, article_id, chunk, headers, extraction_memory
                    )
                except Exception as exc:
                    llm_by_id = {}
                    if progress:
                        progress(
                            f"段落批次 AI 抽取失败，正在逐段降级重试: {exc}",
                            0.1 + 0.75 * (completed / max(1, len(elements))),
                            {"element_ids": [item["element_id"] for item in chunk], "error": str(exc)[:1000]},
                        )
                    for element in chunk:
                        try:
                            llm_by_id[element["element_id"]] = self._llm_records(
                                db, project_id, article_id, element, headers, extraction_memory
                            )
                        except Exception as item_exc:
                            llm_by_id[element["element_id"]] = []
                            if progress:
                                progress(
                                    f"资源 {element['element_id']} 的 AI 抽取失败: {item_exc}",
                                    0.1 + 0.75 * (completed / max(1, len(elements))),
                                    {"element_id": element["element_id"], "error": str(item_exc)[:1000]},
                                )
                for element in chunk:
                    records = [
                        *local_by_id.get(element["element_id"], []),
                        *llm_by_id.get(element["element_id"], []),
                    ]
                    extracted.extend({"element": element, "record": record} for record in records)
                    completed += 1
                    if progress:
                        progress(
                            f"已处理 {completed}/{len(elements)} 个资源",
                            0.1 + 0.75 * (completed / max(1, len(elements))),
                            {
                                "element_id": element["element_id"],
                                "element_type": element["element_type"],
                                "records": len(records),
                                "completed": completed,
                                "total": len(elements),
                            },
                        )
            for item in extracted:
                self._merge_record(db, batch_id, article_id, item["element"], item["record"], headers, project_id)
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
            return {
                "batch_id": batch_id,
                "record_count": count,
                "header_count": len(headers),
                "processed_element_ids": [item["element_id"] for item in elements],
                "processed_element_count": len(elements),
                "applied_rules_count": len(extraction_memory.get("rules", [])),
                "rule_memory_snapshot_id": extraction_memory.get("snapshot_id"),
                "unmatched_source_fields": [],
            }
        except Exception:
            try:
                db.execute("UPDATE extraction_batches SET status = 'failed', updated_at = ? WHERE batch_id = ?", (datetime.now().isoformat(), batch_id))
                db.commit()
            except Exception:
                pass
            raise
        finally:
            db.close()

    def extract_tables(
        self,
        project_id: str,
        article_id: str,
        use_llm: bool = False,
        progress: ProgressCallback | None = None,
        element_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.create_extraction_batch(
            project_id,
            article_id,
            use_llm=use_llm,
            progress=progress,
            element_types=["table"],
            element_ids=element_ids,
        )

    def extract_paragraphs(
        self,
        project_id: str,
        article_id: str,
        element_ids: list[str] | None = None,
        use_llm: bool = True,
        progress: ProgressCallback | None = None,
        table_element_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        # Paragraph extraction keeps selected tables in the batch so the result
        # shown to users is the merged table-first candidate sheet.
        types = ["table", "paragraph"]
        combined_ids = list(element_ids or [])
        if element_ids is not None:
            if table_element_ids is None:
                table_element_ids = [
                    item["element_id"]
                    for item in self.list_elements(project_id, article_id)
                    if item.get("selected") and item.get("element_type") == "table"
                ]
            combined_ids.extend(table_element_ids)
            combined_ids = list(dict.fromkeys(combined_ids))
        return self.create_extraction_batch(
            project_id,
            article_id,
            use_llm=use_llm,
            progress=progress,
            element_types=types,
            element_ids=combined_ids if element_ids is not None else None,
        )

    def merge_candidates(self, project_id: str, article_id: str) -> dict[str, Any]:
        session = self.ensure_session(project_id, article_id)
        batch_id = session.get("active_batch_id")
        if not batch_id:
            return {"batch_id": "", "record_count": 0, "status": "no_active_batch"}
        payload = self.batch_records(project_id, batch_id)
        return {
            "batch_id": batch_id,
            "record_count": len(payload.get("records", [])),
            "status": "already_merged",
        }

    def reapply_rules(self, project_id: str, batch_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            cells = db.fetch_all(
                """SELECT c.* FROM candidate_cells c
                   JOIN candidate_records r ON r.candidate_record_id = c.candidate_record_id
                   JOIN extraction_batches b ON b.batch_id = r.batch_id
                   WHERE b.project_id = ? AND b.batch_id = ?""",
                (project_id, batch_id),
            )
            for cell in cells:
                self._auto_apply_rules(db, project_id, dict(cell))
            db.commit()
            return {"batch_id": batch_id, "updated": len(cells)}
        finally:
            db.close()

    def validate_batch_evidence(self, project_id: str, batch_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        updated = 0
        insufficient = 0
        try:
            rows = db.fetch_all(
                """SELECT c.*, e.element_type, e.caption, e.text_content, e.context_text, e.raw_table_json,
                          e.page_number AS element_page_number
                   FROM candidate_cells c
                   JOIN candidate_records r ON r.candidate_record_id = c.candidate_record_id
                   JOIN extraction_batches b ON b.batch_id = r.batch_id
                   LEFT JOIN document_elements e ON e.element_id = c.element_id
                   WHERE b.project_id = ? AND b.batch_id = ?""",
                (project_id, batch_id),
            )
            headers_by_article: dict[str, list[dict[str, Any]]] = {}
            batch = db.fetch_one("SELECT article_id FROM extraction_batches WHERE project_id = ? AND batch_id = ?", (project_id, batch_id))
            article_id = batch["article_id"] if batch else ""
            headers_by_article[article_id] = self.target_headers(project_id, article_id)
            header_lookup = {h["display_header"]: h for h in headers_by_article[article_id]}
            for row in rows:
                target = header_lookup.get(row["target_header"])
                if not target:
                    continue
                try:
                    snapshot = json.loads(row["source_row_snapshot"] or "{}")
                except Exception:
                    snapshot = {}
                element = {
                    "element_id": row["element_id"] or "",
                    "element_type": row["element_type"] or "",
                    "caption": row["caption"] or "",
                    "text_content": row["text_content"] or "",
                    "context_text": row["context_text"] or "",
                    "raw_table": json.loads(row["raw_table_json"] or "{}") if row["raw_table_json"] else {},
                    "page_number": row["element_page_number"] or row["page_number"],
                }
                status, reason = self._cell_evidence_status(
                    element=element,
                    target=target,
                    original_field=row["original_field"] or "",
                    value=row["value"] or "",
                    extraction_method=row["extraction_method"] or "",
                    source_quote=row["source_quote"] or "",
                    source_row_snapshot=snapshot,
                    applied_rule_id=row["applied_rule_id"] or "",
                )
                if status == "insufficient":
                    insufficient += 1
                if status != row["evidence_status"] or reason != row["evidence_reason"]:
                    db.execute(
                        """UPDATE candidate_cells
                           SET evidence_status = ?, evidence_reason = ?,
                               risk_level = CASE WHEN ? = 'insufficient' THEN 'high' ELSE risk_level END,
                               review_status = CASE WHEN ? = 'insufficient' THEN 'pending' ELSE review_status END,
                               updated_at = ?
                           WHERE cell_id = ?""",
                        (status, reason, status, status, datetime.now().isoformat(), row["cell_id"]),
                    )
                    updated += 1
            db.commit()
            return {"batch_id": batch_id, "updated": updated, "insufficient": insufficient}
        finally:
            db.close()

    def batch_field_mappings(self, project_id: str, batch_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT c.original_field, c.target_header, c.target_field, c.target_unit,
                          c.extraction_method, c.applied_rule_id, c.mapping_status,
                          c.evidence_status, COUNT(*) AS count, AVG(c.confidence) AS confidence
                   FROM candidate_cells c
                   JOIN candidate_records r ON r.candidate_record_id = c.candidate_record_id
                   JOIN extraction_batches b ON b.batch_id = r.batch_id
                   WHERE b.project_id = ? AND b.batch_id = ?
                   GROUP BY c.original_field, c.target_header, c.target_field, c.target_unit,
                            c.extraction_method, c.applied_rule_id, c.mapping_status, c.evidence_status
                   ORDER BY c.target_header, c.original_field""",
                (project_id, batch_id),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def quality_slice(self, project_id: str, batch_id: str, source_type: str = "") -> dict[str, Any]:
        payload = self.batch_records(project_id, batch_id)
        cells = []
        db = self.pm.get_database(project_id)
        try:
            for record in payload.get("records", []):
                for cell in record.get("cells", {}).values():
                    if source_type:
                        element = db.fetch_one("SELECT element_type FROM document_elements WHERE element_id = ?", (cell.get("element_id"),))
                        if not element or element["element_type"] != source_type:
                            continue
                    cells.append({
                        "record_id": record["candidate_record_id"],
                        "sample_id": record.get("sample_id", ""),
                        **cell,
                    })
            return {"batch_id": batch_id, "source_type": source_type, "cells": cells}
        finally:
            db.close()

    def reextract_element(self, project_id: str, article_id: str, element_id: str, use_llm: bool = True, progress: ProgressCallback | None = None) -> dict[str, Any]:
        return self.create_extraction_batch(
            project_id,
            article_id,
            use_llm=use_llm,
            progress=progress,
            element_ids=[element_id],
        )

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
                    try:
                        cell["source_row_snapshot"] = json.loads(cell.get("source_row_snapshot") or "{}")
                    except Exception:
                        cell["source_row_snapshot"] = {"raw": cell.get("source_row_snapshot") or ""}
                    data[cell["target_header"]] = cell["value"]
                    cells[cell["target_header"]] = cell
                records.append({**record, "data": data, "cells": cells})
            memory = self._extraction_memory(project_id, batch["article_id"])
            return {
                "batch": dict(batch),
                "headers": headers,
                "records": records,
                "applied_rules_count": len(memory.get("rules", [])),
                "unmatched_source_fields": [],
                "rule_memory_snapshot_id": memory.get("snapshot_id"),
            }
        finally:
            db.close()

    def update_cell(self, project_id: str, cell_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {"value", "target_header", "target_field", "target_unit", "mapping_status", "review_status", "risk_level", "extraction_method", "evidence_status", "evidence_reason"}
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
            if "value" in values and str(values["value"]) != str(row["value"]):
                values["extraction_method"] = "manual_edit"
                values["evidence_status"] = "weak"
                values["evidence_reason"] = "用户在候选表中手动编辑，需人工审核确认"
            values["updated_at"] = datetime.now().isoformat()
            assignments = ", ".join(f"{key} = ?" for key in values)
            db.execute(f"UPDATE candidate_cells SET {assignments} WHERE cell_id = ?", tuple(values.values()) + (cell_id,))
            db.commit()
            return dict(db.fetch_one("SELECT * FROM candidate_cells WHERE cell_id = ?", (cell_id,)))
        finally:
            db.close()

    def create_manual_candidate_record(
        self,
        project_id: str,
        article_id: str,
        batch_id: str = "",
        sample_id: str = "",
        values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = values or {}
        db = self.pm.get_database(project_id)
        try:
            if not batch_id:
                session = self.ensure_session(project_id, article_id)
                batch_id = str(session.get("active_batch_id") or "")
            batch = db.fetch_one(
                "SELECT * FROM extraction_batches WHERE project_id = ? AND article_id = ? AND batch_id = ?",
                (project_id, article_id, batch_id),
            )
            if not batch:
                raise ValueError("Active extraction batch not found")
            now = datetime.now().isoformat()
            sample_id = str(sample_id or values.get("SampleID") or "").strip()
            record_id = self._next_id(db, "candidate_records", "candidate_record_id", "CAND", 8)
            sample_key_base = self._norm_sample_id(sample_id) or f"manual_{record_id.lower()}"
            sample_key = sample_key_base
            suffix = 1
            while db.fetch_one("SELECT 1 FROM candidate_records WHERE batch_id = ? AND sample_key = ?", (batch_id, sample_key)):
                suffix += 1
                sample_key = f"{sample_key_base}_{suffix}"
            db.execute(
                """INSERT INTO candidate_records
                   (candidate_record_id, batch_id, article_id, sample_key, sample_id, row_index,
                    merge_status, quality_grade, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'manual_added', 'C', ?, ?)""",
                (record_id, batch_id, article_id, sample_key, sample_id, self._next_row_index(db, batch_id), now, now),
            )
            headers = self.target_headers(project_id, article_id)
            header_by_name = {header["display_header"]: header for header in headers}
            sample_header = next((header for header in headers if re.search(r"sample\s*[_-]?\s*id|样品", header["canonical_field"], re.I)), None)
            cell_values = {str(key): value for key, value in values.items() if value not in (None, "")}
            if sample_id and sample_header and sample_header["display_header"] not in cell_values:
                cell_values[sample_header["display_header"]] = sample_id
            for target_header, value in cell_values.items():
                target = header_by_name.get(target_header)
                if not target:
                    continue
                cell_id = self._next_id(db, "candidate_cells", "cell_id", "CELL", 8)
                db.execute(
                    """INSERT INTO candidate_cells
                       (cell_id, candidate_record_id, header_id, target_header, target_field, target_unit,
                        value, original_value, original_field, original_unit, confidence, risk_level,
                        mapping_status, extraction_method, source_label, source_quote, source_row_snapshot,
                        evidence_status, evidence_reason, element_id, page_number, bbox_json, alternatives_json,
                        review_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual_candidate', ?, 0.7, 'medium',
                               'manual', 'manual_edit', '人工新增', ?, ?, 'weak', ?,
                               NULL, NULL, '[]', '[]', 'pending', ?, ?)""",
                    (
                        cell_id, record_id, target["header_id"], target["display_header"],
                        target["canonical_field"], target["target_unit"], str(value), str(value),
                        target["target_unit"], f"人工新增候选行: {value}",
                        json.dumps({"manual_value": str(value)}, ensure_ascii=False),
                        "用户手动新增候选行，需审核确认", now, now,
                    ),
                )
            db.execute(
                "UPDATE extraction_batches SET record_count = (SELECT COUNT(*) FROM candidate_records WHERE batch_id = ?), updated_at = ? WHERE batch_id = ?",
                (batch_id, now, batch_id),
            )
            db.commit()
            return {"candidate_record_id": record_id, "batch_id": batch_id, "sample_id": sample_id}
        finally:
            db.close()

    def delete_candidate_record(self, project_id: str, record_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            record = db.fetch_one(
                """SELECT r.*, b.project_id FROM candidate_records r
                   JOIN extraction_batches b ON b.batch_id = r.batch_id
                   WHERE b.project_id = ? AND r.candidate_record_id = ?""",
                (project_id, record_id),
            )
            if not record:
                raise ValueError("Candidate record not found")
            db.execute("DELETE FROM candidate_cells WHERE candidate_record_id = ?", (record_id,))
            db.execute("DELETE FROM candidate_records WHERE candidate_record_id = ?", (record_id,))
            db.execute(
                "UPDATE extraction_batches SET record_count = (SELECT COUNT(*) FROM candidate_records WHERE batch_id = ?), updated_at = ? WHERE batch_id = ?",
                (record["batch_id"], datetime.now().isoformat(), record["batch_id"]),
            )
            db.commit()
            return {"deleted": True, "candidate_record_id": record_id}
        finally:
            db.close()

    def delete_candidate_cell(self, project_id: str, cell_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                """SELECT c.cell_id FROM candidate_cells c
                   JOIN candidate_records r ON r.candidate_record_id = c.candidate_record_id
                   JOIN extraction_batches b ON b.batch_id = r.batch_id
                   WHERE b.project_id = ? AND c.cell_id = ?""",
                (project_id, cell_id),
            )
            if not row:
                raise ValueError("Candidate cell not found")
            db.execute("DELETE FROM candidate_cells WHERE cell_id = ?", (cell_id,))
            db.commit()
            return {"deleted": True, "cell_id": cell_id}
        finally:
            db.close()

    def upsert_manual_image_cell(
        self,
        project_id: str,
        candidate_record_id: str,
        element_id: str,
        target_header: str,
        value: str,
        evidence_note: str = "",
    ) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            record = db.fetch_one(
                """SELECT r.*, b.project_id, b.article_id
                   FROM candidate_records r
                   JOIN extraction_batches b ON b.batch_id = r.batch_id
                   WHERE b.project_id = ? AND r.candidate_record_id = ?""",
                (project_id, candidate_record_id),
            )
            if not record:
                raise ValueError("Candidate record not found")
            element = db.fetch_one(
                "SELECT * FROM document_elements WHERE project_id = ? AND element_id = ? AND element_type = 'figure'",
                (project_id, element_id),
            )
            if not element:
                raise ValueError("Figure element not found")
            target = next((h for h in self.target_headers(project_id, record["article_id"]) if h["display_header"] == target_header), None)
            if not target:
                raise ValueError("Target header not found")
            now = datetime.now().isoformat()
            bbox = json.loads(element["bbox_json"] or "[]")
            existing = db.fetch_one(
                "SELECT * FROM candidate_cells WHERE candidate_record_id = ? AND target_header = ?",
                (candidate_record_id, target_header),
            )
            original_value = evidence_note.strip() or f"人工从图像补充: {value}"
            if existing:
                db.execute(
                    """UPDATE candidate_cells
                       SET value = ?, original_value = ?, original_field = ?, original_unit = ?,
                           confidence = ?, risk_level = 'medium', mapping_status = 'manual',
                           extraction_method = 'manual_image', source_label = ?, source_quote = ?,
                           source_row_snapshot = ?, evidence_status = 'weak', evidence_reason = ?,
                           element_id = ?, page_number = ?, bbox_json = ?, review_status = 'pending',
                           updated_at = ?
                       WHERE cell_id = ?""",
                    (
                        str(value), original_value, element["caption"] or "figure_manual", target["target_unit"],
                        0.8, self._source_label(dict(element)), original_value,
                        json.dumps({"figure_caption": element["caption"] or "", "note": evidence_note}, ensure_ascii=False),
                        "人工从图像/图注补值，需审核确认",
                        element_id, element["page_number"], json.dumps(bbox), now, existing["cell_id"],
                    ),
                )
                cell_id = existing["cell_id"]
            else:
                cell_id = self._next_id(db, "candidate_cells", "cell_id", "CELL", 8)
                db.execute(
                    """INSERT INTO candidate_cells
                       (cell_id, candidate_record_id, header_id, target_header, target_field, target_unit,
                        value, original_value, original_field, original_unit, confidence, risk_level,
                        mapping_status, extraction_method, source_label, source_quote, source_row_snapshot,
                        evidence_status, evidence_reason, element_id, page_number, bbox_json, alternatives_json,
                        review_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.8, 'medium', 'manual',
                               'manual_image', ?, ?, ?, 'weak', ?,
                               ?, ?, ?, '[]', 'pending', ?, ?)""",
                    (
                        cell_id, candidate_record_id, target["header_id"], target["display_header"],
                        target["canonical_field"], target["target_unit"], str(value), original_value,
                        element["caption"] or "figure_manual", target["target_unit"],
                        self._source_label(dict(element)), original_value,
                        json.dumps({"figure_caption": element["caption"] or "", "note": evidence_note}, ensure_ascii=False),
                        "人工从图像/图注补值，需审核确认", element_id,
                        element["page_number"], json.dumps(bbox), now, now,
                    ),
                )
            db.execute(
                "UPDATE candidate_records SET updated_at = ?, quality_grade = CASE WHEN quality_grade IN ('D', 'E') THEN 'C' ELSE quality_grade END WHERE candidate_record_id = ?",
                (now, candidate_record_id),
            )
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

    def _local_records(
        self,
        element: dict[str, Any],
        headers: list[dict[str, Any]],
        alias_rules: dict[str, str] | None = None,
        alias_rule_ids: dict[str, str] | None = None,
        sample_patterns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if element["element_type"] != "table":
            return []
        raw = element.get("raw_table") or {}
        if raw.get("rows"):
            source_headers = [str(header) for header in raw.get("headers") or []]
            records = []
            for row in raw["rows"]:
                if isinstance(row, dict):
                    values = {str(key): value for key, value in row.items()}
                else:
                    values = {
                        source_headers[index] if index < len(source_headers) else f"Column_{index + 1}": str(value)
                        for index, value in enumerate(row)
                    }
                mapping = dict(self._map_source_headers(list(values.keys()), headers, alias_rules))
                applied = {
                    mapping.get(str(key), str(key)): alias_rule_ids[self._norm(str(key))]
                    for key in values
                    if alias_rule_ids and self._norm(str(key)) in alias_rule_ids and mapping.get(str(key), str(key)) in mapping.values()
                }
                source_fields = {mapping.get(str(key), str(key)): str(key) for key in values}
                source_row_snapshot = {str(key): str(value) for key, value in values.items()}
                values = {mapping.get(str(key), str(key)): value for key, value in values.items()}
                sample_value = values.get("SampleID") or values.get("Sample ID") or values.get("Sample") or ""
                if not sample_value:
                    sample_key = next((key for key in values if re.search(r"sample\s*id|sample|样品", str(key), re.I)), "")
                    sample_value = values.get(sample_key, "") if sample_key else ""
                sample_ids = self._extract_sample_ids(str(sample_value or ""), sample_patterns)
                if not sample_ids:
                    continue
                sample_key = next((key for key in values if re.search(r"sample\s*id|sample|样品", str(key), re.I)), "")
                for sample_id in sample_ids:
                    row_values = dict(values)
                    if sample_key:
                        row_values[sample_key] = sample_id
                    records.append({
                        "sample_id": sample_id,
                        "values": row_values,
                        "_source_method": "local_table_parser",
                        "_applied_rules": applied,
                        "_source_fields": source_fields,
                        "_source_row_snapshot": source_row_snapshot,
                    })
            return records
        source_headers = raw.get("headers") or []
        body = raw.get("body_text") or element.get("text_content") or ""
        matches = self._sample_matches(body, sample_patterns)
        if not matches:
            return []
        mapped_headers = self._map_source_headers(source_headers, headers, alias_rules)
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
            sample_id = match.group(1) if match.groups() else match.group(0)
            values[self._sample_header(headers)] = sample_id
            records.append({
                "sample_id": sample_id,
                "values": values,
                "_source_method": "local_table_parser",
                "_source_quote": chunk[:800],
                "_evidence_reason": "从表格文本中按样品编号后的数字位置推断，需人工确认",
            })
        return records

    def _llm_header_subset(
        self,
        element: dict[str, Any],
        headers: list[dict[str, Any]],
        extraction_memory: dict[str, Any] | None = None,
        limit: int = 36,
    ) -> list[dict[str, Any]]:
        """Keep paragraph prompts focused without hiding plausible target fields."""
        if element.get("element_type") != "paragraph" or len(headers) <= limit:
            return headers

        source_text = str(element.get("text_content") or element.get("context_text") or "")
        matched = {
            self._norm(value)
            for value in [*(element.get("matched_headers") or []), *self._matched_headers(source_text, headers)]
            if value
        }
        rule_targets = {
            self._norm(rule.get("target_header") or rule.get("target_field") or "")
            for rule in (extraction_memory or {}).get("rules", [])
            if rule.get("target_header") or rule.get("target_field")
        }
        text_lower = source_text.lower()
        preferred_context = {
            "sampleid", "sample", "samplename", "formation", "stage", "epoch",
            "period", "age", "agemin", "agemax", "depth", "depthm", "location",
            "site", "sitename", "lithology", "lithtype", "material", "setting", "note",
        }
        stopwords = {
            "field", "value", "sample", "content", "data", "description", "unit",
            "concentration", "information", "record", "measurement", "geochemical",
        }

        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, header in enumerate(headers):
            display = str(header.get("display_header") or "")
            canonical = str(header.get("canonical_field") or display)
            display_norm = self._norm(display)
            canonical_norm = self._norm(canonical)
            score = 0
            if canonical_norm in {"sampleid", "sample", "samplename"}:
                score = 1000
            if display_norm in matched or canonical_norm in matched:
                score = max(score, 900)
            if display_norm in rule_targets or canonical_norm in rule_targets:
                score = max(score, 800)
            if canonical_norm in preferred_context:
                score = max(score, 180)

            description = str(header.get("description") or "")
            description_tokens = {
                token.lower()
                for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", description)
                if token.lower() not in stopwords
            }
            if any(re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text_lower) for token in description_tokens):
                score = max(score, 500)

            # Context fields are useful fallbacks for prose. Chemical/value fields
            # must be visibly matched, otherwise all 156 schema columns come back.
            if score or classify_field(canonical, display) in {"basic_info", "location", "stratigraphy_age", "sample_context"}:
                scored.append((score, -index, header))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        # Never discard visibly matched/rule-backed fields merely to meet the
        # ordinary prompt target. Allow a small hard ceiling for unusually rich
        # paragraphs, then fill remaining slots with contextual fields.
        priority = [item for item in scored if item[0] >= 500][:48]
        selected_ids = {id(item[2]) for item in priority}
        remaining = [item for item in scored if id(item[2]) not in selected_ids]
        selected = [*priority, *remaining[:max(0, limit - len(priority))]]
        subset = [item[2] for item in selected]
        if not subset:
            return headers[:limit]
        return sorted(subset, key=lambda item: int(item.get("order") or 0))

    def _llm_extraction_memory_subset(
        self,
        element: dict[str, Any],
        headers: list[dict[str, Any]],
        extraction_memory: dict[str, Any] | None,
    ) -> dict[str, Any]:
        memory = extraction_memory or {"rules": []}
        text_norm = self._norm(" ".join([
            str(element.get("caption") or ""),
            str(element.get("text_content") or ""),
            str(element.get("context_text") or ""),
        ]))
        target_norms = {
            self._norm(value)
            for header in headers
            for value in (header.get("display_header"), header.get("canonical_field"))
            if value
        }
        relevant = []
        for rule in memory.get("rules", []):
            pattern_norm = self._norm(rule.get("pattern") or "")
            target_norm = self._norm(rule.get("target_header") or rule.get("target_field") or "")
            same_element = bool(rule.get("element_id") and rule.get("element_id") == element.get("element_id"))
            if (
                same_element
                or rule.get("rule_type") == "sample_identifier"
                or target_norm in target_norms
                or (pattern_norm and pattern_norm in text_norm)
            ):
                relevant.append(rule)
        return {"snapshot_id": memory.get("snapshot_id"), "rules": relevant}

    def _llm_source_text(self, element: dict[str, Any]) -> str:
        if element.get("element_type") != "paragraph":
            return str(element.get("context_text") or element.get("text_content") or "")
        paragraph = str(element.get("text_content") or "").strip()
        context = str(element.get("context_text") or "").strip()
        source = paragraph or context
        if paragraph and context and paragraph not in context:
            source = f"{paragraph}\n\nNearby context:\n{context[:2000]}"
        return source[:16000]

    def _llm_paragraph_records_batch(
        self,
        db,
        project_id: str,
        article_id: str,
        elements: list[dict[str, Any]],
        headers: list[dict[str, Any]],
        extraction_memory: dict[str, Any] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Extract a small paragraph group while retaining element provenance."""
        if not elements:
            return {}
        if len(elements) == 1:
            element = elements[0]
            return {
                element["element_id"]: self._llm_records(
                    db, project_id, article_id, element, headers, extraction_memory
                )
            }

        definitions: dict[str, dict[str, Any]] = {}
        resources = []
        valid_ids = {str(element["element_id"]) for element in elements}
        for element in elements:
            prompt_headers = self._llm_header_subset(element, headers, extraction_memory)
            for header in prompt_headers:
                definitions.setdefault(header["display_header"], {
                    "header": header["display_header"],
                    "field": header["canonical_field"],
                    "unit": header["target_unit"],
                    "description": str(header["description"] or "")[:180],
                })
            resources.append({
                "element_id": element["element_id"],
                "source_text": self._llm_source_text(element),
                "allowed_target_headers": [header["display_header"] for header in prompt_headers],
                "extraction_memory": self._llm_extraction_memory_subset(
                    element, prompt_headers, extraction_memory
                ),
            })

        payload = json.dumps({
            "instruction": (
                "Process each resource independently. Return one group for every supplied element_id. "
                "Use only that resource's allowed_target_headers as value keys. Return one record per "
                "sample, omit missing fields, never invent values, and copy a short source_quote from "
                "the same resource for every extracted record."
            ),
            "target_header_definitions": list(definitions.values()),
            "resources": resources,
            "return_schema": {
                "resources": [{
                    "element_id": "exact supplied element_id",
                    "records": [{
                        "sample_id": "string or empty",
                        "source_quote": "copied evidence quote",
                        "values": {"exact target header": "value or {value, source_quote}"},
                    }],
                }],
            },
        }, ensure_ascii=False)
        response = LLMClient(load_config(), db=db).chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative geochemical paragraph extractor. Keep resources "
                        "strictly separate, preserve each supplied element_id, obey confirmed rules, "
                        "and return final JSON only."
                    ),
                },
                {"role": "user", "content": payload},
            ],
            task_name="document_record_extraction",
            project_id=project_id,
            article_id=article_id,
            agent_name="web_workbench",
            skill_name="document_record_extraction_batch",
            temperature_override=0.0,
            max_tokens_override=3200,
            use_cache=True,
        )
        content = response.content or ""
        match = re.search(r"\{[\s\S]*\}", content)
        try:
            parsed = json.loads(match.group(0) if match else content)
        except Exception as exc:
            summary = content.strip().replace("\n", " ")[:500]
            raise ValueError(f"LLM 返回无法解析为分组段落 JSON: {summary}") from exc

        grouped: dict[str, list[dict[str, Any]]] = {element_id: [] for element_id in valid_ids}
        groups = parsed.get("resources", []) if isinstance(parsed, dict) else []
        if not groups and isinstance(parsed, dict) and isinstance(parsed.get("records"), list):
            groups = [{
                "element_id": record.get("source_element_id") or record.get("element_id"),
                "records": [record],
            } for record in parsed["records"] if isinstance(record, dict)]
        seen_ids: set[str] = set()
        for group in groups:
            if not isinstance(group, dict):
                continue
            element_id = str(group.get("element_id") or "")
            if element_id not in valid_ids:
                continue
            seen_ids.add(element_id)
            records = group.get("records") or []
            grouped[element_id].extend(
                {**record, "_source_method": "llm_paragraph"}
                for record in records
                if isinstance(record, dict) and isinstance(record.get("values"), dict)
            )
        missing_ids = valid_ids - seen_ids
        if missing_ids:
            missing = ", ".join(sorted(missing_ids))
            raise ValueError(f"LLM 分组段落结果缺少资源: {missing}")
        return grouped

    def _llm_records(
        self,
        db,
        project_id: str,
        article_id: str,
        element: dict[str, Any],
        headers: list[dict[str, Any]],
        extraction_memory: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        config = load_config()
        task_name = "figure_extraction" if element["element_type"] == "figure" else "document_record_extraction"
        task_config = config.get_task_model(task_name)
        if element["element_type"] == "figure":
            if not task_config:
                db.execute("UPDATE document_elements SET status = 'waiting_for_vision_model' WHERE element_id = ?", (element["element_id"],))
                db.commit()
                return []
            provider = config.get_provider(task_config.provider)
            model_config = next((model for model in provider.models if model.name == task_config.model), None) if provider else None
            if not model_config or not model_config.supports_vision or not element.get("preview_path"):
                db.execute("UPDATE document_elements SET status = 'waiting_for_vision_model' WHERE element_id = ?", (element["element_id"],))
                db.commit()
                return []
        prompt_headers = self._llm_header_subset(element, headers, extraction_memory)
        compact_headers = [{
            "header": h["display_header"], "field": h["canonical_field"],
            "unit": h["target_unit"], "description": str(h["description"] or "")[:180],
        } for h in prompt_headers]
        compact_memory = self._llm_extraction_memory_subset(element, prompt_headers, extraction_memory)
        payload: Any = json.dumps({
            "instruction": "Return one object per sample. Use the exact target header strings as keys. Missing fields must be omitted. Never invent values. For every extracted value include a source_quote copied from the supplied source text/table evidence.",
            "target_headers": compact_headers,
            "source_type": element["element_type"],
            "source_text": self._llm_source_text(element),
            "extraction_memory": compact_memory,
            "return_schema": {"records": [{"sample_id": "string or empty", "source_quote": "short copied evidence quote", "values": {"exact target header": "value or {value, source_quote}"}}]},
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
            [{"role": "system", "content": "You are a conservative geochemical sample-table extractor. You must obey confirmed extraction_memory rules before making any fuzzy inference. Use exact target display headers as JSON keys."}, {"role": "user", "content": user_content}],
            task_name=task_name, project_id=project_id, article_id=article_id,
            agent_name="web_workbench", skill_name=task_name, temperature_override=0.0,
            max_tokens_override=2200 if element["element_type"] == "paragraph" else 5000,
            use_cache=True,
        )
        match = re.search(r"\{[\s\S]*\}", response.content or "")
        try:
            parsed = json.loads(match.group(0) if match else response.content)
        except Exception as exc:
            summary = (response.content or "").strip().replace("\n", " ")[:500]
            raise ValueError(f"LLM 返回无法解析为候选记录 JSON: {summary}") from exc
        records = parsed.get("records", []) if isinstance(parsed, dict) else parsed
        return [
            {**record, "_source_method": "llm_paragraph" if element["element_type"] == "paragraph" else "llm_table"}
            for record in records
            if isinstance(record, dict) and isinstance(record.get("values"), dict)
        ]

    def _merge_record(self, db, batch_id: str, article_id: str, element: dict[str, Any], record: dict[str, Any], headers: list[dict[str, Any]], project_id: str = "") -> None:
        sample_id = str(record.get("sample_id") or record.get("values", {}).get(self._sample_header(headers)) or "").strip()
        sample_key = self._normalize_sample(sample_id) if sample_id else f"unmatched:{element['element_id']}:{self._next_row_index(db, batch_id)}"
        existing = db.fetch_one("SELECT * FROM candidate_records WHERE batch_id = ? AND sample_key = ?", (batch_id, sample_key))
        now = datetime.now().isoformat()
        if existing:
            record_id = existing["candidate_record_id"]
            db.execute("UPDATE candidate_records SET merge_status = 'auto_merged', updated_at = ? WHERE candidate_record_id = ?", (now, record_id))
        else:
            record_id = self._next_id(db, "candidate_records", "candidate_record_id", "CREC", 7)
            initial_quality = "B" if record.get("_source_method") == "local_table_parser" else "D"
            db.execute(
                """INSERT INTO candidate_records
                   (candidate_record_id, batch_id, article_id, sample_key, sample_id, row_index,
                    merge_status, quality_grade, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id, batch_id, article_id, sample_key, sample_id,
                    self._next_row_index(db, batch_id), "matched" if sample_id else "unmatched",
                    initial_quality, now, now,
                ),
            )
        header_lookup = {h["display_header"]: h for h in headers}
        canonical_lookup = {self._norm(h["canonical_field"]): h for h in headers}
        applied_rules = record.get("_applied_rules") or {}
        source_fields = record.get("_source_fields") or {}
        row_snapshot = record.get("_source_row_snapshot") or {}
        for raw_header, raw_value in record.get("values", {}).items():
            value_source_quote = ""
            if isinstance(raw_value, dict):
                value_source_quote = str(raw_value.get("source_quote") or raw_value.get("quote") or "")
                raw_value = raw_value.get("value")
            target = header_lookup.get(raw_header) or canonical_lookup.get(self._norm(raw_header))
            if not target or raw_value in (None, ""):
                continue
            applied_rule_id = str(applied_rules.get(target["display_header"]) or "")
            original_field = str(source_fields.get(target["display_header"]) or raw_header)
            extraction_method = str(record.get("_source_method") or "unknown")
            source_quote = value_source_quote or str(record.get("_source_quote") or "")
            source_row_snapshot = row_snapshot if row_snapshot else {}
            evidence_status, evidence_reason = self._cell_evidence_status(
                element=element,
                target=target,
                original_field=original_field,
                value=str(raw_value),
                extraction_method=extraction_method,
                source_quote=source_quote,
                source_row_snapshot=source_row_snapshot,
                applied_rule_id=applied_rule_id,
            )
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
            deterministic_match = extraction_method == "local_table_parser" and confidence >= 0.85 and evidence_status != "insufficient"
            db.execute(
                """INSERT INTO candidate_cells
                   (cell_id, candidate_record_id, header_id, target_header, target_field, target_unit,
                    value, original_value, original_field, original_unit, confidence, risk_level,
                    mapping_status, applied_rule_id, extraction_method, source_label, source_quote,
                    source_row_snapshot, evidence_status, evidence_reason,
                    element_id, page_number, bbox_json, alternatives_json,
                    review_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)""",
                (
                    cell_id, record_id, target["header_id"], target["display_header"], target["canonical_field"], target["target_unit"],
                    str(raw_value), str(raw_value), original_field, target["target_unit"], confidence,
                    "high" if evidence_status == "insufficient" else "low" if confidence >= 0.85 else "medium",
                    "confirmed" if deterministic_match else "suggested",
                    applied_rule_id, extraction_method, self._source_label(element), source_quote,
                    json.dumps(source_row_snapshot, ensure_ascii=False), evidence_status, evidence_reason,
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

    def _source_label(self, element: dict[str, Any]) -> str:
        kind = {"table": "表格", "paragraph": "段落", "figure": "图片"}.get(str(element.get("element_type") or ""), "资源")
        page = element.get("page_number")
        return f"{kind} {element.get('element_id', '')}" + (f" · p{page}" if page else "")

    def _cell_evidence_status(
        self,
        element: dict[str, Any],
        target: dict[str, Any],
        original_field: str,
        value: str,
        extraction_method: str,
        source_quote: str,
        source_row_snapshot: dict[str, Any],
        applied_rule_id: str = "",
    ) -> tuple[str, str]:
        raw_table_text = json.dumps(element.get("raw_table") or {}, ensure_ascii=False)
        row_text = json.dumps(source_row_snapshot or {}, ensure_ascii=False)
        haystack = " ".join([
            str(element.get("caption") or ""),
            str(element.get("text_content") or ""),
            str(element.get("context_text") or ""),
            raw_table_text,
            row_text,
            source_quote,
            original_field,
        ])
        haystack_norm = self._norm(haystack)
        value_text = str(value or "").strip()
        target_text = " ".join([str(target.get("display_header") or ""), str(target.get("canonical_field") or "")])
        target_norm = self._norm(target_text)
        original_norm = self._norm(original_field)
        quote_ok = bool(source_quote and source_quote.strip() and self._norm(source_quote) in haystack_norm)
        row_ok = bool(source_row_snapshot and original_field in source_row_snapshot)
        value_ok = bool(value_text and self._norm(value_text) in haystack_norm)
        field_ok = bool(applied_rule_id or original_norm in target_norm or target_norm in original_norm or original_norm in haystack_norm)
        if re.search(r"^ts(?:\s*%|$)|totalsul(?:f|ph)ur", target_text, flags=re.I):
            sulfur_hit = bool(re.search(r"\bTS\b|total\s+sulf(?:u|ph)r", haystack, flags=re.I))
            if not sulfur_hit and not applied_rule_id and self._norm(original_field) != "ts":
                return "insufficient", "目标为 TS%，但来源资源中未发现 TS/total sulfur 或已确认规则"
        if extraction_method.startswith("llm"):
            if quote_ok and value_ok:
                return "complete", "LLM 返回的证据片段和值可在当前资源中定位"
            return "insufficient", "LLM 返回值缺少可定位的 source_quote 或值无法在当前资源中验证"
        if extraction_method == "local_table_parser":
            if row_ok:
                return "complete" if (field_ok or applied_rule_id) else "weak", "本地表格解析，已保存原始行快照"
            if value_ok:
                return "weak", "本地表格文本位置推断，需人工确认"
            return "insufficient", "本地表格解析未能保留可验证行证据"
        if extraction_method == "manual_image":
            return "weak", "人工图像补值，需人工审核确认"
        return "weak", "来源证据有限，需人工确认"

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

    def _source_header_profile(self, source: str) -> dict[str, str]:
        """Separate a flattened multi-level header into field, context, and unit.

        Docling/PDF reconstruction intentionally keeps parent labels so users can
        inspect the original table semantics. Mapping should nevertheless target
        the leaf field, e.g. ``relative content of clay minerals(%) K`` -> ``K``.
        """
        value = self._clean_table_cell(source)
        parts = self._split_table_header_path(value)
        if len(parts) <= 1:
            match = re.match(r"^(.*?)(?:\s+)([^\s]+)$", value)
            if match and re.search(r"(?:\([^)]*(?:%|ppm|ppb|wt)\)|\b(?:ppm|ppb|wt%?)\b|[%％‰])", match.group(1), re.I):
                parts = [match.group(1).strip(), match.group(2).strip()]
        field_token = parts[-1] if parts else value
        group_context = " ".join(parts[:-1]).strip()
        unit_match = re.search(r"\(([^)]*(?:%|ppm|ppb|wt|‰)[^)]*)\)", group_context or value, re.I)
        if unit_match:
            detected_unit = unit_match.group(1).strip()
        elif re.search(r"[%％]", group_context or value):
            detected_unit = "%"
        elif "‰" in (group_context or value):
            detected_unit = "‰"
        else:
            detected_unit = ""
        return {
            "field_token": field_token.strip(),
            "group_context": group_context,
            "detected_unit": detected_unit,
        }

    def _match_source_header(
        self,
        source: str,
        targets: list[dict[str, Any]],
        rules: dict[str, str],
    ) -> dict[str, Any] | None:
        norm = self._norm(source)
        rule_target = rules.get(norm)
        target = next((h for h in targets if h["display_header"] == rule_target), None) if rule_target else None
        if target:
            return target
        target = next((h for h in targets if norm in {self._norm(h["canonical_field"]), self._norm(h["display_header"])}), None)
        if target:
            return target

        # Prefer a precise leaf-field match before any broad substring match.
        # This is what makes "... clay minerals(%) K" map to target "K".
        profile = self._source_header_profile(source)
        leaf_norm = self._norm(profile["field_token"])
        if leaf_norm:
            target = next((
                h for h in targets
                if leaf_norm in {self._norm(h["canonical_field"]), self._norm(h["display_header"])}
            ), None)
            if target:
                return target

        target = next((
            h for h in targets
            if len(self._norm(h["canonical_field"])) >= 2 and self._norm(h["canonical_field"]) in norm
        ), None)
        if target:
            return target
        return next((
            h for h in targets
            if len(self._norm(str(h["display_header"]).split()[0])) >= 2
            and self._norm(str(h["display_header"]).split()[0]) in norm
        ), None)

    def _map_source_headers(self, source_headers: list[str], targets: list[dict[str, Any]], alias_rules: dict[str, str] | None = None) -> list[tuple[str, str]]:
        rules = {**self._active_alias_rules(targets), **(alias_rules or {})}
        mapped = []
        for source in source_headers:
            target = self._match_source_header(source, targets, rules)
            mapped.append((source, target["display_header"] if target else source))
        return mapped

    def _active_alias_rules(self, targets: list[dict[str, Any]]) -> dict[str, str]:
        # Built-in conservative aliases plus user rules loaded from DB by target
        rules = {"sample": "SampleID", "samples": "SampleID", "sampleno": "SampleID", "samplenumber": "SampleID", "samplename": "SampleID", "sampleid": "SampleID"}
        available = {h["display_header"] for h in targets}
        target_by_norm = {
            self._norm(h["display_header"]): h["display_header"] for h in targets
        } | {
            self._norm(h["canonical_field"]): h["display_header"] for h in targets
        }
        sample_header = self._sample_header(targets)
        rules = {key: sample_header for key in rules}
        return {key: value for key, value in rules.items() if value in available}

    def _pre_extraction_alias_rules(self, project_id: str, article_id: str, targets: list[dict[str, Any]]) -> dict[str, str]:
        available = {h["display_header"] for h in targets}
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT pattern, target_header, target_field FROM learned_extraction_rules
                   WHERE project_id = ? AND (article_id = ? OR article_id IS NULL OR article_id = '')
                     AND review_status = 'confirmed' AND COALESCE(enabled, 1) = 1
                     AND rule_type IN ('source_alias', 'column_alias', 'sample_identifier')""",
                (project_id, article_id),
            )
            rules = {}
            for row in rows:
                target = row["target_header"] or row["target_field"] or ""
                target = target if target in available else target_by_norm.get(self._norm(target), "")
                if target and row["pattern"]:
                    rules[self._norm(row["pattern"])] = target
            return rules
        finally:
            db.close()

    def _pre_extraction_alias_rule_ids(self, project_id: str, article_id: str, targets: list[dict[str, Any]]) -> dict[str, str]:
        available = {h["display_header"] for h in targets}
        target_by_norm = {
            self._norm(h["display_header"]): h["display_header"] for h in targets
        } | {
            self._norm(h["canonical_field"]): h["display_header"] for h in targets
        }
        db = self.pm.get_database(project_id)
        try:
            rows = db.fetch_all(
                """SELECT rule_id, pattern, target_header, target_field FROM learned_extraction_rules
                   WHERE project_id = ? AND (article_id = ? OR article_id IS NULL OR article_id = '')
                     AND review_status = 'confirmed' AND COALESCE(enabled, 1) = 1
                     AND rule_type IN ('source_alias', 'column_alias', 'sample_identifier')""",
                (project_id, article_id),
            )
            rule_ids = {}
            for row in rows:
                target = row["target_header"] or row["target_field"] or ""
                target = target if target in available else target_by_norm.get(self._norm(target), "")
                if target and row["pattern"]:
                    rule_ids[self._norm(row["pattern"])] = row["rule_id"]
            return rule_ids
        finally:
            db.close()

    def _extraction_memory(self, project_id: str, article_id: str) -> dict[str, Any]:
        rows = self.rule_memory(project_id, article_id)["extraction"]
        enabled = [row for row in rows if row.get("enabled", True)]
        compact_rules = []
        for row in enabled:
            compact_rules.append({
                "rule_id": row["rule_id"],
                "rule_type": row.get("rule_type", ""),
                "pattern": row.get("pattern", ""),
                "target_header": row.get("target_header", ""),
                "target_field": row.get("target_field", ""),
                "scope": row.get("scope", ""),
                "element_id": row.get("element_id", ""),
                "evidence": str(row.get("evidence") or "")[:500],
            })
        return {
            "snapshot_id": datetime.now().isoformat(),
            "rules": compact_rules,
        }

    def _sample_identifier_patterns(self, extraction_memory: dict[str, Any]) -> list[str]:
        patterns = []
        for rule in extraction_memory.get("rules", []):
            if rule.get("rule_type") == "sample_identifier" and rule.get("pattern"):
                patterns.append(str(rule["pattern"]))
        return patterns

    def _sample_matches(self, text: str, sample_patterns: list[str] | None = None) -> list[re.Match[str]]:
        patterns = [r"\b([A-Za-z]{1,8}\d{0,3}[-_][A-Za-z0-9-]{1,12})\b"]
        patterns.extend(sample_patterns or [])
        matches: list[re.Match[str]] = []
        for pattern in patterns:
            try:
                matches.extend(list(re.finditer(pattern, text)))
            except re.error:
                escaped = re.escape(pattern).replace(r"\*", r"[A-Za-z0-9_-]*")
                matches.extend(list(re.finditer(escaped, text)))
        seen = set()
        unique = []
        for match in sorted(matches, key=lambda item: item.start()):
            key = (match.start(), match.group(1) if match.groups() else match.group(0))
            if key not in seen:
                seen.add(key)
                unique.append(match)
        return unique

    def _extract_sample_ids(self, text: str, sample_patterns: list[str] | None = None) -> list[str]:
        values = []
        for match in self._sample_matches(text, sample_patterns):
            values.append(match.group(1) if match.groups() else match.group(0))
        return values

    def _resource_excluded_by_memory(self, element: dict[str, Any], extraction_memory: dict[str, Any]) -> bool:
        text = " ".join([
            str(element.get("section_path") or ""),
            str(element.get("caption") or ""),
            str(element.get("text_content") or ""),
        ]).lower()
        for rule in extraction_memory.get("rules", []):
            if rule.get("rule_type") != "resource_exclude":
                continue
            if rule.get("element_id") and rule["element_id"] == element.get("element_id"):
                return True
            pattern = str(rule.get("pattern") or "").lower().strip()
            if pattern and pattern in text:
                return True
        return False

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
        return self._score_resource("paragraph", text, "", matched, {}).score

    def _score_resource(
        self,
        element_type: str,
        text: str,
        caption: str,
        matched: list[str],
        raw_table: dict[str, Any],
        section_path: str = "",
        user_labels: list[str] | None = None,
    ):
        return self.scoring.score(
            element_type=element_type,
            text=text,
            caption=caption,
            section_path=section_path,
            matched_headers=matched,
            raw_table=raw_table,
            user_labels=user_labels or [],
        )

    def _element_labels(self, db, project_id: str, article_id: str, element_id: str) -> list[str]:
        rows = db.fetch_all(
            """SELECT target_header FROM teaching_events
               WHERE project_id = ? AND article_id = ? AND row_id = ? AND target_field LIKE 'label:%' AND status = 'confirmed'""",
            (project_id, article_id, element_id),
        )
        return [row["target_header"] for row in rows]

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
        page_spans: list[dict[str, Any]] | None = None,
        reading_order: int = 0,
        section_path: str = "",
        source_backend: str = "",
        merge_reason: str = "",
        score_reasons: list[str] | None = None,
    ) -> str:
        if not page_spans:
            page_spans = [{"page_number": page_number, "bbox": bbox, "role": element_type}]
        digest = hashlib.sha256(f"{resource_id}|{element_type}|{page_spans}|{text[:600]}".encode()).hexdigest()[:20]
        existing = None if force_new else db.fetch_one(
            "SELECT element_id FROM document_elements WHERE resource_id = ? AND content_hash = ?",
            (resource_id, digest),
        )
        now = datetime.now().isoformat()
        if existing:
            db.execute(
                """UPDATE document_elements SET element_type = ?, page_number = ?, bbox_json = ?,
                   page_spans_json = ?, text_content = ?, context_text = ?, caption = ?, preview_path = ?,
                   raw_table_json = ?, matched_headers_json = ?, relevance_score = ?, score_reasons_json = ?,
                   reading_order = ?, section_path = ?, source_backend = ?, merge_reason = ?,
                   parser_version = ?, status = 'candidate', updated_at = ?
                   WHERE element_id = ?""",
                (
                    element_type, page_number, json.dumps(bbox), json.dumps(page_spans, ensure_ascii=False),
                    text[:12000], context[:20000],
                    caption[:3000], preview_path, json.dumps(raw_table, ensure_ascii=False),
                    json.dumps(matched, ensure_ascii=False), relevance, json.dumps(score_reasons or [], ensure_ascii=False),
                    reading_order, section_path[:500], source_backend[:80], merge_reason[:200],
                    parser_version or self.PARSER_VERSION, now, existing["element_id"],
                ),
            )
            return existing["element_id"]
        element_id = self._next_id(db, "document_elements", "element_id", "ELM", 7)
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                bbox_json, page_spans_json, text_content, context_text, caption, preview_path, raw_table_json,
                matched_headers_json, relevance_score, score_reasons_json, reading_order, section_path, source_backend,
                merge_reason, content_hash, parser_version, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)""",
            (
                element_id, project_id, article_id, resource_id, element_type, page_number,
                json.dumps(bbox), json.dumps(page_spans, ensure_ascii=False),
                text[:12000], context[:20000], caption[:3000], preview_path,
                json.dumps(raw_table, ensure_ascii=False), json.dumps(matched, ensure_ascii=False),
                relevance, json.dumps(score_reasons or [], ensure_ascii=False), reading_order, section_path[:500], source_backend[:80],
                merge_reason[:200], digest, parser_version or self.PARSER_VERSION, now, now,
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
        page_spans = json.loads(row.pop("page_spans_json", "[]") or "[]")
        if isinstance(page_spans, dict):
            page_spans = [page_spans]
        clean_spans: list[dict[str, Any]] = []
        for span in page_spans if isinstance(page_spans, list) else []:
            if not isinstance(span, dict):
                continue
            try:
                bbox = [float(value) for value in span.get("bbox", [])]
                page_number = int(span.get("page_number") or 0)
            except (TypeError, ValueError):
                continue
            if page_number > 0 and len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                clean_spans.append({
                    "page_number": page_number,
                    "bbox": bbox,
                    "role": span.get("role") or row.get("element_type") or "evidence",
                })
        page_spans = clean_spans
        if not page_spans and row.get("page_number") and row["bbox"]:
            page_spans = [{"page_number": row["page_number"], "bbox": row["bbox"], "role": row.get("element_type") or "evidence"}]
        row["page_spans"] = page_spans
        row["raw_table"] = json.loads(row.pop("raw_table_json") or "{}")
        row["matched_headers"] = json.loads(row.pop("matched_headers_json") or "[]")
        row["score_reasons"] = json.loads(row.pop("score_reasons_json", "[]") or "[]")
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

    def _bbox_iou(self, a: list[float], b: list[float]) -> float:
        if len(a) != 4 or len(b) != 4:
            return 0.0
        x0, y0 = max(a[0], b[0]), max(a[1], b[1])
        x1, y1 = min(a[2], b[2]), min(a[3], b[3])
        if x1 <= x0 or y1 <= y0:
            return 0.0
        inter = (x1 - x0) * (y1 - y0)
        return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-9)

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
