#!/usr/bin/env python3
"""Collect fixed two-root catalogue metadata once; no application rows or grants."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import sys
import time

import psycopg
from psycopg.rows import dict_row

from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review import core_access_metadata_inventory as frozen
from mfdataindia.review import core_access_metadata_spec as spec
from mfdataindia.review.evidence_transport import render_evidence
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import Manifest, seal_evidence

PHASES = {"binding", "connect", "transaction_bounds", "context_before", "context_after", "rollback", "seal"}
PHASES |= {d["id"] for d in frozen.inventory()["statements"]}


class ProbeFailure(Exception):
    def __init__(self, phase, error_type):
        if phase not in PHASES or not re.fullmatch(r"[A-Za-z0-9_]{1,80}", error_type):
            raise ValueError("invalid safe metadata error marker")
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
    paths = {"PROGRAM": Path(__file__).resolve(), "SPEC": Path(spec.__file__).resolve(),
             "INVENTORY": Path(frozen.__file__).resolve()}
    actual = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}
    if (manifest.rollout_id != spec.IDENTITY or manifest.cohort != [154117]
            or manifest.canary_code != 154117 or environment.get("DEPLOYMENT_GIT_SHA") != manifest.git_sha
            or any(environment.get("CORE_ACCESS_METADATA_" + key + "_SHA256") != digest
                   for key, digest in actual.items())):
        raise ValueError("exact packaged metadata program/spec/inventory/Git binding required")
    # Installed package paths are deterministic; never import attribution targets.
    package = paths["SPEC"].parents[1]
    for name, expected in spec.SOURCE_HASHES.items():
        path = (package / name.removeprefix("src/mfdataindia/") if name.startswith("src/mfdataindia/")
                else paths["PROGRAM"].parents[1] / name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("frozen original attribution bytes differ")
    return {"packaged_program_sha256": actual["PROGRAM"], "packaged_spec_sha256": actual["SPEC"],
            "packaged_inventory_sha256": actual["INVENTORY"],
            "manifest_image": manifest.image, "manifest_git_sha": manifest.git_sha}


def collect(conn, *, clock=time.monotonic):
    """No caller SQL/targets, options, implicit BEGIN, PREPARE or retry."""
    spec.require(conn.autocommit is True)
    document = frozen.inventory()
    transaction = document["transaction"]
    deadline = clock() + transaction["application_wall_clock_transaction_deadline_seconds"]
    begun = False
    rollback_attempted = False
    completions = []

    def within_deadline():
        if clock() >= deadline:
            raise TimeoutError("fixed metadata transaction deadline exhausted")

    def control(sql, expected):
        within_deadline()
        with conn.execute(sql, prepare=False) as cursor:
            spec.require(cursor.statusmessage == expected)
        within_deadline()

    def query(definition):
        within_deadline()
        with conn.execute(definition["sql"], prepare=False) as cursor:
            spec.require([c.name for c in cursor.description] == list(definition["columns"]))
            rows = [dict(row) for row in cursor.fetchall()]
            match = re.fullmatch(r"SELECT ([0-9]+)", cursor.statusmessage or "")
            spec.require(match is not None and type(cursor.rowcount) is int)
            count = int(match.group(1))
            spec.require(count == cursor.rowcount == len(rows))
            completion = {"id": definition["id"], "command_rows": count,
                          "driver_rows": cursor.rowcount, "fetched_rows": len(rows)}
        within_deadline()
        spec.validate_rows(definition, rows)
        completions.append(completion)
        return rows

    try:
        for index, sql in enumerate(transaction["control_statements"]):
            if index == 0:
                begun = True  # Cleanup consumes once even if BEGIN I/O is ambiguous.
            stage("transaction_bounds", lambda sql=sql, index=index: control(sql, "BEGIN" if index == 0 else "SET"))
        before = stage("context_before", lambda: spec.validate_context(query(document["context"])))
        captures = []
        for _ in range(2):
            groups = {}
            for definition in document["statements"]:
                def capture(definition=definition):
                    rows = query(definition)
                    if definition["id"] == "M01_targets":
                        spec.require(len(rows) == 2 and all(r["reloid"] is not None and r["nspoid"] is not None for r in rows))
                    return spec.sealed_rows(definition, rows)
                groups[definition["id"]] = stage(definition["id"], capture)
            stage("seal", lambda: spec.validate_snapshot(groups, before))
            captures.append(groups)
        after = stage("context_after", lambda: spec.validate_context(query(document["context"])))
        stage("seal", lambda: spec.require(before == after and captures[0] == captures[1]))
        within_deadline()
        rollback_attempted = True
        stage("rollback", lambda: control(transaction["terminal_statement"], "ROLLBACK"))
    except Exception:
        if begun and not rollback_attempted:
            rollback_attempted = True
            # One driver cleanup only; no further diagnostic query or retry.
            try:
                with conn.execute(transaction["terminal_statement"], prepare=False) as cursor:
                    spec.require(cursor.statusmessage == "ROLLBACK")
            except Exception:
                pass  # Original safe error retained; connection is always closed by main.
        raise
    groups = captures[0]
    return {"level": "CURRENT_METADATA_ONLY_NOT_HISTORICAL_CAUSALITY", "context": before,
            "groups": groups, "completions": completions,
            "specification_hash": fingerprint(spec.specification()),
            "metadata_hash": fingerprint({"context": before, "groups": groups}),
            "source_hashes": spec.SOURCE_HASHES, "access_admission_blockers": spec.admission_blockers(groups),
            "permission_authority": False, "runtime_authority": False,
            "performance_acceptance": False, "rollout_acceptance": False}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    manifest = stage("binding", lambda: Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]))
    binding = stage("binding", lambda: worker_binding(manifest, os.environ))
    dsn = stage("binding", lambda: pinned_dsn(manifest, os.environ, "mfdata_verified_reader"))
    conn = stage("connect", lambda: psycopg.connect(
        dsn, row_factory=dict_row, connect_timeout=5, autocommit=True, prepare_threshold=None))
    try:
        result = {**collect(conn), "worker_binding": binding}
    finally:
        conn.close()
    stage("seal", lambda: spec.validate_result(result))
    # Entire result or terminal overflow, using the unchanged sealed transport.
    rendered = stage("seal", lambda: render_evidence(seal_evidence(manifest, spec.STAGE, result)))
    print(rendered, flush=True)


if __name__ == "__main__":
    try:
        main()
    except ProbeFailure as error:
        print("core access metadata failed: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print("core access metadata failed: seal:" + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
