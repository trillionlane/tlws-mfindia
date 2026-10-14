#!/usr/bin/env python3
"""Manual, exact-manifest DEV rollout. Never run on merge or on a schedule."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

import requests
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from mfdataindia.review.auth import AssertionVerifier  # noqa: E402
from mfdataindia.review.image_provenance import verify_image  # noqa: E402
from mfdataindia.review.policy import fingerprint  # noqa: E402
from mfdataindia.review.rollout import (  # noqa: E402
    API_SA, CALLER_SA, CONNECTION, GEN_SA, PROJECT, ROLES, SECRETS,
    SERVICE, Manifest, load_manifest, seal_evidence, verify_evidence)
from mfdataindia.review.rollout_cloud import Cloud, verify_revision, verify_service  # noqa: E402
from mfdataindia.review.rollout_database import (  # noqa: E402
    PgTools, file_sha256, fresh_backup, install_and_reconcile, proxy_dsn, readonly, rehearse, verify_logins)


def archive(directory, name, manifest, stage, evidence):
    file = directory / (name + '.json')
    with file.open('x') as handle:
        json.dump(seal_evidence(manifest, stage, evidence), handle, sort_keys=True, indent=2)


def external_denial(origin):
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(origin + '/api/review/v1/funds', timeout=20, allow_redirects=False)
        if response.status_code not in (401, 403, 404):
            raise ValueError('external unauthenticated probe did not establish access denial')
    return response.status_code


def recover(cloud, staged, promotion_attempted, manifest):
    recovery = {'status': 'FAILED_CLOSED', 'candidate_retained': True}
    errors = []
    # Each safety action is independent: a failed traffic restoration must not
    # bypass access revocation. No candidate existence query precedes either.
    if staged and staged['previous_revision'] and promotion_attempted:
        try:
            cloud.promote(staged['previous_revision'])
            recovery['prior_traffic_restored'] = True
        except Exception:
            errors.append('traffic_restoration')
    try:
        cloud.close_access()
        recovery['invoker_access_revoked'] = True
    except Exception:
        errors.append('access_revocation')
    if staged:
        try:
            cloud.command('run', 'services', 'update-traffic', SERVICE, '--project=' + PROJECT, '--region=asia-south1',
                          '--remove-tags=cert-' + manifest.rollout_id, '--quiet')
        except Exception:
            errors.append('tag_removal')
    if errors:
        recovery.update(status='RECOVERY_UNCONFIRMED_REQUIRES_OPERATOR', failed_actions=errors)
    return recovery


def rollout(manifest: Manifest, cloud: Cloud, directory: Path, clone_dsn: str, *, database_installer=None, backup_creator=None):
    # All cloud resources are fixed or deterministically named in this module.
    # No cleanup state lives in a shell-local EXIT handler.
    source = proxy_dsn(cloud.secret('tlws-mf-data-admin-dsn-dev', manifest.source_secret_version))
    info = cloud.command('sql', 'instances', 'describe', 'tlws-mf-data-dev', '--project=' + PROJECT, '--format=json', json_output=True)
    if info['connectionName'] != CONNECTION or info['databaseVersion'] != 'POSTGRES_18':
        raise ValueError('live Cloud SQL identity/version differs')
    trust_text = cloud.secret(manifest.trust_secret, manifest.trust_version)
    trust = json.loads(trust_text)
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError('approved trust changed')
    AssertionVerifier(**{**trust, 'allowed_scopes': frozenset(trust['allowed_scopes'])})
    if not {'review:read', 'review:admin'} <= set(trust['allowed_scopes']):
        raise ValueError('cohort certification requires approved read/admin capabilities')
    # Verify prepared image provenance before DB/identity mutations.
    cloud_digest = manifest.image.split('@')[1]
    verify_image(cloud, manifest.image, manifest.git_sha)
    if backup_creator:
        backup = backup_creator(manifest, cloud, directory)
    else:
        backup = local_backup(manifest, cloud, directory, source)
    archive(directory, '01-backup', manifest, 'backup', {k: v for k, v in backup.items() if k != 'roles'})
    return rollout_after_backup(manifest, cloud, directory, clone_dsn, source, backup, cloud_digest,
                                database_installer=database_installer)


def local_backup(manifest, cloud, directory, source):
    backup = fresh_backup(source, directory, PgTools())
    # Immutable create-only backup object. Never upload backups as GitHub artifacts.
    object_url = 'gs://tlws_mf_data_source/verified-review-rollouts/' + manifest.rollout_id + '/snapshot.dump'
    cloud.command('storage', 'cp', str(directory / 'snapshot.dump'), object_url, '--if-generation-match=0')
    backup_object = cloud.command('storage', 'objects', 'describe', object_url, '--format=json', json_output=True)
    if int(backup_object['size']) != backup['backup_bytes'] or not backup_object.get('generation'):
        raise ValueError('immutable backup metadata missing')
    confirmed = directory / 'confirmed.dump'
    cloud.command('storage', 'cp', object_url + '#' + str(backup_object['generation']), str(confirmed))
    if file_sha256(confirmed) != backup['backup_sha256']:
        raise ValueError('archived backup bytes differ from the exported snapshot')
    confirmed.replace(directory / 'snapshot.dump')
    backup['object'] = {'uri': object_url, 'generation': str(backup_object['generation']), 'size': backup['backup_bytes']}
    return backup


def rollout_after_backup(manifest, cloud, directory, clone_dsn, source, backup, cloud_digest, *, database_installer=None):
    rehearsal = rehearse(source, clone_dsn, backup, directory, manifest, ROOT, PgTools())
    archive(directory, '02-rehearsal', manifest, 'rehearsal', rehearsal)

    # Fail on ambiguous partial prior provisioning. No secret/password rotation,
    # guessed 'not found', broad bootstrap or changes to unrelated principals.
    with readonly(source) as conn:
        existing = [r['rolname'] for r in conn.execute('SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)', (list(ROLES),)).fetchall()]
    secret_names = {s['name'].split('/')[-1] for s in cloud.command('secrets', 'list', '--project=' + PROJECT, '--format=json', json_output=True)}
    if existing or secret_names & set(SECRETS.values()):
        raise ValueError('optional principal/credential already exists; explicit reconciliation required')
    for account in (API_SA, GEN_SA):
        cloud.ensure_identity(account)
    cloud.command('secrets', 'add-iam-policy-binding', manifest.trust_secret, '--project=' + PROJECT,
                  '--member=serviceAccount:' + API_SA, '--role=roles/secretmanager.secretAccessor', '--condition=None', '--quiet')
    # The signer is consumer-owned and is only mounted in its approved workload.
    # Its private bytes are never fetched by this runner or provider API.
    for secret_name in (manifest.trust_secret, manifest.signer_secret):
        cloud.command('secrets', 'add-iam-policy-binding', secret_name, '--project=' + PROJECT,
                      '--member=serviceAccount:' + CALLER_SA, '--role=roles/secretmanager.secretAccessor', '--condition=None', '--quiet')
    passwords, versions = {}, {}
    for role, account in zip(ROLES, (GEN_SA, CALLER_SA, API_SA)):
        passwords[role] = secrets.token_hex(32)
        dsn = make_conninfo(dbname='mfdataindia', user=role, password=passwords[role], host='/cloudsql/' + CONNECTION)
        versions[role] = cloud.write_secret(SECRETS[role], dsn, account)
    preservation = (database_installer(manifest, cloud, versions, directory) if database_installer
                    else install_and_reconcile(source, ROOT, manifest, passwords))
    dsns = {role: make_conninfo(source, user=role, password=password) for role, password in passwords.items()}
    verify_logins(dsns)
    archive(directory, '03-database', manifest, 'database', {'preservation': preservation, 'credential_versions': versions,
            'direct_logins_verified': list(ROLES), 'level': 'DEV_CLOUD_SQL'})
    del passwords, dsns
    staged = None
    promotion_attempted = False
    try:
        staged = cloud.stage(manifest, versions, directory)
        external_denial(staged['origin'])
        external_denial(staged['candidate_origin'])
        common = {'DEPLOYMENT_GIT_SHA': manifest.git_sha, 'REVIEW_MANIFEST': manifest.model_dump_json()}
        name = 'mf-review-gen-' + manifest.rollout_id
        gen_execution, generation = cloud.job(manifest, name, GEN_SA, ['scripts/certify_verified_review_dev.py'],
                       {**common, 'REVIEW_OPERATION': 'generate'}, ['MFDATAINDIA_DSN=' + SECRETS[ROLES[0]] + ':' + str(versions[ROLES[0]])], directory)
        generated = verify_evidence(manifest, 'generation', generation)
        if generated.get('level') != 'DEV_DATABASE' or generated.get('source_writes') != 0:
            raise ValueError('generation certificate invalid')
        archive(directory, '04-generation', manifest, 'generation-execution', {'execution': gen_execution, 'certificate': generation})

        def certification(origin, name):
            execution, document = cloud.job(manifest, name, CALLER_SA, ['scripts/certify_verified_review_dev.py'],
                    {**common, 'REVIEW_OPERATION': 'certify', 'REVIEW_ORIGIN': origin, 'REVIEW_AUDIENCE': staged['origin']},
                    ['MFDATAINDIA_DSN=' + SECRETS[ROLES[1]] + ':' + str(versions[ROLES[1]]),
                     '/trust/config.json=' + manifest.trust_secret + ':' + str(manifest.trust_version),
                     '/signer/key.pem=' + manifest.signer_secret + ':' + str(manifest.signer_version)], directory)
            evidence = verify_evidence(manifest, 'certification', document)
            if evidence.get('level') != 'DEV_PRIVATE_HTTP' or evidence.get('endpoints') != 6 or evidence.get('origin') != origin:
                raise ValueError('private certification evidence incomplete')
            if {r['code']: r['result_id'] for r in generated['results']} != {r['code']: r['result_id'] for r in evidence['cohort']}:
                raise ValueError('certification differs from generation; inputs changed')
            return {'execution': execution, 'certificate': document}

        candidate_certificate = certification(staged['candidate_origin'], 'mf-review-cert-' + manifest.rollout_id)
        archive(directory, '05-candidate', manifest, 'candidate', {'deployment': staged, 'certification': candidate_certificate})
        # Reconcile identity, digest, config and IAM immediately before promotion.
        verify_service(cloud.service())
        verify_revision(cloud.revision(staged['candidate']), manifest, versions)
        cloud.iam(complete=True)
        promotion_attempted = True
        cloud.promote(staged['candidate'])
        service = cloud.service()
        verify_service(service)
        serving = [t for t in service['status']['traffic'] if t.get('percent', 0) > 0]
        if len(serving) != 1 or serving[0]['revisionName'] != staged['candidate'] or serving[0]['percent'] != 100:
            raise ValueError('verified candidate is not serving all traffic')
        final = certification(staged['origin'], 'mf-review-final-' + manifest.rollout_id)
        cloud.command('run', 'services', 'update-traffic', SERVICE, '--project=' + PROJECT, '--region=asia-south1',
                      '--remove-tags=cert-' + manifest.rollout_id, '--quiet')
        cloud.iam(complete=True)
        external_denial(staged['origin'])
        archive(directory, '06-closeout', manifest, 'closeout', {'revision': staged['candidate'], 'image_digest': cloud_digest,
                'credential_versions': versions, 'certification': final, 'status': 'PROVIDER_DEV_CERTIFIED',
                'consumer_rbac_acceptance': 'NOT_CERTIFIED', 'source_corrections': 'NOT_ENABLED'})
    except BaseException:
        # Restore traffic independently of candidate lookup/recovery creation.
        # First deployment has no known good prior: close new access instead.
        archive(directory, 'failure-recovery', manifest, 'failure', recover(cloud, staged, promotion_attempted, manifest))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('hash', 'validate', 'execute'))
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--approved-hash')
    parser.add_argument('--git-sha', required=True)
    parser.add_argument('--evidence-directory', type=Path)
    args = parser.parse_args(argv)
    approval = Manifest.model_validate_json(args.manifest.read_text()).seal if args.command == 'hash' else args.approved_hash
    manifest = load_manifest(args.manifest, approved_hash=approval, git_sha=args.git_sha, root=ROOT)
    if args.command in ('hash', 'validate'):
        print(json.dumps({'manifest_hash': manifest.seal, 'source_writes': 0, 'cloud_calls': 0}))
        return 0
    if not args.evidence_directory or args.evidence_directory.exists():
        raise ValueError('require a new evidence directory; never overwrite previous evidence')
    args.evidence_directory.mkdir(mode=0o700)
    clone = os.environ['MFDATA_REVIEW_CLONE_ADMIN_DSN']
    # Backup bytes are private and ephemeral on runner; certificates only enter
    # the evidence directory. Retained immutable backup lives in approved GCS.
    with tempfile.TemporaryDirectory(prefix='mf-review-backup-') as scratch:
        work = Path(scratch)
        try:
            rollout(manifest, Cloud(), work, clone)
        except BaseException:
            archive(work, '07-failure', manifest, 'failed', {'status': 'NOT_CERTIFIED',
                    'new_artifacts_retained': True, 'requires_operator_reconciliation': True})
            raise
        finally:
            for file in work.glob('*.json'):
                if file.name.endswith('-env.json') or file.name == 'api-env.json':
                    continue
                args.evidence_directory.joinpath(file.name).write_bytes(file.read_bytes())
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('verified DEV rollout failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
