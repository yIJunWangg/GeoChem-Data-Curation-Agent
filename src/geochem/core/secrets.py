"""Secret reference helpers for API keys.

Configuration files should store references such as ``${OPENAI_API_KEY}``,
not plaintext credentials.  At runtime we resolve from environment variables
first, then from the optional system keyring when available.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

SECRET_SERVICE = "geochem-data-curation-agent"
SECRET_REF_RE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


def secret_env_name_for_provider(provider_name: str) -> str:
    """Return a stable environment variable name for a provider."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", provider_name or "CUSTOM").strip("_").upper()
    normalized = normalized.replace("OPENCODE_GO_OPENAI", "OPENCODE_GO")
    normalized = normalized.replace("OPENCODE_GO_ANTHROPIC", "OPENCODE_GO")
    normalized = normalized.replace("DEEPSEEK_OPENAI", "DEEPSEEK")
    normalized = normalized.replace("DEEPSEEK_ANTHROPIC", "DEEPSEEK")
    normalized = normalized.replace("XIAOMI_ANTHROPIC", "MIMO")
    normalized = normalized.replace("XIAOMI", "MIMO")
    normalized = normalized.replace("CUSTOM_OPENAI_COMPATIBLE", "CUSTOM_OPENAI")
    normalized = normalized.replace("CUSTOM_ANTHROPIC_COMPATIBLE", "CUSTOM_ANTHROPIC")
    return f"{normalized or 'CUSTOM'}_API_KEY"


def secret_ref_for_provider(provider_name: str) -> str:
    return f"${{{secret_env_name_for_provider(provider_name)}}}"


def parse_secret_ref(value: str | None) -> str:
    match = SECRET_REF_RE.match((value or "").strip())
    return match.group(1) if match else ""


def looks_like_plaintext_secret(value: str | None) -> bool:
    value = (value or "").strip()
    if not value or parse_secret_ref(value):
        return False
    if value in {"ollama", "not-used"}:
        return False
    if value.startswith("sk-") and len(value) >= 12:
        return True
    if len(value) >= 32 and re.search(r"[A-Za-z]", value) and re.search(r"\d", value):
        return True
    return False


def _keyring_get(env_name: str) -> str:
    try:
        import keyring  # type: ignore
    except Exception:
        return ""
    try:
        return keyring.get_password(SECRET_SERVICE, env_name) or ""
    except Exception:
        pass
    if sys.platform == "darwin" and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", SECRET_SERVICE, "-a", env_name, "-w"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            return ""
    return ""


def _keyring_set(env_name: str, value: str) -> bool:
    try:
        import keyring  # type: ignore
    except Exception:
        return False
    try:
        keyring.set_password(SECRET_SERVICE, env_name, value)
        return True
    except Exception:
        pass
    if sys.platform == "darwin" and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            result = subprocess.run(
                ["security", "add-generic-password", "-U", "-s", SECRET_SERVICE, "-a", env_name, "-w", value],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False
    return False


def resolve_secret(value: str | None) -> tuple[str, str, str]:
    """Resolve a config secret value.

    Returns ``(secret, source, env_name)`` where source is one of
    ``environment``, ``keychain``, ``inline`` or ``missing``.
    """
    value = (value or "").strip()
    env_name = parse_secret_ref(value)
    if env_name:
        env_value = os.environ.get(env_name, "")
        if env_value:
            return env_value, "environment", env_name
        keychain_value = _keyring_get(env_name)
        if keychain_value:
            return keychain_value, "keychain", env_name
        return "", "missing", env_name
    if value:
        return value, "inline", ""
    return "", "missing", ""


def store_secret_for_provider(provider_name: str, secret: str, env_name: str | None = None) -> tuple[str, str]:
    """Store a secret outside YAML and return ``(secret_ref, source)``.

    The current process environment is always populated so a just-saved model
    can be used immediately.  If ``keyring`` is installed, the same value is
    also written to the OS keychain for future app launches.
    """
    env_name = env_name or secret_env_name_for_provider(provider_name)
    os.environ[env_name] = secret
    source = "keychain" if _keyring_set(env_name, secret) else "environment"
    return f"${{{env_name}}}", source
