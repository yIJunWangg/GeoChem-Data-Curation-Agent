"""SQLite database schema and access layer."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any

from .database_backend import PostgresConnection, postgres_schema_statements


_POSTGRES_INITIALIZED: set[str] = set()
_POSTGRES_INITIALIZE_LOCK = Lock()

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
    owner_user_id TEXT DEFAULT '',
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
    candidate_record_id TEXT,
    cell_id             TEXT,
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

CREATE TABLE IF NOT EXISTS standardized_cell_provenance (
    provenance_id      TEXT PRIMARY KEY,
    record_id          TEXT NOT NULL,
    target_header      TEXT NOT NULL,
    standardized_value TEXT DEFAULT '',
    target_unit        TEXT DEFAULT '',
    original_field     TEXT DEFAULT '',
    original_value     TEXT DEFAULT '',
    original_unit      TEXT DEFAULT '',
    resource_id        TEXT,
    resource_name      TEXT DEFAULT '',
    element_id         TEXT,
    element_type       TEXT DEFAULT '',
    page_number        INTEGER,
    bbox_json          TEXT DEFAULT '[]',
    source_caption     TEXT DEFAULT '',
    source_context     TEXT DEFAULT '',
    mapping_status     TEXT DEFAULT '',
    mapping_rule_id    TEXT,
    calculation_id     TEXT,
    calculation_formula TEXT DEFAULT '',
    calculation_substitution TEXT DEFAULT '',
    review_status      TEXT DEFAULT '',
    confidence         REAL DEFAULT 0.0,
    source_complete    INTEGER DEFAULT 0,
    created_at         TEXT NOT NULL,
    UNIQUE(record_id, target_header),
    FOREIGN KEY (record_id) REFERENCES standardized_records(record_id),
    FOREIGN KEY (resource_id) REFERENCES resources(resource_id),
    FOREIGN KEY (element_id) REFERENCES document_elements(element_id)
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
    enabled       INTEGER DEFAULT 1,
    element_id    TEXT DEFAULT '',
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
    page_spans_json  TEXT DEFAULT '[]',
    text_content     TEXT DEFAULT '',
    context_text     TEXT DEFAULT '',
    caption          TEXT DEFAULT '',
    preview_path     TEXT DEFAULT '',
    raw_table_json   TEXT DEFAULT '{}',
    matched_headers_json TEXT DEFAULT '[]',
    relevance_score REAL DEFAULT 0.0,
    score_reasons_json TEXT DEFAULT '[]',
    reading_order    INTEGER DEFAULT 0,
    section_path     TEXT DEFAULT '',
    source_backend   TEXT DEFAULT '',
    merge_reason     TEXT DEFAULT '',
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
    applied_rule_id  TEXT DEFAULT '',
    extraction_method TEXT DEFAULT '',
    source_label     TEXT DEFAULT '',
    source_quote     TEXT DEFAULT '',
    source_row_snapshot TEXT DEFAULT '{}',
    evidence_status  TEXT DEFAULT 'weak',
    evidence_reason  TEXT DEFAULT '',
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
    operation_name TEXT DEFAULT '',
    payload_json  TEXT DEFAULT '{}',
    task_key      TEXT DEFAULT '',
    broker_task_id TEXT DEFAULT '',
    retry_count   INTEGER DEFAULT 0,
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

-- Conversational agent audit records. LangGraph checkpoints intentionally live in a
-- separate SQLite file so long-running graph state does not contend with curation writes.
CREATE TABLE IF NOT EXISTS chat_threads (
    thread_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    article_id TEXT,
    title TEXT DEFAULT '',
    scope TEXT DEFAULT 'article',
    selection_context_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'completed',
    model_provider TEXT DEFAULT '',
    model_name TEXT DEFAULT '',
    agent_run_id TEXT DEFAULT '',
    ui_payload_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id)
);

CREATE TABLE IF NOT EXISTS chat_citations (
    citation_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    document_id TEXT DEFAULT '',
    article_id TEXT DEFAULT '',
    record_id TEXT DEFAULT '',
    cell_id TEXT DEFAULT '',
    element_id TEXT DEFAULT '',
    label TEXT DEFAULT '',
    payload_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES chat_messages(message_id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    article_id TEXT,
    thread_id TEXT,
    status TEXT DEFAULT 'pending',
    current_node TEXT DEFAULT '',
    pending_interrupt_json TEXT DEFAULT '{}',
    state_summary_json TEXT DEFAULT '{}',
    skill_version TEXT DEFAULT '',
    prompt_version TEXT DEFAULT '',
    rule_snapshot_json TEXT DEFAULT '{}',
    workflow_step TEXT DEFAULT '',
    checkpoint_kind TEXT DEFAULT '',
    workbench_handoff_status TEXT DEFAULT '',
    workbench_snapshot_json TEXT DEFAULT '{}',
    handoff_context_json TEXT DEFAULT '{}',
    workbench_diff_json TEXT DEFAULT '{}',
    data_version TEXT DEFAULT '',
    run_kind TEXT DEFAULT 'conversation',
    model_provider TEXT DEFAULT '',
    model_name TEXT DEFAULT '',
    selection_context_json TEXT DEFAULT '{}',
    error_message TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id)
);

-- Public acquisition candidates are intentionally separate from articles: an
-- article is only created after a user confirms the candidate.
CREATE TABLE IF NOT EXISTS article_source_candidates (
    source_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    input_value TEXT NOT NULL,
    doi TEXT DEFAULT '',
    title TEXT DEFAULT '',
    authors_json TEXT DEFAULT '[]',
    year INTEGER,
    journal TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    pdf_url TEXT DEFAULT '',
    source_kind TEXT DEFAULT '',
    access_status TEXT DEFAULT 'unavailable',
    validation_message TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    user_confirmed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS agent_interrupts (
    interrupt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    response_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
);

CREATE TABLE IF NOT EXISTS agent_run_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    level TEXT DEFAULT 'INFO',
    message TEXT NOT NULL,
    details_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    thread_id TEXT DEFAULT '',
    project_id TEXT NOT NULL,
    article_id TEXT DEFAULT '',
    tool_name TEXT NOT NULL,
    permission_level TEXT DEFAULT 'read',
    input_summary_json TEXT DEFAULT '{}',
    output_summary_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    error_message TEXT DEFAULT '',
    idempotency_key TEXT DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
);

CREATE TABLE IF NOT EXISTS literature_search_runs (
    search_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    thread_id TEXT DEFAULT '',
    query TEXT NOT NULL,
    sort_mode TEXT DEFAULT 'relevance',
    providers_json TEXT DEFAULT '[]',
    errors_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS literature_search_results (
    result_id TEXT PRIMARY KEY,
    search_id TEXT NOT NULL,
    title TEXT NOT NULL,
    doi TEXT DEFAULT '',
    authors_json TEXT DEFAULT '[]',
    year INTEGER,
    venue TEXT DEFAULT '',
    landing_url TEXT DEFAULT '',
    pdf_url TEXT DEFAULT '',
    open_access INTEGER DEFAULT 0,
    provider TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}',
    selected INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (search_id) REFERENCES literature_search_runs(search_id)
);

CREATE TABLE IF NOT EXISTS retrieval_documents (
    document_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    article_id TEXT DEFAULT '',
    document_type TEXT NOT NULL,
    resource_id TEXT DEFAULT '',
    element_id TEXT DEFAULT '',
    record_id TEXT DEFAULT '',
    cell_id TEXT DEFAULT '',
    content TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    content_hash TEXT DEFAULT '',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(
    document_id UNINDEXED,
    content,
    project_id UNINDEXED,
    article_id UNINDEXED,
    document_type UNINDEXED,
    tokenize='unicode61'
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
    response_summary TEXT DEFAULT '',
    reasoning_present INTEGER DEFAULT 0,
    finish_reason    TEXT DEFAULT '',
    config_version   TEXT DEFAULT '',
    structured_status TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS header_mapping_benchmarks (
    benchmark_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT DEFAULT 'completed',
    metrics_json TEXT DEFAULT '{}',
    config_version TEXT DEFAULT '',
    created_at TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    display_name TEXT DEFAULT '',
    email TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_credentials (
    credential_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    api_format TEXT DEFAULT 'openai',
    base_url TEXT DEFAULT '',
    model_id TEXT NOT NULL,
    secret_ref TEXT DEFAULT '',
    encrypted_secret TEXT DEFAULT '',
    secret_nonce TEXT DEFAULT '',
    key_fingerprint TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_by TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_model_allocations (
    allocation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    monthly_token_limit INTEGER DEFAULT 0,
    monthly_cost_limit REAL DEFAULT 0.0,
    created_by TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, credential_id),
    FOREIGN KEY (credential_id) REFERENCES model_credentials(credential_id)
);

CREATE TABLE IF NOT EXISTS user_storage_quotas (
    user_id TEXT PRIMARY KEY,
    quota_bytes INTEGER NOT NULL DEFAULT 10737418240,
    used_bytes INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_objects (
    object_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_id TEXT DEFAULT '',
    article_id TEXT DEFAULT '',
    object_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    checksum TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(relative_path)
);

CREATE TABLE IF NOT EXISTS user_ai_usage_monthly (
    usage_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    credential_id TEXT DEFAULT '',
    usage_month TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost REAL DEFAULT 0.0,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, credential_id, usage_month)
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
CREATE INDEX IF NOT EXISTS idx_standardized_provenance_record ON standardized_cell_provenance(record_id);
CREATE INDEX IF NOT EXISTS idx_standardized_provenance_element ON standardized_cell_provenance(element_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_project ON llm_calls(project_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_article ON llm_calls(article_id);
CREATE INDEX IF NOT EXISTS idx_header_mapping_benchmarks_project ON header_mapping_benchmarks(project_id);
CREATE INDEX IF NOT EXISTS idx_processing_events_project ON processing_events(project_id);
CREATE INDEX IF NOT EXISTS idx_model_credentials_provider ON model_credentials(provider, enabled);
CREATE INDEX IF NOT EXISTS idx_user_model_allocations_user ON user_model_allocations(user_id, enabled);
CREATE INDEX IF NOT EXISTS idx_storage_objects_user ON storage_objects(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_user_ai_usage_month ON user_ai_usage_monthly(usage_month, user_id);
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
CREATE INDEX IF NOT EXISTS idx_chat_threads_scope ON chat_threads(project_id, article_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_citations_message ON chat_citations(message_id);
CREATE TABLE IF NOT EXISTS api_audit_events (
    audit_id TEXT PRIMARY KEY,
    project_id TEXT DEFAULT '',
    user_id TEXT DEFAULT '',
    username TEXT DEFAULT '',
    roles_json TEXT DEFAULT '[]',
    request_id TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    client_ip TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_audit_created ON api_audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_api_audit_user ON api_audit_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_article ON agent_runs(project_id, article_id, status);
CREATE INDEX IF NOT EXISTS idx_article_source_candidates_project ON article_source_candidates(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_interrupts_run ON agent_interrupts(run_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_run_events(run_id, event_id);
CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run ON agent_tool_calls(run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_literature_results_search ON literature_search_results(search_id, created_at);
CREATE INDEX IF NOT EXISTS idx_retrieval_documents_scope ON retrieval_documents(project_id, article_id, document_type);
"""


