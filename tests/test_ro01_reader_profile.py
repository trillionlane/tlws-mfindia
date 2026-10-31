"""RO-01 policy selection is a trusted composition choice, not an HTTP claim."""

import pytest
from copy import deepcopy
from types import SimpleNamespace
from psycopg.pq import TransactionStatus

from mfdataindia.review import broad_reader_policy as planner
from mfdataindia.review import reader_access_profile as profiles


def test_broad_contract_is_the_existing_planner_contract():
    assert profiles.contract(profiles.BROAD) == planner.profile()
    assert profiles.contract(profiles.RESTRICTED)["source_content_select"] is False
    assert profiles.source_read_expected(planner.READER, profiles.BROAD) is True
    assert profiles.source_read_expected(planner.READER, profiles.RESTRICTED) is False
    assert profiles.source_read_expected("mfdata_verified_generator", profiles.RESTRICTED) is True


@pytest.mark.parametrize("value", [None, True, {}, "broad", "caller-approved", ""])
def test_unknown_or_incomplete_profiles_fail_without_value_echo(value):
    with pytest.raises(ValueError, match="^INVALID_READER_ACCESS_PROFILE$"):
        profiles.contract(value)


@pytest.mark.parametrize("principal", ["postgres", "mfdata_admin", "mfdata_verified_admin",
                                        "mfdata_verified_generator", "untrusted"])
def test_broad_selection_cannot_give_another_identity_reader_powers(principal):
    with pytest.raises(ValueError, match="^READER_PROFILE_IDENTITY_MISMATCH$"):
        profiles.source_read_expected(principal, profiles.BROAD)


def test_shared_prepare_uses_actual_planner_exact_plan_and_does_not_mutate_input():
    from test_broad_reader_policy import state
    before = state()
    saved = deepcopy(before)
    assert profiles.prepare(before) == planner.prepare(before)
    assert before == saved


def test_reconciliation_uses_existing_canonical_defaults_but_keeps_raw_oid_evidence():
    from test_broad_reader_policy import state
    canonical = profiles.prepare(state())["after"]
    raw = deepcopy(canonical)
    assert raw["defaults"]
    for index, row in enumerate(raw["defaults"]):
        row["oid"] = 900000 + index
    saved = deepcopy(raw)
    assert raw != canonical
    assert profiles.prepare(raw)["before"] == canonical
    assert raw == saved  # Existing planner does not discard the caller's raw OIDs.
    for group in raw.keys() - {"defaults"}:
        assert raw[group] == canonical[group]
    drifted = deepcopy(raw)
    drifted["namespaces"][0]["oid"] += 1
    with pytest.raises(profiles.ReaderAccessError):
        profiles.prepare(drifted)


@pytest.mark.parametrize("change", ["grant_option", "write", "membership", "schema_create",
                                    "database_create", "wrong_database", "missing_roles"])
def test_current_profile_rejects_unsafe_or_incomplete_effective_state(change):
    from test_broad_reader_policy import state
    value = state()
    reader = next(r["oid"] for r in value["roles"] if r["rolname"] == planner.READER)
    if change == "grant_option":
        value["namespaces"][0]["acl"].append([16587, reader, "USAGE", True])
    elif change == "write":
        value["columns"][0]["capabilities"][1] = True
    elif change == "membership":
        value["memberships"].append(dict(roleid=16587, member=reader, grantor=16587,
                                          admin_option=False, inherit_option=False, set_option=True))
    elif change == "schema_create":
        value["namespaces"][0]["create_allowed"] = True
    elif change == "database_create":
        value["databases"][0]["create_allowed"] = True
    elif change == "wrong_database":
        value["databases"][0]["datname"] = "unrelated"
    else:
        value["roles"] = []
    with pytest.raises(profiles.ReaderAccessError):
        profiles.prepare(value)


def test_install_requires_callers_existing_transaction_before_any_statement():
    conn = SimpleNamespace(info=SimpleNamespace(transaction_status=TransactionStatus.IDLE),
                           execute=lambda *_: pytest.fail("SQL outside install transaction"))
    with pytest.raises(profiles.ReaderAccessError, match="INSTALL_TRANSACTION_REQUIRED"):
        profiles.reconcile(conn, profiles.BROAD)


def test_untrusted_factory_profile_is_rejected_before_pool_or_database():
    from mfdataindia.review.app import create_review_app
    with pytest.raises(profiles.ReaderAccessError, match="INVALID_READER_ACCESS_PROFILE"):
        create_review_app("postgresql://fixture@127.0.0.1:1/fixture", reader_profile={"broad": True})


@pytest.mark.parametrize("change", ["core_broad", "future_owner", "future_other_creator",
                                    "unknown_relation", "raw_column"])
