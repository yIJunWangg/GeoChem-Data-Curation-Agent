"""Resolve administrator-managed model grants and account their usage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any
from uuid import uuid4

from ..core.runtime import RuntimeSettings
from ..core.secrets import resolve_secret
from .admin_governance import CredentialVault


@dataclass(frozen=True)
class UserModelGrant:
    credential_id: str
    provider: str
    api_format: str
    base_url: str
    model_id: str
    api_key: str
    monthly_token_limit: int
    monthly_cost_limit: float


def _secret_from_row(row: dict[str, Any], runtime: RuntimeSettings) -> str:
    ciphertext = str(row.get("encrypted_secret") or "")
    nonce = str(row.get("secret_nonce") or "")
    if ciphertext and nonce:
        return CredentialVault(runtime.credential_master_key).decrypt(ciphertext, nonce)

    reference = str(row.get("secret_ref") or "").strip()
    if not reference:
        return ""
    secret, _source, env_name = resolve_secret(reference)
    if secret and not env_name and reference == secret:
        # Admins may enter either ``${NAME}`` or the shorter ``NAME`` form.
        # A bare reference is never itself treated as an API key.
        return os.environ.get(reference, "")
    return secret


def resolve_user_model_grant(
    db,
    runtime: RuntimeSettings,
    user_id: str,
    preferred_provider: str = "",
    preferred_model: str = "",
) -> UserModelGrant:
    """Return one enabled model grant or raise an actionable policy error."""

    rows = [
        dict(row)
        for row in db.fetch_all(
            """SELECT a.credential_id, a.monthly_token_limit, a.monthly_cost_limit,
                      a.updated_at AS allocation_updated_at,
                      c.provider, c.api_format, c.base_url, c.model_id,
                      c.secret_ref, c.encrypted_secret, c.secret_nonce
               FROM user_model_allocations a
               JOIN model_credentials c ON c.credential_id = a.credential_id
               WHERE a.user_id = ? AND a.enabled = 1 AND c.enabled = 1
               ORDER BY a.updated_at DESC, c.name ASC""",
            (user_id,),
        )
    ]
    if not rows:
        raise ValueError("管理员尚未为当前账号分配可用的 AI 模型，请联系管理员。")

    preferred_provider = preferred_provider.strip().casefold()
    preferred_model = preferred_model.strip().casefold()

    def rank(row: dict[str, Any]) -> tuple[int, str]:
        provider = str(row.get("provider") or "").casefold()
        model = str(row.get("model_id") or "").casefold()
        if preferred_provider and preferred_model and provider == preferred_provider and model == preferred_model:
            score = 0
        elif preferred_model and model == preferred_model:
            score = 1
        elif preferred_provider and provider == preferred_provider:
            score = 2
        else:
            score = 3
        return score, str(row.get("allocation_updated_at") or "")

    selected = sorted(rows, key=rank)[0]
    api_key = _secret_from_row(selected, runtime)
    if not api_key:
        raise ValueError("管理员分配的模型凭据当前不可用，请检查服务器密钥或环境变量。")

    usage_month = datetime.now(timezone.utc).strftime("%Y-%m")
    usage = db.fetch_one(
        """SELECT total_tokens, estimated_cost FROM user_ai_usage_monthly
           WHERE user_id = ? AND credential_id = ? AND usage_month = ?""",
        (user_id, selected["credential_id"], usage_month),
    )
    used_tokens = int(usage["total_tokens"] or 0) if usage else 0
    used_cost = float(usage["estimated_cost"] or 0) if usage else 0.0
    token_limit = int(selected.get("monthly_token_limit") or 0)
    cost_limit = float(selected.get("monthly_cost_limit") or 0.0)
    if token_limit and used_tokens >= token_limit:
        raise ValueError("当前账号本月的 AI Token 配额已用完，请联系管理员调整额度。")
    if cost_limit and used_cost >= cost_limit:
        raise ValueError("当前账号本月的 AI 成本额度已用完，请联系管理员调整额度。")

    return UserModelGrant(
        credential_id=str(selected["credential_id"]),
        provider=str(selected.get("provider") or "managed-provider"),
        api_format=str(selected.get("api_format") or "openai").lower(),
        base_url=str(selected.get("base_url") or ""),
        model_id=str(selected.get("model_id") or ""),
        api_key=api_key,
        monthly_token_limit=token_limit,
        monthly_cost_limit=cost_limit,
    )


def record_user_model_usage(
    db,
    user_id: str,
    grant: UserModelGrant,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    estimated_cost: float = 0.0,
) -> None:
    """Atomically add one successful model call to the user's monthly usage."""

    usage_month = datetime.now(timezone.utc).strftime("%Y-%m")
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO user_ai_usage_monthly
           (usage_id, user_id, credential_id, usage_month, input_tokens,
            output_tokens, total_tokens, estimated_cost, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, credential_id, usage_month) DO UPDATE SET
             input_tokens=user_ai_usage_monthly.input_tokens + excluded.input_tokens,
             output_tokens=user_ai_usage_monthly.output_tokens + excluded.output_tokens,
             total_tokens=user_ai_usage_monthly.total_tokens + excluded.total_tokens,
             estimated_cost=user_ai_usage_monthly.estimated_cost + excluded.estimated_cost,
             updated_at=excluded.updated_at""",
        (
            f"USAGE_{uuid4().hex.upper()}",
            user_id,
            grant.credential_id,
            usage_month,
            max(0, int(input_tokens)),
            max(0, int(output_tokens)),
            max(0, int(total_tokens)),
            max(0.0, float(estimated_cost)),
            now,
        ),
    )
    db.commit()
