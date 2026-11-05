import base64
from copy import deepcopy
import json

import pytest

from mfdataindia.datasets import policy as p


def entry(**changes):
    value = {"format": p.VERSION, "id": p.dataset_id("mf_research", "example"),
             "schema": "mf_research", "relation": "example", "owner": "mfdata_admin",
             "classification": "fund_research",
             "key_source": "physical_primary_key",
             "columns": [{"name": "id", "type": "int4", "nullable": False},
                         {"name": "name", "type": "text", "nullable": True}],
             "key_columns": ["id"]}
    return {**value, **changes}


def test_descriptor_and_cursor_are_stable_and_copy_isolated():
    original = entry()
    adopted = p.descriptor(original)
    assert adopted == original and adopted is not original
    original["columns"].clear()
    assert len(adopted["columns"]) == 2
    assert p.decode_cursor(p.encode_cursor(adopted, ["7"]), adopted) == ["7"]


@pytest.mark.parametrize("change,reason", [
    ({"schema": "pg_catalog"}, "DATASET_SCHEMA_OUTSIDE_DOMAIN"),
    ({"owner": p.READER}, "READER_CANNOT_OWN_DATASET"),
    ({"sql": "SELECT secret"}, "INVALID_DATASET_DESCRIPTOR"),
    ({"classification": "arbitrary"}, "INVALID_DATASET_CLASSIFICATION"),
    ({"key_columns": ["missing"]}, "INVALID_DATASET_KEY"),
    ({"key_columns": ["name"]}, "UNSAFE_DATASET_KEY"),
    ({"id": "d_" + "0" * 32}, "INVALID_DATASET_DESCRIPTOR"),
])
def test_unknown_incomplete_and_unsafe_enrollments_are_rejected(change, reason):
    with pytest.raises(p.DatasetError, match=reason):
        p.descriptor(entry(**change))


@pytest.mark.parametrize("column", [
    {"name": "id; DROP TABLE mf.funds", "type": "int4", "nullable": False},
    {"name": "id", "type": "untrusted_output", "nullable": False},
    {"name": "id", "type": "int4", "nullable": "false"},
])
def test_column_metadata_is_closed(column):
    with pytest.raises(p.DatasetError):
        p.descriptor(entry(columns=[column]))


@pytest.mark.parametrize("token", ["", "a" * 9000, "!!!", "eyJ2IjoxLCJ2IjoyfQ"])
def test_invalid_duplicate_and_oversized_cursors_are_rejected(token):
    with pytest.raises(p.DatasetError):
        p.decode_cursor(token, entry())


def test_cursor_does_not_cross_dataset_or_schema_change():
    original = entry()
    token = p.encode_cursor(original, ["7"])
    changed = deepcopy(original)
    changed["columns"].append({"name": "new", "type": "text", "nullable": True})
    with pytest.raises(p.DatasetError, match="STALE_OR_WRONG"):
        p.decode_cursor(token, changed)
    changed = entry(relation="other", id=p.dataset_id("mf_research", "other"))
    with pytest.raises(p.DatasetError, match="STALE_OR_WRONG"):
        p.decode_cursor(token, changed)


def test_extra_members_and_wrong_key_types_do_not_become_sql():
    data = {"v": 1, "dataset": entry()["id"], "schema_version": p.fingerprint(entry()), "after": [7]}
    token = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    with pytest.raises(p.DatasetError, match="INVALID_DATASET_CURSOR"):
        p.decode_cursor(token, entry())


def test_no_unique_key_uses_explicit_canonical_offset_not_fabricated_keyset():
    adopted = p.descriptor(entry(key_columns=[],key_source='canonical_offset'))
    assert p.decode_cursor(p.encode_cursor(adopted, ["100"]), adopted) == ["100"]
    with pytest.raises(p.DatasetError, match="INVALID_DATASET_CURSOR"):
        p.decode_cursor(p.encode_cursor(adopted, ["1;SELECT secret"]), adopted)


def test_declared_integral_view_key_keeps_nullable_metadata_and_null_cursor():
    columns = [{"name":"id","type":"int4","nullable":True}]
    adopted = p.descriptor(entry(columns=columns,key_source='declared_view_key'))
    assert adopted['columns'][0]['nullable'] is True
    assert p.decode_cursor(p.encode_cursor(adopted,[None]),adopted)==[None]
    with pytest.raises(p.DatasetError,match='UNSAFE_DATASET_KEY'):
        p.descriptor(entry(columns=[{"name":"id","type":"text","nullable":False}],key_source='declared_view_key'))
