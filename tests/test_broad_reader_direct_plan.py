"""Pure successor plan preserves all function rows and grants only domain roots."""

from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_direct_plan as planner
from mfdataindia.review import broad_reader_policy as policy
from test_broad_reader_dependency_scope import _case as dependency_case
from test_broad_reader_function_scope import _case as function_case


def _case():
    state, attestation = function_case()
    owner = next(r for r in state["roles"] if r["rolname"] == policy.OWNER)
    admin = next(r for r in state["roles"] if r["rolname"] == "mfdata_verified_admin")
    admin.update({k: deepcopy(v) for k, v in owner.items() if k not in ("oid", "rolname")})
    used = {n["oid"] for n in state["namespaces"]}
    for i, name in enumerate(policy.SCHEMAS):
        if name not in {n["nspname"] for n in state["namespaces"]}:
            oid = 50000 + i
            assert oid not in used
            state["namespaces"].append({"oid": oid, "nspname": name, "owner": owner["oid"],
                                        "acl": [], "usage": False, "create_allowed": False})
    for group, rows in state.items():
        rows.sort(key=lambda row: tuple(row[k] for k in policy._SORT[group]))
    _, _, leaves = dependency_case()
    candidates = [{k: deepcopy(row[k]) for k in
                   ("oid", "nspname", "relname", "relkind", "owner", "definition")}
                  for row in state["relations"] if row["relkind"] in ("r", "p", "v", "m")]
    candidates += deepcopy(list(leaves.values()))
    return state, attestation, candidates, leaves


def test_full_state_plan_keeps_helper_callee_and_never_grants_catalogue():
    state, attestation, candidates, leaves = _case()
    original = deepcopy(state)
    plan = planner.prepare(state, helper_attestation=attestation,
                           candidates=candidates, system_leaves=leaves)
    assert state == original and plan["before"] == original
    assert plan["before"]["functions"] == plan["after"]["functions"]
    assert plan["coverage"]["absent_schemas"] == []
    assert len(plan["coverage"]["present_schemas"]) == 16
    assert len(plan["delta"]["defaults"]) == 16
    assert {item["schema"] for item in plan["delta"]["defaults"]} == set(policy.SCHEMAS)
    assert plan["standing_DEV_authorization_not_embedded"] is True
    assert plan["fresh_exact_live_input_and_admission_required"] is True
    assert plan["executed"] is False
    assert plan["source_only_candidate"] is True
    assert plan["full_callee_attestation"] is plan["permission_acceptance"] is False
    assert all("pg_catalog" not in command and "FUNCTION" not in command
               for command in plan["sql"])
    assert {r["nspname"] for r in plan["relation_scope"]["lock_only_leaves"]} == {"pg_catalog"}
    assert plan["format"] == "mf-internal-broad-reader-direct-plan-v2-nonexecutable"
    assert policy.fingerprint(original) == plan["raw_full_input_sha256"]
    assert policy.fingerprint(original) == plan["canonical_full_input_sha256"]
    assert policy.fingerprint(plan["before"]) == plan["full_before_sha256"]
    assert policy.fingerprint(plan["after"]) == plan["full_after_sha256"]
    normalized = deepcopy(original)
    normalized["functions"] = [f for f in normalized["functions"]
                               if f["proname"] != "dependencies"]
    historical = policy.prepare(normalized)
    assert plan["normalized_before_sha256"] == historical["before_sha256"]
    assert plan["normalized_after_sha256"] == historical["after_sha256"]
    assert (plan["sql"], plan["delta"], plan["coverage"], plan["profile_sha256"]) == (
        historical["sql"], historical["delta"], historical["coverage"],
        historical["profile_sha256"])
    assert plan["normalized_before_sha256"] != plan["full_before_sha256"]
    reversal = plan["reversal_template"]
    assert reversal["automatic_reversal"] is False
    assert reversal["separate_exact_approval_required"] is True
    assert reversal["expected_before_sha256"] == plan["full_after_sha256"]
    assert reversal["expected_after_sha256"] == plan["full_before_sha256"]
    assert all(statement.endswith(" RESTRICT") for statement in reversal["sql_template"])
    assert len(reversal["sql_template"]) == len(plan["sql"])


def test_existing_default_acl_row_oid_is_normalized_only_like_historical_planner():
    state, attestation, candidates, leaves = _case()
    state["defaults"].append({"oid": 91000, "defaclrole": 16587,
                              "defaclnamespace": 16595, "defaclobjtype": "r", "acl": []})
    plan = planner.prepare(state, helper_attestation=attestation,
                           candidates=candidates, system_leaves=leaves)
    assert state["defaults"][0]["oid"] == 91000
    assert all("oid" not in row for row in plan["before"]["defaults"])
    assert plan["before"]["functions"] == plan["after"]["functions"]
    assert plan["raw_full_input_sha256"] == policy.fingerprint(state)
    canonical_full = deepcopy(state)
    for row in canonical_full["defaults"]:
        row.pop("oid", None)
    assert plan["before"] == canonical_full
    assert plan["canonical_full_input_sha256"] == policy.fingerprint(canonical_full)
    assert plan["full_before_sha256"] == policy.fingerprint(canonical_full)
    assert plan["raw_full_input_sha256"] != plan["full_before_sha256"]


