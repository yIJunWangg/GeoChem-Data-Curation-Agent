"""Docling-based PDF reader with correct coordinate handling."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging_config import get_logger

logger = get_logger("extractors.docling_reader")


@dataclass
class DocBlock:
    block_type: str          # "table" | "figure" | "paragraph" | "heading"
    text: str
    page_number: int         # 1-indexed
    bbox: list[float]        # [x0, y0, x1, y1] normalized [0,1] TOPLEFT
    preview_path: str = ""
    table_data: dict | None = None
    caption: str = ""
    matched_headers: list[str] = field(default_factory=list)
    relevance_score: float = 0.5


class DoclingReader:
    """Read PDFs using Docling. All coordinates normalized to [0,1] TOPLEFT."""

    def read(self, file_path: Path, schema_fields: list[str] | None = None,
             max_pages: int = 100) -> list[DocBlock]:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import TextItem, TableItem, PictureItem, SectionHeaderItem

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        opts = PdfPipelineOptions()
        opts.do_ocr = False  # PP-OCRv6 incompatible
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
        for element, level in doc.iterate_items():
            try:
                if isinstance(element, TableItem):
                    block = self._make_table(doc, element, out_dir)
                elif isinstance(element, PictureItem):
                    block = self._make_figure(doc, element, out_dir)
                elif isinstance(element, SectionHeaderItem):
                    block = self._make_text(doc, element, is_heading=True)
                elif isinstance(element, TextItem):
                    block = self._make_text(doc, element, is_heading=False)
                else:
                    continue
                if block:
                    blocks.append(block)
            except Exception as e:
                logger.warning(f"Docling element error: {e}")

        blocks = self._dedup(blocks)
        blocks.sort(key=lambda b: (b.page_number, b.bbox[1], b.bbox[0]))
        logger.info(f"DoclingReader: {len(blocks)} blocks from {file_path.name}")
        return blocks

    def _page_size(self, doc, page_no: int) -> tuple[float, float]:
        page = doc.pages.get(page_no)
        if page and hasattr(page, "size") and page.size:
            return page.size.width, page.size.height
        return 595.0, 792.0

    def _norm_bbox(self, element, doc) -> tuple[int, list[float]]:
        """Convert Docling provenance to (page_no, [x0,y0,x1,y1] normalized TOPLEFT)."""
        if not getattr(element, "prov", None):
            return 1, [0.0, 0.0, 1.0, 1.0]
        prov = element.prov[0]
        page_no = prov.page_no  # 1-indexed
        bbox = prov.bbox
        pw, ph = self._page_size(doc, page_no)

        origin = str(getattr(bbox, "coord_origin", ""))
        if "BOTTOM" in origin:
            # BOTTOMLEFT: l=left, t=top-from-bottom(high), r=right, b=bottom-from-bottom(low)
            # TOPLEFT:    x0=left, y0=top-from-top(low), x1=right, y1=bottom-from-top(high)
            # Convert: y_topLeft = page_height - y_bottomLeft
            x0 = bbox.l / pw
            y0 = (ph - bbox.t) / ph   # t is high in BOTTOMLEFT → low in TOPLEFT
            x1 = bbox.r / pw
            y1 = (ph - bbox.b) / ph   # b is low in BOTTOMLEFT → high in TOPLEFT
        else:
            x0, y0 = bbox.l / pw, bbox.t / ph
            x1, y1 = bbox.r / pw, bbox.b / ph

        # Ensure y0 < y1
        if y0 > y1:
            y0, y1 = y1, y0

        return page_no, [max(0, x0), max(0, y0), min(1, x1), min(1, y1)]

    def _make_table(self, doc, element, out_dir) -> DocBlock | None:
        page_no, bbox = self._norm_bbox(element, doc)
        table_data: dict[str, Any] = {"headers": [], "rows": []}
        try:
            df = element.export_to_dataframe(doc=doc)
            table_data = {
                "headers": [str(c) for c in df.columns],
                "rows": [[str(c) if c is not None else "" for c in row] for row in df.values.tolist()],
            }
        except Exception:
            pass
        preview_path = ""
        try:
            img = element.get_image(doc)
            key = hashlib.md5(f"{page_no}:{bbox}".encode()).hexdigest()[:10]
            preview_path = str(out_dir / f"table_p{page_no}_{key}.png")
            img.save(preview_path, "PNG")
        except Exception:
            pass
        caption = ""
        try:
            caption = element.caption_text(doc) or ""
        except Exception:
            pass
        text = caption or " ".join(table_data.get("headers", []))
        return DocBlock(
            block_type="table", text=text, page_number=page_no, bbox=bbox,
            preview_path=preview_path, table_data=table_data, caption=caption,
        )

    def _make_figure(self, doc, element, out_dir) -> DocBlock | None:
        page_no, bbox = self._norm_bbox(element, doc)
        preview_path = ""
        try:
            img = element.get_image(doc)
            key = hashlib.md5(f"{page_no}:{bbox}".encode()).hexdigest()[:10]
            preview_path = str(out_dir / f"fig_p{page_no}_{key}.png")
            img.save(preview_path, "PNG")
        except Exception:
            pass
        caption = ""
        try:
            caption = element.caption_text(doc) or ""
        except Exception:
            pass
        return DocBlock(
            block_type="figure", text=caption or f"Figure (page {page_no})",
            page_number=page_no, bbox=bbox, preview_path=preview_path, caption=caption,
        )

    def _make_text(self, doc, element, is_heading=False) -> DocBlock | None:
        text = element.text if hasattr(element, "text") else ""
        if not isinstance(text, str):
            text = str(text) if text else ""
        text = text.strip()
        if not text or len(text) < 15:
            return None
        page_no, bbox = self._norm_bbox(element, doc)
        return DocBlock(
            block_type="heading" if is_heading else "paragraph",
            text=text, page_number=page_no, bbox=bbox,
        )

    def _dedup(self, blocks: list[DocBlock]) -> list[DocBlock]:
        result = []
        for i, b in enumerate(blocks):
            skip = False
            for j, other in enumerate(blocks):
                if i == j or b.page_number != other.page_number:
                    continue
                if self._iou(b.bbox, other.bbox) > 0.7 and len(other.text) > len(b.text):
                    skip = True
                    break
            if not skip:
                result.append(b)
        return result

    def _iou(self, a: list[float], b: list[float]) -> float:
        x0, y0 = max(a[0], b[0]), max(a[1], b[1])
        x1, y1 = min(a[2], b[2]), min(a[3], b[3])
        if x1 <= x0 or y1 <= y0:
            return 0.0
        inter = (x1 - x0) * (y1 - y0)
        return inter / max((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter, 1e-9)