def test_restricted_profile_rejects_complete_core_and_future_read_drift(change):
    from test_broad_reader_policy import state
    value = state()
    profiles.verify_restricted_catalogue(value)
    if change == "core_broad":
        value = planner.prepare(value)["after"]
    elif change.startswith("future"):
        value["defaults"].append(dict(oid=123, defaclrole=16587 if change == "future_owner" else 49999,
                                      defaclnamespace=16595, defaclobjtype="r",
                                      acl=[[16587, 40032, "SELECT", False]]))
    elif change == "unknown_relation":
        value["relations"][0]["relname"] = "unadopted"
        value["relations"][0]["capabilities"][0] = True
    else:
        value["columns"][-1]["capabilities"][0] = True
    with pytest.raises(profiles.ReaderAccessError, match="READER_PROFILE_SELECTION_CONFLICT"):
        profiles.verify_restricted_catalogue(value)


@pytest.mark.parametrize("schema", ["mf_multi_facts", "mf_book_facts"])
def test_supported_python_installer_prior_views_remain_restricted_reads(schema):
    from test_broad_reader_policy import state
    value = state()
    relation = value["relations"][0]
    relation.update(nspname=schema, relname="prior_field_versions")
    relation["capabilities"][0] = True
    profiles.verify_restricted_catalogue(value)
    relation["relname"] = "unadopted_view"
    with pytest.raises(profiles.ReaderAccessError, match="READER_PROFILE_SELECTION_CONFLICT"):
        profiles.verify_restricted_catalogue(value)


@pytest.mark.parametrize("mutation", ["body", "configuration", "acl"])
def test_restricted_helper_exception_requires_actual_committed_seals(mutation):
    from test_broad_reader_function_scope import _case
    value, _ = _case()
    profiles.verify_restricted_catalogue(value)
    helper = value["functions"][0]
    if mutation == "body":
        helper["private_body_sha256"] = "0" * 64
    elif mutation == "configuration":
        helper["proconfig"]["sha256"] = "0" * 64
    else:
        helper["acl"][1][3] = True
    with pytest.raises(profiles.ReaderAccessError, match="UNREVIEWED_READER_FUNCTION"):
        profiles.verify_restricted_catalogue(value)


@pytest.mark.parametrize("field,value", [("volatility", "v"), ("strict", False),
    ("language", "unreviewed"), ("language_trusted", False), ("leakproof", True),
    ("support_oid", 1), ("parallel", "s"), ("argument_default_count", 1),
    ("cost", "101"), ("rows", "1"), ("definition", {"is_null": False, "sha256": "0" * 64, "utf8_bytes": 1})])
def test_complete_callee_declaration_cannot_drift_under_restricted_profile(field, value):
    from test_broad_reader_function_closure import _case
    from mfdataindia.review import broad_reader_function_closure as closure
    state, rows = _case()
    closure.verify_profile_helpers(rows, state, restricted=True, callee_execute_allowed=True)
    rows[1][field] = value
    with pytest.raises(closure.FunctionClosureError):
        closure.verify_profile_helpers(rows, state, restricted=True, callee_execute_allowed=True)


@pytest.mark.parametrize("owner", ["helper", "callee", "unreviewed_helper", "unreviewed_callee"])
def test_legacy_helper_and_callee_owners_are_independent_bounded_and_callable(owner):
    from test_broad_reader_function_closure import _case
    from mfdataindia.review import broad_reader_function_closure as closure
    state, rows = _case()
    index = 0 if owner.endswith("helper") else 1
    name = "unknown_owner" if owner.startswith("unreviewed") else "postgres"
    state["roles"].append({"rolname": name, "oid": 10})
    namespace = rows[index]["schema"]
    next(n for n in state["namespaces"] if n["nspname"] == namespace)["owner"] = 10
    row = rows[index]
    row["owner_oid"] = 10
    row["acl"] = sorted([[10, atom[1] if atom[1] != 16587 else 10, atom[2], atom[3]] for atom in row["acl"]])
    state["functions"][index]["proowner"] = 10
    state["functions"][index]["acl"] = deepcopy(row["acl"])
    if owner.startswith("unreviewed"):
        with pytest.raises(closure.FunctionClosureError):
            closure.verify_profile_helpers(rows, state, restricted=True, callee_execute_allowed=True)
    else:
        closure.verify_profile_helpers(rows, state, restricted=True, callee_execute_allowed=True)
        with pytest.raises(closure.FunctionClosureError):
            closure.verify_profile_helpers(rows, state, restricted=True, callee_execute_allowed=False)


def test_restricted_helper_requires_schema_usage_while_broad_planner_can_repair_it():
    from test_broad_reader_function_closure import _case
    from mfdataindia.review import broad_reader_function_closure as closure
    state, rows = _case()
    rows[0]["reader_schema_usage"] = False
    next(n for n in state["namespaces"] if n["nspname"] == "mf_fact_review")["usage"] = False
    with pytest.raises(closure.FunctionClosureError):
        closure.verify_profile_helpers(rows, state, restricted=True, callee_execute_allowed=True)
    closure.verify_profile_helpers(rows, state, restricted=False, callee_execute_allowed=True)