class Database:
    """SQLite/PostgreSQL database access layer with a stable query API."""

    def __init__(self, db_path: str | Path, database_url: str = ""):
        self.db_path = Path(db_path)
        self.database_url = database_url
        self.dialect = "postgresql" if database_url.startswith(("postgresql://", "postgresql+psycopg://")) else "sqlite"
        self._conn: sqlite3.Connection | None = None
        self._postgres: PostgresConnection | None = PostgresConnection(database_url) if self.dialect == "postgresql" else None

    def connect(self):
        if self._postgres:
            return self._postgres.connect()
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._postgres:
            self._postgres.close()
            return
        if self._conn:
            self._conn.close()
            self._conn = None

    def initialize(self) -> None:
        """Create all tables if they don't exist."""
        if self._postgres:
            self._initialize_postgres()
            return
        conn = self.connect()
        conn.executescript(SCHEMA_SQL)
        self._ensure_columns(conn)
        conn.commit()

    def _initialize_postgres(self) -> None:
        if self.database_url in _POSTGRES_INITIALIZED:
            return
        with _POSTGRES_INITIALIZE_LOCK:
            if self.database_url in _POSTGRES_INITIALIZED:
                return
            profile = os.environ.get("GEOCHEM_PROFILE", "development").strip().lower()
            default_mode = "alembic" if profile in {"staging", "production"} else "auto"
            schema_mode = os.environ.get("GEOCHEM_POSTGRES_SCHEMA_MODE", default_mode).strip().lower()
            if schema_mode not in {"auto", "alembic"}:
                raise RuntimeError(
                    "GEOCHEM_POSTGRES_SCHEMA_MODE must be 'auto' or 'alembic'"
                )
            if schema_mode == "alembic":
                self._verify_postgres_migration()
                _POSTGRES_INITIALIZED.add(self.database_url)
                return
            try:
                for statement in postgres_schema_statements(SCHEMA_SQL):
                    self._postgres.execute(statement)
                self._ensure_columns(self._postgres)
                self._postgres.commit()
            except Exception:
                self._postgres.rollback()
                raise
            _POSTGRES_INITIALIZED.add(self.database_url)

    def _verify_postgres_migration(self) -> None:
        """Require a completed Alembic migration without mutating server DDL."""

        try:
            revision = self._postgres.fetch_one(
                "SELECT version_num FROM alembic_version LIMIT 1"
            )
            projects_table = self._postgres.fetch_one(
                "SELECT to_regclass('projects') AS table_name"
            )
        except Exception as exc:
            raise RuntimeError(
                "PostgreSQL schema is not ready. Run 'alembic upgrade head' before starting GeoChem."
            ) from exc
        if not revision or not revision.get("version_num") or not projects_table or not projects_table.get("table_name"):
            raise RuntimeError(
                "PostgreSQL schema is not ready. Run 'alembic upgrade head' before starting GeoChem."
            )

    def _ensure_columns(self, conn) -> None:
        """Backfill additive columns for databases created by earlier versions."""
        self._ensure_table_columns(conn, "export_jobs", {
            "article_id": "TEXT",
            "table_id": "TEXT",
            "package_path": "TEXT",
        })
        self._ensure_table_columns(conn, "articles", {
            "article_dir": "TEXT DEFAULT ''",
            "owner_user_id": "TEXT DEFAULT ''",
        })
        self._ensure_table_columns(conn, "calculation_records", {
            "candidate_record_id": "TEXT",
            "cell_id": "TEXT",
        })
        self._ensure_table_columns(conn, "document_elements", {
            "page_spans_json": "TEXT DEFAULT '[]'",
            "reading_order": "INTEGER DEFAULT 0",
            "section_path": "TEXT DEFAULT ''",
            "source_backend": "TEXT DEFAULT ''",
            "merge_reason": "TEXT DEFAULT ''",
            "score_reasons_json": "TEXT DEFAULT '[]'",
        })
        self._ensure_table_columns(conn, "learned_extraction_rules", {
            "enabled": "INTEGER DEFAULT 1",
            "element_id": "TEXT DEFAULT ''",
        })
        self._ensure_table_columns(conn, "candidate_cells", {
            "applied_rule_id": "TEXT DEFAULT ''",
            "extraction_method": "TEXT DEFAULT ''",
            "source_label": "TEXT DEFAULT ''",
            "source_quote": "TEXT DEFAULT ''",
            "source_row_snapshot": "TEXT DEFAULT '{}'",
            "evidence_status": "TEXT DEFAULT 'weak'",
            "evidence_reason": "TEXT DEFAULT ''",
        })
        self._ensure_table_columns(conn, "llm_calls", {
            "reasoning_present": "INTEGER DEFAULT 0",
            "finish_reason": "TEXT DEFAULT ''",
            "config_version": "TEXT DEFAULT ''",
            "structured_status": "TEXT DEFAULT ''",
        })
        self._ensure_table_columns(conn, "agent_runs", {
            "workflow_step": "TEXT DEFAULT ''",
            "checkpoint_kind": "TEXT DEFAULT ''",
            "workbench_handoff_status": "TEXT DEFAULT ''",
            "workbench_snapshot_json": "TEXT DEFAULT '{}'",
            "selection_context_json": "TEXT DEFAULT '{}'",
            "handoff_context_json": "TEXT DEFAULT '{}'",
            "workbench_diff_json": "TEXT DEFAULT '{}'",
            "data_version": "TEXT DEFAULT ''",
            "run_kind": "TEXT DEFAULT 'conversation'",
            "model_provider": "TEXT DEFAULT ''",
            "model_name": "TEXT DEFAULT ''",
        })
        self._ensure_table_columns(conn, "chat_threads", {
            "selection_context_json": "TEXT DEFAULT '{}'",
        })
        self._ensure_table_columns(conn, "chat_messages", {
            "ui_payload_json": "TEXT DEFAULT '{}'",
        })
        self._ensure_table_columns(conn, "workflow_tasks", {
            "operation_name": "TEXT DEFAULT ''",
            "payload_json": "TEXT DEFAULT '{}'",
            "task_key": "TEXT DEFAULT ''",
            "broker_task_id": "TEXT DEFAULT ''",
            "retry_count": "INTEGER DEFAULT 0",
        })
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_tasks_active_key
               ON workflow_tasks(task_key)
               WHERE task_key <> '' AND status IN ('pending', 'running')"""
        )

    def _ensure_table_columns(self, conn, table: str, additions: dict[str, str]) -> None:
        if self.dialect == "postgresql":
            existing = {
                row["column_name"]
                for row in conn.fetch_all(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema = current_schema() AND table_name = ?""",
                    (table,),
                )
            }
            for column, decl in additions.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            return
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, decl in additions.items():
            if column not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                except sqlite3.OperationalError as exc:
                    # Multiple initial API requests can initialize the same legacy DB
                    # concurrently. A competing connection may add the column first.
                    if "duplicate column name" not in str(exc).lower():
                        raise

    def execute(self, sql: str, params: tuple = ()):
        if self._postgres:
            return self._postgres.execute(sql, params)
        conn = self.connect()
        return conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]):
        if self._postgres:
            return self._postgres.executemany(sql, params_list)
        conn = self.connect()
        return conn.executemany(sql, params_list)

    def commit(self) -> None:
        if self._postgres:
            self._postgres.commit()
            return
        conn = self.connect()
        conn.commit()

    def rollback(self) -> None:
        if self._postgres:
            self._postgres.rollback()
            return
        conn = self.connect()
        conn.rollback()

    def fetch_one(self, sql: str, params: tuple = ()):
        if self._postgres:
            return self._postgres.fetch_one(sql, params)
        cursor = self.execute(sql, params)
        return cursor.fetchone()

    def fetch_all(self, sql: str, params: tuple = ()) -> list[Any]:
        if self._postgres:
            return self._postgres.fetch_all(sql, params)
        cursor = self.execute(sql, params)
        return cursor.fetchall()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
