"""Disposable-PG-only identity translation for the native transaction tests.

This file is not packaged or imported by production. The public collector and
all grant SQL still use the original catalog OIDs; only a deep copy presented
to the unchanged fixed-DEV validator receives a bijective OID renaming.
"""

from copy import deepcopy

from mfdataindia.review import reader_function_attestation_validation as validation
from mfdataindia.review.policy import fingerprint


def _require(condition):
    if not condition:
        raise ValueError("INVALID_NATIVE_OID_ISOMORPHISM")


def validate(value, original_validator):
    _require(type(value) is dict and type(value.get("attestation")) is dict)
    captured = {key: value[key] for key in ("context", "attestation", "completions")}
    # Never repair or hide a bad original capture fingerprint.
    _require(value.get("capture_sha256") == fingerprint(captured))
    actual = value["attestation"]
    _require(type(actual.get("reader")) is list and len(actual["reader"]) == 1)
    _require(type(actual.get("functions")) is list and len(actual["functions"]) == 1)
    _require(type(actual.get("schemas")) is list and len(actual["schemas"]) == 16)
    reader = actual["reader"][0]
    function = actual["functions"][0]
    schemas = actual["schemas"]
    _require(type(reader) is dict and type(function) is dict)
    _require(all(type(item) is dict and set(item) == {"oid", "nspname"}
                 for item in schemas))
    _require({item["nspname"] for item in schemas} == set(validation.SCHEMAS))
    schema_oids = [item["oid"] for item in schemas]
    _require(all(type(oid) is int and oid > 0 for oid in schema_oids)
             and len(set(schema_oids)) == 16)
    _require(schema_oids == sorted(schema_oids))
    namespace = next(item["oid"] for item in schemas
                     if item["nspname"] == "mf_fact_review")
    _require(function.get("namespace_oid") == namespace)
    atoms = function.get("execute_acl_atoms")
    _require(type(atoms) is list and len(atoms) == 3)
    _require(all(type(atom) is dict for atom in atoms))
    _require(all(type(atom.get("grantor_oid")) is int
                 and type(atom.get("grantee_oid")) is int
                 and type(atom.get("is_grantable")) is bool for atom in atoms))
    native_order = [(atom.get("grantor_oid"), atom.get("grantee_oid"),
                     atom.get("is_grantable")) for atom in atoms]
    _require(native_order == sorted(set(native_order)))
    by_name = {atom.get("grantee_name"): atom for atom in atoms}
    _require(len(by_name) == 3 and set(by_name) == {
        "mfdata_admin", "mfdata_verified_reader", "mfdata_verified_admin"})
    role_oids = {name: atom["grantee_oid"] for name, atom in by_name.items()}
    _require(all(type(oid) is int and oid > 0 for oid in role_oids.values())
             and len(set(role_oids.values())) == 3)
    _require(reader.get("oid") == role_oids["mfdata_verified_reader"])
    _require(function.get("owner_oid") == role_oids["mfdata_admin"])
    _require(all(atom.get("grantor_oid") == role_oids["mfdata_admin"]
                 for atom in atoms))
    _require(type(function.get("oid")) is int and function["oid"] > 0)

    normalized = deepcopy(value)
    doc = normalized["attestation"]
    doc["reader"][0]["oid"] = 40032
    for index, item in enumerate(sorted(doc["schemas"], key=lambda row: row["nspname"])):
        item["oid"] = 41816 if item["nspname"] == "mf_fact_review" else 50000 + index
    doc["schemas"].sort(key=lambda row: row["oid"])
    copied = doc["functions"][0]
    copied.update(oid=41849, namespace_oid=41816, owner_oid=16587)
    mapped = {"mfdata_admin": 16587, "mfdata_verified_reader": 40032,
              "mfdata_verified_admin": 40033}
    for atom in copied["execute_acl_atoms"]:
        atom["grantor_oid"] = 16587
        atom["grantee_oid"] = mapped[atom["grantee_name"]]
    copied["execute_acl_atoms"].sort(key=lambda atom: (
        atom["grantor_oid"], atom["grantee_oid"], atom["is_grantable"]))
    normalized["capture_sha256"] = fingerprint(
        {key: normalized[key] for key in ("context", "attestation", "completions")})
    original_validator(normalized)
    return None
