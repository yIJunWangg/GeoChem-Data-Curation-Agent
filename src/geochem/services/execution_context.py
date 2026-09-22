"""Request-scoped identity propagated into background model calls."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterable, Iterator


_CURRENT_USER_ID: ContextVar[str] = ContextVar("geochem_current_user_id", default="")
_CURRENT_USER_ROLES: ContextVar[frozenset[str]] = ContextVar(
    "geochem_current_user_roles", default=frozenset()
)


def current_user_id() -> str:
    """Return the authenticated account currently executing business work."""

    return _CURRENT_USER_ID.get()


def current_user_roles() -> frozenset[str]:
    """Return platform roles bound to the current request or background task."""

    return _CURRENT_USER_ROLES.get()


@contextmanager
def user_execution_context(
    user_id: str | None,
    roles: Iterable[str] | None = None,
) -> Iterator[None]:
    """Temporarily bind an authenticated account to this execution context."""

    token = _CURRENT_USER_ID.set(str(user_id or "").strip())
    role_token = _CURRENT_USER_ROLES.set(
        frozenset(str(role).strip().lower() for role in (roles or ()) if role)
    )
    try:
        yield
    finally:
        _CURRENT_USER_ROLES.reset(role_token)
        _CURRENT_USER_ID.reset(token)
