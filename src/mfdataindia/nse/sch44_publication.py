"""Gated SCH44 UAT publication through the existing atomic SCH transaction."""

from collections import Counter, defaultdict
from hashlib import sha256

from . import contract as c, mapping, sch44, store
from .parser import Parsed

APPROVAL_FORMAT = 'nse-sch44-uat-publish-v1'
APPROVAL_FIELDS = {
    'format', 'environment', 'scope', 'member_scope_reference', 'scope_evidence_sha256',
    'date_evidence_sha256', 'source_date', 'expected_sha256', 'expected_rows', 'expected_bytes',
    'crosswalk_sha256', 'catalogue_sha256', 'connector_revision', 'expected_head',
}


def prepare(raw, receipt, approval, approval_sha256, walk):
    """Return only inputs bound to the separate, operator-reviewed approval."""
    c.require(type(approval) is dict and set(approval) == APPROVAL_FIELDS
              and approval['format'] == APPROVAL_FORMAT and approval['environment'] == 'UAT'
              and approval['scope'] == 'FULL', 'SCH44_APPROVAL_INVALID')
    c.require(type(approval_sha256) is str and c.HASH.fullmatch(approval_sha256)
              and c.digest(approval) == approval_sha256, 'SCH44_APPROVAL_UNTRUSTED')
    c.require(type(approval['member_scope_reference']) is str
              and c.NAME.fullmatch(approval['member_scope_reference']), 'SCH44_SCOPE_UNVERIFIED')
    for field in ('scope_evidence_sha256', 'date_evidence_sha256', 'expected_sha256',
                  'crosswalk_sha256', 'catalogue_sha256'):
        c.require(type(approval[field]) is str and c.HASH.fullmatch(approval[field]),
                  'SCH44_APPROVAL_INVALID')
    if approval['expected_head'] is not None:
        c.operation(approval['expected_head'])
    source_date = receipt.get('export_date')
    c.require(source_date is not None, 'SCH44_EXPORT_DATE_UNVERIFIED')
    sch44._date(source_date)
    rows = sch44.parse(raw)
    digest = sha256(raw).hexdigest()
    sch44._api_capture(receipt, digest, len(raw), source_date, len(rows))
    c.require(type(approval['expected_rows']) is int and approval['expected_rows'] == len(rows)
              and type(approval['expected_bytes']) is int and approval['expected_bytes'] == len(raw)
              and approval['source_date'] == source_date and approval['expected_sha256'] == digest
              and approval['connector_revision'] == receipt['connector_revision'],
              'SCH44_APPROVAL_SOURCE_DIFFERS')
    mapping.validate_crosswalk(walk)
    c.require(c.digest(walk) == approval['crosswalk_sha256'], 'SCH44_CROSSWALK_DIFFERS')
    manifest = {**approval, 'family': 'SCH', 'schema': sch44.PROFILE,
                'normalization': sch44.PROFILE, 'approval_sha256': approval_sha256}
    return Parsed(manifest, raw, tuple(rows), c.digest([cells for cells, _ in rows]))


def _resolve_rows(rows, walk, funds):
    by_code = {fund['amfi_scheme_code']: fund for fund in funds}
    isin_slots = defaultdict(set)
    for code, fund in by_code.items():
        for slot in mapping.SLOTS:
            if fund[slot]:
                isin_slots[fund[slot]].add((code, slot))
    routes = defaultdict(list)
    for target in walk['targets']:
        routes[target['amc_code'], target['scheme_code'], target['isin']].append(target)

    source_keys = Counter((cells[5], cells[1], cells[4], cells[7], cells[10], cells[17])
                          for cells, _ in rows)
    result = []
    for ordinal, (cells, holds) in enumerate(rows, 1):
        key = (cells[5], cells[1], cells[4], cells[7], cells[10], cells[17])
        c.require(source_keys[key] == 1, 'SCH44_DUPLICATE_SOURCE_IDENTITY')
        targets = routes[key[:3]]
        state, reason = sch44._resolve(cells, walk, targets, by_code, isin_slots[cells[4]])
        c.require(state in {'EXACT', 'OUT_OF_SCOPE'}, 'SCH44_MAPPING_NOT_COMPLETE')
        target = targets[0]
        candidate = {name: target[name] for name in ('amfi_scheme_code', 'isin_slot', 'option')}
        result.append({
            'ordinal': ordinal, 'cells': cells, 'plan': target['plan'], 'normalized': {},
            'field_holds': holds, 'status': 'MAPPED' if state == 'EXACT' else state,
            'row_reason': None if state == 'EXACT' else reason, 'candidates': [candidate],
        })
    c.require(any(row['status'] == 'MAPPED' for row in result), 'SCH44_NO_EXPOSED_ROWS')
    return result


def publish(conn, raw, receipt, approval, approval_sha256, walk, operation_id):
    c.operation(operation_id)
    parsed = prepare(raw, receipt, approval, approval_sha256, walk)
    return store._publish_transaction(conn, parsed, walk, operation_id,
                                      expected_head=approval['expected_head'],
                                      resolve_rows=_resolve_rows,
                                      mapping_pin=approval['catalogue_sha256'])
