from __future__ import annotations

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient

from geochem.core.project import ProjectManager
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

    assert result["record_count"] == 47
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

    assert finalized["records"] == 47
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
    assert response.json()["total"] == 47
    assert filtered.json()["total"] == 46
    assert detail.status_code == 200
    assert detail.json()["resources"][0]["resource_id"] == "RES_WEB"
    db = pm.get_database("WEB_TEST")
    try:
        assert db.fetch_one("SELECT COUNT(*) AS n FROM standardized_records WHERE article_id = 'ART_WEB'")["n"] == 47
        assert db.fetch_one("SELECT COUNT(*) AS n FROM standardized_cell_provenance")["n"] > 47
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

    assert finalized["records"] == 46
    assert summaries["total"] == 46


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

    result = service.export_article("WEB_TEST", "ART_WEB", "csv", str(out_dir))
    summaries = service.trace_records("WEB_TEST")

    assert result["records"] == 47
    assert Path(result["path"]).parent == out_dir
    assert Path(result["path"]).is_file()
    assert summaries["total"] == 47
    db = pm.get_database("WEB_TEST")
    try:
        job = db.fetch_one("SELECT * FROM export_jobs WHERE article_id = 'ART_WEB'")
        assert job["output_path"] == result["path"]
        assert job["record_count"] == 47
    finally:
        db.close()
