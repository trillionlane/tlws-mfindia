#!/usr/bin/env python3
"""Export the reviewed MFDataIndia JSON read contract deterministically."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mfdataindia.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "mfdataindia-openapi-v1.json"
CONTRACT_ID = "mfdataindia-json-read-v1"
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


def render_contract() -> str:
    app = create_app("postgresql://contract:contract@127.0.0.1:1/contract")
    schema = app.openapi()
    api_paths = {path for path in schema["paths"] if path.startswith("/api/")}
    if api_paths != EXPECTED_API_PATHS:
        missing = sorted(EXPECTED_API_PATHS - api_paths)
        unexpected = sorted(api_paths - EXPECTED_API_PATHS)
        raise RuntimeError(f"MFData API inventory drift: missing={missing}, unexpected={unexpected}")
    non_get = sorted(
        f"{method.upper()} {path}"
        for path in api_paths
        for method in schema["paths"][path]
        if method.lower() not in {"get", "parameters"}
    )
    if non_get:
        raise RuntimeError(f"MFData contract is no longer read-only: {non_get}")
    schema["info"]["x-contract-id"] = CONTRACT_ID
    schema["info"]["x-contract-version"] = 1
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
