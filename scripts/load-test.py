#!/usr/bin/env python3
"""Read-only HTTP capacity baseline for a GeoChem LAN deployment."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import ssl
import statistics
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


PUBLIC_PATHS = (
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/auth/config",
)
AUTHENTICATED_PATHS = (
    "/api/v1/dashboard?project_id=DEFAULT_WORKSPACE",
    "/api/v1/articles?project_id=DEFAULT_WORKSPACE",
    "/api/v1/header-configs?project_id=DEFAULT_WORKSPACE",
)


@dataclass(frozen=True)
class Observation:
    elapsed_ms: float
    status: int
    error: str = ""


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a non-mutating 20-50 user capacity baseline against GeoChem."
    )
    parser.add_argument("--base-url", default=os.environ.get("GEOCHEM_BASE_URL", "https://geochem.lan"))
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--duration", type=int, default=60, help="seconds")
    parser.add_argument("--timeout", type=float, default=10.0, help="per request seconds")
    parser.add_argument("--token", default=os.environ.get("GEOCHEM_TEST_ACCESS_TOKEN", ""))
    parser.add_argument("--ca-cert", default=os.environ.get("GEOCHEM_CA_CERT", "deploy/geochem-lan-root.crt"))
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=2000.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if not 1 <= args.users <= 100:
        parser.error("--users must be between 1 and 100")
    if args.duration < 5:
        parser.error("--duration must be at least 5 seconds")
    return args


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/") + "/"
    ca_path = Path(args.ca_cert).expanduser()
    context = ssl.create_default_context(cafile=str(ca_path)) if ca_path.is_file() else ssl.create_default_context()
    paths = AUTHENTICATED_PATHS if args.token else PUBLIC_PATHS
    mode = "authenticated-business-read" if args.token else "public-edge-only"
    deadline = time.monotonic() + args.duration
    observations: list[Observation] = []
    lock = threading.Lock()

    def worker(worker_id: int) -> None:
        cursor = worker_id % len(paths)
        local: list[Observation] = []
        while time.monotonic() < deadline:
            path = paths[cursor % len(paths)]
            cursor += 1
            headers = {"Accept": "application/json", "User-Agent": "geochem-capacity-test/1"}
            if args.token:
                headers["Authorization"] = f"Bearer {args.token}"
            started = time.perf_counter()
            try:
                request = Request(urljoin(base_url, path.lstrip("/")), headers=headers)
                with urlopen(request, timeout=args.timeout, context=context) as response:
                    response.read(4096)
                    status = response.status
                error = "" if 200 <= status < 400 else f"HTTP {status}"
            except HTTPError as exc:
                status = exc.code
                error = f"HTTP {exc.code}"
            except (URLError, TimeoutError, OSError) as exc:
                status = 0
                error = f"{type(exc).__name__}: {exc}"
            elapsed_ms = (time.perf_counter() - started) * 1000
            local.append(Observation(elapsed_ms=elapsed_ms, status=status, error=error))
        with lock:
            observations.extend(local)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.users, thread_name_prefix="geochem-load") as pool:
        list(pool.map(worker, range(args.users)))
    wall_seconds = max(0.001, time.monotonic() - started)

    latencies = [item.elapsed_ms for item in observations]
    failures = [item for item in observations if item.error]
    status_counts: dict[str, int] = {}
    for item in observations:
        key = str(item.status or "transport_error")
        status_counts[key] = status_counts.get(key, 0) + 1
    error_rate = len(failures) / len(observations) if observations else 1.0
    report = {
        "mode": mode,
        "base_url": args.base_url,
        "users": args.users,
        "duration_seconds": round(wall_seconds, 3),
        "requests": len(observations),
        "requests_per_second": round(len(observations) / wall_seconds, 2),
        "errors": len(failures),
        "error_rate": round(error_rate, 6),
        "status_counts": status_counts,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "thresholds": {
            "max_error_rate": args.max_error_rate,
            "max_p95_ms": args.max_p95_ms,
        },
        "sample_errors": [item.error for item in failures[:10]],
    }
    report["passed"] = bool(
        observations
        and error_rate <= args.max_error_rate
        and report["latency_ms"]["p95"] <= args.max_p95_ms
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.token:
        print(
            "WARNING: no access token supplied; this proves only Caddy/FastAPI public endpoint capacity, "
            "not authenticated business-query capacity.",
            flush=True,
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

