from __future__ import annotations

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient

from geochem.core.project import ProjectManager
from geochem.core.models import LLMResponse
from geochem.providers.llm_client import LLMClient
from geochem.curation.resource_scoring import ResourceScoringEngine
from geochem.web.api import create_app
from geochem.workbench_service import WorkbenchService


ROOT = Path(__file__).resolve().parents[2]
PDF_FIXTURE = ROOT / "tests" / "feart-09-788349.pdf"


def _workspace(tmp_path):
    pm = ProjectManager(tmp_path)
    project_dir = pm.create_project("Workbench Test", "WEB_TEST")
    article_dir = pm.ensure_article_dir(project_dir, "Spatiotemporal Variation", "10.3389/feart.2021.788349")
    pdf_path = project_dir / article_dir / "source" / "main.pdf"
    shutil.copy2(PDF_FIXTURE, pdf_path)
    now = datetime.now().isoformat()
    headers = [
        {"字段名": "SampleID", "canonical_field": "SampleID"},
        {"字段名": "Depth/m", "canonical_field": "Depth", "默认单位": "m"},
        {"字段名": "TOC %", "canonical_field": "TOC", "默认单位": "%"},
        *[
            {"字段名": f"{field}(wt%)", "canonical_field": field, "默认单位": "wt%"}
            for field in ("SiO2", "Al2O3", "K2O", "Na2O", "Fe2O3", "MgO", "MnO", "TiO2", "CaO", "P2O5")
        ],
        *[
            {"字段名": f"{field} ppm", "canonical_field": field, "默认单位": "ppm"}
            for field in ("Th", "Zr", "Cd", "Co", "Sc", "Hf", "U", "Mo")
        ],
    ]
    db = pm.get_database("WEB_TEST")
    db.execute(
        """INSERT INTO articles
           (article_id, project_id, title, doi, article_dir, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'imported', ?)""",
        ("ART_WEB", "WEB_TEST", "Spatiotemporal Variation", "10.3389/feart.2021.788349", article_dir, now),
    )
    db.execute(
        """INSERT INTO resources
           (resource_id, article_id, resource_type, file_name, local_path, status, created_at)
           VALUES (?, ?, 'main_pdf', 'main.pdf', ?, 'pending', ?)""",
        ("RES_WEB", "ART_WEB", str(pdf_path.relative_to(project_dir)), now),
    )
    db.execute(
        """INSERT INTO header_configs
           (config_id, project_id, name, headers_json, status, created_at, updated_at)
           VALUES ('HDR_WEB', 'WEB_TEST', 'Ordovician', ?, 'active', ?, ?)""",
        (json.dumps(headers), now, now),
    )
    db.execute(
        """INSERT INTO article_header_assignments
           (assignment_id, project_id, article_id, config_id, status, created_at)
           VALUES ('ASSIGN_WEB', 'WEB_TEST', 'ART_WEB', 'HDR_WEB', 'confirmed', ?)""",
        (now,),
    )
    db.commit()
    db.close()
    return pm, headers


