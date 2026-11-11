"""Deterministic values-screen decisions over explicit complete evidence."""
from copy import deepcopy
import sys
from pathlib import Path

import pytest

from mfdataindia.review.policy import fingerprint
from mfdataindia.tagging import values_policy as policy, values_registry
from mfdataindia.tagging.policy import TaggingError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fund_exposure_policy import derive  # noqa: E402


def screen(*, exclusions=(), inclusions=(), age=120, status='active'):
    core = {
        'screen_id': 'custom-test-v1', 'label': 'Custom test screen',
        'kind': 'custom-values', 'status': status, 'valid_from': '2020-01-01',
        'valid_until': None, 'max_evidence_age_days': age,
        'require_complete_entity_mapping': True, 'require_lookthrough': True,
        'require_derivative_classification': True,
        'exclusions': [{'activity': value, 'max_weight_percent': maximum}
                       for value, maximum in exclusions],
        'inclusions': [{'activity': value, 'min_weight_percent': minimum}
                       for value, minimum in inclusions],
        'certification_claim': False,
        'interpretation': 'Synthetic custom rule for deterministic provider-free tests.',
    }
    return {**core, 'evidence_hash': fingerprint(core)}


def screens(**kwargs):
    return {'format': policy.SCREEN_FORMAT, 'screens': [screen(**kwargs)]}


def activities(one=(), two=(), *, omit_two=False, complete=True):
    rows = []
    for entity, values, marker in (
        ('synthetic:issuer-one', one, '1'), ('synthetic:issuer-two', two, '2')
    ):
        if omit_two and entity.endswith('two'):
            continue
        rows.append({
            'entity_id': entity, 'state': 'verified',
            'classification_complete': complete, 'activities': list(values),
            'valid_from': '2020-01-01', 'valid_until': None,
            'evidence_hash': marker * 64,
        })
    return {'format': policy.ACTIVITY_FORMAT, 'records': rows}


def refresh(exposure):
    exposure['assessment_id'] = fingerprint({
        key: value for key, value in exposure.items() if key != 'assessment_id'
    })
    return exposure


def result(exposure=None, screen_document=None, activity_document=None):
    return policy.assess(
        derive() if exposure is None else exposure,
        screens() if screen_document is None else screen_document,
        activities() if activity_document is None else activity_document,
    )[0]


def test_complete_zero_exposure_custom_screen_is_supported_not_certification():
    value = result(screen_document=screens(exclusions=(('alcohol', '0'),)))
    assert value['state'] == 'supported' and value['reasons'] == ['ALL_EXPLICIT_CRITERIA_MET']
    assert value['certification_claim'] is False
    assert value['custom_screen_not_religious_or_regulatory_certification'] is True
    assert value['missing_evidence_never_means_zero'] is True


def test_each_packaged_screen_has_deterministic_positive_negative_and_unknown_cases():
    exposure = derive()
    exposure.update(portfolio_as_of='2026-11-11', observed_on='2026-11-11', evidence_age_days=0)
    refresh(exposure)
    for packaged in values_registry.screens()['screens']:
        rule = {'format': policy.SCREEN_FORMAT, 'screens': [packaged]}
        positive_one = ('renewable-energy',) if packaged['screen_id'] == 'custom-green-v1' else ()
        positive = policy.assess(exposure, rule, activities(one=positive_one))[0]
        assert positive['state'] == 'supported', packaged['screen_id']

        first_exclusion = packaged['exclusions'][0]['activity']
        negative_one = tuple(sorted(set(positive_one) | {first_exclusion}))
        negative = policy.assess(exposure, rule, activities(one=negative_one))[0]
        assert negative['state'] == 'contradicted', packaged['screen_id']

        unknown = policy.assess(
            exposure, rule, activities(one=positive_one, omit_two=True)
        )[0]
        assert unknown['state'] == 'insufficient-evidence', packaged['screen_id']


def test_observed_excluded_activity_is_contradicted_with_dated_weight():
    value = result(screen_document=screens(exclusions=(('alcohol', '0'),)),
                   activity_document=activities(one=('alcohol',)))
    assert value['state'] == 'contradicted'
    assert value['reasons'] == ['EXCLUSION_THRESHOLD_EXCEEDED']
    observed = next(item for item in value['activity_exposures'] if item['activity'] == 'alcohol')
    assert observed['weight']['lower'] == observed['weight']['upper'] == '50.0'


def test_missing_or_incomplete_entity_classification_never_becomes_zero():
    for registry in (activities(omit_two=True), activities(complete=False)):
        value = result(screen_document=screens(exclusions=(('tobacco', '0'),)),
                       activity_document=registry)
        assert value['state'] == 'insufficient-evidence'
        assert 'ENTITY_ACTIVITY_CLASSIFICATION_INCOMPLETE' in value['reasons']


