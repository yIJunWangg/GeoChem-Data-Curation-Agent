"""Tests for database operations."""

import json
from datetime import datetime

import pytest

from geochem.core.database import Database, _POSTGRES_INITIALIZED
from geochem.core.database_backend import postgres_schema_statements, qmark_to_pyformat, translate_postgres_sql

NOW = datetime.now().isoformat()


def test_postgres_parameter_translation_preserves_quoted_question_marks():
    sql = "SELECT '?' AS literal, title FROM articles WHERE article_id = ?"
    assert qmark_to_pyformat(sql) == "SELECT '?' AS literal, title FROM articles WHERE article_id = %s"


def test_postgres_insert_or_ignore_translation():
    translated = translate_postgres_sql("INSERT OR IGNORE INTO projects (project_id) VALUES (?)")
    assert translated == "INSERT INTO projects (project_id) VALUES (%s) ON CONFLICT DO NOTHING"


def test_postgres_schema_replaces_sqlite_specific_features():
    statements = postgres_schema_statements(
        """CREATE TABLE IF NOT EXISTS parent (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS child (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id TEXT,
            FOREIGN KEY (parent_id) REFERENCES parent(id)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(
            document_id UNINDEXED, content, project_id UNINDEXED
        );"""
    )
    joined = "\n".join(statements)
    assert "AUTOINCREMENT" not in joined
    assert "BIGSERIAL PRIMARY KEY" in joined
    assert "CREATE VIRTUAL TABLE" not in joined
    assert "to_tsvector('simple', content)" in joined
    assert "ALTER TABLE child ADD CONSTRAINT" in joined


class _FakePostgres:
    def __init__(self, *, migrated=True):
        self.migrated = migrated
        self.executed = []

    def fetch_one(self, sql, params=()):
        if "alembic_version" in sql:
            return {"version_num": "20260718_0001"} if self.migrated else None
        if "to_regclass" in sql:
            return {"table_name": "projects"} if self.migrated else {"table_name": None}
        raise AssertionError(sql)

    def execute(self, sql, params=()):
        self.executed.append((sql, params))


def _postgres_database(url, fake):
    database = Database.__new__(Database)
    database.db_path = None
    database.database_url = url
    database.dialect = "postgresql"
    database._conn = None
    database._postgres = fake
    return database


def test_postgres_alembic_mode_verifies_without_runtime_ddl(monkeypatch):
    url = "postgresql+psycopg://test/schema-verification"
    _POSTGRES_INITIALIZED.discard(url)
    monkeypatch.setenv("GEOCHEM_PROFILE", "production")
    monkeypatch.setenv("GEOCHEM_POSTGRES_SCHEMA_MODE", "alembic")
    fake = _FakePostgres(migrated=True)

    _postgres_database(url, fake).initialize()

    assert fake.executed == []
    assert url in _POSTGRES_INITIALIZED


def test_postgres_alembic_mode_rejects_unmigrated_database(monkeypatch):
    url = "postgresql+psycopg://test/missing-migration"
    _POSTGRES_INITIALIZED.discard(url)
    monkeypatch.setenv("GEOCHEM_PROFILE", "production")
    monkeypatch.setenv("GEOCHEM_POSTGRES_SCHEMA_MODE", "alembic")

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        _postgres_database(url, _FakePostgres(migrated=False)).initialize()


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    yield db
    db.close()


def _insert_project(db, project_id="PRJ_001"):
    db.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (project_id, "Test Project", NOW, NOW),
    )
    db.commit()


def _insert_article(db, article_id="ART_001", project_id="PRJ_001"):
    db.execute(
        "INSERT INTO articles (article_id, project_id, title, authors, year, doi, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (article_id, project_id, "Test Paper", '["Author A"]', 2020, "10.1234/test", "imported", NOW),
    )
    db.commit()