def test_new_workbench_schema_is_initialized(tmp_path):
    pm, _headers = _workspace(tmp_path)
    db = pm.get_database("WEB_TEST")
    tables = {row["name"] for row in db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()

    assert {
        "document_elements", "workbench_sessions", "element_selections",
        "extraction_batches", "candidate_records", "candidate_cells", "workflow_tasks",
        "standardized_cell_provenance",
    }.issubset(tables)


def test_pdf_discovery_saves_table_page_and_normalized_bbox(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)

    result = service.discover_article("WEB_TEST", "ART_WEB")
    elements = service.list_elements("WEB_TEST", "ART_WEB")
    page5 = next(item for item in elements if item["element_type"] == "table" and item["page_number"] == 5)
    page6 = next(item for item in elements if item["element_type"] == "table" and item["page_number"] == 6)

    assert result["table"] >= 2
    assert "TABLE 1" in page5["caption"]
    assert "TABLE 2" in page6["caption"]
    assert page5["bbox"][1] < 0.1
    assert page5["bbox"][3] < 0.8
    assert all(0 <= value <= 1 for value in page5["bbox"] + page6["bbox"])
    assert "TOC %" in page5["matched_headers"]
    assert "Th ppm" in page6["matched_headers"]
    assert page5["score_reasons"]


def test_resource_scoring_caps_references_even_with_geochemistry_terms():
    result = ResourceScoringEngine().score(
        element_type="paragraph",
        text="Smith et al. (2020) geochemistry samples reported ppm values. Li et al. (2021) discussed sample isotope data. Wang et al. (2022) TOC ppm.",
        section_path="References",
        matched_headers=["SampleID", "TOC %", "Li ppm"],
    )

    assert result.score <= 0.15
    assert any("硬上限" in reason for reason in result.reasons)


def test_standardize_raw_table_flattens_multilevel_headers():
    service = WorkbenchService()
    raw = {
        "headers": ["Number", "Sample", "relative content of clay minerals(%)", "", "", "", "", "", "", "quantitative analysis of the whole rock (%)", "", ""],
        "rows": [
            ["", "", "K", "C", "I", "S", "I/S", "C/S", "%S", "Clay", "Quartz", "Pyrite"],
            ["", "", "", "", "", "", "", "", "I/S", "", "", ""],
            ["1", "HDP-B1", "2", "1", "20", "/", "75", "2", "8", "38.7", "38.9", "0.7"],
            ["2", "HDP-B2", "2", "1", "13", "/", "82", "2", "7", "36.7", "35.4", "6.8"],
        ],
    }

    normalized = service._standardize_raw_table(raw)

    assert normalized["table_standardized"] is True
    assert normalized["headers"][1] == "Sample"
    assert "relative content of clay minerals(%) K" in normalized["headers"]
    assert "quantitative analysis of the whole rock (%) Quartz" in normalized["headers"]
    assert normalized["rows"][0][1] == "HDP-B1"
    assert normalized["rows"][0][normalized["headers"].index("quantitative analysis of the whole rock (%) Quartz")] == "38.9"
    assert normalized["source_headers"] == raw["headers"]
    assert normalized["source_rows"] == raw["rows"]


def test_standardize_raw_table_cleans_docling_preflattened_headers():
    service = WorkbenchService()
    raw = {
        "headers": [
            "Number.Number.Number",
            "Sample.Sample.Sample",
            "relative content of clay minerals(%).K.K",
            "relative content of clay minerals(%).C.C",
            "relative content of clay minerals(%).I.I",
            "relative content of clay minerals(%).%S.I/S",
            "relative content of clay minerals(%)..C/S",
            "quantitative analysis of the whole rock (%).Clay.Clay",
            "quantitative analysis of the whole rock (%).Quartz.Quartz",
        ],
        "rows": [
            ["1", "HDP-B1", "2", "1", "20", "8", "12", "38.7", "38.9"],
            ["2", "HDP-B2", "2", "1", "13", "7", "12", "36.7", "35.4"],
        ],
    }

    normalized = service._standardize_raw_table(raw)

    assert normalized["headers"][:5] == [
        "Number",
        "Sample",
        "relative content of clay minerals(%) K",
        "relative content of clay minerals(%) C",
        "relative content of clay minerals(%) I",
    ]
    assert "relative content of clay minerals(%) %S I/S" in normalized["headers"]
    assert "relative content of clay minerals(%) %S C/S" in normalized["headers"]
    assert "quantitative analysis of the whole rock (%) Clay" in normalized["headers"]
    assert "quantitative analysis of the whole rock (%) Quartz" in normalized["headers"]
    assert "Number.Number.Number" not in normalized["headers"]


def test_standardize_raw_table_reruns_from_original_headers_after_old_bad_standardization():
    service = WorkbenchService()
    raw = {
        "headers": ["Number.Number.Number", "Sample.Sample.Sample", "clay minerals %", "clay minerals %_2", "clay minerals %_3"],
        "original_headers": [
            "Number.Number.Number",
            "Sample.Sample.Sample",
            "relative content of clay minerals(%).K.K",
            "relative content of clay minerals(%).C.C",
            "relative content of clay minerals(%).I.I",
        ],
        "rows": [["1", "HDP-B1", "2", "1", "20"]],
        "header_parse_method": "local_standardizer",
        "table_standardized": True,
    }

    normalized = service._standardize_raw_table(raw)

    assert normalized["headers"] == [
        "Number",
        "Sample",
        "relative content of clay minerals(%) K",
        "relative content of clay minerals(%) C",
        "relative content of clay minerals(%) I",
    ]


def _p4_multilevel_table_words():
    rows = [
        [(55, "Number"), (88, "Sample"), (131, "relative"), (148, "content"), (165, "of"), (177, "clay"), (200, "minerals(%)"), (330, "quantitative"), (358, "analysis"), (371, "of"), (384, "the"), (400, "whole"), (417, "rock"), (436, "(%)")],
        [(115, "K"), (136, "C"), (157, "I"), (181, "S"), (201, "I/S"), (226, "C/S"), (254, "%S"), (306, "Clay"), (330, "Quartz"), (360, "Microcline"), (402, "Albite"), (430, "Pyrite"), (458, "Calcite"), (488, "Dolomite"), (526, "pyroxene")],
        [(254, "I/S"), (279, "C/S")],
        [(44, "1"), (78, "HDP-B1"), (115, "2"), (136, "1"), (157, "20"), (181, "/"), (201, "75"), (226, "2"), (254, "8"), (279, "12"), (306, "38.7"), (330, "38.9"), (360, "0.7"), (402, "5.6"), (430, "0.7"), (458, "6.1"), (488, "9.3"), (526, "﹨")],
        [(44, "2"), (78, "HDP-B2"), (115, "2"), (136, "1"), (157, "13"), (181, "/"), (201, "82"), (226, "2"), (254, "7"), (279, "12"), (306, "36.7"), (330, "35.4"), (360, "﹨"), (402, "5"), (430, "6.8"), (458, "1.6"), (488, "14.5"), (526, "﹨")],
    ]
    words = []
    for row_index, row in enumerate(rows):
        y0 = 540 + row_index * 17
        for center_x, text in row:
            width = max(8, len(text) * 4)
            x0 = center_x - width / 2
            words.append((x0, y0, x0 + width, y0 + 8, text))
    return words


def test_malformed_flat_headers_are_detected():
    service = WorkbenchService()

    assert service._table_headers_are_malformed_flat({
        "headers": [
            "Number",
            "Sample",
            "clay minerals %",
            "clay minerals %_2",
            "clay minerals %_3",
            "whole rock %",
            "whole rock %_2",
            "whole rock %_3",
        ],
    })


def test_multilevel_table_from_pdf_words_rebuilds_parent_child_headers():
    service = WorkbenchService()

    normalized = service._multilevel_table_from_words(_p4_multilevel_table_words())

    assert normalized is not None
    assert normalized["headers"] == [
        "Number",
        "Sample",
        "relative content of clay minerals(%) K",
        "relative content of clay minerals(%) C",
        "relative content of clay minerals(%) I",
        "relative content of clay minerals(%) S",
        "relative content of clay minerals(%) I/S",
        "relative content of clay minerals(%) C/S",
        "relative content of clay minerals(%) %S I/S",
        "relative content of clay minerals(%) %S C/S",
        "quantitative analysis of the whole rock (%) Clay",
        "quantitative analysis of the whole rock (%) Quartz",
        "quantitative analysis of the whole rock (%) Microcline",
        "quantitative analysis of the whole rock (%) Albite",
        "quantitative analysis of the whole rock (%) Pyrite",
        "quantitative analysis of the whole rock (%) Calcite",
        "quantitative analysis of the whole rock (%) Dolomite",
        "quantitative analysis of the whole rock (%) pyroxene",
    ]
    assert normalized["rows"][0][1] == "HDP-B1"
    assert normalized["rows"][0][normalized["headers"].index("quantitative analysis of the whole rock (%) Quartz")] == "38.9"


def test_standardize_tables_endpoint_accepts_post(tmp_path):
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    response = client.post("/api/v1/articles/ART_WEB/standardize-tables", json={
        "project_id": "WEB_TEST",
        "article_id": "ART_WEB",
        "use_llm": False,
    })

    assert response.status_code == 200
    assert response.json()["task_id"].startswith("TASK_")


def test_standard_table_edit_and_restandardize_endpoints(tmp_path):
    pm, _headers = _workspace(tmp_path)
    db = pm.get_database("WEB_TEST")
    source_headers = ["Sample.Sample.Sample", "relative content of clay minerals(%).K.K"]
    source_rows = [["HDP-B1", "2"]]
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_STD_API', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1, ?, 0.9, ?, ?)""",
            (json.dumps({"source_headers": source_headers, "source_rows": source_rows, "headers": source_headers, "rows": source_rows}), now, now),
        )
        db.commit()
    finally:
        db.close()
    client = TestClient(create_app(pm))

    edit = client.patch("/api/v1/elements/EL_STD_API/standard-table", json={
        "project_id": "WEB_TEST",
        "headers": ["Sample", "Clay K"],
        "rows": [["HDP-B1", "3"]],
        "edit_reason": "manual fix",
    })
    assert edit.status_code == 200
    assert edit.json()["raw_table"]["headers"] == ["Sample", "Clay K"]
    assert edit.json()["raw_table"]["user_edited"] is True

    restored = client.post("/api/v1/elements/EL_STD_API/restandardize-table", json={
        "project_id": "WEB_TEST",
        "mode": "source_headers",
    })
    assert restored.status_code == 200
    assert restored.json()["raw_table"]["headers"] == ["Sample", "relative content of clay minerals(%) K"]


