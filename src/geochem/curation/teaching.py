"""User teaching loop: evidence, learned extraction rules, and record patches."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..core.database import Database
from ..core.models import RiskLevel, ReviewStatus
from ..providers.llm_client import LLMClient


class TeachingManager:
    """Persist user-provided evidence and patch values."""

    def add_event(
        self,
        db: Database,
        project_id: str,
        article_id: str,
        target_field: str,
        value: str,
        table_id: str | None = None,
        sample_id: str | None = None,
        target_header: str = "",
        unit: str | None = None,
        source_type: str = "",
        evidence: str = "",
        notes: str = "",
    ) -> dict[str, str]:
        row_id = self._find_row_id(db, table_id, sample_id) if table_id and sample_id else None
        event_id = self._generate_id(db, "TEACH", "teaching_events", "event_id")
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO teaching_events
            (event_id, project_id, article_id, table_id, row_id, sample_id, target_field,
             target_header, target_unit, value, source_type, evidence, notes, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                project_id,
                article_id,
                table_id,
                row_id,
                sample_id,
                target_field,
                target_header,
                unit,
                str(value),
                source_type,
                evidence,
                notes,
                ReviewStatus.CONFIRMED.value,
                now,
            ),
        )
        patch_id = self.add_patch(
            db=db,
            project_id=project_id,
            article_id=article_id,
            table_id=table_id,
            row_id=row_id,
            sample_id=sample_id,
            target_field=target_field,
            target_header=target_header,
            target_unit=unit,
            value=str(value),
            source_unit=unit,
            source_type=source_type,
            evidence_id=event_id,
            learned_rule_id=None,
            confidence=1.0,
            risk_level=RiskLevel.LOW.value,
            review_status=ReviewStatus.CONFIRMED.value,
            reason="User-provided teaching value",
        )
        db.commit()
        return {"event_id": event_id, "patch_id": patch_id}

    def add_patch(
        self,
        db: Database,
        project_id: str,
        article_id: str,
        target_field: str,
        value: str,
        table_id: str | None = None,
        row_id: str | None = None,
        sample_id: str | None = None,
        target_header: str = "",
        target_unit: str | None = None,
        source_unit: str | None = None,
        source_type: str = "",
        evidence_id: str | None = None,
        learned_rule_id: str | None = None,
        confidence: float = 1.0,
        risk_level: str = RiskLevel.LOW.value,
        review_status: str = ReviewStatus.CONFIRMED.value,
        reason: str = "",
    ) -> str:
        existing = db.fetch_one(
            """SELECT patch_id FROM record_patches
            WHERE project_id = ? AND article_id = ? AND COALESCE(table_id, '') = COALESCE(?, '')
              AND COALESCE(row_id, '') = COALESCE(?, '') AND COALESCE(sample_id, '') = COALESCE(?, '')
              AND target_field = ? AND value = ?""",
            (project_id, article_id, table_id, row_id, sample_id, target_field, str(value)),
        )
        if existing:
            return existing["patch_id"]
        patch_id = self._generate_id(db, "PATCH", "record_patches", "patch_id")
        db.execute(
            """INSERT INTO record_patches
            (patch_id, project_id, article_id, table_id, row_id, sample_id, target_field,
             target_header, target_unit, value, source_unit, source_type, evidence_id,
             learned_rule_id, confidence, risk_level, review_status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                patch_id,
                project_id,
                article_id,
                table_id,
                row_id,
                sample_id,
                target_field,
                target_header,
                target_unit,
                str(value),
                source_unit,
                source_type,
                evidence_id,
                learned_rule_id,
                confidence,
                risk_level,
                review_status,
                reason,
                datetime.now().isoformat(),
            ),
        )
        return patch_id

    def list_events(self, db: Database, project_id: str, article_id: str | None = None) -> list[Any]:
        if article_id:
            return db.fetch_all(
                "SELECT * FROM teaching_events WHERE project_id = ? AND article_id = ? ORDER BY created_at",
                (project_id, article_id),
            )
        return db.fetch_all("SELECT * FROM teaching_events WHERE project_id = ? ORDER BY created_at", (project_id,))

    def explain_patch(self, db: Database, patch_id: str) -> dict[str, Any]:
        patch = db.fetch_one("SELECT * FROM record_patches WHERE patch_id = ?", (patch_id,))
        if not patch:
            raise ValueError(f"Patch not found: {patch_id}")
        event = None
        rule = None
        if patch["evidence_id"]:
            event = db.fetch_one("SELECT * FROM teaching_events WHERE event_id = ?", (patch["evidence_id"],))
        if patch["learned_rule_id"]:
            rule = db.fetch_one("SELECT * FROM learned_extraction_rules WHERE rule_id = ?", (patch["learned_rule_id"],))
        return {"patch": dict(patch), "event": dict(event) if event else None, "rule": dict(rule) if rule else None}

    def _find_row_id(self, db: Database, table_id: str, sample_id: str) -> str | None:
        rows = db.fetch_all("SELECT row_id, raw_data FROM candidate_rows WHERE table_id = ?", (table_id,))
        for row in rows:
            try:
                data = json.loads(row["raw_data"])
            except Exception:
                continue
            for key in ("SampleID", "SampleId", "sample_id", "Sample ID"):
                if str(data.get(key, "")).strip() == str(sample_id).strip():
                    return row["row_id"]
        return None

    def _generate_id(self, db: Database, prefix: str, table: str, column: str) -> str:
        row = db.fetch_one(
            f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id "
            f"FROM {table} WHERE {column} LIKE ?",
            (f"{prefix}_%",),
        )
        max_id = row["max_id"] if row and row["max_id"] else 0
        return f"{prefix}_{max_id + 1:03d}"


