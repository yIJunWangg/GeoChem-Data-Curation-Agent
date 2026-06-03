"""Persist extraction results to database."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..core.database import Database
from ..core.logging_config import get_logger
from ..core.models import CandidateColumn
from ..curation.header_normalizer import HeaderNormalizer

logger = get_logger("extractors.result_persistence")


class ResultPersistence:
    """Write extraction results to candidate_tables, candidate_columns, candidate_rows."""

    def __init__(self):
        self.normalizer = HeaderNormalizer()

    def save(
        self,
        db: Database,
        article_id: str,
        resource_id: str,
        content_text: str,
        rows: list[dict[str, Any]],
        headers: list[str],
        matched_fields: dict[str, str],
        header_units: dict[str, str] | None = None,
        source_type: str = "",
        sheet_name: str | None = None,
        page_number: int | None = None,
        confidence: float = 0.0,
        extract_method: str = "llm",
    ) -> str:
        """Save extraction results. Returns table_id."""
        now = datetime.now().isoformat()
        table_id = self._generate_id(db, "TBL", "candidate_tables", "table_id")

        # Save candidate_table
        db.execute(
            """INSERT INTO candidate_tables
            (table_id, article_id, resource_id, source_type, sheet_name,
             page_number, row_count, col_count, extract_method, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                table_id, article_id, resource_id, source_type, sheet_name,
                page_number, len(rows), len(headers), extract_method, confidence, now,
            ),
        )

        # Save candidate_columns
        self._save_columns(db, table_id, headers, matched_fields, rows, header_units or {})

        # Save candidate_rows
        self._save_rows(db, table_id, rows)

        db.commit()
        logger.info(f"Saved extraction results: table {table_id}, {len(rows)} rows, {len(headers)} columns")
        return table_id

    def _save_columns(
        self,
        db: Database,
        table_id: str,
        headers: list[str],
        matched_fields: dict[str, str],
        rows: list[dict],
        header_units: dict[str, str],
    ) -> list[str]:
        """Create candidate_columns records with sample_values."""
        column_ids = []
        for i, header in enumerate(headers):
            column_id = self._generate_id(db, "COL", "candidate_columns", "column_id")
            normalized = matched_fields.get(header, "")

            # Collect sample values (first 5 non-null)
            sample_values = []
            for row in rows:
                val = row.get(header) or row.get(normalized)
                if val is not None and val != "" and len(sample_values) < 5:
                    sample_values.append(val)

            # Infer dtype
            dtype = self._infer_dtype(sample_values)

            unit_candidate = header_units.get(header) or self.normalizer.normalize(header).unit or None
            db.execute(
                """INSERT INTO candidate_columns
                (column_id, table_id, raw_name, normalized_name, unit_candidate, dtype, sample_values)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    column_id,
                    table_id,
                    header,
                    normalized,
                    unit_candidate,
                    dtype,
                    json.dumps(sample_values, ensure_ascii=False),
                ),
            )
            column_ids.append(column_id)

        return column_ids

    def _save_rows(self, db: Database, table_id: str, rows: list[dict]) -> list[str]:
        """Create candidate_rows records."""
        row_ids = []
        for i, row_data in enumerate(rows):
            row_id = self._generate_id(db, "ROW", "candidate_rows", "row_id")
            db.execute(
                """INSERT INTO candidate_rows
                (row_id, table_id, row_index, raw_data)
                VALUES (?, ?, ?, ?)""",
                (row_id, table_id, i, json.dumps(row_data, ensure_ascii=False)),
            )
            row_ids.append(row_id)
        return row_ids

    def _infer_dtype(self, sample_values: list) -> str:
        """Infer data type from sample values."""
        if not sample_values:
            return "unknown"

        numeric_count = 0
        for val in sample_values:
            if isinstance(val, (int, float)):
                numeric_count += 1
            elif isinstance(val, str):
                try:
                    float(val)
                    numeric_count += 1
                except ValueError:
                    pass

        if numeric_count == len(sample_values):
            return "float"
        if numeric_count > len(sample_values) / 2:
            return "mixed"
        return "string"

    def _generate_id(self, db: Database, prefix: str, table: str, column: str) -> str:
        """Sequential ID generation."""
        try:
            row = db.fetch_one(
                f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id "
                f"FROM {table} WHERE {column} LIKE ?",
                (f"{prefix}_%",),
            )
            max_id = row["max_id"] if row and row["max_id"] else 0
        except Exception:
            max_id = 0
        return f"{prefix}_{max_id + 1:03d}"
