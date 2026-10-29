"""Provider-free protocol/source-authority checks, not catalogue admission."""
from copy import deepcopy
import base64
import gzip
import json
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

import reader_source_reference as r

ROOT = Path(__file__).resolve().parents[1]


def test_exact_authority_precedes_all_fixture_activity():
    value = r.authority(ROOT)
    assert len(value["files"]) == 44 and value["total_bytes"] == 783832
    assert value["files"][0]["path"] == "sql/000_schema_migrations.sql"
    assert value["files"][-1]["path"] == "sql/optional/044_uti_complete_aum_rows.sql"
    assert not any(x["path"] == "sql/007_fund_data_status.sql" for x in value["files"])


def test_complete_catalogue_schema_and_support_references_are_explicit():
    assert set(r.FIELDS) == set(r.REFS)
    for catalog in r.FIELDS:
        sql = r.projection(catalog)
        assert "SELECT *" not in sql
        assert "jsonb_build_object" in sql
        assert "'oid'" in sql
    assert {"pg_opfamily", "pg_am", "pg_amop", "pg_amproc", "pg_constraint"} <= set(r.FIELDS)
    assert {"typispreferred", "typisdefined", "typdelim"} <= set(r.FIELDS["pg_type"])
    assert {"aggtransspace", "aggmtransspace"} <= set(r.AGG_FIELDS)
    assert {"string_agg", "to_json", "unnest"} <= set(r.FUNCTION_NAMES)
    assert r.REFS["pg_am"]["amhandler"] == "pg_proc"
    assert r.REFS["pg_language"]["lanvalidator"] == "pg_proc"
    assert r.REFS["pg_type"]["typsubscript"] == "pg_proc"
    assert r.AGG_REFS["aggcombinefn"] == "pg_proc"


@pytest.mark.parametrize("catalog", sorted(r.PRIVATE))
def test_private_expressions_are_sealed_server_side(catalog):
    sql = r.projection(catalog)
    for key in r.PRIVATE[catalog]:
        assert "'" + key + "_seal'" in sql
    assert "'sha256'" in sql and "'utf8_bytes'" in sql and "'is_null'" in sql
    if catalog == "pg_proc":
        assert "'prosrc'" not in sql and "'proconfig'" not in sql
        assert "pg_get_expr(r.proargdefaults,0,false)" in sql
    if catalog == "pg_authid":
        assert "rolpassword" not in sql and "'rolconfig'" not in sql


def example():
    return {"format": "synthetic-NOT-admission", "payload": "source" * 2500,
            "runtime_authority": False}


def test_lossless_bounded_numbered_envelope_roundtrip_with_CI_prefix():
    value = example()
    lines = r.reference_envelope(value, identity="a" * 32)
    assert r.extract_envelope(["job\tUNKNOWN STEP\t2026-10-29T00:00:00Z " + s for s in lines]) == value
    assert max(len(s) for s in lines) < 61000


def test_multichunk_population_roundtrip_and_middle_gap_rejection():
    value = {"fixture": random.Random(731).randbytes(140000).hex()}
    lines = r.reference_envelope(value, identity="a" * 32)
    assert len(lines) > 4
    assert r.extract_envelope(lines) == value
    with pytest.raises(ValueError):
        r.extract_envelope([*lines[:2], *lines[3:]])


@pytest.mark.parametrize("target,field", [("header", "chunk_bytes"), ("header", "encoded_bytes"), ("header", "compressed_bytes"), ("chunk", "count"), ("chunk", "chars")])
def test_protocol_integer_fields_reject_equal_floats(target, field):
    lines = r.reference_envelope(example(), identity="a" * 32)
    records = [json.loads(x.removeprefix(r.MARKER)) for x in lines]
    record = records[0]["header"] if target == "header" else records[1]["chunk"]
    record[field] = float(record[field])
    with pytest.raises(ValueError):
        r.extract_envelope([r.MARKER + r.canonical(v) for v in records])


