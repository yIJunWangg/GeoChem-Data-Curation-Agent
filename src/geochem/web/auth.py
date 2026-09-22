"""OIDC authentication, role authorization, and API audit logging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable
from uuid import uuid4

import jwt
from fastapi.responses import JSONResponse
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..core.project import DEFAULT_WORKSPACE_ID, ProjectManager
from ..core.runtime import RuntimeSettings
from ..services.execution_context import user_execution_context
from ..services.workspace_access import WorkspaceAccessService
from .rate_limit import ApiRateLimiter, RateLimitDecision


GEOCHEM_ROLES = frozenset({"admin", "curator", "reviewer", "viewer"})
PUBLIC_PATHS = frozenset({
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/auth/config",
    "/docs",
    "/openapi.json",
    "/redoc",
})
ADMIN_PREFIXES = (
    "/api/v1/admin",
    "/api/v1/settings",
    "/api/v1/model-setup",
    "/api/v1/provider-presets",
    "/api/v1/providers",
    "/api/v1/costs",
    "/api/v1/export-directory",
)
WORKSPACE_EXEMPT_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/admin/",
    "/api/v1/settings",
    "/api/v1/model-setup",
    "/api/v1/provider-presets",
    "/api/v1/providers",
    "/api/v1/costs",
    "/api/v1/export-directory",
)


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str
    username: str
    email: str
    roles: frozenset[str]
    claims: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "username": self.username,
            "email": self.email,
            "roles": sorted(self.roles),
        }


def _normalized_role(value: Any) -> str:
    role = str(value or "").strip().lower().replace("_", "-")
    if role.startswith("geochem-"):
        role = role.removeprefix("geochem-")
    return role


def roles_from_claims(claims: dict[str, Any], client_id: str) -> frozenset[str]:
    """Read GeoChem roles from Keycloak realm and client role claims."""

    values: set[str] = set()
    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        values.update(str(role) for role in realm_access.get("roles", []) if role)
    resource_access = claims.get("resource_access")
    if isinstance(resource_access, dict):
        client_access = resource_access.get(client_id)
        if isinstance(client_access, dict):
            values.update(str(role) for role in client_access.get("roles", []) if role)
    normalized = {_normalized_role(role) for role in values}
    return frozenset(normalized.intersection(GEOCHEM_ROLES))


def required_roles(method: str, path: str) -> frozenset[str]:
    """Return the minimum accepted role set for one HTTP operation."""

    if path in PUBLIC_PATHS or method.upper() == "OPTIONS" or not path.startswith("/api/"):
        return frozenset()
    if path == "/api/v1/export-directory" and method.upper() == "GET":
        return GEOCHEM_ROLES
    # Rule governance is organization-scoped. Workspace membership decides
    # whether a member may submit or approve, while Keycloak only establishes
    # their platform identity.
    if path.startswith("/api/v1/admin/rule-submissions"):
        return GEOCHEM_ROLES
    if path.startswith(ADMIN_PREFIXES):
        return frozenset({"admin"})

    upper_method = method.upper()
    review_write = (
        ("/candidate-records/" in path and path.endswith(("/approve", "/reject")))
        or "/batch-approve" in path
        or path.endswith("/finalize")
        or (path.endswith("/export") and upper_method in {"GET", "POST"})
    )
    if review_write:
        return frozenset({"admin", "reviewer"})

    # Candidate construction and mapping are curation work. Reviewers may fix
    # a value while inspecting it, but viewers must remain read-only. Formal
    # approval/finalization is kept in the stricter branch above.
    candidate_write = (
        ("/candidate-cells/" in path and upper_method in {"PATCH", "POST", "DELETE"})
        or ("/candidate-records/" in path and upper_method in {"POST", "DELETE"})
        or "/batch-confirm" in path
    )
    if candidate_write:
        return frozenset({"admin", "curator", "reviewer"})
    if upper_method in {"GET", "HEAD"}:
        return GEOCHEM_ROLES
    return frozenset({"admin", "curator"})


def required_workspace_action(method: str, path: str) -> str:
    """Map an already role-authorized request to a workspace permission."""

    upper_method = method.upper()
    if path.startswith("/api/v1/admin/rule-submissions"):
        return "manage"
    if path.startswith(("/api/v1/rule-submissions", "/api/v1/rule-imports")):
        # The governance service performs the finer submitter-role check so
        # reviewers can propose rules while viewers remain read-only.
        return "read"
    if upper_method in {"GET", "HEAD"}:
        return "read"
    if (
        ("/candidate-records/" in path and path.endswith(("/approve", "/reject")))
        or "/reviews" in path
        or "/batch-approve" in path
        or path.endswith("/finalize")
        or path.endswith("/export")
    ):
        return "review"
    return "curate"


def _workspace_uses_membership_policy(path: str) -> bool:
    if path.startswith("/api/v1/admin/rule-submissions"):
        return True
    return (
        path.startswith("/api/v1/")
        and path not in PUBLIC_PATHS
        and not path.startswith(WORKSPACE_EXEMPT_PREFIXES)
    )


class OIDCAuthenticator:
    """Validate Keycloak access tokens with issuer, audience, and JWKS."""

    def __init__(self, settings: RuntimeSettings):
        self.settings = settings
        jwks_url = settings.oidc_jwks_url or (
            settings.oidc_issuer_url.rstrip("/") + "/protocol/openid-connect/certs"
        )
        self.jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300)

    def authenticate(self, authorization: str) -> AuthenticatedUser:
        if not authorization.lower().startswith("bearer "):
            raise ValueError("缺少有效的 Bearer access token。")
        token = authorization.split(" ", 1)[1].strip()
        if not token:
            raise ValueError("缺少有效的 Bearer access token。")
        signing_key = self.jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "RS384", "RS512", "PS256"],
            audience=self.settings.oidc_audience,
            issuer=self.settings.oidc_issuer_url.rstrip("/"),
            options={"require": ["exp", "iat", "sub"]},
            leeway=30,
        )
        roles = roles_from_claims(claims, self.settings.oidc_client_id)
        if not roles:
            raise PermissionError("账号尚未分配 GeoChem 角色。")
        return AuthenticatedUser(
            subject=str(claims.get("sub") or ""),
            username=str(
                claims.get("preferred_username")
                or claims.get("name")
                or claims.get("email")
                or claims.get("sub")
                or ""
            ),
            email=str(claims.get("email") or ""),
            roles=roles,
            claims=dict(claims),
        )


class ApiAuditLogger:
    """Write mutation and denied-request audit events to the shared workspace."""

    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager

    def __call__(
        self,
        request: Request,
        request_id: str,
        status_code: int,
        user: AuthenticatedUser | None,
    ) -> None:
        login_verified = (
            request.method.upper() == "GET"
            and request.url.path == "/api/v1/auth/me"
            and status_code < 400
        )
        admin_read = (
            request.method.upper() == "GET"
            and request.url.path.startswith("/api/v1/admin/")
            and status_code < 400
        )
        if (
            request.method.upper() in {"GET", "HEAD", "OPTIONS"}
            and status_code < 400
            and not login_verified
            and not admin_read
        ):
            return
        project_id = request.query_params.get("project_id") or DEFAULT_WORKSPACE_ID
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        client_ip = forwarded or (request.client.host if request.client else "")
        db = self.project_manager.get_default_database()
        try:
            db.execute(
                """INSERT INTO api_audit_events
                   (audit_id, project_id, user_id, username, roles_json, request_id,
                    method, path, status_code, client_ip, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"AUD_{uuid4().hex.upper()}",
                    project_id,
                    user.subject if user else "",
                    user.username if user else "",
                    json.dumps(sorted(user.roles) if user else [], ensure_ascii=False),
                    request_id,
                    request.method.upper(),
                    request.url.path,
                    status_code,
                    client_ip,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Protect API routes while leaving the local development profile frictionless."""

    def __init__(
        self,
        app,
        *,
        settings: RuntimeSettings,
        authenticator: OIDCAuthenticator | None = None,
        audit_logger: Callable[[Request, str, int, AuthenticatedUser | None], None] | None = None,
        workspace_access: WorkspaceAccessService | None = None,
        rate_limiter: ApiRateLimiter | None = None,
    ):
        super().__init__(app)
        self.settings = settings
        self.authenticator = authenticator or (
            OIDCAuthenticator(settings) if settings.auth_mode == "oidc" else None
        )
        self.audit_logger = audit_logger
        self.workspace_access = workspace_access
        self.rate_limiter = rate_limiter

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid4().hex
        needed = required_roles(request.method, request.url.path)
        # Keycloak authenticates the person and identifies platform admins.
        # Workspace membership is the source of truth for business actions, so
        # the same person can be a curator in one workspace and a viewer in another.
        if self.workspace_access is not None and _workspace_uses_membership_policy(
            request.url.path
        ):
            needed = GEOCHEM_ROLES
        user: AuthenticatedUser | None = None
        rate_decision: RateLimitDecision | None = None
        json_payload: dict[str, Any] = {}
        if self.settings.auth_mode == "disabled":
            user = AuthenticatedUser(
                subject="local-development",
                username="Local Developer",
                email="",
                roles=GEOCHEM_ROLES,
                claims={},
            )
        elif needed:
            try:
                user = self.authenticator.authenticate(request.headers.get("authorization", ""))
            except PermissionError as exc:
                return self._error(request, request_id, 403, str(exc), None)
            except Exception:
                return self._error(
                    request,
                    request_id,
                    401,
                    "登录已失效或 access token 无法验证，请重新登录。",
                    None,
                )
            if not user.roles.intersection(needed):
                return self._error(
                    request,
                    request_id,
                    403,
                    "当前账号没有执行此操作所需的角色权限。",
                    user,
                )

        if (
            user is not None
            and self.rate_limiter is not None
            and request.url.path.startswith("/api/v1/")
            and request.url.path not in PUBLIC_PATHS
            and request.method.upper() != "OPTIONS"
        ):
            try:
                rate_decision = self.rate_limiter.check(
                    user.subject,
                    request.method,
                    request.url.path,
                )
            except RuntimeError as exc:
                return self._error(request, request_id, 503, str(exc), user)
            if not rate_decision.allowed:
                return self._error(
                    request,
                    request_id,
                    429,
                    "请求过于频繁，请稍后重试。",
                    user,
                    extra_headers={
                        "Retry-After": str(rate_decision.retry_after),
                        "X-RateLimit-Limit": str(rate_decision.limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Bucket": rate_decision.bucket,
                    },
                )

        if (
            user is not None
            and self.workspace_access is not None
            and _workspace_uses_membership_policy(request.url.path)
        ):
            project_id = request.query_params.get("project_id") or ""
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type == "application/json":
                try:
                    payload = json.loads((await request.body()).decode("utf-8") or "{}")
                    if isinstance(payload, dict):
                        json_payload = payload
                        if not project_id:
                            project_id = str(payload.get("project_id") or "")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            project_id = project_id or DEFAULT_WORKSPACE_ID
            try:
                request.state.workspace_role = self.workspace_access.require(
                    project_id,
                    user.subject,
                    user.roles,
                    required_workspace_action(request.method, request.url.path),
                )
                self.workspace_access.require_request_objects(
                    project_id,
                    request.url.path,
                    {
                        **{
                            key: value
                            for key, value in request.query_params.items()
                            if key != "project_id"
                        },
                        **json_payload,
                    },
                )
            except PermissionError as exc:
                return self._error(request, request_id, 403, str(exc), user)

        request.state.user = user
        request.state.request_id = request_id
        with user_execution_context(
            user.subject if user else "",
            user.roles if user else (),
        ):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if rate_decision is not None:
            response.headers["X-RateLimit-Limit"] = str(rate_decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(rate_decision.remaining)
            response.headers["X-RateLimit-Bucket"] = rate_decision.bucket
        if self.audit_logger:
            self.audit_logger(request, request_id, response.status_code, user)
        return response

    def _error(
        self,
        request: Request,
        request_id: str,
        status_code: int,
        detail: str,
        user: AuthenticatedUser | None,
        extra_headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        if self.audit_logger:
            self.audit_logger(request, request_id, status_code, user)
        headers = {"X-Request-ID": request_id}
        headers.update(extra_headers or {})
        if status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)
