from __future__ import annotations

import asyncio
from io import BytesIO

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from geochem.core.project import ProjectManager
from geochem.core.runtime import load_runtime_settings
from geochem.services.admin_governance import AdminGovernanceService
from geochem.web.api import _persist_upload, _safe_upload_name, create_app


def test_safe_upload_name_removes_client_path_and_unsafe_characters():
    assert _safe_upload_name("../../paper:2026?.pdf", "article.pdf") == "paper_2026_.pdf"


def test_persist_upload_streams_within_limit(tmp_path):
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"abc" * 1024))
    destination = tmp_path / "paper.pdf"

    written = asyncio.run(_persist_upload(upload, destination, max_upload_mb=1))

    assert written == 3072
    assert destination.read_bytes() == b"abc" * 1024


def test_persist_upload_rejects_oversized_file_and_removes_partial(tmp_path):
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"x" * (1024 * 1024 + 1)))
    destination = tmp_path / "paper.pdf"

    try:
        asyncio.run(_persist_upload(upload, destination, max_upload_mb=1))
    except HTTPException as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("Oversized upload was accepted")

    assert not destination.exists()


def test_api_documentation_can_be_disabled_without_static_fallback(tmp_path):
    runtime = load_runtime_settings({
        "GEOCHEM_PROFILE": "development",
        "GEOCHEM_STORAGE_ROOT": str(tmp_path),
        "GEOCHEM_ENABLE_API_DOCS": "false",
        "GEOCHEM_OPEN_BROWSER": "false",
    })
    app = create_app(ProjectManager(tmp_path), runtime)

    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404


def test_article_upload_is_rejected_when_local_user_quota_is_exceeded(tmp_path):
    runtime = load_runtime_settings({
        "GEOCHEM_PROFILE": "development",
        "GEOCHEM_STORAGE_ROOT": str(tmp_path),
        "GEOCHEM_OPEN_BROWSER": "false",
    })
    manager = ProjectManager(tmp_path)
    app = create_app(manager, runtime)
    AdminGovernanceService(manager, runtime).set_storage_quota(
        "local-development",
        1,
        "admin-user",
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/import/file?project_id=DEFAULT_WORKSPACE",
            files={"file": ("paper.pdf", b"not-a-real-pdf", "application/pdf")},
        )

    assert response.status_code == 413
    assert "存储配额不足" in response.json()["detail"]
