"""Tests for workflow services and UI view models."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
import yaml
from openpyxl import Workbook

from geochem.core.project import ProjectManager
from geochem.ui.viewmodels import DataFrameTableModel, ProjectRepository
from geochem.workflow import EventBus, TaskService, WorkflowRunner

NOW = datetime.now().isoformat()


def _project(tmp_path):
    pm = ProjectManager(base_dir=tmp_path)
    path = pm.create_project("UI Test Project", "UI_TEST")
    db = pm.get_database("UI_TEST")
    db.execute(
        "INSERT INTO articles (article_id, project_id, title, doi, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ART_001", "UI_TEST", "Test Paper", "10.1234/test", "imported", NOW),
    )
    db.execute(
        "INSERT INTO resources (resource_id, article_id, resource_type, file_name, local_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("RES_001", "ART_001", "supplementary_excel", "data.xlsx", "raw/supplementary/data.xlsx", "pending", NOW),
    )
    db.execute(
        """INSERT INTO candidate_tables
        (table_id, article_id, resource_id, source_type, sheet_name, row_count, col_count, extract_method, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TBL_001", "ART_001", "RES_001", "excel", "Sheet1", 1, 2, "reader", 1.0, NOW),
    )
    db.execute(
        """INSERT INTO candidate_columns (column_id, table_id, raw_name, normalized_name, unit_candidate, dtype, sample_values)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("COL_001", "TBL_001", "SampleID", "SampleID", None, "string", json.dumps(["S1"])),
    )
    db.execute(
        """INSERT INTO candidate_rows (row_id, table_id, row_index, raw_data)
        VALUES (?, ?, ?, ?)""",
        ("ROW_001", "TBL_001", 0, json.dumps({"SampleID": "S1", "Li ppm": 42.1})),
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
    db.execute(
        """INSERT INTO llm_calls
        (call_id, project_id, article_id, agent_name, skill_name, model_provider,
         model_name, input_tokens, output_tokens, total_tokens, estimated_cost,
         latency_ms, started_at, status, retry_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "LLM_000001", "UI_TEST", "ART_001", "Schema Mapper", "field_mapping",
            "xiaomi", "mimo-v2.5-pro", 1200, 400, 1600, 0.012, 850, NOW, "success", 0,
        ),
    )
    db.execute(
        """INSERT INTO llm_calls
        (call_id, project_id, article_id, agent_name, skill_name, model_provider,
         model_name, input_tokens, output_tokens, total_tokens, estimated_cost,
         latency_ms, started_at, status, retry_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "LLM_000002", "UI_TEST", "ART_001", "Teaching Agent", "rule_learning",
            "xiaomi", "mimo-v2.5-pro", 80000, 12000, 92000, 0.75, 2400, NOW, "success", 1,
        ),
    )
    db.commit()
    db.close()
    return pm, path


def test_table_model_basic_display():
    model = DataFrameTableModel([{"A": 1, "B": "x"}], ["A", "B"])

    assert model.rowCount() == 1
    assert model.columnCount() == 2
    assert model.headerData(0, Qt.Horizontal) == "A"
    assert model.data(model.index(0, 1), Qt.DisplayRole) == "x"


def test_project_repository_loads_ui_rows(tmp_path):
    pm, _ = _project(tmp_path)
    repo = ProjectRepository(pm)

    metrics = repo.dashboard_metrics("UI_TEST")
    tables = repo.candidate_tables("UI_TEST")
    rows, columns = repo.candidate_rows("UI_TEST", "TBL_001")
    standardized, std_columns = repo.standardized_records("UI_TEST", "TBL_001")
    trace = repo.trace_rows("UI_TEST", "TBL_001")
    token_stats = repo.token_stats("UI_TEST")

    assert metrics["articles"] == 1
    assert tables[0]["table_id"] == "TBL_001"
    assert rows[0]["SampleID"] == "S1"
    assert "SampleID" in columns
    assert standardized[0]["Record_ID"] == "STD_001"
    assert "Quality_Grade" in std_columns
    assert trace[0]["record_id"] == "STD_001"
    assert token_stats["summary"]["calls"] == 2
    assert token_stats["summary"]["total_tokens"] == 93600
    assert token_stats["daily"][0]["total_tokens"] == 93600
    assert token_stats["agents"][0]["agent_name"] == "Teaching Agent"
    assert token_stats["anomalies"][0]["retry_count"] == 1


def test_task_service_dispatches_workflow_action(tmp_path):
    pm, _ = _project(tmp_path)
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    service = TaskService(WorkflowRunner(project_manager=pm, event_bus=bus))

    result = service.run("trace_table", project_id="UI_TEST", table_id="TBL_001")

    assert result[0]["record_id"] == "STD_001"


def test_header_config_from_excel_reads_units_and_groups(tmp_path):
    pm, _ = _project(tmp_path)
    xlsx = tmp_path / "headers.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["SampleID", "Age（min）", "Al2O3(wt%)", "Li\nppm", "δ15Nbulk\n‰"])
    ws.append(["S1", 450, 12.3, 42.1, 3.2])
    wb.save(xlsx)

    rows, summary = ProjectRepository(pm).header_config_from_excel(xlsx)

    assert summary["field_count"] == 5
    assert [row["字段名"] for row in rows] == ["SampleID", "Age（min）", "Al2O3(wt%)", "Li ppm", "δ15Nbulk ‰"]
    assert rows[2]["默认单位"] == "wt%"
    assert rows[3]["默认单位"] == "ppm"
    assert rows[4]["字段组"] == "isotope_organic"


def test_header_config_from_header_only_csv(tmp_path):
    pm, _ = _project(tmp_path)
    csv_path = tmp_path / "headers_only.csv"
    csv_path.write_text("SampleID,Li ppm,Al2O3(wt%)\n", encoding="utf-8")

    rows, summary = ProjectRepository(pm).header_config_from_file(csv_path)

    assert summary["field_count"] == 3
    assert [row["字段名"] for row in rows] == ["SampleID", "Li ppm", "Al2O3(wt%)"]


def test_import_files_workflow_adds_resource(tmp_path):
    pm, _ = _project(tmp_path)
    xlsx = tmp_path / "source.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["SampleID", "Li ppm"])
    ws.append(["S1", 42.1])
    wb.save(xlsx)
    service = TaskService(WorkflowRunner(project_manager=pm, event_bus=EventBus()))

    result = service.run("import_files", project_id="UI_TEST", file_paths=[str(xlsx)])
    resources = ProjectRepository(pm).resources("UI_TEST")

    assert result["count"] == 1
    assert any(row["file_name"] == "source.xlsx" for row in resources)


def test_header_config_can_be_saved_loaded_and_assigned(tmp_path):
    pm, _ = _project(tmp_path)
    repo = ProjectRepository(pm)
    rows = [
        {"序号": 1, "字段名": "SampleID", "类型": "文本", "默认单位": "—", "审核策略": "自动校验", "必填": "是", "状态": "已完成", "字段组": "basic_info"},
        {"序号": 2, "字段名": "Li ppm", "类型": "数值", "默认单位": "ppm", "审核策略": "单位/换算需审核", "必填": "—", "状态": "配置中", "字段组": "trace_elements"},
    ]

    config_id = repo.save_header_config("UI_TEST", "测试表头", rows)
    configs = repo.list_header_configs("UI_TEST")
    loaded_rows, summary = repo.load_header_config("UI_TEST", config_id)
    suggestion = repo.suggest_header_config_for_article("UI_TEST", "ART_001")
    assignment_id = repo.assign_header_config_to_article("UI_TEST", "ART_001", config_id)

    assert configs[0]["field_count"] == 2
    assert (_ / "headers" / f"{config_id}.json").exists()
    assert loaded_rows[1]["字段名"] == "Li ppm"
    assert summary["name"] == "测试表头"
    assert suggestion["config_id"] == config_id
    assert assignment_id.startswith("HASN_")


def test_confirm_browser_access_saves_fallback_resource_when_fetch_empty(tmp_path, monkeypatch):
    pm, _ = _project(tmp_path)

    from geochem.workflow import BrowserService

    monkeypatch.setattr(BrowserService, "read_payload_after_auth", lambda self, url: {"text": "", "html": "", "pdf_url": ""})
    service = TaskService(WorkflowRunner(project_manager=pm, event_bus=EventBus()))

    result = service.run(
        "confirm_browser_access",
        project_id="UI_TEST",
        article_id="ART_001",
        url="https://doi.org/10.3390/min14030258",
    )
    resources = ProjectRepository(pm).resources("UI_TEST")

    assert result["fetched_content"] is False
    assert result["status"] == "auth_confirmed"
    assert any(row["resource_id"] == result["resource_id"] and row["status"] == "auth_confirmed" for row in resources)


def test_import_page_discovery_excludes_raw_resources(tmp_path):
    pm, _ = _project(tmp_path)
    repo = ProjectRepository(pm)

    rows = repo.resource_discovery_rows("UI_TEST")

    assert rows
    assert all(row["discovery_kind"] != "resource" for row in rows)
    assert all(row.get("discovery_name") != "data.xlsx" for row in rows)


def test_resource_discovery_shows_article_evidence(tmp_path):
    pm, _ = _project(tmp_path)
    db = pm.get_database("UI_TEST")
    db.execute(
        """INSERT INTO article_evidence
           (evidence_id, project_id, article_id, resource_id, target_header, evidence_type,
            evidence_text, page_or_section, confidence, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("EVD_000001", "UI_TEST", "ART_001", "RES_001", "Li ppm", "table_caption", "Table 1. Trace element data", "page 5", 0.8, "candidate", NOW),
    )
    db.commit()
    db.close()

    rows = ProjectRepository(pm).resource_discovery_rows("UI_TEST")

    assert any(row["discovery_kind"] == "evidence" and row["discovery_name"] == "Li ppm" for row in rows)


def test_selected_resource_items_are_persisted_for_extraction(tmp_path):
    pm, _ = _project(tmp_path)
    db = pm.get_database("UI_TEST")
    db.execute(
        """INSERT INTO table_assets
           (asset_id, resource_id, article_id, table_number, title, caption,
            page_or_sheet, raw_file_path, extract_method, confidence, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "TASS_000001", "RES_001", "ART_001", "Table 1", "Table 1. Trace elements",
            "| SampleID | Li ppm |\n| --- | --- |\n| S1 | 42.1 |", "page 5",
            str(tmp_path / "page5.png"),
            "pdfplumber", 0.8, "pending", NOW,
        ),
    )
    db.execute(
        """INSERT INTO article_evidence
           (evidence_id, project_id, article_id, resource_id, evidence_type,
            evidence_text, page_or_section, confidence, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("EVD_000001", "UI_TEST", "ART_001", "RES_001", "figure_caption", "Fig. 2. REE patterns", "page 6", 0.7, "candidate", NOW),
    )
    db.execute(
        """INSERT INTO article_evidence
           (evidence_id, project_id, article_id, resource_id, evidence_type,
            evidence_text, page_or_section, confidence, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("EVD_000002", "UI_TEST", "ART_001", "RES_001", "paragraph", "Whole-rock geochemistry data are listed in Table 1.", "page 4", 0.75, "candidate", NOW),
    )
    db.commit()
    db.close()

    service = TaskService(WorkflowRunner(project_manager=pm, event_bus=EventBus()))
    result = service.run(
        "select_extraction_items",
        project_id="UI_TEST",
        selected=[("table", "TASS_000001"), ("figure", "EVD_000001"), ("text", "EVD_000002")],
    )
    repo = ProjectRepository(pm)
    selected = repo.selected_extraction_items("UI_TEST")
    summary = repo.selected_source_summary("UI_TEST")
    elements = repo.discovered_elements("UI_TEST")

    assert result == {"tables": 1, "figures": 1, "texts": 1, "total": 3}
    assert {item["item_type"] for item in selected} == {"table", "figure", "text"}
    assert summary["total"] == 3
    assert summary["tables"] == 1
    assert summary["figures"] == 1
    assert summary["texts"] == 1
    assert elements["tables"][0]["status"] == "selected_for_extraction"
    assert selected[0]["raw_file_path"].endswith("page5.png")
    assert any(row["evidence_type"] == "figure_caption" for row in elements["figures"])


def test_settings_data_and_save(tmp_path, monkeypatch):
    pm, _ = _project(tmp_path)
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "default_project_dir": str(tmp_path / "projects"),
            "providers": [
                {
                    "name": "xiaomi",
                    "display_name": "Xiaomi MiMo",
                    "api_key": "${MIMO_API_KEY}",
                    "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                    "api_format": "openai",
                    "enabled": True,
                    "models": [
                        {
                            "name": "mimo-v2.5-pro",
                            "display_name": "MiMo V2.5 Pro",
                            "max_tokens": 8192,
                            "supports_streaming": True,
                            "supports_vision": False,
                            "pricing": {"input_price": 0, "output_price": 0, "cached_input_price": 0},
                        }
                    ],
                    "default_headers": {},
                }
            ],
            "task_models": {
                "_default": {
                    "provider": "xiaomi",
                    "model": "mimo-v2.5-pro",
                    "temperature": 0.2,
                    "max_tokens": 4096,
                }
            },
            "fallback_chain": [],
            "log_level": "INFO",
            "log_dir": "logs",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("GEOCHEM_CONFIG", str(config_path))
    monkeypatch.setenv("MIMO_API_KEY", "sk-test-1234567890")
    repo = ProjectRepository(pm)

    settings = repo.settings_data("UI_TEST")
    assert settings["default_provider"] == "xiaomi"
    assert settings["providers"][0]["key_status"] == "configured"
    assert "MIMO_API_KEY" in settings["providers"][0]["api_key_mask"]

    repo.save_settings({
        "default_provider": "xiaomi",
        "default_model": "mimo-v2.5-pro",
        "temperature": 0.7,
        "default_project_dir": str(tmp_path / "new_projects"),
    })
    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert updated["default_project_dir"] == str(tmp_path / "new_projects")
    assert updated["task_models"]["_default"]["temperature"] == 0.7
