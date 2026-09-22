"""Add versioned geochemical mapping knowledge.

Revision ID: 20260803_0005
Revises: 20260730_0004
"""

from alembic import op


revision = "20260803_0005"
down_revision = "20260730_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("The server migration target must be PostgreSQL")

    op.execute(
        """CREATE TABLE IF NOT EXISTS mapping_knowledge_releases (
            release_id TEXT PRIMARY KEY,
            version TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'staged',
            source_manifest_json TEXT DEFAULT '[]',
            created_by TEXT DEFAULT 'system',
            created_at TEXT NOT NULL,
            published_at TEXT
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS mapping_knowledge_concepts (
            concept_version_id TEXT PRIMARY KEY,
            concept_id TEXT NOT NULL,
            release_id TEXT NOT NULL REFERENCES mapping_knowledge_releases(release_id),
            canonical_name TEXT NOT NULL,
            concept_type TEXT NOT NULL,
            chemical_form TEXT DEFAULT '',
            unit_dimension TEXT DEFAULT '',
            allowed_units_json TEXT DEFAULT '[]',
            context_json TEXT DEFAULT '[]',
            forbidden_forms_json TEXT DEFAULT '[]',
            source_references_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(release_id, concept_id)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS mapping_knowledge_terms (
            term_id TEXT PRIMARY KEY,
            concept_version_id TEXT NOT NULL REFERENCES mapping_knowledge_concepts(concept_version_id),
            display_term TEXT NOT NULL,
            normalized_term TEXT NOT NULL,
            term_type TEXT DEFAULT 'alias',
            language TEXT DEFAULT 'en',
            score DOUBLE PRECISION DEFAULT 0.95,
            created_at TEXT NOT NULL,
            UNIQUE(concept_version_id, normalized_term)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS mapping_knowledge_imports (
            import_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_url TEXT DEFAULT '',
            source_version TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'staged',
            staged_release_id TEXT DEFAULT '',
            diff_json TEXT DEFAULT '{}',
            error_message TEXT DEFAULT '',
            created_by TEXT DEFAULT 'system',
            created_at TEXT NOT NULL,
            published_at TEXT
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_mapping_knowledge_release_status ON mapping_knowledge_releases(status, published_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mapping_knowledge_concept_release ON mapping_knowledge_concepts(release_id, concept_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mapping_knowledge_term_normalized ON mapping_knowledge_terms(normalized_term)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mapping_knowledge_import_status ON mapping_knowledge_imports(status, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mapping_knowledge_import_status")
    op.execute("DROP INDEX IF EXISTS idx_mapping_knowledge_term_normalized")
    op.execute("DROP INDEX IF EXISTS idx_mapping_knowledge_concept_release")
    op.execute("DROP INDEX IF EXISTS idx_mapping_knowledge_release_status")
    op.execute("DROP TABLE IF EXISTS mapping_knowledge_imports CASCADE")
    op.execute("DROP TABLE IF EXISTS mapping_knowledge_terms CASCADE")
    op.execute("DROP TABLE IF EXISTS mapping_knowledge_concepts CASCADE")
    op.execute("DROP TABLE IF EXISTS mapping_knowledge_releases CASCADE")
