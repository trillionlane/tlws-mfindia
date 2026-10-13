#!/usr/bin/env python3
"""Pin the isolated enhancement contract without changing the 19-route v2 pin."""
import argparse
import json
from pathlib import Path

from mfdataindia.review.app import create_review_app

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/mfdataindia-verified-review-v1.json"
EXPECTED = {
    "/api/review/v1/policies/{policy_hash}": {"get"},
    "/api/review/v1/funds": {"get"},
    "/api/review/v1/funds/{code}": {"get"},
    "/api/review/v1/funds/{code}/analytics": {"get"},
    "/api/review/v1/funds/{code}/reviews": {"post"},
    "/api/review/v1/funds/{code}/enrichment-proposals": {"post"},
}


def render():
    schema = create_review_app("postgresql://contract:contract@127.0.0.1:1/contract").openapi()
    if {p:set(ops) for p,ops in schema["paths"].items()} != EXPECTED:
        raise ValueError("review endpoint inventory drift")
    for path, operations in schema["paths"].items():
        for operation in operations.values():
            if not operation.get("security"):
                raise ValueError("missing signed assertion requirement: " + path)
    schema["info"]["x-contract-id"] = "mfdataindia-verified-review-v1"
    schema["x-source-mutations"] = False
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = render()
    if args.check:
        if not CONTRACT.exists() or CONTRACT.read_text() != content:
            raise SystemExit("verified-review contract drift")
    else:
        CONTRACT.write_text(content)
