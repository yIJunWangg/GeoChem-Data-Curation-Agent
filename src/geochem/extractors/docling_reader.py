"""Docling-based PDF reader with paragraph span merging.

Docling gives us useful layout items, but scientific PDFs often split one
natural paragraph into multiple same-column or cross-column text items.  This
module keeps Docling's reading order, records every provenance span, and then
merges conservative continuation segments before the workbench persists them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..curation.resource_scoring import ResourceScoringEngine
from ..core.logging_config import get_logger

logger = get_logger("extractors.docling_reader")

PageSpan = dict[str, Any]


@dataclass
class DocBlock:
    block_type: str          # "table" | "figure" | "paragraph"
    text: str
    page_number: int         # Primary 1-indexed page
    bbox: list[float]        # Primary [x0, y0, x1, y1] normalized [0,1] TOPLEFT
    preview_path: str = ""
    table_data: dict | None = None
    caption: str = ""
    matched_headers: list[str] = field(default_factory=list)
    relevance_score: float = 0.5
    score_reasons: list[str] = field(default_factory=list)
    page_spans: list[PageSpan] = field(default_factory=list)
    reading_order: int = 0
    section_path: str = ""
    merge_reason: str = ""
    source_backend: str = "docling"


class DoclingReader:
    """Read PDFs using Docling. All coordinates normalized to [0,1] TOPLEFT."""

    def read(
        self,
        file_path: Path,
        schema_fields: list[str] | None = None,
        max_pages: int = 100,
    ) -> list[DocBlock]:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        opts = PdfPipelineOptions()
        opts.do_ocr = False
        opts.do_table_structure = True
        opts.generate_picture_images = True
        opts.images_scale = 2.0

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        result = converter.convert(str(file_path), max_num_pages=max_pages)
        doc = result.document

        out_dir = file_path.parent / ".cache" / "docling"
        out_dir.mkdir(parents=True, exist_ok=True)

        blocks: list[DocBlock] = []
        section_stack: list[str] = []
        schema_fields = schema_fields or []
        for order, (element, level) in enumerate(doc.iterate_items(), start=1):
            try:
                if isinstance(element, SectionHeaderItem):
                    heading = self._text_of(element)
                    if heading:
                        depth = max(1, int(level or 1))
                        section_stack = section_stack[: depth - 1] + [heading]
                    continue
                section_path = " > ".join(section_stack)
                if isinstance(element, TableItem):
                    block = self._make_table(doc, element, out_dir, order, section_path)
                elif isinstance(element, PictureItem):
                    block = self._make_figure(doc, element, out_dir, order, section_path)
                elif isinstance(element, TextItem):
                    block = self._make_text(doc, element, order, section_path)
                else:
                    continue
                if block:
                    self._decorate(block, schema_fields)
                    blocks.append(block)
            except Exception as exc:
                logger.warning(f"Docling element error: {exc}")

        blocks = self._dedup(blocks)
        blocks = self.merge_paragraphs(blocks, schema_fields)
        blocks.sort(key=lambda block: block.reading_order)
        logger.info(f"DoclingReader: {len(blocks)} blocks from {file_path.name}")
        return blocks

    def merge_paragraphs(self, blocks: list[DocBlock], schema_fields: list[str] | None = None) -> list[DocBlock]:
        """Merge same-column, cross-column and cross-page paragraph continuations."""
        schema_fields = schema_fields or []
        merged: list[DocBlock] = []
        for block in sorted(blocks, key=lambda item: item.reading_order):
            previous = merged[-1] if merged else None
            if previous and self._should_merge_paragraph(previous, block):
                self._merge_into(previous, block, schema_fields)
            else:
                merged.append(block)
        return merged

    def _page_size(self, doc, page_no: int) -> tuple[float, float]:
        page = doc.pages.get(page_no)
        if page and getattr(page, "size", None):
            return page.size.width, page.size.height
        return 595.0, 792.0

    def _spans(self, element, doc, role: str) -> list[PageSpan]:
        if not getattr(element, "prov", None):
            return [{"page_number": 1, "bbox": [0.0, 0.0, 1.0, 1.0], "role": role}]
        spans: list[PageSpan] = []
        for prov in element.prov:
            page_no = int(getattr(prov, "page_no", 1) or 1)
            bbox = getattr(prov, "bbox", None)
            if not bbox:
                continue
            pw, ph = self._page_size(doc, page_no)
            origin = str(getattr(bbox, "coord_origin", ""))
            if "BOTTOM" in origin:
                x0, y0 = bbox.l / pw, (ph - bbox.t) / ph
                x1, y1 = bbox.r / pw, (ph - bbox.b) / ph
            else:
                x0, y0 = bbox.l / pw, bbox.t / ph
                x1, y1 = bbox.r / pw, bbox.b / ph
            if y0 > y1:
                y0, y1 = y1, y0
            spans.append({
                "page_number": page_no,
                "bbox": [max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)],
                "role": role,
            })
        return spans or [{"page_number": 1, "bbox": [0.0, 0.0, 1.0, 1.0], "role": role}]

    def _primary(self, spans: list[PageSpan]) -> tuple[int, list[float]]:
        span = spans[0] if spans else {"page_number": 1, "bbox": [0.0, 0.0, 1.0, 1.0]}
        return int(span["page_number"]), list(span["bbox"])

    def _make_table(self, doc, element, out_dir: Path, order: int, section_path: str) -> DocBlock | None:
        spans = self._spans(element, doc, "table")
        page_no, bbox = self._primary(spans)
        table_data: dict[str, Any] = {"headers": [], "rows": [], "body_text": ""}
        try:
            df = element.export_to_dataframe(doc=doc)
            headers = [str(column) for column in df.columns]
            rows = [[str(cell) if cell is not None else "" for cell in row] for row in df.values.tolist()]
            table_data = {"headers": headers, "rows": rows, "body_text": self._table_body_text(headers, rows)}
        except Exception:
            pass
        preview_path = ""
        try:
            image = element.get_image(doc)
            key = hashlib.md5(f"{page_no}:{bbox}".encode()).hexdigest()[:10]
            preview_path = str(out_dir / f"table_p{page_no}_{key}.png")
            image.save(preview_path, "PNG")
        except Exception:
            pass
        caption = self._caption(element, doc)
        text = " ".join(value for value in [caption, " ".join(table_data.get("headers", [])), table_data.get("body_text", "")] if value)
        return DocBlock(
            block_type="table", text=text, page_number=page_no, bbox=bbox,
            preview_path=preview_path, table_data=table_data, caption=caption,
            page_spans=spans, reading_order=order, section_path=section_path,
            relevance_score=0.65,
        )

    def _make_figure(self, doc, element, out_dir: Path, order: int, section_path: str) -> DocBlock | None:
        spans = self._spans(element, doc, "figure")
        page_no, bbox = self._primary(spans)
        preview_path = ""
        try:
            image = element.get_image(doc)
            key = hashlib.md5(f"{page_no}:{bbox}".encode()).hexdigest()[:10]
            preview_path = str(out_dir / f"fig_p{page_no}_{key}.png")
            image.save(preview_path, "PNG")
        except Exception:
            pass
        caption = self._caption(element, doc)
        return DocBlock(
            block_type="figure", text=caption or f"Figure (page {page_no})",
            page_number=page_no, bbox=bbox, preview_path=preview_path, caption=caption,
            page_spans=spans, reading_order=order, section_path=section_path,
            relevance_score=0.45,
        )

    def _make_text(self, doc, element, order: int, section_path: str) -> DocBlock | None:
        text = self._text_of(element)
        if not text or len(text) < 15:
            return None
        spans = self._spans(element, doc, "text")
        page_no, bbox = self._primary(spans)
        return DocBlock(
            block_type="paragraph", text=text, page_number=page_no, bbox=bbox,
            page_spans=spans, reading_order=order, section_path=section_path,
            relevance_score=0.35,
        )

    def _text_of(self, element) -> str:
        return " ".join(str(getattr(element, "text", "") or "").split())

    def _caption(self, element, doc) -> str:
        try:
            return " ".join((element.caption_text(doc) or "").split())
        except Exception:
            return ""

    def _table_body_text(self, headers: list[str], rows: list[list[str]]) -> str:
        lines = ["\t".join(headers)] if headers else []
        lines.extend("\t".join(row) for row in rows[:80])
        return "\n".join(lines)

    def _dedup(self, blocks: list[DocBlock]) -> list[DocBlock]:
        result: list[DocBlock] = []
        for block in sorted(blocks, key=lambda item: item.reading_order):
            if any(
                block.block_type == other.block_type
                and self._same_page_overlap(block, other) > 0.72
                and len(other.text) >= len(block.text)
                for other in result
            ):
                continue
            result.append(block)
        return result

    def _should_merge_paragraph(self, previous: DocBlock, block: DocBlock) -> bool:
        if previous.block_type != "paragraph" or block.block_type != "paragraph":
            return False
        if previous.section_path != block.section_path:
            return False
        if block.reading_order - previous.reading_order > 4:
            return False
        if self._looks_like_heading(block.text):
            return False

        same_page = block.page_number == self._last_page(previous)
        next_page = block.page_number == self._last_page(previous) + 1
        if same_page and self._same_column(previous, block):
            return self._vertical_gap(previous, block) < 0.08 and not self._hard_paragraph_end(previous.text)
        if same_page and self._cross_column_continuation(previous, block):
            return not self._hard_paragraph_end(previous.text)
        if next_page:
            return (
                abs(previous.bbox[0] - block.bbox[0]) < 0.22
                and (self._ends_hyphen(previous.text) or not self._ends_sentence(previous.text))
            )
        return False

    def _merge_into(self, previous: DocBlock, block: DocBlock, schema_fields: list[str]) -> None:
        joiner = "" if self._ends_hyphen(previous.text) else " "
        previous.text = re.sub(r"[-‐‑‒–—]\s*$", "", previous.text.rstrip()) + joiner + block.text.lstrip()
        previous.page_spans.extend(block.page_spans)
        previous.merge_reason = self._merge_reason(previous, block)
        self._decorate(previous, schema_fields)

    def _same_column(self, previous: DocBlock, block: DocBlock) -> bool:
        return abs(previous.bbox[0] - block.bbox[0]) < 0.08 and abs(previous.bbox[2] - block.bbox[2]) < 0.12

    def _cross_column_continuation(self, previous: DocBlock, block: DocBlock) -> bool:
        return (
            previous.bbox[0] < 0.48
            and block.bbox[0] > 0.45
            and previous.bbox[1] > 0.45
            and block.bbox[1] < 0.45
        )

    def _vertical_gap(self, previous: DocBlock, block: DocBlock) -> float:
        return max(0.0, block.bbox[1] - previous.bbox[3])

    def _hard_paragraph_end(self, text: str) -> bool:
        return self._ends_sentence(text) and not self._ends_hyphen(text)

    def _ends_sentence(self, text: str) -> bool:
        return bool(re.search(r"[.!?。！？]\s*(?:\]|\))?\s*$", text[-80:]))

    def _ends_hyphen(self, text: str) -> bool:
        return bool(re.search(r"[-‐‑‒–—]\s*$", text.rstrip()))

    def _looks_like_heading(self, text: str) -> bool:
        value = text.strip()
        if len(value) > 140:
            return False
        return bool(re.match(r"^\d+(?:\.\d+)*\s+[A-Z]", value) or (value.isupper() and len(value.split()) <= 12))

    def _merge_reason(self, previous: DocBlock, block: DocBlock) -> str:
        if block.page_number == self._last_page(previous) + 1:
            return "cross_page_paragraph"
        if self._cross_column_continuation(previous, block):
            return "cross_column_paragraph"
        return "same_column_paragraph"

    def _last_page(self, block: DocBlock) -> int:
        return max(int(span["page_number"]) for span in block.page_spans) if block.page_spans else block.page_number

    def _same_page_overlap(self, left: DocBlock, right: DocBlock) -> float:
        score = 0.0
        for left_span in left.page_spans:
            for right_span in right.page_spans:
                if left_span["page_number"] == right_span["page_number"]:
                    score = max(score, self._iou(left_span["bbox"], right_span["bbox"]))
        return score

    def _decorate(self, block: DocBlock, schema_fields: list[str]) -> None:
        haystack = " ".join([block.text or "", block.caption or "", " ".join((block.table_data or {}).get("headers", []))])
        block.matched_headers = self._matched_headers(haystack, schema_fields)
        result = self.scoring.score(
            element_type=block.block_type,
            text=block.text,
            caption=block.caption,
            section_path=block.section_path,
            matched_headers=block.matched_headers,
            raw_table=block.table_data or {},
        )
        block.relevance_score = result.score
        block.score_reasons = result.reasons

    def _matched_headers(self, text: str, schema_fields: list[str]) -> list[str]:
        normalized = self._norm(text)
        matches: list[str] = []
        for field in schema_fields:
            field_text = str(field or "").strip()
            literal = re.sub(r"\s*(?:\(|（)?\s*(?:ppm|ppb|wt\s*%|%|‰|mg/kg|ug/g|m)\s*(?:\)|）)?\s*$", "", field_text, flags=re.I).strip()
            candidates = {field_text.lower(), literal.lower(), self._norm(field_text), self._norm(literal)}
            if any(candidate and (candidate in text.lower() or (len(candidate) >= 2 and candidate in normalized)) for candidate in candidates):
                matches.append(field_text)
        return matches[:40]

    def _norm(self, value: str) -> str:
        return re.sub(r"[^a-z0-9δ]+", "", str(value).lower())

    def _iou(self, a: list[float], b: list[float]) -> float:
        x0, y0 = max(a[0], b[0]), max(a[1], b[1])
        x1, y1 = min(a[2], b[2]), min(a[3], b[3])
        if x1 <= x0 or y1 <= y0:
            return 0.0
        inter = (x1 - x0) * (y1 - y0)
        return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-9)
    def __init__(self):
        self.scoring = ResourceScoringEngine()
