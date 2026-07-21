"""Minimal Keycloak Admin API adapter used by the GeoChem administrator UI."""

from __future__ import annotations

from threading import Lock
import time
from typing import Any

import httpx

from ..core.runtime import RuntimeSettings
from .auth import GEOCHEM_ROLES


class KeycloakAdminError(RuntimeError):
    """Raised when the identity service rejects an administration request."""


class KeycloakAdminClient:
    """Manage realm users through a least-privilege service account."""

    def __init__(self, settings: RuntimeSettings, client: httpx.Client | None = None):
        self.settings = settings
        self.base_url = settings.keycloak_internal_url.rstrip("/")
        self.realm = settings.keycloak_realm
        self.client_id = settings.keycloak_admin_client_id
        self.client_secret = settings.keycloak_admin_client_secret
        self.client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
        self._owns_client = client is None
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = Lock()

    @property
    def available(self) -> bool:
        return bool(
            self.settings.auth_mode == "oidc"
            and self.base_url
            and self.realm
            and self.client_id
            and self.client_secret
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _access_token(self, *, force: bool = False) -> str:
        if not self.available:
            raise KeycloakAdminError("Keycloak 用户管理服务尚未配置。")
        with self._token_lock:
            now = time.monotonic()
            if not force and self._token and now < self._token_expires_at:
                return self._token
            response = self.client.post(
                f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Accept": "application/json"},
            )
            self._raise_for_status(response, "获取 Keycloak 管理令牌失败")
            payload = response.json()
            token = str(payload.get("access_token") or "")
            if not token:
                raise KeycloakAdminError("Keycloak 没有返回管理 access token。")
            self._token = token
            self._token_expires_at = now + max(5, int(payload.get("expires_in") or 60) - 15)
            return token

    @staticmethod
    def _raise_for_status(response: httpx.Response, prefix: str) -> None:
        if response.is_success:
            return
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("errorMessage") or body.get("error_description") or body.get("error") or "")
        except Exception:
            detail = response.text.strip()[:300]
        suffix = f"：{detail}" if detail else ""
        raise KeycloakAdminError(f"{prefix}（HTTP {response.status_code}）{suffix}")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}/admin/realms/{self.realm}{path}"
        for attempt in range(2):
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {self._access_token(force=attempt > 0)}"
            headers.setdefault("Accept", "application/json")
            response = self.client.request(method, url, headers=headers, **kwargs)
            if response.status_code != 401 or attempt:
                self._raise_for_status(response, "Keycloak 用户管理请求失败")
                return response
            self._token = ""
        raise KeycloakAdminError("Keycloak 用户管理认证失败。")

    def _user_roles(self, user_id: str) -> list[str]:
        response = self._request("GET", f"/users/{user_id}/role-mappings/realm")
        return sorted(
            str(role.get("name"))
            for role in response.json()
            if str(role.get("name") or "") in GEOCHEM_ROLES
        )

    def list_users(self, search: str = "", first: int = 0, limit: int = 100) -> dict[str, Any]:
        params: dict[str, Any] = {"first": max(0, first), "max": min(max(1, limit), 200)}
        if search.strip():
            params["search"] = search.strip()
        response = self._request("GET", "/users", params=params)
        users = []
        for row in response.json():
            user_id = str(row.get("id") or "")
            users.append({
                "id": user_id,
                "username": str(row.get("username") or ""),
                "email": str(row.get("email") or ""),
                "first_name": str(row.get("firstName") or ""),
                "last_name": str(row.get("lastName") or ""),
                "enabled": bool(row.get("enabled", True)),
                "email_verified": bool(row.get("emailVerified", False)),
                "created_at": int(row.get("createdTimestamp") or 0),
                "roles": self._user_roles(user_id) if user_id else [],
            })
        count_params = {"search": search.strip()} if search.strip() else None
        count = int(self._request("GET", "/users/count", params=count_params).json())
        return {"users": users, "total": count, "first": first, "limit": limit}

    def create_user(
        self,
        *,
        username: str,
        email: str = "",
        first_name: str = "",
        last_name: str = "",
        enabled: bool = True,
        roles: list[str] | None = None,
        password: str = "",
        temporary_password: bool = True,
    ) -> dict[str, Any]:
        response = self._request("POST", "/users", json={
            "username": username,
            "email": email or None,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": enabled,
            "emailVerified": False,
        })
        user_id = response.headers.get("Location", "").rstrip("/").rsplit("/", 1)[-1]
        if not user_id:
            matches = self.list_users(username, 0, 20)["users"]
            exact = [item for item in matches if item["username"].lower() == username.lower()]
            user_id = str(exact[0]["id"]) if exact else ""
        if not user_id:
            raise KeycloakAdminError("用户已创建，但无法取得 Keycloak 用户 ID。")
        self.replace_roles(user_id, roles or ["viewer"])
        if password:
            self.reset_password(user_id, password, temporary_password)
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict[str, Any]:
        row = self._request("GET", f"/users/{user_id}").json()
        return {
            "id": str(row.get("id") or user_id),
            "username": str(row.get("username") or ""),
            "email": str(row.get("email") or ""),
            "first_name": str(row.get("firstName") or ""),
            "last_name": str(row.get("lastName") or ""),
            "enabled": bool(row.get("enabled", True)),
            "email_verified": bool(row.get("emailVerified", False)),
            "created_at": int(row.get("createdTimestamp") or 0),
            "roles": self._user_roles(user_id),
        }

    def update_user(self, user_id: str, values: dict[str, Any]) -> dict[str, Any]:
        current = self._request("GET", f"/users/{user_id}").json()
        mapping = {
            "username": "username",
            "email": "email",
            "first_name": "firstName",
            "last_name": "lastName",
            "enabled": "enabled",
        }
        for source, target in mapping.items():
            if source in values and values[source] is not None:
                current[target] = values[source]
        self._request("PUT", f"/users/{user_id}", json=current)
        return self.get_user(user_id)

    def replace_roles(self, user_id: str, roles: list[str]) -> dict[str, Any]:
        requested = {str(role).strip().lower() for role in roles}
        invalid = requested.difference(GEOCHEM_ROLES)
        if invalid:
            raise KeycloakAdminError(f"未知 GeoChem 角色：{', '.join(sorted(invalid))}")
        if not requested:
            requested = {"viewer"}
        all_roles = self._request("GET", "/roles").json()
        role_by_name = {str(role.get("name")): role for role in all_roles}
        missing = requested.difference(role_by_name)
        if missing:
            raise KeycloakAdminError(f"Keycloak realm 缺少角色：{', '.join(sorted(missing))}")
        current = self._request("GET", f"/users/{user_id}/role-mappings/realm").json()
        removable = [role for role in current if str(role.get("name") or "") in GEOCHEM_ROLES]
        if removable:
            self._request("DELETE", f"/users/{user_id}/role-mappings/realm", json=removable)
        self._request(
            "POST",
            f"/users/{user_id}/role-mappings/realm",
            json=[role_by_name[name] for name in sorted(requested)],
        )
        return self.get_user(user_id)

    def reset_password(self, user_id: str, password: str, temporary: bool = True) -> None:
        if len(password) < 10:
            raise KeycloakAdminError("临时密码至少需要 10 个字符。")
        self._request("PUT", f"/users/{user_id}/reset-password", json={
            "type": "password",
            "value": password,
            "temporary": temporary,
        })

    def logout_user(self, user_id: str) -> None:
        self._request("POST", f"/users/{user_id}/logout")

    def delete_user(self, user_id: str) -> None:
        self._request("DELETE", f"/users/{user_id}")
