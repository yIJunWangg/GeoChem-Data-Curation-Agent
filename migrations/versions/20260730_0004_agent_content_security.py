"""Add Agent content-security and actionable-message audit fields.

Revision ID: 20260730_0004
Revises: 20260722_0003
"""

from alembic import op


revision = "20260730_0004"
down_revision = "20260722_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("The server migration target must be PostgreSQL")

    op.execute(
        "ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS "
        "latest_actionable_message_id TEXT DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS action_state TEXT DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS "
        "superseded_by_message_id TEXT DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS "
        "trust_source TEXT DEFAULT 'trusted_user'"
    )
    op.execute(
        "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS "
        "policy_decision TEXT DEFAULT 'allowed'"
    )
    op.execute(
        "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS "
        "confirmation_status TEXT DEFAULT 'not_required'"
    )
    op.execute(
        "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS rejection_reason TEXT DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS duration_ms INTEGER DEFAULT 0"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS content_security_assessments (
            assessment_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            source_type TEXT DEFAULT '',
            source_id TEXT DEFAULT '',
            content_hash TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            risk_level TEXT DEFAULT 'low',
            reason_codes_json TEXT DEFAULT '[]',
            assessed_at TEXT NOT NULL,
            UNIQUE(project_id, content_hash, trust_level)
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_security_project "
        "ON content_security_assessments(project_id, risk_level)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_content_security_project")
    op.execute("DROP TABLE IF EXISTS content_security_assessments CASCADE")
    op.execute("ALTER TABLE agent_tool_calls DROP COLUMN IF EXISTS duration_ms")
    op.execute("ALTER TABLE agent_tool_calls DROP COLUMN IF EXISTS rejection_reason")
    op.execute("ALTER TABLE agent_tool_calls DROP COLUMN IF EXISTS confirmation_status")
    op.execute("ALTER TABLE agent_tool_calls DROP COLUMN IF EXISTS policy_decision")
    op.execute("ALTER TABLE agent_tool_calls DROP COLUMN IF EXISTS trust_source")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS superseded_by_message_id")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS action_state")
    op.execute("ALTER TABLE chat_threads DROP COLUMN IF EXISTS latest_actionable_message_id")
