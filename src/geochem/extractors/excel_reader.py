"""Excel file reader using openpyxl."""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl

from ..core.exceptions import ExtractionError
from ..core.logging_config import get_logger
from .base_reader import BaseReader, RawContent

logger = get_logger("extractors.excel_reader")

# Known unit patterns for header parsing
UNIT_PATTERNS = [
    re.compile(r"\(([^)]+)\)\s*$"),       # (wt%), (ppm), etc.
    re.compile(r"\n\s*(.+)$"),             # \nppm, \n%, etc.
    re.compile(r"/\s*([a-zA-Z%‰]+)$"),    # /m, /g, etc.
]

# Strings that look like units
KNOWN_UNITS = {
    "%", "‰", "ppm", "ppb", "ppt", "wt%", "ppmw",
    "mg/g", "mg/kg", "mg/l", "mg/L", "μg/g", "ug/g",
    "ng/g", "nmol/g", "mol%", "°C", "C",
    "m", "cm", "mm", "km",
    "g/cm3", "g/cm³", "kg/m3",
    "mg CO2/g TOC", "mg HC/g TOC", "mg HC/g Rock", "mg CO2/g Rock",
}


class ExcelReader(BaseReader):
    """Read Excel files (.xlsx/.xls) using openpyxl."""

    def read(self, file_path: Path, sheet_names: list[str] | None = None, **kwargs) -> list[RawContent]:
        """Read Excel file, one RawContent per sheet.

        Args:
            file_path: Path to Excel file.
            sheet_names: Specific sheets to read. None = all sheets.

        Returns:
            List of RawContent, one per sheet with data.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Excel file not found: {file_path}")

        try:
            wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
        except Exception as e:
            raise ExtractionError(f"Cannot open Excel file: {e}")

        results = []
        target_sheets = sheet_names or wb.sheetnames

        for sheet_name in target_sheets:
            if sheet_name not in wb.sheetnames:
                logger.warning(f"Sheet '{sheet_name}' not found in {file_path.name}")
                continue

            ws = wb[sheet_name]
            content = self._read_sheet(ws, sheet_name, file_path.name)
            if content:
                results.append(content)

        wb.close()

        if not results:
            logger.warning(f"No data found in {file_path.name}")

        return results

    def _read_sheet(self, ws, sheet_name: str, file_name: str) -> RawContent | None:
        """Read a single worksheet into RawContent."""
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row))

        if not rows:
            return None

        # First row is headers
        raw_headers = []
        for h in rows[0]:
            if h is None:
                raw_headers.append("")
            else:
                raw_headers.append(str(h).strip())

        # Skip empty trailing headers
        while raw_headers and not raw_headers[-1]:
            raw_headers.pop()

        if not raw_headers:
            return None

        # Trim data rows to match header count
        data_rows = []
        for row in rows[1:]:
            trimmed = list(row[:len(raw_headers)])
            data_rows.append(trimmed)

        # Remove completely empty rows
        data_rows = [r for r in data_rows if any(cell is not None and str(cell).strip() for cell in r)]

        if not data_rows:
            return None

        # Extract units from headers
        clean_headers = []
        header_units = {}
        for h in raw_headers:
            clean, unit = self._extract_header_unit(h)
            clean_headers.append(clean)
            if unit:
                header_units[clean] = unit

        # Convert to markdown
        markdown = self._to_markdown(clean_headers, data_rows)

        return RawContent(
            text=markdown,
            headers=clean_headers,
            row_count=len(data_rows),
            source_type="excel",
            sheet_name=sheet_name,
            title=sheet_name,
            raw_headers=raw_headers,
            header_units=header_units,
            raw_rows=data_rows,
            metadata={"file_name": file_name},
        )

    def _extract_header_unit(self, header: str) -> tuple[str, str]:
        """Split header into (clean_name, unit).

        Examples:
            'Al2O3(wt%)' → ('Al2O3', 'wt%')
            'Li\\nppm' → ('Li', 'ppm')
            'δ15Nbulk\\n‰' → ('δ15Nbulk', '‰')
            'Depth/m' → ('Depth', 'm')
            'Fepy/Fehr' → ('Fepy/Fehr', '')  # ratio, no unit
            'CIA' → ('CIA', '')
        """
        header = header.strip()
        if not header:
            return "", ""

        # Pattern 1: newline-separated unit
        if "\n" in header:
            parts = header.split("\n", 1)
            candidate = parts[1].strip()
            if self._is_unit(candidate):
                return parts[0].strip(), candidate

        # Pattern 2: parenthesized unit
        match = re.search(r"\(([^)]+)\)\s*$", header)
        if match:
            candidate = match.group(1).strip()
            if self._is_unit(candidate):
                clean = header[:match.start()].strip()
                return clean, candidate

        # Pattern 3: slash-separated unit (like Depth/m)
        match = re.search(r"/\s*([a-zA-Z%‰]+)\s*$", header)
        if match:
            candidate = match.group(1).strip()
            if self._is_unit(candidate):
                clean = header[:match.start()].strip()
                return clean, candidate

        return header, ""

    def _is_unit(self, text: str) -> bool:
        """Check if text looks like a unit."""
        if not text:
            return False
        # Direct match in known units
        if text in KNOWN_UNITS:
            return True
        # Contains common unit characters
        if any(c in text for c in ("%", "‰", "°")):
            return True
        return False

    def _to_markdown(self, headers: list[str], rows: list[list]) -> str:
        """Convert table to markdown format."""
        lines = []

        # Header row
        header_line = "| " + " | ".join(str(h) for h in headers) + " |"
        lines.append(header_line)

        # Separator
        sep_line = "| " + " | ".join("---" for _ in headers) + " |"
        lines.append(sep_line)

        # Data rows
        for row in rows:
            cells = []
            for cell in row:
                if cell is None:
                    cells.append("")
                else:
                    cells.append(str(cell).strip())
            line = "| " + " | ".join(cells) + " |"
            lines.append(line)

        return "\n".join(lines)