class LearningEngine:
    """Turn teaching events into reusable extraction rules."""

    def __init__(self, llm_client: LLMClient | None = None, provider: str | None = None, model: str | None = None):
        self.llm_client = llm_client
        self.provider = provider
        self.model = model

    def learn(
        self,
        db: Database,
        project_id: str,
        article_id: str,
        memory_path: Path | None = None,
    ) -> list[dict[str, Any]]:
        events = db.fetch_all(
            "SELECT * FROM teaching_events WHERE project_id = ? AND article_id = ? ORDER BY created_at",
            (project_id, article_id),
        )
        if not events:
            return []
        proposals = self._llm_rules(project_id, article_id, events) if self.llm_client else []
        if not proposals:
            proposals = self._local_rules(events)
        saved = [self._save_rule(db, project_id, article_id, proposal) for proposal in proposals]
        db.commit()
        if memory_path:
            self.write_memory(db, project_id, memory_path)
        return saved

    def write_memory(self, db: Database, project_id: str, memory_path: Path) -> None:
        rules = db.fetch_all(
            "SELECT * FROM learned_extraction_rules WHERE project_id = ? ORDER BY rule_id",
            (project_id,),
        )
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rules": [dict(row) for row in rules]}
        memory_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        md = ["# Extraction Memory", "", "## Learned Extraction Rules", ""]
        for row in rules:
            md.extend([
                f"### {row['rule_id']}",
                f"- Target field: {row['target_field']}",
                f"- Target header: {row['target_header']}",
                f"- Unit: {row['target_unit'] or ''}",
                f"- Rule type: {row['rule_type']}",
                f"- Source type: {row['source_type']}",
                f"- Pattern: {row['pattern']}",
                f"- Confidence: {row['confidence']}",
                f"- Risk: {row['risk_level']}",
                f"- Scope: {row['scope']}",
                "",
            ])
        memory_path.with_name("extraction_memory.md").write_text("\n".join(md), encoding="utf-8")

    def _llm_rules(self, project_id: str, article_id: str, events: list[Any]) -> list[dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You infer reusable geochemical extraction rules from user teaching evidence. "
                    "Return only JSON with key rules. Each rule must include target_field, "
                    "target_header, target_unit, rule_type, source_type, pattern, conditions, "
                    "confidence, risk_level, and evidence. Do not invent row data."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "teaching_events": [
                            {
                                "target_field": e["target_field"],
                                "target_header": e["target_header"],
                                "target_unit": e["target_unit"],
                                "source_type": e["source_type"],
                                "evidence": e["evidence"],
                                "notes": e["notes"],
                            }
                            for e in events
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = self.llm_client.chat(
            messages=messages,
            task_name="teaching_rule_learning",
            provider_override=self.provider,
            model_override=self.model,
            project_id=project_id,
            article_id=article_id,
            agent_name="Teaching Agent",
            skill_name="rule_learning",
            use_cache=False,
        )
        try:
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1].removeprefix("json").strip()
            data = json.loads(text)
        except Exception:
            return []
        return data.get("rules", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []

    def _local_rules(self, events: list[Any]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str | None], list[Any]] = {}
        for event in events:
            key = (event["target_field"], event["target_header"] or "", event["target_unit"])
            grouped.setdefault(key, []).append(event)
        rules = []
        for (field, header, unit), items in grouped.items():
            evidence = " | ".join(i["evidence"] for i in items if i["evidence"])
            source_types = sorted({i["source_type"] for i in items if i["source_type"]})
            rules.append({
                "target_field": field,
                "target_header": header,
                "target_unit": unit,
                "rule_type": "field_location",
                "source_type": ",".join(source_types),
                "pattern": evidence[:500] or f"Use user-taught evidence for {field}",
                "conditions": {"sample_count": len(items)},
                "confidence": 0.9,
                "risk_level": RiskLevel.LOW.value,
                "review_status": ReviewStatus.CONFIRMED.value,
                "scope": "project",
                "created_by": "user",
                "evidence": evidence,
            })
        return rules

    def _save_rule(self, db: Database, project_id: str, article_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        target = proposal.get("target_field") or ""
        pattern = proposal.get("pattern") or proposal.get("evidence") or ""
        existing = db.fetch_one(
            """SELECT rule_id FROM learned_extraction_rules
            WHERE project_id = ? AND COALESCE(article_id, '') = COALESCE(?, '')
              AND target_field = ? AND pattern = ?""",
            (project_id, article_id, target, pattern),
        )
        rule_id = existing["rule_id"] if existing else self._generate_id(db, "LRULE", "learned_extraction_rules", "rule_id")
        confidence = float(proposal.get("confidence") or 0.8)
        risk_level = proposal.get("risk_level") or RiskLevel.MEDIUM.value
        review_status = proposal.get("review_status") or ReviewStatus.CONFIRMED.value
        if risk_level != RiskLevel.LOW.value or confidence < 0.9:
            review_status = ReviewStatus.PENDING.value
        row = {
            "rule_id": rule_id,
            "project_id": project_id,
            "article_id": article_id,
            "target_field": target,
            "target_header": proposal.get("target_header") or "",
            "target_unit": proposal.get("target_unit"),
            "rule_type": proposal.get("rule_type") or "value_extraction_pattern",
            "source_type": proposal.get("source_type") or "",
            "pattern": pattern,
            "evidence": proposal.get("evidence") or "",
            "conditions": json.dumps(proposal.get("conditions") or {}, ensure_ascii=False),
            "confidence": confidence,
            "risk_level": risk_level,
            "review_status": review_status,
            "scope": proposal.get("scope") or "project",
            "created_at": datetime.now().isoformat(),
            "created_by": proposal.get("created_by") or "agent",
        }
        if existing:
            db.execute(
                """UPDATE learned_extraction_rules
                SET target_header = ?, target_unit = ?, rule_type = ?, source_type = ?, evidence = ?,
                    conditions = ?, confidence = ?, risk_level = ?, review_status = ?, scope = ?,
                    created_at = ?, created_by = ?
                WHERE rule_id = ?""",
                (
                    row["target_header"], row["target_unit"], row["rule_type"], row["source_type"],
                    row["evidence"], row["conditions"], row["confidence"], row["risk_level"],
                    row["review_status"], row["scope"], row["created_at"], row["created_by"], rule_id,
                ),
            )
        else:
            db.execute(
                """INSERT INTO learned_extraction_rules
                (rule_id, project_id, article_id, target_field, target_header, target_unit,
                 rule_type, source_type, pattern, evidence, conditions, confidence, risk_level,
                 review_status, scope, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(row.values()),
            )
        if row["review_status"] == ReviewStatus.PENDING.value:
            self._create_rule_review(db, row)
        return row

    def _create_rule_review(self, db: Database, rule: dict[str, Any]) -> None:
        existing = db.fetch_one(
            """SELECT review_id FROM review_items
            WHERE item_type = 'learned_extraction_rule' AND ai_suggestion = ? AND status = 'pending'""",
            (rule["rule_id"],),
        )
        if existing:
            return
        review_id = self._generate_id(db, "REV", "review_items", "review_id")
        db.execute(
            """INSERT INTO review_items
            (review_id, article_id, table_id, item_type, risk_level, original_field,
             original_unit, original_value, ai_suggestion, confidence, available_actions,
             status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                review_id,
                rule["article_id"] or "",
                None,
                "learned_extraction_rule",
                rule["risk_level"],
                rule["target_field"],
                rule["target_unit"],
                rule["pattern"],
                rule["rule_id"],
                rule["confidence"],
                json.dumps(["accept", "reject", "defer"]),
                ReviewStatus.PENDING.value,
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


class RuleApplicationEngine:
    """Apply confirmed learned rules to create reusable patches."""

    def __init__(self, teaching_manager: TeachingManager | None = None):
        self.teaching = teaching_manager or TeachingManager()

    def apply(self, db: Database, project_id: str, table_id: str) -> int:
        table = db.fetch_one("SELECT * FROM candidate_tables WHERE table_id = ?", (table_id,))
        if not table:
            raise ValueError(f"Candidate table not found: {table_id}")
        article_id = table["article_id"]
        events = db.fetch_all(
            """SELECT * FROM teaching_events
            WHERE project_id = ? AND article_id = ? AND (table_id = ? OR table_id IS NULL)""",
            (project_id, article_id, table_id),
        )
        rules = db.fetch_all(
            """SELECT * FROM learned_extraction_rules
            WHERE project_id = ? AND review_status = 'confirmed'
              AND (article_id = ? OR article_id IS NULL OR scope = 'project')""",
            (project_id, article_id),
        )
        rule_by_field = {r["target_field"]: r for r in rules}
        created = 0
        for event in events:
            if not event["value"]:
                continue
            rule = rule_by_field.get(event["target_field"])
            if not rule:
                continue
            row_id = event["row_id"] or (self.teaching._find_row_id(db, table_id, event["sample_id"]) if event["sample_id"] else None)
            before = db.fetch_one("SELECT COUNT(*) as cnt FROM record_patches")
            self.teaching.add_patch(
                db=db,
                project_id=project_id,
                article_id=article_id,
                table_id=table_id,
                row_id=row_id,
                sample_id=event["sample_id"],
                target_field=event["target_field"],
                target_header=event["target_header"],
                target_unit=event["target_unit"],
                value=event["value"],
                source_unit=event["target_unit"],
                source_type=event["source_type"],
                evidence_id=event["event_id"],
                learned_rule_id=rule["rule_id"],
                confidence=rule["confidence"],
                risk_level=rule["risk_level"],
                review_status=rule["review_status"],
                reason=f"Applied learned rule {rule['rule_id']}",
            )
            after = db.fetch_one("SELECT COUNT(*) as cnt FROM record_patches")
            if after["cnt"] > before["cnt"]:
                created += 1
        db.commit()
        return created
