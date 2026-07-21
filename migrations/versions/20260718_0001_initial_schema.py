"""Create the shared GeoChem schema.

Revision ID: 20260718_0001
Revises:
"""

from alembic import op

from geochem.core.database import SCHEMA_SQL
from geochem.core.database_backend import postgres_schema_statements


revision = "20260718_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("The server migration target must be PostgreSQL")
    for statement in postgres_schema_statements(SCHEMA_SQL):
        op.execute(statement)
    op.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_tasks_active_key
           ON workflow_tasks(task_key)
           WHERE task_key <> '' AND status IN ('pending', 'running')"""
    )


def downgrade() -> None:
    table_rows = op.get_bind().exec_driver_sql(
        """SELECT tablename FROM pg_tables
           WHERE schemaname = current_schema()
             AND tablename NOT IN ('alembic_version')"""
    ).fetchall()
    for (table_name,) in reversed(table_rows):
        op.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