def test_standardize_tables_releases_write_lock_before_progress(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO workflow_tasks
               (task_id, project_id, article_id, task_type, status, progress, message, created_at)
               VALUES ('TASK_LOCK', 'WEB_TEST', 'ART_WEB', 'table_standardization', 'running', 0, '', ?)""",
            (now,),
        )
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_STD_LOCK', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1, ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["Sample", "TOC"], "rows": [["WC-1", "3.2"], ["WC-2", "4.1"]]}), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_STD_LOCK"])

    def progress(message: str, value: float, details: dict[str, object] | None = None) -> None:
        progress_db = pm.get_database("WEB_TEST")
        try:
            progress_db.execute(
                """INSERT INTO workflow_task_events
                   (task_id, level, message, progress, details_json, created_at)
                   VALUES ('TASK_LOCK', 'INFO', ?, ?, ?, ?)""",
                (message, value, json.dumps(details or {}), datetime.now().isoformat()),
            )
            progress_db.commit()
        finally:
            progress_db.close()

    result = service.standardize_tables("WEB_TEST", "ART_WEB", progress=progress)

    assert result["updated"] == 1


def test_update_element_saves_edited_standard_table(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                caption, raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_EDIT_TABLE', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1,
                       'Table 1', ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["Sample", "TOC"], "rows": [["WC-1", "3.2"]], "table_standardized": True}), now, now),
        )
        db.commit()
    finally:
        db.close()

    updated = service.update_standard_table(
        "WEB_TEST",
        "EL_EDIT_TABLE",
        ["Sample", "TOC %"],
        [["WC-1", "3.4"]],
        "unit test edit",
    )

    assert updated["raw_table"]["headers"] == ["Sample", "TOC %"]
    assert updated["raw_table"]["rows"] == [["WC-1", "3.4"]]
    assert updated["raw_table"]["table_standardized"] is True
    assert updated["raw_table"]["user_edited"] is True
    assert updated["raw_table"]["header_parse_method"] == "user_edited_standard_table"
    assert updated["raw_table"]["source_headers"] == ["Sample", "TOC"]


def test_restandardize_table_restores_source_headers_after_edit(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    source_headers = [
        "Number.Number.Number",
        "Sample.Sample.Sample",
        "relative content of clay minerals(%).K.K",
        "relative content of clay minerals(%).C.C",
        "relative content of clay minerals(%).I.I",
    ]
    source_rows = [["1", "HDP-B1", "2", "1", "20"]]
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                caption, raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_RESTD_TABLE', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1,
                       'Table 1', ?, 0.9, ?, ?)""",
            (json.dumps({
                "source_headers": source_headers,
                "source_rows": source_rows,
                "headers": ["bad", "bad_2"],
                "rows": [["x", "y"]],
                "user_edited": True,
                "header_parse_method": "user_edited_standard_table",
            }), now, now),
        )
        db.commit()
    finally:
        db.close()

    restored = service.restandardize_table("WEB_TEST", "EL_RESTD_TABLE")

    assert restored["raw_table"]["headers"] == [
        "Number",
        "Sample",
        "relative content of clay minerals(%) K",
        "relative content of clay minerals(%) C",
        "relative content of clay minerals(%) I",
    ]
    assert restored["raw_table"]["rows"] == source_rows
    assert restored["raw_table"]["user_edited"] is False


