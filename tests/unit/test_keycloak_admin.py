from __future__ import annotations

import httpx

from geochem.core.runtime import RuntimeSettings
from geochem.web.keycloak_admin import KeycloakAdminClient


def _client():
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "admin-token", "expires_in": 60})
        if request.url.path.endswith("/users/count"):
            return httpx.Response(200, json=1)
        if request.url.path.endswith("/users/USER_1/role-mappings/realm"):
            return httpx.Response(200, json=[
                {"id": "ROLE_1", "name": "curator"},
                {"id": "ROLE_OFFLINE", "name": "offline_access"},
            ])
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json=[{
                "id": "USER_1",
                "username": "curator.one",
                "email": "curator@example.test",
                "firstName": "Curator",
                "lastName": "One",
                "enabled": True,
                "emailVerified": True,
                "createdTimestamp": 123456,
            }])
        return httpx.Response(404, json={"errorMessage": "unexpected test request"})

    settings = RuntimeSettings(
        auth_mode="oidc",
        keycloak_internal_url="http://keycloak:8080",
        keycloak_realm="geochem",
        keycloak_admin_client_id="geochem-admin-api",
        keycloak_admin_client_secret="test-secret",
    )
    transport = httpx.MockTransport(handler)
    return KeycloakAdminClient(settings, httpx.Client(transport=transport)), requests


def test_keycloak_admin_lists_users_with_only_geochem_roles():
    client, requests = _client()

    result = client.list_users("curator")

    assert result["total"] == 1
    assert result["users"] == [{
        "id": "USER_1",
        "username": "curator.one",
        "email": "curator@example.test",
        "first_name": "Curator",
        "last_name": "One",
        "enabled": True,
        "email_verified": True,
        "created_at": 123456,
        "roles": ["curator"],
    }]
    assert requests.count(("POST", "/realms/geochem/protocol/openid-connect/token")) == 1


def test_keycloak_admin_is_unavailable_without_service_account_secret():
    settings = RuntimeSettings(
        auth_mode="oidc",
        keycloak_internal_url="http://keycloak:8080",
        keycloak_realm="geochem",
        keycloak_admin_client_id="geochem-admin-api",
        keycloak_admin_client_secret="",
    )

    assert KeycloakAdminClient(settings).available is False
