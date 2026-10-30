"""Conservative full grant-certificate capacity before opening an owner DSN.

The exact frozen plan determines every possible lock row identity. The witness
may additionally contain AccessShareLock on every locked table, so this builds
that maximum rather than assuming the successful small fixture. No grant is
allowed if the full maximal certificate cannot be transported unchanged.
"""

from copy import deepcopy
import json

from mfdataindia.review import broad_reader_direct_delivery_spec as delivery
from mfdataindia.review import broad_reader_policy as policy


MAX_SAFE_RAW_CERTIFICATE_BYTES = 2_500_000
MAX_PID_DIGITS = 20  # Deliberately larger than PostgreSQL backend PID width.
_MAX_PID = int("9" * MAX_PID_DIGITS)
_MAX_DATABASE_OID = 4294967295
_HASH = "f" * 64


class CapacityError(ValueError):
    """The complete postcommit proof might exceed bounded log transport."""


def _require(condition):
    if not condition:
        raise CapacityError("UNREPORTABLE_MAXIMAL_DIRECT_GRANT_PROOF")


def _lock(row, mode):
    return {"pid": _MAX_PID, "database": _MAX_DATABASE_OID,
            "oid": row["oid"], "nspname": row["nspname"],
            "relname": row["relname"], "relkind": row["relkind"],
            "mode": mode, "granted": True}


def maximal_grant_receipt(document):
    """Overapproximate the actual direct.run success receipt by full bytes."""
    _require(type(document) is dict and type(document.get("before_snapshot")) is dict)
    plan = document["before_snapshot"].get("plan")
    _require(type(plan) is dict and type(plan.get("relation_scope")) is dict
             and type(plan.get("delta")) is dict)
    classification = plan["relation_scope"]
    roots = classification.get("grant_roots")
    leaves = classification.get("lock_only_leaves")
    unlocked = classification.get("materialized_unlocked")
    _require(type(roots) is list and type(leaves) is list
             and type(unlocked) is list and len(roots) > 0 and len(leaves) == 3)
    _require(all(type(row) is dict
                 and type(row.get("oid")) is int and row["oid"] > 0
                 and type(row.get("nspname")) is str
                 and type(row.get("relname")) is str
                 and row.get("relkind") in ("r", "p", "v", "m")
                 for row in roots + leaves))
    tables = [_lock(row, "AccessExclusiveLock") for row in roots
              if row["relkind"] in ("r", "p")]
    views = [_lock(row, "AccessShareLock") for row in roots
             if row["relkind"] == "v"]
    leaf_rows = [_lock(row, "AccessShareLock") for row in leaves]
    overlap = [_lock(row, "AccessShareLock") for row in roots
               if row["relkind"] in ("r", "p")]
    final = tables + views + leaf_rows + overlap
    phase_added = views + leaf_rows + overlap
    effective = [{"oid": row["oid"], "mode": "AccessExclusiveLock"}
                 for row in roots if row["relkind"] in ("r", "p")]
    effective += [{"oid": row["oid"], "mode": "AccessShareLock"}
                  for row in roots if row["relkind"] == "v"]
    effective += [{"oid": row["oid"], "mode": "AccessShareLock"}
                  for row in leaves]
    lock_proof = {
        "format": "mf-broad-reader-lock-phases-v1-nonexecutable",
        "target_pid": _MAX_PID, "observer_pid": int("8" * MAX_PID_DIGITS),
        "database_oid": _MAX_DATABASE_OID, "baseline": [],
        "tables": tables, "views": final, "phase_2_added": phase_added,
        "effective": effective, "per_view_provenance_claim": False,
        "grant_authorized": False}
    return {
        "format": "mf-broad-reader-direct-transaction-receipt-v1",
        "stage": "COMMITTED_STATE_VERIFIED",
        "request_sha256": _HASH, "before_sha256": _HASH,
        "after_sha256": _HASH, "source_sha256": _HASH,
        "lock_proof": lock_proof,
        "pregrant_equality": {
            "format": "mf-broad-reader-pregrant-snapshot-equality-v1",
            "before_sha256": _HASH, "after_sha256": _HASH,
            "grant_authorized": False},
        "postgrant_proof": {
            "format": "mf-broad-reader-postgrant-full-state-v1",
            "before_sha256": _HASH, "after_sha256": _HASH,
            "planned_after_sha256": _HASH, "permission_enabled": False},
        "delta": deepcopy(plan["delta"]),
        "materialized_unlocked": deepcopy(unlocked),
        "postcommit_permanence_guarantee": False,
        "authenticated_reader_verified": False, "ti_verified": False,
        "permission_enabled": False,
    }


def prove_capacity(document, *, payload_sha256):
    """Proof must pass before owner connection or any possibility of COMMIT."""
    receipt = maximal_grant_receipt(document)
    proof = {"format": "mf-broad-reader-direct-capacity-v1-nonexecutable",
             "maximal_receipt_sha256": policy.fingerprint(receipt),
             "maximal_raw_bytes": MAX_SAFE_RAW_CERTIFICATE_BYTES,
             "grant_authorized": False}
    evidence = {"transaction_receipt": receipt, "capacity_proof": proof}
    certificate = delivery.seal_certificate(
        payload_sha256=payload_sha256, stage="direct-grant-transaction",
        evidence=evidence)
    raw = json.dumps(certificate, sort_keys=True, separators=(",", ":")).encode()
    # A conservative bound also covers incompressible deflate output within
    # the existing 64 x 60000-character chunk transport (no limit increase).
    _require(len(raw) <= MAX_SAFE_RAW_CERTIFICATE_BYTES)
    return {**proof, "maximal_raw_bytes": len(raw)}
