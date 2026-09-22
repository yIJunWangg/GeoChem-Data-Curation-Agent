"""Organization membership and workspace-level authorization."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Literal
from uuid import uuid4

from ..core.project import DEFAULT_WORKSPACE_ID, ProjectManager


DEFAULT_ORGANIZATION_ID = "ORG_DEFAULT"
DEFAULT_ORGANIZATION_NAME = "GeoChem 工作室"
WORKSPACE_ROLES = frozenset({"owner", "curator", "reviewer", "viewer"})
WorkspaceAction = Literal["read", "curate", "review", "manage"]

_ACTION_ROLES: dict[WorkspaceAction, frozenset[str]] = {
    "read": WORKSPACE_ROLES,
    "curate": frozenset({"owner", "curator"}),
    "review": frozenset({"owner", "reviewer"}),
    "manage": frozenset({"owner"}),
}

_PATH_OBJECT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/articles/([^/]+)"), "article"),
    (re.compile(r"/resources/([^/]+)"), "resource"),
    (re.compile(r"/elements/([^/]+)"), "element"),
    (re.compile(r"/extraction-batches/([^/]+)"), "batch"),
    (re.compile(r"/candidate-records/([^/]+)"), "candidate_record"),
    (re.compile(r"/candidate-cells/([^/]+)"), "candidate_cell"),
    (re.compile(r"/trace-records/([^/]+)"), "standardized_record"),
    (re.compile(r"/export-jobs/([^/]+)"), "export_job"),
    (re.compile(r"/tasks/([^/]+)"), "workflow_task"),
    (re.compile(r"/(?:rules|rule-memory)/([^/]+)"), "rule"),
    (re.compile(r"/rule-submissions/([^/]+)"), "rule_submission"),
    (re.compile(r"/rule-imports/([^/]+)"), "rule_import"),
)

_PAYLOAD_OBJECT_KEYS: dict[str, str] = {
    "article_id": "article",
    "resource_id": "resource",
    "element_id": "element",
    "batch_id": "batch",
    "candidate_record_id": "candidate_record",
    "cell_id": "candidate_cell",
    "config_id": "header_config",
    "task_id": "workflow_task",
    "job_id": "export_job",
    "rule_id": "rule",
}

_ROUTE_ACTION_SEGMENTS = frozenset({"manual", "manual-image", "merge", "hit-test"})

_OBJECT_QUERIES: dict[str, str] = {
    "article": "SELECT 1 FROM articles WHERE article_id=? AND project_id=?",
    "resource": """SELECT 1 FROM resources r JOIN articles a ON a.article_id=r.article_id
                    WHERE r.resource_id=? AND a.project_id=?""",
    "element": "SELECT 1 FROM document_elements WHERE element_id=? AND project_id=?",
    "batch": "SELECT 1 FROM extraction_batches WHERE batch_id=? AND project_id=?",
    "candidate_record": """SELECT 1 FROM candidate_records c
                              JOIN extraction_batches b ON b.batch_id=c.batch_id
                              WHERE c.candidate_record_id=? AND b.project_id=?""",
    "candidate_cell": """SELECT 1 FROM candidate_cells cc
                            JOIN candidate_records c ON c.candidate_record_id=cc.candidate_record_id
                            JOIN extraction_batches b ON b.batch_id=c.batch_id
                            WHERE cc.cell_id=? AND b.project_id=?""",
    "standardized_record": """SELECT 1 FROM standardized_records s
                                 JOIN articles a ON a.article_id=s.article_id
                                 WHERE s.record_id=? AND a.project_id=?""",
    "export_job": "SELECT 1 FROM export_jobs WHERE job_id=? AND project_id=?",
    "workflow_task": "SELECT 1 FROM workflow_tasks WHERE task_id=? AND project_id=?",
    "rule": "SELECT 1 FROM learned_extraction_rules WHERE rule_id=? AND project_id=?",
    "rule_submission": "SELECT 1 FROM mapping_rule_submissions WHERE submission_id=? AND project_id=?",
    "rule_import": "SELECT 1 FROM mapping_rule_imports WHERE import_id=? AND project_id=?",
    "header_config": "SELECT 1 FROM header_configs WHERE config_id=? AND project_id=?",
}

_ORGANIZATION_OBJECT_QUERIES: dict[str, str] = {
    "rule": """SELECT 1 FROM learned_extraction_rules r
                 WHERE r.rule_id=? AND (
                   r.project_id=? OR (
                     r.scope='organization' AND r.organization_id=(
                       SELECT organization_id FROM projects WHERE project_id=?
                     )
                   )
                 )""",
    "rule_submission": """SELECT 1 FROM mapping_rule_submissions s
                            WHERE s.submission_id=? AND (
                              s.project_id=? OR s.organization_id=(
                                SELECT organization_id FROM projects WHERE project_id=?
                              )
                            )""",
    "rule_import": """SELECT 1 FROM mapping_rule_imports i
                        WHERE i.import_id=? AND (
                          i.project_id=? OR i.organization_id=(
                            SELECT organization_id FROM projects WHERE project_id=?
                          )
                        )""",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if value is not None else {}


class WorkspaceAccessService:
    """Resolve platform identity into organization-scoped business permissions."""

    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager

    @staticmethod
    def _is_platform_admin(platform_roles: Iterable[str]) -> bool:
        return "admin" in {str(role).strip().lower() for role in platform_roles}

    @staticmethod
    def _default_workspace_role(platform_roles: Iterable[str]) -> str:
        roles = {str(role).strip().lower() for role in platform_roles}
        if "admin" in roles:
            return "owner"
        if "curator" in roles:
            return "curator"
        if "reviewer" in roles:
            return "reviewer"
        return "viewer"

    def bootstrap_default_organization(self) -> None:
        """Attach legacy data to a default organization without changing IDs."""

        self.project_manager.ensure_default_workspace()
        db = self.project_manager.get_default_database()
        now = _now()
        try:
            db.execute(
                """INSERT INTO organizations
                   (organization_id, name, slug, status, created_by, created_at, updated_at)
                   VALUES (?, ?, 'geochem-default', 'active', 'system', ?, ?)
                   ON CONFLICT(organization_id) DO UPDATE SET
                     name = excluded.name,
                     updated_at = excluded.updated_at""",
                (DEFAULT_ORGANIZATION_ID, DEFAULT_ORGANIZATION_NAME, now, now),
            )
            db.execute(
                """UPDATE projects SET organization_id = ?
                   WHERE project_id = ? AND (organization_id IS NULL OR organization_id = '')""",
                (DEFAULT_ORGANIZATION_ID, DEFAULT_WORKSPACE_ID),
            )
            # One-time compatibility migration for accounts that existed before
            # workspace membership was introduced. New accounts are never added
            # here once at least one membership exists; administrators assign
            # them explicitly from the management console.
            membership_count = db.fetch_one(
                "SELECT COUNT(*) AS count FROM organization_members"
            )
            if int(_row(membership_count).get("count") or 0) == 0:
                for profile in db.fetch_all("SELECT user_id FROM user_profiles ORDER BY created_at"):
                    legacy_user_id = str(_row(profile).get("user_id") or "").strip()
                    if not legacy_user_id:
                        continue
                    db.execute(
                        """INSERT INTO organization_members
                           (membership_id, organization_id, user_id, role, status,
                            invited_by, created_at, updated_at)
                           VALUES (?, ?, ?, 'viewer', 'active', 'system-migration', ?, ?)
                           ON CONFLICT(organization_id, user_id) DO NOTHING""",
                        (
                            f"MEM_{uuid4().hex.upper()}",
                            DEFAULT_ORGANIZATION_ID,
                            legacy_user_id,
                            now,
                            now,
                        ),
                    )
            db.commit()
        finally:
            db.close()

    def provision_default_membership(
        self,
        user_id: str,
        platform_roles: Iterable[str],
        *,
        invited_by: str = "system",
    ) -> str:
        """Provision a Keycloak-assigned account into the legacy shared workspace.

        Keycloak roles are assigned by an administrator, so this compatibility
        bootstrap does not create a public self-registration path.
        """

        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise PermissionError("无法识别当前账号。")
        self.bootstrap_default_organization()
        role = self._default_workspace_role(platform_roles)
        db = self.project_manager.get_default_database()
        now = _now()
        try:
            existing = db.fetch_one(
                """SELECT role, status FROM organization_members
                   WHERE organization_id = ? AND user_id = ?""",
                (DEFAULT_ORGANIZATION_ID, normalized_user),
            )
            if existing:
                value = _row(existing)
                if value.get("status") != "active":
                    raise PermissionError("当前账号的工作区成员资格已停用。")
                return str(value.get("role") or role)
            db.execute(
                """INSERT INTO organization_members
                   (membership_id, organization_id, user_id, role, status, invited_by,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    f"MEM_{uuid4().hex.upper()}",
                    DEFAULT_ORGANIZATION_ID,
                    normalized_user,
                    role,
                    invited_by,
                    now,
                    now,
                ),
            )
            db.commit()
            return role
        finally:
            db.close()

    def workspace_role(
        self,
        project_id: str,
        user_id: str,
        platform_roles: Iterable[str] = (),
    ) -> str:
        normalized_project = str(project_id or DEFAULT_WORKSPACE_ID).strip()
        normalized_user = str(user_id or "").strip()
        if self._is_platform_admin(platform_roles):
            return "owner"
        db = self.project_manager.get_default_database()
        try:
            row = db.fetch_one(
                """SELECT om.role, om.status
                   FROM projects p
                   JOIN organization_members om
                     ON om.organization_id = p.organization_id
                   WHERE p.project_id = ? AND om.user_id = ?""",
                (normalized_project, normalized_user),
            )
            value = _row(row)
            if value.get("status") != "active":
                return ""
            role = str(value.get("role") or "")
            return role if role in WORKSPACE_ROLES else ""
        finally:
            db.close()

    def require(
        self,
        project_id: str,
        user_id: str,
        platform_roles: Iterable[str],
        action: WorkspaceAction = "read",
    ) -> str:
        """Require membership and return the effective workspace role."""

        normalized_project = str(project_id or DEFAULT_WORKSPACE_ID).strip()
        normalized_user = str(user_id or "").strip()
        roles = frozenset(str(role).strip().lower() for role in platform_roles)
        role = self.workspace_role(normalized_project, normalized_user, roles)
        if not role:
            raise PermissionError("当前账号不是该工作区的有效成员。")
        if role not in _ACTION_ROLES[action]:
            raise PermissionError("当前工作区角色没有执行此操作的权限。")
        return role

    def require_request_objects(
        self,
        project_id: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Reject known object IDs that do not belong to the authorized workspace."""

        requested: set[tuple[str, str]] = set()
        for pattern, object_type in _PATH_OBJECT_PATTERNS:
            for match in pattern.finditer(path):
                object_id = match.group(1).strip()
                if (
                    object_id
                    and not object_id.startswith("{")
                    and object_id not in _ROUTE_ACTION_SEGMENTS
                ):
                    requested.add((object_type, object_id))
        for key, object_type in _PAYLOAD_OBJECT_KEYS.items():
            value = str((payload or {}).get(key) or "").strip()
            if value:
                requested.add((object_type, value))
        if not requested:
            return
        db = self.project_manager.get_database(project_id)
        try:
            for object_type, object_id in requested:
                query = _ORGANIZATION_OBJECT_QUERIES.get(object_type)
                params: tuple[Any, ...]
                if query:
                    params = (object_id, project_id, project_id)
                else:
                    query = _OBJECT_QUERIES[object_type]
                    params = (object_id, project_id)
                if not db.fetch_one(query, params):
                    raise PermissionError("请求的对象不存在或不属于当前工作区。")
        finally:
            db.close()

    def list_workspaces(
        self,
        user_id: str,
        platform_roles: Iterable[str],
    ) -> list[dict[str, Any]]:
        # Legacy memberships are migrated once during application startup.
        # Running that migration during every list call could accidentally add
        # the first newly authenticated account in an invite-only deployment.
        db = self.project_manager.get_default_database()
        try:
            if self._is_platform_admin(platform_roles):
                rows = db.fetch_all(
                    """SELECT p.project_id, p.project_name, p.description, p.organization_id,
                              o.name AS organization_name, 'owner' AS workspace_role
                       FROM projects p
                       JOIN organizations o ON o.organization_id = p.organization_id
                       WHERE o.status = 'active'
                       ORDER BY p.project_name"""
                )
            else:
                rows = db.fetch_all(
                    """SELECT p.project_id, p.project_name, p.description, p.organization_id,
                              o.name AS organization_name, om.role AS workspace_role
                       FROM projects p
                       JOIN organizations o ON o.organization_id = p.organization_id
                       JOIN organization_members om ON om.organization_id = p.organization_id
                       WHERE om.user_id = ? AND om.status = 'active' AND o.status = 'active'
                       ORDER BY p.project_name""",
                    (user_id,),
                )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def list_members(self, project_id: str) -> list[dict[str, Any]]:
        db = self.project_manager.get_default_database()
        try:
            rows = db.fetch_all(
                """SELECT om.membership_id, om.user_id, om.role, om.status, om.invited_by,
                          om.created_at, om.updated_at,
                          COALESCE(up.username, '') AS username,
                          COALESCE(up.display_name, '') AS display_name,
                          COALESCE(up.email, '') AS email
                   FROM projects p
                   JOIN organization_members om ON om.organization_id = p.organization_id
                   LEFT JOIN user_profiles up ON up.user_id = om.user_id
                   WHERE p.project_id = ?
                   ORDER BY CASE om.role WHEN 'owner' THEN 0 WHEN 'curator' THEN 1
                                WHEN 'reviewer' THEN 2 ELSE 3 END,
                            COALESCE(up.username, om.user_id)""",
                (project_id,),
            )
            return [dict(row) for row in rows]
        finally:
            db.close()

    def upsert_member(
        self,
        project_id: str,
        user_id: str,
        role: str,
        actor_id: str,
        *,
        status: str = "active",
    ) -> dict[str, Any]:
        normalized_role = str(role or "").strip().lower()
        normalized_status = str(status or "active").strip().lower()
        if normalized_role not in WORKSPACE_ROLES:
            raise ValueError("工作区角色必须是 owner、curator、reviewer 或 viewer。")
        if normalized_status not in {"active", "disabled"}:
            raise ValueError("成员状态必须是 active 或 disabled。")
        db = self.project_manager.get_default_database()
        now = _now()
        try:
            project = db.fetch_one(
                "SELECT organization_id FROM projects WHERE project_id = ?",
                (project_id,),
            )
            if not project:
                raise ValueError("工作区不存在。")
            organization_id = str(_row(project).get("organization_id") or "")
            db.execute(
                """INSERT INTO organization_members
                   (membership_id, organization_id, user_id, role, status, invited_by,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(organization_id, user_id) DO UPDATE SET
                     role = excluded.role,
                     status = excluded.status,
                     invited_by = excluded.invited_by,
                     updated_at = excluded.updated_at""",
                (
                    f"MEM_{uuid4().hex.upper()}",
                    organization_id,
                    str(user_id).strip(),
                    normalized_role,
                    normalized_status,
                    actor_id,
                    now,
                    now,
                ),
            )
            db.commit()
        finally:
            db.close()
        return next(
            member for member in self.list_members(project_id)
            if member.get("user_id") == str(user_id).strip()
        )

    def remove_member(self, project_id: str, user_id: str) -> None:
        db = self.project_manager.get_default_database()
        try:
            project = db.fetch_one(
                "SELECT organization_id FROM projects WHERE project_id = ?",
                (project_id,),
            )
            if not project:
                raise ValueError("工作区不存在。")
            organization_id = str(_row(project).get("organization_id") or "")
            owners = db.fetch_one(
                """SELECT COUNT(*) AS count FROM organization_members
                   WHERE organization_id = ? AND role = 'owner' AND status = 'active'""",
                (organization_id,),
            )
            target = db.fetch_one(
                """SELECT role, status FROM organization_members
                   WHERE organization_id = ? AND user_id = ?""",
                (organization_id, user_id),
            )
            if not target:
                raise ValueError("工作区成员不存在。")
            if (
                _row(target).get("role") == "owner"
                and _row(target).get("status") == "active"
                and int(_row(owners).get("count") or 0) <= 1
            ):
                raise ValueError("不能移除工作区最后一位有效 Owner。")
            db.execute(
                "DELETE FROM organization_members WHERE organization_id = ? AND user_id = ?",
                (organization_id, user_id),
            )
            db.commit()
        finally:
            db.close()
