"""User-scoped API rate limiting for local and server deployments."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
import time
from typing import Any

from ..core.runtime import RuntimeProfile, RuntimeSettings


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    bucket: str


class ApiRateLimiter:
    """Fixed-window limiter backed by Redis in server profiles.

    Development intentionally uses an in-process store so a Mac preview does
    not require Redis. Staging and production fail closed when Redis is not
    reachable because silently disabling abuse protection is unsafe.
    """

    def __init__(self, settings: RuntimeSettings):
        self.settings = settings
        self._lock = Lock()
        self._memory: dict[str, tuple[int, int]] = {}
        self._redis: Any | None = None
        if settings.profile != RuntimeProfile.DEVELOPMENT:
            from redis import Redis

            self._redis = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )

    def policy(self, method: str, path: str) -> tuple[str, int, int]:
        upper = method.upper()
        # Chat/run state is polled while an Agent task is active. Keep those
        # reads governed, but do not charge them against the user's LLM budget.
        if upper in {"GET", "HEAD", "OPTIONS"}:
            return "read", max(self.settings.request_rate_per_minute, 600), 60
        if upper == "POST" and (
            path.endswith("/upload")
            or path.endswith("/import/file")
            or "/header-configs/import" in path
        ):
            return "upload", self.settings.upload_rate_per_hour, 3600
        if (
            "/chat/" in path
            or "/agent-runs" in path
            or "/rag/" in path
            or "/literature/" in path
            or path.endswith("/assist")
        ):
            return "ai", self.settings.chat_rate_per_minute, 60
        return "api", self.settings.request_rate_per_minute, 60

    def check(self, user_id: str, method: str, path: str) -> RateLimitDecision:
        bucket, limit, window = self.policy(method, path)
        now = int(time.time())
        period = now // window
        retry_after = max(1, window - (now % window))
        key = f"geochem:rate:{bucket}:{user_id}:{period}"
        if self._redis is not None:
            try:
                pipeline = self._redis.pipeline(transaction=True)
                pipeline.incr(key)
                pipeline.expire(key, window + 5)
                count = int(pipeline.execute()[0])
            except Exception as exc:
                raise RuntimeError("限流服务暂时不可用，请稍后重试。") from exc
        else:
            with self._lock:
                stale_period = period - 2
                if len(self._memory) > 2000:
                    self._memory = {
                        item_key: item_value
                        for item_key, item_value in self._memory.items()
                        if item_value[0] >= stale_period
                    }
                stored_period, count = self._memory.get(key, (period, 0))
                count = count + 1 if stored_period == period else 1
                self._memory[key] = (period, count)
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after=retry_after,
            bucket=bucket,
        )
