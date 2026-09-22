"""Add organizations, workspace memberships, and conversation ownership.

Revision ID: 20260722_0003
Revises: 20260720_0002
"""

from alembic import op


revision = "20260722_0003"
down_revision = "20260720_0002"
branch_labels = None
depends_on = None


DEFAULT_ORGANIZATION_ID = "ORG_DEFAULT"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("The server migration target must be PostgreSQL")

    op.execute(
        """CREATE TABLE IF NOT EXISTS organizations (
            organization_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'active',
            created_by TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS organization_members (
            membership_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            status TEXT NOT NULL DEFAULT 'active',
            invited_by TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(organization_id, user_id)
        )"""
    )
    op.execute(
        """INSERT INTO organizations
           (organization_id, name, slug, status, created_by, created_at, updated_at)
           VALUES ('ORG_DEFAULT', 'GeoChem 工作室', 'geochem-default', 'active',
                   'migration', CURRENT_TIMESTAMP::text, CURRENT_TIMESTAMP::text)
           ON CONFLICT (organization_id) DO NOTHING"""
    )
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS organization_id "
        "TEXT NOT NULL DEFAULT 'ORG_DEFAULT'"
    )
    op.execute(
        "ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS created_by_user_id TEXT DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS created_by_user_id TEXT DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE workflow_tasks ADD COLUMN IF NOT EXISTS created_by_user_id TEXT DEFAULT ''"
    )
    op.execute(
        "UPDATE projects SET organization_id = 'ORG_DEFAULT' "
        "WHERE organization_id IS NULL OR organization_id = ''"
    )
    op.execute(
        "UPDATE chat_threads SET created_by_user_id = 'legacy-admin' "
        "WHERE created_by_user_id IS NULL OR created_by_user_id = ''"
    )
    op.execute(
        "UPDATE agent_runs SET created_by_user_id = 'legacy-admin' "
        "WHERE created_by_user_id IS NULL OR created_by_user_id = ''"
    )
    op.execute(
        "UPDATE workflow_tasks SET created_by_user_id = 'legacy-admin' "
        "WHERE created_by_user_id IS NULL OR created_by_user_id = ''"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_organization ON projects(organization_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_organization_members_user "
        "ON organization_members(user_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_organization_members_org "
        "ON organization_members(organization_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_threads_owner "
        "ON chat_threads(project_id, created_by_user_id, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_owner "
        "ON agent_runs(project_id, created_by_user_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_tasks_owner "
        "ON workflow_tasks(project_id, created_by_user_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_workflow_tasks_owner")
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_owner")
    op.execute("DROP INDEX IF EXISTS idx_chat_threads_owner")
    op.execute("DROP INDEX IF EXISTS idx_organization_members_org")
    op.execute("DROP INDEX IF EXISTS idx_organization_members_user")
    op.execute("DROP INDEX IF EXISTS idx_projects_organization")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS created_by_user_id")
    op.execute("ALTER TABLE workflow_tasks DROP COLUMN IF EXISTS created_by_user_id")
    op.execute("ALTER TABLE chat_threads DROP COLUMN IF EXISTS created_by_user_id")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS organization_id")
    op.execute("DROP TABLE IF EXISTS organization_members CASCADE")
    op.execute("DROP TABLE IF EXISTS organizations CASCADE")