def test_standardize_tables_rebuilds_user_edited_table_from_source_headers(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    source_headers = [
        "Sample.Sample.Sample",
        "relative content of clay minerals(%).K.K",
        "relative content of clay minerals(%).C.C",
    ]
    source_rows = [["HDP-B1", "2", "1"]]
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_REBUILD_TABLE', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1, ?, 0.9, ?, ?)""",
            (json.dumps({
                "source_headers": source_headers,
                "source_rows": source_rows,
                "headers": ["bad", "bad_2"],
                "rows": [["x", "y"]],
                "user_edited": True,
                "header_parse_method": "user_edited_standard_table",
            }), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_REBUILD_TABLE"])

    service.standardize_tables("WEB_TEST", "ART_WEB")
    rebuilt = next(item for item in service.list_elements("WEB_TEST", "ART_WEB") if item["element_id"] == "EL_REBUILD_TABLE")

    assert rebuilt["raw_table"]["headers"] == [
        "Sample",
        "relative content of clay minerals(%) K",
        "relative content of clay minerals(%) C",
    ]
    assert rebuilt["raw_table"]["rows"] == source_rows
    assert rebuilt["raw_table"]["user_edited"] is False


def test_standardize_tables_uses_pdf_words_when_source_headers_are_malformed(tmp_path, monkeypatch):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_BAD_FLAT_TABLE', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1, ?, 0.9, ?, ?)""",
            (json.dumps({
                "source_headers": ["Number", "Sample", "clay minerals %", "clay minerals %_2", "clay minerals %_3", "whole rock %", "whole rock %_2"],
                "source_rows": [["1", "HDP-B1", "2", "1", "20", "38.7", "38.9"]],
                "headers": ["Number", "Sample", "clay minerals %", "clay minerals %_2", "clay minerals %_3", "whole rock %", "whole rock %_2"],
                "rows": [["1", "HDP-B1", "2", "1", "20", "38.7", "38.9"]],
            }), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_BAD_FLAT_TABLE"])

    def fake_pdf_rebuild(_db, _project_id, _element):
        return {
            "headers": ["Number", "Sample", "relative content of clay minerals(%) K"],
            "rows": [["1", "HDP-B1", "2"]],
            "source_headers": ["Number", "Sample", "relative content of clay minerals(%) K"],
            "source_rows": [["1", "HDP-B1", "2"]],
            "header_rows": [["Number", "Sample", "relative content of clay minerals(%)"]],
            "source_header_rows": [["Number", "Sample", "relative content of clay minerals(%)"]],
            "body_text": "Number\tSample\trelative content of clay minerals(%) K\n1\tHDP-B1\t2",
            "header_parse_method": "pdf_words_multilevel_reconstruction",
            "standardization_source": "pdf_words_multilevel_header",
            "header_confidence": 0.84,
            "table_standardized": True,
            "user_edited": False,
            "standardization_warnings": ["原始表头已丢失层级，已从 PDF words 重建"],
        }

    monkeypatch.setattr(service, "_standardize_multilevel_table_from_pdf", fake_pdf_rebuild)

    service.standardize_tables("WEB_TEST", "ART_WEB")
    rebuilt = next(item for item in service.list_elements("WEB_TEST", "ART_WEB") if item["element_id"] == "EL_BAD_FLAT_TABLE")

    assert rebuilt["raw_table"]["headers"] == ["Number", "Sample", "relative content of clay minerals(%) K"]
    assert rebuilt["raw_table"]["standardization_source"] == "pdf_words_multilevel_header"


def test_rotated_sample_table_reconstruction_from_pdf_words():
    service = WorkbenchService()
    words = [
        (100, 720, 118, 740, "Sample"),
        (130, 720, 150, 740, "SiO2"),
        (165, 720, 192, 740, "Al2O3"),
        (210, 720, 232, 740, "CIA"),
        (100, 675, 122, 695, "HDP-B1"),
        (130, 675, 150, 695, "57.02"),
        (165, 675, 192, 695, "15.36"),
        (210, 675, 232, 695, "71.1"),
        (100, 645, 122, 665, "HDP-B2"),
        (130, 645, 150, 665, "54.12"),
        (165, 645, 192, 665, "15.09"),
        (210, 645, 232, 665, "70.8"),
        (100, 615, 122, 635, "HDP-B3"),
        (130, 615, 150, 635, "56.19"),
        (165, 615, 192, 635, "15.48"),
        (210, 615, 232, 635, "72.2"),
        (100, 585, 122, 605, "HDP-B4"),
        (130, 585, 150, 605, "56.27"),
        (165, 585, 192, 605, "15.39"),
        (210, 585, 232, 605, "72.0"),
    ]

    table = service._rotated_sample_table_from_words(words)

    assert table is not None
    assert table["headers"] == ["Sample", "SiO2", "Al2O3", "CIA"]
    assert table["rows"][0] == ["HDP-B1", "57.02", "15.36", "71.1"]
    assert table["rows"][3] == ["HDP-B4", "56.27", "15.39", "72.0"]


def test_hit_test_finds_existing_document_element(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    element = next(item for item in service.list_elements("WEB_TEST", "ART_WEB") if item["element_type"] == "table")

    result = service.hit_test_elements("WEB_TEST", "ART_WEB", element["resource_id"], element["page_number"], element["bbox"])

    assert result["suggested_action"] == "use_existing"
    assert result["hits"][0]["element"]["element_id"] == element["element_id"]


def test_resource_rule_is_visible_in_rule_memory(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)

    rule = service.add_pre_extraction_rule("WEB_TEST", "ART_WEB", {
        "source_alias": "specimen",
        "target_header": "SampleID",
        "rule_type": "source_alias",
        "element_id": "EL_RULE",
    })
    memory = service.rule_memory("WEB_TEST", "ART_WEB")

    assert any(item["rule_id"] == rule["rule_id"] for item in memory["extraction"])
    assert next(item for item in memory["extraction"] if item["rule_id"] == rule["rule_id"])["enabled"] is True


def test_disabled_pre_extraction_rule_is_not_applied(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    rule = service.add_pre_extraction_rule("WEB_TEST", "ART_WEB", {
        "source_alias": "specimen",
        "target_header": "SampleID",
        "rule_type": "source_alias",
    })
    service.update_rule_memory("WEB_TEST", rule["rule_id"], {"enabled": False})
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_SAMPLE', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1, ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["specimen", "TOC"], "rows": [["WC-1", "3.2"]]}), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_SAMPLE"])

    batch = service.create_extraction_batch("WEB_TEST", "ART_WEB", use_llm=False)
    payload = service.batch_records("WEB_TEST", batch["batch_id"])

    assert payload["records"] == []


def test_table_rule_preflight_suggests_sampleid_before_extraction(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                caption, raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_PREFLIGHT', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1,
                       'Table 1 sample data', ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["specimen", "TOC"], "rows": [["WC-1", "3.2"]]}), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_PREFLIGHT"])

    result = service.table_rule_preflight("WEB_TEST", "ART_WEB")
    specimen = next(item for item in result["items"] if item["source_header"] == "specimen")

    assert specimen["suggested_target_header"] == "SampleID"
    assert specimen["confidence"] >= 0.9


def test_table_rule_preflight_keeps_exact_target_when_display_matches_source(tmp_path):
    pm, headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        headers = [
            {**header, "字段名": "SiO2"}
            if header.get("canonical_field") == "SiO2" else header
            for header in headers
        ]
        db.execute("UPDATE header_configs SET headers_json = ? WHERE config_id = 'HDR_WEB'", (json.dumps(headers),))
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_SIO2_PREFLIGHT', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1, ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["SiO2"], "rows": [["73.5"]]}), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_SIO2_PREFLIGHT"])

    result = service.table_rule_preflight("WEB_TEST", "ART_WEB")
    sio2 = next(item for item in result["items"] if item["source_header"] == "SiO2")

    assert sio2["suggested_target_header"] == "SiO2"
    assert sio2["mapping_source"] == "exact"


def test_multilevel_source_header_maps_by_leaf_field_and_keeps_percent_context():
    service = WorkbenchService()
    targets = [
        {"display_header": "K", "canonical_field": "K", "target_unit": "%", "description": "全岩钾"},
        {"display_header": "K2O(wt%)", "canonical_field": "K2O", "target_unit": "wt%", "description": "氧化钾"},
    ]
    source = "relative content of clay minerals(%) K"

    profile = service._source_header_profile(source)
    mapping = dict(service._map_source_headers([source], targets))

    assert profile == {
        "field_token": "K",
        "group_context": "relative content of clay minerals(%)",
        "detected_unit": "%",
    }
    assert mapping[source] == "K"


def test_truncated_llm_mapping_json_is_not_recovered_from_partial_output():
    service = WorkbenchService()
    mappings, status = service._parse_table_mapping_response(
        '''{"mappings":[
          {"source_header":"Sample","target_header":"SampleID","confidence":0.95,"reason":"alias"},
          {"source_header":"TOC","target_header":"TOC %","confidence":0.91,"reason":"unit"},
          {"source_header":"unfinished"'''
    )

    assert mappings == []
    assert status == "json_invalid"


def test_llm_header_mapping_rejects_serial_number_as_sample_id():
    service = WorkbenchService()
    target = {"display_header": "SampleID", "canonical_field": "SampleID"}

    assert service._llm_mapping_is_safe("Number", target, {}) is False


def test_confirm_table_rule_preflight_writes_rule_memory(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)

    result = service.confirm_table_rule_preflight("WEB_TEST", "ART_WEB", [{
        "source_header": "sample no.",
        "target_header": "SampleID",
        "target_unit": "id",
        "conditions": {"conversion_formula": "keep text"},
        "confidence": 0.95,
        "element_id": "EL_TABLE_RULE",
    }])
    memory = service.rule_memory("WEB_TEST", "ART_WEB")["extraction"]

    assert result["created"] == 1
    assert any(rule["pattern"] == "sample no." and rule["target_header"] == "SampleID" for rule in memory)
    rule = next(rule for rule in memory if rule["pattern"] == "sample no.")
    assert rule["target_unit"] == "id"
    assert "keep text" in rule["conditions"]


def test_llm_assisted_preflight_only_maps_to_bound_target_headers(tmp_path, monkeypatch):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                caption, raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_LLM_PREFLIGHT', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 2,
                       'Table 2 organic geochemistry', ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["Sample", "Total organic carbon"], "rows": [["WC-1", "3.2"]]}), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_LLM_PREFLIGHT"])
    captured = {}

    class FakeLLMClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, messages, **kwargs):
            captured["messages"] = messages
            captured["task_name"] = kwargs.get("task_name")
            return LLMResponse(
                content=json.dumps({
                    "mappings": [
                        {"source_header": "Total organic carbon", "target_header": "TOC %", "confidence": 0.91, "reason": "TOC alias"},
                        {"source_header": "Unknown", "target_header": "NotInSchema", "confidence": 0.99, "reason": "invalid"},
                    ]
                }),
                final_content=json.dumps({
                    "mappings": [
                        {"source_header": "Total organic carbon", "target_header": "TOC %", "confidence": 0.91, "reason": "TOC alias"},
                        {"source_header": "Unknown", "target_header": "NotInSchema", "confidence": 0.99, "reason": "invalid"},
                    ]
                }),
                model="fake",
                provider="fake",
            )

    monkeypatch.setattr("geochem.workbench_service.LLMClient", FakeLLMClient)
    result = service.assist_table_rule_preflight("WEB_TEST", "ART_WEB")
    toc = next(item for item in result["items"] if item["source_header"] == "Total organic carbon")

    assert toc["suggested_target_header"] == "TOC %"
    assert toc["mapping_source"] == "llm"
    assert result["llm_assisted_count"] == 1
    assert captured["task_name"] == "field_mapping"
    prompt = json.loads(captured["messages"][1]["content"])
    assert "rows" not in prompt
    assert any(candidate["display_header"] == "TOC %" for item in prompt["source_headers"] for candidate in item["candidate_targets"])


def test_llm_assisted_preflight_keeps_local_suggestions_when_model_returns_non_json(tmp_path, monkeypatch):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_LLM_EMPTY', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 2, ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["Total organic carbon"], "rows": [["3.2"]]}), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_LLM_EMPTY"])

    class FakeLLMClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, *_args, **_kwargs):
            return LLMResponse(content="I cannot provide JSON right now.", final_content="I cannot provide JSON right now.", model="fake", provider="fake")

    monkeypatch.setattr("geochem.workbench_service.LLMClient", FakeLLMClient)
    result = service.assist_table_rule_preflight("WEB_TEST", "ART_WEB")

    assert result["llm_assisted_count"] == 0
    assert "保留本地预检建议" in result["llm_message"]


def test_reasoning_only_mapping_response_never_generates_suggestions(tmp_path, monkeypatch):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_REASONING_ONLY', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 2, ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["Total organic carbon"], "rows": [["3.2"]]}), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_REASONING_ONLY"])

    class FakeLLMClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, *_args, **_kwargs):
            return LLMResponse(content="", final_content="", reasoning_present=True, model="fake", provider="fake")

    monkeypatch.setattr("geochem.workbench_service.LLMClient", FakeLLMClient)
    result = service.assist_table_rule_preflight("WEB_TEST", "ART_WEB")

    assert result["llm_assisted_count"] == 0
    assert result["batches"]
    assert any(batch["status"] == "reasoning_only" for batch in result["batches"])
    assert "reasoning" not in result["llm_message"].lower()


