"""Closed read-only custom-values API and truthful unavailable states."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from mfdataindia.datasets.policy import DatasetError
from mfdataindia.tagging import values_app as app, values_reads as reads, values_registry
from mfdataindia.tagging.policy import TaggingError
from test_fund_exposure_policy import derive

ROOT = Path(__file__).resolve().parents[1]


def client():
    class Connection:
        def transaction(self):
            return nullcontext()

        def execute(self, *args):
            return None

    api = app.create_app('offline-not-a-live-connection')
    api.state.db_pool = SimpleNamespace(connection=lambda: nullcontext(Connection()))
    return TestClient(api)


def test_query_surface_is_closed_and_mutations_are_absent_before_database_read(monkeypatch):
    def never(*args, **kwargs):
        pytest.fail('invalid request reached a database read')

    monkeypatch.setattr(reads, 'summary', never)
    api = client()
    for suffix in ('/0', '/1?screen=caller-policy', '/1?profile=owner', '/bad'):
        response = api.get(reads.PREFIX + '/funds' + suffix)
        assert response.status_code == 422
        assert response.json() == {'detail': 'INVALID_VALUES_QUERY'}
    for method in ('post', 'put', 'patch', 'delete'):
        assert getattr(api, method)(reads.PREFIX + '/funds/1').status_code == 405


@pytest.mark.parametrize('exception', [ValueError, TaggingError, DatasetError])
def test_underlying_values_are_never_echoed(monkeypatch, exception):
    def fail(*args):
        raise exception('password=synthetic-secret-marker')

    monkeypatch.setattr(reads, 'summary', fail)
    response = client().get(reads.PREFIX + '/funds/1')
    assert response.status_code == 503
    assert response.json() == {'detail': 'VALUES_READ_UNAVAILABLE'}
    assert 'synthetic-secret-marker' not in response.text


def test_discovery_reports_explicit_custom_rules_and_zero_live_activity_records(monkeypatch):
    seen = []
    monkeypatch.setattr('mfdataindia.tagging.exposure_reads._checked',
                        lambda conn: seen.append('portfolio-checked'))
    result = reads.discovery(None)
    assert seen == ['portfolio-checked']
    assert [value['screen_id'] for value in result['screen_definitions']] == [
        'custom-green-v1', 'custom-esg-risk-v1', 'custom-sattvik-v1', 'custom-ethical-v1'
    ]
    assert result['activity_mapping_records'] == 0
    assert result['positive_live_assignments_claimed'] is False
    assert result['certification_claimed'] is False
    monkeypatch.setattr(reads, 'discovery', lambda conn: result)
    response = client().get(reads.PREFIX)
    assert response.status_code == 200
    assert response.json()['exact_scheme_news_unchanged'] is True


def test_pre_policy_portfolio_is_not_assessed(monkeypatch):
    monkeypatch.setattr('mfdataindia.tagging.exposure_reads._derive',
                        lambda conn, code: ('available', None, derive()))
    response = client().get(reads.PREFIX + '/funds/1')
    assert response.status_code == 200
    body = response.json()
    assert len(body['assessments']) == 4
    assert {value['state'] for value in body['assessments']} == {'not-assessed'}
    assert all(value['reasons'] == ['SCREEN_NOT_ACTIVE_AT_PORTFOLIO_DATE']
               for value in body['assessments'])
    assert body['positive_live_assignments_claimed'] is False


def test_active_policy_with_empty_activity_registry_is_insufficient_not_zero(monkeypatch):
    exposure = derive()
    exposure.update(portfolio_as_of='2026-11-11', observed_on='2026-11-11', evidence_age_days=0)
    from mfdataindia.review.policy import fingerprint
    exposure['assessment_id'] = fingerprint({
        key: value for key, value in exposure.items() if key != 'assessment_id'
    })
    monkeypatch.setattr('mfdataindia.tagging.exposure_reads._derive',
                        lambda conn, code: ('available', None, exposure))
    body = client().get(reads.PREFIX + '/funds/1').json()
    assert {value['state'] for value in body['assessments']} == {'insufficient-evidence'}
    assert all('ENTITY_ACTIVITY_CLASSIFICATION_INCOMPLETE' in value['reasons']
               for value in body['assessments'])


@pytest.mark.parametrize(('state', 'reason'), [
    ('not_captured', 'no_accepted_complete_portfolio'),
    ('identity_changed', 'current_identity_differs'),
    ('conflicting', 'same_date_distinct_retained_sources'),
])
def test_unavailable_exposure_is_not_an_empty_positive_screen(monkeypatch, state, reason):
    monkeypatch.setattr('mfdataindia.tagging.exposure_reads._derive',
                        lambda conn, code: (state, reason, None))
    body = client().get(reads.PREFIX + '/funds/1').json()
    assert body['exposure_state'] == state and body['exposure_reason'] == reason
    assert body['assessments'] == [] and body['portfolio_as_of'] is None


def contract(document):
    paths = {key: value for key, value in document['paths'].items()
             if key.startswith(reads.PREFIX)}
    return {
        'openapi': document['openapi'],
        'paths': paths,
        'components': {'schemas': {
            key: value for key, value in document['components']['schemas'].items()
            if key.startswith(('Values', 'ActivityExposure', 'Screen'))
        }},
    }


def test_contract_preserves_all_tag_and_exposure_routes_and_is_deterministic():
    from mfdataindia.tagging.exposure_app import create_app as before

    old = before('offline').openapi()
    new = app.create_app('offline').openapi()
    assert all(new['paths'][path] == value for path, value in old['paths'].items())
    assert contract(new) == json.loads(
        (ROOT / 'contracts/mfdataindia-fund-values-v1.json').read_text()
    )
    assert values_registry.screens() == values_registry.screens()
    assert values_registry.activities() == values_registry.activities()