@pytest.mark.parametrize("mutation", ["duplicate", "drop", "reorder", "mixed", "count", "index", "hash", "chars", "value", "header", "tail", "extra_envelope", "bool_count", "oversize", "trailing_gzip", "padding"])
def test_whole_protocol_rejects_ambiguity_and_partial_material(mutation):
    lines = r.reference_envelope(example(), identity="a" * 32)
    records = [json.loads(x.removeprefix(r.MARKER)) for x in lines]
    if mutation == "duplicate":
        records.insert(1, deepcopy(records[1]))
    elif mutation == "drop":
        records.pop(1)
    elif mutation == "reorder":
        records.reverse()
    elif mutation == "mixed":
        records[1]["chunk"]["identity"] = "b" * 32
    elif mutation == "count":
        records[1]["chunk"]["count"] += 1
    elif mutation == "index":
        records[1]["chunk"]["index"] = 1
    elif mutation == "hash":
        records[1]["chunk"]["sha256"] = "0" * 64
    elif mutation == "chars":
        records[1]["chunk"]["chars"] += 1
    elif mutation == "value":
        records[1]["chunk"]["value"] += "A"
    elif mutation == "header":
        records[0]["header"]["authority_sha256"] = "b" * 64
    elif mutation == "tail":
        records[-1]["end"]["identity"] = "b" * 32
    elif mutation == "extra_envelope":
        records += deepcopy(records)
    elif mutation == "bool_count":
        records[0]["header"]["count"] = True
    elif mutation == "oversize":
        records[0]["header"]["raw_bytes"] = r.MAX_RAW + 1
    else:
        h = records[0]["header"]
        c = records[1]["chunk"]
        body = base64.b64decode(c["value"])
        if mutation == "trailing_gzip":
            body += gzip.compress(b"PRIVATE_TRAILING", mtime=0)
            c["value"] = base64.b64encode(body).decode()
        else:
            c["value"] += "="
        h["compressed_bytes"], h["compressed_sha256"] = len(body), r.digest(body)
        h["encoded_bytes"] = c["chars"] = len(c["value"])
        c["sha256"] = r.digest(c["value"].encode())
    with pytest.raises((ValueError, KeyError, TypeError)):
        r.extract_envelope([r.MARKER + r.canonical(v) for v in records])


def test_duplicate_json_keys_and_noncanonical_raw_rejected():
    lines = r.reference_envelope(example(), identity="a" * 32)
    with pytest.raises(ValueError):
        r.extract_envelope([lines[0].replace('"header":', '"header":{},"header":'), *lines[1:]])
    with pytest.raises(ValueError):
        r.unique([("x", 1), ("x", 2)])


def test_missing_identity_is_not_normalized_into_a_production_OID():
    raw = {"format": "mf-reader-raw-CI-source-reference-v1", "identities": [],
           "objects": [{"catalog": "pg_class", "oid": 999999,
                        "row": {k: 0 for k in r.FIELDS["pg_class"]}}]}
    with pytest.raises((ValueError, KeyError)):
        r.semantic_reference(raw)


def test_raw_cap_and_invalid_identity_fail_before_output():
    with pytest.raises(ValueError):
        r.reference_envelope({"x": "x" * r.MAX_RAW}, identity="a" * 32)
    with pytest.raises(ValueError):
        r.reference_envelope(example(), identity="not-an-identity")


def extension():
    return {"extconfig": [43, 44], "conditions_seal": {"is_null": False},
            "config_count": 2, "condition_count": 2}


@pytest.mark.parametrize("mutation", ["config_oid", "count", "condition_count", "nullness"])
def test_extension_configuration_alignment_rejects_mismatch(mutation):
    value = extension()
    r.extension_shape(value)
    if mutation == "config_oid":
        value["extconfig"][0] = True
    elif mutation == "count":
        value["config_count"] = 1
    elif mutation == "condition_count":
        value["condition_count"] = 1
    else:
        value["conditions_seal"]["is_null"] = True
    with pytest.raises(ValueError):
        r.extension_shape(value)


@pytest.mark.parametrize("field", sorted(r.RANGE_REFS))
def test_every_range_support_identity_is_typed_and_followed(field):
    value = {f: 100 + i for i, f in enumerate(r.RANGE_REFS)}
    targets = r.range_references(value)
    assert (r.RANGE_REFS[field], value[field]) in targets
    value[field] = True
    with pytest.raises(ValueError):
        r.range_references(value)


