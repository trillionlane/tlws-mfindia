"""Synthetic preparation tests only. No database, provider or runtime execution."""
import hashlib
import json
import re
import unittest
from copy import deepcopy
from pathlib import Path

from mfdataindia.review import reader_function_identification_validation as v
from reader_function_identification_preservation import restore_identification_parent

ROOT = Path(__file__).resolve().parents[1] / "docs/function_identification_seed"


def atom(grantee=0, *, available=True, member=False, can_set=False, grantable=False):
    return {
        "grantor_oid": 10, "grantee_oid": grantee,
        "grantee_name": "PUBLIC" if grantee == 0 else
                        "mfdata_verified_reader" if grantee == 40032 else "synthetic_group",
        "is_grantable": grantable, "reader_is_grantee": grantee == 40032,
        "role_privileges_immediately_available": available,
        "reader_is_member": member, "reader_can_set_role": can_set,
    }


def fixture():
    return {
        "format": v.FORMAT,
        "reader": [{"oid": 40032, "rolname": "mfdata_verified_reader",
                    "rolsuper": False, "rolinherit": False}],
        "schemas": [{"oid": 5000 + i, "nspname": name}
                    for i, name in enumerate(v.SCHEMAS)],
        "functions": [{
            "oid": 9000, "namespace_oid": 5000, "schema": "mf",
            "name": "synthetic_function", "kind": "f",
            "owner_oid": 10, "owner_name": "mfdata_admin",
            "argument_type_oids": [23, 25, 23], "return_type_oid": 2278,
            "security_definer": True, "effective_execute": True,
            "schema_usage": True, "acl_is_null": False,
            "execute_acl_atoms": [atom()],
        }],
    }


