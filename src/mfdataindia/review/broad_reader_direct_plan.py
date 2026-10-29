"""Pure broad-reader proposal, including unchanged pre-existing function state.

The historical planner rejects every reader-executable SECURITY DEFINER
function.  This successor permits only the separately attested existing helper;
it never proposes a function privilege change or executes SQL.  The live
capture, exact approval, guarded transaction and reader postcheck are separate.
"""

from copy import deepcopy

from psycopg import sql

from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_dependency_scope as dependencies
from mfdataindia.review import broad_reader_function_scope as functions


class PlanError(ValueError):
    """The exact existing DEV state cannot be represented by this plan."""


def _require(condition):
    if not condition:
        raise PlanError("INCOMPLETE_BROAD_READER_PROPOSAL")


def reversal_template(plan):
    """Prearmed exact-delta RESTRICT text, never runnable by this module."""
    _require(type(plan) is dict and type(plan.get("delta")) is dict)
    _require(set(plan["delta"]) == {"schemas", "relations", "defaults"})
    commands = []
    for item in reversed(plan["delta"]["defaults"]):
        commands.append(sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "REVOKE SELECT ON TABLES FROM {} RESTRICT"
        ).format(sql.Identifier(policy.OWNER), sql.Identifier(item["schema"]),
                 sql.Identifier(policy.READER)).as_string())
    for item in reversed(plan["delta"]["relations"]):
        commands.append(sql.SQL("REVOKE SELECT ON TABLE {}.{} FROM {} RESTRICT").format(
            sql.Identifier(item["schema"]), sql.Identifier(item["name"]),
            sql.Identifier(policy.READER)).as_string())
    for item in reversed(plan["delta"]["schemas"]):
        commands.append(sql.SQL("REVOKE USAGE ON SCHEMA {} FROM {} RESTRICT").format(
            sql.Identifier(item["schema"]), sql.Identifier(policy.READER)).as_string())
    return {"format": "mf-broad-reader-exact-delta-reversal-v1-not-executable",
            "grant_plan_sha256": policy.fingerprint(
                {key: value for key, value in plan.items() if key != "reversal_template"}),
            "expected_before_sha256": plan["full_after_sha256"],
            "expected_after_sha256": plan["full_before_sha256"],
            "delta": deepcopy(plan["delta"]), "sql_template": commands,
            "separate_exact_approval_required": True,
            "defaults_reversal_does_not_revoke_inherited_object_grants": True,
            "automatic_reversal": False}


def prepare(state, *, helper_attestation, candidates, system_leaves):
    """Build a full-state delta without treating a lock-only leaf as a grant.

    Removing the attested helper is a local input to the historical *pure*
    planner only.  The returned before/after contain its unmodified full row,
    and the future executor must compare those complete rows on every read.
    """
    _require(type(state) is dict)
    helper_oid = functions.require_existing_helper(state, helper_attestation)
    functions.require_existing_callee(state)
    closure = dependencies.classify(state, candidates, system_leaves=system_leaves)
    _require({n["nspname"] for n in state["namespaces"] if n["nspname"] in policy.SCHEMAS}
             == set(policy.SCHEMAS))

    raw_full_input_sha256 = policy.fingerprint(state)
    canonical_full_input = deepcopy(state)
    for default in canonical_full_input["defaults"]:
        default.pop("oid", None)  # The frozen catalogue's sole documented normalization.

    normalized = deepcopy(state)
    helper = next(f for f in normalized["functions"] if f["oid"] == helper_oid)
    normalized["functions"] = [f for f in normalized["functions"] if f["oid"] != helper_oid]
    normalized = policy.catalogue(normalized)  # Only default-ACL row OIDs are normalized.
    historical = policy.prepare(normalized)
    _require(historical["coverage"]["absent_schemas"] == [])
    _require(len(historical["coverage"]["present_schemas"]) == len(policy.SCHEMAS))

    before = deepcopy(historical["before"])
    after = deepcopy(historical["after"])
    for document in (before, after):
        document["functions"].append(deepcopy(helper))
        document["functions"].sort(key=lambda row: row["oid"])
    expected = deepcopy(normalized)
    expected["functions"].append(deepcopy(helper))
    expected["functions"].sort(key=lambda row: row["oid"])
    _require(expected == canonical_full_input and before == canonical_full_input)
    _require(before["functions"] == after["functions"])
    _require(not any("FUNCTION" in statement or "pg_catalog" in statement
                     for statement in historical["sql"]))
    _require({(item["schema"], item["name"]) for item in historical["delta"]["relations"]}
             <= {(r["nspname"], r["relname"]) for r in closure["grant_roots"]})

    plan = {"format": "mf-internal-broad-reader-direct-plan-v2-nonexecutable",
            "profile_sha256": historical["profile_sha256"],
            "before": before, "after": after,
            "raw_full_input_sha256": raw_full_input_sha256,
            "canonical_full_input_sha256": policy.fingerprint(canonical_full_input),
            "normalized_before_sha256": historical["before_sha256"],
            "normalized_after_sha256": historical["after_sha256"],
            "full_before_sha256": policy.fingerprint(before),
            "full_after_sha256": policy.fingerprint(after),
            "sql": deepcopy(historical["sql"]),
            "delta": deepcopy(historical["delta"]),
            "coverage": deepcopy(historical["coverage"]),
            "relation_scope": closure,
            "lock_sql": dependencies.lock_statements(closure),
            "helper_attestation_sha256": policy.fingerprint(helper_attestation),
            "full_callee_attestation": False,
            "source_only_candidate": True,
            "permission_acceptance": False,
            "standing_DEV_authorization_not_embedded": True,
            "fresh_exact_live_input_and_admission_required": True,
            "executed": False}
    plan["reversal_template"] = reversal_template(plan)
    return plan
