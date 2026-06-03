"""Schema mapping engine with deterministic rules before LLM fallback."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..core.database import Database
from ..core.memory import MemoryStore
from ..core.models import MappingType, ReviewStatus, RiskLevel
from ..core.schema_manager import SchemaManager
from ..providers.llm_client import LLMClient
from .header_normalizer import HeaderNormalizer
from .target_headers import TargetHeaderBuilder, classify_field


class MappingEngine:
    """Create field mapping suggestions for a candidate table."""

    def __init__(
        self,
        schema_manager: SchemaManager,
        memory_store: MemoryStore | None = None,
        llm_client: LLMClient | None = None,
        normalizer: HeaderNormalizer | None = None,
        provider_override: str | None = None,
        model_override: str | None = None,
    ):
        self.schema_manager = schema_manager
        self.memory_store = memory_store
        self.llm_client = llm_client
        self.normalizer = normalizer or HeaderNormalizer()
        self.provider_override = provider_override
        self.model_override = model_override

    def map_table(
        self,
        db: Database,
        table_id: str,
        project_id: str = "",
        article_id: str = "",
        use_llm: bool = True,
        grouped: bool = False,
        header_descriptions_path: str | None = None,
    ) -> list[dict[str, Any]]:
        table = db.fetch_one("SELECT * FROM candidate_tables WHERE table_id = ?", (table_id,))
        if not table:
            raise ValueError(f"Candidate table not found: {table_id}")
        article_id = article_id or table["article_id"]
        columns = db.fetch_all("SELECT * FROM candidate_columns WHERE table_id = ? ORDER BY column_id", (table_id,))
        suggestions = [self._map_column(col, article_id, table_id) for col in columns]

        unresolved = [s for s in suggestions if not s["target_field"]]
        if use_llm and unresolved and self.llm_client:
            if grouped:
                llm_suggestions = self._llm_map_grouped(unresolved, columns, project_id, article_id, header_descriptions_path)
            else:
                llm_suggestions = self._llm_map(unresolved, project_id, article_id)
            by_source = {s["source_field"]: s for s in llm_suggestions}
            for i, suggestion in enumerate(suggestions):
                replacement = by_source.get(suggestion["source_field"])
                if replacement:
                    suggestions[i].update(replacement)

        saved = []
        for suggestion in suggestions:
            if not suggestion["target_field"]:
                continue
            existing = db.fetch_one(
                "SELECT mapping_id FROM field_mappings WHERE table_id = ? AND source_field = ?",
                (table_id, suggestion["source_field"]),
            )
            mapping_id = existing["mapping_id"] if existing else self._generate_id(db, "MAP", "field_mappings", "mapping_id")
            suggestion["mapping_id"] = mapping_id
            if existing:
                db.execute(
                    """UPDATE field_mappings
                    SET column_id = ?, target_field = ?, source_unit = ?, target_unit = ?,
                        mapping_type = ?, confidence = ?, risk_level = ?,
                        requires_review = ?, reason = ?, created_at = ?
                    WHERE mapping_id = ?""",
                    (
                        suggestion.get("column_id"),
                        suggestion["target_field"],
                        suggestion.get("source_unit"),
                        suggestion.get("target_unit"),
                        suggestion["mapping_type"],
                        suggestion["confidence"],
                        suggestion["risk_level"],
                        1 if suggestion["requires_review"] else 0,
                        suggestion["reason"],
                        datetime.now().isoformat(),
                        mapping_id,
                    ),
                )
            else:
                db.execute(
                    """INSERT INTO field_mappings
                    (mapping_id, article_id, table_id, column_id, source_field, target_field,
                     source_unit, target_unit, mapping_type, confidence, risk_level,
                     requires_review, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        mapping_id,
                        article_id,
                        table_id,
                        suggestion.get("column_id"),
                        suggestion["source_field"],
                        suggestion["target_field"],
                        suggestion.get("source_unit"),
                        suggestion.get("target_unit"),
                        suggestion["mapping_type"],
                        suggestion["confidence"],
                        suggestion["risk_level"],
                        1 if suggestion["requires_review"] else 0,
                        suggestion["reason"],
                        datetime.now().isoformat(),
                    ),
                )
            db.execute(
                "UPDATE candidate_columns SET normalized_name = ?, unit_candidate = ? WHERE column_id = ?",
                (suggestion["target_field"], suggestion.get("source_unit"), suggestion.get("column_id")),
            )
            saved.append(suggestion)
        db.commit()
        return saved

    def _map_column(self, col, article_id: str, table_id: str) -> dict[str, Any]:
        source = col["raw_name"]
        normalized = self.normalizer.normalize(source, col["unit_candidate"] or "")
        source_unit = col["unit_candidate"] or normalized.unit or ""
        field, conf = self._match_schema(source, source_unit)
        mapping_type = MappingType.UNCERTAIN.value
        reason = "No deterministic match"

        if field:
            mapping_type = MappingType.EXACT.value if conf == 1.0 else MappingType.ALIAS_MAPPING.value
            reason = "Matched by schema aliases or normalized header"
        else:
            memory_rule = self._match_memory(source, normalized.clean)
            if memory_rule:
                field = memory_rule.target_field
                conf = 0.98 if memory_rule.review_status == ReviewStatus.CONFIRMED else 0.75
                mapping_type = memory_rule.mapping_type.value
                reason = f"Matched by memory rule {memory_rule.rule_id}"

        target_unit = self._target_unit(field)
        risk = self._risk_level(source, field, source_unit, target_unit, conf, mapping_type)
        requires_review = risk != RiskLevel.LOW.value or conf < 0.9

        return {
            "article_id": article_id,
            "table_id": table_id,
            "column_id": col["column_id"],
            "source_field": source,
            "target_field": field or "",
            "source_unit": source_unit or normalized.unit or None,
            "target_unit": target_unit,
            "mapping_type": mapping_type,
            "confidence": round(conf, 3),
            "risk_level": risk,
            "requires_review": requires_review,
            "reason": reason,
        }

    def _match_schema(self, source: str, source_unit: str) -> tuple[str | None, float]:
        best_field = None
        best_conf = 0.0
        for variant in self.normalizer.variants(source, source_unit):
            field, conf = self.schema_manager.match_field(variant)
            if conf > best_conf:
                best_field, best_conf = field, conf
        return best_field, best_conf

    def _match_memory(self, source: str, clean: str):
        if not self.memory_store:
            return None
        for key in (source, clean):
            rules = self.memory_store.query_by_source(key)
            if rules:
                return rules[0]
        return None

    def _target_unit(self, field_name: str | None) -> str | None:
        if not field_name:
            return None
        field = self.schema_manager.get_field(field_name)
        return field.default_unit if field else None

    def _risk_level(
        self,
        source: str,
        target: str | None,
        source_unit: str | None,
        target_unit: str | None,
        confidence: float,
        mapping_type: str,
    ) -> str:
        if not target:
            return RiskLevel.HIGH.value
        source_norm = self.normalizer.normalized_key(source).lower()
        target_norm = target.lower()
        oxide_element_pairs = {("na2o", "na"), ("k2o", "k")}
        if (source_norm, target_norm) in oxide_element_pairs:
            return RiskLevel.HIGH.value
        if mapping_type in {MappingType.OXIDE_TO_ELEMENT.value, MappingType.ELEMENT_TO_OXIDE.value}:
            return RiskLevel.HIGH.value
        if source_unit and target_unit and source_unit != target_unit:
            return RiskLevel.MEDIUM.value
        if confidence < 0.7:
            return RiskLevel.HIGH.value
        if confidence < 0.9:
            return RiskLevel.MEDIUM.value
        return RiskLevel.LOW.value

    def _llm_map(self, unresolved: list[dict[str, Any]], project_id: str, article_id: str) -> list[dict[str, Any]]:
        schema_fields = self.schema_manager.get_all_field_names()
        headers = [
            {
                "source_field": item["source_field"],
                "source_unit": item.get("source_unit"),
                "normalized_key": self.normalizer.normalize(item["source_field"], item.get("source_unit") or "").normalized_key,
            }
            for item in unresolved
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "You map geochemistry table headers to a fixed schema. "
                    "Return only JSON: a list of objects with source_field, target_field, "
                    "confidence, risk_level, reason. Use target_field null when unsure."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"headers": headers, "schema_fields": schema_fields},
                    ensure_ascii=False,
                ),
            },
        ]
        response = self.llm_client.chat(
            messages=messages,
            task_name="field_mapping",
            provider_override=self.provider_override,
            model_override=self.model_override,
            project_id=project_id,
            article_id=article_id,
            agent_name="Schema Mapper",
            skill_name="field_mapping",
            use_cache=True,
        )
        try:
            data = json.loads(self._json_text(response.content))
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("mappings", [])
        result = []
        schema_set = set(schema_fields)
        for item in data if isinstance(data, list) else []:
            target = item.get("target_field")
            if target not in schema_set:
                continue
            source = item.get("source_field")
            source_item = next((u for u in unresolved if u["source_field"] == source), None)
            if not source_item:
                continue
            confidence = float(item.get("confidence") or 0.75)
            risk = item.get("risk_level") or ("medium" if confidence >= 0.7 else "high")
            result.append({
                "source_field": source,
                "target_field": target,
                "target_unit": self._target_unit(target),
                "mapping_type": MappingType.UNCERTAIN.value,
                "confidence": confidence,
                "risk_level": risk,
                "requires_review": risk != "low" or confidence < 0.9,
                "reason": item.get("reason") or "Suggested by LLM fallback",
            })
        return result

    def _llm_map_grouped(
        self,
        unresolved: list[dict[str, Any]],
        columns,
        project_id: str,
        article_id: str,
        header_descriptions_path: str | None,
    ) -> list[dict[str, Any]]:
        target_headers = TargetHeaderBuilder(self.schema_manager).build(
            [col["raw_name"] for col in columns if col["raw_name"]],
            header_descriptions_path,
        )
        by_group: dict[str, list[dict[str, Any]]] = {}
        for item in unresolved:
            group = classify_field(self.normalizer.normalize(item["source_field"]).normalized_key, item["source_field"])
            by_group.setdefault(group, []).append(item)

        suggestions = []
        for group, group_items in by_group.items():
            group_targets = [h for h in target_headers if h.field_group == group]
            if not group_targets:
                group_targets = target_headers
            suggestions.extend(self._llm_map_group(group, group_items, group_targets, project_id, article_id))
        return suggestions

    def _llm_map_group(
        self,
        group: str,
        unresolved: list[dict[str, Any]],
        target_headers,
        project_id: str,
        article_id: str,
    ) -> list[dict[str, Any]]:
        headers = [
            {
                "source_field": item["source_field"],
                "source_unit": item.get("source_unit"),
                "normalized_key": self.normalizer.normalize(item["source_field"], item.get("source_unit") or "").normalized_key,
            }
            for item in unresolved
        ]
        targets = [
            {
                "display_header": target.display_header,
                "canonical_field": target.canonical_field,
                "description": target.description,
                "target_unit": target.target_unit,
                "field_group": target.field_group,
            }
            for target in target_headers
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "You map geochemistry table headers to user target headers for one field group. "
                    "You receive headers and target descriptions/units only, never row values. "
                    "Return only JSON list with source_field, target_field, target_unit, needs_conversion, "
                    "confidence, risk_level, reason. Use target_field null when unsure."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "field_group": group,
                        "source_headers": headers,
                        "target_headers": targets,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = self.llm_client.chat(
            messages=messages,
            task_name="field_mapping",
            provider_override=self.provider_override,
            model_override=self.model_override,
            project_id=project_id,
            article_id=article_id,
            agent_name="Schema Mapper",
            skill_name=f"grouped_field_mapping:{group}",
            use_cache=True,
        )
        try:
            data = json.loads(self._json_text(response.content))
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("mappings", [])

        target_fields = {target.canonical_field: target for target in target_headers}
        result = []
        for item in data if isinstance(data, list) else []:
            source = item.get("source_field")
            source_item = next((u for u in unresolved if u["source_field"] == source), None)
            if not source_item:
                continue
            target = item.get("target_field")
            if target not in target_fields:
                display_match = next((t for t in target_headers if t.display_header == target), None)
                target = display_match.canonical_field if display_match else None
            if not target:
                continue
            confidence = float(item.get("confidence") or 0.75)
            target_unit = item.get("target_unit") or target_fields[target].target_unit or self._target_unit(target)
            risk = self._risk_level(
                source,
                target,
                source_item.get("source_unit"),
                target_unit,
                confidence,
                MappingType.UNCERTAIN.value,
            )
            if item.get("needs_conversion"):
                risk = RiskLevel.MEDIUM.value if risk == RiskLevel.LOW.value else risk
            result.append({
                "source_field": source,
                "target_field": target,
                "target_unit": target_unit,
                "mapping_type": MappingType.UNCERTAIN.value,
                "confidence": confidence,
                "risk_level": risk,
                "requires_review": risk != "low" or confidence < 0.9,
                "reason": item.get("reason") or f"Suggested by grouped LLM fallback ({group})",
            })
        return result

    def _json_text(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.replace("json\n", "", 1)
        start_obj = text.find("{")
        start_arr = text.find("[")
        starts = [i for i in (start_obj, start_arr) if i >= 0]
        if starts:
            text = text[min(starts):]
        end_obj = text.rfind("}")
        end_arr = text.rfind("]")
        end = max(end_obj, end_arr)
        if end >= 0:
            text = text[:end + 1]
        return text

    def _generate_id(self, db: Database, prefix: str, table: str, column: str) -> str:
        row = db.fetch_one(
            f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id "
            f"FROM {table} WHERE {column} LIKE ?",
            (f"{prefix}_%",),
        )
        max_id = row["max_id"] if row and row["max_id"] else 0
        return f"{prefix}_{max_id + 1:03d}"