class PreparationTests(unittest.TestCase):
    def rejected(self, value):
        with self.assertRaisesRegex(ValueError, "^INVALID_FUNCTION_IDENTIFICATION$"):
            v.validate_document(value)

    def test_public_stored(self):
        self.assertEqual(v.route_summary(fixture())[0]["routes"], ["PUBLIC_STORED"])

    def test_public_default(self):
        d = fixture()
        d["functions"][0]["acl_is_null"] = True
        owner = atom(10, available=False)
        owner["grantee_name"] = "mfdata_admin"
        d["functions"][0]["execute_acl_atoms"].append(owner)
        self.assertEqual(v.route_summary(d)[0]["routes"], ["PUBLIC_DEFAULT"])

    def test_direct_reader(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] = [atom(40032, member=True, can_set=True)]
        self.assertEqual(v.route_summary(d)[0]["routes"], ["DIRECT_READER"])

    def test_inherited_current_is_usage_not_rolinherit_flag(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] = [atom(20, member=True)]
        self.assertFalse(d["reader"][0]["rolinherit"])
        self.assertEqual(v.route_summary(d)[0]["routes"], ["INHERITED_CURRENT"])

    def test_member_only_does_not_supply_execute(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] = [atom(20, available=False, member=True)]
        self.rejected(d)

    def test_set_only_does_not_supply_execute(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] = [atom(20, available=False, member=True, can_set=True)]
        self.rejected(d)

    def test_member_and_set_route_annotations_not_current(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] += [
            atom(20, available=False, member=True),
            atom(30, available=False, member=True, can_set=True),
        ]
        self.assertEqual(v.route_summary(d)[0]["routes"], [
            "MEMBER_ONLY_NOT_CURRENT_EXECUTE_ROUTE", "PUBLIC_STORED",
            "SET_ROLE_POSSIBLE_NOT_CURRENT_EXECUTE_ROUTE",
        ])

    def test_multiple_available_routes_not_unique_cause(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] += [
            atom(20, member=True), atom(40032, member=True, can_set=True)]
        self.assertEqual(v.route_summary(d)[0]["routes"],
                         ["DIRECT_READER", "INHERITED_CURRENT", "PUBLIC_STORED"])

    def test_owner_alone_not_current_execute(self):
        d = fixture()
        d["functions"][0].update(owner_oid=40032, owner_name="mfdata_verified_reader",
                                execute_acl_atoms=[])
        self.rejected(d)

    def test_superuser_empty_acl_is_reported_not_hidden(self):
        d = fixture()
        d["reader"][0]["rolsuper"] = True
        d["functions"][0]["execute_acl_atoms"] = []
        self.assertEqual(v.route_summary(d)[0]["routes"], ["SUPERUSER_BYPASS"])

    def test_superuser_does_not_mislabel_membership_cause(self):
        d = fixture()
        d["reader"][0]["rolsuper"] = True
        d["functions"][0]["execute_acl_atoms"] = [atom(20)]
        self.assertEqual(v.route_summary(d)[0]["routes"],
                         ["ROLE_PRIVILEGES_AVAILABLE_SUPERUSER_NOT_CAUSAL", "SUPERUSER_BYPASS"])

    def test_zero_matches_is_later_snapshot_not_old_failure_waiver(self):
        d = fixture()
        d["functions"] = []
        self.assertEqual(v.route_summary(d), [])

    def test_trigger_and_event_trigger_not_called(self):
        for oid in (2279, 3838):
            with self.subTest(oid=oid):
                d = fixture()
                d["functions"][0]["return_type_oid"] = oid
                out = v.route_summary(d)[0]
                self.assertFalse(out["callability_proved"])
                self.assertFalse(out["function_write_safety_proved"])

    def test_procedure_is_not_omitted(self):
        d = fixture()
        d["functions"][0]["kind"] = "p"
        self.assertEqual(v.validate_document(d), d)

    def test_schema_usage_not_assumed_from_execute(self):
        d = fixture()
        d["functions"][0]["schema_usage"] = False
        self.assertEqual(v.validate_document(d), d)

    def test_argument_order_and_duplicates_preserved(self):
        d = fixture()
        out = v.validate_document(d)
        self.assertEqual(out["functions"][0]["argument_type_oids"], [23, 25, 23])
        self.assertEqual(out, d)
        self.assertIsNot(out, d)

    def test_numeric_oid_strict_no_coercion(self):
        for bad in (True, False, 0, -1, 4294967296, "9000", 9000.0, None):
            with self.subTest(bad=bad):
                d = fixture()
                d["functions"][0]["oid"] = bad
                self.rejected(d)

    def test_argument_oid_strict(self):
        d = fixture()
        d["functions"][0]["argument_type_oids"] = [True]
        self.rejected(d)

    def test_unknown_keys_and_private_fields_rejected_without_echo(self):
        for field in ("prosrc", "probin", "prosqlbody", "proconfig", "rolconfig",
                      "proargdefaults", "proargnames", "body", "future_unknown"):
            with self.subTest(field=field):
                d = fixture()
                d["functions"][0][field] = "PRIVATE_SENTINEL_NEVER_ECHO"
                try:
                    v.validate_document(d)
                except ValueError as e:
                    self.assertEqual(str(e), "INVALID_FUNCTION_IDENTIFICATION")
                else:
                    self.fail("private/unknown field accepted")

    def test_missing_key_rejected(self):
        d = fixture()
        del d["functions"][0]["owner_oid"]
        self.rejected(d)

    def test_duplicate_oid_and_unordered_rows_rejected(self):
        d = fixture()
        d["functions"].append(deepcopy(d["functions"][0]))
        self.rejected(d)
        d["functions"][0]["oid"] = 9001
        self.rejected(d)

    def test_duplicate_acl_logical_atom_even_different_grant_option(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"].append(atom(grantable=True))
        self.rejected(d)

    def test_acl_order_rejected(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] = [atom(20, member=True), atom()]
        self.rejected(d)

    def test_null_or_unknown_grantee_rejected(self):
        for name in (None, "", 21):
            with self.subTest(name=name):
                d = fixture()
                d["functions"][0]["execute_acl_atoms"][0]["grantee_name"] = name
                self.rejected(d)

    def test_public_is_not_role_membership(self):
        for key in ("reader_is_member", "reader_can_set_role", "reader_is_grantee"):
            with self.subTest(key=key):
                d = fixture()
                d["functions"][0]["execute_acl_atoms"][0][key] = True
                self.rejected(d)

    def test_direct_grant_requires_reflexive_server_flags(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] = [atom(40032)]
        self.rejected(d)

    def test_nonmember_usage_cannot_be_inherited_without_superuser(self):
        d = fixture()
        d["functions"][0]["execute_acl_atoms"] = [atom(20)]
        self.rejected(d)

    def test_nonboolean_flag_rejected(self):
        d = fixture()
        d["functions"][0]["effective_execute"] = 1
        self.rejected(d)

    def test_nonmatching_predicate_rejected(self):
        for key in ("effective_execute", "security_definer"):
            with self.subTest(key=key):
                d = fixture()
                d["functions"][0][key] = False
                self.rejected(d)

    def test_reader_identity_missing_duplicate_or_drift_rejected(self):
        for rows in ([], fixture()["reader"] * 2,
                     [{"oid": 40033, "rolname": "mfdata_verified_reader",
                       "rolsuper": False, "rolinherit": False}]):
            with self.subTest(rows=rows):
                d = fixture()
                d["reader"] = rows
                self.rejected(d)

    def test_schema_missing_duplicate_outside_or_unordered_rejected(self):
        for mode in ("missing", "duplicate", "outside", "unordered"):
            with self.subTest(mode=mode):
                d = fixture()
                if mode == "missing":
                    d["schemas"].pop()
                elif mode == "duplicate":
                    d["schemas"][-1]["nspname"] = "mf"
                elif mode == "outside":
                    d["schemas"][-1]["nspname"] = "public"
                else:
                    d["schemas"].reverse()
                self.rejected(d)

    def test_function_namespace_mismatch_rejected(self):
        d = fixture()
        d["functions"][0]["namespace_oid"] = 5001
        self.rejected(d)

    def test_json_roundtrip_duplicate_key_and_nonfinite_rejection(self):
        d = fixture()
        self.assertEqual(v.parse_document(v.canonical(d).encode()), d)
        for raw in (b'{"format":"one","format":"two"}', b'NaN', b'Infinity', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                v.parse_document(raw)

    def test_oversized_raw_rejected_not_truncated(self):
        with self.assertRaises(ValueError):
            v.parse_document(b" " * (v.MAX_RAW_BYTES + 1))

    def test_frozen_inventory_preserved_and_four_select_sequence(self):
        authority = json.loads((ROOT / "SOURCE_AUTHORITY.json").read_text())
        inv = json.loads((ROOT / "identification-query-inventory.json").read_text())
        self.assertEqual(inv["controls"], authority["frozen_controls"])
        self.assertEqual(inv["terminal"], authority["frozen_terminal"])
        self.assertEqual(inv["queries"][0]["sql"], authority["frozen_context_SQL"])
        self.assertEqual(inv["queries"][0]["columns"], authority["frozen_context_columns"])
        self.assertEqual(inv["queries"][1]["sha256"], hashlib.sha256(
            (ROOT / inv["queries"][1]["file"]).read_bytes()).hexdigest())
        self.assertEqual(inv["sequence"], [
            "context", "function_identification", "function_identification", "context"])
        self.assertFalse(inv["live_execution_authorized"])
        self.assertFalse(inv["native_SQL_validated"])

    def test_fixed_sql_privacy_and_predicate_static_not_native_proof(self):
        sql = (ROOT / "fixed-function-identification.sql").read_text()
        for token in ("prosrc", "probin", "prosqlbody", "proargdefaults", "proargnames",
                      "proconfig", "rolconfig", "pg_get_functiondef", "pg_get_function_arguments"):
            self.assertNotIn(token, sql)
        self.assertIn("WHERE p.prosecdef", sql)
        self.assertIn("pg_catalog.has_function_privilege(r.oid, p.oid, 'EXECUTE')", sql)
        self.assertIn("WHERE a.privilege_type = 'EXECUTE'", sql)
        for name in v.SCHEMAS:
            self.assertIn("'" + name + "'", sql)
        without_literals = re.sub("'(?:''|[^'])*'", "''", sql)
        self.assertIsNone(re.search(
            r"\b(INSERT|UPDATE|DELETE|CALL|DO|CREATE|GRANT|REVOKE|ALTER|DROP|COPY|TRUNCATE|LOCK|COMMIT|LIMIT)\b",
            without_literals, re.IGNORECASE))
        self.assertNotIn(";", sql)
        self.assertIn("a.grantor::bigint", sql)
        self.assertIn("a.grantee::bigint", sql)

    def test_all_parent_full_bytes_unchanged(self):
        authority = json.loads((ROOT / "SOURCE_AUTHORITY.json").read_text())
        repo = ROOT.parents[1]
        for row in authority["all_files"]:
            p = repo / row["path"]
            raw = restore_identification_parent(row["path"], p.read_bytes())
            self.assertEqual(len(raw), row["bytes"], row["path"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"], row["path"])
            self.assertEqual(bool(p.stat().st_mode & 0o111),
                             row["mode"] == "100755", row["path"])


if __name__ == "__main__":
    unittest.main()
