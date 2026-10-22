#!/usr/bin/env python3
"""Fixed catalogue-only reader-permission diagnostic; never a query retry or grant."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import sys

import psycopg
from psycopg.rows import dict_row

from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review import core_permission_spec as spec
from mfdataindia.review.evidence_transport import render_evidence
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import Manifest, seal_evidence

DIAGNOSTIC_ID = spec.IDENTITY
PHASES = {"binding", "connect", "transaction_bounds", "context_before", "role_catalogue",
          "schema_catalogue", "relation_catalogue", "context_after", "seal"}


class ProbeFailure(Exception):
    def __init__(self, phase, error_type):
        if phase not in PHASES or not re.fullmatch(r"[A-Za-z0-9_]{1,80}", error_type):
            raise ValueError("invalid safe diagnostic error marker")
        self.phase, self.error_type = phase, error_type
        super().__init__(phase + ":" + error_type)


def stage(phase, action):
    try:
        return action()
    except ProbeFailure:
        raise
    except Exception as error:
        raise ProbeFailure(phase, type(error).__name__) from None


def worker_binding(manifest, environment):
    program = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    specification = hashlib.sha256(Path(spec.__file__).read_bytes()).hexdigest()
    if (manifest.rollout_id != DIAGNOSTIC_ID or manifest.cohort != [154117]
            or manifest.canary_code != 154117
            or environment.get("DEPLOYMENT_GIT_SHA") != manifest.git_sha
            or environment.get("CORE_PERMISSION_PROGRAM_SHA256") != program
            or environment.get("CORE_PERMISSION_SPEC_SHA256") != specification):
        raise ValueError("exact permission identity/Git/packaged worker/spec binding required")
    spec.verify_sources(Path(__file__).resolve().parents[1])
    return {"packaged_program_sha256": program, "packaged_spec_sha256": specification,
            "manifest_image": manifest.image, "manifest_git_sha": manifest.git_sha}


def one(conn, sql):
    rows = conn.execute(sql).fetchall()
    if len(rows) != 1:
        raise ValueError("one exact metadata context/role row required")
    return dict(rows[0])


def diagnose(conn):
    def bounds():
        for sql in spec.BOUNDS:
            conn.execute(sql)
    stage("transaction_bounds", bounds)
    before = stage("context_before", lambda: one(conn, spec.CONTEXT_SQL))
    stage("context_before", lambda: spec.validate_context(before))
    role = stage("role_catalogue", lambda: one(conn, spec.ROLE_SQL))
    stage("role_catalogue", lambda: spec.validate_role(role))
    rows = {}
    for phase, sql, bindings in spec.STATEMENTS[1:]:
        rows[phase] = stage(phase, lambda sql=sql, bindings=bindings:
                            [dict(r) for r in conn.execute(sql, bindings).fetchall()])
    after = stage("context_after", lambda: one(conn, spec.CONTEXT_SQL))
    stage("context_after", lambda: spec.validate_context(after))
    if before != after:
        raise ProbeFailure("context_after", "ValueError")
    catalogue = {"context": before, "role": role, "schemas": rows["schema_catalogue"],
                 "relations": rows["relation_catalogue"], "specification_hash": fingerprint(spec.specification())}
    stage("seal", lambda: spec.validate_catalogue(catalogue))
    return {"level": "CURRENT_CATALOGUE_PERMISSIONS_NOT_HISTORICAL_CAUSALITY",
            "catalogue": catalogue, "catalogue_hash": fingerprint(catalogue),
            "source_hashes": spec.SOURCE_HASHES, "historical_denied_statement_proved": False,
            "performance_acceptance": False, "rollout_acceptance": False,
            "application_queries_executed": 0, "application_functions_executed": 0,
            "data_writes": 0, "source_requests": 0, "human_events_created": 0,
            "permission_changes": 0, "role_changes": 0}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    manifest = stage("binding", lambda: Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]))
    binding = stage("binding", lambda: worker_binding(manifest, os.environ))
    dsn = stage("binding", lambda: pinned_dsn(manifest, os.environ, "mfdata_verified_reader"))
    conn = stage("connect", lambda: psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5))
    with conn:
        result = {**diagnose(conn), "worker_binding": binding}
    stage("seal", lambda: spec.validate_result(result))
    print(render_evidence(seal_evidence(manifest, spec.STAGE, result)), flush=True)


if __name__ == "__main__":
    try:
        main()
    except ProbeFailure as error:
        print("core permission diagnostic failed: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print("core permission diagnostic failed: seal:" + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