def test_model_capabilities_endpoint_reports_active_mapping_route(tmp_path):
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    response = client.get("/api/v1/model-capabilities")

    assert response.status_code == 200
    assert "field_mapping" in response.json()


def test_llm_call_ids_are_unique_without_database_sequence():
    client = object.__new__(LLMClient)
    call_ids = {LLMClient._generate_call_id(client) for _ in range(100)}

    assert len(call_ids) == 100
    assert all(call_id.startswith("LLM_") for call_id in call_ids)


def test_preflight_rule_with_lowercase_target_fills_sampleid(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                raw_table_json, relevance_score, created_at, updated_at)
               VALUES ('EL_CASE_RULE', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'table', 1, ?, 0.9, ?, ?)""",
            (json.dumps({"headers": ["specimen", "TOC"], "rows": [["WC-1", "3.2"]]}), now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_CASE_RULE"])
    service.confirm_table_rule_preflight("WEB_TEST", "ART_WEB", [{
        "source_header": "specimen",
        "target_header": "sampleID",
        "confidence": 0.95,
        "element_id": "EL_CASE_RULE",
    }])

    batch = service.extract_tables("WEB_TEST", "ART_WEB", use_llm=False)
    payload = service.batch_records("WEB_TEST", batch["batch_id"])

    assert payload["records"][0]["data"]["SampleID"] == "WC-1"


def test_paragraph_cues_surface_possible_missed_data_paragraph(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                text_content, context_text, relevance_score, created_at, updated_at)
               VALUES ('EL_CUE', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'paragraph', 3,
                       'Samples WC-1 and WC-2 were collected from the Wufeng Formation and TOC values are 3.2 and 4.1%.',
                       '', 0.42, ?, ?)""",
            (now, now),
        )
        db.commit()
    finally:
        db.close()

    result = service.paragraph_cues("WEB_TEST", "ART_WEB")
    cue = next(item for item in result["cues"] if item["element_id"] == "EL_CUE")

    assert cue["bucket"] in {"high", "possible_missed"}
    assert "线索" in cue["reason"] or "样品编号" in cue["reason"]


def test_llm_prompt_contains_confirmed_rule_memory(tmp_path, monkeypatch):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.add_pre_extraction_rule("WEB_TEST", "ART_WEB", {
        "source_alias": "sample",
        "target_header": "SampleID",
        "rule_type": "source_alias",
    })
    captured = {}

    class FakeLLMClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, messages, **_kwargs):
            captured["messages"] = messages
            return LLMResponse(content='{"records":[]}', model="fake", provider="fake")

    monkeypatch.setattr("geochem.workbench_service.LLMClient", FakeLLMClient)
    db = pm.get_database("WEB_TEST")
    try:
        element = {
            "element_id": "EL_PAR",
            "element_type": "paragraph",
            "text_content": "Samples WC-1 and WC-2 contain TOC data.",
            "context_text": "",
            "caption": "",
            "preview_path": "",
        }
        service._llm_records(db, "WEB_TEST", "ART_WEB", element, service.target_headers("WEB_TEST", "ART_WEB"), service._extraction_memory("WEB_TEST", "ART_WEB"))
    finally:
        db.close()

    payload = captured["messages"][1]["content"]
    assert "extraction_memory" in payload
    assert "sample" in payload
    assert "SampleID" in payload


def test_paragraph_llm_prompt_uses_relevant_header_subset():
    service = WorkbenchService()
    headers = [
        {
            "display_header": "SampleID",
            "canonical_field": "SampleID",
            "target_unit": "",
            "description": "Sample identifier",
            "order": 0,
        },
        {
            "display_header": "TOC %",
            "canonical_field": "TOC",
            "target_unit": "%",
            "description": "Total organic carbon",
            "order": 1,
        },
        *[
            {
                "display_header": f"Trace{index} ppm",
                "canonical_field": f"Trace{index}",
                "target_unit": "ppm",
                "description": f"Unrelated trace element {index}",
                "order": index + 2,
            }
            for index in range(80)
        ],
    ]
    element = {
        "element_type": "paragraph",
        "text_content": "Samples HDP-B1 and HDP-B2 have TOC values of 3.2 and 4.1%.",
        "context_text": "",
        "matched_headers": ["TOC %"],
    }

    subset = service._llm_header_subset(element, headers, {"rules": []})
    names = [item["display_header"] for item in subset]

    assert len(subset) <= 48
    assert "SampleID" in names
    assert "TOC %" in names
    assert "Trace79 ppm" not in names


def test_paragraph_llm_batch_preserves_each_resource_provenance(tmp_path, monkeypatch):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    captured = {"calls": 0}

    class FakeLLMClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, messages, **_kwargs):
            captured["calls"] += 1
            request = json.loads(messages[1]["content"])
            resources = request["resources"]
            return LLMResponse(
                content=json.dumps({
                    "resources": [
                        {
                            "element_id": resource["element_id"],
                            "records": [{
                                "sample_id": f"S-{index + 1}",
                                "source_quote": resource["source_text"][:40],
                                "values": {"TOC %": str(index + 1)},
                            }],
                        }
                        for index, resource in enumerate(resources)
                    ],
                }),
                model="fake",
                provider="fake",
            )

    monkeypatch.setattr("geochem.workbench_service.LLMClient", FakeLLMClient)
    elements = [
        {
            "element_id": f"EL_BATCH_{index}",
            "element_type": "paragraph",
            "text_content": f"Sample S-{index + 1} has TOC {index + 1}%.",
            "context_text": "",
            "matched_headers": ["TOC %"],
        }
        for index in range(4)
    ]
    db = pm.get_database("WEB_TEST")
    try:
        result = service._llm_paragraph_records_batch(
            db,
            "WEB_TEST",
            "ART_WEB",
            elements,
            service.target_headers("WEB_TEST", "ART_WEB"),
            {"rules": []},
        )
    finally:
        db.close()

    assert captured["calls"] == 1
    assert set(result) == {item["element_id"] for item in elements}
    assert result["EL_BATCH_0"][0]["sample_id"] == "S-1"
    assert result["EL_BATCH_3"][0]["_source_method"] == "llm_paragraph"


