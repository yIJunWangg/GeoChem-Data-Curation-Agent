"""SQLite database schema and access layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    description  TEXT DEFAULT '',
    research_field TEXT DEFAULT '',
    schema_file  TEXT DEFAULT '',
    mapping_memory TEXT DEFAULT '',
    output_database TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    article_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title      TEXT DEFAULT '',
    authors    TEXT DEFAULT '[]',
    year       INTEGER,
    doi        TEXT,
    url        TEXT,
    journal    TEXT DEFAULT '',
    article_dir TEXT DEFAULT '',
    status     TEXT DEFAULT 'imported',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS article_evidence (
    evidence_id   TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    article_id    TEXT NOT NULL,
    resource_id   TEXT,
    target_header TEXT DEFAULT '',
    target_field  TEXT DEFAULT '',
    evidence_type TEXT DEFAULT 'paragraph',
    evidence_text TEXT DEFAULT '',
    page_or_section TEXT DEFAULT '',
    asset_id      TEXT,
    confidence    REAL DEFAULT 0.0,
    status        TEXT DEFAULT 'candidate',
    local_path    TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id),
    FOREIGN KEY (resource_id) REFERENCES resources(resource_id),
    FOREIGN KEY (asset_id) REFERENCES table_assets(asset_id)
);

CREATE TABLE IF NOT EXISTS resources (
    resource_id   TEXT PRIMARY KEY,
    article_id    TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    file_name     TEXT NOT NULL,
    local_path    TEXT,
    source_url    TEXT,
    file_hash     TEXT,
    file_size     INTEGER,
    relevance_score REAL,
    status        TEXT DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS header_configs (
    config_id    TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    source_file  TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    headers_json TEXT DEFAULT '[]',
    status       TEXT DEFAULT 'active',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS article_header_assignments (
    assignment_id TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    article_id    TEXT NOT NULL,
    config_id     TEXT NOT NULL,
    suggested_by  TEXT DEFAULT 'user',
    confidence    REAL DEFAULT 1.0,
    status        TEXT DEFAULT 'confirmed',
    reason        TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id),
    FOREIGN KEY (config_id) REFERENCES header_configs(config_id)
);

CREATE TABLE IF NOT EXISTS sections (
    section_id   TEXT PRIMARY KEY,
    article_id   TEXT NOT NULL,
    section_type TEXT NOT NULL,
    title        TEXT DEFAULT '',
    page_start   INTEGER,
    page_end     INTEGER,
    content_preview TEXT DEFAULT '',
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS table_assets (
    asset_id     TEXT PRIMARY KEY,
    resource_id  TEXT NOT NULL,
    article_id   TEXT NOT NULL,
    table_number TEXT,
    title        TEXT DEFAULT '',
    caption      TEXT DEFAULT '',
    page_or_sheet TEXT,
    raw_file_path TEXT,
    extract_method TEXT DEFAULT '',
    confidence   REAL DEFAULT 0.0,
    status       TEXT DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    FOREIGN KEY (resource_id) REFERENCES resources(resource_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS candidate_tables (
    table_id      TEXT PRIMARY KEY,
    article_id    TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    asset_id      TEXT,
    source_type   TEXT DEFAULT '',
    sheet_name    TEXT,
    page_number   INTEGER,
    title         TEXT DEFAULT '',
    caption       TEXT DEFAULT '',
    row_count     INTEGER DEFAULT 0,
    col_count     INTEGER DEFAULT 0,
    extract_method TEXT DEFAULT '',
    confidence    REAL DEFAULT 0.0,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS candidate_columns (
    column_id       TEXT PRIMARY KEY,
    table_id        TEXT NOT NULL,
    raw_name        TEXT NOT NULL,
    normalized_name TEXT DEFAULT '',
    unit_candidate  TEXT,
    dtype           TEXT DEFAULT 'unknown',
    sample_values   TEXT DEFAULT '[]',
    FOREIGN KEY (table_id) REFERENCES candidate_tables(table_id)
);

CREATE TABLE IF NOT EXISTS candidate_rows (
    row_id     TEXT PRIMARY KEY,
    table_id   TEXT NOT NULL,
    row_index  INTEGER NOT NULL,
    raw_data   TEXT DEFAULT '{}',
    FOREIGN KEY (table_id) REFERENCES candidate_tables(table_id)
);

CREATE TABLE IF NOT EXISTS field_mappings (
    mapping_id   TEXT PRIMARY KEY,
    article_id   TEXT NOT NULL,
    table_id     TEXT NOT NULL,
    column_id    TEXT,
    source_field TEXT NOT NULL,
    target_field TEXT NOT NULL,
    source_unit  TEXT,
    target_unit  TEXT,
    mapping_type TEXT DEFAULT 'uncertain',
    confidence   REAL DEFAULT 0.0,
    risk_level   TEXT DEFAULT 'medium',
    requires_review INTEGER DEFAULT 1,
    reason       TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS mapping_rules (
    rule_id          TEXT PRIMARY KEY,
    source_field     TEXT NOT NULL,
    target_field     TEXT NOT NULL,
    source_unit      TEXT,
    target_unit      TEXT,
    mapping_type     TEXT DEFAULT 'exact',
    formula          TEXT,
    conversion_factor REAL,
    review_status    TEXT DEFAULT 'confirmed',
    scope            TEXT DEFAULT 'project',
    version          INTEGER DEFAULT 1,
    created_at       TEXT NOT NULL,
    created_by       TEXT DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS review_items (
    review_id      TEXT PRIMARY KEY,
    article_id     TEXT NOT NULL,
    table_id       TEXT,
    row_id         TEXT,
    item_type      TEXT DEFAULT 'field_mapping',
    risk_level     TEXT DEFAULT 'medium',
    original_field TEXT DEFAULT '',
    original_unit  TEXT,
    original_value TEXT,
    ai_suggestion  TEXT DEFAULT '',
    confidence     REAL DEFAULT 0.0,
    available_actions TEXT DEFAULT '[]',
    status         TEXT DEFAULT 'pending',
    created_at     TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id    TEXT PRIMARY KEY,
    review_id      TEXT NOT NULL,
    action         TEXT DEFAULT '',
    target_field   TEXT,
    target_unit    TEXT,
    formula        TEXT,
    save_as_rule   INTEGER DEFAULT 0,
    rule_scope     TEXT DEFAULT 'project',
    notes          TEXT DEFAULT '',
    decided_at     TEXT NOT NULL,
    FOREIGN KEY (review_id) REFERENCES review_items(review_id)
);

CREATE TABLE IF NOT EXISTS calculation_records (
    calc_id            TEXT PRIMARY KEY,
    article_id         TEXT NOT NULL,
    table_id           TEXT NOT NULL,
    row_id             TEXT NOT NULL,
    target_field       TEXT NOT NULL,
    source_field       TEXT NOT NULL,
    source_value       REAL NOT NULL,
    source_unit        TEXT NOT NULL,
    target_unit        TEXT NOT NULL,
    formula            TEXT NOT NULL,
    formula_source     TEXT DEFAULT '',
    substitution       TEXT DEFAULT '',
    result             REAL NOT NULL,
    significant_figures INTEGER,
    rule_id            TEXT,
    review_status      TEXT DEFAULT 'confirmed',
    archive_file       TEXT,
    created_at         TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS standardized_records (
    record_id        TEXT PRIMARY KEY,
    article_id       TEXT NOT NULL,
    table_id         TEXT NOT NULL,
    row_id           TEXT NOT NULL,
    data             TEXT DEFAULT '{}',
    reference        TEXT DEFAULT '',
    doi              TEXT,
    source_file      TEXT DEFAULT '',
    source_table     TEXT DEFAULT '',
    source_row       INTEGER,
    original_fields  TEXT DEFAULT '{}',
    original_units   TEXT DEFAULT '{}',
    original_values  TEXT DEFAULT '{}',
    mapped_fields    TEXT DEFAULT '{}',
    mapped_units     TEXT DEFAULT '{}',
    mapping_rule_ids TEXT DEFAULT '{}',
    calculation_ids  TEXT DEFAULT '{}',
    review_statuses  TEXT DEFAULT '{}',
    confidence_scores TEXT DEFAULT '{}',
    quality_grade    TEXT DEFAULT 'D',
    processed_at     TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS export_jobs (
    job_id        TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    article_id     TEXT,
    table_id       TEXT,
    export_format TEXT NOT NULL,
    output_path   TEXT NOT NULL,
    package_path   TEXT,
    record_count  INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS teaching_events (
    event_id      TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    article_id    TEXT NOT NULL,
    table_id      TEXT,
    row_id        TEXT,
    sample_id     TEXT,
    target_field  TEXT NOT NULL,
    target_header TEXT DEFAULT '',
    target_unit   TEXT,
    value         TEXT,
    source_type   TEXT DEFAULT '',
    evidence      TEXT DEFAULT '',
    notes         TEXT DEFAULT '',
    status        TEXT DEFAULT 'confirmed',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS learned_extraction_rules (
    rule_id       TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    article_id    TEXT,
    target_field  TEXT NOT NULL,
    target_header TEXT DEFAULT '',
    target_unit   TEXT,
    rule_type     TEXT DEFAULT 'value_extraction_pattern',
    source_type   TEXT DEFAULT '',
    pattern       TEXT DEFAULT '',
    evidence      TEXT DEFAULT '',
    conditions    TEXT DEFAULT '{}',
    confidence    REAL DEFAULT 0.0,
    risk_level    TEXT DEFAULT 'medium',
    review_status TEXT DEFAULT 'confirmed',
    scope         TEXT DEFAULT 'project',
    created_at    TEXT NOT NULL,
    created_by    TEXT DEFAULT 'agent',
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS record_patches (
    patch_id      TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    article_id    TEXT NOT NULL,
    table_id      TEXT,
    row_id        TEXT,
    sample_id     TEXT,
    target_field  TEXT NOT NULL,
    target_header TEXT DEFAULT '',
    target_unit   TEXT,
    value         TEXT NOT NULL,
    source_unit   TEXT,
    source_type   TEXT DEFAULT '',
    evidence_id   TEXT,
    learned_rule_id TEXT,
    confidence    REAL DEFAULT 1.0,
    risk_level    TEXT DEFAULT 'low',
    review_status TEXT DEFAULT 'confirmed',
    reason        TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id),
    FOREIGN KEY (evidence_id) REFERENCES teaching_events(event_id),
    FOREIGN KEY (learned_rule_id) REFERENCES learned_extraction_rules(rule_id)
);

CREATE TABLE IF NOT EXISTS extraction_candidates (
    candidate_id  TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    article_id    TEXT NOT NULL,
    evidence_id   TEXT NOT NULL,
    source_type   TEXT DEFAULT '',
    target_header TEXT DEFAULT '',
    target_field  TEXT DEFAULT '',
    target_unit   TEXT DEFAULT '',
    value         TEXT DEFAULT '',
    source_unit   TEXT DEFAULT '',
    confidence    REAL DEFAULT 0.0,
    risk_level    TEXT DEFAULT 'medium',
    status        TEXT DEFAULT 'pending',
    reason        TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id),
    FOREIGN KEY (evidence_id) REFERENCES article_evidence(evidence_id)
);

CREATE TABLE IF NOT EXISTS document_elements (
    element_id       TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    article_id       TEXT NOT NULL,
    resource_id      TEXT NOT NULL,
    legacy_source_id TEXT DEFAULT '',
    element_type     TEXT NOT NULL,
    page_number      INTEGER,
    bbox_json        TEXT DEFAULT '[]',
    text_content     TEXT DEFAULT '',
    context_text     TEXT DEFAULT '',
    caption          TEXT DEFAULT '',
    preview_path     TEXT DEFAULT '',
    raw_table_json   TEXT DEFAULT '{}',
    matched_headers_json TEXT DEFAULT '[]',
    relevance_score REAL DEFAULT 0.0,
    content_hash     TEXT DEFAULT '',
    parser_version   TEXT DEFAULT 'layout-v1',
    status           TEXT DEFAULT 'candidate',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id),
    FOREIGN KEY (resource_id) REFERENCES resources(resource_id)
);

CREATE TABLE IF NOT EXISTS workbench_sessions (
    session_id       TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    article_id       TEXT NOT NULL,
    header_config_id TEXT,
    current_step     TEXT DEFAULT 'discovery',
    discovery_status TEXT DEFAULT 'pending',
    discovery_hash   TEXT DEFAULT '',
    active_batch_id  TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(project_id, article_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id),
    FOREIGN KEY (header_config_id) REFERENCES header_configs(config_id)
);

CREATE TABLE IF NOT EXISTS element_selections (
    selection_id TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    element_id   TEXT NOT NULL,
    selected_by  TEXT DEFAULT 'user',
    status       TEXT DEFAULT 'selected',
    created_at   TEXT NOT NULL,
    UNIQUE(session_id, element_id),
    FOREIGN KEY (session_id) REFERENCES workbench_sessions(session_id),
    FOREIGN KEY (element_id) REFERENCES document_elements(element_id)
);

CREATE TABLE IF NOT EXISTS extraction_batches (
    batch_id         TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    article_id       TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    header_config_id TEXT,
    status           TEXT DEFAULT 'pending',
    record_count     INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id),
    FOREIGN KEY (session_id) REFERENCES workbench_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS candidate_records (
    candidate_record_id TEXT PRIMARY KEY,
    batch_id         TEXT NOT NULL,
    article_id       TEXT NOT NULL,
    sample_key       TEXT NOT NULL,
    sample_id        TEXT DEFAULT '',
    row_index        INTEGER NOT NULL,
    merge_status     TEXT DEFAULT 'unmatched',
    quality_grade    TEXT DEFAULT 'D',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(batch_id, sample_key),
    FOREIGN KEY (batch_id) REFERENCES extraction_batches(batch_id),
    FOREIGN KEY (article_id) REFERENCES articles(article_id)
);

CREATE TABLE IF NOT EXISTS candidate_cells (
    cell_id          TEXT PRIMARY KEY,
    candidate_record_id TEXT NOT NULL,
    header_id        TEXT DEFAULT '',
    target_header    TEXT NOT NULL,
    target_field     TEXT DEFAULT '',
    target_unit      TEXT DEFAULT '',
    value            TEXT DEFAULT '',
    original_value   TEXT DEFAULT '',
    original_field   TEXT DEFAULT '',
    original_unit    TEXT DEFAULT '',
    confidence       REAL DEFAULT 0.0,
    risk_level       TEXT DEFAULT 'medium',
    mapping_status   TEXT DEFAULT 'pending',
    mapping_id       TEXT,
    element_id       TEXT,
    page_number      INTEGER,
    bbox_json        TEXT DEFAULT '[]',
    alternatives_json TEXT DEFAULT '[]',
    review_status    TEXT DEFAULT 'pending',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(candidate_record_id, target_header),
    FOREIGN KEY (candidate_record_id) REFERENCES candidate_records(candidate_record_id),
    FOREIGN KEY (element_id) REFERENCES document_elements(element_id)
);

CREATE TABLE IF NOT EXISTS workflow_tasks (
    task_id       TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    article_id    TEXT,
    task_type     TEXT NOT NULL,
    status        TEXT DEFAULT 'pending',
    progress      REAL DEFAULT 0.0,
    message       TEXT DEFAULT '',
    result_json   TEXT DEFAULT '{}',
    error_message TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS workflow_task_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    level      TEXT DEFAULT 'INFO',
    message    TEXT NOT NULL,
    progress   REAL DEFAULT 0.0,
    details_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES workflow_tasks(task_id)
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id          TEXT PRIMARY KEY,
    project_id       TEXT DEFAULT '',
    article_id       TEXT DEFAULT '',
    agent_name       TEXT DEFAULT '',
    skill_name       TEXT DEFAULT '',
    model_provider   TEXT DEFAULT '',
    model_name       TEXT DEFAULT '',
    prompt_version   TEXT DEFAULT '',
    prompt_hash      TEXT DEFAULT '',
    input_tokens     INTEGER DEFAULT 0,
    output_tokens    INTEGER DEFAULT 0,
    cached_tokens    INTEGER DEFAULT 0,
    total_tokens     INTEGER DEFAULT 0,
    estimated_cost   REAL DEFAULT 0.0,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    latency_ms       INTEGER,
    status           TEXT DEFAULT 'success',
    error_message    TEXT,
    retry_count      INTEGER DEFAULT 0,
    request_summary  TEXT DEFAULT '',
    response_summary TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS processing_events (
    event_id     TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    article_id   TEXT,
    event_type   TEXT DEFAULT '',
    agent_name   TEXT DEFAULT '',
    skill_name   TEXT DEFAULT '',
    message      TEXT DEFAULT '',
    details      TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE INDEX IF NOT EXISTS idx_articles_project ON articles(project_id);
CREATE INDEX IF NOT EXISTS idx_resources_article ON resources(article_id);
CREATE INDEX IF NOT EXISTS idx_article_evidence_article ON article_evidence(article_id);
CREATE INDEX IF NOT EXISTS idx_header_configs_project ON header_configs(project_id);
CREATE INDEX IF NOT EXISTS idx_article_header_assignments_article ON article_header_assignments(article_id);
CREATE INDEX IF NOT EXISTS idx_table_assets_article ON table_assets(article_id);
CREATE INDEX IF NOT EXISTS idx_candidate_tables_article ON candidate_tables(article_id);
CREATE INDEX IF NOT EXISTS idx_candidate_columns_table ON candidate_columns(table_id);
CREATE INDEX IF NOT EXISTS idx_candidate_rows_table ON candidate_rows(table_id);
CREATE INDEX IF NOT EXISTS idx_field_mappings_article ON field_mappings(article_id);
CREATE INDEX IF NOT EXISTS idx_review_items_article ON review_items(article_id);
CREATE INDEX IF NOT EXISTS idx_review_items_status ON review_items(status);
CREATE INDEX IF NOT EXISTS idx_calculation_records_article ON calculation_records(article_id);
CREATE INDEX IF NOT EXISTS idx_standardized_records_article ON standardized_records(article_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_project ON llm_calls(project_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_article ON llm_calls(article_id);
CREATE INDEX IF NOT EXISTS idx_processing_events_project ON processing_events(project_id);
CREATE INDEX IF NOT EXISTS idx_teaching_events_project ON teaching_events(project_id);
CREATE INDEX IF NOT EXISTS idx_learned_rules_project ON learned_extraction_rules(project_id);
CREATE INDEX IF NOT EXISTS idx_record_patches_table ON record_patches(table_id);
CREATE INDEX IF NOT EXISTS idx_extraction_candidates_evidence ON extraction_candidates(evidence_id);
CREATE INDEX IF NOT EXISTS idx_extraction_candidates_project ON extraction_candidates(project_id);
CREATE INDEX IF NOT EXISTS idx_document_elements_article ON document_elements(article_id);
CREATE INDEX IF NOT EXISTS idx_document_elements_resource ON document_elements(resource_id);
CREATE INDEX IF NOT EXISTS idx_element_selections_session ON element_selections(session_id);
CREATE INDEX IF NOT EXISTS idx_extraction_batches_article ON extraction_batches(article_id);
CREATE INDEX IF NOT EXISTS idx_candidate_records_batch ON candidate_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_candidate_cells_record ON candidate_cells(candidate_record_id);
CREATE INDEX IF NOT EXISTS idx_workflow_tasks_project ON workflow_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_workflow_task_events_task ON workflow_task_events(task_id);
"""


class Database:
    """SQLite database access layer."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def initialize(self) -> None:
        """Create all tables if they don't exist."""
        conn = self.connect()
        conn.executescript(SCHEMA_SQL)
        self._ensure_columns(conn)
        conn.commit()

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        """Backfill additive columns for databases created by earlier versions."""
        self._ensure_table_columns(conn, "export_jobs", {
            "article_id": "TEXT",
            "table_id": "TEXT",
            "package_path": "TEXT",
        })
        self._ensure_table_columns(conn, "articles", {
            "article_dir": "TEXT DEFAULT ''",
        })

    def _ensure_table_columns(self, conn: sqlite3.Connection, table: str, additions: dict[str, str]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, decl in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self.connect()
        return conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        conn = self.connect()
        return conn.executemany(sql, params_list)

    def commit(self) -> None:
        conn = self.connect()
        conn.commit()

    def fetch_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        cursor = self.execute(sql, params)
        return cursor.fetchone()

    def fetch_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        cursor = self.execute(sql, params)
        return cursor.fetchall()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
