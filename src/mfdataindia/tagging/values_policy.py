"""Versioned sustainability and custom-values assessments over dated evidence.

This module never infers a certification, suitability judgement, or exclusion
from a fund name. Positive results require a complete reviewed entity-activity
classification for every relevant held entity, bounded portfolio freshness,
and the screen's explicit look-through and derivative requirements.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, localcontext, Context, ROUND_HALF_EVEN
import re

from mfdataindia.review.policy import fingerprint
from . import exposure_policy as exposures
from .policy import TaggingError, canonical, require

VERSION = 'mfdata-sustainability-values-v1'
SCREEN_FORMAT = 'mfdata-values-screen-registry-v1'
ACTIVITY_FORMAT = 'mfdata-entity-activities-v1'
HASH = re.compile(r'[0-9a-f]{64}')
SCREEN = re.compile(r'[a-z][a-z0-9-]{0,63}')
ACTIVITY = re.compile(r'[a-z][a-z0-9-]{0,63}')
MAX_SCREENS = 50
MAX_RECORDS = 10000
STATES = ('supported', 'contradicted', 'insufficient-evidence', 'not-assessed')


def _date(value, reason='VALUES_EVIDENCE_DIFFERS'):
    require(type(value) is str, reason)
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise TaggingError(reason) from None
    require(result.isoformat() == value, reason)
    return result


def _interval(record, reason):
    start = _date(record['valid_from'], reason)
    end = _date(record['valid_until'], reason) if record['valid_until'] is not None else None
    require(end is None or end >= start, reason)
    require(type(record['evidence_hash']) is str and HASH.fullmatch(record['evidence_hash']), reason)


def _active(record, when):
    return record['valid_from'] <= when and (record['valid_until'] is None or when <= record['valid_until'])


def _number(value, reason):
    require(type(value) is str and 0 < len(value) <= 160, reason)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise TaggingError(reason) from None
    require(number.is_finite() and 0 <= number <= 200, reason)
    return number


def _signed_number(value, reason):
    require(type(value) is str and 0 < len(value) <= 160, reason)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise TaggingError(reason) from None
    require(number.is_finite() and -200 <= number <= 200, reason)
    return number


def _bounded(document, maximum, reason):
    try:
        value = canonical(document)
    except (TypeError, ValueError):
        raise TaggingError(reason) from None
    require(len(value) <= maximum, reason)


def validate_screens(document):
    _bounded(document, 2 * 1024 * 1024, 'VALUES_SCREEN_REGISTRY_DIFFERS')
    require(type(document) is dict and set(document) == {'format', 'screens'}
            and document['format'] == SCREEN_FORMAT and type(document['screens']) is list
            and len(document['screens']) <= MAX_SCREENS, 'VALUES_SCREEN_REGISTRY_DIFFERS')
    seen = set()
    for screen in document['screens']:
        require(type(screen) is dict and set(screen) == {
            'screen_id', 'label', 'kind', 'status', 'valid_from', 'valid_until',
            'evidence_hash', 'max_evidence_age_days', 'require_complete_entity_mapping',
            'require_lookthrough', 'require_derivative_classification', 'exclusions',
            'inclusions', 'certification_claim', 'interpretation'
        }, 'VALUES_SCREEN_REGISTRY_DIFFERS')
        require(type(screen['screen_id']) is str and SCREEN.fullmatch(screen['screen_id'])
                and screen['screen_id'] not in seen and type(screen['label']) is str
                and 0 < len(screen['label']) <= 120 and screen['kind'] in {
                    'declared-esg', 'environmental', 'custom-values'
                } and screen['status'] in {'active', 'draft'}
                and type(screen['max_evidence_age_days']) is int
                and 0 <= screen['max_evidence_age_days'] <= 3660
                and screen['require_complete_entity_mapping'] is True
                and type(screen['require_lookthrough']) is bool
                and type(screen['require_derivative_classification']) is bool
                and screen['certification_claim'] is False
                and type(screen['interpretation']) is str
                and 0 < len(screen['interpretation']) <= 500,
                'VALUES_SCREEN_REGISTRY_DIFFERS')
        seen.add(screen['screen_id'])
        _interval(screen, 'VALUES_SCREEN_REGISTRY_DIFFERS')
        require(screen['evidence_hash'] == fingerprint({
            key: value for key, value in screen.items() if key != 'evidence_hash'
        }), 'VALUES_SCREEN_REGISTRY_DIFFERS')
        for key in ('exclusions', 'inclusions'):
            require(type(screen[key]) is list and len(screen[key]) <= 50,
                    'VALUES_SCREEN_REGISTRY_DIFFERS')
        activities = set()
        for criterion in screen['exclusions']:
            require(type(criterion) is dict and set(criterion) == {'activity', 'max_weight_percent'}
                    and type(criterion['activity']) is str and ACTIVITY.fullmatch(criterion['activity'])
                    and criterion['activity'] not in activities,
                    'VALUES_SCREEN_REGISTRY_DIFFERS')
            activities.add(criterion['activity'])
            _number(criterion['max_weight_percent'], 'VALUES_SCREEN_REGISTRY_DIFFERS')
        for criterion in screen['inclusions']:
            require(type(criterion) is dict and set(criterion) == {'activity', 'min_weight_percent'}
                    and type(criterion['activity']) is str and ACTIVITY.fullmatch(criterion['activity'])
                    and criterion['activity'] not in activities,
                    'VALUES_SCREEN_REGISTRY_DIFFERS')
            activities.add(criterion['activity'])
            _number(criterion['min_weight_percent'], 'VALUES_SCREEN_REGISTRY_DIFFERS')
        require(bool(screen['exclusions'] or screen['inclusions']), 'VALUES_SCREEN_REGISTRY_DIFFERS')
    return document


def validate_activities(document):
    _bounded(document, 8 * 1024 * 1024, 'VALUES_ACTIVITY_REGISTRY_DIFFERS')
    require(type(document) is dict and set(document) == {'format', 'records'}
            and document['format'] == ACTIVITY_FORMAT and type(document['records']) is list
            and len(document['records']) <= MAX_RECORDS, 'VALUES_ACTIVITY_REGISTRY_DIFFERS')
    by_entity = {}
    for record in document['records']:
        require(type(record) is dict and set(record) == {
            'entity_id', 'state', 'classification_complete', 'activities',
            'valid_from', 'valid_until', 'evidence_hash'
        } and type(record['entity_id']) is str and exposures.ENTITY.fullmatch(record['entity_id'])
            and record['state'] in {'verified', 'ambiguous'}
            and type(record['classification_complete']) is bool
            and type(record['activities']) is list and len(record['activities']) <= 100
            and all(type(value) is str and ACTIVITY.fullmatch(value) for value in record['activities'])
            and len(record['activities']) == len(set(record['activities'])),
            'VALUES_ACTIVITY_REGISTRY_DIFFERS')
        _interval(record, 'VALUES_ACTIVITY_REGISTRY_DIFFERS')
        by_entity.setdefault(record['entity_id'], []).append(record)
    return by_entity


def _validate_exposure(document):
    _bounded(document, 16 * 1024 * 1024, 'VALUES_EXPOSURE_DIFFERS')
    required = {
        'format', 'derivation_policy_hash', 'code', 'portfolio_snapshot_id', 'portfolio_as_of',
        'portfolio_engine_hash', 'source_content_hash', 'identity_content_hash', 'registry_hash',
        'observed_on', 'evidence_age_days', 'freshness_certified', 'basis', 'statement_complete',
        'holdings_count', 'mapped_holding_count', 'withheld_row_reasons',
        'derivative_position_count', 'lookthrough_performed', 'absence_or_exclusion_certified',
        'positions', 'securities', 'entities', 'attributes', 'originals_replaced', 'assessment_id'
    }
    require(type(document) is dict and set(document) == required
            and document['format'] == exposures.VERSION
            and type(document['code']) is int and 0 < document['code'] < 2**31
            and type(document['entities']) is list and len(document['entities']) <= 1000
            and type(document['positions']) is list and len(document['positions']) <= 2000
            and type(document['withheld_row_reasons']) is dict
            and document['statement_complete'] is True
            and document['freshness_certified'] is False
            and document['lookthrough_performed'] is False
            and document['absence_or_exclusion_certified'] is False
            and document['originals_replaced'] is False
            and type(document['evidence_age_days']) is int and document['evidence_age_days'] >= 0
            and type(document['derivative_position_count']) is int
            and document['derivative_position_count'] >= 0
            and document['assessment_id'] == fingerprint({
                key: value for key, value in document.items() if key != 'assessment_id'
            }), 'VALUES_EXPOSURE_DIFFERS')
    _date(document['portfolio_as_of'], 'VALUES_EXPOSURE_DIFFERS')
    _date(document['observed_on'], 'VALUES_EXPOSURE_DIFFERS')
    for key in ('derivation_policy_hash', 'portfolio_snapshot_id', 'portfolio_engine_hash',
                'source_content_hash', 'identity_content_hash', 'registry_hash', 'assessment_id'):
        require(type(document[key]) is str and HASH.fullmatch(document[key]), 'VALUES_EXPOSURE_DIFFERS')
    require(len(document['positions']) == document['holdings_count']
            + document['derivative_position_count'], 'VALUES_EXPOSURE_DIFFERS')
    for position in document['positions']:
        require(type(position) is dict and set(position) == {
            'position_kind', 'row', 'source_label', 'asset_class', 'identifier',
            'identifier_kind', 'direction', 'weight', 'mapping_state', 'entity_id',
            'evidence_hash'
        } and position['position_kind'] in {'holding', 'derivative'}
            and type(position['row']) is int and position['row'] > 0
            and type(position['source_label']) is str
            and 0 < len(position['source_label']) <= 500
            and position['asset_class'] in {
                'equity', 'debt', 'government', 'cash', 'current_assets_liabilities', 'derivative'
            }
            and (position['identifier'] is None or (
                type(position['identifier']) is str and 0 < len(position['identifier']) <= 200
            ))
            and position['identifier_kind'] in {'isin', 'issuer_local', 'not_applicable'}
            and position['direction'] in {None, 'Long', 'Short'}
            and position['mapping_state'] in {
                'mapped', 'unmapped', 'ambiguous', 'identity-unverified',
                'entity-kind-conflict', 'not-applicable'
            }
            and (position['entity_id'] is None or (
                type(position['entity_id']) is str
                and exposures.ENTITY.fullmatch(position['entity_id'])
            ))
            and type(position['evidence_hash']) is str
            and HASH.fullmatch(position['evidence_hash'])
            and type(position['weight']) is dict
            and set(position['weight']) == {'lower', 'upper', 'upper_exclusive', 'unit'}
            and position['weight']['unit'] == 'percent_of_original_disclosed_AUM'
            and type(position['weight']['upper_exclusive']) is bool,
            'VALUES_EXPOSURE_DIFFERS')
        lower = _signed_number(position['weight']['lower'], 'VALUES_EXPOSURE_DIFFERS')
        upper = _signed_number(position['weight']['upper'], 'VALUES_EXPOSURE_DIFFERS')
        require(lower <= upper, 'VALUES_EXPOSURE_DIFFERS')
    for entity in document['entities']:
        require(type(entity) is dict and set(entity) == {
            'entity_id', 'entity_kind', 'weight', 'rows', 'security_ids', 'evidence_hashes'
        } and type(entity['entity_id']) is str and exposures.ENTITY.fullmatch(entity['entity_id'])
            and entity['entity_kind'] in {'company', 'sovereign', 'fund', 'other'}
            and type(entity['weight']) is dict and set(entity['weight']) == {
                'lower', 'upper', 'upper_exclusive', 'unit'
            } and entity['weight']['unit'] == 'percent_of_original_disclosed_AUM'
            and type(entity['weight']['upper_exclusive']) is bool,
            'VALUES_EXPOSURE_DIFFERS')
        require(type(entity['evidence_hashes']) is list
                and len(entity['evidence_hashes']) <= 1000
                and all(type(value) is str and HASH.fullmatch(value)
                        for value in entity['evidence_hashes']),
                'VALUES_EXPOSURE_DIFFERS')
        lower = _number(entity['weight']['lower'], 'VALUES_EXPOSURE_DIFFERS')
        upper = _number(entity['weight']['upper'], 'VALUES_EXPOSURE_DIFFERS')
        require(lower <= upper, 'VALUES_EXPOSURE_DIFFERS')
    return document


def _weight_text(value):
    return format(value, 'f')


def assess(exposure, screens, activities):
    """Evaluate explicit rules without treating missing evidence as absence."""
    exposure = _validate_exposure(exposure)
    validate_screens(screens)
    by_entity = validate_activities(activities)
    when = exposure['portfolio_as_of']
    results = []
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        active_records = {}
        for entity in exposure['entities']:
            candidates = [record for record in by_entity.get(entity['entity_id'], [])
                          if _active(record, when)]
            active_records[entity['entity_id']] = candidates
        for screen in sorted(screens['screens'], key=lambda value: value['screen_id']):
            reasons = []
            state = 'supported'
            activity_weights = {}
            if screen['status'] != 'active' or not _active(screen, when):
                state, reasons = 'not-assessed', ['SCREEN_NOT_ACTIVE_AT_PORTFOLIO_DATE']
            elif exposure['evidence_age_days'] > screen['max_evidence_age_days']:
                state, reasons = 'insufficient-evidence', ['PORTFOLIO_EVIDENCE_STALE']
            else:
                unknown_reasons = {
                    key: value for key, value in exposure['withheld_row_reasons'].items()
                    if value and key not in {'NON_ISSUER_CASH_OR_NET_BALANCE'}
                }
                if unknown_reasons:
                    reasons.append('PORTFOLIO_ENTITY_COVERAGE_INCOMPLETE')
                if screen['require_lookthrough'] and exposure['withheld_row_reasons'].get(
                        'FUND_LOOKTHROUGH_UNAVAILABLE', 0):
                    reasons.append('FUND_LOOKTHROUGH_INCOMPLETE')
                if screen['require_derivative_classification'] and exposure['derivative_position_count']:
                    reasons.append('DERIVATIVE_CLASSIFICATION_INCOMPLETE')
                for entity in exposure['entities']:
                    if entity['entity_kind'] not in {'company', 'sovereign'}:
                        continue
                    candidates = active_records[entity['entity_id']]
                    if (len(candidates) != 1 or candidates[0]['state'] != 'verified'
                            or not candidates[0]['classification_complete']):
                        reasons.append('ENTITY_ACTIVITY_CLASSIFICATION_INCOMPLETE')
                        continue
                    lower = _number(entity['weight']['lower'], 'VALUES_EXPOSURE_DIFFERS')
                    upper = _number(entity['weight']['upper'], 'VALUES_EXPOSURE_DIFFERS')
                    for activity in candidates[0]['activities']:
                        bucket = activity_weights.setdefault(activity, {
                            'lower': Decimal(0), 'upper': Decimal(0), 'upper_exclusive': False,
                            'entity_ids': set(), 'evidence_hashes': set()
                        })
                        bucket['lower'] += lower
                        bucket['upper'] += upper
                        bucket['upper_exclusive'] |= entity['weight']['upper_exclusive']
                        bucket['entity_ids'].add(entity['entity_id'])
                        bucket['evidence_hashes'].add(candidates[0]['evidence_hash'])
                reasons = sorted(set(reasons))
                coverage_incomplete = bool(reasons)
                uncertain = False
                contradicted = False
                for criterion in screen['exclusions']:
                    bucket = activity_weights.get(criterion['activity'])
                    lower = Decimal(0) if bucket is None else bucket['lower']
                    upper = Decimal(0) if bucket is None else bucket['upper']
                    maximum = _number(criterion['max_weight_percent'],
                                      'VALUES_SCREEN_REGISTRY_DIFFERS')
                    # A proven lower bound above the maximum is conclusive even
                    # when unrelated positions remain unclassified.
                    if lower > maximum:
                        contradicted = True
                        reasons.append('EXCLUSION_THRESHOLD_EXCEEDED')
                    elif upper > maximum:
                        uncertain = True
                        reasons.append('EXCLUSION_THRESHOLD_UNCERTAIN')
                for criterion in screen['inclusions']:
                    bucket = activity_weights.get(criterion['activity'])
                    lower = Decimal(0) if bucket is None else bucket['lower']
                    upper = Decimal(0) if bucket is None else bucket['upper']
                    upper_exclusive = False if bucket is None else bucket['upper_exclusive']
                    minimum = _number(criterion['min_weight_percent'],
                                      'VALUES_SCREEN_REGISTRY_DIFFERS')
                    proven_miss = upper < minimum or (upper == minimum and upper_exclusive)
                    if proven_miss and not coverage_incomplete:
                        contradicted = True
                        reasons.append('INCLUSION_THRESHOLD_NOT_MET')
                    elif proven_miss or lower < minimum:
                        uncertain = True
                        reasons.append('INCLUSION_THRESHOLD_UNCERTAIN')
                if contradicted:
                    state = 'contradicted'
                elif coverage_incomplete or uncertain:
                    state = 'insufficient-evidence'
                else:
                    reasons.append('ALL_EXPLICIT_CRITERIA_MET')
            serialized = [{
                'activity': activity,
                'weight': {
                    'lower': _weight_text(bucket['lower']),
                    'upper': _weight_text(bucket['upper']),
                    'upper_exclusive': bucket['upper_exclusive'],
                    'unit': 'percent_of_original_disclosed_AUM',
                },
                'entity_ids': sorted(bucket['entity_ids']),
                'evidence_hashes': sorted(bucket['evidence_hashes']),
            } for activity, bucket in sorted(activity_weights.items())]
            result = {
                'format': VERSION,
                'screen_id': screen['screen_id'],
                'screen_label': screen['label'],
                'screen_kind': screen['kind'],
                'state': state,
                'reasons': sorted(set(reasons)),
                'code': exposure['code'],
                'portfolio_snapshot_id': exposure['portfolio_snapshot_id'],
                'portfolio_as_of': exposure['portfolio_as_of'],
                'exposure_assessment_id': exposure['assessment_id'],
                'screen_registry_hash': fingerprint(screens),
                'activity_registry_hash': fingerprint(activities),
                'screen_evidence_hash': screen['evidence_hash'],
                'activity_exposures': serialized,
                'certification_claim': False,
                'custom_screen_not_religious_or_regulatory_certification': True,
                'missing_evidence_never_means_zero': True,
            }
            results.append({**result, 'assessment_id': fingerprint(result)})
    return results
