"""Base reader interface and RawContent dataclass for file extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RawContent:
    """Content extracted from a file by a reader.

    This is the intermediate representation between file readers and LLM extractor.
    """
    text: str                               # Full table as markdown/CSV text
    headers: list[str]                      # Cleaned column headers (after unit extraction)
    row_count: int                          # Number of data rows
    source_type: str                        # "excel", "csv", "pdf", "html"
    sheet_name: str | None = None           # Excel sheet name
    page_number: int | None = None          # PDF page number
    title: str = ""                         # Table/section title
    raw_headers: list[str] = field(default_factory=list)  # Original headers before preprocessing
    header_units: dict[str, str] = field(default_factory=dict)  # {clean_header: unit}
    raw_rows: list[list] = field(default_factory=list)  # Original data rows (list of cell value lists)
    metadata: dict[str, Any] = field(default_factory=dict)  # Extra info


class BaseReader(ABC):
    """Abstract base class for file readers."""

    @abstractmethod
    def read(self, file_path: Path, **kwargs) -> list[RawContent]:
        """Read a file and return one or more RawContent.

        One RawContent per table/sheet/page. Excel files with multiple sheets
        return multiple RawContent objects.

        Args:
            file_path: Path to the file to read.
            **kwargs: Reader-specific options (sheet_names, pages, encoding, etc.)

        Returns:
            List of RawContent objects, one per extractable table.

        Raises:
            FileNotFoundError: File does not exist.
            ExtractionError: File cannot be read or parsed.
        """
