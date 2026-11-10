"""Strict local manifests. FULL is an independently supplied byte/count binding.

Only the reviewed synthetic fixture profile is executable in NSE01-A. Neither a
clean EOF nor a caller's `FULL` string certifies an original export's scope.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import re
from uuid import UUID

from .schema import SCHEMA43, VERSION

MAX_BYTES = 64 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_ROW_BYTES = 16 * 1024
HASH = re.compile(r'[0-9a-f]{64}\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')
FAMILIES = frozenset({'SCH', 'SIP', 'STP', 'SWP', 'NAV', 'SET'})


class ImportError(ValueError):
    """Fixed reasons only. Never wrap provider/SQL/input exception text."""


def require(condition, reason):
    if not condition:
        raise ImportError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'DUPLICATE_MEMBER')
        value[key] = item
    return value


def decode(raw):
    require(type(raw) is bytes and len(raw) <= 256 * 1024, 'INVALID_METADATA')
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ImportError('INVALID_METADATA')))
    except (ValueError, UnicodeError):
        raise ImportError('INVALID_METADATA') from None


def operation(value):
    try:
        require(type(value) is str and str(UUID(value)) == value, 'INVALID_OPERATION')
    except (ValueError, AttributeError):
        raise ImportError('INVALID_OPERATION') from None
    return value


def manifest(value):
    keys = {'format', 'family', 'environment', 'schema', 'normalization', 'source_date',
            'scope', 'completeness_basis', 'expected_sha256', 'expected_bytes',
            'expected_rows', 'reference'}
    require(type(value) is dict and set(value) == keys, 'INVALID_MANIFEST')
    require(type(value['family']) is str and value['family'] in FAMILIES, 'INVALID_FAMILY')
    require(value['family'] == 'SCH', 'FAMILY_NOT_IMPLEMENTED')
    require(value['format'] == 'nse01-full-manifest-v1', 'INVALID_MANIFEST')
    require(value['schema'] == SCHEMA43, 'SCHEMA_UNVERIFIED')
    require(value['environment'] == 'FIXTURE' and value['scope'] == 'FULL'
            and value['completeness_basis'] == 'SYNTHETIC_KNOWN_COMPLETE', 'FULL_SCOPE_UNVERIFIED')
    require(value['normalization'] == VERSION, 'NORMALIZATION_UNVERIFIED')
    require(type(value['expected_sha256']) is str and HASH.fullmatch(value['expected_sha256']), 'INVALID_MANIFEST')
    for name, maximum in (('expected_bytes', MAX_BYTES), ('expected_rows', MAX_ROWS)):
        require(type(value[name]) is int and 0 < value[name] <= maximum, 'INVALID_MANIFEST')
    require(type(value['reference']) is str and NAME.fullmatch(value['reference']), 'INVALID_MANIFEST')
    try:
        require(type(value['source_date']) is str
                and date.fromisoformat(value['source_date']).isoformat() == value['source_date'], 'INVALID_SOURCE_DATE')
    except ValueError:
        raise ImportError('INVALID_SOURCE_DATE') from None
    return decode(canonical(value))
