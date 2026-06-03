"""CSV file reader using pandas."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..core.exceptions import ExtractionError
from ..core.logging_config import get_logger
from .base_reader import BaseReader, RawContent

logger = get_logger("extractors.csv_reader")


class CsvReader(BaseReader):
    """Read CSV files using pandas."""

    def read(self, file_path: Path, encoding: str | None = None, **kwargs) -> list[RawContent]:
        """Read CSV file and return single RawContent.

        Args:
            file_path: Path to CSV file.
            encoding: File encoding. None = auto-detect.

        Returns:
            List with single RawContent.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        # Try multiple encodings
        encodings = [encoding] if encoding else ["utf-8", "gbk", "gb2312", "latin-1"]
        df = None
        for enc in encodings:
            try:
                df = pd.read_csv(file_path, encoding=enc, dtype=str, keep_default_na=False)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception as e:
                raise ExtractionError(f"Cannot read CSV file: {e}")

        if df is None:
            raise ExtractionError(f"Cannot decode CSV file with any encoding: {encodings}")

        if df.empty:
            logger.warning(f"CSV file is empty: {file_path.name}")
            return []

        raw_headers = [str(h).strip() for h in df.columns.tolist()]
        # Remove empty trailing headers
        while raw_headers and not raw_headers[-1]:
            raw_headers.pop()

        if not raw_headers:
            return []

        # Build data rows
        data_rows = []
        for _, row in df.iterrows():
            cells = [str(row[h]) if h in row.index else "" for h in raw_headers]
            data_rows.append(cells)

        # Remove empty rows
        data_rows = [r for r in data_rows if any(c.strip() for c in r)]

        if not data_rows:
            return []

        # Convert to markdown
        markdown = self._to_markdown(raw_headers, data_rows)

        return [RawContent(
            text=markdown,
            headers=raw_headers,
            row_count=len(data_rows),
            source_type="csv",
            raw_headers=raw_headers,
            raw_rows=data_rows,
            metadata={"file_name": file_path.name, "encoding": enc},
        )]

    def _to_markdown(self, headers: list[str], rows: list[list]) -> str:
        """Convert table to markdown format."""
        lines = []
        header_line = "| " + " | ".join(headers) + " |"
        lines.append(header_line)
        sep_line = "| " + " | ".join("---" for _ in headers) + " |"
        lines.append(sep_line)
        for row in rows:
            line = "| " + " | ".join(c.strip() for c in row) + " |"
            lines.append(line)
        return "\n".join(lines)
