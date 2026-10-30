"""The unmodified bounded log transport has a precommit maximal-size proof."""

from copy import deepcopy
import hashlib

import pytest

from mfdataindia.review import broad_reader_direct_capacity as capacity
from mfdataindia.review import broad_reader_direct_delivery_spec as delivery
from mfdataindia.review.policy import canonical


def _document(count=2):
    roots = [{"oid": i + 100, "nspname": "mf", "relname": f"fund_{i}",
              "relkind": "r" if i % 2 == 0 else "v"}
             for i in range(count)]
    leaves = [{"oid": 10000 + i, "nspname": "pg_catalog",
               "relname": name, "relkind": "r"}
              for i, name in enumerate(("pg_class", "pg_inherits", "pg_namespace"))]
    return {"before_snapshot": {"plan": {
        "relation_scope": {"grant_roots": roots, "lock_only_leaves": leaves,
                           "materialized_unlocked": []},
        "delta": {"schemas": [], "relations": roots,
                  "columns": [], "defaults": []}}}}


def test_maximal_receipt_contains_all_possible_phase_rows_and_existing_bounds():
    document = _document(8)
    result = capacity.prove_capacity(document, payload_sha256="a" * 64)
    receipt = capacity.maximal_grant_receipt(document)
    roots = document["before_snapshot"]["plan"]["relation_scope"]["grant_roots"]
    locked_tables = [row for row in roots if row["relkind"] == "r"]
    proof = receipt["lock_proof"]
    assert len(proof["tables"]) == len(locked_tables)
    assert len(proof["views"]) == 2 * len(locked_tables) + 4 + 3
    assert len(proof["phase_2_added"]) == len(locked_tables) + 4 + 3
    assert result["grant_authorized"] is False
    assert result["maximal_raw_bytes"] <= capacity.MAX_SAFE_RAW_CERTIFICATE_BYTES
    delivery.seal_certificate(payload_sha256="a" * 64,
                              stage="direct-grant-transaction", evidence=receipt)


def test_capacity_growth_fails_before_an_unreportable_commit():
    document = _document(2)
    document["before_snapshot"]["plan"]["delta"]["columns"] = [
        hashlib.sha256(str(i).encode()).hexdigest() for i in range(90000)]
    with pytest.raises((capacity.CapacityError, ValueError)):
        capacity.prove_capacity(document, payload_sha256="a" * 64)


def test_maximal_proof_does_not_mutate_reviewed_plan():
    document = _document(3)
    old = deepcopy(document)
    capacity.prove_capacity(document, payload_sha256="a" * 64)
    assert canonical(document) == canonical(old)