def test_censored_excluded_weight_is_uncertain_not_supported_or_contradicted():
    exposure = derive()
    exposure['entities'][0]['weight'].update(lower='0', upper='0.01', upper_exclusive=True)
    refresh(exposure)
    value = result(exposure, screens(exclusions=(('tobacco', '0'),)),
                   activities(one=('tobacco',)))
    assert value['state'] == 'insufficient-evidence'
    assert value['reasons'] == ['EXCLUSION_THRESHOLD_UNCERTAIN']


@pytest.mark.parametrize(('change', 'reason'), [
    ({'evidence_age_days': 121}, 'PORTFOLIO_EVIDENCE_STALE'),
    ({'derivative_position_count': 1}, 'DERIVATIVE_CLASSIFICATION_INCOMPLETE'),
])
def test_stale_or_unclassified_derivative_evidence_withholds_positive(change, reason):
    exposure = derive()
    exposure.update(change)
    if change.get('derivative_position_count'):
        exposure['positions'].append({
            'position_kind': 'derivative', 'row': 20,
            'source_label': 'Synthetic Index', 'asset_class': 'derivative',
            'identifier': None, 'identifier_kind': 'not_applicable',
            'direction': 'Long',
            'weight': {'lower': '5', 'upper': '5', 'upper_exclusive': False,
                       'unit': 'percent_of_original_disclosed_AUM'},
            'mapping_state': 'not-applicable', 'entity_id': None,
            'evidence_hash': 'd' * 64,
        })
    refresh(exposure)
    value = result(exposure, screens(exclusions=(('tobacco', '0'),)), activities())
    assert value['state'] == 'insufficient-evidence' and reason in value['reasons']


def test_fund_lookthrough_and_unknown_security_coverage_withhold_positive():
    exposure = derive()
    exposure['withheld_row_reasons']['FUND_LOOKTHROUGH_UNAVAILABLE'] = 1
    refresh(exposure)
    value = result(exposure, screens(exclusions=(('tobacco', '0'),)), activities())
    assert value['state'] == 'insufficient-evidence'
    assert {'FUND_LOOKTHROUGH_INCOMPLETE', 'PORTFOLIO_ENTITY_COVERAGE_INCOMPLETE'} <= set(value['reasons'])


def test_inclusion_threshold_supported_and_proven_miss_contradicted():
    registry = activities(one=('renewable-energy',))
    supported = result(screen_document=screens(inclusions=(('renewable-energy', '50'),)),
                       activity_document=registry)
    missed = result(screen_document=screens(inclusions=(('renewable-energy', '60'),)),
                    activity_document=registry)
    assert supported['state'] == 'supported'
    assert missed['state'] == 'contradicted' and missed['reasons'] == ['INCLUSION_THRESHOLD_NOT_MET']


def test_replay_is_deterministic_and_evidence_changes_identity():
    rule = screens(exclusions=(('alcohol', '0'),))
    registry = activities()
    first = policy.assess(derive(), rule, registry)
    assert first == policy.assess(derive(), deepcopy(rule), deepcopy(registry))
    changed = activities(one=('alcohol',))
    assert policy.assess(derive(), rule, changed)[0]['assessment_id'] != first[0]['assessment_id']


def test_corrupted_retained_position_evidence_fails_closed():
    exposure = derive()
    exposure['positions'][0]['row'] = True
    refresh(exposure)
    with pytest.raises(TaggingError, match='^VALUES_EXPOSURE_DIFFERS$'):
        result(exposure, screens(exclusions=(('alcohol', '0'),)), activities())


@pytest.mark.parametrize('defect', ['screen_hash', 'duplicate_activity', 'ambiguous', 'bad_date'])
def test_invalid_or_ambiguous_policy_evidence_fails_closed(defect):
    rule = screens(exclusions=(('alcohol', '0'),))
    registry = activities()
    if defect == 'screen_hash':
        rule['screens'][0]['evidence_hash'] = '0' * 64
    elif defect == 'duplicate_activity':
        registry['records'][0]['activities'] = ['alcohol', 'alcohol']
    elif defect == 'ambiguous':
        registry['records'][0]['state'] = 'ambiguous'
        value = policy.assess(derive(), rule, registry)[0]
        assert value['state'] == 'insufficient-evidence'
        return
    else:
        registry['records'][0]['valid_from'] = 'not-a-date'
    with pytest.raises(TaggingError):
        policy.assess(derive(), rule, registry)

