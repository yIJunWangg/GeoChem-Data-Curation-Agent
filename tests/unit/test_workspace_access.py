from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from geochem.core.project import DEFAULT_WORKSPACE_ID, ProjectManager
from geochem.core.runtime import RuntimeSettings
from geochem.services.workspace_access import WorkspaceAccessService
from geochem.web.auth import AuthenticatedUser, AuthenticationMiddleware


class MembershipAuthenticator:
    def authenticate(self, authorization: str) -> AuthenticatedUser:
        user_id = authorization.removeprefix("Bearer ").strip()
        if not user_id:
            raise ValueError("missing token")
        return AuthenticatedUser(
            subject=user_id,
            username=user_id,
            email=f"{user_id}@example.test",
            roles=frozenset({"admin"}) if user_id == "platform-admin" else frozenset({"viewer"}),
            claims={},
        )


def _service(tmp_path) -> tuple[ProjectManager, WorkspaceAccessService]:
    manager = ProjectManager(tmp_path / "workspaces")
    service = WorkspaceAccessService(manager)
    service.bootstrap_default_organization()
    return manager, service


def test_new_account_is_not_auto_added_after_startup_bootstrap(tmp_path):
    manager, service = _service(tmp_path)
    db = manager.get_default_database()
    now = datetime.now().isoformat()
    try:
        db.execute(
            """INSERT INTO user_profiles
               (user_id, username, display_name, email, status, created_at, updated_at)
               VALUES ('new-user', 'new-user', '', '', 'active', ?, ?)""",
            (now, now),
        )
        db.commit()
    finally:
        db.close()

    assert service.list_workspaces("new-user", {"viewer"}) == []
    assert service.list_workspaces("platform-admin", {"admin"})[0]["workspace_role"] == "owner"


def test_workspace_roles_control_business_actions(tmp_path):
    _manager, service = _service(tmp_path)
    service.upsert_member(DEFAULT_WORKSPACE_ID, "owner-user", "owner", "platform-admin")
    service.upsert_member(DEFAULT_WORKSPACE_ID, "curator-user", "curator", "platform-admin")
    service.upsert_member(DEFAULT_WORKSPACE_ID, "reviewer-user", "reviewer", "platform-admin")
    service.upsert_member(DEFAULT_WORKSPACE_ID, "viewer-user", "viewer", "platform-admin")

    assert service.require(DEFAULT_WORKSPACE_ID, "viewer-user", {"viewer"}, "read") == "viewer"
    with pytest.raises(PermissionError):
        service.require(DEFAULT_WORKSPACE_ID, "viewer-user", {"viewer"}, "curate")
    assert service.require(DEFAULT_WORKSPACE_ID, "curator-user", {"viewer"}, "curate") == "curator"
    with pytest.raises(PermissionError):
        service.require(DEFAULT_WORKSPACE_ID, "curator-user", {"viewer"}, "review")
    assert service.require(DEFAULT_WORKSPACE_ID, "reviewer-user", {"viewer"}, "review") == "reviewer"
    with pytest.raises(PermissionError):
        service.require(DEFAULT_WORKSPACE_ID, "reviewer-user", {"viewer"}, "curate")
    assert service.require(DEFAULT_WORKSPACE_ID, "owner-user", {"viewer"}, "manage") == "owner"
    assert service.require(DEFAULT_WORKSPACE_ID, "platform-admin", {"admin"}, "manage") == "owner"


def test_workspace_membership_is_authoritative_in_http_middleware(tmp_path):
    _manager, service = _service(tmp_path)
    service.upsert_member(DEFAULT_WORKSPACE_ID, "curator-user", "curator", "platform-admin")
    service.upsert_member(DEFAULT_WORKSPACE_ID, "reviewer-user", "reviewer", "platform-admin")
    service.upsert_member(DEFAULT_WORKSPACE_ID, "viewer-user", "viewer", "platform-admin")

    app = FastAPI()
    app.add_middleware(
        AuthenticationMiddleware,
        settings=RuntimeSettings(auth_mode="oidc", oidc_issuer_url="https://id.example.test"),
        authenticator=MembershipAuthenticator(),
        workspace_access=service,
    )

    @app.get("/api/v1/workflow/items")
    def read_items():
        return {"ok": True}

    @app.post("/api/v1/workflow/items")
    def curate_items():
        return {"ok": True}

    @app.post("/api/v1/reviews/finalize")
    def review_items():
        return {"ok": True}

    client = TestClient(app)
    headers = lambda user: {"Authorization": f"Bearer {user}"}

    assert client.get("/api/v1/workflow/items", headers=headers("viewer-user")).status_code == 200
    assert client.post("/api/v1/workflow/items", headers=headers("viewer-user")).status_code == 403
    assert client.post("/api/v1/workflow/items", headers=headers("curator-user")).status_code == 200
    assert client.post("/api/v1/reviews/finalize", headers=headers("curator-user")).status_code == 403
    assert client.post("/api/v1/reviews/finalize", headers=headers("reviewer-user")).status_code == 200
    assert client.post("/api/v1/workflow/items", headers=headers("reviewer-user")).status_code == 403
    assert client.post("/api/v1/workflow/items", headers=headers("platform-admin")).status_code == 200


def test_object_ids_cannot_cross_workspace_boundary(tmp_path):
    manager, service = _service(tmp_path)
    db = manager.get_default_database()
    now = datetime.now().isoformat()
    try:
        db.execute(
            """INSERT INTO articles (article_id, project_id, title, created_at)
               VALUES ('ART_OWN', ?, 'Owned article', ?)""",
            (DEFAULT_WORKSPACE_ID, now),
        )
        db.execute(
            """INSERT INTO projects
               (project_id, organization_id, project_name, created_at, updated_at)
               VALUES ('OTHER_WORKSPACE', 'ORG_DEFAULT', 'Other', ?, ?)""",
            (now, now),
        )
        db.execute(
            """INSERT INTO articles (article_id, project_id, title, created_at)
               VALUES ('ART_OTHER', 'OTHER_WORKSPACE', 'Other article', ?)""",
            (now,),
        )
        db.commit()
    finally:
        db.close()

    service.require_request_objects(DEFAULT_WORKSPACE_ID, "/api/v1/articles/ART_OWN")
    with pytest.raises(PermissionError):
        service.require_request_objects(DEFAULT_WORKSPACE_ID, "/api/v1/articles/ART_OTHER")


def test_last_active_owner_cannot_be_removed(tmp_path):
    _manager, service = _service(tmp_path)
    service.upsert_member(DEFAULT_WORKSPACE_ID, "owner-one", "owner", "platform-admin")
    with pytest.raises(ValueError, match="最后一位"):
        service.remove_member(DEFAULT_WORKSPACE_ID, "owner-one")

    service.upsert_member(DEFAULT_WORKSPACE_ID, "owner-two", "owner", "platform-admin")
    service.remove_member(DEFAULT_WORKSPACE_ID, "owner-one")
    assert all(member["user_id"] != "owner-one" for member in service.list_members(DEFAULT_WORKSPACE_ID))
