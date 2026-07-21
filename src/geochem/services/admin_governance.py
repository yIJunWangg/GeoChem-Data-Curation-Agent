"""Administrative model-access, quota, usage, and audit governance."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..core.project import ProjectManager
from ..core.runtime import RuntimeSettings


DEFAULT_STORAGE_QUOTA_BYTES = 10 * 1024 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if value is not None else {}


class CredentialVault:
    """Encrypt provider secrets with an operator-managed AES-256-GCM key."""

    def __init__(self, encoded_key: str):
        self._key = self._decode_key(encoded_key) if encoded_key else b""

    @property
    def available(self) -> bool:
        return len(self._key) == 32

    @staticmethod
    def _decode_key(value: str) -> bytes:
        stripped = value.strip()
        if len(stripped) == 64:
            try:
                decoded = bytes.fromhex(stripped)
                if len(decoded) == 32:
                    return decoded
            except ValueError:
                pass
        try:
            decoded = base64.urlsafe_b64decode(stripped + "=" * (-len(stripped) % 4))
            if len(decoded) == 32:
                return decoded
        except ValueError:
            pass
        raise ValueError("GEOCHEM_CREDENTIAL_MASTER_KEY 必须是 32 字节的 base64 或 64 位十六进制密钥。")

    def encrypt(self, secret: str) -> tuple[str, str, str]:
        if not self.available:
            raise ValueError("服务器尚未配置凭据主密钥，不能安全保存 API Key。")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, secret.encode("utf-8"), b"geochem-model-credential-v1")
        fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]
        return (
            base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            base64.urlsafe_b64encode(nonce).decode("ascii"),
            fingerprint,
        )

    def decrypt(self, ciphertext: str, nonce: str) -> str:
        if not self.available:
            raise ValueError("服务器尚未配置凭据主密钥。")
        return AESGCM(self._key).decrypt(
            base64.urlsafe_b64decode(nonce),
            base64.urlsafe_b64decode(ciphertext),
            b"geochem-model-credential-v1",
        ).decode("utf-8")


class AdminGovernanceService:
    """Keep administrative policy data outside the identity provider."""

    def __init__(self, project_manager: ProjectManager, runtime: RuntimeSettings):
        self.project_manager = project_manager
        self.runtime = runtime
        self.vault = CredentialVault(runtime.credential_master_key)

    def ensure_user_profile(
        self,
        user_id: str,
        username: str = "",
        email: str = "",
        display_name: str = "",
    ) -> None:
        if not user_id:
            return
        db = self.project_manager.get_default_database()
        now = _now()
        try:
            db.execute(
                """INSERT INTO user_profiles
                   (user_id, username, display_name, email, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'active', ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     username = excluded.username,
                     display_name = excluded.display_name,
                     email = excluded.email,
                     updated_at = excluded.updated_at""",
                (user_id, username, display_name, email, now, now),
            )
            db.execute(
                """INSERT INTO user_storage_quotas
                   (user_id, quota_bytes, used_bytes, updated_by, updated_at)
                   VALUES (?, ?, 0, 'system', ?)
                   ON CONFLICT(user_id) DO NOTHING""",
                (user_id, DEFAULT_STORAGE_QUOTA_BYTES, now),
            )
            db.commit()
        finally:
            db.close()

    def overview(self) -> dict[str, Any]:
        db = self.project_manager.get_default_database()
        try:
            credential_count = db.fetch_one(
                "SELECT COUNT(*) AS count FROM model_credentials WHERE enabled = 1"
            )
            allocation_count = db.fetch_one(
                "SELECT COUNT(*) AS count FROM user_model_allocations WHERE enabled = 1"
            )
            quota = db.fetch_one(
                "SELECT COALESCE(SUM(quota_bytes), 0) AS quota, COALESCE(SUM(used_bytes), 0) AS used FROM user_storage_quotas"
            )
            running = db.fetch_one(
                "SELECT COUNT(*) AS count FROM workflow_tasks WHERE status IN ('pending', 'running')"
            )
            recent_failures = db.fetch_one(
                "SELECT COUNT(*) AS count FROM workflow_tasks WHERE status = 'failed'"
            )
            return {
                "credential_vault_ready": self.vault.available,
                "active_credentials": int(_row(credential_count).get("count") or 0),
                "active_allocations": int(_row(allocation_count).get("count") or 0),
                "storage_quota_bytes": int(_row(quota).get("quota") or 0),
                "storage_used_bytes": int(_row(quota).get("used") or 0),
                "running_tasks": int(_row(running).get("count") or 0),
                "failed_tasks": int(_row(recent_failures).get("count") or 0),
            }
        finally:
            db.close()

    @staticmethod
    def _public_credential(row: Any) -> dict[str, Any]:
        value = _row(row)
        return {
            "credential_id": value.get("credential_id", ""),
            "name": value.get("name", ""),
            "provider": value.get("provider", ""),
            "api_format": value.get("api_format", "openai"),
            "base_url": value.get("base_url", ""),
            "model_id": value.get("model_id", ""),
            "secret_ref": value.get("secret_ref", ""),
            "key_fingerprint": value.get("key_fingerprint", ""),
            "secret_configured": bool(value.get("encrypted_secret") or value.get("secret_ref")),
            "enabled": bool(value.get("enabled")),
            "created_by": value.get("created_by", ""),
            "created_at": value.get("created_at", ""),
            "updated_at": value.get("updated_at", ""),
        }

    def list_credentials(self) -> list[dict[str, Any]]:
        db = self.project_manager.get_default_database()
        try:
            rows = db.fetch_all("SELECT * FROM model_credentials ORDER BY enabled DESC, name ASC")
            return [self._public_credential(row) for row in rows]
        finally:
            db.close()

    def create_credential(self, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
        api_key = str(payload.pop("api_key", "") or "").strip()
        secret_ref = str(payload.get("secret_ref", "") or "").strip()
        if not api_key and not secret_ref:
            raise ValueError("请提供 API Key 或服务器环境变量引用。")
        encrypted_secret = secret_nonce = fingerprint = ""
        if api_key:
            encrypted_secret, secret_nonce, fingerprint = self.vault.encrypt(api_key)
        credential_id = f"CRED_{uuid4().hex.upper()}"
        now = _now()
        db = self.project_manager.get_default_database()
        try:
            db.execute(
                """INSERT INTO model_credentials
                   (credential_id, name, provider, api_format, base_url, model_id,
                    secret_ref, encrypted_secret, secret_nonce, key_fingerprint,
                    enabled, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    credential_id,
                    str(payload.get("name", "")).strip(),
                    str(payload.get("provider", "")).strip(),
                    str(payload.get("api_format", "openai")).strip(),
                    str(payload.get("base_url", "")).strip(),
                    str(payload.get("model_id", "")).strip(),
                    secret_ref,
                    encrypted_secret,
                    secret_nonce,
                    fingerprint,
                    1 if payload.get("enabled", True) else 0,
                    actor_id,
                    now,
                    now,
                ),
            )
            db.commit()
            row = db.fetch_one("SELECT * FROM model_credentials WHERE credential_id = ?", (credential_id,))
            return self._public_credential(row)
        finally:
            db.close()

    def update_credential(self, credential_id: str, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
        db = self.project_manager.get_default_database()
        try:
            existing = db.fetch_one("SELECT * FROM model_credentials WHERE credential_id = ?", (credential_id,))
            if not existing:
                raise ValueError("模型凭据不存在。")
            value = _row(existing)
            api_key = str(payload.pop("api_key", "") or "").strip()
            if api_key:
                encrypted, nonce, fingerprint = self.vault.encrypt(api_key)
                value.update(encrypted_secret=encrypted, secret_nonce=nonce, key_fingerprint=fingerprint, secret_ref="")
            for field in ("name", "provider", "api_format", "base_url", "model_id", "secret_ref"):
                if field in payload:
                    value[field] = str(payload[field] or "").strip()
            if "enabled" in payload:
                value["enabled"] = 1 if payload["enabled"] else 0
            db.execute(
                """UPDATE model_credentials SET name=?, provider=?, api_format=?, base_url=?,
                   model_id=?, secret_ref=?, encrypted_secret=?, secret_nonce=?, key_fingerprint=?,
                   enabled=?, updated_at=? WHERE credential_id=?""",
                (
                    value["name"], value["provider"], value["api_format"], value["base_url"],
                    value["model_id"], value["secret_ref"], value["encrypted_secret"],
                    value["secret_nonce"], value["key_fingerprint"], value["enabled"],
                    _now(), credential_id,
                ),
            )
            db.commit()
            return self._public_credential(db.fetch_one("SELECT * FROM model_credentials WHERE credential_id = ?", (credential_id,)))
        finally:
            db.close()

    def delete_credential(self, credential_id: str) -> None:
        db = self.project_manager.get_default_database()
        try:
            db.execute("DELETE FROM user_model_allocations WHERE credential_id = ?", (credential_id,))
            db.execute("DELETE FROM model_credentials WHERE credential_id = ?", (credential_id,))
            db.commit()
        finally:
            db.close()

    def user_policy(self, user_id: str) -> dict[str, Any]:
        self.ensure_user_profile(user_id)
        self.refresh_storage_usage(user_id)
        db = self.project_manager.get_default_database()
        try:
            quota = _row(db.fetch_one("SELECT * FROM user_storage_quotas WHERE user_id = ?", (user_id,)))
            allocations = db.fetch_all(
                """SELECT a.*, c.name AS credential_name, c.provider, c.model_id
                   FROM user_model_allocations a JOIN model_credentials c
                     ON c.credential_id = a.credential_id
                   WHERE a.user_id = ? ORDER BY c.name""",
                (user_id,),
            )
            return {
                "user_id": user_id,
                "storage": {
                    "quota_bytes": int(quota.get("quota_bytes") or DEFAULT_STORAGE_QUOTA_BYTES),
                    "used_bytes": int(quota.get("used_bytes") or 0),
                    "updated_at": quota.get("updated_at", ""),
                },
                "model_allocations": [dict(row) for row in allocations],
            }
        finally:
            db.close()

    def set_storage_quota(self, user_id: str, quota_bytes: int, actor_id: str) -> dict[str, Any]:
        if quota_bytes < 0:
            raise ValueError("存储配额不能小于 0。")
        self.ensure_user_profile(user_id)
        db = self.project_manager.get_default_database()
        try:
            db.execute(
                "UPDATE user_storage_quotas SET quota_bytes=?, updated_by=?, updated_at=? WHERE user_id=?",
                (quota_bytes, actor_id, _now(), user_id),
            )
            db.commit()
        finally:
            db.close()
        return self.user_policy(user_id)["storage"]

    def article_storage_owner(self, article_id: str, fallback_user_id: str) -> str:
        """Return the stable owner charged for one shared-workspace article."""

        if not article_id:
            return fallback_user_id
        db = self.project_manager.get_default_database()
        try:
            row = db.fetch_one(
                "SELECT owner_user_id FROM articles WHERE article_id = ?",
                (article_id,),
            )
            owner = str(_row(row).get("owner_user_id") or "").strip()
            return owner or fallback_user_id
        finally:
            db.close()

    def ensure_storage_available(self, user_id: str, incoming_bytes: int) -> dict[str, int]:
        """Reject a write when it would exceed the user's server quota."""

        self.ensure_user_profile(user_id)
        used = self.refresh_storage_usage(user_id)
        db = self.project_manager.get_default_database()
        try:
            row = _row(
                db.fetch_one(
                    "SELECT quota_bytes FROM user_storage_quotas WHERE user_id = ?",
                    (user_id,),
                )
            )
            quota = int(row.get("quota_bytes") or 0)
        finally:
            db.close()
        incoming = max(0, int(incoming_bytes))
        if used + incoming > quota:
            remaining = max(0, quota - used)
            raise ValueError(
                f"存储配额不足：本次需要 {incoming} 字节，当前仅剩 {remaining} 字节。"
            )
        return {"quota_bytes": quota, "used_bytes": used, "remaining_bytes": quota - used}

    def register_storage_object(
        self,
        user_id: str,
        project_id: str,
        article_id: str,
        object_type: str,
        relative_path: str,
        size_bytes: int,
        checksum: str = "",
    ) -> None:
        """Record a persisted project object without exposing host filesystem paths."""

        self.ensure_user_profile(user_id)
        now = _now()
        db = self.project_manager.get_default_database()
        try:
            if article_id:
                db.execute(
                    "UPDATE articles SET owner_user_id = ? "
                    "WHERE article_id = ? AND COALESCE(owner_user_id, '') = ''",
                    (user_id, article_id),
                )
                owner_row = db.fetch_one(
                    "SELECT owner_user_id FROM articles WHERE article_id = ?",
                    (article_id,),
                )
                user_id = str(_row(owner_row).get("owner_user_id") or user_id)
            db.execute(
                """INSERT INTO storage_objects
                   (object_id, user_id, project_id, article_id, object_type,
                    relative_path, size_bytes, checksum, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(relative_path) DO UPDATE SET
                     user_id=excluded.user_id,
                     project_id=excluded.project_id,
                     article_id=excluded.article_id,
                     object_type=excluded.object_type,
                     size_bytes=excluded.size_bytes,
                     checksum=excluded.checksum""",
                (
                    f"STO_{uuid4().hex.upper()}", user_id, project_id, article_id,
                    object_type, relative_path, max(0, int(size_bytes)), checksum, now,
                ),
            )
            db.commit()
        finally:
            db.close()
        self.refresh_storage_usage(user_id)

    def refresh_storage_usage(self, user_id: str) -> int:
        """Reconcile charged bytes with article directories and tracked loose files."""

        self.ensure_user_profile(user_id)
        _config, project_dir = self.project_manager.ensure_default_workspace()
        root = project_dir.resolve()
        db = self.project_manager.get_default_database()
        try:
            articles = db.fetch_all(
                "SELECT article_dir FROM articles WHERE owner_user_id = ?",
                (user_id,),
            )
            loose_objects = db.fetch_all(
                "SELECT relative_path FROM storage_objects "
                "WHERE user_id = ? AND COALESCE(article_id, '') = ''",
                (user_id,),
            )
            paths: set[Path] = set()
            for row in articles:
                relative = str(row["article_dir"] or "").strip()
                if relative:
                    paths.add((project_dir / relative).resolve())
            for row in loose_objects:
                relative = str(row["relative_path"] or "").strip()
                if relative:
                    paths.add((project_dir / relative).resolve())

            size = 0
            counted_files: set[Path] = set()
            for path in paths:
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                candidates = path.rglob("*") if path.is_dir() else [path]
                for candidate in candidates:
                    if candidate.is_file() and candidate not in counted_files:
                        counted_files.add(candidate)
                        try:
                            size += candidate.stat().st_size
                        except OSError:
                            continue
            db.execute(
                "UPDATE user_storage_quotas SET used_bytes=?, updated_at=? WHERE user_id=?",
                (size, _now(), user_id),
            )
            db.commit()
            return size
        finally:
            db.close()

    def set_model_allocation(
        self,
        user_id: str,
        credential_id: str,
        enabled: bool,
        monthly_token_limit: int,
        monthly_cost_limit: float,
        actor_id: str,
    ) -> dict[str, Any]:
        self.ensure_user_profile(user_id)
        now = _now()
        db = self.project_manager.get_default_database()
        try:
            credential = db.fetch_one("SELECT credential_id FROM model_credentials WHERE credential_id = ?", (credential_id,))
            if not credential:
                raise ValueError("模型凭据不存在。")
            db.execute(
                """INSERT INTO user_model_allocations
                   (allocation_id, user_id, credential_id, enabled, monthly_token_limit,
                    monthly_cost_limit, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, credential_id) DO UPDATE SET
                     enabled=excluded.enabled,
                     monthly_token_limit=excluded.monthly_token_limit,
                     monthly_cost_limit=excluded.monthly_cost_limit,
                     created_by=excluded.created_by,
                     updated_at=excluded.updated_at""",
                (
                    f"ALLOC_{uuid4().hex.upper()}", user_id, credential_id,
                    1 if enabled else 0, max(0, monthly_token_limit), max(0.0, monthly_cost_limit),
                    actor_id, now, now,
                ),
            )
            db.commit()
        finally:
            db.close()
        return self.user_policy(user_id)

    def audit_events(self, limit: int = 200, user_id: str = "") -> list[dict[str, Any]]:
        db = self.project_manager.get_default_database()
        try:
            if user_id:
                rows = db.fetch_all(
                    "SELECT * FROM api_audit_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                )
            else:
                rows = db.fetch_all("SELECT * FROM api_audit_events ORDER BY created_at DESC LIMIT ?", (limit,))
            result = []
            for row in rows:
                value = dict(row)
                try:
                    value["roles"] = json.loads(value.pop("roles_json", "[]"))
                except json.JSONDecodeError:
                    value["roles"] = []
                result.append(value)
            return result
        finally:
            db.close()

    def tasks(self, limit: int = 200) -> list[dict[str, Any]]:
        db = self.project_manager.get_default_database()
        try:
            rows = db.fetch_all("SELECT * FROM workflow_tasks ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in rows]
        finally:
            db.close()
