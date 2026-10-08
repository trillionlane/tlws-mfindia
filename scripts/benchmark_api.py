#!/usr/bin/env python3
"""Run a small, repeatable HTTP benchmark against an MFDataIndia API.

The benchmark is intentionally bounded. It compares identity and gzip transfer
sizes without issuing writes or creating enough traffic to act as a load test.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

DEFAULT_ENDPOINTS = (
    "/api/stats",
    "/api/funds?per_page=50",
    "/api/funds/100033",
    "/api/funds/100033/nav?years=20",
    "/api/movers/categories?period=1m&limit=5",
)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _fetch(url: str, *, encoding: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": encoding,
            "User-Agent": "mfdataindia-bounded-benchmark/1.0",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return {
                "status": response.status,
                "duration_ms": (time.perf_counter() - started) * 1000,
                "bytes": len(body),
                "content_encoding": response.headers.get("Content-Encoding", "identity"),
            }
    except urllib.error.HTTPError as exc:
        exc.read()
        return {
            "status": exc.code,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "bytes": 0,
            "content_encoding": "identity",
        }


def _benchmark(
    base_url: str,
    endpoint: str,
    *,
    encoding: str,
    warmups: int,
    samples: int,
    timeout: float,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{endpoint}"
    for _ in range(warmups):
        warmup = _fetch(url, encoding=encoding, timeout=timeout)
        if warmup["status"] != 200:
            raise RuntimeError(f"warm-up failed for {endpoint}: HTTP {warmup['status']}")

    results = [_fetch(url, encoding=encoding, timeout=timeout) for _ in range(samples)]
    failures = [result["status"] for result in results if result["status"] != 200]
    if failures:
        raise RuntimeError(f"benchmark failed for {endpoint}: HTTP statuses {failures}")

    durations = [result["duration_ms"] for result in results]
    sizes = [result["bytes"] for result in results]
    return {
        "endpoint": endpoint,
        "requested_encoding": encoding,
        "content_encoding": results[-1]["content_encoding"],
        "samples": samples,
        "p50_ms": round(_percentile(durations, 0.50), 1),
        "p95_ms": round(_percentile(durations, 0.95), 1),
        "min_bytes": min(sizes),
        "max_bytes": max(sizes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--endpoint", action="append", dest="endpoints")
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    if args.warmups < 0 or args.samples < 1 or args.samples > 100:
        parser.error("warmups must be non-negative and samples must be between 1 and 100")

    endpoints = tuple(args.endpoints or DEFAULT_ENDPOINTS)
    report = {
        "base_url": args.base_url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warmups": args.warmups,
        "results": [
            _benchmark(
                args.base_url,
                endpoint,
                encoding=encoding,
                warmups=args.warmups,
                samples=args.samples,
                timeout=args.timeout,
            )
            for endpoint in endpoints
            for encoding in ("identity", "gzip")
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