def test_extract_paragraphs_keeps_selected_table_records(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    elements = service.list_elements("WEB_TEST", "ART_WEB")
    table = next(item for item in elements if item["element_type"] == "table" and item["page_number"] == 5)
    paragraph = next(item for item in elements if item["element_type"] == "paragraph")
    service.set_selections("WEB_TEST", "ART_WEB", [table["element_id"], paragraph["element_id"]])

    result = service.extract_paragraphs(
        "WEB_TEST",
        "ART_WEB",
        element_ids=[paragraph["element_id"]],
        use_llm=False,
    )

    assert result["record_count"] == 46


def test_llm_value_without_visible_ts_evidence_is_marked_insufficient(tmp_path, monkeypatch):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    db = pm.get_database("WEB_TEST")
    try:
        config = db.fetch_one("SELECT headers_json FROM header_configs WHERE config_id = 'HDR_WEB'")
        headers = json.loads(config["headers_json"])
        headers.append({"字段名": "TS %", "canonical_field": "TS", "默认单位": "%"})
        db.execute(
            "UPDATE header_configs SET headers_json = ? WHERE config_id = 'HDR_WEB'",
            (json.dumps(headers),),
        )
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                text_content, context_text, relevance_score, created_at, updated_at)
               VALUES ('EL_TS_PAR', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'paragraph', 2,
                       'Samples WC-1 and WC-2 contain TOC data and were collected from the Wufeng Formation.',
                       '', 0.9, ?, ?)""",
            (now, now),
        )
        db.commit()
    finally:
        db.close()
    service.set_selections("WEB_TEST", "ART_WEB", ["EL_TS_PAR"])

    class FakeLLMClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, _messages, **_kwargs):
            return LLMResponse(
                content='{"records":[{"sample_id":"WC-1","values":{"TS %":"3.2"}}]}',
                model="fake",
                provider="fake",
            )

    monkeypatch.setattr("geochem.workbench_service.LLMClient", FakeLLMClient)

    batch = service.create_extraction_batch("WEB_TEST", "ART_WEB", use_llm=True)
    payload = service.batch_records("WEB_TEST", batch["batch_id"])
    ts_cell = payload["records"][0]["cells"]["TS %"]

    assert ts_cell["extraction_method"] == "llm_paragraph"
    assert ts_cell["evidence_status"] == "insufficient"
    assert ts_cell["risk_level"] == "high"


def test_pdf_discovery_releases_database_before_progress_logging(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)

    def progress(_message, _value, _details):
        progress_db = pm.get_database("WEB_TEST")
        progress_db.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = 'WEB_TEST'",
            (datetime.now().isoformat(),),
        )
        progress_db.commit()
        progress_db.close()

    result = service.discover_article("WEB_TEST", "ART_WEB", progress=progress)

    assert result["table"] >= 2


def test_candidate_grid_merges_same_sample_and_preserves_headers(tmp_path):
    pm, headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    table_elements = [
        item for item in service.list_elements("WEB_TEST", "ART_WEB")
        if item["element_type"] == "table" and item["page_number"] in {5, 6}
    ]
    service.set_selections("WEB_TEST", "ART_WEB", [item["element_id"] for item in table_elements])

    result = service.create_extraction_batch("WEB_TEST", "ART_WEB", use_llm=False)
    payload = service.batch_records("WEB_TEST", result["batch_id"])
    first = next(row for row in payload["records"] if row["sample_id"] == "c1-01")
    duplicate = next(row for row in payload["records"] if row["sample_id"] == "c1-20")

    assert result["record_count"] == 46
    assert [item["display_header"] for item in payload["headers"]] == [item["字段名"] for item in headers]
    assert first["merge_status"] == "auto_merged"
    assert first["data"]["Depth/m"] == "1202.3"
    assert first["data"]["TOC %"] == "3.49"
    assert first["data"]["SiO2(wt%)"] == "73.51"
    assert first["data"]["Th ppm"] == "14.33"
    assert first["cells"]["TOC %"]["page_number"] == 5
    assert first["cells"]["Th ppm"]["page_number"] == 6
    assert first["cells"]["TOC %"]["mapping_status"] == "confirmed"
    assert first["cells"]["Th ppm"]["review_status"] == "confirmed"
    assert duplicate["cells"]["Depth/m"]["alternatives"]
    assert duplicate["cells"]["Depth/m"]["risk_level"] == "high"
    assert "Mo ppm" in first["data"]


def test_fastapi_health_and_article_elements(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    client = TestClient(create_app(pm))

    assert client.get("/api/v1/health").json()["status"] == "ok"
    articles = client.get("/api/v1/articles", params={"project_id": "WEB_TEST"}).json()
    elements = client.get("/api/v1/articles/ART_WEB/elements", params={"project_id": "WEB_TEST"}).json()
    settings = client.get("/api/v1/settings").json()

    assert articles[0]["article_id"] == "ART_WEB"
    assert any(item["element_type"] == "table" and item["page_number"] == 5 for item in elements)
    assert settings["providers"]
    assert all("api_key" not in provider for provider in settings["providers"])


def test_settings_api_saves_custom_provider_and_manual_model(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    monkeypatch.setenv("GEOCHEM_CONFIG", str(config_path))
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    response = client.put("/api/v1/settings", json={
        "default_provider": "opencode-go",
        "default_model": "go-plan",
        "vision_provider": "",
        "vision_model": "",
        "task_models": {
            "_default": {"provider": "opencode-go", "model": "go-plan", "temperature": 0.1, "max_tokens": 4096},
            "field_mapping": {"provider": "opencode-go", "model": "go-plan", "temperature": 0.0, "max_tokens": 8192},
        },
        "providers": [{
            "name": "opencode-go",
            "display_name": "OpenCode GO",
            "api_format": "openai",
            "base_url": "https://example.invalid/v1",
            "api_key_ref": "${OPENCODE_GO_API_KEY}",
            "is_custom": True,
            "models": [],
        }],
        "api_keys": {},
    })
    settings = client.get("/api/v1/settings").json()
    provider = next(item for item in settings["providers"] if item["name"] == "opencode-go")
    routed_provider = next(item for item in settings["providers"] if item["name"] == "opencode-go-openai")

    assert response.status_code == 200
    assert settings["default_provider"] == "opencode-go-openai"
    assert settings["task_models"]["field_mapping"]["provider"] == "opencode-go-openai"
    assert settings["task_models"]["field_mapping"]["model"] == "go-plan"
    assert provider["is_custom"] is True
    assert "go-plan" in {model["name"] for model in routed_provider["models"]}
    assert "api_key" not in provider


def test_settings_include_core_provider_presets(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    monkeypatch.setenv("GEOCHEM_CONFIG", str(config_path))
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    settings = client.get("/api/v1/settings").json()
    providers = {item["name"]: item for item in settings["providers"]}

    for name in ("deepseek-openai", "deepseek-anthropic", "opencode-go-openai", "opencode-go-anthropic", "xiaomi", "xiaomi-anthropic", "openrouter", "openai", "anthropic"):
        assert name in providers
        assert "api_key" not in providers[name]
    assert providers["opencode-go-openai"]["base_url"] == "https://opencode.ai/zen/go/v1"
    assert "kimi-k2.7-code" in {model["name"] for model in providers["opencode-go-openai"]["models"]}
    assert "qwen3.7-plus" in {model["name"] for model in providers["opencode-go-anthropic"]["models"]}


def test_opencode_model_alias_is_normalized_in_routes(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    monkeypatch.setenv("GEOCHEM_CONFIG", str(config_path))
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    response = client.put("/api/v1/settings", json={
        "default_provider": "opencode-go",
        "default_model": "opencode-go/qwen3.7-plus",
        "vision_provider": "",
        "vision_model": "",
        "task_models": {
            "_default": {"provider": "opencode-go", "model": "opencode-go/qwen3.7-plus", "temperature": 0.1, "max_tokens": 4096},
        },
        "providers": [],
        "api_keys": {},
    })
    settings = client.get("/api/v1/settings").json()

    assert response.status_code == 200
    assert settings["default_provider"] == "opencode-go-anthropic"
    assert settings["default_model"] == "qwen3.7-plus"


def test_model_setup_saves_single_model_to_all_text_tasks(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    monkeypatch.setenv("GEOCHEM_CONFIG", str(config_path))
    monkeypatch.setattr("geochem.web.api._test_provider_config", lambda provider, api_key="", model_name="": {
        "success": True,
        "models": [{"name": model_name, "display_name": model_name, "supports_vision": False, "max_tokens": 4096}],
        "provider": provider.name,
        "model": model_name,
        "message": "ok",
        "last_tested_at": "2026-07-02T00:00:00",
    })
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    response = client.put("/api/v1/model-setup", json={
        "provider_preset": "opencode-go",
        "model_id": "qwen3.7-plus",
        "api_key": "sk-opencode",
        "base_url": "",
    })
    settings = client.get("/api/v1/settings").json()
    setup = client.get("/api/v1/model-setup").json()

    assert response.status_code == 200
    assert setup["provider"] == "opencode-go-anthropic"
    assert setup["model"] == "qwen3.7-plus"
    assert setup["configured"] is True
    assert setup["api_key_ref"] == "${OPENCODE_GO_API_KEY}"
    assert "sk-opencode" not in config_path.read_text(encoding="utf-8")
    assert "${OPENCODE_GO_API_KEY}" in config_path.read_text(encoding="utf-8")
    for task_name in ("_default", "field_mapping", "document_record_extraction", "unit_suggestion", "rule_learning", "data_extraction"):
        assert settings["task_models"][task_name]["provider"] == "opencode-go-anthropic"
        assert settings["task_models"][task_name]["model"] == "qwen3.7-plus"
    assert "figure_extraction" not in settings["task_models"]


def test_settings_api_never_persists_plaintext_api_keys(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    monkeypatch.setenv("GEOCHEM_CONFIG", str(config_path))
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    response = client.put("/api/v1/settings", json={
        "default_provider": "opencode-go",
        "default_model": "kimi-k2.7-code",
        "vision_provider": "",
        "vision_model": "",
        "task_models": {
            "_default": {"provider": "opencode-go", "model": "kimi-k2.7-code", "temperature": 0.1, "max_tokens": 4096},
        },
        "providers": [],
        "api_keys": {"opencode-go-openai": "sk-unit-test-secret"},
    })
    settings = client.get("/api/v1/settings").json()
    provider = next(item for item in settings["providers"] if item["name"] == "opencode-go-openai")
    saved = config_path.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "sk-unit-test-secret" not in saved
    assert "${OPENCODE_GO_API_KEY}" in saved
    assert provider["api_key_ref"] == "${OPENCODE_GO_API_KEY}"
    assert "api_key" not in provider


def test_model_setup_test_reports_failure_without_saving(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    monkeypatch.setenv("GEOCHEM_CONFIG", str(config_path))
    monkeypatch.setattr("geochem.web.api._test_provider_config", lambda provider, api_key="", model_name="": (_ for _ in ()).throw(RuntimeError("401 invalid key")))
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    response = client.post("/api/v1/model-setup/test", json={
        "provider_preset": "openrouter",
        "model_id": "openai/gpt-4o-mini",
        "api_key": "sk-wrong",
        "base_url": "",
    })
    body = response.json()

    assert response.status_code == 200
    assert body["success"] is False
    assert "401 invalid key" in body["message"]


def test_session_creation_is_idempotent_under_concurrent_requests(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)

    with ThreadPoolExecutor(max_workers=6) as pool:
        sessions = list(pool.map(lambda _index: service.ensure_session("WEB_TEST", "ART_WEB"), range(12)))

    assert len({item["session_id"] for item in sessions}) == 1


def test_header_only_csv_can_be_imported_through_api(tmp_path):
    pm, _headers = _workspace(tmp_path)
    client = TestClient(create_app(pm))

    response = client.post(
        "/api/v1/header-configs/import",
        params={"project_id": "WEB_TEST", "name": "Header Only"},
        files={"file": ("headers.csv", "SampleID,Li ppm,Al2O3(wt%)\n", "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["field_count"] == 3
    configs = client.get("/api/v1/header-configs", params={"project_id": "WEB_TEST"}).json()
    imported = next(item for item in configs if item["name"] == "Header Only")
    assert [item["字段名"] for item in imported["headers"]] == ["SampleID", "Li ppm", "Al2O3(wt%)"]


def test_pdf_upload_can_create_first_article(tmp_path):
    pm = ProjectManager(tmp_path)
    pm.create_project("Empty Web Workspace", "EMPTY_WEB")
    client = TestClient(create_app(pm))

    response = client.post(
        "/api/v1/import/file",
        params={"project_id": "EMPTY_WEB"},
        files={"file": ("article.pdf", PDF_FIXTURE.read_bytes(), "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["article_id"]
    assert payload["resource_type"] == "main_pdf"
    articles = client.get("/api/v1/articles", params={"project_id": "EMPTY_WEB"}).json()
    assert len(articles) == 1
    assert articles[0]["resource_count"] == 1


def test_resource_download_rejects_database_path_outside_project(tmp_path):
    pm, _headers = _workspace(tmp_path)
    external_pdf = tmp_path / "outside.pdf"
    external_pdf.write_bytes(b"not a managed project resource")
    db = pm.get_database("WEB_TEST")
    try:
        db.execute(
            "UPDATE resources SET local_path=? WHERE resource_id='RES_WEB'",
            (str(external_pdf),),
        )
        db.commit()
    finally:
        db.close()

    response = TestClient(create_app(pm)).get(
        "/api/v1/resources/RES_WEB/pdf",
        params={"project_id": "WEB_TEST"},
    )

    assert response.status_code == 403


def test_element_preview_rejects_database_path_outside_project(tmp_path):
    pm, _headers = _workspace(tmp_path)
    external_preview = tmp_path / "outside.png"
    external_preview.write_bytes(b"not a managed project preview")
    db = pm.get_database("WEB_TEST")
    try:
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type,
                preview_path, created_at, updated_at)
               VALUES ('ELM_OUTSIDE', 'WEB_TEST', 'ART_WEB', 'RES_WEB', 'figure', ?, ?, ?)""",
            (str(external_preview), datetime.now().isoformat(), datetime.now().isoformat()),
        )
        db.commit()
    finally:
        db.close()

    response = TestClient(create_app(pm)).get(
        "/api/v1/elements/ELM_OUTSIDE/preview",
        params={"project_id": "WEB_TEST"},
    )

    assert response.status_code == 403


