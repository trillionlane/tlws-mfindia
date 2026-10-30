"""Complete fixed read-only inputs for the DEV broad-reader decision.

The caller owns the bounded owner/observer connections and transaction.  This
module accepts no caller SQL, target names, or grant authority.  Its result is
an exact comparison input, never a permission receipt.
"""

from copy import deepcopy

from mfdataindia.review import broad_reader_capture_inventory as inventory
from mfdataindia.review import broad_reader_dependency_scope as dependencies
from mfdataindia.review import broad_reader_direct_plan as direct_plan
from mfdataindia.review import broad_reader_function_closure as functions
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review import broad_reader_view_profile as views
from mfdataindia.review.evidence_transport import MAX_RAW_BYTES
from mfdataindia.review.policy import canonical


class SnapshotError(ValueError):
    """The full fresh reader decision cannot be established."""


def _require(condition):
    if not condition:
        raise SnapshotError("INCOMPLETE_BROAD_READER_LIVE_SNAPSHOT")


def _rows(conn, statement, columns):
    with conn.execute(statement, prepare=False) as cursor:
        _require([c.name for c in cursor.description] == columns)
        rows = [dict(row) for row in cursor.fetchall()]
        _require(type(cursor.rowcount) is int and cursor.rowcount == len(rows))
        _require(cursor.statusmessage == "SELECT " + str(len(rows)))
        return rows


def canonical_state(state):
    """Normalize only default row OIDs and their complete logical ordering."""
    _require(type(state) is dict and type(state.get("defaults")) is list)
    value = deepcopy(state)
    required = {"defaclrole", "defaclnamespace", "defaclobjtype", "acl"}
    for row in value["defaults"]:
        _require(type(row) is dict and set(row) in (required, required | {"oid"}))
        _require(type(row["defaclrole"]) is int
                 and type(row["defaclnamespace"]) is int
                 and type(row["defaclobjtype"]) is str
                 and type(row["acl"]) is list)
        if "oid" in row:
            _require(type(row["oid"]) is int)
        row.pop("oid", None)
    value["defaults"].sort(key=lambda row: (
        row["defaclrole"], row["defaclnamespace"], row["defaclobjtype"]))
    return value


def read(conn, *, packaged, helper_attestation):
    """Read and validate all bounded metadata on an existing observer backend.

    This deliberately does not open/close a connection or start a transaction.
    The caller must bind its read-only status, identity, wall, and lifecycle.
    """
    state_rows = _rows(conn, policy.STATE_SQL, ["value"])
    _require(len(state_rows) == 1 and type(state_rows[0]["value"]) is dict)
    state = state_rows[0]["value"]
    candidates = _rows(conn, inventory.CANDIDATE_SQL,
                       inventory.CANDIDATE_COLUMNS)
    leaves = _rows(conn, dependencies.SYSTEM_LEAVES_SQL,
                   ["oid", "nspname", "relname", "relkind", "owner", "definition"])
    _require(len(leaves) == len(dependencies.SYSTEM_LEAF_NAMES))
    leaf_by_name = {row["relname"]: row for row in leaves}
    _require(len(leaf_by_name) == len(leaves)
             and set(leaf_by_name) == dependencies.SYSTEM_LEAF_NAMES)
    function_rows = _rows(conn, functions.FUNCTION_SQL,
                          functions.FUNCTION_COLUMNS)
    ledger = _rows(conn, source.LEDGER_SQL,
                   ["migration_name", "content_sha256"])

    migration_proof = source.verify_live_ledger(ledger, packaged)
    function_proof = functions.verify(function_rows, state)
    profile = views.select(
        state, candidates,
        base_authority_sha256=packaged["base_sha256"],
        variant_authority_sha256=packaged["variant_sha256"])
    plan = direct_plan.prepare(
        state, helper_attestation=helper_attestation,
        candidates=candidates + leaves, system_leaves=leaf_by_name)
    result = {"format": "mf-broad-reader-live-snapshot-v1-nonexecutable",
              "state": deepcopy(state), "candidates": deepcopy(candidates),
              "system_leaves": deepcopy(leaves),
              "function_rows": deepcopy(function_rows), "ledger": deepcopy(ledger),
              "migration_proof": migration_proof, "function_proof": function_proof,
              "view_profile": profile, "plan": plan,
              "grant_authorized": False, "permission_enabled": False}
    _require(len(canonical(result).encode()) <= MAX_RAW_BYTES)
    return result


def compare(before, after):
    """Require byte-equivalent complete pre-GRANT reread, no field masking."""
    _require(type(before) is dict and type(after) is dict)
    _require(before == after)
    return {"format": "mf-broad-reader-pregrant-snapshot-equality-v1",
            "before_sha256": policy.fingerprint(before),
            "after_sha256": policy.fingerprint(after),
            "grant_authorized": False}


def compare_post(before, after):
    """Admit only the exact planned policy delta and unchanged other evidence.

    A newly granted schema USAGE legitimately changes a function's effective
    schema-usage projection; every actual function definition, ACL, source
    file, candidate relation and migration row must remain exact.
    """
    _require(type(before) is dict and type(after) is dict)
    plan = before.get("plan")
    _require(type(plan) is dict
             and canonical_state(after.get("state")) == plan.get("after"))
    for key in ("candidates", "system_leaves", "ledger", "migration_proof",
                "view_profile"):
        _require(before.get(key) == after.get(key))
    old_functions = deepcopy(before.get("function_rows"))
    new_functions = deepcopy(after.get("function_rows"))
    _require(type(old_functions) is list and type(new_functions) is list
             and len(old_functions) == len(new_functions) == 2)
    for old, new in zip(old_functions, new_functions, strict=True):
        _require(type(old) is dict and type(new) is dict)
        old.pop("reader_schema_usage", None)
        new.pop("reader_schema_usage", None)
        _require(old == new)
    _require(after["plan"]["after"] == canonical_state(after["state"])
             and not any(after["plan"]["delta"].values()))
    return {"format": "mf-broad-reader-postgrant-full-state-v1",
            "before_sha256": policy.fingerprint(before["state"]),
            "after_sha256": policy.fingerprint(after["state"]),
            "planned_after_sha256": plan["full_after_sha256"],
            "permission_enabled": False}
