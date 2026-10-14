"""Explicit existing-human-operator arrangement; no deployer privilege expansion.

This is separate from the CI service-account executor. The project operator
already controls both provider and consumer infrastructure and is checked before
any operation. No Owner/Editor/actAs grant is made to that operator or MFData's
deployer. Consumer private signing material never enters a provider runtime.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preflight import SIGNER_SECRET, TRUST_SECRET
from mfdataindia.review.rollout import CALLER_SA, PROJECT


class OperatorApproval(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    format: Literal['mfdata-review-operator-approval-v2']
    project: Literal['trillionlane-dev']
    account: StrictStr = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
    git_sha: StrictStr = Field(pattern=r'^[0-9a-f]{40}$')
    operation: Literal['bootstrap-consumer-trust', 'execute-rollout']
    binding_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    database_execution: Literal['private-cloud-run']
    backup_image: StrictStr | None = Field(pattern=r'^asia-south1-docker\.pkg\.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:[0-9a-f]{64}$')

    @model_validator(mode='after')
    def backup_pin(self):
        if self.operation == 'execute-rollout' and self.backup_image is None:
            raise ValueError('operator execution requires immutable regional backup image')
        return self

    @property
    def seal(self):
        return fingerprint(self.model_dump(mode='json'))


def trust_declaration():
    return {'issuer': 'trillion-insights', 'audience': 'mfdata-verified-review-dev',
            'caller': 'trillion-insights', 'allowed_scopes': ['review:read', 'review:admin'],
            'trust_secret': TRUST_SECRET, 'signer_secret': SIGNER_SECRET,
            'signer_runtime': CALLER_SA}


def load_operator_approval(path: Path, *, approved_hash: str, operation: str, binding_hash: str):
    def unique(pairs):
        document = {}
        for key, value in pairs:
            if key in document:
                raise ValueError('duplicate operator approval field')
            document[key] = value
        return document
    approval = OperatorApproval.model_validate(json.loads(path.read_text(), object_pairs_hook=unique))
    if (approval.seal != approved_hash or approval.operation != operation or approval.binding_hash != binding_hash
            or approval.account.endswith('.gserviceaccount.com')):
        raise ValueError('operator approval identity/operation/binding differs')
    return approval


def verify_checkout(root: Path, git_sha: str):
    """Exact merged clean code, not a SHA supplied to unreviewed local source."""
    if not re.fullmatch('[0-9a-f]{40}', git_sha):
        raise ValueError('invalid exact source SHA')
    for arguments, expected in ((['rev-parse', 'HEAD'], git_sha), (['rev-parse', 'origin/main'], git_sha),
                                (['remote', 'get-url', 'origin'], 'https://github.com/trillionlane/tlws-mfindia.git')):
        value = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True, text=True, check=True).stdout.strip()
        if value != expected:
            raise ValueError('operator checkout must be exact merged provider main')
    state = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all'],
                           capture_output=True, text=True, check=True).stdout
    if state.strip():
        raise ValueError('operator checkout must be clean; keep manifests/evidence outside it')
    live = subprocess.run(['gh', 'api', 'repos/trillionlane/tlws-mfindia/git/ref/heads/main', '--jq', '.object.sha'],
                          capture_output=True, text=True, check=True).stdout.strip()
    if live != git_sha:
        raise ValueError('live merged main differs; reconcile and renew exact pins')


def verify_operator(cloud, approval: OperatorApproval):
    if cloud.account != approval.account:
        raise ValueError('cloud account must be explicitly approval-pinned')
    # Do not infer authority from a role name or principal supplied by the runner.
    observed = cloud.command('auth', 'list', '--filter=account:' + approval.account,
                             '--format=json', json_output=True)
    if not isinstance(observed, list) or len(observed) != 1 or observed[0].get('account') != approval.account:
        raise ValueError('approved human operator credentials unavailable')
    project = cloud.command('projects', 'get-iam-policy', PROJECT, '--format=json', json_output=True)
    if not any(b.get('role') == 'roles/owner' and not b.get('condition')
               and 'user:' + approval.account in b.get('members', []) for b in project.get('bindings', [])):
        raise ValueError('requires the existing approved project operator; never bootstrap privilege')
    return {'account': approval.account, 'level': 'EXISTING_PROJECT_OPERATOR',
            'operator_iam_grants': 0, 'provider_deployer_consumer_grants': 0}


def bootstrap_consumer_trust(cloud, approval: OperatorApproval, directory: Path):
    if approval.operation != 'bootstrap-consumer-trust' or approval.binding_hash != fingerprint(trust_declaration()):
        raise ValueError('consumer trust bootstrap scope differs')
    verify_operator(cloud, approval)
    existing = cloud.command('secrets', 'list', '--project=' + PROJECT, '--format=json', json_output=True)
    if not isinstance(existing, list) or any(not isinstance(s, dict) or not isinstance(s.get('name'), str) for s in existing):
        raise ValueError('secret inventory unavailable')
    if {s['name'].split('/')[-1] for s in existing} & {TRUST_SECRET, SIGNER_SECRET}:
        raise ValueError('consumer trust already exists; no rotation or blind reconciliation')
    key = Ed25519PrivateKey.generate()
    trust = {k: v for k, v in trust_declaration().items() if k in ('issuer', 'audience', 'caller', 'allowed_scopes')}
    trust['public_key'] = key.public_key().public_bytes(serialization.Encoding.PEM,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    AssertionVerifier(**{**trust, 'allowed_scopes': frozenset(trust['allowed_scopes'])})
    # Retain the public half BEFORE any cloud mutation, allowing failure diagnosis
    # without retrieving private material. This file contains no private key.
    directory.mkdir(mode=0o700)
    with (directory / 'public-trust.json').open('x') as handle:
        json.dump(trust, handle, sort_keys=True)
    versions = {}
    values = [(SIGNER_SECRET, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()).decode()),
              (TRUST_SECRET, json.dumps(trust, sort_keys=True))]
    for name, value in values:
        cloud.command('secrets', 'create', name, '--project=' + PROJECT, '--replication-policy=automatic',
                      '--labels=product=trillion-insights,environment=dev,purpose=mfdata-review-delegation')
        metadata = cloud.command('secrets', 'versions', 'add', name, '--project=' + PROJECT,
                                 '--data-file=-', '--format=json', input_text=value, json_output=True)
        version = metadata.get('name', '').split('/')[-1]
        if not version.isdigit() or int(version) <= 0:
            raise ValueError('created consumer credential version not established')
        versions[name] = int(version)
        cloud.command('secrets', 'add-iam-policy-binding', name, '--project=' + PROJECT,
                      '--member=serviceAccount:' + CALLER_SA, '--role=roles/secretmanager.secretAccessor',
                      '--condition=None', '--quiet')
    del values, key
    receipt = {'format': 'mfdata-review-consumer-trust-bootstrap-v1', 'git_sha': approval.git_sha,
               'operator_approval_hash': approval.seal, 'trust_hash': fingerprint(trust), 'versions': versions,
               'private_key_fetched': False, 'private_key_written_to_local_files': False,
               'private_key_logged': False,
               'signer_owner': 'trillion-insights', 'authorized_signer_reader': CALLER_SA,
               'provider_deployer_consumer_grants': 0, 'consumer_rbac': 'NOT_CERTIFIED'}
    with (directory / 'bootstrap-receipt.json').open('x') as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2)
    return receipt
