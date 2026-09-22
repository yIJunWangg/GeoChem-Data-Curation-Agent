"""Organization-scoped mapping rule submission, review and publication."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import pandas as pd

from ..core.project import ProjectManager
from ..mapping_knowledge import (
    AUTO_APPLY_THRESHOLD,
    MappingKnowledgeService,
    normalize_mapping_term,
    unit_dimension,
)


SUBMISSION_STATUSES = frozenset({"draft", "pending", "approved", "rejected"})
RULE_SCOPES = frozenset({"article", "project", "organization"})
IMPORT_COLUMNS = (
    "source_term",
    "target_canonical_field",
    "source_unit",
    "target_unit",
    "chemical_form",
    "context",
    "conversion_formula",
    "evidence",
    "notes",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex.upper()}"


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if value is not None else {}


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


class RuleGovernanceService:
    """Keep executable rules immutable and publish member proposals safely."""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    @staticmethod
    def can_submit(workspace_role: str, platform_roles: Iterable[str] = ()) -> bool:
        return "admin" in set(platform_roles) or workspace_role in {"owner", "curator", "reviewer"}

    @staticmethod
    def can_approve(workspace_role: str, platform_roles: Iterable[str] = ()) -> bool:
        return "admin" in set(platform_roles) or workspace_role == "owner"

    def _context(self, db: Any, project_id: str) -> dict[str, str]:
        project = db.fetch_one(
            "SELECT project_id, organization_id FROM projects WHERE project_id = ?",
            (project_id,),
        )
        if not project:
            raise ValueError("工作区不存在。")
        organization_id = str(_row(project).get("organization_id") or "ORG_DEFAULT")
        # Legacy per-project SQLite databases may predate the organization
        # registry while already carrying the default organization_id column.
        # Seed the referenced row idempotently before governance writes so the
        # foreign-key boundary is equally reliable in local and PostgreSQL modes.
        organization = db.fetch_one(
            "SELECT organization_id FROM organizations WHERE organization_id = ?",
            (organization_id,),
        )
        if not organization:
            now = _now()
            slug = "geochem-default" if organization_id == "ORG_DEFAULT" else f"org-{organization_id.lower()}"
            db.execute(
                """INSERT INTO organizations
                   (organization_id, name, slug, status, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, 'active', 'system-migration', ?, ?)""",
                (organization_id, organization_id, slug, now, now),
            )
            db.commit()
        return {
            "project_id": project_id,
            "organization_id": organization_id,
        }

    @staticmethod
    def _decode_rule(value: Any) -> dict[str, Any]:
        item = _row(value)
        item["conditions"] = _loads(item.get("conditions"), {})
        item["enabled"] = bool(item.get("enabled"))
        item["revision"] = int(item.get("revision") or 1)
        item["usage_count"] = int(item.get("usage_count") or 0)
        item["related_article_count"] = int(item.get("related_article_count") or 0)
        return item

    @staticmethod
    def _decode_submission(value: Any) -> dict[str, Any]:
        item = _row(value)
        item["validation"] = _loads(item.pop("validation_json", "{}"), {})
        item["confidence"] = float(item.get("confidence") or 0.0)
        return item

    def list_rules(
        self,
        project_id: str,
        *,
        q: str = "",
        scope: str = "",
        status: str = "confirmed",
        chemical_form: str = "",
        submitter: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            clauses = [
                "(r.project_id = ? OR (r.scope = 'organization' AND r.organization_id = ?))"
            ]
            params: list[Any] = [project_id, context["organization_id"]]
            if status:
                clauses.append("r.review_status = ?")
                params.append(status)
            if scope:
                clauses.append("r.scope = ?")
                params.append(scope)
            if submitter:
                clauses.append("r.created_by = ?")
                params.append(submitter)
            if q:
                clauses.append(
                    "(LOWER(r.pattern) LIKE ? OR LOWER(r.target_field) LIKE ? "
                    "OR LOWER(r.target_header) LIKE ? OR LOWER(r.target_unit) LIKE ?)"
                )
                needle = f"%{q.strip().lower()}%"
                params.extend([needle, needle, needle, needle])
            params.append(max(1, min(int(limit), 2000)))
            rows = db.fetch_all(
                f"""SELECT r.*,
                           COALESCE((SELECT COUNT(*) FROM candidate_cells c
                                     WHERE c.applied_rule_id = r.rule_id), 0) AS usage_count,
                           COALESCE((SELECT COUNT(DISTINCT b.article_id)
                                     FROM candidate_cells c
                                     JOIN candidate_records cr ON cr.candidate_record_id = c.candidate_record_id
                                     JOIN extraction_batches b ON b.batch_id = cr.batch_id
                                     WHERE c.applied_rule_id = r.rule_id), 0) AS related_article_count,
                           COALESCE(up.display_name, up.username, r.created_by, '') AS submitter_name
                    FROM learned_extraction_rules r
                    LEFT JOIN user_profiles up ON up.user_id = r.created_by
                    WHERE {' AND '.join(clauses)}
                    ORDER BY CASE r.scope WHEN 'article' THEN 0 WHEN 'project' THEN 1 ELSE 2 END,
                             COALESCE(r.published_at, r.created_at) DESC
                    LIMIT ?""",
                tuple(params),
            )
            items = [self._decode_rule(row) for row in rows]
            if chemical_form:
                expected = chemical_form.strip().lower()
                items = [
                    item for item in items
                    if str(item["conditions"].get("chemical_form") or "").lower() == expected
                ]
            return {
                "items": items,
                "count": len(items),
                "organization_id": context["organization_id"],
            }
        finally:
            db.close()

    def rule(self, project_id: str, rule_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            row = db.fetch_one(
                """SELECT r.*,
                          COALESCE((SELECT COUNT(*) FROM candidate_cells c
                                    WHERE c.applied_rule_id=r.rule_id), 0) AS usage_count,
                          COALESCE((SELECT COUNT(DISTINCT b.article_id)
                                    FROM candidate_cells c
                                    JOIN candidate_records cr ON cr.candidate_record_id=c.candidate_record_id
                                    JOIN extraction_batches b ON b.batch_id=cr.batch_id
                                    WHERE c.applied_rule_id=r.rule_id), 0) AS related_article_count,
                          COALESCE(up.display_name, up.username, r.created_by, '') AS submitter_name
                   FROM learned_extraction_rules r
                   LEFT JOIN user_profiles up ON up.user_id=r.created_by
                   WHERE r.rule_id=?
                     AND (r.project_id=? OR (r.scope='organization' AND r.organization_id=?))""",
                (rule_id, project_id, context["organization_id"]),
            )
            if not row:
                raise ValueError("规则不存在或当前工作区无权查看。")
            item = self._decode_rule(row)
            item["history"] = self.rule_history(project_id, rule_id, _db=db)
            return item
        finally:
            db.close()

    def rule_history(self, project_id: str, rule_id: str, *, _db: Any | None = None) -> list[dict[str, Any]]:
        owns_db = _db is None
        db = _db or self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            current = db.fetch_one(
                """SELECT * FROM learned_extraction_rules
                   WHERE rule_id=?
                     AND (project_id=? OR (scope='organization' AND organization_id=?))""",
                (rule_id, project_id, context["organization_id"]),
            )
            if not current:
                raise ValueError("规则不存在或当前工作区无权查看。")
            value = _row(current)
            scope = str(value.get("scope") or "project")
            if scope == "organization":
                rows = db.fetch_all(
                    """SELECT * FROM learned_extraction_rules
                       WHERE scope='organization' AND organization_id=?
                       ORDER BY revision DESC, created_at DESC""",
                    (context["organization_id"],),
                )
            elif scope == "article":
                rows = db.fetch_all(
                    """SELECT * FROM learned_extraction_rules
                       WHERE scope='article' AND project_id=? AND COALESCE(article_id, '')=?
                       ORDER BY revision DESC, created_at DESC""",
                    (project_id, str(value.get("article_id") or "")),
                )
            else:
                rows = db.fetch_all(
                    """SELECT * FROM learned_extraction_rules
                       WHERE scope='project' AND project_id=?
                       ORDER BY revision DESC, created_at DESC""",
                    (project_id,),
                )
            source_key = normalize_mapping_term(str(value.get("pattern") or ""))
            target_key = normalize_mapping_term(str(value.get("target_field") or value.get("target_header") or ""))
            return [
                self._decode_rule(row) for row in rows
                if normalize_mapping_term(str(_row(row).get("pattern") or "")) == source_key
                and normalize_mapping_term(
                    str(_row(row).get("target_field") or _row(row).get("target_header") or "")
                ) == target_key
            ]
        finally:
            if owns_db:
                db.close()

    def _validate(self, db: Any, context: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        source_term = str(payload.get("source_term") or "").strip()
        target = str(payload.get("target_canonical_field") or "").strip()
        source_unit = str(payload.get("source_unit") or "").strip()
        target_unit = str(payload.get("target_unit") or "").strip()
        scope = str(payload.get("scope") or "organization").strip().lower()
        errors: list[str] = []
        warnings: list[str] = []
        if not source_term:
            errors.append("原始术语不能为空")
        if not target:
            errors.append("目标规范字段不能为空")
        if scope not in RULE_SCOPES:
            errors.append("规则范围必须是 article、project 或 organization")
        if scope == "article" and not str(payload.get("article_id") or "").strip():
            errors.append("文章级规则必须指定 article_id")

        source_dimension = unit_dimension(source_unit)
        target_dimension = unit_dimension(target_unit)
        unit_compatibility = "unknown"
        if source_dimension and target_dimension:
            unit_compatibility = "compatible" if source_dimension == target_dimension else "incompatible"
            if unit_compatibility == "incompatible":
                errors.append("源单位与目标单位的维度不兼容")

        knowledge = MappingKnowledgeService(db)
        source_matches = knowledge.search(source_term, limit=3)["items"]
        target_matches = knowledge.search(target, limit=3)["items"]
        source_concept = next((item for item in source_matches if item["match_score"] >= AUTO_APPLY_THRESHOLD), None)
        target_concept = next((item for item in target_matches if item["match_score"] >= AUTO_APPLY_THRESHOLD), None)
        known_safe = True
        knowledge_score = 0.0
        concept_id = ""
        release_id = knowledge.current_release()["release_id"]
        chemical_form = str(payload.get("chemical_form") or "").strip()
        if source_concept and target_concept:
            known_safe = source_concept["concept_id"] == target_concept["concept_id"]
            knowledge_score = min(float(source_concept["match_score"]), float(target_concept["match_score"]))
            concept_id = str(target_concept["concept_id"])
            inferred_form = str(target_concept.get("chemical_form") or "")
            if chemical_form and inferred_form and chemical_form != inferred_form:
                known_safe = False
            chemical_form = chemical_form or inferred_form
            if not known_safe:
                errors.append(
                    f"化学形态或规范概念冲突：{source_concept['canonical_name']} 不能自动映射为 "
                    f"{target_concept['canonical_name']}"
                )
        elif target_concept:
            concept_id = str(target_concept["concept_id"])
            chemical_form = chemical_form or str(target_concept.get("chemical_form") or "")
            warnings.append("知识库未识别原始术语，发布后仅作为人工确认候选")
        else:
            warnings.append("知识库未识别目标规范字段，发布后不会自动应用")

        existing_rows = db.fetch_all(
            """SELECT rule_id, pattern, target_field, target_header, scope, conditions
               FROM learned_extraction_rules
               WHERE review_status='confirmed' AND enabled=1
                 AND (project_id=? OR (scope='organization' AND organization_id=?))""",
            (context["project_id"], context["organization_id"]),
        )
        source_key = normalize_mapping_term(source_term)
        target_key = normalize_mapping_term(target)
        duplicates: list[str] = []
        conflicts: list[dict[str, str]] = []
        for row in existing_rows:
            item = _row(row)
            if item.get("scope") != scope or normalize_mapping_term(item.get("pattern") or "") != source_key:
                continue
            current_target = str(item.get("target_field") or item.get("target_header") or "")
            if normalize_mapping_term(current_target) == target_key:
                duplicates.append(str(item.get("rule_id") or ""))
            else:
                conflicts.append({"rule_id": str(item.get("rule_id") or ""), "target": current_target})

        if duplicates and str(payload.get("supersedes_rule_id") or "") not in duplicates:
            warnings.append("已存在相同的已发布规则")
        if conflicts and not str(payload.get("supersedes_rule_id") or ""):
            warnings.append("同范围存在映射到其他目标字段的规则，审批时必须选择被替代版本")

        auto_apply_allowed = bool(
            not errors
            and source_concept
            and target_concept
            and known_safe
            and knowledge_score >= AUTO_APPLY_THRESHOLD
            and unit_compatibility != "incompatible"
            and not conflicts
        )
        conflict_status = "conflict" if conflicts or errors else ("duplicate" if duplicates else "clear")
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "conflict_status": conflict_status,
            "duplicates": duplicates,
            "conflicts": conflicts,
            "knowledge_concept_id": concept_id,
            "knowledge_release_id": release_id,
            "knowledge_score": round(knowledge_score, 3),
            "chemical_form": chemical_form,
            "unit_compatibility": unit_compatibility,
            "auto_apply_allowed": auto_apply_allowed,
        }

    def create_submission(
        self,
        project_id: str,
        actor_id: str,
        workspace_role: str,
        platform_roles: Iterable[str],
        payload: dict[str, Any],
        *,
        import_id: str = "",
    ) -> dict[str, Any]:
        if not self.can_submit(workspace_role, platform_roles):
            raise PermissionError("当前工作区角色只能查看规则，不能提交规则。")
        submission_id = _id("RMS")
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            validation = self._validate(db, context, payload)
            now = _now()
            db.execute(
                """INSERT INTO mapping_rule_submissions
                   (submission_id, import_id, project_id, organization_id, article_id,
                    source_term, target_canonical_field, target_header, source_unit, target_unit,
                    chemical_form, context_text, conversion_formula, evidence, notes,
                    knowledge_concept_id, knowledge_release_id, scope, confidence, status,
                    conflict_status, validation_json, supersedes_rule_id, created_by,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)""",
                (
                    submission_id,
                    import_id,
                    project_id,
                    context["organization_id"],
                    str(payload.get("article_id") or ""),
                    str(payload.get("source_term") or "").strip(),
                    str(payload.get("target_canonical_field") or "").strip(),
                    str(payload.get("target_header") or "").strip(),
                    str(payload.get("source_unit") or "").strip(),
                    str(payload.get("target_unit") or "").strip(),
                    str(validation.get("chemical_form") or payload.get("chemical_form") or ""),
                    str(payload.get("context") or payload.get("context_text") or "").strip(),
                    str(payload.get("conversion_formula") or "").strip(),
                    str(payload.get("evidence") or "").strip(),
                    str(payload.get("notes") or "").strip(),
                    str(validation.get("knowledge_concept_id") or ""),
                    str(validation.get("knowledge_release_id") or ""),
                    str(payload.get("scope") or "organization").strip().lower(),
                    float(payload.get("confidence") or 0.95),
                    str(validation.get("conflict_status") or "clear"),
                    json.dumps(validation, ensure_ascii=False),
                    str(payload.get("supersedes_rule_id") or "") or None,
                    actor_id,
                    now,
                    now,
                ),
            )
            db.commit()
        finally:
            db.close()
        return self.submission(project_id, submission_id)

    def submission(self, project_id: str, submission_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            row = db.fetch_one(
                """SELECT s.*, COALESCE(up.display_name, up.username, s.created_by, '') AS submitter_name
                   FROM mapping_rule_submissions s
                   LEFT JOIN user_profiles up ON up.user_id=s.created_by
                   WHERE s.submission_id=? AND s.organization_id=?""",
                (submission_id, context["organization_id"]),
            )
            if not row:
                raise ValueError("规则提交不存在或当前组织无权查看。")
            item = self._decode_submission(row)
            item["reviews"] = [
                _row(review) for review in db.fetch_all(
                    "SELECT * FROM mapping_rule_reviews WHERE submission_id=? ORDER BY reviewed_at DESC",
                    (submission_id,),
                )
            ]
            return item
        finally:
            db.close()

    def list_submissions(
        self,
        project_id: str,
        actor_id: str,
        *,
        status: str = "",
        mine: bool = False,
        q: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            clauses = ["s.organization_id=?"]
            params: list[Any] = [context["organization_id"]]
            if status:
                clauses.append("s.status=?")
                params.append(status)
            if mine:
                clauses.append("s.created_by=?")
                params.append(actor_id)
            if q:
                needle = f"%{q.strip().lower()}%"
                clauses.append("(LOWER(s.source_term) LIKE ? OR LOWER(s.target_canonical_field) LIKE ?)")
                params.extend([needle, needle])
            params.append(max(1, min(int(limit), 2000)))
            rows = db.fetch_all(
                f"""SELECT s.*, COALESCE(up.display_name, up.username, s.created_by, '') AS submitter_name
                    FROM mapping_rule_submissions s
                    LEFT JOIN user_profiles up ON up.user_id=s.created_by
                    WHERE {' AND '.join(clauses)}
                    ORDER BY s.updated_at DESC LIMIT ?""",
                tuple(params),
            )
            return {"items": [self._decode_submission(row) for row in rows], "count": len(rows)}
        finally:
            db.close()

    def update_submission(
        self,
        project_id: str,
        submission_id: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            existing = db.fetch_one(
                "SELECT * FROM mapping_rule_submissions WHERE submission_id=? AND organization_id=?",
                (submission_id, context["organization_id"]),
            )
            item = _row(existing)
            if not item:
                raise ValueError("规则提交不存在。")
            if item.get("created_by") != actor_id:
                raise PermissionError("只能修改自己创建的规则草稿。")
            if item.get("status") != "draft":
                raise ValueError("只有草稿状态的规则可以修改。")
            merged = {**item, **payload}
            validation = self._validate(db, context, merged)
            fields = {
                "article_id": str(merged.get("article_id") or ""),
                "source_term": str(merged.get("source_term") or "").strip(),
                "target_canonical_field": str(merged.get("target_canonical_field") or "").strip(),
                "target_header": str(merged.get("target_header") or "").strip(),
                "source_unit": str(merged.get("source_unit") or "").strip(),
                "target_unit": str(merged.get("target_unit") or "").strip(),
                "chemical_form": str(validation.get("chemical_form") or merged.get("chemical_form") or ""),
                "context_text": str(merged.get("context") or merged.get("context_text") or "").strip(),
                "conversion_formula": str(merged.get("conversion_formula") or "").strip(),
                "evidence": str(merged.get("evidence") or "").strip(),
                "notes": str(merged.get("notes") or "").strip(),
                "knowledge_concept_id": str(validation.get("knowledge_concept_id") or ""),
                "knowledge_release_id": str(validation.get("knowledge_release_id") or ""),
                "scope": str(merged.get("scope") or "organization"),
                "confidence": float(merged.get("confidence") or 0.95),
                "conflict_status": str(validation.get("conflict_status") or "clear"),
                "validation_json": json.dumps(validation, ensure_ascii=False),
                "supersedes_rule_id": str(merged.get("supersedes_rule_id") or "") or None,
                "updated_at": _now(),
            }
            assignments = ", ".join(f"{key}=?" for key in fields)
            db.execute(
                f"UPDATE mapping_rule_submissions SET {assignments} WHERE submission_id=?",
                (*fields.values(), submission_id),
            )
            db.commit()
        finally:
            db.close()
        return self.submission(project_id, submission_id)

    def submit(self, project_id: str, submission_id: str, actor_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one(
                "SELECT * FROM mapping_rule_submissions WHERE submission_id=? AND project_id=?",
                (submission_id, project_id),
            )
            item = _row(row)
            if not item:
                raise ValueError("规则提交不存在。")
            if item.get("created_by") != actor_id:
                raise PermissionError("只能提交自己创建的规则草稿。")
            if item.get("status") != "draft":
                raise ValueError("只有草稿可以提交审批。")
            validation = _loads(item.get("validation_json"), {})
            if not validation.get("valid"):
                raise ValueError("规则校验未通过，请先修正错误。")
            now = _now()
            db.execute(
                "UPDATE mapping_rule_submissions SET status='pending', submitted_at=?, updated_at=? WHERE submission_id=?",
                (now, now, submission_id),
            )
            if item.get("import_id"):
                db.execute(
                    "UPDATE mapping_rule_imports SET status='submitted', submitted_at=? WHERE import_id=?",
                    (now, item["import_id"]),
                )
            db.commit()
        finally:
            db.close()
        return self.submission(project_id, submission_id)

    def import_file(
        self,
        project_id: str,
        actor_id: str,
        workspace_role: str,
        platform_roles: Iterable[str],
        path: Path,
        filename: str,
    ) -> dict[str, Any]:
        if not self.can_submit(workspace_role, platform_roles):
            raise PermissionError("当前工作区角色不能上传规则。")
        suffix = path.suffix.lower()
        if suffix == ".csv":
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        elif suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, dtype=str, keep_default_na=False)
        else:
            raise ValueError("规则批量上传仅支持 CSV、XLSX 或 XLS。")
        frame.columns = [str(column).strip() for column in frame.columns]
        missing = [column for column in ("source_term", "target_canonical_field") if column not in frame.columns]
        if missing:
            raise ValueError(f"规则文件缺少必填列：{', '.join(missing)}")

        raw_bytes = path.read_bytes()
        import_id = _id("RMI")
        db = self.pm.get_database(project_id)
        created_ids: list[str] = []
        try:
            context = self._context(db, project_id)
            preview: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str, str]] = set()
            rows: list[dict[str, Any]] = []
            for index, raw in frame.iterrows():
                payload = {column: str(raw.get(column, "") or "").strip() for column in IMPORT_COLUMNS}
                payload["scope"] = "organization"
                key = (
                    normalize_mapping_term(payload["source_term"]),
                    normalize_mapping_term(payload["target_canonical_field"]),
                    payload["source_unit"].lower(),
                    payload["chemical_form"].lower(),
                )
                item = {"row": int(index) + 2, **payload}
                if not payload["source_term"] or not payload["target_canonical_field"]:
                    item["status"] = "invalid"
                    item["errors"] = ["source_term 和 target_canonical_field 为必填列"]
                    errors.append(item)
                elif key in seen:
                    item["status"] = "duplicate_in_file"
                    item["errors"] = ["文件内存在重复规则"]
                    errors.append(item)
                else:
                    seen.add(key)
                    validation = self._validate(db, context, payload)
                    item["status"] = "valid" if validation["valid"] else "invalid"
                    item["validation"] = validation
                    if validation["valid"]:
                        rows.append(payload)
                    else:
                        errors.append(item)
                preview.append(item)
            now = _now()
            db.execute(
                """INSERT INTO mapping_rule_imports
                   (import_id, project_id, organization_id, filename, file_hash, status,
                    total_rows, valid_rows, invalid_rows, preview_json, errors_json,
                    created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, 'parsed', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    import_id,
                    project_id,
                    context["organization_id"],
                    filename,
                    hashlib.sha256(raw_bytes).hexdigest(),
                    len(preview),
                    len(rows),
                    len(errors),
                    json.dumps(preview, ensure_ascii=False),
                    json.dumps(errors, ensure_ascii=False),
                    actor_id,
                    now,
                ),
            )
            db.commit()
        finally:
            db.close()

        for payload in rows:
            created = self.create_submission(
                project_id,
                actor_id,
                workspace_role,
                platform_roles,
                payload,
                import_id=import_id,
            )
            created_ids.append(created["submission_id"])
        return self.import_preview(
            project_id,
            import_id,
            actor_id=actor_id,
            can_view_all=self.can_approve(workspace_role, platform_roles),
        ) | {"submission_ids": created_ids}

    def import_preview(
        self,
        project_id: str,
        import_id: str,
        *,
        actor_id: str = "",
        can_view_all: bool = False,
    ) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            row = db.fetch_one(
                "SELECT * FROM mapping_rule_imports WHERE import_id=? AND organization_id=?",
                (import_id, context["organization_id"]),
            )
            if not row:
                raise ValueError("规则导入批次不存在。")
            item = _row(row)
            if actor_id and not can_view_all and str(item.get("created_by") or "") != actor_id:
                raise PermissionError("只能查看自己上传的规则批次。")
            item["preview"] = _loads(item.pop("preview_json", "[]"), [])
            item["errors"] = _loads(item.pop("errors_json", "[]"), [])
            return item
        finally:
            db.close()

    def approve(
        self,
        project_id: str,
        submission_id: str,
        actor_id: str,
        workspace_role: str,
        platform_roles: Iterable[str],
        comment: str = "",
    ) -> dict[str, Any]:
        if not self.can_approve(workspace_role, platform_roles):
            raise PermissionError("只有组织 Owner 或平台管理员可以批准规则。")
        rule_id = ""
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            row = db.fetch_one(
                "SELECT * FROM mapping_rule_submissions WHERE submission_id=? AND organization_id=?",
                (submission_id, context["organization_id"]),
            )
            item = _row(row)
            if not item:
                raise ValueError("规则提交不存在。")
            if item.get("status") != "pending":
                raise ValueError("只有待审批规则可以发布。")
            validation = self._validate(db, context, item)
            if not validation["valid"]:
                raise ValueError("规则校验未通过，不能发布。")
            supersedes = str(item.get("supersedes_rule_id") or "")
            if validation["conflicts"] and not supersedes:
                raise ValueError("规则与现有规则冲突，请先指定要替代的规则版本。")
            duplicate_ids = validation.get("duplicates") or []
            if duplicate_ids and not supersedes:
                raise ValueError("相同规则已经发布，无需重复发布。")

            revision = 1
            if supersedes:
                old = db.fetch_one(
                    """SELECT rule_id, revision, organization_id, project_id, article_id,
                              scope, pattern, review_status, enabled
                       FROM learned_extraction_rules WHERE rule_id=?""",
                    (supersedes,),
                )
                old_item = _row(old)
                if not old_item or str(old_item.get("organization_id") or "") != context["organization_id"]:
                    raise ValueError("被替代规则不存在或不属于当前组织。")
                allowed_supersedes = {
                    str(rule_id)
                    for rule_id in duplicate_ids
                    if str(rule_id)
                }
                allowed_supersedes.update(
                    str(conflict.get("rule_id") or "")
                    for conflict in validation.get("conflicts") or []
                    if str(conflict.get("rule_id") or "")
                )
                if supersedes not in allowed_supersedes:
                    raise ValueError("被替代规则与当前提交不属于同一映射冲突。")
                requested_scope = str(item.get("scope") or "organization")
                if str(old_item.get("scope") or "project") != requested_scope:
                    raise ValueError("被替代规则与当前提交的作用范围不一致。")
                if normalize_mapping_term(str(old_item.get("pattern") or "")) != normalize_mapping_term(
                    str(item.get("source_term") or "")
                ):
                    raise ValueError("被替代规则与当前提交的原始术语不一致。")
                if requested_scope in {"project", "article"} and str(old_item.get("project_id") or "") != str(
                    item.get("project_id") or project_id
                ):
                    raise ValueError("被替代规则不属于原提交工作区。")
                if requested_scope == "article" and str(old_item.get("article_id") or "") != str(
                    item.get("article_id") or ""
                ):
                    raise ValueError("被替代规则不属于当前文章。")
                if str(old_item.get("review_status") or "") != "confirmed" or not bool(old_item.get("enabled")):
                    raise ValueError("只能替代当前启用的已发布规则。")
                revision = int(old_item.get("revision") or 1) + 1

            now = _now()
            rule_id = _id("RULE")
            rule_project_id = str(item.get("project_id") or project_id)
            if str(item.get("scope") or "organization") != "organization" and rule_project_id != project_id:
                raise ValueError("项目级或文章级规则必须在原提交工作区审批。")
            conditions = {
                "source_unit": item.get("source_unit") or "",
                "target_unit": item.get("target_unit") or "",
                "chemical_form": validation.get("chemical_form") or item.get("chemical_form") or "",
                "context": item.get("context_text") or "",
                "conversion_formula": item.get("conversion_formula") or "",
                "knowledge_concept_id": validation.get("knowledge_concept_id") or "",
                "knowledge_release_id": validation.get("knowledge_release_id") or "",
                "knowledge_score": validation.get("knowledge_score") or 0.0,
                "unit_compatibility": validation.get("unit_compatibility") or "unknown",
                "auto_apply_allowed": bool(validation.get("auto_apply_allowed")),
                "notes": item.get("notes") or "",
            }
            if supersedes:
                db.execute(
                    """UPDATE learned_extraction_rules
                       SET review_status='superseded', enabled=0
                       WHERE rule_id=?""",
                    (supersedes,),
                )
            db.execute(
                """INSERT INTO learned_extraction_rules
                   (rule_id, project_id, article_id, target_field, target_header, target_unit,
                    rule_type, source_type, pattern, evidence, conditions, confidence, risk_level,
                    review_status, scope, enabled, organization_id, source_submission_id, revision,
                    supersedes_rule_id, published_by, published_at, created_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, 'source_alias', 'organization_submission', ?, ?, ?, ?, ?,
                           'confirmed', ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule_id,
                    rule_project_id,
                    str(item.get("article_id") or "") or None,
                    str(item.get("target_canonical_field") or ""),
                    str(item.get("target_header") or item.get("target_canonical_field") or ""),
                    str(item.get("target_unit") or ""),
                    str(item.get("source_term") or ""),
                    str(item.get("evidence") or ""),
                    json.dumps(conditions, ensure_ascii=False),
                    float(item.get("confidence") or 0.95),
                    "low" if validation.get("auto_apply_allowed") else "medium",
                    str(item.get("scope") or "organization"),
                    context["organization_id"],
                    submission_id,
                    revision,
                    supersedes or None,
                    actor_id,
                    now,
                    now,
                    str(item.get("created_by") or actor_id),
                ),
            )
            db.execute(
                """UPDATE mapping_rule_submissions
                   SET status='approved', reviewed_at=?, updated_at=?, validation_json=?,
                       conflict_status=? WHERE submission_id=?""",
                (
                    now,
                    now,
                    json.dumps(validation, ensure_ascii=False),
                    validation.get("conflict_status") or "clear",
                    submission_id,
                ),
            )
            db.execute(
                """INSERT INTO mapping_rule_reviews
                   (review_id, submission_id, decision, comment, snapshot_json, reviewed_by, reviewed_at)
                   VALUES (?, ?, 'approved', ?, ?, ?, ?)""",
                (_id("RMR"), submission_id, comment, json.dumps(item, ensure_ascii=False), actor_id, now),
            )
            db.commit()
        finally:
            db.close()
        return {"submission": self.submission(project_id, submission_id), "rule": self.rule(project_id, rule_id)}

    def reject(
        self,
        project_id: str,
        submission_id: str,
        actor_id: str,
        workspace_role: str,
        platform_roles: Iterable[str],
        comment: str,
    ) -> dict[str, Any]:
        if not self.can_approve(workspace_role, platform_roles):
            raise PermissionError("只有组织 Owner 或平台管理员可以驳回规则。")
        if not comment.strip():
            raise ValueError("驳回规则时必须填写原因。")
        db = self.pm.get_database(project_id)
        try:
            context = self._context(db, project_id)
            row = db.fetch_one(
                "SELECT * FROM mapping_rule_submissions WHERE submission_id=? AND organization_id=?",
                (submission_id, context["organization_id"]),
            )
            item = _row(row)
            if not item:
                raise ValueError("规则提交不存在。")
            if item.get("status") != "pending":
                raise ValueError("只有待审批规则可以驳回。")
            now = _now()
            db.execute(
                "UPDATE mapping_rule_submissions SET status='rejected', reviewed_at=?, updated_at=? WHERE submission_id=?",
                (now, now, submission_id),
            )
            db.execute(
                """INSERT INTO mapping_rule_reviews
                   (review_id, submission_id, decision, comment, snapshot_json, reviewed_by, reviewed_at)
                   VALUES (?, ?, 'rejected', ?, ?, ?, ?)""",
                (_id("RMR"), submission_id, comment, json.dumps(item, ensure_ascii=False), actor_id, now),
            )
            db.commit()
        finally:
            db.close()
        return self.submission(project_id, submission_id)
