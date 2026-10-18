#!/usr/bin/env python3
"""Read-only actual source/gap export; no acquisition, publication or activation."""

import argparse
import os
import sys

import psycopg
from psycopg.rows import dict_row

from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.enrichment.source_operations import engine_hash, instant, inventory
from mfdataindia.review.evidence_transport import render_evidence
from mfdataindia.review.rollout import Manifest, seal_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=instant, required=True)
    args = parser.parse_args()
    manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
    if os.environ.get("DEPLOYMENT_GIT_SHA") != manifest.git_sha:
        raise ValueError("exact deployed inventory image required")
    with psycopg.connect(pinned_dsn(manifest, os.environ, "mfdata_verified_reader"), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        result = inventory(conn, as_of=args.as_of)
    print(render_evidence(seal_evidence(manifest, "fact-source-operations", {
        "level": "DEV_READONLY_SOURCE_OPERATIONS_NOT_PUBLICATION",
        "engine_hash": engine_hash(), "inventory": result,
    })), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("source operations inventory failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
