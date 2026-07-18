"""Tests for the UI-independent workflow orchestration layer."""

from __future__ import annotations

import json
from datetime import datetime

from openpyxl import Workbook

from geochem.core.project import ProjectManager
from geochem.workflow import BrowserService, EventBus, WorkflowRunner


NOW = datetime.now().isoformat()


def _project(tmp_path):
    manager = ProjectManager(base_dir=tmp_path)
    manager.create_project("Workflow Test", "WORKFLOW_TEST")
    db = manager.get_database("WORKFLOW_TEST")
    db.execute(
        "INSERT INTO articles (article_id, project_id, title, doi, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ART_001", "WORKFLOW_TEST", "Test Paper", "10.1234/test", "imported", NOW),
    )
    db.execute(
        "INSERT INTO resources (resource_id, article_id, resource_type, file_name, local_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("RES_001", "ART_001", "supplementary_excel", "data.xlsx", "raw/data.xlsx", "pending", NOW),
    )
    db.execute(
        """INSERT INTO candidate_tables
        (table_id, article_id, resource_id, source_type, sheet_name, row_count, col_count, extract_method, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TBL_001", "ART_001", "RES_001", "excel", "Sheet1", 1, 1, "reader", 1.0, NOW),
    )
    db.execute(
        "INSERT INTO candidate_rows (row_id, table_id, row_index, raw_data) VALUES (?, ?, ?, ?)",
        ("ROW_001", "TBL_001", 0, json.dumps({"SampleID": "S1"})),
    )
    db.execute(
        """INSERT INTO standardized_records
        (record_id, article_id, table_id, row_id, data, source_file, source_table, source_row,
         original_fields, original_units, original_values, mapped_fields, mapped_units,
         mapping_rule_ids, calculation_ids, review_statuses, confidence_scores, quality_grade, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "STD_001", "ART_001", "TBL_001", "ROW_001", json.dumps({"SampleID": "S1"}),
            "data.xlsx", "Sheet1", 0, "{}", "{}", "{}", "{}", "{}", "{}", "{}",
            json.dumps({"SampleID": "auto"}), json.dumps({"SampleID": 1.0}), "A", NOW,
        ),
    )
    db.commit()
    db.close()
    return manager


def test_workflow_runner_traces_standardized_table(tmp_path):
    manager = _project(tmp_path)

    result = WorkflowRunner(project_manager=manager).trace_table(
        project_id="WORKFLOW_TEST",
        table_id="TBL_001",
    )

    assert result[0]["record_id"] == "STD_001"


def test_workflow_runner_imports_local_excel(tmp_path):
    manager = _project(tmp_path)
    source = tmp_path / "source.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SampleID", "Li ppm"])
    sheet.append(["S1", 42.1])
    workbook.save(source)
    events = []
    bus = EventBus()
    bus.subscribe(events.append)

    result = WorkflowRunner(project_manager=manager, event_bus=bus).import_files(
        project_id="WORKFLOW_TEST",
        file_paths=[source],
    )

    assert result["count"] == 1
    assert result["resources"][0]["file_name"] == "source.xlsx"
    assert events[-1].event_type == "import"


def test_confirm_browser_access_saves_handoff_when_fetch_is_empty(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    monkeypatch.setattr(
        BrowserService,
        "read_payload_after_auth",
        lambda self, url: {"text": "", "html": "", "pdf_url": ""},
    )

    result = WorkflowRunner(project_manager=manager).confirm_browser_access(
        project_id="WORKFLOW_TEST",
        article_id="ART_001",
        url="https://doi.org/10.3390/min14030258",
    )

    db = manager.get_database("WORKFLOW_TEST")
    try:
        resource = db.fetch_one("SELECT * FROM resources WHERE resource_id = ?", (result["resource_id"],))
    finally:
        db.close()
    assert result["fetched_content"] is False
    assert result["status"] == "auth_confirmed"
    assert resource["status"] == "auth_confirmed"
