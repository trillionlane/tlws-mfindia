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
