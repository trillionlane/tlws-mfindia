#!/usr/bin/env python3
"""Export the reviewed MFDataIndia JSON read contract deterministically."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mfdataindia.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "mfdataindia-openapi-v2.json"
CONTRACT_ID = "mfdataindia-json-read-v2"
EXPECTED_API_PATHS = {
    "/api/amcs",
    "/api/categories",
    "/api/compare",
    "/api/fund-families",
    "/api/funds",
    "/api/funds/batch",
    "/api/funds/{code}",
    "/api/funds/{code}/analytics",
    "/api/funds/{code}/nav",
    "/api/funds/{code}/peers",
    "/api/funds/{code}/returns",
    "/api/funds/{code}/risk-reward",
    "/api/health",
    "/api/holdings-overlap",
    "/api/movers",
    "/api/movers/categories",
    "/api/options",
    "/api/stats",
    "/api/suggest",
}


#: Endpoints changed in the data-quality / performance-methodology sprint.
#: Their exported responses MUST carry the concrete schemas (not a generic
#: additionalProperties object), so the guard below fails on drift.
_TYPED_ENDPOINTS = {
    "/api/funds/{code}": "FundDetail",
    "/api/funds/{code}/returns": "ReturnsResponse",
    "/api/funds/{code}/analytics": "AnalyticsResponse",
    "/api/funds/{code}/peers": "PeersResponse",
    "/api/funds/{code}/risk-reward": "RiskRewardResponse",
    "/api/movers": "MoversResponse",
    "/api/movers/categories": "CategoryMoversResponse",
}


def render_contract() -> str:
    app = create_app("postgresql://contract:contract@127.0.0.1:1/contract")
    schema = app.openapi()
    api_paths = {path for path in schema["paths"] if path.startswith("/api/")}
    if api_paths != EXPECTED_API_PATHS:
        missing = sorted(EXPECTED_API_PATHS - api_paths)
        unexpected = sorted(api_paths - EXPECTED_API_PATHS)
        raise RuntimeError(
            f"MFData API inventory drift: missing={missing}, unexpected={unexpected}"
        )
    non_get = sorted(
        f"{method.upper()} {path}"
        for path in api_paths
        for method in schema["paths"][path]
        if method.lower() not in {"get", "parameters"}
    )
    if non_get:
        raise RuntimeError(f"MFData contract is no longer read-only: {non_get}")

    # v2: the changed endpoints must export concrete response schemas.
    schemas = schema["components"]["schemas"]
    for path, model in _TYPED_ENDPOINTS.items():
        if model not in schemas:
            raise RuntimeError(f"contract v2 lost the explicit response model {model} for {path}")
        op = schema["paths"][path]["get"]
        ref = (
            op.get("responses", {})
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
            .get("$ref", "")
        )
        if not ref.endswith(model):
            raise RuntimeError(f"{path} no longer exposes {model} as its 200 response schema")

    # The private API has no authentication surface: no security schemes, no
    # operation-level security requirements (access control is network-level,
    # owned by the deployment, not the API).
    if "securitySchemes" in schema.get("components", {}):
        raise RuntimeError("MFData contract must not add authentication schemes")
    for path in api_paths:
        op = schema["paths"][path].get("get", {})
        if op.get("security"):
            raise RuntimeError(f"{path} added an operation-level security requirement")

    schema["info"]["x-contract-id"] = CONTRACT_ID
    schema["info"]["x-contract-version"] = 2
    schema["x-mfdata-api-path-count"] = len(api_paths)
    schema["x-mfdata-read-only"] = True
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_contract()
    if args.check:
        if not CONTRACT_PATH.is_file() or CONTRACT_PATH.read_text() != rendered:
            raise SystemExit(
                "MFData OpenAPI contract drifted; run scripts/export_openapi.py and review it"
            )
        return
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(rendered)


if __name__ == "__main__":
    main()
