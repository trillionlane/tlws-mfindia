"""One reviewed pre-existing helper, never a blanket function exception."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mfdataindia.review import broad_reader_function_scope as scope
from mfdataindia.review import reader_function_attestation_inventory as inventory
from mfdataindia.review import reader_function_attestation_reference as reference
from mfdataindia.review import reader_function_attestation_spec as spec
from function_attestation_fixtures import context
from test_reader_access_correction import frozen_state
from test_reader_function_attestation_validation import fixture as attestation_document


def test_callee_seals_derive_from_exact_committed_migration_bytes():
    source = (Path(__file__).resolve().parents[1]
              / "sql/optional/023_verified_fund_review.sql").read_bytes()
    start = (b"CREATE FUNCTION mf_review.canonical_json(value jsonb) RETURNS text\n"
             b"LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,mf_review AS $$")
    end = b"\nCREATE FUNCTION mf_review.require_seal"
    assert source.count(start) == source.count(end) == 1
    statement = source[source.index(start):source.index(end)]
    body = statement.split(b" AS $$", 1)[1].rsplit(b"$$;", 1)[0]
    migration_spelling = json.dumps(
        ["search_path=pg_catalog,mf_review"], separators=(",", ":")).encode()
    catalog_spelling = json.dumps(
        ["search_path=pg_catalog, mf_review"], separators=(",", ":")).encode()
    assert hashlib.sha256(source).hexdigest() == scope.CALLEE_MIGRATION_SHA256
    assert (len(body), hashlib.sha256(body).hexdigest()) == (
        scope.CALLEE_BODY_BYTES, scope.CALLEE_BODY_SHA256)
    assert (len(migration_spelling), hashlib.sha256(migration_spelling).hexdigest()) == (
        36, "a2cac24529b7f82b6e5a6c1574baf25bcd24678b648faca2b3da9869f6925c46")
    assert (len(catalog_spelling), hashlib.sha256(catalog_spelling).hexdigest()) == (
        scope.CALLEE_CONFIG_BYTES, scope.CALLEE_CONFIG_SHA256)
    assert (scope.CALLEE_CONFIG_SHA256, scope.CALLEE_CONFIG_BYTES) == (
        scope.CALLEE_SOURCE_CONFIG_SHA256, scope.CALLEE_SOURCE_CONFIG_BYTES)


def _case():
    captured = {"context": context(), "attestation": attestation_document(),
                "completions": [{"id": name, "command_rows": 1,
                                 "driver_rows": 1, "fetched_rows": 1}
                                for name in inventory.SEQUENCE]}
    attestation = spec.result(captured)
    state = frozen_state()
    state["namespaces"].append({"oid": 41816, "nspname": "mf_fact_review", "owner": 16587,
                                "acl": [], "usage": True, "create_allowed": False})
    state["namespaces"].append({"oid": 16596, "nspname": "mf_review", "owner": 16587,
                                "acl": [], "usage": False, "create_allowed": False})
    state["roles"].append({"oid": 40033, "rolname": "mfdata_verified_admin"})
    state["functions"] = [{"oid": 41849, "pronamespace": 41816,
                           "proname": "dependencies", "proowner": 16587,
                           "prosecdef": True, "prokind": "f", "proargtypes": "23",
                           "prorettype": 3802,
                           "proconfig": {"is_null": False,
                                         "sha256": reference.EXPECTED_DECLARATION["configuration_sha256"],
                                         "utf8_bytes": reference.EXPECTED_DECLARATION["configuration_bytes"]},
                           "private_body_sha256": reference.BODY_SHA256,
                           "acl": [[16587, 16587, "EXECUTE", False],
                                   [16587, 40032, "EXECUTE", False],
                                   [16587, 40033, "EXECUTE", False]],
                           "execute_allowed": True}]
    state["functions"].append({"oid": 41900, "pronamespace": 16596,
                               "proname": "canonical_json", "proowner": 16587,
                               "prosecdef": False, "prokind": "f", "proargtypes": "3802",
                               "prorettype": 25,
                               "proconfig": {"is_null": False,
                                             "sha256": scope.CALLEE_CONFIG_SHA256,
                                             "utf8_bytes": scope.CALLEE_CONFIG_BYTES},
                               "private_body_sha256": scope.CALLEE_BODY_SHA256,
                               "acl": [[16587, 16587, "EXECUTE", False]],
                               "execute_allowed": False})
    return state, attestation


def test_exact_existing_helper_attestation_matches_catalogue_row():
    state, attestation = _case()
    assert scope.require_existing_helper(state, attestation) == 41849
    assert scope.require_existing_callee(state) == 41900


@pytest.mark.parametrize("change", ["body", "settings", "owner", "definer", "duplicate",
                                    "public_execute", "effective_execute"])
def test_unreviewed_callee_rejected(change):
    state, _ = _case()
    callee = state["functions"][1]
    if change == "body":
        callee["private_body_sha256"] = "0" * 64
    elif change == "settings":
        callee["proconfig"]["sha256"] = "0" * 64
    elif change == "owner":
        callee["proowner"] += 1
    elif change == "definer":
        callee["prosecdef"] = True
    elif change == "public_execute":
        callee["acl"].append([16587, 0, "EXECUTE", False])
    elif change == "effective_execute":
        callee["execute_allowed"] = True
    else:
        state["functions"].append(deepcopy(callee))
    with pytest.raises(scope.FunctionScopeError):
        scope.require_existing_callee(state)


@pytest.mark.parametrize("change", ["second", "body", "settings", "acl", "owner",
                                    "extra_key", "function"])
def test_drift_or_second_privileged_helper_rejected(change):
    state, attestation = _case()
    if change == "second":
        other = deepcopy(state["functions"][0])
        other["oid"] += 1
        state["functions"].append(other)
    elif change == "body":
        state["functions"][0]["private_body_sha256"] = "0" * 64
    elif change == "settings":
        state["functions"][0]["proconfig"]["sha256"] = "0" * 64
    elif change == "acl":
        state["functions"][0]["acl"].append([16587, 0, "EXECUTE", False])
    elif change == "owner":
        state["functions"][0]["proowner"] += 1
    elif change == "extra_key":
        state["functions"][0]["unreviewed"] = "value"
    else:
        attestation["reference_comparison"]["declaration_matches_committed_reference"] = False
    with pytest.raises((scope.FunctionScopeError, ValueError)):
        scope.require_existing_helper(state, attestation)