class Cursor:
    def __init__(self, status):
        self.statusmessage = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Recording:
    def __init__(self, rollback_error=False):
        self.calls, self.rollback_error = [], rollback_error
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=r.TransactionStatus.IDLE)

    def execute(self, statement, *, prepare=False):
        assert prepare is False
        self.calls.append(statement)
        if self.rollback_error and statement == "ROLLBACK":
            raise RuntimeError("fixture rollback failure")
        return Cursor("BEGIN" if statement.startswith("BEGIN") else "ROLLBACK" if statement == "ROLLBACK" else "SET")


def test_same_original_context_controls_and_one_confirmed_rollback():
    conn = Recording()
    with r.reference_transaction(conn):
        pass
    assert conn.calls == ["BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
                          "SET LOCAL search_path=pg_catalog", "SET LOCAL statement_timeout='5s'",
                          "SET LOCAL lock_timeout='5s'", "SET LOCAL transaction_timeout='15s'",
                          "SET LOCAL TimeZone='UTC'", "ROLLBACK"]


def test_original_validation_error_only_after_confirmed_rollback():
    conn = Recording()
    with pytest.raises(ValueError):
        with r.reference_transaction(conn):
            raise ValueError("fixture")
    assert conn.calls.count("ROLLBACK") == 1


@pytest.mark.parametrize("original", [True, False])
def test_rollback_failure_dominates_without_second_attempt(original):
    conn = Recording(rollback_error=True)
    with pytest.raises(r.AbortUnconfirmed):
        with r.reference_transaction(conn):
            if original:
                raise ValueError("fixture")
    assert conn.calls.count("ROLLBACK") == 1


def test_spent_wall_permits_no_further_SQL_even_cleanup():
    conn = Recording()
    with pytest.raises(r.AbortUnconfirmed):
        with r.reference_transaction(conn):
            raise r.WallSpent("fixture spent")
    assert "ROLLBACK" not in conn.calls


def test_existing_timer_rejects_before_any_SQL():
    conn = Recording()
    r.signal.setitimer(r.signal.ITIMER_REAL, 30)
    try:
        with pytest.raises(ValueError):
            with r.reference_transaction(conn):
                pytest.fail("existing timer accepted")
        assert conn.calls == []
    finally:
        r.signal.setitimer(r.signal.ITIMER_REAL, 0)


def test_nonidle_connection_rejected_before_any_SQL():
    conn = Recording()
    conn.info.transaction_status = r.TransactionStatus.INTRANS
    with pytest.raises(ValueError):
        with r.reference_transaction(conn):
            pytest.fail("active transaction accepted")
    assert conn.calls == []


@pytest.mark.parametrize("terminal", [r.TransactionStatus.INTRANS, r.TransactionStatus.UNKNOWN])
def test_rollback_tag_with_nonidle_driver_is_unconfirmed(terminal):
    conn = Recording()
    with pytest.raises(r.AbortUnconfirmed):
        with r.reference_transaction(conn):
            conn.info.transaction_status = terminal
    assert conn.calls.count("ROLLBACK") == 1


