from __future__ import annotations

from fastapi.testclient import TestClient

from geochem.core.project import ProjectManager
from geochem.web.api import create_app


def test_local_admin_page_reports_honest_preview_state(tmp_path):
    manager = ProjectManager(tmp_path)
    manager.create_project("Admin Preview", "ADMIN_PREVIEW")

    with TestClient(create_app(manager)) as client:
        status = client.get("/api/v1/admin/status")
        users = client.get("/api/v1/admin/users")
        create = client.post("/api/v1/admin/users", json={"username": "new-user"})

    assert status.status_code == 200
    assert status.json()["auth_mode"] == "disabled"
    assert status.json()["user_management"]["mode"] == "local-preview"
    assert users.status_code == 200
    assert users.json()["preview"] is True
    assert users.json()["users"][0]["username"] == "Local Developer"
    assert create.status_code == 409
    assert "Keycloak" in create.json()["detail"]
