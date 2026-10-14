"""Pinned DEV rollout inputs and evidence; no implicit infrastructure mutations."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from mfdataindia.review.policy import fingerprint, policy

PROJECT = 'trillionlane-dev'
REGION = 'asia-south1'
CONNECTION = PROJECT + ':' + REGION + ':tlws-mf-data-dev'
SERVICE = 'tlws-mf-review-dev'
API_SA = 'tlws-mf-review-api-dev@' + PROJECT + '.iam.gserviceaccount.com'
GEN_SA = 'tlws-mf-review-gen-dev@' + PROJECT + '.iam.gserviceaccount.com'
CALLER_SA = 'trillion-insights-runtime-dev@' + PROJECT + '.iam.gserviceaccount.com'
ROLES = ('mfdata_verified_generator', 'mfdata_verified_reader', 'mfdata_verified_admin')
SECRETS = {r: 'tlws-mf-review-' + suffix + '-dsn-dev' for r, suffix in zip(ROLES, ('gen', 'read', 'admin'))}


class Manifest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    format: Literal['mfdata-review-dev-rollout-v1']
    rollout_id: StrictStr = Field(pattern=r'^[a-z][a-z0-9-]{7,23}$')
    project: Literal['trillionlane-dev']
    region: Literal['asia-south1']
    source_connection: Literal['trillionlane-dev:asia-south1:tlws-mf-data-dev']
    database: Literal['mfdataindia']
    git_sha: StrictStr = Field(pattern=r'^[0-9a-f]{40}$')
    image: StrictStr = Field(pattern=r'^asia-south1-docker\.pkg\.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:[0-9a-f]{64}$')
    policy_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    contract_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    migration_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    trust_secret: StrictStr = Field(pattern=r'^tlws-mf-review-trust-dev$')
    trust_version: StrictInt = Field(gt=0)
    trust_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    signer_secret: StrictStr = Field(pattern=r'^trillion-insights-mf-review-signer-dev$')
    signer_version: StrictInt = Field(gt=0)
    source_secret_version: StrictInt = Field(gt=0)
    cohort: list[StrictInt] = Field(min_length=1, max_length=10)
    actor: StrictStr = Field(pattern=r'^rollout-certification:[a-z][a-z0-9-]{7,23}$')
    canary_code: StrictInt = Field(gt=0, le=2147483647)
    allow_canary_events: Literal[True]
    required_coverage: list[Literal['growth_eligible', 'unsupported_option', 'explicit_null_signals', 'unavailable_metric', 'quality_signalled']] = Field(min_length=2, max_length=5)

    @model_validator(mode='after')
    def bounded(self):
        if len(set(self.cohort)) != len(self.cohort) or any(not 0 < c <= 2147483647 for c in self.cohort):
            raise ValueError('cohort must contain unique positive int32 codes')
        if self.canary_code not in self.cohort or self.actor != 'rollout-certification:' + self.rollout_id:
            raise ValueError('canary/actor must be explicitly bound to rollout')
        if self.policy_hash != fingerprint(policy()):
            raise ValueError('unsupported calculation policy')
        if len(set(self.required_coverage)) != len(self.required_coverage) or not {'growth_eligible', 'unsupported_option'} <= set(self.required_coverage):
            raise ValueError('require explicit Growth/unsupported controls and unique coverage requirements')
        return self

    @property
    def seal(self):
        return fingerprint(self.model_dump(mode='json'))


def load_manifest(path: Path, *, approved_hash: str, git_sha: str, root: Path) -> Manifest:
    # Duplicate JSON members must not give humans and machines different inputs.
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate manifest field')
            result[key] = value
        return result
    manifest = Manifest.model_validate(json.loads(path.read_text(), object_pairs_hook=unique))
    if manifest.seal != approved_hash or manifest.git_sha != git_sha:
        raise ValueError('execution approval does not match manifest/checkout')
    for file, expected in (('contracts/mfdataindia-verified-review-v1.json', manifest.contract_hash),
                           ('sql/optional/023_verified_fund_review.sql', manifest.migration_hash)):
        if hashlib.sha256((root / file).read_bytes()).hexdigest() != expected:
            raise ValueError('contract or migration drift')
    return manifest


def service_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (not re.fullmatch(r'https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.run\.app', value)
            or parsed.username or parsed.password or parsed.port):
        raise ValueError('require exact Cloud Run HTTPS origin; no delimiters/path/query')
    return value


def seal_evidence(manifest: Manifest, stage: str, evidence: dict) -> dict:
    document = {'format': 'mfdata-review-rollout-evidence-v1', 'manifest_hash': manifest.seal,
                'stage': stage, 'evidence': evidence}
    return {**document, 'sha256': fingerprint(document)}


def verify_evidence(manifest: Manifest, stage: str, document: dict) -> dict:
    body = {k: v for k, v in document.items() if k != 'sha256'}
    if (set(document) != {'format', 'manifest_hash', 'stage', 'evidence', 'sha256'}
            or document['format'] != 'mfdata-review-rollout-evidence-v1'
            or document['manifest_hash'] != manifest.seal or document['stage'] != stage
            or document['sha256'] != fingerprint(body)):
        raise ValueError('evidence binding/hash mismatch')
    return document['evidence']
