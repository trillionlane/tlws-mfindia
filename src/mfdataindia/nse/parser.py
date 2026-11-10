"""Strict SCH43 fixture parser; all original cells survive in private evidence.

Semantic holds never become positive facility eligibility. Structural errors
block the complete snapshot, rather than dropping a malformed row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re

from . import contract as c
from .schema import FIELDS43, HEADERS43, HEADERS44

NUMBER = re.compile(r'(?:0|[0-9]+)(?:\.[0-9]+)?\Z')
ISIN = re.compile(r'[A-Z][A-Z0-9]{11}\Z')


@dataclass(frozen=True)
class Parsed:
    manifest: dict
    raw: bytes
    rows: tuple
    semantic_hash: str


def _value(text, kind, width):
    if not text:
        return None
    c.require(not any(ord(ch) < 32 or ord(ch) == 127 for ch in text), 'INVALID_CELL')
    c.require(width is None or len(text) <= int(width), 'CELL_WIDTH_EXCEEDED')
    if kind == 'NUMERIC':
        c.require(NUMBER.fullmatch(text), 'INVALID_DECIMAL')
        try:
            value = Decimal(text)
        except InvalidOperation:
            raise c.ImportError('INVALID_DECIMAL') from None
        c.require(value.is_finite(), 'INVALID_DECIMAL')
        return value
    if kind == 'DATE':
        try:
            value = datetime.strptime(text, '%d-%m-%Y').date()
            c.require(value.strftime('%d-%m-%Y') == text, 'INVALID_DATE')
            return value
        except ValueError:
            raise c.ImportError('INVALID_DATE') from None
    return text


def parse(raw, manifest):
    meta = c.manifest(manifest)
    c.require(type(raw) is bytes and 0 < len(raw) <= c.MAX_BYTES, 'INVALID_FILE')
    # Independent expected values are checked BEFORE decoding or any DB effect.
    c.require(len(raw) == meta['expected_bytes']
              and sha256(raw).hexdigest() == meta['expected_sha256'], 'FULL_BYTES_MISMATCH')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        raise c.ImportError('INVALID_ENCODING') from None
    c.require('\ufeff' not in text and '\x00' not in text and '\r' not in text.replace('\r\n', ''), 'INVALID_ENCODING')
    lines = text.replace('\r\n', '\n').split('\n')
    if lines[-1] == '':
        lines.pop()  # One LF/CRLF terminator, not arbitrary blank records.
    c.require(len(lines) > 1, 'EMPTY_OR_ERROR_FILE')
    header = tuple(lines[0].split('|'))
    c.require(header != HEADERS44, 'SCHEMA_UNVERIFIED')
    c.require(header == HEADERS43, 'INVALID_HEADER')
    c.require(len(lines) - 1 == meta['expected_rows'], 'FULL_ROW_COUNT_MISMATCH')
    rows, seen = [], {}
    for ordinal, line in enumerate(lines[1:], 1):
        c.require(0 < len(line.encode()) <= c.MAX_ROW_BYTES, 'INVALID_ROW_BYTES')
        cells = tuple(line.split('|'))
        c.require(len(cells) == 43, 'INVALID_COLUMN_COUNT')
        values = [_value(text, kind, width) for text, (_, kind, width) in zip(cells, FIELDS43)]
        c.require(not cells[18] and not cells[42], 'DOCUMENT_BLANK_FIELD_NONEMPTY')
        c.require(not cells[4] or ISIN.fullmatch(cells[4]), 'INVALID_ISIN')
        for i in (15, 24):
            if cells[i]:
                try:
                    parsed = datetime.strptime(cells[i], '%H:%M:%S')
                    c.require(parsed.strftime('%H:%M:%S') == cells[i], 'INVALID_TIME')
                except ValueError:
                    raise c.ImportError('INVALID_TIME') from None
        for lower, upper in ((21, 22),):
            if values[lower] is not None and values[upper] is not None:
                c.require(values[lower] <= values[upper], 'CONTRADICTORY_BOUNDS')
        if values[35] is not None and values[36] is not None:
            c.require(values[35] <= values[36], 'CONTRADICTORY_DATES')
        # Source serial does not distinguish business rules. Preserve identical
        # rows privately; a different payload under the same candidate blocks.
        key = tuple(cells[i] for i in (5, 1, 4, 7, 10, 17))
        body = cells[1:]
        c.require(key not in seen or seen[key] == body, 'CONFLICTING_DUPLICATE')
        seen[key] = body
        row = normalize(ordinal, cells, values)
        rows.append(row)
    return Parsed(meta, raw, tuple(rows), c.digest([r['cells'] for r in rows]))


def normalize(ordinal, cells, values):
    holds = {
        'maximum_purchase_amount': 'SCH_FIELD14',
        'additional_purchase_maximum': 'SCH_FIELD14',
        'minimum_redemption_quantity': 'SCH_BLANK19', 'reopening_date': 'SCH_BLANK43',
        'purchase_cutoff_instant': 'SCH_CUTOFF', 'redemption_cutoff_instant': 'SCH_CUTOFF',
        'settlement_join': 'SCH_SETTLEMENT', 'source_metadata': 'SCH_SOURCE_METADATA',
    }
    normalized = {key: None for key in holds if key != 'source_metadata'}
    normalized.update(purchase_eligibility='UNKNOWN', redemption_eligibility='UNKNOWN')
    # Explicit synthetic-only tokens. Unknown is never the document's implicit
    # "else Regular". Source semantics not reviewed here remain field holds.
    plan = {'R': 'REGULAR', 'D': 'DIRECT'}.get(cells[7])
    flags = {'purchase_allowed': (9, {'Y': True, 'N': False}),
             'redemption_allowed': (16, {'1': True, '0': False}),
             'amc_active': (26, {'1': True, '0': False}),
             'dividend_reinvestment': (27, {'1': True, '0': False})}
    for name, (i, tokens) in flags.items():
        normalized[name] = tokens.get(cells[i])
        if normalized[name] is None:
            holds[name] = 'SCH_FLAGS_MODES' if cells[i] else 'SCH_REQUIREDNESS'
    for name, i in (('purchase_mode', 10), ('redemption_mode', 17)):
        normalized[name] = cells[i] if cells[i] in {'P', 'D', 'DP'} else None
        if normalized[name] is None:
            holds[name] = 'SCH_FLAGS_MODES' if cells[i] else 'SCH_REQUIREDNESS'
    numeric = {'minimum_purchase_amount': 11, 'additional_purchase_amount': 12,
               'purchase_multiple': 14, 'redemption_quantity_multiple': 19,
               'maximum_redemption_quantity': 20, 'minimum_redemption_amount': 21,
               'maximum_redemption_amount': 22, 'redemption_amount_multiple': 23,
               'face_value': 34}
    for name, i in numeric.items():
        normalized[name] = str(values[i]) if values[i] is not None else None
        if values[i] is None:
            holds[name] = 'SCH_REQUIREDNESS'
        elif ('multiple' in name or name == 'face_value') and values[i] <= 0:
            c.require(False, 'NONPOSITIVE_RULE')
    for name, i in (('sip_flag', 28), ('stp_flag', 29), ('swp_flag', 30),
                    ('switch_flag', 31), ('exit_load', 38), ('lock_in_period', 40)):
        # Later-family eligibility / unverified unit and grammar are withheld.
        normalized[name] = None
        holds[name] = 'SCH_FLAGS_MODES' if 'flag' in name else 'SCH_NUMERIC'
    return {'ordinal': ordinal, 'cells': cells, 'plan': plan,
            'normalized': normalized, 'field_holds': holds}
