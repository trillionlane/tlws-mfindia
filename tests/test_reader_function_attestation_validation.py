"""Pure exact-function attestation tests. No provider, secret or database."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mfdataindia.review import reader_function_attestation_reference as reference
from mfdataindia.review import reader_function_attestation_validation as v

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    schemas = [{"oid": 41816 if name == "mf_fact_review" else 5000 + i, "nspname": name}
               for i, name in enumerate(v.SCHEMAS)]
    return {
        "format": v.FORMAT,
        "reader": [{"oid": 40032, "rolname": "mfdata_verified_reader",
                    "rolsuper": False, "rolinherit": False}],
        "schemas": sorted(schemas, key=lambda x: x["oid"]),
        "functions": [{
            "oid": 41849, "namespace_oid": 41816, "schema": "mf_fact_review",
            "name": "dependencies", "kind": "f", "owner_oid": 16587,
            "owner_name": "mfdata_admin", "argument_type_oids": [23],
            "return_type_oid": 3802, "security_definer": True, "effective_execute": True,
            "schema_usage": True, "acl_is_null": False, "execute_acl_atoms": v.expected_acl(),
            **deepcopy(reference.EXPECTED_DECLARATION),
            "definition_sha256": "a" * 64, "definition_bytes": 2400,
        }],
    }


def test_reference_exact_entire_body_and_parent_migration_without_normalization():
    raw = (ROOT / "sql/optional/036_official_fact_verification.sql").read_bytes()
    start = (b"CREATE FUNCTION mf_fact_review.dependencies(p_code integer) RETURNS jsonb\n"
             b"LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$")
    end = b"\nCREATE FUNCTION mf_fact_review.record_review"
    assert raw.count(start) == raw.count(end) == 1
    statement = raw[raw.index(start):raw.index(end)]
    body = statement.split(b" AS $$", 1)[1].rsplit(b"$$;", 1)[0]
    assert hashlib.sha256(raw).hexdigest() == reference.MIGRATION_SHA256
    assert hashlib.sha256(body).hexdigest() == reference.BODY_SHA256
    assert len(body) == reference.BODY_BYTES
    for prefix, value in (("argument_names", ["p_code"]),
                          ("configuration", ["search_path=pg_catalog"])):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        assert reference.EXPECTED_DECLARATION[prefix + "_sha256"] == hashlib.sha256(encoded).hexdigest()
        assert reference.EXPECTED_DECLARATION[prefix + "_bytes"] == len(encoded)


def test_whole_fixed_profile_roundtrip_deepcopy_and_nonaccepting_comparison():
    doc = fixture()
    assert v.validate_document(doc) == doc
    assert v.validate_document(doc) is not doc
    assert v.parse_document(v.canonical(doc).encode()) == doc
    result = reference.comparison(doc["functions"][0])
    assert result == {"body_matches_committed_reference": True,
                      "settings_match_committed_reference": True,
                      "declaration_matches_committed_reference": True,
                      "function_invoked": False, "permission_acceptance": False}


def mutate_declaration(function, key):
    value = function[key]
    if key.endswith("_is_null"):
        prefix = key.removesuffix("_is_null")
        function[key] = not value
        function[prefix + "_sha256"] = "f" * 64 if value else None
        function[prefix + "_bytes"] = 1 if value else 0
    elif key.endswith("_sha256") and value is None:
        prefix = key.removesuffix("_sha256")
        function[prefix + "_is_null"] = False
        function[key] = "f" * 64
        function[prefix + "_bytes"] = 1
    elif key.endswith("_bytes") and function.get(key.removesuffix("_bytes") + "_is_null"):
        prefix = key.removesuffix("_bytes")
        function[prefix + "_is_null"] = False
        function[prefix + "_sha256"] = "f" * 64
        function[key] = 1
    elif key == "configuration_is_exact_catalog_search_path":
        function["configuration_sha256"] = "f" * 64
    elif type(value) is bool:
        function[key] = not value
    elif value is None:
        function[key] = []
    elif type(value) is int:
        function[key] += 1
    elif key == "language":
        function[key] = "sql"
    elif key == "volatility":
        function[key] = "v"
    elif key == "parallel":
        function[key] = "s"
    elif key in ("cost", "rows"):
        function[key] = "101"
    else:
        function[key] = "f" * 64
    if key.startswith("configuration_"):
        function["configuration_is_exact_catalog_search_path"] = False


@pytest.mark.parametrize("key", sorted(reference.EXPECTED_DECLARATION))
def test_every_declaration_bearing_field_mutation_cannot_report_reference_match(key):
    doc = fixture()
    mutate_declaration(doc["functions"][0], key)
    v.validate_document(doc)  # A mismatch is bounded metadata, never safety acceptance.
    assert reference.comparison(doc["functions"][0])["declaration_matches_committed_reference"] is False
    assert reference.comparison(doc["functions"][0])["permission_acceptance"] is False


@pytest.mark.parametrize("defect", [
    "superuser", "inherit", "schema_usage", "public", "alternative", "no_reader",
    "grantoption", "grantor", "owner", "namespace", "oid", "name", "argument",
    "return", "acl_null", "reader_oid", "security", "effective", "duplicate",
])
def test_exact_access_identity_rejects_all_additive_or_subtractive_drift(defect):
    doc = fixture()
    f = doc["functions"][0]
    if defect in ("superuser", "inherit"):
        doc["reader"][0]["rolsuper" if defect == "superuser" else "rolinherit"] = True
    elif defect in ("schema_usage", "security", "effective"):
        f[{"schema_usage": "schema_usage", "security": "security_definer",
           "effective": "effective_execute"}[defect]] = False
    elif defect == "public":
        atom = deepcopy(f["execute_acl_atoms"][0])
        atom.update(grantee_oid=0, grantee_name="PUBLIC", role_privileges_immediately_available=True)
        f["execute_acl_atoms"].insert(0, atom)
    elif defect == "alternative":
        f["execute_acl_atoms"][-1].update(role_privileges_immediately_available=True,
                                         reader_is_member=True, reader_can_set_role=True)
    elif defect == "no_reader":
        f["execute_acl_atoms"].pop(1)
    elif defect in ("grantoption", "grantor"):
        f["execute_acl_atoms"][1]["is_grantable" if defect == "grantoption" else "grantor_oid"] = (
            True if defect == "grantoption" else 16588)
    elif defect == "owner":
        f["owner_oid"] += 1
    elif defect == "namespace":
        f["namespace_oid"] += 1
    elif defect == "oid":
        f["oid"] += 1
    elif defect == "name":
        f["name"] += "_other"
    elif defect == "argument":
        f["argument_type_oids"] = [25]
    elif defect == "return":
        f["return_type_oid"] = 25
    elif defect == "acl_null":
        f["acl_is_null"] = True
    elif defect == "reader_oid":
        doc["reader"][0]["oid"] += 1
    else:
        doc["functions"].append(deepcopy(f))
    with pytest.raises(ValueError):
        v.validate_document(doc)


@pytest.mark.parametrize("key", ["prosrc", "proconfig", "probin", "prosqlbody",
                                 "proargdefaults", "proargnames", "body", "future_field"])
def test_private_or_unknown_fields_never_cross_boundary_or_echo(key):
    doc = fixture()
    doc["functions"][0][key] = "PRIVATE_SENTINEL"
    with pytest.raises(ValueError) as caught:
        v.validate_document(doc)
    assert "PRIVATE_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize("bad", [True, False, -1, "1824", 1824.0, None])
def test_sealed_byte_counts_are_strict_integers(bad):
    doc = fixture()
    doc["functions"][0]["body_bytes"] = bad
    with pytest.raises(ValueError):
        v.validate_document(doc)


@pytest.mark.parametrize("prefix", ["argument_names", "binary", "sql_body",
                                   "argument_defaults", "configuration"])
def test_null_empty_and_private_text_seals_are_distinct(prefix):
    doc = fixture()
    f = doc["functions"][0]
    f.update({prefix + "_is_null": False,
              prefix + "_sha256": hashlib.sha256(b"").hexdigest(), prefix + "_bytes": 0})
    if prefix == "configuration":
        f["configuration_is_exact_catalog_search_path"] = False
    v.validate_document(doc)
    assert reference.comparison(f)["declaration_matches_committed_reference"] is False
    f[prefix + "_is_null"] = True
    with pytest.raises(ValueError):
        v.validate_document(doc)


@pytest.mark.parametrize("bad", ["NaN", "-1", "inf", "1e99999999999999999999999999999999999",
                                 True, 100, None])
def test_cost_rows_bounded_text_not_coerced_or_nonfinite(bad):
    doc = fixture()
    doc["functions"][0]["cost"] = bad
    with pytest.raises(ValueError):
        v.validate_document(doc)


@pytest.mark.parametrize("raw", [b'{"format":"a","format":"b"}', b"NaN", b"Infinity",
                               b"\xff", b" " * 131001])
def test_strict_json_duplicate_nonfinite_unicode_size_gate(raw):
    with pytest.raises(ValueError):
        v.parse_document(raw)


def test_fixed_SQL_exact_source_and_server_sealing_not_routine_invocation():
    from mfdataindia.review import reader_function_attestation_inventory as inv
    raw = (ROOT / "docs/function_attestation_seed/fixed-function-attestation.sql").read_text()
    assert raw == inv.ATTESTATION_SQL
    assert "pg_catalog.pg_get_expr(f.proargdefaults, 0, false)" in raw
    assert "pg_catalog.pg_get_functiondef(f.oid)" in raw
    assert "p.proname = 'dependencies'" in raw
    assert "p.proargtypes::text = '23'" in raw
    assert "f.prosupport::oid::bigint" in raw
    assert "t.type_oid::bigint" in raw
    assert "mf_fact_review.dependencies(" not in raw
    assert ";" not in raw
