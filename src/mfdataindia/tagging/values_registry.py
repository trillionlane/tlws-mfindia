"""Reviewed server-side screen vocabulary; no caller-controlled policy.

The first packaged release deliberately contains definitions but no live entity
activity classifications. Therefore the API can describe the custom screens,
while a fund cannot receive a positive live result until reviewed activity
evidence is added in a later data operation.
"""
from mfdataindia.review.policy import fingerprint
from . import values_policy as policy


def _screen(screen_id, label, kind, *, exclusions=(), inclusions=(), age=120,
            lookthrough=True, derivatives=True, interpretation):
    core = {
        'screen_id': screen_id,
        'label': label,
        'kind': kind,
        'status': 'active',
        'valid_from': '2026-11-11',
        'valid_until': None,
        'max_evidence_age_days': age,
        'require_complete_entity_mapping': True,
        'require_lookthrough': lookthrough,
        'require_derivative_classification': derivatives,
        'exclusions': [{'activity': value, 'max_weight_percent': '0'} for value in exclusions],
        'inclusions': [{'activity': value, 'min_weight_percent': threshold}
                       for value, threshold in inclusions],
        'certification_claim': False,
        'interpretation': interpretation,
    }
    return {**core, 'evidence_hash': fingerprint(core)}


def screens():
    document = {'format': policy.SCREEN_FORMAT, 'screens': [
        _screen(
            'custom-green-v1', 'Custom green evidence screen', 'environmental',
            exclusions=('thermal-coal', 'oil-gas-extraction'),
            inclusions=(('renewable-energy', '50'),),
            interpretation=('A custom evidence screen requiring at least 50 percent observed renewable-energy '
                            'exposure and zero observed thermal-coal or oil-gas-extraction exposure. It is not '
                            'an issuer, regulatory, impact, or sustainability certification.'),
        ),
        _screen(
            'custom-esg-risk-v1', 'Custom ESG risk exclusion screen', 'custom-values',
            exclusions=('tobacco', 'weapons', 'thermal-coal', 'oil-gas-extraction'),
            interpretation=('A custom ESG-risk screen with explicit zero thresholds for the named activities. '
                            'It is not an ESG rating, regulatory classification, endorsement, or claim that '
                            'other environmental, social, or governance factors were assessed.'),
        ),
        _screen(
            'custom-sattvik-v1', 'Custom sattvik evidence screen', 'custom-values',
            exclusions=('alcohol', 'tobacco', 'gambling', 'weapons', 'animal-harm'),
            interpretation=('A custom user-values screen with explicit zero thresholds for the named activities. '
                            'It does not decide conventional-finance treatment and is not religious certification.'),
        ),
        _screen(
            'custom-ethical-v1', 'Custom ethical exclusion screen', 'custom-values',
            exclusions=('alcohol', 'tobacco', 'gambling', 'weapons'),
            interpretation=('A custom exclusion screen over dated reviewed entity activities. It is not an '
                            'endorsement, suitability determination, or universal definition of ethical investing.'),
        ),
    ]}
    policy.validate_screens(document)
    return document


def activities():
    document = {'format': policy.ACTIVITY_FORMAT, 'records': []}
    policy.validate_activities(document)
    return document
