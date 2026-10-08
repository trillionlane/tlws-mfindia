#!/usr/bin/env python3
"""Verify the private MFDataIndia API from an internal Cloud Run job.

The job uses its attached service account to obtain a short-lived Google-signed
OIDC token. It performs only bounded GET requests and never prints tokens or
response bodies.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from urllib.parse import urlsplit

import requests

METADATA_IDENTITY_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
REQUEST_TIMEOUT_SECONDS = 15
METADATA_TIMEOUT_SECONDS = 3


class PrivateSmokeError(RuntimeError):
    """The internal API did not satisfy the private smoke contract."""


def _service_url(raw: str, *, name: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise PrivateSmokeError(f"{name} must be an HTTPS service origin")
    return value


def _identity_token(session: requests.Session, audience: str) -> str:
    response = session.get(
        METADATA_IDENTITY_URL,
        params={"audience": audience},
        headers={"Metadata-Flavor": "Google"},
        timeout=METADATA_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise PrivateSmokeError(f"workload identity token unavailable: HTTP {response.status_code}")
    token = response.text.strip()
    if not token:
        raise PrivateSmokeError("workload identity token unavailable: empty response")
    return token


def _get_json(
    session: requests.Session,
    base_url: str,
    path: str,
    token: str,
    *,
    label: str,
) -> Any:
    response = session.get(
        f"{base_url}{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "mfdataindia-private-smoke/1.0",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise PrivateSmokeError(
            f"private API smoke failed for {label}: HTTP {response.status_code}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise PrivateSmokeError(f"private API returned invalid JSON for {label}") from exc


def run_smoke(base_url: str, audience: str, session: requests.Session) -> None:
    base_url = _service_url(base_url, name="MFDATAINDIA_PRIVATE_BASE_URL")
    audience = _service_url(audience, name="MFDATAINDIA_PRIVATE_AUDIENCE")
    if audience != base_url:
        raise PrivateSmokeError("private API audience must exactly match the service origin")

    token = _identity_token(session, audience)

    health = _get_json(session, base_url, "/api/health", token, label="health")
    if not isinstance(health, dict) or health.get("ok") is not True:
        raise PrivateSmokeError("private API health contract failed")
    if not str(health.get("db", "")).startswith("PostgreSQL 18."):
        raise PrivateSmokeError("private API database contract failed")

    stats = _get_json(session, base_url, "/api/stats", token, label="stats")
    required_positive = ("schemes_total", "in_scope_live", "nav_rows", "dataset_version")
    if not isinstance(stats, dict) or any(
        not isinstance(stats.get(key), int) or stats[key] < 1 for key in required_positive
    ):
        raise PrivateSmokeError("private API statistics contract failed")
    if not stats.get("nav_first") or not stats.get("nav_last"):
        raise PrivateSmokeError("private API NAV bounds are absent")

    search = _get_json(session, base_url, "/api/funds?q=360&per_page=1", token, label="fund search")
    results = search.get("results") if isinstance(search, dict) else None
    if not isinstance(results, list) or len(results) != 1:
        raise PrivateSmokeError("private API search contract failed")
    code = results[0].get("amfi_scheme_code") if isinstance(results[0], dict) else None
    if not isinstance(code, int):
        raise PrivateSmokeError("private API search returned no AMFI code")

    detail = _get_json(session, base_url, f"/api/funds/{code}", token, label="fund detail")
    if not isinstance(detail, dict):
        raise PrivateSmokeError("private API fund-detail contract failed")
    family = detail.get("family")
    if detail.get("amfi_scheme_code") != code or not isinstance(family, dict):
        raise PrivateSmokeError("private API fund-detail contract failed")
    if not family.get("tlws_mf_id"):
        raise PrivateSmokeError("private API family identity is absent")

    nav = _get_json(session, base_url, f"/api/funds/{code}/nav?years=1", token, label="NAV")
    if not isinstance(nav, dict):
        raise PrivateSmokeError("private API NAV contract failed")
    points = nav.get("points")
    if nav.get("code") != code or not isinstance(points, list) or not points:
        raise PrivateSmokeError("private API NAV contract failed")


def main() -> int:
    base_url = os.environ.get("MFDATAINDIA_PRIVATE_BASE_URL", "")
    audience = os.environ.get("MFDATAINDIA_PRIVATE_AUDIENCE", "")
    session = requests.Session()
    session.trust_env = False
    try:
        run_smoke(base_url, audience, session)
    except (PrivateSmokeError, requests.RequestException) as exc:
        print(f"private MFDataIndia smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()
    print("private MFDataIndia smoke passed: endpoints=5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
