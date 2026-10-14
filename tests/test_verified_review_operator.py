"""Explicit existing operator, sealed approvals and consumer-only key handling."""
import json

import pytest
from cryptography.hazmat.primitives import serialization

from mfdataindia.review.operator import (
    OperatorApproval, bootstrap_consumer_trust, load_operator_approval,
    trust_declaration, verify_checkout, verify_operator,
)
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import CALLER_SA, PROJECT
from mfdataindia.review.rollout_cloud import Cloud


def approval(**changes):
    return OperatorApproval.model_validate({
        'format': 'mfdata-review-operator-approval-v1', 'project': PROJECT,
        'account': 'fixture-operator@example.test', 'git_sha': 'a' * 40,
        'operation': 'bootstrap-consumer-trust', 'binding_hash': fingerprint(trust_declaration()), **changes})


class OperatorCloud:
    def __init__(self):
        self.account = approval().account
        self.owner = True
        self.auth = True
        self.secrets = []
        self.calls = []
        self.payloads = {}

    def command(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('auth', 'list'):
            return [{'account': self.account}] if self.auth else []
        if args[:2] == ('projects', 'get-iam-policy'):
            return {'bindings': [{'role': 'roles/owner', 'members': ['user:' + self.account]}] if self.owner else []}
        if args[:2] == ('secrets', 'list'):
            return self.secrets
        if args[:2] == ('secrets', 'create'):
            assert '--labels=product=trillion-insights,environment=dev,purpose=mfdata-review-delegation' in args
            return ''
        if args[:3] == ('secrets', 'versions', 'add'):
            self.payloads[args[3]] = kwargs['input_text']
            return {'name': 'projects/' + PROJECT + '/secrets/' + args[3] + '/versions/1'}
        if args[:2] == ('secrets', 'add-iam-policy-binding'):
            assert '--member=serviceAccount:' + CALLER_SA in args
            assert '--role=roles/secretmanager.secretAccessor' in args
            return ''
        raise AssertionError('undeclared or privileged command')


def test_approval_is_exact_and_duplicate_fields_rejected(tmp_path):
    item = approval()
    path = tmp_path / 'approval.json'
    path.write_text(item.model_dump_json())
    assert load_operator_approval(path, approved_hash=item.seal, operation=item.operation,
                                  binding_hash=item.binding_hash) == item
    for overrides in ({'approved_hash': '0' * 64}, {'operation': 'execute-rollout'}, {'binding_hash': '0' * 64}):
        with pytest.raises(ValueError):
            load_operator_approval(path, **{'approved_hash': item.seal, 'operation': item.operation,
                                           'binding_hash': item.binding_hash, **overrides})
    path.write_text(item.model_dump_json()[:-1] + ',"account":"other@example.test"}')
    with pytest.raises(ValueError, match='duplicate'):
        load_operator_approval(path, approved_hash=item.seal, operation=item.operation, binding_hash=item.binding_hash)


def test_service_account_cannot_be_used_as_human_operator(tmp_path):
    item = approval(account=CALLER_SA)
    path = tmp_path / 'approval.json'
    path.write_text(item.model_dump_json())
    with pytest.raises(ValueError, match='identity'):
        load_operator_approval(path, approved_hash=item.seal, operation=item.operation, binding_hash=item.binding_hash)


@pytest.mark.parametrize('attribute,value', [('owner', False), ('auth', False), ('account', 'other@example.test')])
def test_missing_existing_operator_authority_never_bootstraps_iam(attribute, value, tmp_path):
    cloud = OperatorCloud()
    setattr(cloud, attribute, value)
    with pytest.raises(ValueError):
        bootstrap_consumer_trust(cloud, approval(), tmp_path / 'receipt')
    assert not any(a[:2] == ('secrets', 'create') or 'add-iam-policy-binding' in a for a in cloud.calls)


def test_private_key_only_enters_consumer_secret_not_files_logs_or_provider(tmp_path):
    cloud = OperatorCloud()
    path = tmp_path / 'receipt'
    receipt = bootstrap_consumer_trust(cloud, approval(), path)
    trust = json.loads((path / 'public-trust.json').read_text())
    private = cloud.payloads['trillion-insights-mf-review-signer-dev']
    key = serialization.load_pem_private_key(private.encode(), password=None)
    assert key.public_key().public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo).decode() == trust['public_key']
    assert receipt['trust_hash'] == fingerprint(trust)
    assert receipt['provider_deployer_consumer_grants'] == 0
    assert receipt['consumer_rbac'] == 'NOT_CERTIFIED'
    assert 'PRIVATE KEY' not in ''.join(p.read_text() for p in path.iterdir())
    assert not any('access' in a or '--role=roles/owner' in a or '--role=roles/editor' in a
                   or '--role=roles/iam.serviceAccountUser' in a for a in cloud.calls)
    assert verify_operator(cloud, approval())['operator_iam_grants'] == 0


