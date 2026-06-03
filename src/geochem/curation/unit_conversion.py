"""Deterministic unit and chemical form conversions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..core.database import Database


class UnitConversionEngine:
    """Apply deterministic conversions and archive calculations."""

    FACTORS = {
        ("ppm", "wt%"): (0.0001, "wt% = ppm / 10000"),
        ("mg/kg", "ppm"): (1.0, "ppm = mg/kg"),
        ("ug/g", "ppm"): (1.0, "ppm = ug/g"),
        ("μg/g", "ppm"): (1.0, "ppm = μg/g"),
        ("Na2O", "Na"): (0.741857, "Na = Na2O * 0.741857"),
        ("K2O", "K"): (0.830153, "K = K2O * 0.830153"),
    }

    def convert(
        self,
        value,
        source_field: str,
        target_field: str,
        source_unit: str | None,
        target_unit: str | None,
    ) -> tuple[float | None, dict | None]:
        numeric = self._to_float(value)
        if numeric is None:
            return value, None

        key = None
        if source_unit and target_unit and source_unit != target_unit:
            key = (source_unit, target_unit)
        field_key = (source_field, target_field)
        if field_key in self.FACTORS:
            key = field_key
        if key not in self.FACTORS:
            return numeric, None

        factor, formula = self.FACTORS[key]
        result = numeric * factor
        return result, {
            "source_value": numeric,
            "result": result,
            "formula": formula,
            "factor": factor,
        }

    def archive(
        self,
        db: Database,
        project_dir: Path,
        article_id: str,
        table_id: str,
        row_id: str,
        target_field: str,
        source_field: str,
        source_unit: str,
        target_unit: str,
        calc: dict,
        rule_id: str | None = None,
        review_status: str = "confirmed",
    ) -> str:
        calc_id = self._generate_id(db, "CALC", "calculation_records", "calc_id")
        archive_dir = project_dir / "calculations" / article_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_file = archive_dir / "row_level_calculations.jsonl"
        record = {
            "calc_id": calc_id,
            "article_id": article_id,
            "table_id": table_id,
            "row_id": row_id,
            "target_field": target_field,
            "source_field": source_field,
            "source_value": calc["source_value"],
            "source_unit": source_unit,
            "target_unit": target_unit,
            "formula": calc["formula"],
            "result": calc["result"],
            "rule_id": rule_id,
            "review_status": review_status,
            "created_at": datetime.now().isoformat(),
        }
        with open(archive_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        db.execute(
            """INSERT INTO calculation_records
            (calc_id, article_id, table_id, row_id, target_field, source_field,
             source_value, source_unit, target_unit, formula, substitution, result,
             rule_id, review_status, archive_file, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                calc_id,
                article_id,
                table_id,
                row_id,
                target_field,
                source_field,
                calc["source_value"],
                source_unit,
                target_unit,
                calc["formula"],
                f"{target_field} = {calc['source_value']} * {calc['factor']}",
                calc["result"],
                rule_id,
                review_status,
                str(archive_file),
                datetime.now().isoformat(),
            ),
        )
        return calc_id

    def _to_float(self, value):
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except ValueError:
            return None

    def _generate_id(self, db: Database, prefix: str, table: str, column: str) -> str:
        row = db.fetch_one(
            f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id "
            f"FROM {table} WHERE {column} LIKE ?",
            (f"{prefix}_%",),
        )
        max_id = row["max_id"] if row and row["max_id"] else 0
        return f"{prefix}_{max_id + 1:03d}"
