"""Versioned factual taxonomy. Unknown evidence never becomes an ethical verdict."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import uuid

VERSION = 'mfdata-fund-tags-v1'
OWNER = 'mfdata_admin'
WRITER = 'mfdata_tag_writer'
READER = 'mfdata_verified_reader'
MAX_FUNDS = 50000
NAMESPACES = (
    'identity', 'asset-class', 'scheme-category', 'strategy', 'market-cap', 'geography',
    'sector', 'theme', 'instrument', 'duration-credit', 'benchmark', 'risk', 'terms',
    'esg-strategy', 'environment', 'values-screen', 'entity-exposure', 'assessment',
)
# Factual attributes stay typed even where their labels happen to be identical.
FIELDS = ('amc', 'scheme-category', 'scheme-structure', 'plan', 'option', 'vehicle',
          'asset-class', 'risk')
RESERVED = tuple(n for n in NAMESPACES if n not in {'identity', 'asset-class', 'scheme-category',
                                                  'instrument', 'risk', 'terms'})
DIMENSIONS = FIELDS + RESERVED
ROW_KEYS = frozenset('code amc_id amc scheme_name scheme_type scheme_category category_source '
                     'plan_type plan_source option_type is_etf in_scope last_seen '
                     'group_key family_id'.split())
CONTRACT = {'format': VERSION, 'dimensions': list(DIMENSIONS),
            'factual_inputs': 'AMFI database identity observations, not certified SID or portfolio evidence',
            'reserved': list(RESERVED), 'ethical_screening_enabled': False,
            'family_identity': 'existing UUID retained only with unique AMC/category membership',
            'source_rows_changed': False}


class TaggingError(ValueError):
    """Stable bounded reason, never underlying SQL or source values."""


def require(condition, reason):
    if not condition:
        raise TaggingError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def valid_family_id(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def validate(rows):
    require(type(rows) is list and 0 < len(rows) <= MAX_FUNDS, 'INVALID_FUND_CENSUS')
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == ROW_KEYS, 'INVALID_FUND_CENSUS_ROW')
        require(type(row['code']) is int and 0 < row['code'] < 2**31
                and row['code'] not in seen, 'INVALID_FUND_CENSUS_CODE')
        seen.add(row['code'])
        require(type(row['amc_id']) is int and row['amc_id'] > 0, 'INVALID_FUND_CENSUS_ROW')
        for key in ROW_KEYS - {'code', 'amc_id', 'is_etf', 'in_scope', 'last_seen',
                               'group_key', 'family_id'}:
            require(type(row[key]) is str and 0 < len(row[key]) <= 1000
                    and not any(ord(c) < 32 for c in row[key]), 'INVALID_FUND_CENSUS_ROW')
        require(type(row['is_etf']) is bool and type(row['in_scope']) is bool,
                'INVALID_FUND_CENSUS_ROW')
        for key in ('group_key', 'family_id'):
            require(row[key] is None or (type(row[key]) is str and len(row[key]) <= 1000),
                    'INVALID_FUND_CENSUS_ROW')
        try:
            require(row['last_seen'] is None or
                    (type(row['last_seen']) is str and
                     date.fromisoformat(row['last_seen']).isoformat() == row['last_seen']),
                    'INVALID_FUND_CENSUS_DATE')
        except (ValueError, TypeError):
            raise TaggingError('INVALID_FUND_CENSUS_DATE') from None
    require(len(canonical(rows)) <= 67108864, 'FUND_CENSUS_TOO_LARGE')


def prepare(rows):
    """Pure complete-census proposal; no IDs rewritten or external facts inferred."""
    validate(rows)
    rows = sorted(rows, key=lambda r: r['code'])
    catalogue_rows = [{key: row[key] for key in (
        'code', 'amc_id', 'amc', 'scheme_name', 'scheme_type', 'scheme_category',
        'category_source', 'plan_type', 'plan_source', 'option_type', 'is_etf',
        'in_scope', 'last_seen',
    )} for row in rows]
    family_rows = [{key: row[key] for key in ('code', 'group_key', 'family_id')}
                   for row in rows]
    catalogue_census_hash = digest(catalogue_rows)
    family_projection_hash = digest(family_rows)
    source_hash = digest({
        'catalogue_census_hash': catalogue_census_hash,
        'family_projection_hash': family_projection_hash,
    })
    policy_hash = digest({'contract': CONTRACT,
                          'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    snapshot = digest({'policy_hash': policy_hash, 'source_hash': source_hash})
    groups, family_ids, family_keys = {}, {}, {}
    for r in rows:
        if r['group_key']:
            groups.setdefault(r['group_key'], set()).add(
                (r['amc_id'], r['scheme_type'], r['scheme_category']))
            if r['family_id']:
                family_ids.setdefault(r['group_key'], set()).add(r['family_id'])
                family_keys.setdefault(r['family_id'], set()).add(r['group_key'])
    definitions, assessments, mapping_counts = {}, [], {}
    for r in rows:
        key = r['group_key']
        if not key or not r['family_id']:
            mapping = 'FAMILY_MISSING'
        elif (len(groups[key]) != 1 or not valid_family_id(r['family_id'])
              or family_ids.get(key) != {r['family_id']}
              or family_keys.get(r['family_id']) != {key}):
            mapping = 'FAMILY_AMBIGUOUS'
        else:
            mapping = 'FAMILY_VERIFIED'
        mapping_counts[mapping] = mapping_counts.get(mapping, 0) + 1
        # Exact observed strings, not lossy slug-derived primary keys.
        values = {'amc': r['amc'], 'scheme-category': r['scheme_category'],
                  'scheme-structure': r['scheme_type'], 'plan': r['plan_type'],
                  'option': r['option_type'], 'vehicle': 'ETF' if r['is_etf'] else 'NON_ETF',
                  'asset-class': None, 'risk': None}
        prefix = r['scheme_category'].split(' - ', 1)[0].strip().casefold()
        values['asset-class'] = {'equity scheme': 'equity', 'debt scheme': 'debt',
                                'hybrid scheme': 'hybrid'}.get(prefix)
        for dimension in DIMENSIONS:
            value = values.get(dimension)
            reason = 'SUPPORTED_IDENTITY_OBSERVATION'
            state = 'supported'
            if not r['in_scope']:
                value, state, reason = None, 'not-applicable', 'OUTSIDE_CURRENT_PLAN_SCOPE'
            elif dimension in RESERVED or dimension == 'risk':
                value, state, reason = None, 'not-assessed', 'AUTHORITATIVE_EVIDENCE_NOT_ASSESSED'
            elif r['last_seen'] is None:
                value, state, reason = None, 'insufficient-evidence', 'SOURCE_OBSERVATION_DATE_MISSING'
            elif dimension in {'scheme-category', 'asset-class'} and r['category_source'] != 'AMFI':
                value, state, reason = None, 'insufficient-evidence', 'CATEGORY_AUTHORITY_NOT_AMFI'
            elif dimension == 'plan' and r['plan_source'] != 'COLUMN':
                value, state, reason = None, 'insufficient-evidence', 'EXPLICIT_PLAN_EVIDENCE_MISSING'
            elif value is None or value in {'UNKNOWN', 'UNLABELLED'}:
                value, state, reason = None, 'insufficient-evidence', 'UNMAPPED_FACT_VALUE'
            tag_id = digest({'version': VERSION, 'dimension': dimension, 'value': value}) if value is not None else None
            if tag_id:
                definitions[tag_id] = {'tag_id': tag_id, 'policy_version': VERSION, 'dimension': dimension, 'value': value}
            assessments.append({'snapshot_id': snapshot, 'code': r['code'], 'dimension': dimension,
                'state': state, 'reason': reason, 'tag_id': tag_id, 'value': value,
                'family_id': r['family_id'] if mapping == 'FAMILY_VERIFIED' else None,
                'mapping_state': mapping, 'currency': 'not_certified',
                'basis': 'observed_database_identity', 'in_scope': r['in_scope'], 'as_of': r['last_seen'],
                'evidence_hash': digest(r), 'policy_version': VERSION})
    counts = {s: sum(a['state'] == s for a in assessments)
              for s in ('supported', 'insufficient-evidence', 'not-assessed', 'not-applicable')}
    return {'snapshot_id': snapshot, 'source_hash': source_hash,
            'catalogue_census_hash': catalogue_census_hash,
            'family_projection_hash': family_projection_hash, 'policy_hash': policy_hash,
            'funds': len(rows), 'in_scope_funds': sum(r['in_scope'] for r in rows),
            'mapping_counts': mapping_counts, 'counts': counts,
            'observations': rows, 'definitions': sorted(definitions.values(), key=lambda d: d['tag_id']),
            'assessments': assessments, 'consumer_acceptance': 'NOT_OBSERVED'}
