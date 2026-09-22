from __future__ import annotations

import sqlite3

from geochem.core.postgres_migration import schema_table_order, sqlite_tables


def test_schema_table_order_contains_fts_after_documents():
    tables = schema_table_order()

    assert tables[0] == "organizations"
    assert tables.index("organizations") < tables.index("projects")
    assert tables.index("organizations") < tables.index("organization_members")
    assert "workflow_tasks" in tables
    assert "agent_run_events" in tables
    assert tables.index("retrieval_fts") == tables.index("retrieval_documents") + 1
    assert len(tables) == len(set(tables))


def test_sqlite_tables_omits_internal_fts_shadow_tables():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE projects (project_id TEXT)")
        connection.execute("CREATE VIRTUAL TABLE retrieval_fts USING fts5(document_id, content)")

        tables = sqlite_tables(connection)

        assert "projects" in tables
        assert "retrieval_fts" in tables
        assert not any(name.startswith("retrieval_fts_") for name in tables)
    finally:
        connection.close()
