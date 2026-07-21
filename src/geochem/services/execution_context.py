"""Request-scoped identity propagated into background model calls."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_CURRENT_USER_ID: ContextVar[str] = ContextVar("geochem_current_user_id", default="")


def current_user_id() -> str:
    """Return the authenticated account currently executing business work."""

    return _CURRENT_USER_ID.get()


@contextmanager
def user_execution_context(user_id: str | None) -> Iterator[None]:
    """Temporarily bind an authenticated account to this execution context."""

    token = _CURRENT_USER_ID.set(str(user_id or "").strip())
    try:
        yield
    finally:
        _CURRENT_USER_ID.reset(token)
