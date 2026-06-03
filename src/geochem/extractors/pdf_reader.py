"""PDF file reader using pdfplumber for tables and PyMuPDF for text."""

from __future__ import annotations

from pathlib import Path

from ..core.exceptions import ExtractionError
from ..core.logging_config import get_logger
from .base_reader import BaseReader, RawContent

logger = get_logger("extractors.pdf_reader")


class PdfReader(BaseReader):
    """Read PDF files, extracting tables and text."""

    def read(self, file_path: Path, pages: list[int] | None = None, **kwargs) -> list[RawContent]:
        """Read PDF and extract tables.

        Args:
            file_path: Path to PDF file.
            pages: Specific page numbers (0-indexed). None = all pages.

        Returns:
            List of RawContent, one per table found.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        results = []

        # Try pdfplumber for table extraction
        try:
            results = self._extract_tables(file_path, pages)
        except Exception as e:
            logger.warning(f"pdfplumber table extraction failed: {e}")

        # If no tables found, extract full text as fallback
        if not results:
            try:
                text = self._extract_text(file_path, pages)
                if text and len(text.strip()) > 50:
                    results.append(RawContent(
                        text=text,
                        headers=[],
                        row_count=0,
                        source_type="pdf",
                        title=file_path.stem,
                        metadata={"file_name": file_path.name, "extraction_method": "text"},
                    ))
            except Exception as e:
                logger.warning(f"PyMuPDF text extraction failed: {e}")

        if not results:
            logger.warning(f"No content extracted from {file_path.name}")

        return results

    def _extract_tables(self, file_path: Path, pages: list[int] | None) -> list[RawContent]:
        """Extract tables using pdfplumber."""
        import pdfplumber

        results = []
        with pdfplumber.open(str(file_path)) as pdf:
            total_pages = len(pdf.pages)
            target_pages = pages if pages else list(range(total_pages))

            for page_num in target_pages:
                if page_num >= total_pages:
                    continue

                page = pdf.pages[page_num]
                tables = page.extract_tables()

                for table_idx, table in enumerate(tables):
                    if not table or len(table) < 2:
                        continue

                    # First row as headers
                    raw_headers = [str(h).strip() if h else "" for h in table[0]]
                    while raw_headers and not raw_headers[-1]:
                        raw_headers.pop()

                    if not raw_headers:
                        continue

                    # Data rows
                    data_rows = []
                    for row in table[1:]:
                        cells = [str(c).strip() if c else "" for c in row[:len(raw_headers)]]
                        data_rows.append(cells)

                    # Remove empty rows
                    data_rows = [r for r in data_rows if any(c.strip() for c in r)]

                    if not data_rows:
                        continue

                    markdown = self._to_markdown(raw_headers, data_rows)

                    results.append(RawContent(
                        text=markdown,
                        headers=raw_headers,
                        row_count=len(data_rows),
                        source_type="pdf",
                        page_number=page_num,
                        title=f"Table {table_idx + 1} (Page {page_num + 1})",
                        raw_headers=raw_headers,
                        raw_rows=data_rows,
                        metadata={"file_name": file_path.name},
                    ))

        return results

    def _extract_text(self, file_path: Path, pages: list[int] | None) -> str:
        """Extract full text using PyMuPDF."""
        import fitz

        doc = fitz.open(str(file_path))
        total_pages = len(doc)
        target_pages = pages if pages else list(range(total_pages))

        text_parts = []
        for page_num in target_pages:
            if page_num >= total_pages:
                continue
            page = doc[page_num]
            text_parts.append(page.get_text())

        doc.close()
        return "\n\n".join(text_parts)

    def _to_markdown(self, headers: list[str], rows: list[list]) -> str:
        """Convert table to markdown format."""
        lines = []
        header_line = "| " + " | ".join(headers) + " |"
        lines.append(header_line)
        sep_line = "| " + " | ".join("---" for _ in headers) + " |"
        lines.append(sep_line)
        for row in rows:
            line = "| " + " | ".join(c for c in row) + " |"
            lines.append(line)
        return "\n".join(lines)
