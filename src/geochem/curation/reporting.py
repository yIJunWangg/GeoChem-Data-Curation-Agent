"""Trace, cost, and audit-package reporting utilities."""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.database import Database


class TraceService:
    """Build row-level trace reports from standardized records."""

    def trace_row(self, db: Database, record_id: str) -> dict[str, Any]:
        record = db.fetch_one("SELECT * FROM standardized_records WHERE record_id = ?", (record_id,))
        if not record:
            raise ValueError(f"Standardized record not found: {record_id}")
        patches = db.fetch_all(
            """SELECT * FROM record_patches
            WHERE table_id = ? AND (row_id = ? OR sample_id = ?)""",
            (record["table_id"], record["row_id"], self._sample_id(record)),
        )
        reviews = db.fetch_all(
            """SELECT r.*, d.action, d.target_field, d.target_unit, d.rule_scope
            FROM review_items r LEFT JOIN review_decisions d ON r.review_id = d.review_id
            WHERE r.table_id = ? AND (r.row_id = ? OR r.row_id IS NULL)""",
            (record["table_id"], record["row_id"]),
        )
        calculations = db.fetch_all(
            "SELECT * FROM calculation_records WHERE table_id = ? AND row_id = ?",
            (record["table_id"], record["row_id"]),
        )
        return {
            "record_id": record["record_id"],
            "article_id": record["article_id"],
            "table_id": record["table_id"],
            "row_id": record["row_id"],
            "source_file": record["source_file"],
            "source_table": record["source_table"],
            "source_row": record["source_row"],
            "quality_grade": record["quality_grade"],
            "data": json.loads(record["data"]),
            "original_fields": json.loads(record["original_fields"]),
            "review_statuses": json.loads(record["review_statuses"]),
            "confidence_scores": json.loads(record["confidence_scores"]),
            "patches": [dict(p) for p in patches],
            "reviews": [dict(r) for r in reviews],
            "calculations": [dict(c) for c in calculations],
        }

    def trace_table(self, db: Database, table_id: str) -> list[dict[str, Any]]:
        records = db.fetch_all("SELECT * FROM standardized_records WHERE table_id = ? ORDER BY source_row, record_id", (table_id,))
        summary = []
        for record in records:
            patch_count = db.fetch_one(
                "SELECT COUNT(*) as cnt FROM record_patches WHERE table_id = ? AND row_id = ?",
                (table_id, record["row_id"]),
            )["cnt"]
            review_count = db.fetch_one(
                "SELECT COUNT(*) as cnt FROM review_items WHERE table_id = ? AND (row_id = ? OR row_id IS NULL)",
                (table_id, record["row_id"]),
            )["cnt"]
            calc_count = db.fetch_one(
                "SELECT COUNT(*) as cnt FROM calculation_records WHERE table_id = ? AND row_id = ?",
                (table_id, record["row_id"]),
            )["cnt"]
            summary.append({
                "record_id": record["record_id"],
                "row_id": record["row_id"],
                "source_file": record["source_file"],
                "source_table": record["source_table"],
                "source_row": record["source_row"],
                "quality_grade": record["quality_grade"],
                "patch_count": patch_count,
                "review_count": review_count,
                "calculation_count": calc_count,
            })
        return summary

    def write_trace_files(self, db: Database, table_id: str, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = self.trace_table(db, table_id)
        csv_path = out_dir / "trace_rows.csv"
        fields = [
            "record_id", "row_id", "source_file", "source_table", "source_row",
            "quality_grade", "patch_count", "review_count", "calculation_count",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        md = ["# Row Trace Summary", "", f"- Table: {table_id}", f"- Records: {len(rows)}", ""]
        by_grade: dict[str, int] = {}
        for row in rows:
            by_grade[row["quality_grade"]] = by_grade.get(row["quality_grade"], 0) + 1
        for grade, count in sorted(by_grade.items()):
            md.append(f"- Grade {grade}: {count}")
        (out_dir / "trace_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    def _sample_id(self, record) -> str:
        try:
            data = json.loads(record["data"])
        except Exception:
            return ""
        return str(data.get("SampleID") or data.get("sample_id") or "")


class CostReporter:
    """Aggregate LLM call costs by useful dimensions."""

    GROUPS = {
        "model": ["model_provider", "model_name"],
        "task": ["skill_name"],
        "agent": ["agent_name"],
        "article": ["article_id"],
        "provider": ["model_provider"],
    }

    def report(self, db: Database, project_id: str, group_by: str = "model") -> list[dict[str, Any]]:
        fields = self.GROUPS.get(group_by, self.GROUPS["model"])
        select = ", ".join(fields)
        group = ", ".join(fields)
        rows = db.fetch_all(
            f"""SELECT {select},
                       COUNT(*) as calls,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(total_tokens) as total_tokens,
                       SUM(estimated_cost) as estimated_cost
                FROM llm_calls
                WHERE project_id = ?
                GROUP BY {group}
                ORDER BY total_tokens DESC""",
            (project_id,),
        )
        return [dict(row) for row in rows]

    def write_files(self, db: Database, project_id: str, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = self.report(db, project_id, "model")
        fields = ["model_provider", "model_name", "calls", "input_tokens", "output_tokens", "total_tokens", "estimated_cost"]
        with open(out_dir / "llm_cost_report.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        total = sum(row.get("estimated_cost") or 0 for row in rows)
        md = ["# LLM Cost Report", "", f"- Project: {project_id}", f"- Total estimated cost: ${total:.4f}", ""]
        for row in rows:
            md.append(
                f"- {row.get('model_provider', '')}/{row.get('model_name', '')}: "
                f"{row.get('calls', 0)} calls, {row.get('total_tokens') or 0} tokens, "
                f"${(row.get('estimated_cost') or 0):.4f}"
            )
        (out_dir / "llm_cost_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")


class AuditPackageBuilder:
    """Create a directory containing export artifacts and reviewable audit files."""

    def create(
        self,
        db: Database,
        project_id: str,
        project_dir: Path,
        table_id: str | None,
        data_file: Path,
        export_format: str,
    ) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        package_dir = project_dir / "output" / f"audit_package_{stamp}"
        package_dir.mkdir(parents=True, exist_ok=True)
        if data_file.exists():
            shutil.copy2(data_file, package_dir / data_file.name)
        self._write_candidate_summary(db, table_id, package_dir)
        self._write_reviews(db, table_id, package_dir)
        self._write_rules(db, project_id, package_dir)
        self._write_patches(db, table_id, package_dir)
        if table_id:
            TraceService().write_trace_files(db, table_id, package_dir)
        CostReporter().write_files(db, project_id, package_dir)
        self._copy_calculations(project_dir, package_dir)
        self._record_export_job(db, project_id, table_id, data_file, package_dir, export_format)
        db.commit()
        return package_dir

    def _write_candidate_summary(self, db: Database, table_id: str | None, out_dir: Path) -> None:
        if table_id:
            tables = db.fetch_all("SELECT * FROM candidate_tables WHERE table_id = ?", (table_id,))
        else:
            tables = db.fetch_all("SELECT * FROM candidate_tables ORDER BY table_id")
        rows = [dict(t) for t in tables]
        self._write_csv(out_dir / "candidate_tables.csv", rows)

    def _write_reviews(self, db: Database, table_id: str | None, out_dir: Path) -> None:
        if table_id:
            rows = db.fetch_all("SELECT * FROM review_items WHERE table_id = ? ORDER BY created_at", (table_id,))
        else:
            rows = db.fetch_all("SELECT * FROM review_items ORDER BY created_at")
        self._write_csv(out_dir / "review_items.csv", [dict(r) for r in rows])
        decisions = db.fetch_all(
            """SELECT d.* FROM review_decisions d
            JOIN review_items r ON d.review_id = r.review_id
            WHERE (? IS NULL OR r.table_id = ?)
            ORDER BY d.decided_at""",
            (table_id, table_id),
        )
        self._write_csv(out_dir / "review_decisions.csv", [dict(d) for d in decisions])

    def _write_rules(self, db: Database, project_id: str, out_dir: Path) -> None:
        mapping = db.fetch_all("SELECT * FROM mapping_rules ORDER BY rule_id")
        learned = db.fetch_all("SELECT * FROM learned_extraction_rules WHERE project_id = ? ORDER BY rule_id", (project_id,))
        self._write_csv(out_dir / "mapping_rules.csv", [dict(r) for r in mapping])
        self._write_csv(out_dir / "learned_extraction_rules.csv", [dict(r) for r in learned])

    def _write_patches(self, db: Database, table_id: str | None, out_dir: Path) -> None:
        if table_id:
            patches = db.fetch_all("SELECT * FROM record_patches WHERE table_id = ? ORDER BY created_at", (table_id,))
            events = db.fetch_all(
                """SELECT e.* FROM teaching_events e
                WHERE e.table_id = ? OR e.event_id IN
                  (SELECT evidence_id FROM record_patches WHERE table_id = ? AND evidence_id IS NOT NULL)
                ORDER BY e.created_at""",
                (table_id, table_id),
            )
        else:
            patches = db.fetch_all("SELECT * FROM record_patches ORDER BY created_at")
            events = db.fetch_all("SELECT * FROM teaching_events ORDER BY created_at")
        self._write_csv(out_dir / "record_patches.csv", [dict(p) for p in patches])
        self._write_csv(out_dir / "teaching_events.csv", [dict(e) for e in events])

    def _copy_calculations(self, project_dir: Path, out_dir: Path) -> None:
        calc_dir = project_dir / "calculations"
        if calc_dir.exists():
            dest = out_dir / "calculations"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(calc_dir, dest)

    def _record_export_job(
        self,
        db: Database,
        project_id: str,
        table_id: str | None,
        data_file: Path,
        package_dir: Path,
        export_format: str,
    ) -> None:
        record_count = 0
        if table_id:
            row = db.fetch_one("SELECT COUNT(*) as cnt FROM standardized_records WHERE table_id = ?", (table_id,))
            record_count = row["cnt"] if row else 0
        article_id = None
        if table_id:
            table = db.fetch_one("SELECT article_id FROM candidate_tables WHERE table_id = ?", (table_id,))
            article_id = table["article_id"] if table else None
        job_id = self._generate_id(db, "EXP", "export_jobs", "job_id")
        db.execute(
            """INSERT INTO export_jobs
            (job_id, project_id, article_id, table_id, export_format, output_path,
             package_path, record_count, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                project_id,
                article_id,
                table_id,
                export_format,
                str(data_file),
                str(package_dir),
                record_count,
                "success",
                datetime.now().isoformat(),
            ),
        )

    def _write_csv(self, path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            path.write_text("", encoding="utf-8-sig")
            return
        fields = sorted({key for row in rows for key in row.keys()})
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def _generate_id(self, db: Database, prefix: str, table: str, column: str) -> str:
        row = db.fetch_one(
            f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id "
            f"FROM {table} WHERE {column} LIKE ?",
            (f"{prefix}_%",),
        )
        max_id = row["max_id"] if row and row["max_id"] else 0
        return f"{prefix}_{max_id + 1:03d}"