def mock_catalogue(monkeypatch, *, override=False, linked_roles=False):
    """Run the REAL compiler graph/old conversion loop without a connection."""
    class_ids = {n: 1000 + i for i, n in enumerate(sorted(r.FIELDS))}
    rows_by_class = {}
    for oid, name, category in ((23, "int4", "N"), (25, "text", "S")):
        value = {field: 0 for field in r.FIELDS["pg_type"]}
        value.update(oid=oid, typname=name, typcategory=category, typtype="b", typtypmod=-1,
                     typisdefined=True, typinput=0, typoutput=0)
        value.update({k + "_seal": {"is_null": True, "sha256": None, "utf8_bytes": 0}
                      for k in r.PRIVATE["pg_type"]})
        rows_by_class[("pg_type", oid)] = value
    roles = ("mfdata_admin", "mfdata_verified_generator", "mfdata_verified_reader", "mfdata_verified_admin")
    for i, role in enumerate(roles):
        value = {field: False for field in r.FIELDS["pg_authid"] if field != "rolconfig"}
        value.update(oid=40000 + i, rolname=role,
                     configuration_seal={"is_null": True, "sha256": None, "utf8_bytes": 0})
        rows_by_class[("pg_authid", value["oid"])] = value
    if linked_roles:
        for oid, role in ((45000, "synthetic_parent"), (45001, "synthetic_grantor"), (45002, "synthetic_acl_grantee")):
            value = deepcopy(rows_by_class[("pg_authid", 40000)])
            value.update(oid=oid, rolname=role)
            rows_by_class[("pg_authid", oid)] = value
    if override:
        rows_by_class[("pg_cast", 50000)] = dict(oid=50000, castsource=23, casttarget=25,
                                                castfunc=0, castcontext="e", castmethod="b")

    def fake(_conn, query, parameters=()):
        if query.startswith("SELECT current_database()"):
            return [dict(database="mfdata_reference_synthetic", principal="mfdata_admin",
                         current_user="mfdata_admin", version=180006, readonly="on",
                         isolation="repeatable read", search_path="pg_catalog", timezone="UTC",
                         statement_timeout="5s", lock_timeout="5s", transaction_timeout="15s")]
        if "relname FROM pg_catalog.pg_class" in query:
            return [{"oid": oid, "relname": name} for name, oid in class_ids.items()]
        if "pg_catalog.pg_identify_object" in query:
            cat = next(k for k, v in class_ids.items() if v == parameters[2])
            result = []
            for oid, sub in zip(parameters[0], parameters[1], strict=True):
                row = rows_by_class[(cat, oid)]
                name = row.get("typname", row.get("rolname", "synthetic-cast"))
                result.append({"oid": oid, "subid": sub, "value": {
                    "type": cat, "schema": "pg_catalog" if cat != "pg_authid" else None,
                    "name": name, "identity": name}})
            return result
        if query.startswith("SELECT pg_catalog.jsonb_build_object(") and " r WHERE r.oid=ANY" in query:
            cat = query.partition(" FROM pg_catalog.")[2].split()[0]
            cat = "pg_authid" if cat == "pg_roles" else cat
            return [{"value": deepcopy(rows_by_class[(cat, oid)])} for oid in parameters[0]]
        if "pg_catalog.pg_roles" in query and "rolname=ANY" in query:
            return [{"oid": 40000 + i, "rolname": name} for i, name in enumerate(roles)]
        if "FROM pg_catalog.pg_type WHERE" in query:
            return [{"oid": 23}, {"oid": 25}]
        if query.startswith("SELECT oid::bigint AS oid FROM pg_catalog.pg_cast"):
            return [{"oid": 50000}] if override else []
        if "FROM pg_catalog.pg_auth_members" in query:
            return [dict(roleid=45000, member=40002, grantor=45001, admin_option=False,
                         inherit_option=False, set_option=False)] if linked_roles else []
        if "FROM pg_catalog.pg_shdepend" in query:
            if linked_roles and parameters[0] == class_ids["pg_type"] and 23 in parameters[1]:
                return [dict(objid=23, objsubid=0, refclassid=class_ids["pg_authid"],
                             refobjid=45002, deptype="a")]
            return []
        if "FROM pg_catalog.pg_database WHERE" in query:
            return [dict(oid=999, datname="mfdata_reference_synthetic", encoding=6,
                         datlocprovider="c", datcollate="C", datctype="C", datlocale=None,
                         datcollversion=None, actual_collation_version=None,
                         icu_rules_seal={"is_null": True, "sha256": None, "utf8_bytes": 0})]
        return []

    monkeypatch.setattr(r, "rows", fake)
    return r.collect(object())


def test_all_four_governed_roles_are_in_real_compiler_output(monkeypatch):
    raw = mock_catalogue(monkeypatch)
    names = {row["row"]["rolname"] for row in raw["objects"] if row["catalog"] == "pg_authid"}
    assert names == {"mfdata_admin", "mfdata_verified_generator", "mfdata_verified_reader", "mfdata_verified_admin"}


def test_real_compiler_IO_directions_are_not_implicit_or_symmetric(monkeypatch):
    raw = mock_catalogue(monkeypatch)
    by_pair = {(row["source"], row["target"]): row for row in raw["conversions"]}
    assert by_pair[(23, 25)]["eligible_contexts"] == ["a", "e"]
    assert by_pair[(25, 23)]["eligible_contexts"] == ["e"]
    assert by_pair[(23, 25)]["reason"] == "TO_STRING_ASSIGNMENT_OR_EXPLICIT"
    assert by_pair[(25, 23)]["reason"] == "FROM_STRING_EXPLICIT_ONLY"


