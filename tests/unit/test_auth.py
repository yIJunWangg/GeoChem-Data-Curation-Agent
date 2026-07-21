from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from geochem.core.runtime import RuntimeSettings
from geochem.services.execution_context import current_user_id
from geochem.web.auth import (
    AuthenticatedUser,
    AuthenticationMiddleware,
    required_roles,
    roles_from_claims,
)


def _user(role: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        subject=f"user-{role}",
        username=role,
        email=f"{role}@example.test",
        roles=frozenset({role}),
        claims={},
    )


class FakeAuthenticator:
    def authenticate(self, authorization: str) -> AuthenticatedUser:
        token = authorization.removeprefix("Bearer ")
        if token not in {"viewer", "curator", "reviewer", "admin"}:
            raise ValueError("bad token")
        return _user(token)


def _client() -> TestClient:
    app = FastAPI()
    settings = RuntimeSettings(
        auth_mode="oidc",
        oidc_issuer_url="https://auth.example.test/realms/geochem",
    )
    app.add_middleware(
        AuthenticationMiddleware,
        settings=settings,
        authenticator=FakeAuthenticator(),
    )

    @app.get("/api/v1/articles")
    def articles():
        return {"ok": True}

    @app.post("/api/v1/articles/ART_001/discover")
    def discover():
        return {"ok": True}

    @app.post("/api/v1/articles/ART_001/finalize")
    def finalize():
        return {"ok": True}

    @app.get("/api/v1/settings")
    def settings():
        return {"ok": True}

    @app.get("/api/v1/auth/me")
    def authenticated_identity():
        return {"user_id": current_user_id()}

    return TestClient(app)


def test_roles_from_keycloak_realm_and_client_claims():
    roles = roles_from_claims(
        {
            "realm_access": {"roles": ["geochem-viewer", "offline_access"]},
            "resource_access": {"geochem-web": {"roles": ["curator"]}},
        },
        "geochem-web",
    )

    assert roles == frozenset({"viewer", "curator"})


def test_role_policy_separates_read_write_review_and_admin():
    assert "viewer" in required_roles("GET", "/api/v1/articles")
    assert required_roles("POST", "/api/v1/articles/ART_001/discover") == frozenset({"admin", "curator"})
    assert required_roles("POST", "/api/v1/articles/ART_001/finalize") == frozenset({"admin", "reviewer"})
    assert required_roles("POST", "/api/v1/articles/ART_001/export") == frozenset({"admin", "reviewer"})
    assert required_roles("PATCH", "/api/v1/candidate-cells/CELL_001") == frozenset(
        {"admin", "curator", "reviewer"}
    )
    assert required_roles("POST", "/api/v1/candidate-records/manual") == frozenset(
        {"admin", "curator", "reviewer"}
    )
    assert required_roles("POST", "/api/v1/articles/ART_001/batch-confirm") == frozenset(
        {"admin", "curator", "reviewer"}
    )
    assert "viewer" in required_roles("GET", "/api/v1/export-jobs/EXP_0000001/download")
    assert "viewer" in required_roles("GET", "/api/v1/export-directory")
    assert required_roles("PUT", "/api/v1/export-directory") == frozenset({"admin"})
    assert required_roles("GET", "/api/v1/settings") == frozenset({"admin"})
    assert required_roles("GET", "/api/v1/admin/users") == frozenset({"admin"})
    assert required_roles("DELETE", "/api/v1/admin/users/USER_001") == frozenset({"admin"})


def test_oidc_middleware_requires_a_token():
    response = _client().get("/api/v1/articles")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_viewer_can_read_but_cannot_mutate():
    client = _client()

    assert client.get("/api/v1/articles", headers={"Authorization": "Bearer viewer"}).status_code == 200
    assert client.post(
        "/api/v1/articles/ART_001/discover",
        headers={"Authorization": "Bearer viewer"},
    ).status_code == 403


def test_curator_reviewer_and_admin_permissions():
    client = _client()

    assert client.post(
        "/api/v1/articles/ART_001/discover",
        headers={"Authorization": "Bearer curator"},
    ).status_code == 200
    assert client.post(
        "/api/v1/articles/ART_001/finalize",
        headers={"Authorization": "Bearer reviewer"},
    ).status_code == 200
    assert client.get(
        "/api/v1/settings",
        headers={"Authorization": "Bearer admin"},
    ).status_code == 200


def test_oidc_identity_is_available_inside_request_execution_context():
    response = _client().get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer curator"},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == "user-curator"
