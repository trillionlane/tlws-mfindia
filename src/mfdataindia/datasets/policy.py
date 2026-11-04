"""Small, versioned consumer-enrollment contract; no connections or grants."""
from __future__ import annotations

import base64
import hashlib
import json
import re

from mfdataindia.review import broad_reader_policy as installed_reader_policy

READER = installed_reader_policy.READER
OWNER = installed_reader_policy.OWNER
CURRENT_SCHEMAS = installed_reader_policy.SCHEMAS

VERSION = "mfdata-consumer-datasets-v1"
MAX_PAGE = 100
MAX_CURSOR_BYTES = 4096
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_BINARY_CHUNK = 128 * 1024
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
DATASET_ID = re.compile(r"d_[0-9a-f]{32}\Z")
CLASSIFICATIONS = {"fund_research", "source_evidence", "internal_control"}
LOGICAL_KEY_TYPES = frozenset('int2 int4 int8 date timestamp timestamptz uuid'.split())
# Built-in data types only: output routines from arbitrary user-defined types
# must not be invoked by a generic reader. Types are checked by namespace too.
TYPES = frozenset("bool int2 int4 int8 numeric float4 float8 text varchar bpchar date timestamp timestamptz json jsonb uuid bytea".split())
TYPES = TYPES | frozenset('_' + name for name in TYPES if name != 'bytea')


class DatasetError(ValueError):
    """Only fixed, value-free reason codes cross the HTTP/log boundary."""


def require(ok, reason):
    if not ok:
        raise DatasetError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def dataset_id(schema, relation):
    require(type(schema) is str and IDENTIFIER.fullmatch(schema), "INVALID_DATASET_DESCRIPTOR")
    require(type(relation) is str and IDENTIFIER.fullmatch(relation), "INVALID_DATASET_DESCRIPTOR")
    require(schema == "mf" or schema.startswith("mf_"), "DATASET_SCHEMA_OUTSIDE_DOMAIN")
    return "d_" + fingerprint([schema, relation])[:32]


def descriptor(value):
    require(type(value) is dict and set(value) == {
        "format", "id", "schema", "relation", "owner", "classification", "columns", "key_columns", "key_source",
    }, "INVALID_DATASET_DESCRIPTOR")
    require(value["format"] == VERSION and value["id"] == dataset_id(value["schema"], value["relation"]),
            "INVALID_DATASET_DESCRIPTOR")
    require(type(value["owner"]) is str and IDENTIFIER.fullmatch(value["owner"]), "INVALID_DATASET_DESCRIPTOR")
    require(value["owner"] != READER, "READER_CANNOT_OWN_DATASET")
    require(type(value["classification"]) is str and value["classification"] in CLASSIFICATIONS,
            "INVALID_DATASET_CLASSIFICATION")
    require(type(value["columns"]) is list and 0 < len(value["columns"]) <= 256, "INVALID_DATASET_COLUMNS")
    names = []
    for column in value["columns"]:
        require(type(column) is dict and set(column) == {"name", "type", "nullable"}, "INVALID_DATASET_COLUMNS")
        require(type(column["name"]) is str and IDENTIFIER.fullmatch(column["name"]), "INVALID_DATASET_COLUMNS")
        require(type(column["type"]) is str and column["type"] in TYPES
                and type(column["nullable"]) is bool, "UNSUPPORTED_DATASET_TYPE")
        names.append(column["name"])
    require(len(names) == len(set(names)), "DUPLICATE_DATASET_COLUMN")
    keys = value["key_columns"]
    require(type(keys) is list and 0 <= len(keys) <= 8
            and all(type(k) is str and k in names for k in keys)
            and len(keys) == len(set(keys)), "INVALID_DATASET_KEY")
    columns = {c["name"]: c for c in value["columns"]}
    require(value['key_source'] in {'physical_primary_key','declared_view_key','canonical_offset'}, 'INVALID_DATASET_KEY')
    if value['key_source'] == 'declared_view_key':
        require(bool(keys) and all(columns[k]['type'] in LOGICAL_KEY_TYPES for k in keys), 'UNSAFE_DATASET_KEY')
    else:
        require((bool(keys) and value['key_source']=='physical_primary_key')
                or (not keys and value['key_source']=='canonical_offset'), 'INVALID_DATASET_KEY')
        require(all(not columns[k]["nullable"] and not columns[k]["type"].startswith('_')
                and columns[k]["type"] not in {"bytea", "json", "jsonb", "float4", "float8"}
                    for k in keys), "UNSAFE_DATASET_KEY")
    # An enrollment is deliberate current authority, not an HTTP-selected role,
    # arbitrary SQL fragment, wildcard, new function grant or public permission.
    return json.loads(canonical(value))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DatasetError("DUPLICATE_CURSOR_MEMBER")
        result[key] = value
    return result


def encode_cursor(entry, keys):
    body = {"v": 1, "dataset": entry["id"], "schema_version": fingerprint(entry), "after": keys}
    raw = canonical(body)
    require(len(raw) <= MAX_CURSOR_BYTES, "DATASET_CURSOR_TOO_LARGE")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token, entry):
    require(type(token) is str and 0 < len(token) <= MAX_CURSOR_BYTES * 2
            and re.fullmatch(r"[A-Za-z0-9_-]+", token), "INVALID_DATASET_CURSOR")
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        require(len(raw) <= MAX_CURSOR_BYTES, "INVALID_DATASET_CURSOR")
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: None)
    except (ValueError, UnicodeError):
        raise DatasetError("INVALID_DATASET_CURSOR") from None
    require(type(value) is dict and set(value) == {"v", "dataset", "schema_version", "after"}
            and type(value["v"]) is int and value["v"] == 1 and value["dataset"] == entry["id"]
            and value["schema_version"] == fingerprint(entry), "STALE_OR_WRONG_DATASET_CURSOR")
    keys = value["after"]
    require(type(keys) is list and len(keys) == (len(entry["key_columns"]) or 1)
            and all(k is None or (type(k) is str and len(k.encode()) <= 1024) for k in keys), "INVALID_DATASET_CURSOR")
    if entry['key_columns']:
        columns = {c['name']:c for c in entry['columns']}
        require(all(value is not None or columns[name]['nullable']
                    for name,value in zip(entry['key_columns'],keys)), 'INVALID_DATASET_CURSOR')
    if not entry["key_columns"]:
        require(type(keys[0]) is str and keys[0].isascii() and keys[0].isdigit() and int(keys[0]) < 2**63,
                "INVALID_DATASET_CURSOR")
    # A cursor is a bounded position, not authority or SQL. Changing a position
    # cannot increase the dataset/column/read scope; every use revalidates it.
    return keys
