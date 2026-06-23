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
