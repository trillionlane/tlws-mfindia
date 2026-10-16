#!/usr/bin/env python3
"""Read-only all-AMC census; no acquisition/publication or deployment certification."""

import argparse
import json
import os
import sys
import psycopg
from psycopg.rows import dict_row
from mfdataindia.enrichment.amc_coverage import summary
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.rollout import Manifest, seal_evidence

if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        with psycopg.connect(
            pinned_dsn(manifest, os.environ, "mfdata_verified_reader"), row_factory=dict_row
        ) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            result = summary(conn)
        print(json.dumps(seal_evidence(manifest, "amc-coverage-inventory", result), sort_keys=True))
    except Exception as error:
        print("AMC inventory failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
