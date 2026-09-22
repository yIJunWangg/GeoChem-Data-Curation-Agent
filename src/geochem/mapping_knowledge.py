"""Versioned, offline geochemical vocabulary used by header mapping.

The bundled release is deliberately curated and small. External vocabulary
updates are staged and must be published by an administrator before they can
affect mapping decisions.
"""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import hashlib
from importlib.resources import files
import json
import re
import unicodedata
from typing import Any


AUTO_APPLY_THRESHOLD = 0.92


def normalize_mapping_term(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("δ", "delta").replace("Δ", "delta")
    text = re.sub(r"(?:wt\.?\s*%|weight\s*percent|mass\s*%|\bppm\b|\bppb\b|‰|\bper\s*mil\b|\bpermil\b)", " ", text, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def normalize_unit(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("weight percent", "wt%").replace("wt. %", "wt%").replace("wt %", "wt%")
    if text in {"%", "wt%", "mass%", "mass %"}:
        return "wt%"
    if text in {"ppm", "mg/kg", "ug/g", "\u03bcg/g"}:
        return "ppm"
    if text in {"ppb", "ug/kg", "ng/g", "\u03bcg/kg"}:
        return "ppb"
    if text in {"‰", "permil", "per mil", "ppt"}:
        return "permil"
    if text in {"1", "ratio", "dimensionless"}:
        return "ratio"
    return text


def unit_dimension(value: str) -> str:
    unit = normalize_unit(value)
    if unit in {"wt%", "ppm", "ppb"}:
        return "mass_fraction"
    if unit == "permil":
        return "isotope_delta"
    if unit == "ratio":
        return "dimensionless"
    if not unit:
        return ""
    return "other"


class MappingKnowledgeService:
    """Store, search and safely rank one published knowledge release."""

    def __init__(self, db: Any):
        self.db = db
        self.ensure_builtin_release()

    @staticmethod
    def bundled_snapshot() -> dict[str, Any]:
        path = files("geochem").joinpath("data/geochem_mapping_v1.json")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _stable_id(prefix: str, *values: str) -> str:
        digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()[:20].upper()
        return f"{prefix}_{digest}"

    def ensure_builtin_release(self) -> dict[str, Any]:
        current = self.db.fetch_one(
            "SELECT * FROM mapping_knowledge_releases WHERE status = 'published' ORDER BY published_at DESC LIMIT 1"
        )
        if current:
            return dict(current)
        snapshot = self.bundled_snapshot()
        release_id = self._stable_id("MKR", snapshot["version"])
        now = datetime.now().isoformat()
        self._insert_snapshot(snapshot, release_id, "published", now)
        return dict(self.db.fetch_one("SELECT * FROM mapping_knowledge_releases WHERE release_id = ?", (release_id,)))

    def _insert_snapshot(self, snapshot: dict[str, Any], release_id: str, status: str, now: str) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO mapping_knowledge_releases
               (release_id, version, name, status, source_manifest_json, created_by, created_at, published_at)
               VALUES (?, ?, ?, ?, ?, 'system', ?, ?)""",
            (
                release_id, snapshot["version"], snapshot["name"], status,
                json.dumps(snapshot.get("sources") or [], ensure_ascii=False), now,
                now if status == "published" else None,
            ),
        )
        for concept in snapshot.get("concepts") or []:
            concept_version_id = self._stable_id("MKC", release_id, concept["concept_id"])
            self.db.execute(
                """INSERT OR IGNORE INTO mapping_knowledge_concepts
                   (concept_version_id, concept_id, release_id, canonical_name, concept_type,
                    chemical_form, unit_dimension, allowed_units_json, context_json,
                    forbidden_forms_json, source_references_json, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)""",
                (
                    concept_version_id, concept["concept_id"], release_id, concept["canonical_name"],
                    concept["concept_type"], concept.get("chemical_form", ""),
                    concept.get("unit_dimension", ""),
                    json.dumps(concept.get("allowed_units") or [], ensure_ascii=False),
                    json.dumps(concept.get("contexts") or [], ensure_ascii=False),
                    json.dumps(concept.get("forbidden_forms") or [], ensure_ascii=False),
                    json.dumps(concept.get("source_references") or [], ensure_ascii=False), now,
                ),
            )
            terms = list(dict.fromkeys([concept["canonical_name"], *(concept.get("terms") or [])]))
            for index, term in enumerate(terms):
                normalized = normalize_mapping_term(term)
                if not normalized:
                    continue
                self.db.execute(
                    """INSERT OR IGNORE INTO mapping_knowledge_terms
                       (term_id, concept_version_id, display_term, normalized_term, term_type, language, score, created_at)
                       VALUES (?, ?, ?, ?, ?, 'en', ?, ?)""",
                    (
                        self._stable_id("MKT", concept_version_id, normalized), concept_version_id,
                        term, normalized, "canonical" if index == 0 else "alias",
                        0.99 if index == 0 else 0.96, now,
                    ),
                )
        self.db.commit()

    def current_release(self) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT * FROM mapping_knowledge_releases WHERE status = 'published' ORDER BY published_at DESC LIMIT 1"
        )
        if not row:
            row = self.ensure_builtin_release()
        result = dict(row)
        result["sources"] = json.loads(result.pop("source_manifest_json", "[]") or "[]")
        return result

    @staticmethod
    def _decode_concept(row: Any) -> dict[str, Any]:
        item = dict(row)
        for column, output in (
            ("allowed_units_json", "allowed_units"),
            ("context_json", "contexts"),
            ("forbidden_forms_json", "forbidden_forms"),
            ("source_references_json", "source_references"),
        ):
            item[output] = json.loads(item.pop(column, "[]") or "[]")
        item["metadata"] = json.loads(item.pop("metadata_json", "{}") or "{}")
        return item

    def _published_terms(self) -> list[dict[str, Any]]:
        release = self.current_release()
        rows = self.db.fetch_all(
            """SELECT c.*, t.display_term, t.normalized_term, t.term_type, t.score AS term_score
               FROM mapping_knowledge_concepts c
               JOIN mapping_knowledge_terms t ON t.concept_version_id = c.concept_version_id
               WHERE c.release_id = ?""",
            (release["release_id"],),
        )
        return [self._decode_concept(row) for row in rows]

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        normalized = normalize_mapping_term(query)
        matches: dict[str, dict[str, Any]] = {}
        for row in self._published_terms():
            term = row["normalized_term"]
            similarity = SequenceMatcher(None, normalized, term).ratio() if normalized and term else 0.0
            if normalized == term:
                score = float(row["term_score"])
            elif len(normalized) >= 3 and (normalized in term or term in normalized):
                score = min(0.9, 0.72 + 0.18 * similarity)
            elif similarity >= 0.72:
                score = min(0.86, similarity)
            else:
                continue
            prior = matches.get(row["concept_version_id"])
            candidate = {**row, "match_score": round(score, 3), "matched_term": row["display_term"]}
            if not prior or candidate["match_score"] > prior["match_score"]:
                matches[row["concept_version_id"]] = candidate
        items = sorted(matches.values(), key=lambda item: (-item["match_score"], item["canonical_name"]))[:limit]
        return {"release": self.current_release(), "query": query, "items": items}

    def _concept_for_value(self, value: str) -> dict[str, Any] | None:
        result = self.search(value, limit=3)
        exact = [item for item in result["items"] if item["match_score"] >= 0.92]
        return exact[0] if exact else None

    @staticmethod
    def _unit_compatibility(source_unit: str, target_unit: str, concept: dict[str, Any]) -> str:
        source_dimension = unit_dimension(source_unit)
        target_dimension = unit_dimension(target_unit)
        concept_dimension = str(concept.get("unit_dimension") or "")
        if source_dimension and target_dimension:
            return "compatible" if source_dimension == target_dimension else "incompatible"
        known = source_dimension or target_dimension
        if known:
            return "compatible" if known == concept_dimension or concept_dimension in {"none", ""} else "incompatible"
        return "compatible" if concept_dimension in {"none", "dimensionless"} else "inferred_compatible"

    def rank_targets(
        self,
        source_header: str,
        profile: dict[str, Any],
        targets: list[dict[str, Any]],
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        source_concept = self._concept_for_value(str(profile.get("field_token") or source_header))
        if not source_concept:
            source_concept = self._concept_for_value(source_header)
        if not source_concept:
            return []
        ranked: list[dict[str, Any]] = []
        for target in targets:
            target_concept = self._concept_for_value(str(target.get("canonical_field") or target.get("display_header") or ""))
            if not target_concept:
                target_concept = self._concept_for_value(str(target.get("display_header") or ""))
            if not target_concept or target_concept["concept_id"] != source_concept["concept_id"]:
                continue
            compatibility = self._unit_compatibility(
                str(profile.get("detected_unit") or ""), str(target.get("target_unit") or ""), source_concept,
            )
            score = min(float(source_concept["match_score"]), float(target_concept["match_score"]), 0.99)
            if compatibility == "incompatible":
                score = min(score, 0.4)
            ranked.append({
                "target": target,
                "score": round(score, 3),
                "concept_id": source_concept["concept_id"],
                "concept_version_id": source_concept["concept_version_id"],
                "release_id": source_concept["release_id"],
                "chemical_form": source_concept["chemical_form"],
                "unit_compatibility": compatibility,
                "source_references": source_concept["source_references"],
                "auto_applied": bool(score >= AUTO_APPLY_THRESHOLD and compatibility != "incompatible"),
            })
        ranked.sort(key=lambda item: (-item["score"], item["target"].get("order", 0)))
        return ranked[:limit]

    def validate_mapping(
        self,
        source_header: str,
        profile: dict[str, Any],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a proposed mapping when both sides are known concepts.

        Unknown terms remain eligible for a user decision.  A known source and
        target, however, must resolve to the same concept and compatible unit
        dimension.  This prevents fuzzy matching or an LLM from treating K as
        K2O, or FeO as Fe2O3.
        """
        source_concept = self._concept_for_value(str(profile.get("field_token") or source_header))
        if not source_concept:
            source_concept = self._concept_for_value(source_header)
        target_concept = self._concept_for_value(
            str(target.get("canonical_field") or target.get("display_header") or "")
        )
        if not target_concept:
            target_concept = self._concept_for_value(str(target.get("display_header") or ""))
        if not source_concept or not target_concept:
            return {
                "known": False,
                "safe": True,
                "reason": "知识库未同时识别源字段和目标字段，需要其他规则或人工确认",
            }

        compatibility = self._unit_compatibility(
            str(profile.get("detected_unit") or ""),
            str(target.get("target_unit") or ""),
            source_concept,
        )
        same_concept = source_concept["concept_id"] == target_concept["concept_id"]
        source_form = str(source_concept.get("chemical_form") or "")
        target_form = str(target_concept.get("chemical_form") or "")
        forbidden_forms = {str(value) for value in source_concept.get("forbidden_forms") or []}
        form_conflict = bool(
            (source_form and target_form and source_form != target_form)
            or (target_form and target_form in forbidden_forms)
        )
        safe = same_concept and compatibility != "incompatible" and not form_conflict
        if not same_concept or form_conflict:
            reason = (
                f"化学形态不一致：源字段 {source_concept['canonical_name']}"
                f"，目标字段 {target_concept['canonical_name']}"
            )
        elif compatibility == "incompatible":
            reason = "源字段与目标字段的单位维度不兼容"
        else:
            reason = "知识库概念与单位兼容"
        return {
            "known": True,
            "safe": safe,
            "reason": reason,
            "source_concept_id": source_concept["concept_id"],
            "target_concept_id": target_concept["concept_id"],
            "chemical_form": source_form,
            "unit_compatibility": compatibility,
            "release_id": source_concept["release_id"],
        }

    def stage_builtin_sync(self, created_by: str = "admin") -> dict[str, Any]:
        snapshot = self.bundled_snapshot()
        current = self.current_release()
        now = datetime.now().isoformat()
        import_id = self._stable_id("MKI", snapshot["version"], now)
        status = "no_changes" if current["version"] == snapshot["version"] else "staged"
        staged_release_id = current["release_id"] if status == "no_changes" else self._stable_id("MKR", snapshot["version"])
        diff = {
            "current_version": current["version"],
            "incoming_version": snapshot["version"],
            "added_concepts": 0 if status == "no_changes" else len(snapshot.get("concepts") or []),
            "changed_concepts": 0,
            "removed_concepts": 0,
        }
        if status == "staged":
            self._insert_snapshot(snapshot, staged_release_id, "staged", now)
        self.db.execute(
            """INSERT INTO mapping_knowledge_imports
               (import_id, source_name, source_url, source_version, status, staged_release_id,
                diff_json, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (import_id, snapshot["name"], "bundled://geochem_mapping_v1.json", snapshot["version"], status,
             staged_release_id, json.dumps(diff, ensure_ascii=False), created_by, now),
        )
        self.db.commit()
        return self.import_diff(import_id)

    def import_diff(self, import_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM mapping_knowledge_imports WHERE import_id = ?", (import_id,))
        if not row:
            raise ValueError("映射知识库导入记录不存在")
        result = dict(row)
        result["diff"] = json.loads(result.pop("diff_json", "{}") or "{}")
        return result

    def publish_import(self, import_id: str) -> dict[str, Any]:
        item = self.import_diff(import_id)
        if item["status"] == "no_changes":
            return {"import": item, "release": self.current_release()}
        if item["status"] != "staged":
            raise ValueError("该知识库导入不能发布")
        now = datetime.now().isoformat()
        self.db.execute("UPDATE mapping_knowledge_releases SET status = 'archived' WHERE status = 'published'")
        self.db.execute(
            "UPDATE mapping_knowledge_releases SET status = 'published', published_at = ? WHERE release_id = ?",
            (now, item["staged_release_id"]),
        )
        self.db.execute(
            "UPDATE mapping_knowledge_imports SET status = 'published', published_at = ? WHERE import_id = ?",
            (now, import_id),
        )
        self.db.commit()
        return {"import": self.import_diff(import_id), "release": self.current_release()}
