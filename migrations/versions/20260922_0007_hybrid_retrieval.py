"""Add retrieval chunks and PostgreSQL chunk full-text index.

Revision ID: 20260922_0007
Revises: 20260804_0006
"""

from alembic import op


revision = "20260922_0007"
down_revision = "20260804_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("The server migration target must be PostgreSQL")

    op.execute(
        """CREATE TABLE IF NOT EXISTS retrieval_chunks (
            chunk_id TEXT PRIMARY KEY,
            parent_document_id TEXT NOT NULL REFERENCES retrieval_documents(document_id),
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            article_id TEXT DEFAULT '',
            resource_id TEXT DEFAULT '',
            element_id TEXT DEFAULT '',
            record_id TEXT DEFAULT '',
            cell_id TEXT DEFAULT '',
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            element_type TEXT NOT NULL,
            page_number INTEGER,
            page_spans_json TEXT DEFAULT '[]',
            section_path TEXT DEFAULT '',
            bbox_json TEXT DEFAULT '[]',
            reading_order INTEGER DEFAULT 0,
            metadata_json TEXT DEFAULT '{}',
            embedding_model TEXT DEFAULT '',
            embedding_version TEXT DEFAULT '',
            vector_point_id TEXT DEFAULT '',
            vector_status TEXT DEFAULT 'pending',
            vector_indexed_at TEXT,
            updated_at TEXT NOT NULL
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS retrieval_chunk_fts (
            chunk_id TEXT PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '',
            project_id TEXT NOT NULL DEFAULT '',
            article_id TEXT NOT NULL DEFAULT '',
            element_type TEXT NOT NULL DEFAULT ''
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_scope ON retrieval_chunks(project_id, article_id, element_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_parent ON retrieval_chunks(parent_document_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_vector ON retrieval_chunks(project_id, article_id, vector_status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_chunk_fts_scope ON retrieval_chunk_fts(project_id, article_id, element_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_chunk_fts_content ON retrieval_chunk_fts USING GIN (to_tsvector('simple', content))")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS retrieval_chunk_fts CASCADE")
    op.execute("DROP TABLE IF EXISTS retrieval_chunks CASCADE")
