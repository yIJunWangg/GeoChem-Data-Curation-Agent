"""Build standardized records from candidates, mappings, reviews, and calculations."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.database import Database
from ..core.models import DataQualityGrade
from .unit_conversion import UnitConversionEngine


class StandardizationPipeline:
    """Conservative standardization into standardized_records."""

    def __init__(self, converter: UnitConversionEngine | None = None):
        self.converter = converter or UnitConversionEngine()

    def standardize_table(self, db: Database, project_dir: Path, table_id: str) -> int:
        table = db.fetch_one("SELECT * FROM candidate_tables WHERE table_id = ?", (table_id,))
        if not table:
            raise ValueError(f"Candidate table not found: {table_id}")
        article = db.fetch_one("SELECT * FROM articles WHERE article_id = ?", (table["article_id"],))
        mappings = self._approved_mappings(db, table_id)
        rows = db.fetch_all("SELECT * FROM candidate_rows WHERE table_id = ? ORDER BY row_index", (table_id,))
        patches = self._approved_patches(db, table_id)
        db.execute("DELETE FROM standardized_records WHERE table_id = ?", (table_id,))
        db.execute("DELETE FROM calculation_records WHERE table_id = ?", (table_id,))
        used_patch_ids = set()
        count = 0
        for row in rows:
            raw_data = json.loads(row["raw_data"])
            row_patches = self._patches_for_row(patches, row, raw_data)
            used_patch_ids.update(p["patch_id"] for p in row_patches)
            standardized = self._standardize_row(db, project_dir, table, article, row, raw_data, mappings, row_patches)
            if standardized:
                self._save_record(db, table, article, row, standardized)
                count += 1
        for patch in patches:
            if patch["patch_id"] in used_patch_ids or patch["row_id"]:
                continue
            pseudo_row = {"row_id": patch["patch_id"], "row_index": self._patch_row_index(patch)}
            standardized = self._standardize_patch_only(table, patch)
            if standardized:
                self._save_record(db, table, article, pseudo_row, standardized)
                count += 1
        db.commit()
        return count

    def _approved_mappings(self, db: Database, table_id: str) -> list[dict[str, Any]]:
        mappings = []
        for mapping in db.fetch_all("SELECT * FROM field_mappings WHERE table_id = ?", (table_id,)):
            approved = not bool(mapping["requires_review"])
            review_status = "auto"
            decision = db.fetch_one(
                """SELECT d.* FROM review_decisions d
                JOIN review_items r ON d.review_id = r.review_id
                WHERE r.table_id = ? AND r.original_field = ?
                ORDER BY d.decided_at DESC LIMIT 1""",
                (table_id, mapping["source_field"]),
            )
            target_field = mapping["target_field"]
            target_unit = mapping["target_unit"]
            if decision:
                if decision["action"] in {"accept", "edit"}:
                    approved = True
                    target_field = decision["target_field"] or target_field
                    target_unit = decision["target_unit"] or target_unit
                    review_status = "confirmed"
                elif decision["action"] == "reject":
                    approved = False
                    review_status = "rejected"
            if approved:
                item = dict(mapping)
                item["target_field"] = target_field
                item["target_unit"] = target_unit
                item["review_status"] = review_status
                mappings.append(item)
        return mappings

    def _approved_patches(self, db: Database, table_id: str) -> list[dict[str, Any]]:
        patches = []
        for patch in db.fetch_all(
            """SELECT * FROM record_patches
            WHERE table_id = ? AND review_status IN ('confirmed', 'auto')
            ORDER BY created_at""",
            (table_id,),
        ):
            if patch["risk_level"] == "high" and patch["review_status"] != "confirmed":
                continue
            patches.append(dict(patch))
        return patches

    def _patches_for_row(self, patches: list[dict[str, Any]], row, raw_data: dict[str, Any]) -> list[dict[str, Any]]:
        sample_id = self._sample_id(raw_data)
        matched = []
        for patch in patches:
            if patch["row_id"] and patch["row_id"] == row["row_id"]:
                matched.append(patch)
            elif patch["sample_id"] and sample_id and str(patch["sample_id"]).strip() == sample_id:
                matched.append(patch)
        return matched

    def _sample_id(self, raw_data: dict[str, Any]) -> str:
        for key in ("SampleID", "SampleId", "sample_id", "Sample ID"):
            value = raw_data.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _patch_row_index(self, patch: dict[str, Any]) -> int:
        digits = "".join(ch for ch in patch["patch_id"] if ch.isdigit())
        return 1_000_000 + int(digits or 0)

    def _standardize_row(self, db, project_dir, table, article, row, raw_data, mappings, patches=None):
        data = {}
        original_fields = {}
        original_units = {}
        original_values = {}
        mapped_fields = {}
        mapped_units = {}
        calculation_ids = {}
        review_statuses = {}
        confidence_scores = {}
        quality = DataQualityGrade.A

        for mapping in mappings:
            source = mapping["source_field"]
            target = mapping["target_field"]
            value = raw_data.get(target, raw_data.get(source))
            if value is None or value == "":
                continue
            stored_value, calc = self.converter.convert(
                value,
                source_field=source,
                target_field=target,
                source_unit=mapping["source_unit"],
                target_unit=mapping["target_unit"],
            )
            calc_id = None
            if calc:
                calc_id = self.converter.archive(
                    db=db,
                    project_dir=project_dir,
                    article_id=table["article_id"],
                    table_id=table["table_id"],
                    row_id=row["row_id"],
                    target_field=target,
                    source_field=source,
                    source_unit=mapping["source_unit"] or "",
                    target_unit=mapping["target_unit"] or "",
                    calc=calc,
                    review_status=mapping.get("review_status", "confirmed"),
                )
                quality = DataQualityGrade.C
            elif mapping["review_status"] == "confirmed" and quality == DataQualityGrade.A:
                quality = DataQualityGrade.B

            data[target] = stored_value
            original_fields[target] = source
            original_units[target] = mapping["source_unit"] or ""
            original_values[target] = value
            mapped_fields[source] = target
            mapped_units[target] = mapping["target_unit"] or ""
            if calc_id:
                calculation_ids[target] = calc_id
            review_statuses[target] = mapping["review_status"]
            confidence_scores[target] = mapping["confidence"]

        self._merge_patches(
            patches or [],
            data,
            original_fields,
            original_units,
            original_values,
            mapped_fields,
            mapped_units,
            calculation_ids,
            review_statuses,
            confidence_scores,
        )
        if patches and quality == DataQualityGrade.A:
            quality = DataQualityGrade.B

        if not data:
            return None
        return {
            "data": data,
            "original_fields": original_fields,
            "original_units": original_units,
            "original_values": original_values,
            "mapped_fields": mapped_fields,
            "mapped_units": mapped_units,
            "calculation_ids": calculation_ids,
            "review_statuses": review_statuses,
            "confidence_scores": confidence_scores,
            "quality_grade": quality.value,
        }

    def _standardize_patch_only(self, table, patch: dict[str, Any]):
        data = {}
        original_fields = {}
        original_units = {}
        original_values = {}
        mapped_fields = {}
        mapped_units = {}
        calculation_ids = {}
        review_statuses = {}
        confidence_scores = {}
        self._merge_patches(
            [patch],
            data,
            original_fields,
            original_units,
            original_values,
            mapped_fields,
            mapped_units,
            calculation_ids,
            review_statuses,
            confidence_scores,
        )
        if patch.get("sample_id"):
            data.setdefault("SampleID", patch["sample_id"])
            original_fields.setdefault("SampleID", "record_patch.sample_id")
            original_values.setdefault("SampleID", patch["sample_id"])
            review_statuses.setdefault("SampleID", patch["review_status"])
            confidence_scores.setdefault("SampleID", patch["confidence"])
        if not data:
            return None
        return {
            "data": data,
            "original_fields": original_fields,
            "original_units": original_units,
            "original_values": original_values,
            "mapped_fields": mapped_fields,
            "mapped_units": mapped_units,
            "calculation_ids": calculation_ids,
            "review_statuses": review_statuses,
            "confidence_scores": confidence_scores,
            "quality_grade": DataQualityGrade.B.value if patch["review_status"] == "confirmed" else DataQualityGrade.C.value,
        }

    def _merge_patches(
        self,
        patches,
        data,
        original_fields,
        original_units,
        original_values,
        mapped_fields,
        mapped_units,
        calculation_ids,
        review_statuses,
        confidence_scores,
    ) -> None:
        for patch in patches:
            target = patch["target_field"]
            if data.get(target) not in (None, ""):
                continue
            data[target] = patch["value"]
            original_fields[target] = patch["target_header"] or f"record_patch:{patch['patch_id']}"
            original_units[target] = patch["source_unit"] or ""
            original_values[target] = patch["value"]
            mapped_fields[patch["target_header"] or target] = target
            mapped_units[target] = patch["target_unit"] or ""
            if patch["learned_rule_id"]:
                calculation_ids[f"{target}__learned_rule"] = patch["learned_rule_id"]
            if patch["evidence_id"]:
                calculation_ids[f"{target}__evidence"] = patch["evidence_id"]
            review_statuses[target] = patch["review_status"]
            confidence_scores[target] = patch["confidence"]

    def _save_record(self, db, table, article, row, standardized):
        record_id = self._generate_id(db, "STD", "standardized_records", "record_id")
        source_file = ""
        resource = db.fetch_one("SELECT * FROM resources WHERE resource_id = ?", (table["resource_id"],))
        if resource:
            source_file = resource["file_name"]
        db.execute(
            """INSERT INTO standardized_records
            (record_id, article_id, table_id, row_id, data, reference, doi, source_file,
             source_table, source_row, original_fields, original_units, original_values,
             mapped_fields, mapped_units, mapping_rule_ids, calculation_ids, review_statuses,
             confidence_scores, quality_grade, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                table["article_id"],
                table["table_id"],
                row["row_id"],
                json.dumps(standardized["data"], ensure_ascii=False),
                article["title"] if article else "",
                article["doi"] if article else None,
                source_file,
                table["sheet_name"] or table["title"] or table["table_id"],
                row["row_index"],
                json.dumps(standardized["original_fields"], ensure_ascii=False),
                json.dumps(standardized["original_units"], ensure_ascii=False),
                json.dumps(standardized["original_values"], ensure_ascii=False),
                json.dumps(standardized["mapped_fields"], ensure_ascii=False),
                json.dumps(standardized["mapped_units"], ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
                json.dumps(standardized["calculation_ids"], ensure_ascii=False),
                json.dumps(standardized["review_statuses"], ensure_ascii=False),
                json.dumps(standardized["confidence_scores"], ensure_ascii=False),
                standardized["quality_grade"],
                datetime.now().isoformat(),
            ),
        )

    def _generate_id(self, db: Database, prefix: str, table: str, column: str) -> str:
        row = db.fetch_one(
            f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id "
            f"FROM {table} WHERE {column} LIKE ?",
            (f"{prefix}_%",),
        )
        max_id = row["max_id"] if row and row["max_id"] else 0
        return f"{prefix}_{max_id + 1:03d}"
