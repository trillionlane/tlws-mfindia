"""Read-only values assessments composed from the existing exposure boundary."""
from mfdataindia.review.policy import fingerprint

from . import exposure_reads, reads as tags, values_policy as policy, values_registry

PREFIX = '/api/fund-values/v1'


def _definition(screen):
    criteria = [
        {'activity': item['activity'], 'threshold_percent': item['max_weight_percent'],
         'comparison': 'maximum'} for item in screen['exclusions']
    ] + [
        {'activity': item['activity'], 'threshold_percent': item['min_weight_percent'],
         'comparison': 'minimum'} for item in screen['inclusions']
    ]
    return {
        'screen_id': screen['screen_id'], 'label': screen['label'], 'kind': screen['kind'],
        'status': screen['status'], 'valid_from': screen['valid_from'],
        'valid_until': screen['valid_until'],
        'max_evidence_age_days': screen['max_evidence_age_days'],
        'require_complete_entity_mapping': screen['require_complete_entity_mapping'],
        'require_lookthrough': screen['require_lookthrough'],
        'require_derivative_classification': screen['require_derivative_classification'],
        'criteria': criteria, 'certification_claim': False,
        'interpretation': screen['interpretation'], 'evidence_hash': screen['evidence_hash'],
    }


def discovery(conn):
    # Retain the complete portfolio policy/ACL/OID verification even though no
    # fund is read by this operation; consumers must not see a detached policy.
    exposure_reads._checked(conn)
    screens = values_registry.screens()
    activities = values_registry.activities()
    return tags.bounded({
        'format': policy.VERSION,
        'screen_registry_format': policy.SCREEN_FORMAT,
        'activity_registry_format': policy.ACTIVITY_FORMAT,
        'screen_registry_hash': fingerprint(screens),
        'activity_registry_hash': fingerprint(activities),
        'screen_definitions': [_definition(screen) for screen in screens['screens']],
        'activity_mapping_records': len(activities['records']),
        'funds_url': PREFIX + '/funds/{code}',
        'positive_live_assignments_claimed': False,
        'certification_claimed': False,
        'exact_scheme_news_unchanged': True,
    })


def summary(conn, code):
    state, reason, exposure = exposure_reads._derive(conn, code)
    assessments = [] if exposure is None else policy.assess(
        exposure, values_registry.screens(), values_registry.activities()
    )
    return tags.bounded({
        'format': policy.VERSION, 'code': code, 'exposure_state': state,
        'exposure_reason': reason,
        'portfolio_as_of': None if exposure is None else exposure['portfolio_as_of'],
        'assessments': assessments,
        'positive_live_assignments_claimed': False,
        'certification_claimed': False,
    })
