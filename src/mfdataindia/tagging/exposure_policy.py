"""Dated observed exposure evidence, separate from mandate or ethical verdicts.

Pure derivation only. The runtime must obtain the snapshot through the existing
sealed portfolio/reader boundary and the registry through trusted composition.
A self-consistent JSON hash is not independent source certification.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext, Context, ROUND_HALF_EVEN
import hashlib
from pathlib import Path
import re

from pydantic import ValidationError

from mfdataindia.enrichment import portfolio_schemas, portfolios
from mfdataindia.enrichment.evidence import valid_isin
from mfdataindia.review.policy import fingerprint
from .policy import TaggingError, require, canonical

VERSION = 'mfdata-observed-fund-exposures-v1'
REGISTRY_VERSION = 'mfdata-security-entities-v1'
DIMENSIONS = ('issuer-domicile', 'sector', 'theme')
HASH = re.compile(r'[0-9a-f]{64}')
ENTITY = re.compile(r'[a-z][a-z0-9-]{0,31}:[A-Za-z0-9][A-Za-z0-9._-]{0,95}')
VALUE = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
MAX_REGISTRY_RECORDS = 10000


def _date(value):
    require(type(value) is str, 'EXPOSURE_EVIDENCE_DIFFERS')
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise TaggingError('EXPOSURE_EVIDENCE_DIFFERS') from None
    require(result.isoformat() == value, 'EXPOSURE_EVIDENCE_DIFFERS')
    return result


def _interval(row):
    start = _date(row['valid_from'])
    end = _date(row['valid_until']) if row['valid_until'] is not None else None
    require(end is None or end >= start, 'EXPOSURE_EVIDENCE_DIFFERS')
    require(type(row['evidence_hash']) is str and HASH.fullmatch(row['evidence_hash']),
            'EXPOSURE_EVIDENCE_DIFFERS')


def _active(row, when):
    return row['valid_from'] <= when and (row['valid_until'] is None or when <= row['valid_until'])


def _bounded(value, maximum, code):
    try:
        raw = canonical(value)
    except (ValueError, TypeError):
        raise TaggingError(code) from None
    require(len(raw) <= maximum, code)


def validate_registry(document):
    _bounded(document, 8*1024*1024, 'EXPOSURE_REGISTRY_DIFFERS')
    require(type(document) is dict and set(document) == {'format', 'records'}
            and document['format'] == REGISTRY_VERSION and type(document['records']) is list
            and len(document['records']) <= MAX_REGISTRY_RECORDS, 'EXPOSURE_REGISTRY_DIFFERS')
    by_security = {}
    for record in document['records']:
        require(type(record) is dict and set(record) == {'security_id', 'entity_id', 'entity_kind', 'state',
                'valid_from', 'valid_until', 'evidence_hash', 'attributes'}, 'EXPOSURE_REGISTRY_DIFFERS')
        security = record['security_id']
        require(type(security) is str and valid_isin(security), 'EXPOSURE_REGISTRY_DIFFERS')
        require(type(record['entity_id']) is str and ENTITY.fullmatch(record['entity_id'])
                and record['entity_kind'] in ('company','sovereign','fund','other')
                and record['state'] in ('verified', 'ambiguous') and type(record['attributes']) is list
                and len(record['attributes']) <= 50, 'EXPOSURE_REGISTRY_DIFFERS')
        _interval(record)
        for attribute in record['attributes']:
            require(type(attribute) is dict and set(attribute) == {'dimension', 'value',
                    'valid_from', 'valid_until', 'evidence_hash'}, 'EXPOSURE_REGISTRY_DIFFERS')
            require(attribute['dimension'] in DIMENSIONS and type(attribute['value']) is str
                    and VALUE.fullmatch(attribute['value']), 'EXPOSURE_REGISTRY_DIFFERS')
            if attribute['dimension'] == 'issuer-domicile':
                require(re.fullmatch(r'[a-z]{2}', attribute['value']), 'EXPOSURE_REGISTRY_DIFFERS')
            _interval(attribute)
        by_security.setdefault(security, []).append(record)
    return by_security


def _number(value, *, monetary=False):
    code = 'EXPOSURE_PORTFOLIO_DIFFERS' if monetary else 'EXPOSURE_WEIGHT_DIFFERS'
    if monetary:
        # Reuse the frozen monetary-token rule, not a percent magnitude cap.
        try:
            return portfolios.decimal(value)
        except (ValueError, InvalidOperation):
            raise TaggingError(code) from None
    # A frozen source percent can expand to 129 decimal characters after its
    # permitted e-99 conversion; do not truncate or turn it into a censor.
    require(type(value) is str and 0 < len(value) <= 160, code)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise TaggingError(code) from None
    require(number.is_finite() and -100 <= number <= 200, code)
    return number


def _weight(holding):
    weight = holding['weight_percent']
    if weight['basis'] == 'reported':
        require(weight['value'] is not None and weight['upper_bound_exclusive'] is None,
                'EXPOSURE_WEIGHT_DIFFERS')
        number = _number(weight['value'])
        return number, number, False
    require(weight['value'] is None and weight['upper_bound_exclusive'] is not None,
            'EXPOSURE_WEIGHT_DIFFERS')
    bound = _number(weight['upper_bound_exclusive'])
    require(bound == Decimal('0.01'), 'EXPOSURE_WEIGHT_DIFFERS')
    return Decimal(0), bound, True


def _text(value):
    return format(value, 'f')


def _strict_source_integers(document):
    """Reject JSON booleans/floats before non-strict model coercion.

    Snapshot hashes prove byte-level consistency, not that a self-consistent
    document used the contract's exact scalar types.  Python also considers
    ``True == 1``, so the later model-dump equality check cannot do this job.
    """
    try:
        values = [
            document['code'],
            document['identity']['code'],
            document['identity_evidence']['amfi_code'],
            document['holdings_count'],
            document['grand_total_row'],
            *(item['row'] for item in document['holdings']),
            *(item['row'] for item in document['subtotals']),
            *(item['row'] for item in document['derivative_positions']),
        ]
    except (KeyError, TypeError):
        raise TaggingError('EXPOSURE_PORTFOLIO_DIFFERS') from None
    require(all(type(value) is int for value in values)
            and len(document['derivative_positions']) <= 1000,
            'EXPOSURE_PORTFOLIO_DIFFERS')


def _position_weight(lower, upper, exclusive):
    return {
        'lower': _text(lower),
        'upper': _text(upper),
        'upper_exclusive': exclusive,
        'unit': 'percent_of_original_disclosed_AUM',
    }


def derive(document, registry, *, observed_on):
    """Keep original AUM denominator and explicit unknowns; never infer absence.

    Registry ambiguity withholds the affected identity, never an unrelated fund.
    Dates apply at portfolio date. Domicile is not revenue geography; a theme
    attribute is not a scheme mandate, sustainability screen or recommendation.
    """
    when_observed = _date(observed_on)
    require(type(document) is dict and type(document.get('code')) is int
            and 0 < document['code'] < 2**31, 'EXPOSURE_PORTFOLIO_DIFFERS')
    _bounded(document, 16*1024*1024, 'EXPOSURE_PORTFOLIO_DIFFERS')
    _strict_source_integers(document)
    try:
        snapshot = portfolio_schemas.Snapshot.model_validate(document).model_dump(mode='json')
    except (ValidationError, ValueError, TypeError):
        raise TaggingError('EXPOSURE_PORTFOLIO_DIFFERS') from None
    require(snapshot == document and len(document['holdings']) == document['holdings_count']
            and document['snapshot_id'] == fingerprint({k:v for k,v in document.items() if k != 'snapshot_id'})
            and document['identity_hash'] == fingerprint(document['identity'])
            and document['policy_hash'] == fingerprint(portfolios.policy()), 'EXPOSURE_PORTFOLIO_DIFFERS')
    for key in ('snapshot_id', 'identity_hash', 'source_id', 'identity_source_id', 'content_hash',
                'identity_content_hash', 'member_hash', 'sheet_cells_hash', 'engine_code_hash', 'policy_hash'):
        require(type(document[key]) is str and HASH.fullmatch(document[key]), 'EXPOSURE_PORTFOLIO_DIFFERS')
    as_of = _date(snapshot['disclosure_as_of'])
    require(as_of <= when_observed and snapshot['identity_evidence']['amfi_code'] == snapshot['code'],
            'EXPOSURE_PORTFOLIO_DIFFERS')
    by_security = validate_registry(registry)
    kinds = {}
    for records in by_security.values():
        for record in records:
            if _active(record,as_of.isoformat()):
                kinds.setdefault(record['entity_id'],set()).add(record['entity_kind'])
    entities, attributes, securities, positions, reasons = {}, {}, [], [], Counter()
    mapped_rows, rows = 0, set()
    with localcontext(Context(prec=50,rounding=ROUND_HALF_EVEN)):
        weights = [_weight(h) for h in snapshot['holdings']]
        lower_total = sum((w[0] for w in weights), Decimal(0))
        upper_total = sum((w[1] for w in weights), Decimal(0))
        require(lower_total-Decimal('0.0005') <= 100 <= upper_total+Decimal('0.0005'),
                'EXPOSURE_PORTFOLIO_DIFFERS')
        total = _number(snapshot['grand_total_inr_lakh'],monetary=True)
        require(total > 0 and abs(sum((_number(h['market_value_inr_lakh'],monetary=True) for h in snapshot['holdings']),Decimal(0))-total)
                <= Decimal('0.005')*(len(snapshot['holdings'])+1), 'EXPOSURE_PORTFOLIO_DIFFERS')
        entity_attributes, entity_attribute_records = {}, {}
        for records in by_security.values():
            for record in records:
                if record['state'] != 'verified' or not _active(record,as_of.isoformat()):
                    continue
                entity = record['entity_id']
                for attribute in record['attributes']:
                    if _active(attribute,as_of.isoformat()):
                        entity_attributes.setdefault((entity,attribute['dimension']),set()).add(attribute['value'])
                        entity_attribute_records.setdefault(entity,[]).append(attribute)
        for holding in snapshot['holdings']:
            require(type(holding['row']) is int and holding['row'] > 0 and holding['row'] not in rows,
                    'EXPOSURE_PORTFOLIO_DIFFERS')
            rows.add(holding['row'])
            lower, upper, exclusive = _weight(holding)
            require(type(holding['name']) is str and 0 < len(holding['name']) <= 500,
                    'EXPOSURE_PORTFOLIO_DIFFERS')
            require(holding['identifier'] is None or (
                type(holding['identifier']) is str
                and 0 < len(holding['identifier']) <= 200
                and not any(ord(value) < 32 for value in holding['identifier'])
            ), 'EXPOSURE_PORTFOLIO_DIFFERS')
            position = {
                'position_kind': 'holding',
                'row': holding['row'],
                'source_label': holding['name'],
                'asset_class': holding['asset_class'],
                'identifier': holding['identifier'],
                'identifier_kind': holding['identifier_kind'],
                'direction': None,
                'weight': _position_weight(lower, upper, exclusive),
                'mapping_state': 'identity-unverified',
                'entity_id': None,
            }
            if holding['asset_class'] in ('cash', 'current_assets_liabilities'):
                position['mapping_state'] = 'not-applicable'
                reasons['NON_ISSUER_CASH_OR_NET_BALANCE'] += 1
                positions.append({**position, 'evidence_hash': fingerprint({
                    'portfolio_snapshot_id': snapshot['snapshot_id'], 'source_position': holding
                })})
                continue
            require(lower >= 0, 'EXPOSURE_SIGNED_SECURITY_UNSUPPORTED')
            if (holding['identifier_kind'] != 'isin' or type(holding['identifier']) is not str
                    or not valid_isin(holding['identifier'])):
                reasons['SECURITY_IDENTITY_UNVERIFIED'] += 1
                positions.append({**position, 'evidence_hash': fingerprint({
                    'portfolio_snapshot_id': snapshot['snapshot_id'], 'source_position': holding
                })})
                continue
            securities.append({'security_id':holding['identifier'],'row':holding['row'],
                'asset_class':holding['asset_class'],
                'weight':{'lower':_text(lower),'upper':_text(upper),'upper_exclusive':exclusive,
                          'unit':'percent_of_original_disclosed_AUM'}})
            candidates = [r for r in by_security.get(holding['identifier'], []) if _active(r, as_of.isoformat())]
            if len(candidates) != 1 or candidates[0]['state'] != 'verified':
                position['mapping_state'] = 'ambiguous' if candidates else 'unmapped'
                reasons['SECURITY_ENTITY_AMBIGUOUS' if candidates else 'SECURITY_ENTITY_NOT_MAPPED_AT_DATE'] += 1
                positions.append({**position, 'evidence_hash': fingerprint({
                    'portfolio_snapshot_id': snapshot['snapshot_id'], 'source_position': holding
                })})
                continue
            record = candidates[0]
            entity = record['entity_id']
            if len(kinds[entity]) != 1:
                position['mapping_state'] = 'entity-kind-conflict'
                reasons['ENTITY_KIND_CONFLICT'] += 1
                positions.append({**position, 'evidence_hash': fingerprint({
                    'portfolio_snapshot_id': snapshot['snapshot_id'], 'source_position': holding
                })})
                continue
            position['mapping_state'] = 'mapped'
            position['entity_id'] = entity
            positions.append({**position, 'evidence_hash': fingerprint({
                'portfolio_snapshot_id': snapshot['snapshot_id'], 'source_position': holding
            })})
            mapped_rows += 1
            group = entities.setdefault(entity, {'lower':Decimal(0), 'upper':Decimal(0),
                'exclusive':False, 'rows':[], 'security_ids':set(), 'evidence_hashes':set(),
                'entity_kind':record['entity_kind']})
            group['lower'] += lower
            group['upper'] += upper
            group['exclusive'] |= exclusive
            group['rows'].append(holding['row'])
            group['security_ids'].add(holding['identifier'])
            group['evidence_hashes'].add(record['evidence_hash'])
            if record['entity_kind'] in ('fund','other'):
                reasons['FUND_LOOKTHROUGH_UNAVAILABLE' if record['entity_kind']=='fund' else 'ENTITY_CLASSIFICATION_UNAVAILABLE'] += 1
                continue
            active_attributes = entity_attribute_records.get(entity,[])
            # Conflicting single-valued attributes are not resolved by order.
            for dimension in DIMENSIONS:
                values = {a['value'] for a in active_attributes if a['dimension'] == dimension}
                if dimension != 'theme' and len(entity_attributes.get((entity,dimension),set())) > 1:
                    reasons['ENTITY_ATTRIBUTE_CONFLICT'] += 1
                    continue
                for value in sorted(values):
                    key = (dimension, value)
                    bucket = attributes.setdefault(key, {'lower':Decimal(0), 'upper':Decimal(0),
                        'exclusive':False, 'entities':set(), 'evidence_hashes':set()})
                    bucket['lower'] += lower
                    bucket['upper'] += upper
                    bucket['exclusive'] |= exclusive
                    bucket['entities'].add(entity)
                    bucket['evidence_hashes'].update(a['evidence_hash'] for a in active_attributes
                        if a['dimension'] == dimension and a['value'] == value)
        def bounds(group):
            return {'lower':_text(group['lower']), 'upper':_text(group['upper']),
                    'upper_exclusive':group['exclusive'], 'unit':'percent_of_original_disclosed_AUM'}
        company = [{'entity_id':entity, 'entity_kind':group['entity_kind'], 'weight':bounds(group), 'rows':sorted(group['rows']),
            'security_ids':sorted(group['security_ids']), 'evidence_hashes':sorted(group['evidence_hashes'])}
            for entity,group in sorted(entities.items())]
        facets = [{'dimension':key[0], 'value':key[1], 'weight':bounds(group),
            'entity_ids':sorted(group['entities']), 'evidence_hashes':sorted(group['evidence_hashes'])}
            for key,group in sorted(attributes.items())]
        for derivative in snapshot['derivative_positions']:
            require(type(derivative['underlying_name']) is str
                    and 0 < len(derivative['underlying_name']) <= 500,
                    'EXPOSURE_PORTFOLIO_DIFFERS')
            reported = _number(derivative['reported_exposure_percent'])
            positions.append({
                'position_kind': 'derivative',
                'row': derivative['row'],
                'source_label': derivative['underlying_name'],
                'asset_class': 'derivative',
                'identifier': None,
                'identifier_kind': 'not_applicable',
                'direction': derivative['direction'],
                'weight': _position_weight(reported, reported, False),
                'mapping_state': 'not-applicable',
                'entity_id': None,
                'evidence_hash': fingerprint({
                    'portfolio_snapshot_id': snapshot['snapshot_id'],
                    'source_position': derivative,
                }),
            })
    result = {'format':VERSION, 'derivation_policy_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'code':snapshot['code'], 'portfolio_snapshot_id':snapshot['snapshot_id'],
        'portfolio_as_of':as_of.isoformat(), 'portfolio_engine_hash':snapshot['engine_code_hash'],
        'source_content_hash':snapshot['content_hash'],
        'identity_content_hash':snapshot['identity_content_hash'], 'registry_hash':fingerprint(registry),
        'observed_on':observed_on, 'evidence_age_days':(when_observed-as_of).days,
        'freshness_certified':False, 'basis':'dated_held_issuer_attributes_not_scheme_mandate',
        'statement_complete':True, 'holdings_count':len(rows), 'mapped_holding_count':mapped_rows,
        'withheld_row_reasons':dict(sorted(reasons.items())),
        'derivative_position_count':len(snapshot['derivative_positions']),
        'lookthrough_performed':False, 'absence_or_exclusion_certified':False,
        'positions':sorted(positions,key=lambda item:(item['row'],item['position_kind'])),
        'securities':sorted(securities,key=lambda item:item['security_id']),
        'entities':company, 'attributes':facets, 'originals_replaced':False}
    return {**result, 'assessment_id':fingerprint(result)}
