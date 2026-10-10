#!/usr/bin/env python3
"""Export the reviewed private association-tag contract deterministically."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mfdataindia.api.association_app import create_association_app

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "mfdataindia-association-tags-v1.json"
CONTRACT_ID = "mfdataindia-association-tags-v1"
RESOURCE = "/api/fund-families/{tlws_mf_id}/association-tags"


def render_contract() -> str:
    app = create_association_app(
        "postgresql://contract:contract@127.0.0.1:1/contract",
        writes_enabled=False,
    )
    schema = app.openapi()
    api_paths = {path for path in schema["paths"] if path.startswith("/api/")}
    if api_paths != {"/api/health", RESOURCE}:
        raise RuntimeError(f"association API inventory drift: {sorted(api_paths)}")
    methods = {
        method
        for method in schema["paths"][RESOURCE]
        if method.lower() not in {"parameters"}
    }
    if methods != {"get", "post"}:
        raise RuntimeError(f"association resource methods drifted: {sorted(methods)}")
    if "securitySchemes" in schema.get("components", {}):
        raise RuntimeError("association contract must use deployment IAM, not API secrets")
    schema["info"]["x-contract-id"] = CONTRACT_ID
    schema["info"]["x-contract-version"] = 1
    schema["x-mfdata-private"] = True
    schema["x-mfdata-browser-access"] = False
    schema["x-mfdata-authentication"] = "google-oidc-workload-identity"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_contract()
    if args.check:
        if not CONTRACT_PATH.is_file() or CONTRACT_PATH.read_text() != rendered:
            raise SystemExit(
                "association OpenAPI contract drifted; run "
                "scripts/export_association_openapi.py and review it"
            )
        return
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(rendered)


if __name__ == "__main__":
    main()
