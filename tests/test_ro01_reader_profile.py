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
