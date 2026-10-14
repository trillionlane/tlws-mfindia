"""Prerequisite inventory is hermetic and cannot authorize live operations."""
import copy

import pytest

from mfdataindia.review.preflight import (
    CALLER_SA, CONNECTION, DEPLOY_SA, PROJECT, SIGNER_SECRET, SOURCE_SECRET,
    TRUST_SECRET, inventory,
)


class Metadata:
    def __init__(self):
        self.calls = []
        self.secrets = [{'name': 'projects/' + PROJECT + '/secrets/' + n}
                        for n in (SOURCE_SECRET, TRUST_SECRET, SIGNER_SECRET)]
        self.accounts = [{'email': CALLER_SA, 'disabled': False}]
        self.policy = {'bindings': [{'role': 'roles/iam.serviceAccountUser',
                                    'members': ['serviceAccount:' + DEPLOY_SA]}]}
        self.source = {'connectionName': CONNECTION, 'databaseVersion': 'POSTGRES_18', 'state': 'RUNNABLE'}
        self.services = []
        self.version_state = 'ENABLED'
        self.failure = None

    def command(self, *args, **kwargs):
        self.calls.append(args)
        # This fake rejects every command except the declared metadata reads.
        prefix = args[:3]
        if self.failure == prefix:
            raise RuntimeError('private-fixture-secret-must-not-appear')
        if args[:2] == ('secrets', 'list'):
            return copy.deepcopy(self.secrets)
        if prefix == ('secrets', 'versions', 'describe'):
            name = next(a.removeprefix('--secret=') for a in args if a.startswith('--secret='))
            return {'name': 'projects/' + PROJECT + '/secrets/' + name + '/versions/' + args[3],
                    'state': self.version_state}
        if prefix == ('iam', 'service-accounts', 'list'):
            return copy.deepcopy(self.accounts)
        if prefix == ('iam', 'service-accounts', 'get-iam-policy'):
            return copy.deepcopy(self.policy)
        if prefix == ('sql', 'instances', 'describe'):
            return copy.deepcopy(self.source)
        if prefix == ('run', 'services', 'list'):
            return copy.deepcopy(self.services)
        raise AssertionError('forbidden command')


def report(cloud):
    return inventory(cloud, git_sha='a' * 40,
                     versions={SOURCE_SECRET: 1, TRUST_SECRET: 2, SIGNER_SECRET: 3})


def test_observed_metadata_is_not_authorization_or_certification():
    cloud = Metadata()
    result = report(cloud)
    assert result['status'] == 'PREREQUISITES_OBSERVED_NOT_CERTIFIED'
    assert result['execution_authorized'] is False
    assert result['cloud_mutations'] == result['database_calls'] == result['secret_payload_reads'] == 0
    assert 'consumer_rbac' in result['not_verified']
    assert all(c['state'] == 'present' for c in result['checks'])
    assert all('--format=json' in args for args in cloud.calls)
    assert not any('access' in args or 'add-iam-policy-binding' in args for args in cloud.calls)


def test_missing_trust_and_signer_are_reported_without_payload_reads():
    cloud = Metadata()
    cloud.secrets = cloud.secrets[:1]
    result = report(cloud)
    missing = {c['check'] for c in result['checks'] if c['state'] == 'missing'}
    assert missing == {TRUST_SECRET, SIGNER_SECRET}
    assert result['status'] == 'PREREQUISITES_UNRESOLVED_NOT_CERTIFIED'
    assert len([a for a in cloud.calls if a[:3] == ('secrets', 'versions', 'describe')]) == 1


@pytest.mark.parametrize('prefix', [('secrets', 'list', '--project=' + PROJECT),
                                  ('iam', 'service-accounts', 'list'),
                                  ('iam', 'service-accounts', 'get-iam-policy'),
                                  ('sql', 'instances', 'describe'), ('run', 'services', 'list')])
def test_lookup_failures_never_become_absence_or_pass(prefix):
    cloud = Metadata()
    cloud.failure = prefix
    result = report(cloud)
    assert result['status'] == 'PREREQUISITES_UNRESOLVED_NOT_CERTIFIED'
    assert any(c['state'] == 'unverified' for c in result['checks'])
    assert 'private-fixture-secret' not in str(result)


@pytest.mark.parametrize('change', ['condition', 'wrong_role', 'wrong_member', 'empty', 'malformed'])
def test_caller_execution_arrangement_fails_closed(change):
    cloud = Metadata()
    if change == 'condition':
        cloud.policy['bindings'][0]['condition'] = {'expression': 'true'}
    elif change == 'wrong_role':
        cloud.policy['bindings'][0]['role'] = 'roles/iam.serviceAccountTokenCreator'
    elif change == 'wrong_member':
        cloud.policy['bindings'][0]['members'] = ['serviceAccount:other@' + PROJECT + '.iam.gserviceaccount.com']
    elif change == 'empty':
        cloud.policy = {}
    else:
        cloud.policy = {'bindings': [{'role': 'roles/iam.serviceAccountUser', 'members': 'not-a-list'}]}
    result = report(cloud)
    assert next(c for c in result['checks'] if c['check'] == 'consumer_execution_arrangement')['state'] == 'unverified'


@pytest.mark.parametrize('state', ['DISABLED', 'DESTROYED', None])
def test_disabled_or_unknown_secret_version_is_not_accepted(state):
    cloud = Metadata()
    cloud.version_state = state
    assert report(cloud)['status'] == 'PREREQUISITES_UNRESOLVED_NOT_CERTIFIED'


def test_unpinned_versions_cannot_appear_ready():
    cloud = Metadata()
    result = inventory(cloud, git_sha='a' * 40)
    assert result['status'] == 'PREREQUISITES_UNRESOLVED_NOT_CERTIFIED'
    assert not any(a[:3] == ('secrets', 'versions', 'describe') for a in cloud.calls)


@pytest.mark.parametrize('attribute,value', [('accounts', [{'email': CALLER_SA, 'disabled': True}]),
                                           ('accounts', [{'email': CALLER_SA}]),
                                           ('accounts', [{'email': CALLER_SA, 'disabled': False}] * 2),
                                           ('source', {'connectionName': 'wrong'}),
                                           ('source', {}), ('services', [{'metadata': {}}]),
                                           ('secrets', [{'wrong': 'shape'}])])
def test_wrong_or_ambiguous_metadata_cannot_appear_ready(attribute, value):
    cloud = Metadata()
    setattr(cloud, attribute, value)
    assert report(cloud)['status'] == 'PREREQUISITES_UNRESOLVED_NOT_CERTIFIED'


def test_existing_feature_resources_require_reconciliation():
    from mfdataindia.review.rollout import API_SA, SECRETS, SERVICE
    cloud = Metadata()
    cloud.secrets.append({'name': 'projects/' + PROJECT + '/secrets/' + next(iter(SECRETS.values()))})
    cloud.accounts.append({'email': API_SA, 'disabled': False})
    cloud.services = [{'metadata': {'name': SERVICE}}]
    result = report(cloud)
    assert len([c for c in result['checks'] if c['state'] == 'reconciliation_required']) == 3


@pytest.mark.parametrize('sha,versions', [('main', {}), ('a' * 40, {TRUST_SECRET: True}),
                                         ('a' * 40, {TRUST_SECRET: 0}), ('a' * 40, {'wrong': 1})])
def test_invalid_inputs_make_no_cloud_calls(sha, versions):
    cloud = Metadata()
    with pytest.raises(ValueError):
        inventory(cloud, git_sha=sha, versions=versions)
    assert cloud.calls == []
