"""Read-only first-install prerequisite inventory; never a rollout certificate."""
from __future__ import annotations

import re

from mfdataindia.review.rollout import (
    API_SA, CALLER_SA, CONNECTION, GEN_SA, PROJECT, REGION, SECRETS, SERVICE,
)

DEPLOY_SA = 'tlws-mf-data-deploy-dev@' + PROJECT + '.iam.gserviceaccount.com'
TRUST_SECRET = 'tlws-mf-review-trust-dev'
SIGNER_SECRET = 'trillion-insights-mf-review-signer-dev'
SOURCE_SECRET = 'tlws-mf-data-admin-dsn-dev'


def inventory(cloud, *, git_sha: str, versions: dict[str, int] | None = None) -> dict:
    """Only metadata reads. Lookup failures are unknown, never absence/success.

    Explicit caller delegation is a prerequisite of the current runner, not an
    instruction to add it. A safer consumer-owned execution arrangement requires
    reviewed runner changes. IAM conditions/inherited privileges are not guessed.
    Key correspondence, source grants, backup and APIs are NOT certified here.
    """
    if not re.fullmatch('[0-9a-f]{40}', git_sha):
        raise ValueError('require exact source SHA')
    versions = versions or {}
    if set(versions) - {TRUST_SECRET, SIGNER_SECRET, SOURCE_SECRET} or any(
        type(v) is not int or v <= 0 for v in versions.values()
    ):
        raise ValueError('require known secret names and positive numerical versions')
    checks = []

    def check(name, state, detail):
        checks.append({'check': name, 'state': state, 'detail': detail})

    def read(*args):
        try:
            return cloud.command(*args, '--format=json', json_output=True)
        except Exception:
            # Never retain exception text: adapters may embed credentials/errors.
            return None

    secrets = read('secrets', 'list', '--project=' + PROJECT)
    if isinstance(secrets, list) and all(isinstance(s, dict) and isinstance(s.get('name'), str) for s in secrets):
        names = {s['name'].split('/')[-1] for s in secrets}
        for name in (SOURCE_SECRET, TRUST_SECRET, SIGNER_SECRET):
            if name not in names:
                check(name, 'missing', 'required secret metadata absent')
                continue
            revision = versions.get(name)
            if revision is None:
                check(name, 'unverified', 'pin a numerical secret version; payload is never read')
                continue
            metadata = read('secrets', 'versions', 'describe', str(revision),
                            '--secret=' + name, '--project=' + PROJECT)
            expected = 'projects/'
            identity_ok = (isinstance(metadata, dict) and isinstance(metadata.get('name'), str)
                           and metadata['name'].startswith(expected)
                           and metadata['name'].endswith('/secrets/' + name + '/versions/' + str(revision)))
            check(name, 'present' if identity_ok and metadata.get('state') == 'ENABLED' else 'unverified',
                  'exact enabled version metadata required; key contents/permissions not certified')
        conflicts = sorted(names & set(SECRETS.values()))
        check('first_install_credentials', 'reconciliation_required' if conflicts else 'present',
              'existing optional credentials require reviewed reconciliation' if conflicts else 'optional credentials absent')
    else:
        check('secret_inventory', 'unverified', 'secret metadata inventory unavailable or malformed')

    accounts = read('iam', 'service-accounts', 'list', '--project=' + PROJECT)
    if isinstance(accounts, list) and all(isinstance(a, dict) and isinstance(a.get('email'), str) for a in accounts):
        matches = [a for a in accounts if a['email'] == CALLER_SA]
        check('consumer_identity', 'present' if len(matches) == 1 and matches[0].get('disabled') is False else 'unverified',
              'exact enabled Insights runtime required')
        for email in (API_SA, GEN_SA):
            matches = [a for a in accounts if a['email'] == email]
            check(email, 'reconciliation_required' if matches else 'present',
                  'existing identity privileges require reviewed reconciliation' if matches else 'identity absent; creation not performed')
    else:
        check('identity_inventory', 'unverified', 'identity inventory unavailable or malformed')

    policy = read('iam', 'service-accounts', 'get-iam-policy', CALLER_SA, '--project=' + PROJECT)
    bindings = policy.get('bindings') if isinstance(policy, dict) else None
    if isinstance(bindings, list) and all(isinstance(b, dict) and isinstance(b.get('role'), str)
                                        and isinstance(b.get('members'), list)
                                        and all(isinstance(m, str) for m in b['members']) for b in bindings):
        explicit = any(b['role'] == 'roles/iam.serviceAccountUser' and not b.get('condition')
                       and 'serviceAccount:' + DEPLOY_SA in b['members'] for b in bindings)
        check('consumer_execution_arrangement', 'present' if explicit else 'unverified',
              'current runner explicit delegation found; effective authorization not certified' if explicit else
              'current runner delegation not established; prefer reviewed consumer-owned execution; no IAM grant performed')
    else:
        check('consumer_execution_arrangement', 'unverified', 'consumer IAM policy unavailable or malformed')

    source = read('sql', 'instances', 'describe', 'tlws-mf-data-dev', '--project=' + PROJECT)
    check('source_instance', 'present' if isinstance(source, dict) and source.get('connectionName') == CONNECTION
          and source.get('databaseVersion') == 'POSTGRES_18' and source.get('state') == 'RUNNABLE' else 'unverified',
          'exact runnable PostgreSQL 18 source required; no connection/data reads performed')
    services = read('run', 'services', 'list', '--project=' + PROJECT, '--region=' + REGION)
    if isinstance(services, list) and all(isinstance(s, dict) and isinstance(s.get('metadata'), dict)
                                         and isinstance(s['metadata'].get('name'), str) for s in services):
        exists = any(s['metadata']['name'] == SERVICE for s in services)
        check('first_install_service', 'reconciliation_required' if exists else 'present',
              'existing service requires reviewed reconciliation' if exists else 'review service absent; no deployment performed')
    else:
        check('first_install_service', 'unverified', 'service metadata inventory unavailable or malformed')
    return {'format': 'mfdata-review-prerequisites-v1', 'git_sha': git_sha,
            'project': PROJECT, 'region': REGION,
            'status': 'PREREQUISITES_OBSERVED_NOT_CERTIFIED' if all(c['state'] == 'present' for c in checks)
            else 'PREREQUISITES_UNRESOLVED_NOT_CERTIFIED',
            'checks': checks, 'cloud_mutations': 0, 'database_calls': 0, 'secret_payload_reads': 0,
            'execution_authorized': False,
            'not_verified': ['key_correspondence', 'effective_deployer_permissions', 'database_principals',
                             'backup_restore', 'source_preservation', 'private_api', 'consumer_rbac']}
