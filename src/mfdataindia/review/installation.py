"""Same atomic preservation gate, executed beside Cloud SQL rather than off-region."""
from __future__ import annotations

import json
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from mfdataindia.review.rollout import CONNECTION, PROJECT, ROLES, SECRETS, seal_evidence, verify_evidence
from mfdataindia.review.rollout_database import install_and_reconcile, readonly

MIGRATION_SA = 'tlws-mf-data-migrate-dev@' + PROJECT + '.iam.gserviceaccount.com'
ROLE_ENV = dict(zip(ROLES, ('MFDATA_REVIEW_GEN_DSN', 'MFDATA_REVIEW_READ_DSN', 'MFDATA_REVIEW_ADMIN_DSN')))


def install_job(manifest, environment: dict, root: Path):
    source = environment['MFDATAINDIA_DSN']
    parsed = conninfo_to_dict(source)
    if (parsed.get('host') != '/cloudsql/' + CONNECTION or parsed.get('dbname') != 'mfdataindia'
            or parsed.get('user') != 'mfdata_admin' or not parsed.get('password') or parsed.get('hostaddr')):
        raise ValueError('installation source credential identity differs')
    with readonly(source) as conn:
        identity = conn.execute("SELECT current_database() AS database,session_user AS principal,"
                                "current_setting('server_version_num')::int AS version").fetchone()
        if identity['database'] != 'mfdataindia' or identity['principal'] != 'mfdata_admin' or identity['version'] // 10000 != 18:
            raise ValueError('installation live database identity differs')
    passwords = {}
    for role, name in ROLE_ENV.items():
        credential = conninfo_to_dict(environment[name])
        if (credential.get('host') != parsed['host'] or credential.get('dbname') != parsed['dbname']
                or credential.get('user') != role or not credential.get('password') or credential.get('hostaddr')):
            raise ValueError('optional credential is not bound to its approved source/role')
        passwords[role] = credential['password']
    preservation = install_and_reconcile(source, root, manifest, passwords)
    return seal_evidence(manifest, 'database-installation',
                         {'level': 'DEV_CLOUD_SQL_JOB', 'preservation': preservation, 'original_source_rows_changed': 0,
                          'source_ledger_action': 'APPEND_PINNED_OPTIONAL_MIGRATION_RECEIPT',
                          'optional_migration': '023_verified_fund_review.sql', 'transaction_bound_seconds': 180})


def cloud_install(manifest, cloud, versions, directory):
    if set(versions) != set(ROLES) or any(type(v) is not int or v <= 0 for v in versions.values()):
        raise ValueError('installation requires exact three numerical credential versions')
    granted = []
    try:
        # Existing migration workload already owns source-admin-secret access.
        # New role credentials are temporary readers on NEW secrets only.
        for role in ROLES:
            # Arm cleanup before a possibly-applied command failure.
            granted.append(SECRETS[role])
            cloud.command('secrets', 'add-iam-policy-binding', SECRETS[role], '--project=' + PROJECT,
                          '--member=serviceAccount:' + MIGRATION_SA, '--role=roles/secretmanager.secretAccessor',
                          '--condition=None', '--quiet')
        assignments = ['MFDATAINDIA_DSN=tlws-mf-data-admin-dsn-dev:' + str(manifest.source_secret_version)]
        assignments += [ROLE_ENV[role] + '=' + SECRETS[role] + ':' + str(versions[role]) for role in ROLES]
        execution, document = cloud.job(manifest, 'mf-review-install-' + manifest.rollout_id, MIGRATION_SA,
                                        ['scripts/install_verified_review_dev.py'],
                                        {'DEPLOYMENT_GIT_SHA': manifest.git_sha, 'REVIEW_MANIFEST': manifest.model_dump_json()},
                                        assignments, directory)
        result = verify_evidence(manifest, 'database-installation', document)
        if (result.get('level') != 'DEV_CLOUD_SQL_JOB' or result.get('original_source_rows_changed') != 0
                or result.get('source_ledger_action') != 'APPEND_PINNED_OPTIONAL_MIGRATION_RECEIPT'
                or result.get('transaction_bound_seconds') != 180
                or result.get('optional_migration') != '023_verified_fund_review.sql'
                or not result.get('preservation', {}).get('source_values_preserved')):
            raise ValueError('installation certificate incomplete')
        # Receipt is retained even if temporary-access removal fails afterward.
        with (directory / '03-install-execution.json').open('x') as handle:
            json.dump({'execution': execution, 'certificate': document}, handle, sort_keys=True, indent=2)
        return result['preservation']
    finally:
        failures = []
        for name in granted:
            try:
                cloud.command('secrets', 'remove-iam-policy-binding', name, '--project=' + PROJECT,
                              '--member=serviceAccount:' + MIGRATION_SA, '--role=roles/secretmanager.secretAccessor',
                              '--condition=None', '--quiet')
                policy = cloud.command('secrets', 'get-iam-policy', name, '--project=' + PROJECT,
                                       '--format=json', json_output=True)
                if (not isinstance(policy, dict) or not isinstance(policy.get('bindings', []), list)
                        or any(not isinstance(b, dict) or not isinstance(b.get('members', []), list)
                               or 'serviceAccount:' + MIGRATION_SA in b.get('members', [])
                               for b in policy.get('bindings', []))):
                    raise ValueError('temporary secret-level reader binding remains or is unverified')
            except Exception:
                failures.append(name)
        if failures:
            raise RuntimeError('temporary installation credential access removal unconfirmed')
