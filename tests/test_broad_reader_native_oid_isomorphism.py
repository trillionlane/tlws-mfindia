"""Provider-free adversarial checks for the disposable native OID renaming."""

from copy import deepcopy

import pytest

from broad_reader_native_oid_isomorphism import validate
from function_attestation_fixtures import context
from mfdataindia.review import reader_function_attestation_inventory as inventory
from mfdataindia.review import reader_function_attestation_spec as spec
from mfdataindia.review import broad_reader_function_scope as scope
from mfdataindia.review.policy import fingerprint
from test_broad_reader_function_scope import _case as function_case
from test_reader_function_attestation_validation import fixture as attestation_document


def _native_case():
    captured = {"context": context(), "attestation": attestation_document(),
                "completions": [{"id": name, "command_rows": 1,
                                 "driver_rows": 1, "fetched_rows": 1}
                                for name in inventory.SEQUENCE]}
    value = spec.result(captured)
    doc = value["attestation"]
    role_map = {16587: 70001, 40032: 70002, 40033: 70003}
    schema_map = {row["oid"]: row["oid"] + 100000 for row in doc["schemas"]}
    doc["reader"][0]["oid"] = role_map[40032]
    for row in doc["schemas"]:
        row["oid"] = schema_map[row["oid"]]
    function = doc["functions"][0]
    function["oid"] = 70004
    function["namespace_oid"] = schema_map[41816]
    function["owner_oid"] = role_map[16587]
    for atom in function["execute_acl_atoms"]:
        atom["grantor_oid"] = role_map[atom["grantor_oid"]]
        atom["grantee_oid"] = role_map[atom["grantee_oid"]]
    value["capture_sha256"] = fingerprint(
        {key: value[key] for key in ("context", "attestation", "completions")})
    return value, role_map, schema_map


def test_only_oid_copy_is_normalized_and_original_remains_unchanged():
    value, _, _ = _native_case()
    original = deepcopy(value)
    with pytest.raises(ValueError, match="INVALID_FUNCTION_ATTESTATION"):
        spec.validate_capture_result(value)
    assert validate(value, spec.validate_capture_result) is None
    assert value == original


@pytest.mark.parametrize("defect", ["capture_hash", "role_collision", "wrong_acl",
                                    "wrong_source_seal", "wrong_schema_oid"])
def test_oid_translation_never_repairs_nonidentity_or_broken_graph(defect):
    value, _, _ = _native_case()
    function = value["attestation"]["functions"][0]
    if defect == "capture_hash":
        value["capture_sha256"] = "0" * 64
    elif defect == "role_collision":
        function["execute_acl_atoms"][2]["grantee_oid"] = (
            function["execute_acl_atoms"][1]["grantee_oid"])
    elif defect == "wrong_acl":
        function["execute_acl_atoms"][1]["is_grantable"] = True
    elif defect == "wrong_source_seal":
        function["body_sha256"] = "0" * 64
    else:
        function["namespace_oid"] += 1
    if defect != "capture_hash":
        value["capture_sha256"] = fingerprint(
            {key: value[key] for key in ("context", "attestation", "completions")})
    with pytest.raises(ValueError):
        validate(value, spec.validate_capture_result)


def test_actual_oid_catalogue_cross_match_still_required(monkeypatch):
    value, role_map, schema_map = _native_case()
    state, _ = function_case()
    for row in state["roles"]:
        row["oid"] = role_map.get(row["oid"], row["oid"])
    for row in state["namespaces"]:
        row["oid"] = schema_map.get(row["oid"], row["oid"])
        row["owner"] = role_map.get(row["owner"], row["owner"])
    for row in state["functions"]:
        row["oid"] = 70004 if row["proname"] == "dependencies" else row["oid"]
        row["pronamespace"] = schema_map.get(row["pronamespace"], row["pronamespace"])
        row["proowner"] = role_map.get(row["proowner"], row["proowner"])
        row["acl"] = [[role_map.get(grantor, grantor),
                       role_map.get(grantee, grantee), privilege, grantable]
                      for grantor, grantee, privilege, grantable in row["acl"]]
    strict = spec.validate_capture_result
    monkeypatch.setattr(spec, "validate_capture_result",
                        lambda document: validate(document, strict))
    assert scope.require_existing_helper(state, value) == 70004
    state["functions"][0]["oid"] += 1
    with pytest.raises(scope.FunctionScopeError, match="UNREVIEWED_PRIVILEGED_FUNCTION"):
        scope.require_existing_helper(state, value)