def test_short_element_symbols_are_case_sensitive(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    targets = [{
        "display_header": "In ppm",
        "canonical_field": "In",
        "description": "铟含量",
        "target_unit": "ppm",
    }]

    assert service._matched_headers("In ppm was measured by ICP-MS.", targets) == ["In ppm"]
    assert service._matched_headers("In this study, samples were collected.", targets) == []


def test_rediscovery_preserves_referenced_elements_without_fk_failure(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    table = next(item for item in service.list_elements("WEB_TEST", "ART_WEB") if item["element_type"] == "table")
    service.set_selections("WEB_TEST", "ART_WEB", [table["element_id"]])

    result = service.discover_article("WEB_TEST", "ART_WEB")
    refreshed = service.list_elements("WEB_TEST", "ART_WEB")

    assert result["table"] >= 2
    assert any(item["element_id"] == table["element_id"] and item["selected"] for item in refreshed)


def test_extraction_skips_selected_figures_for_manual_handling(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    elements = service.list_elements("WEB_TEST", "ART_WEB")
    table = next(item for item in elements if item["element_type"] == "table")
    figure = next(item for item in elements if item["element_type"] == "figure")
    events = []
    service.set_selections("WEB_TEST", "ART_WEB", [table["element_id"], figure["element_id"]])

    result = service.create_extraction_batch(
        "WEB_TEST", "ART_WEB", use_llm=False,
        progress=lambda message, progress, details: events.append((message, details)),
    )
    refreshed = service.list_elements("WEB_TEST", "ART_WEB")

    assert result["record_count"] > 0
    assert any("图像资源等待人工处理" in message for message, _details in events)
    assert any(details.get("completed") and details.get("total") for _message, details in events)
    assert next(item for item in refreshed if item["element_id"] == figure["element_id"])["status"] != "waiting_for_vision_model"


def test_table_extraction_honors_explicit_selected_element_scope(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    tables = [
        item for item in service.list_elements("WEB_TEST", "ART_WEB")
        if item["element_type"] == "table"
    ]
    assert len(tables) >= 2
    service.set_selections(
        "WEB_TEST", "ART_WEB", [tables[0]["element_id"], tables[1]["element_id"]]
    )

    result = service.extract_tables(
        "WEB_TEST",
        "ART_WEB",
        use_llm=False,
        element_ids=[tables[0]["element_id"]],
    )

    assert result["processed_element_ids"] == [tables[0]["element_id"]]
    assert result["processed_element_count"] == 1


def test_manual_image_cell_upserts_pending_candidate_cell(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    elements = service.list_elements("WEB_TEST", "ART_WEB")
    table = next(item for item in elements if item["element_type"] == "table")
    figure = next(item for item in elements if item["element_type"] == "figure")
    service.set_selections("WEB_TEST", "ART_WEB", [table["element_id"]])
    batch = service.create_extraction_batch("WEB_TEST", "ART_WEB", use_llm=False)
    db = pm.get_database("WEB_TEST")
    try:
        record = db.fetch_one(
            "SELECT candidate_record_id FROM candidate_records WHERE batch_id = ? ORDER BY row_index LIMIT 1",
            (batch["batch_id"],),
        )
    finally:
        db.close()

    cell = service.upsert_manual_image_cell(
        "WEB_TEST", record["candidate_record_id"], figure["element_id"], "Mo ppm", "12.3",
        "Figure caption indicates Mo enrichment",
    )

    assert cell["target_header"] == "Mo ppm"
    assert cell["value"] == "12.3"
    assert cell["element_id"] == figure["element_id"]
    assert cell["mapping_status"] == "manual"
    assert cell["review_status"] == "pending"


def test_finalize_snapshots_cell_sources_and_trace_search(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    tables = [
        item for item in service.list_elements("WEB_TEST", "ART_WEB")
        if item["element_type"] == "table" and item["page_number"] in {5, 6}
    ]
    service.set_selections("WEB_TEST", "ART_WEB", [item["element_id"] for item in tables])
    service.create_extraction_batch("WEB_TEST", "ART_WEB", use_llm=False)

    finalized = service.finalize_standardized("WEB_TEST", "ART_WEB")
    summaries = service.trace_records("WEB_TEST", query="c1-01")
    detail = service.trace_record_detail("WEB_TEST", summaries["items"][0]["record_id"])
    toc = next(field for field in detail["fields"] if field["target_header"] == "TOC %")
    thorium = next(field for field in detail["fields"] if field["target_header"] == "Th ppm")

    assert finalized["records"] == 46
    assert summaries["total"] == 1
    assert summaries["items"][0]["sample_id"] == "c1-01"
    assert {source["page_number"] for source in summaries["items"][0]["sources"]} == {5, 6}
    assert toc["source"]["page_number"] == 5
    assert thorium["source"]["page_number"] == 6
    assert toc["source_complete"] is True
    assert thorium["source"]["resource_id"] == "RES_WEB"


def test_trace_api_defaults_to_all_articles_and_deduplicates_finalize_runs(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    tables = [
        item for item in service.list_elements("WEB_TEST", "ART_WEB")
        if item["element_type"] == "table" and item["page_number"] in {5, 6}
    ]
    service.set_selections("WEB_TEST", "ART_WEB", [item["element_id"] for item in tables])
    service.create_extraction_batch("WEB_TEST", "ART_WEB", use_llm=False)
    service.finalize_standardized("WEB_TEST", "ART_WEB")
    service.finalize_standardized("WEB_TEST", "ART_WEB")
    client = TestClient(create_app(pm))

    response = client.get("/api/v1/trace-records", params={"project_id": "WEB_TEST"})
    filtered = client.get(
        "/api/v1/trace-records",
        params={"project_id": "WEB_TEST", "article_id": "ART_WEB", "q": "Th ppm"},
    )
    record_id = response.json()["items"][0]["record_id"]
    detail = client.get(
        f"/api/v1/trace-records/{record_id}", params={"project_id": "WEB_TEST"},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 46
    assert filtered.json()["total"] == 43
    assert detail.status_code == 200
    assert detail.json()["resources"][0]["resource_id"] == "RES_WEB"
    db = pm.get_database("WEB_TEST")
    try:
        assert db.fetch_one("SELECT COUNT(*) AS n FROM standardized_records WHERE article_id = 'ART_WEB'")["n"] == 46
        assert db.fetch_one("SELECT COUNT(*) AS n FROM standardized_cell_provenance")["n"] > 46
    finally:
        db.close()


def test_finalize_excludes_candidate_grade_d_records(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    tables = [
        item for item in service.list_elements("WEB_TEST", "ART_WEB")
        if item["element_type"] == "table" and item["page_number"] in {5, 6}
    ]
    service.set_selections("WEB_TEST", "ART_WEB", [item["element_id"] for item in tables])
    batch = service.create_extraction_batch("WEB_TEST", "ART_WEB", use_llm=False)
    db = pm.get_database("WEB_TEST")
    try:
        first = db.fetch_one(
            "SELECT candidate_record_id FROM candidate_records WHERE batch_id = ? ORDER BY row_index LIMIT 1",
            (batch["batch_id"],),
        )
        db.execute(
            "UPDATE candidate_records SET quality_grade = 'D' WHERE candidate_record_id = ?",
            (first["candidate_record_id"],),
        )
        db.commit()
    finally:
        db.close()

    finalized = service.finalize_standardized("WEB_TEST", "ART_WEB")
    summaries = service.trace_records("WEB_TEST")

    assert finalized["records"] == 45
    assert summaries["total"] == 45


def test_export_auto_finalizes_and_records_trace(tmp_path):
    pm, _headers = _workspace(tmp_path)
    service = WorkbenchService(pm)
    service.discover_article("WEB_TEST", "ART_WEB")
    tables = [
        item for item in service.list_elements("WEB_TEST", "ART_WEB")
        if item["element_type"] == "table" and item["page_number"] in {5, 6}
    ]
    service.set_selections("WEB_TEST", "ART_WEB", [item["element_id"] for item in tables])
    service.create_extraction_batch("WEB_TEST", "ART_WEB", use_llm=False)
    out_dir = tmp_path / "chosen_exports"

    with TestClient(create_app(pm)) as client:
        response = client.post(
            "/api/v1/articles/ART_WEB/export",
            json={
                "project_id": "WEB_TEST",
                "format": "csv",
                "output_dir": str(out_dir),
            },
        )
        assert response.status_code == 200
        result = response.json()
        jobs = client.get(
            "/api/v1/export-jobs",
            params={"project_id": "WEB_TEST", "article_id": "ART_WEB"},
        )
        download = client.get(
            f"/api/v1/export-jobs/{result['job_id']}/download",
            params={"project_id": "WEB_TEST"},
        )

    summaries = service.trace_records("WEB_TEST")

    assert result["records"] == 46
    assert result["job_id"].startswith("EXP_")
    assert Path(result["path"]).parent == out_dir
    assert Path(result["path"]).is_file()
    assert jobs.status_code == 200
    assert jobs.json()[0]["job_id"] == result["job_id"]
    assert download.status_code == 200
    assert "text/csv" in download.headers["content-type"]
    assert download.content.startswith(b"\xef\xbb\xbf")
    assert summaries["total"] == 46
    db = pm.get_database("WEB_TEST")
    try:
        job = db.fetch_one("SELECT * FROM export_jobs WHERE article_id = 'ART_WEB'")
        assert job["output_path"] == result["path"]
        assert job["record_count"] == 46
    finally:
        db.close()
