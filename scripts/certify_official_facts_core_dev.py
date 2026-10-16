#!/usr/bin/env python3
"""Certify all 19 actual private core read routes with real nullable-quality evidence."""

import argparse
import json
import os
import sys
import psycopg
import requests
from psycopg.rows import dict_row
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.enrichment.process_core_certification import certify
from mfdataindia.review.rollout import Manifest, seal_evidence
from mfdataindia.review.privileges import verify_runtime

if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
        dsn = pinned_dsn(manifest, os.environ, "mfdata_verified_reader")

        def database():
            conn = psycopg.connect(dsn, row_factory=dict_row)
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            verify_runtime(conn, "api")
            return conn

        with database() as conn:
            dataset = conn.execute(
                "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
            ).fetchone()["dataset_version"]
            row = conn.execute(
                "SELECT f.amfi_scheme_code FROM mf.funds f WHERE NOT EXISTS(SELECT 1 FROM mf.nav_quality_assessments q WHERE q.amfi_scheme_code=f.amfi_scheme_code) ORDER BY f.amfi_scheme_code LIMIT 1"
            ).fetchone()
            if not row:
                raise ValueError(
                    "no real unassessed sample; never manufacture unknown/null evidence"
                )
            unassessed = row["amfi_scheme_code"]

        def options(codes):
            with database() as conn:
                return {
                    r["amfi_scheme_code"]: r["option_type"]
                    for r in conn.execute(
                        "SELECT amfi_scheme_code,option_type FROM mf.funds WHERE amfi_scheme_code=ANY(%s)",
                        (codes,),
                    )
                }

        with requests.Session() as session:
            session.trust_env = False
            result = certify(session, unassessed_code=unassessed, option_lookup=options)
        if result["provider_dataset_version"] != dataset:
            raise ValueError("actual provider HTTP dataset differs from the bound reader database")
        with database() as conn:
            if (
                conn.execute(
                    "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
                ).fetchone()["dataset_version"]
                != dataset
            ):
                raise ValueError(
                    "NAV dataset changed during core certification; obtain fresh evidence"
                )
        print(
            json.dumps(
                seal_evidence(
                    manifest,
                    "official-facts-core-http",
                    {"level": "DEV_PRIVATE_HTTP", "nav_dataset_version": dataset, **result},
                ),
                sort_keys=True,
            )
        )
    except Exception as error:
        print("official-facts core certification failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