@pytest.mark.parametrize("already_granted", [False, True])
def test_complete_default_rows_compare_by_logical_key_after_physical_oid_order(already_granted):
    state, attestation, candidates, leaves = _case()
    assert state["defaults"] == []
    owner_oid = next(r["oid"] for r in state["roles"] if r["rolname"] == policy.OWNER)
    schema_oids = sorted(n["oid"] for n in state["namespaces"]
                         if n["nspname"] in policy.SCHEMAS)
    assert len(schema_oids) == len(policy.SCHEMAS)
    reader_oid = next(r["oid"] for r in state["roles"] if r["rolname"] == policy.READER)
    acl = [[owner_oid, reader_oid, "SELECT", False]] if already_granted else []
    state["defaults"] = [
        {"oid": 91000, "defaclrole": owner_oid,
         "defaclnamespace": schema_oids[-1], "defaclobjtype": "r", "acl": deepcopy(acl)},
        {"oid": 91001, "defaclrole": owner_oid,
         "defaclnamespace": schema_oids[0], "defaclobjtype": "r", "acl": deepcopy(acl)},
    ]
    original = deepcopy(state)
    plan = planner.prepare(state, helper_attestation=attestation,
                           candidates=candidates, system_leaves=leaves)
    assert state == original
    assert plan["raw_full_input_sha256"] == policy.fingerprint(original)
    canonical_full = deepcopy(original)
    for row in canonical_full["defaults"]:
        row.pop("oid")
    canonical_full["defaults"].sort(
        key=lambda row: (row["defaclrole"], row["defaclnamespace"],
                         row["defaclobjtype"]))
    assert plan["before"] == canonical_full
    assert plan["canonical_full_input_sha256"] == policy.fingerprint(canonical_full)
    assert plan["full_before_sha256"] == policy.fingerprint(canonical_full)
    assert plan["before"]["functions"] == plan["after"]["functions"]
    assert len(plan["delta"]["defaults"]) == len(policy.SCHEMAS) - (2 if already_granted else 0)
    assert all("FUNCTION" not in statement and "pg_catalog" not in statement
               for statement in plan["sql"])


@pytest.mark.parametrize("fault, fixed_error", [
    ("missing_key", "INVALID_BROAD_READER_INPUT"),
    ("duplicate_key", "DUPLICATE_CATALOGUE_IDENTITY"),
])
def test_default_row_sort_does_not_bypass_historical_validation(fault, fixed_error):
    state, attestation, candidates, leaves = _case()
    owner_oid = next(r["oid"] for r in state["roles"] if r["rolname"] == policy.OWNER)
    namespace_oid = next(n["oid"] for n in state["namespaces"]
                         if n["nspname"] == "mf")
    row = {"oid": 91000, "defaclrole": owner_oid,
           "defaclnamespace": namespace_oid, "defaclobjtype": "r", "acl": []}
    state["defaults"] = [row]
    if fault == "missing_key":
        row.pop("defaclrole")
    else:
        duplicate = deepcopy(row)
        duplicate["oid"] = 91001
        state["defaults"].append(duplicate)
    with pytest.raises(policy.PolicyError, match=fixed_error):
        planner.prepare(state, helper_attestation=attestation,
                        candidates=candidates, system_leaves=leaves)


@pytest.mark.parametrize("change", ["missing", "extra", "altered"])
def test_default_row_sort_never_hides_complete_row_changes(change):
    state, attestation, candidates, leaves = _case()
    owner_oid = next(r["oid"] for r in state["roles"] if r["rolname"] == policy.OWNER)
    reader_oid = next(r["oid"] for r in state["roles"] if r["rolname"] == policy.READER)
    namespace_oid = next(n["oid"] for n in state["namespaces"]
                         if n["nspname"] == "mf")
    state["defaults"] = [{"oid": 91000, "defaclrole": owner_oid,
                          "defaclnamespace": namespace_oid,
                          "defaclobjtype": "r", "acl": []}]
    original = planner.prepare(state, helper_attestation=attestation,
                               candidates=candidates, system_leaves=leaves)
    changed = deepcopy(state)
    if change == "missing":
        changed["defaults"].clear()
    elif change == "extra":
        changed["defaults"].append({"oid": 91001, "defaclrole": owner_oid,
                                    "defaclnamespace": namespace_oid,
                                    "defaclobjtype": "S", "acl": []})
    else:
        changed["defaults"][0]["acl"] = [[owner_oid, reader_oid, "SELECT", False]]
    result = planner.prepare(changed, helper_attestation=attestation,
                             candidates=candidates, system_leaves=leaves)
    assert result["raw_full_input_sha256"] == policy.fingerprint(changed)
    assert result["raw_full_input_sha256"] != original["raw_full_input_sha256"]
    assert result["full_before_sha256"] != original["full_before_sha256"]
    assert result["before"]["defaults"] != original["before"]["defaults"]


@pytest.mark.parametrize("change", ["missing_schema", "function_drift", "outside_leaf"])
def test_incomplete_plan_fails_before_any_sql(change):
    state, attestation, candidates, leaves = _case()
    if change == "missing_schema":
        state["namespaces"].pop()
    elif change == "function_drift":
        state["functions"][0]["private_body_sha256"] = "0" * 64
    else:
        candidates.append({"oid": 90000, "nspname": "public", "relname": "outside",
                           "relkind": "r", "owner": 16587,
                           "definition": {"is_null": True, "sha256": None, "utf8_bytes": 0}})
    with pytest.raises((planner.PlanError, ValueError)):
        planner.prepare(state, helper_attestation=attestation,
                        candidates=candidates, system_leaves=leaves)