def test_initialize_creates_tables(db):
    """All 17 tables should exist after initialization."""
    tables = db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    table_names = {t["name"] for t in tables}
    expected = {
        "projects", "articles", "resources", "sections",
        "table_assets", "candidate_tables", "candidate_columns", "candidate_rows",
        "field_mappings", "mapping_rules", "review_items", "review_decisions",
        "calculation_records", "standardized_records", "export_jobs",
        "llm_calls", "processing_events", "teaching_events",
        "learned_extraction_rules", "record_patches",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


def test_insert_and_fetch_article(db):
    _insert_project(db)
    _insert_article(db)

    row = db.fetch_one("SELECT * FROM articles WHERE article_id = ?", ("ART_001",))
    assert row is not None
    assert row["title"] == "Test Paper"
    assert row["year"] == 2020
    assert row["doi"] == "10.1234/test"
    assert json.loads(row["authors"]) == ["Author A"]


def test_insert_resource(db):
    _insert_project(db)
    _insert_article(db, "ART_002")
    db.execute(
        "INSERT INTO resources (resource_id, article_id, resource_type, file_name, local_path, file_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("RES_001", "ART_002", "supplementary_excel", "Table_S1.xlsx", "/path/to/file", "abc123", "parsed", NOW),
    )
    db.commit()

    row = db.fetch_one("SELECT * FROM resources WHERE resource_id = ?", ("RES_001",))
    assert row["file_name"] == "Table_S1.xlsx"
    assert row["resource_type"] == "supplementary_excel"


def test_insert_llm_call(db):
    db.execute(
        """INSERT INTO llm_calls
        (call_id, project_id, article_id, agent_name, skill_name,
         model_provider, model_name, input_tokens, output_tokens, total_tokens,
         estimated_cost, started_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("LLM_000001", "PRJ_001", "ART_001", "SchemaMapper", "field_mapping",
         "anthropic", "claude-sonnet-4-20250514", 1200, 450, 1650,
         0.005, NOW, "success"),
    )
    db.commit()

    row = db.fetch_one("SELECT * FROM llm_calls WHERE call_id = ?", ("LLM_000001",))
    assert row["model_provider"] == "anthropic"
    assert row["input_tokens"] == 1200
    assert row["total_tokens"] == 1650


def test_insert_mapping_rule(db):
    db.execute(
        """INSERT INTO mapping_rules
        (rule_id, source_field, target_field, source_unit, target_unit,
         mapping_type, formula, conversion_factor, review_status, scope, version, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("RULE_001", "Na2O", "Na", "wt%", "wt%",
         "oxide_to_element", "Na = Na2O * 0.741857", 0.741857,
         "confirmed", "project", 1, NOW, "user"),
    )
    db.commit()

    row = db.fetch_one("SELECT * FROM mapping_rules WHERE rule_id = ?", ("RULE_001",))
    assert row["source_field"] == "Na2O"
    assert row["target_field"] == "Na"
    assert row["conversion_factor"] == pytest.approx(0.741857)


def test_insert_review_item(db):
    _insert_project(db)
    _insert_article(db)
    db.execute(
        """INSERT INTO review_items
        (review_id, article_id, item_type, risk_level, original_field,
         original_unit, ai_suggestion, confidence, available_actions, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("REV_001", "ART_001", "field_mapping", "high", "Na2O",
         "wt%", "Convert Na2O to elemental Na", 0.78,
         '["accept", "change_target", "skip"]', "pending", NOW),
    )
    db.commit()

    row = db.fetch_one("SELECT * FROM review_items WHERE review_id = ?", ("REV_001",))
    assert row["risk_level"] == "high"
    assert row["status"] == "pending"
    assert json.loads(row["available_actions"]) == ["accept", "change_target", "skip"]


def test_insert_calculation_record(db):
    _insert_project(db)
    _insert_article(db)
    db.execute(
        """INSERT INTO calculation_records
        (calc_id, article_id, table_id, row_id, target_field, source_field,
         source_value, source_unit, target_unit, formula, result, review_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("CALC_001", "ART_001", "TBL_001", "ROW_001", "Na", "Na2O",
         2.87, "wt%", "wt%", "Na = Na2O * 0.741857", 2.129, "confirmed", NOW),
    )
    db.commit()

    row = db.fetch_one("SELECT * FROM calculation_records WHERE calc_id = ?", ("CALC_001",))
    assert row["source_value"] == pytest.approx(2.87)
    assert row["result"] == pytest.approx(2.129)
    assert row["formula"] == "Na = Na2O * 0.741857"


def test_fetch_all_returns_multiple_rows(db):
    _insert_project(db)
    for i in range(5):
        db.execute(
            "INSERT INTO articles (article_id, project_id, title, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"ART_{i:03d}", "PRJ_001", f"Paper {i}", "imported", NOW),
        )
    db.commit()

    rows = db.fetch_all("SELECT * FROM articles WHERE project_id = ?", ("PRJ_001",))
    assert len(rows) == 5


def test_context_manager(tmp_path):
    db_path = tmp_path / "ctx.db"
    with Database(db_path) as db:
        db.initialize()
        db.execute("INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                   ("P1", "Test", NOW, NOW))
        db.commit()
        row = db.fetch_one("SELECT * FROM projects WHERE project_id = ?", ("P1",))
        assert row is not None
