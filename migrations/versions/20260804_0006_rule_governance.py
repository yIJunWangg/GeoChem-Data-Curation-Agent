"""Add organization rule governance and immutable rule revisions.

Revision ID: 20260804_0006
Revises: 20260803_0005
"""

from alembic import op


revision = "20260804_0006"
down_revision = "20260803_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("The server migration target must be PostgreSQL")

    for statement in (
        "ALTER TABLE learned_extraction_rules ADD COLUMN IF NOT EXISTS organization_id TEXT DEFAULT ''",
        "ALTER TABLE learned_extraction_rules ADD COLUMN IF NOT EXISTS source_submission_id TEXT DEFAULT ''",
        "ALTER TABLE learned_extraction_rules ADD COLUMN IF NOT EXISTS revision INTEGER DEFAULT 1",
        "ALTER TABLE learned_extraction_rules ADD COLUMN IF NOT EXISTS supersedes_rule_id TEXT DEFAULT ''",
        "ALTER TABLE learned_extraction_rules ADD COLUMN IF NOT EXISTS published_by TEXT DEFAULT ''",
        "ALTER TABLE learned_extraction_rules ADD COLUMN IF NOT EXISTS published_at TEXT",
    ):
        op.execute(statement)

    op.execute(
        """CREATE TABLE IF NOT EXISTS mapping_rule_imports (
            import_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
            filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'parsed',
            total_rows INTEGER DEFAULT 0,
            valid_rows INTEGER DEFAULT 0,
            invalid_rows INTEGER DEFAULT 0,
            preview_json TEXT DEFAULT '[]',
            errors_json TEXT DEFAULT '[]',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            submitted_at TEXT
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS mapping_rule_submissions (
            submission_id TEXT PRIMARY KEY,
            import_id TEXT DEFAULT '',
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
            article_id TEXT DEFAULT '',
            source_term TEXT NOT NULL,
            target_canonical_field TEXT NOT NULL,
            target_header TEXT DEFAULT '',
            source_unit TEXT DEFAULT '',
            target_unit TEXT DEFAULT '',
            chemical_form TEXT DEFAULT '',
            context_text TEXT DEFAULT '',
            conversion_formula TEXT DEFAULT '',
            evidence TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            knowledge_concept_id TEXT DEFAULT '',
            knowledge_release_id TEXT DEFAULT '',
            scope TEXT NOT NULL DEFAULT 'organization',
            confidence DOUBLE PRECISION DEFAULT 0.95,
            status TEXT NOT NULL DEFAULT 'draft',
            conflict_status TEXT DEFAULT 'clear',
            validation_json TEXT DEFAULT '{}',
            supersedes_rule_id TEXT DEFAULT '' REFERENCES learned_extraction_rules(rule_id),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            submitted_at TEXT,
            reviewed_at TEXT
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS mapping_rule_reviews (
            review_id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL REFERENCES mapping_rule_submissions(submission_id),
            decision TEXT NOT NULL,
            comment TEXT DEFAULT '',
            snapshot_json TEXT DEFAULT '{}',
            reviewed_by TEXT NOT NULL,
            reviewed_at TEXT NOT NULL
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_mapping_rule_import_project ON mapping_rule_imports(project_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mapping_rule_submission_project ON mapping_rule_submissions(project_id, status, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mapping_rule_submission_org ON mapping_rule_submissions(organization_id, status, submitted_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mapping_rule_review_submission ON mapping_rule_reviews(submission_id, reviewed_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_learned_rules_org ON learned_extraction_rules(organization_id, scope, review_status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_learned_rules_org")
    op.execute("DROP INDEX IF EXISTS idx_mapping_rule_review_submission")
    op.execute("DROP INDEX IF EXISTS idx_mapping_rule_submission_org")
    op.execute("DROP INDEX IF EXISTS idx_mapping_rule_submission_project")
    op.execute("DROP INDEX IF EXISTS idx_mapping_rule_import_project")
    op.execute("DROP TABLE IF EXISTS mapping_rule_reviews CASCADE")
    op.execute("DROP TABLE IF EXISTS mapping_rule_submissions CASCADE")
    op.execute("DROP TABLE IF EXISTS mapping_rule_imports CASCADE")
    for column in (
        "published_at",
        "published_by",
        "supersedes_rule_id",
        "revision",
        "source_submission_id",
        "organization_id",
    ):
        op.execute(f"ALTER TABLE learned_extraction_rules DROP COLUMN IF EXISTS {column}")