def test_catalogue_cast_overrides_real_compiler_generic_IO(monkeypatch):
    raw = mock_catalogue(monkeypatch, override=True)
    direct = next(row for row in raw["conversions"] if (row["source"], row["target"]) == (23, 25))
    assert direct["catalogue_override_oid"] == 50000
    assert direct["eligible_contexts"] == []
    assert direct["reason"] == "CATALOGUE_CAST_SUPPRESSES_GENERIC_IO"


def test_real_compiler_closes_membership_grantor_and_shared_ACL_roles(monkeypatch):
    raw = mock_catalogue(monkeypatch, linked_roles=True)
    names = {o["row"]["rolname"] for o in raw["objects"] if o["catalog"] == "pg_authid"}
    assert names == set(r.GOVERNED_ROLES) | {"synthetic_parent", "synthetic_grantor", "synthetic_acl_grantee"}
    assert raw["memberships"] == [dict(roleid=45000, member=40002, grantor=45001,
                                       admin_option=False, inherit_option=False, set_option=False)]
    assert any(edge[3:7] == ["pg_authid", 45002, 0, "shared:a"] for edge in raw["edges"])
    semantic = r.semantic_reference(raw)
    assert semantic["memberships"][0]["grantor"]["name"] == "synthetic_grantor"


def test_domain_base_direction_and_constraint_obligation_are_distinct():
    integer = dict(typtype="b", typcategory="N", typbasetype=0, typtypmod=-1,
                   typinput=0, typoutput=0, typelem=0, typsubscript=0, typnotnull=False)
    text = {**integer, "typcategory": "S"}
    domain = {**text, "typtype": "d", "typbasetype": 25, "typtypmod": 20, "typnotnull": True}
    values = r.io_conversion_records({("pg_type", 23): integer, ("pg_type", 25): text,
                                     ("pg_type", 50001): domain})
    outward = next(v for v in values if v["kind"] == "domain_base")
    inward = next(v for v in values if v["kind"] == "base_domain")
    assert (outward["source"], outward["target"]) == (50001, 25)
    assert outward["target_domain_requires_constraints"] is False
    assert (inward["source"], inward["target"]) == (25, 50001)
    assert inward["target_domain_requires_constraints"] is True
    io = next(v for v in values if v["kind"] == "automatic_io_candidate" and (v["source"], v["target"]) == (23, 50001))
    assert io["target_base"] == 25 and io["target_domain_chain"] == [50001]
    assert io["eligible_contexts"] == ["a", "e"] and io["target_domain_requires_constraints"] is True


def test_cyclic_domain_reference_rejects_instead_of_guessing_a_conversion():
    domain = dict(typtype="d", typcategory="S", typbasetype=50002, typtypmod=-1,
                  typinput=0, typoutput=0, typelem=0, typsubscript=0, typnotnull=False)
    with pytest.raises(ValueError):
        r.io_conversion_records({("pg_type", 50001): domain,
                                 ("pg_type", 50002): {**domain, "typbasetype": 50001}})


@pytest.mark.parametrize("catalogue_override", [False, True])
def test_internal_is_not_an_IO_path_even_with_a_catalogue_override(catalogue_override):
    scalar = dict(typtype="b", typcategory="S", typbasetype=0, typtypmod=-1,
                  typinput=0, typoutput=0, typelem=0, typsubscript=0, typnotnull=False)
    objects = {("pg_type", 25): scalar,
               ("pg_type", 2281): {**scalar, "typtype": "p", "typcategory": "P"}}
    if catalogue_override:
        objects[("pg_cast", 50000)] = dict(oid=50000, castsource=2281, casttarget=25,
                                           castfunc=0, castcontext="i", castmethod="b")
    values = r.io_conversion_records(objects)
    assert all(value["source"] != 2281 and value["target"] != 2281 for value in values)


def test_domain_over_internal_is_invalid_metadata_not_a_relabel_path():
    internal = dict(typtype="p", typcategory="P", typbasetype=0, typtypmod=-1,
                    typinput=0, typoutput=0, typelem=0, typsubscript=0, typnotnull=False)
    with pytest.raises(ValueError):
        r.io_conversion_records({("pg_type", 2281): internal,
                                 ("pg_type", 50001): {**internal, "typtype": "d", "typbasetype": 2281}})
