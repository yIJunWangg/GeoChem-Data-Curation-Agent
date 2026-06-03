"""Human review queue and mapping memory persistence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.database import Database
from ..core.memory import MemoryStore
from ..core.models import MappingRule, MappingType, ReviewStatus


class ReviewManager:
    """Create and resolve review items for mapping suggestions."""

    def create_for_table(self, db: Database, table_id: str) -> int:
        mappings = db.fetch_all(
            "SELECT * FROM field_mappings WHERE table_id = ? AND requires_review = 1",
            (table_id,),
        )
        created = 0
        for mapping in mappings:
            existing = db.fetch_one(
                """SELECT review_id FROM review_items
                WHERE table_id = ? AND original_field = ? AND status = 'pending'""",
                (table_id, mapping["source_field"]),
            )
            if existing:
                continue
            review_id = self._generate_id(db, "REV", "review_items", "review_id")
            db.execute(
                """INSERT INTO review_items
                (review_id, article_id, table_id, item_type, risk_level, original_field,
                 original_unit, ai_suggestion, confidence, available_actions, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id,
                    mapping["article_id"],
                    table_id,
                    "field_mapping",
                    mapping["risk_level"],
                    mapping["source_field"],
                    mapping["source_unit"],
                    mapping["target_field"],
                    mapping["confidence"],
                    json.dumps(["accept", "reject", "edit", "defer"]),
                    ReviewStatus.PENDING.value,
                    datetime.now().isoformat(),
                ),
            )
            created += 1
        db.commit()
        return created

    def decide(
        self,
        db: Database,
        review_id: str,
        action: str,
        target_field: str | None = None,
        target_unit: str | None = None,
        formula: str | None = None,
        save_as_rule: bool = False,
        rule_scope: str = "project",
        notes: str = "",
        memory_path: str | Path | None = None,
    ) -> dict[str, Any]:
        review = db.fetch_one("SELECT * FROM review_items WHERE review_id = ?", (review_id,))
        if not review:
            raise ValueError(f"Review item not found: {review_id}")
        action = action.lower()
        if action not in {"accept", "reject", "edit", "defer"}:
            raise ValueError("action must be accept, reject, edit, or defer")

        final_target = target_field
        if action == "accept":
            final_target = target_field or review["ai_suggestion"]
        elif action == "reject":
            final_target = None
        elif action == "defer":
            final_target = target_field or review["ai_suggestion"]

        decision_id = self._generate_id(db, "DEC", "review_decisions", "decision_id")
        db.execute(
            """INSERT INTO review_decisions
            (decision_id, review_id, action, target_field, target_unit, formula,
             save_as_rule, rule_scope, notes, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                review_id,
                action,
                final_target,
                target_unit,
                formula,
                1 if save_as_rule else 0,
                rule_scope,
                notes,
                datetime.now().isoformat(),
            ),
        )

        new_status = {
            "accept": ReviewStatus.CONFIRMED.value,
            "edit": ReviewStatus.CONFIRMED.value,
            "reject": ReviewStatus.REJECTED.value,
            "defer": ReviewStatus.DEFERRED.value,
        }[action]
        db.execute("UPDATE review_items SET status = ? WHERE review_id = ?", (new_status, review_id))
        if review["item_type"] == "learned_extraction_rule" and review["ai_suggestion"]:
            db.execute(
                "UPDATE learned_extraction_rules SET review_status = ? WHERE rule_id = ?",
                (new_status, review["ai_suggestion"]),
            )

        rule_id = None
        if save_as_rule and final_target and action in {"accept", "edit"}:
            rule_id = self._save_rule(
                db=db,
                review=review,
                target_field=final_target,
                target_unit=target_unit,
                formula=formula,
                scope=rule_scope,
                memory_path=memory_path,
            )

        db.commit()
        return {"decision_id": decision_id, "review_status": new_status, "rule_id": rule_id}

    def _save_rule(
        self,
        db: Database,
        review,
        target_field: str,
        target_unit: str | None,
        formula: str | None,
        scope: str,
        memory_path: str | Path | None,
    ) -> str:
        rule_id = self._generate_id(db, "RULE", "mapping_rules", "rule_id")
        mapping_type = MappingType.ALIAS_MAPPING.value
        if formula:
            mapping_type = MappingType.UNIT_CONVERSION.value
        db.execute(
            """INSERT INTO mapping_rules
            (rule_id, source_field, target_field, source_unit, target_unit, mapping_type,
             formula, review_status, scope, version, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rule_id,
                review["original_field"],
                target_field,
                review["original_unit"],
                target_unit,
                mapping_type,
                formula,
                ReviewStatus.CONFIRMED.value,
                scope,
                1,
                datetime.now().isoformat(),
                "user",
            ),
        )

        if memory_path:
            store = MemoryStore(memory_path)
            store.load()
            store.add_rule(MappingRule(
                rule_id=rule_id,
                source_field=review["original_field"],
                target_field=target_field,
                source_unit=review["original_unit"],
                target_unit=target_unit,
                mapping_type=MappingType(mapping_type),
                formula=formula,
                review_status=ReviewStatus.CONFIRMED,
                scope=scope,
                created_by="user",
            ))
            store.save()
            self._write_memory_markdown(Path(memory_path), store)
        return rule_id

    def _write_memory_markdown(self, yaml_path: Path, store: MemoryStore) -> None:
        md_path = yaml_path.with_name("mapping_memory.md")
        lines = ["# Mapping Memory", "", "## Confirmed Rules", ""]
        for rule in store.get_all_rules():
            lines.extend([
                f"### {rule.rule_id}",
                f"- Source field: {rule.source_field}",
                f"- Target field: {rule.target_field}",
                f"- Source unit: {rule.source_unit or ''}",
                f"- Target unit: {rule.target_unit or ''}",
                f"- Mapping type: {rule.mapping_type.value}",
                f"- Formula: {rule.formula or ''}",
                f"- Review status: {rule.review_status.value}",
                f"- Scope: {rule.scope}",
                "",
            ])
        md_path.write_text("\n".join(lines), encoding="utf-8")

    def _generate_id(self, db: Database, prefix: str, table: str, column: str) -> str:
        row = db.fetch_one(
            f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id "
            f"FROM {table} WHERE {column} LIKE ?",
            (f"{prefix}_%",),
        )
        max_id = row["max_id"] if row and row["max_id"] else 0
        return f"{prefix}_{max_id + 1:03d}"
