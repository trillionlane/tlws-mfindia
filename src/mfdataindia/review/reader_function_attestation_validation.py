"""Strict single-routine metadata, no raw code/settings and no grant admission."""
from copy import deepcopy
import json
import re

from mfdataindia.review import reader_function_identification_validation as original
from mfdataindia.review import reader_function_attestation_reference as reference

FORMAT = "mf-reader-function-attestation-v1"
SCHEMAS = original.SCHEMAS
MAX_RAW_BYTES = 131000
EXTRA_KEYS = (*reference.EXPECTED_DECLARATION, "definition_sha256", "definition_bytes")


def require(ok):
    if not ok:
        raise ValueError("INVALID_FUNCTION_ATTESTATION")


def digest(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def count(value):
    require(type(value) is int and 0 <= value <= 2147483647)


def sealed(function, prefix):
    null, sha, size = (function[prefix + suffix] for suffix in ("_is_null", "_sha256", "_bytes"))
    require(type(null) is bool)
    count(size)
    if null:
        require(sha is None and size == 0)
    else:
        digest(sha)


def expected_acl():
    return [
        {"grantor_oid": 16587, "grantee_oid": oid, "grantee_name": name,
         "is_grantable": False, "reader_is_grantee": oid == 40032,
         "role_privileges_immediately_available": oid == 40032,
         "reader_is_member": oid == 40032, "reader_can_set_role": oid == 40032}
        for oid, name in ((16587, "mfdata_admin"), (40032, "mfdata_verified_reader"),
                          (40033, "mfdata_verified_admin"))
    ]


def validate_document(value):
    require(type(value) is dict and set(value) == {"format", "reader", "schemas", "functions"})
    require(value["format"] == FORMAT and type(value["functions"]) is list
            and len(value["functions"]) == 1)
    function = value["functions"][0]
    require(type(function) is dict and set(function) == set(original.FUNCTION_KEYS) | set(EXTRA_KEYS))
    projection = {k: deepcopy(value[k]) for k in ("reader", "schemas")}
    projection.update(format=original.FORMAT,
                      functions=[{k: deepcopy(function[k]) for k in original.FUNCTION_KEYS}])
    try:
        original.validate_document(projection)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError("INVALID_FUNCTION_ATTESTATION") from None
    require(value["reader"] == [{"oid": 40032, "rolname": "mfdata_verified_reader",
                                "rolsuper": False, "rolinherit": False}])
    require({k: function[k] for k in ("oid", "namespace_oid", "schema", "name", "kind",
                                     "owner_oid", "owner_name", "argument_type_oids", "return_type_oid")}
            == {"oid": 41849, "namespace_oid": 41816, "schema": "mf_fact_review",
                "name": "dependencies", "kind": "f", "owner_oid": 16587,
                "owner_name": "mfdata_admin", "argument_type_oids": [23], "return_type_oid": 3802})
    require(function["schema_usage"] is True and function["acl_is_null"] is False
            and function["execute_acl_atoms"] == expected_acl())
    require(type(function["language"]) is str and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,62}",
                                                               function["language"]) is not None)
    for key in ("language_trusted", "strict", "leakproof", "returns_set",
                "configuration_is_exact_catalog_search_path"):
        require(type(function[key]) is bool)
    for key, allowed in (("volatility", "ivs"), ("parallel", "usr")):
        require(type(function[key]) is str and len(function[key]) == 1 and function[key] in allowed)
    for key in ("cost", "rows"):
        require(type(function[key]) is str and len(function[key]) <= 32
                and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:[.][0-9]+)?(?:e[+-]?[0-9]+)?",
                                 function[key]) is not None)
    for key in ("variadic_type_oid", "support_function_oid"):
        original.oid(function[key], public=True)
    for key in ("argument_count", "argument_default_count"):
        count(function[key])
    for key in ("all_argument_type_oids", "transform_type_oids"):
        values = function[key]
        require(values is None or type(values) is list)
        if values is not None:
            for item in values:
                original.oid(item)
    modes = function["argument_modes"]
    require(modes is None or (type(modes) is list
            and all(type(m) is str and len(m) == 1 and m in "iobvt" for m in modes)))
    for prefix in ("argument_names", "binary", "sql_body", "argument_defaults", "configuration"):
        sealed(function, prefix)
    for prefix in ("body", "definition"):
        digest(function[prefix + "_sha256"])
        count(function[prefix + "_bytes"])
    expected = reference.EXPECTED_DECLARATION
    config_matches = all(function[k] == expected[k] for k in
                         ("configuration_is_null", "configuration_sha256", "configuration_bytes"))
    require(function["configuration_is_exact_catalog_search_path"] is config_matches)
    try:
        require(len(canonical(value).encode("utf-8")) <= MAX_RAW_BYTES)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("INVALID_FUNCTION_ATTESTATION") from None
    return deepcopy(value)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def parse_document(raw):
    require(type(raw) is bytes and len(raw) <= MAX_RAW_BYTES)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    def constant(_):
        raise ValueError("INVALID_FUNCTION_ATTESTATION")
    try:
        return validate_document(json.loads(raw, object_pairs_hook=unique, parse_constant=constant))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("INVALID_FUNCTION_ATTESTATION") from None
