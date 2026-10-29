"""Pure check of the one pre-existing privileged reader function.

This does not authorize an EXECUTE grant or a broad-reader write.  A separate
callee/dependency attestation is required before a live proposal can use it.
"""

from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import reader_function_attestation_reference as reference
from mfdataindia.review import reader_function_attestation_spec as attestation_spec


class FunctionScopeError(ValueError):
    """Privileged function identity or declaration is not the reviewed one."""


def _require(condition):
    if not condition:
        raise FunctionScopeError("UNREVIEWED_PRIVILEGED_FUNCTION")


CALLEE_MIGRATION_SHA256 = "16e18cd8092fc12baa0216687ece92124f82953abb1b61b500128951e8dce0b6"
CALLEE_BODY_SHA256 = "9f6d6eb2b15070d79ad0e12052218cf9f914335c68c41dd28421b7d5acf444ba"
CALLEE_BODY_BYTES = 835
# The full source pg_proc seal is distinct from the frozen broad STATE_SQL
# projection, whose to_jsonb serialization has a different byte/hash value.
CALLEE_SOURCE_CONFIG_SHA256 = "c7db48fe507ed87f844868dba5cb1a12ad5a20481adfb34d288e4e213b54dc25"
CALLEE_SOURCE_CONFIG_BYTES = 37
CALLEE_CONFIG_SHA256 = "a2cac24529b7f82b6e5a6c1574baf25bcd24678b648faca2b3da9869f6925c46"
CALLEE_CONFIG_BYTES = 36
_FUNCTION_KEYS = frozenset({"oid", "pronamespace", "proname", "proowner", "prosecdef",
                            "prokind", "proargtypes", "prorettype", "proconfig",
                            "private_body_sha256", "acl", "execute_allowed"})


def require_existing_helper(state, attestation):
    """Match a fresh exact helper attestation to the full catalogue row."""
    attestation_spec.validate_capture_result(attestation)
    comparison = attestation["reference_comparison"]
    _require(all(comparison[k] is True for k in (
        "body_matches_committed_reference",
        "settings_match_committed_reference",
        "declaration_matches_committed_reference",
    )))
    function = attestation["attestation"]["functions"][0]
    _require(function["schema_usage"] is True)
    _require(type(state) is dict and type(state.get("functions")) is list)
    namespaces = state.get("namespaces")
    roles = state.get("roles")
    _require(type(namespaces) is list and type(roles) is list)
    reader = next((r for r in roles if r.get("rolname") == policy.READER), None)
    owner = next((r for r in roles if r.get("rolname") == policy.OWNER), None)
    namespace = next((n for n in namespaces if n.get("nspname") == "mf_fact_review"), None)
    _require(reader is not None and owner is not None and namespace is not None)
    _require(reader["oid"] == function["execute_acl_atoms"][1]["grantee_oid"])
    _require(owner["oid"] == function["owner_oid"] and namespace["oid"] == function["namespace_oid"])
    privileged = [f for f in state["functions"] if f.get("prosecdef") is True
                  and f.get("execute_allowed") is True]
    _require(len(privileged) == 1)
    row = privileged[0]
    _require(type(row) is dict and set(row) == _FUNCTION_KEYS)
    _require(row["oid"] == function["oid"] and row["pronamespace"] == namespace["oid"])
    _require(row["proname"] == "dependencies" and row["proowner"] == owner["oid"])
    _require(row["prokind"] == "f" and row["proargtypes"] == "23" and row["prorettype"] == 3802)
    _require(row["private_body_sha256"] == reference.BODY_SHA256)
    _require(row["proconfig"] == {"is_null": False,
                                  "sha256": reference.EXPECTED_DECLARATION["configuration_sha256"],
                                  "utf8_bytes": reference.EXPECTED_DECLARATION["configuration_bytes"]})
    _require(row["acl"] == [
        [owner["oid"], owner["oid"], "EXECUTE", False],
        [owner["oid"], reader["oid"], "EXECUTE", False],
        [owner["oid"], function["execute_acl_atoms"][2]["grantee_oid"], "EXECUTE", False],
    ])
    return row["oid"]


def require_existing_callee(state):
    """Pin the only user-defined callee's existing catalogue identity/seals.

    This is a necessary check, not sufficient execution or grant authority;
    the full live declaration and actual call graph require separate evidence.
    """
    _require(type(state) is dict and type(state.get("functions")) is list)
    namespaces = state.get("namespaces")
    roles = state.get("roles")
    _require(type(namespaces) is list and type(roles) is list)
    owner = next((r for r in roles if r.get("rolname") == policy.OWNER), None)
    namespace = next((n for n in namespaces if n.get("nspname") == "mf_review"), None)
    _require(owner is not None and namespace is not None)
    matching = [f for f in state["functions"] if f.get("pronamespace") == namespace["oid"]
                and f.get("proname") == "canonical_json" and f.get("proargtypes") == "3802"]
    _require(len(matching) == 1)
    row = matching[0]
    _require(set(row) == _FUNCTION_KEYS)
    _require(row["proowner"] == owner["oid"] and row["prokind"] == "f")
    _require(row["prosecdef"] is False and row["prorettype"] == 25)
    _require(row["private_body_sha256"] == CALLEE_BODY_SHA256)
    _require(row["proconfig"] == {"is_null": False, "sha256": CALLEE_CONFIG_SHA256,
                                  "utf8_bytes": CALLEE_CONFIG_BYTES})
    # This value must come from a fresh live state snapshot. The source
    # expectation alone is never substituted for the actual ACL/capability.
    _require(row["acl"] == [[owner["oid"], owner["oid"], "EXECUTE", False]])
    _require(row["execute_allowed"] is False)
    return row["oid"]