@pytest.mark.parametrize('name', ['tlws-mf-review-trust-dev', 'trillion-insights-mf-review-signer-dev'])
def test_existing_secret_stops_without_rotation(name, tmp_path):
    cloud = OperatorCloud()
    cloud.secrets = [{'name': 'projects/' + PROJECT + '/secrets/' + name}]
    with pytest.raises(ValueError, match='already exists'):
        bootstrap_consumer_trust(cloud, approval(), tmp_path / 'receipt')
    assert not any(a[:2] == ('secrets', 'create') for a in cloud.calls)


def test_partial_bootstrap_retains_public_evidence_and_never_deletes(tmp_path):
    cloud = OperatorCloud()
    original = cloud.command
    def fail(*args, **kwargs):
        if args[:3] == ('secrets', 'create', 'tlws-mf-review-trust-dev'):
            raise RuntimeError('simulated failure')
        return original(*args, **kwargs)
    cloud.command = fail
    with pytest.raises(RuntimeError):
        bootstrap_consumer_trust(cloud, approval(), tmp_path / 'receipt')
    assert (tmp_path / 'receipt/public-trust.json').is_file()
    assert 'trillion-insights-mf-review-signer-dev' in cloud.payloads
    assert not any('delete' in a or 'destroy' in a for a in cloud.calls)


def test_cloud_pins_account_without_modifying_shared_configuration(monkeypatch):
    calls = []
    class Result:
        returncode = 0
        stdout = '{}'
    def run(args, **kwargs):
        calls.append(args)
        return Result()
    monkeypatch.setattr('mfdataindia.review.rollout_cloud.subprocess.run', run)
    assert Cloud(account=approval().account).command('projects', 'describe', PROJECT, json_output=True) == {}
    assert calls[0][:2] == ['gcloud', '--account=' + approval().account]
    Cloud().command('projects', 'describe', PROJECT)
    assert calls[1][:2] == ['gcloud', 'projects']


@pytest.mark.parametrize('drift', [None, 'head', 'origin', 'remote', 'dirty', 'live'])
def test_operator_execution_requires_clean_exact_live_merged_checkout(monkeypatch, tmp_path, drift):
    def run(args, **kwargs):
        command = args[3:]
        value = 'a' * 40
        if command[:3] == ['remote', 'get-url', 'origin']:
            value = 'https://github.com/trillionlane/tlws-mfindia.git'
            if drift == 'remote':
                value = 'https://example.test/other'
        elif command[:2] == ['rev-parse', 'HEAD'] and drift == 'head':
            value = 'b' * 40
        elif command[:2] == ['rev-parse', 'origin/main'] and drift == 'origin':
            value = 'b' * 40
        elif command[0] == 'status':
            value = ' M src/changed.py' if drift == 'dirty' else ''
        elif args[0] == 'gh' and drift == 'live':
            value = 'b' * 40
        return type('Result', (), {'stdout': value})()
    monkeypatch.setattr('mfdataindia.review.operator.subprocess.run', run)
    if drift is None:
        verify_checkout(tmp_path, 'a' * 40)
    else:
        with pytest.raises(ValueError):
            verify_checkout(tmp_path, 'a' * 40)
