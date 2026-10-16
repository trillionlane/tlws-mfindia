#!/usr/bin/env python3
"""Report real pilot coverage; read-only, not a rollout/scheduler/HTTP certificate."""

import argparse
import json
import os
import sys
import psycopg
from psycopg.rows import dict_row
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.enrichment.process_inventory import inventory
from mfdataindia.review.rollout import Manifest, seal_evidence

if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        with psycopg.connect(
            pinned_dsn(manifest, os.environ, "mfdata_verified_reader"), row_factory=dict_row
        ) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            result = inventory(conn)
        print(
            json.dumps(
                seal_evidence(manifest, "official-facts-process-inventory", result), sort_keys=True
            )
        )
    except Exception as error:
        print("official-facts inventory failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
