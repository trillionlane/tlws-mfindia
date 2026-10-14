"""Read-only PG18 export beside Cloud SQL; immutable private archive, no restore."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from mfdataindia.review.rollout import CONNECTION, PROJECT, seal_evidence, verify_evidence
from mfdataindia.review.image_provenance import verify_image
from mfdataindia.review.rollout_database import file_sha256, fresh_backup

BUCKET = 'tlws_mf_data_source'
BACKUP_SA = 'tlws-mf-data-restore-dev@' + PROJECT + '.iam.gserviceaccount.com'
TOOLS_PATTERN = r'asia-south1-docker\.pkg\.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:[0-9a-f]{64}'


class NativePg18:
    def run(self, tool, dsn, directory, arguments):
        if tool != 'pg_dump':
            raise ValueError('regional worker only exports; never restores')
        version = subprocess.run(['pg_dump', '--version'], capture_output=True, text=True, check=True).stdout
        if not re.search(r'PostgreSQL\) 18\.', version):
            raise ValueError('regional export requires PostgreSQL 18 tools')
        parsed = conninfo_to_dict(dsn)
        env = {**os.environ, 'PGHOST': parsed['host'], 'PGPORT': parsed.get('port', '5432'),
               'PGDATABASE': parsed['dbname'], 'PGUSER': parsed['user'], 'PGPASSWORD': parsed['password'],
               'PGCONNECT_TIMEOUT': '10'}
        actual = [a if a != '--file=/backup/snapshot.dump' else '--file=' + str(directory / 'snapshot.dump') for a in arguments]
        result = subprocess.run(['pg_dump', *actual], env=env, capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError('regional export failed; no credential-bearing error retained')
        return result.stdout


def md5_base64(path):
    digest = hashlib.md5(usedforsecurity=False)
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return base64.b64encode(digest.digest()).decode()


def upload_create_only(session, token, name, path):
    # Fixed Google endpoint and reviewed path; redirects/retries forbidden.
    with path.open('rb') as payload:
        response = session.post('https://storage.googleapis.com/upload/storage/v1/b/' + BUCKET + '/o',
                            params={'uploadType': 'media', 'name': name, 'ifGenerationMatch': '0'},
                            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/octet-stream',
                                     'Content-MD5': md5_base64(path)}, data=payload,
                            timeout=180, allow_redirects=False)
    if response.status_code not in (200, 201):
        raise RuntimeError('create-only private archive upload failed')
    metadata = response.json()
    if (metadata.get('bucket') != BUCKET or metadata.get('name') != name
            or str(metadata.get('generation', '')).isdigit() is False or int(metadata['generation']) <= 0
            or int(metadata.get('size', -1)) != path.stat().st_size or metadata.get('md5Hash') != md5_base64(path)):
        raise ValueError('uploaded immutable archive metadata differs')
    return {'uri': 'gs://' + BUCKET + '/' + name, 'generation': str(metadata['generation']),
            'size': path.stat().st_size, 'sha256': file_sha256(path), 'md5': metadata['md5Hash']}


def regional_export(manifest, environment, directory: Path, session):
    source = environment['MFDATAINDIA_DSN']
    parsed = conninfo_to_dict(source)
    if (parsed.get('host') != '/cloudsql/' + CONNECTION or parsed.get('dbname') != 'mfdataindia'
            or parsed.get('user') != 'mfdata_admin' or not parsed.get('password') or parsed.get('hostaddr')):
        raise ValueError('regional export source credential differs')
    directory.mkdir(mode=0o700)
    backup = fresh_backup(source, directory, NativePg18())
    with (directory / 'backup-evidence.json').open('x') as handle:
        json.dump(backup, handle, sort_keys=True)
    identity = session.get('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
                           headers={'Metadata-Flavor': 'Google'}, timeout=10, allow_redirects=False)
    if identity.status_code != 200 or not isinstance(identity.json().get('access_token'), str):
        raise ValueError('workload archive credentials unavailable')
    token = identity.json()['access_token']
    prefix = 'verified-review-rollouts/' + manifest.rollout_id + '/'
    dump = upload_create_only(session, token, prefix + 'snapshot.dump', directory / 'snapshot.dump')
    evidence = upload_create_only(session, token, prefix + 'backup-evidence.json', directory / 'backup-evidence.json')
    return seal_evidence(manifest, 'regional-backup', {'level': 'DEV_EXPORTED_MVCC_SNAPSHOT', 'source_writes': 0,
                                                      'dump': dump, 'baseline': evidence})


def archive_condition(manifest):
    prefix = 'projects/_/buckets/' + BUCKET + '/objects/verified-review-rollouts/' + manifest.rollout_id + '/'
    return "expression=resource.name.startsWith('" + prefix + "'),title=mf-review-backup-" + manifest.rollout_id


def cloud_backup(manifest, cloud, directory: Path, *, image):
    if not re.fullmatch(TOOLS_PATTERN, image):
        raise ValueError('regional backup needs exact approved tooling digest')
    condition = archive_condition(manifest)
    bucket = cloud.command('storage', 'buckets', 'describe', 'gs://' + BUCKET, '--project=' + PROJECT, '--format=json', json_output=True)
    if (bucket.get('name') != BUCKET or bucket.get('public_access_prevention') != 'enforced'
            or bucket.get('uniform_bucket_level_access') is not True):
        raise ValueError('regional archive bucket privacy not established')
    verify_image(cloud, image, manifest.git_sha)
    try:
        # Arm cleanup before possibly-applied grants; existing snapshot read IAM
        # and existing source-admin credential access remain unchanged.
        cloud.command('storage', 'buckets', 'add-iam-policy-binding', 'gs://' + BUCKET, '--project=' + PROJECT,
                      '--member=serviceAccount:' + BACKUP_SA, '--role=roles/storage.objectCreator',
                      '--condition=' + condition, '--quiet')
        execution, certificate = cloud.job(manifest, 'mf-review-backup-' + manifest.rollout_id, BACKUP_SA,
                                            ['scripts/backup_verified_review_dev.py'],
                                            {'DEPLOYMENT_GIT_SHA': manifest.git_sha, 'REVIEW_MANIFEST': manifest.model_dump_json()},
                                            ['MFDATAINDIA_DSN=tlws-mf-data-admin-dsn-dev:' + str(manifest.source_secret_version)],
                                            directory, image=image)
        result = verify_evidence(manifest, 'regional-backup', certificate)
        if result.get('level') != 'DEV_EXPORTED_MVCC_SNAPSHOT' or result.get('source_writes') != 0:
            raise ValueError('regional backup evidence incomplete')
        with (directory / '00-backup-execution.json').open('x') as handle:
            json.dump({'execution': execution, 'certificate': certificate, 'image': image}, handle, sort_keys=True, indent=2)
        for key, filename in (('dump', 'snapshot.dump'), ('baseline', 'backup-evidence.json')):
            item = result[key]
            expected = 'gs://' + BUCKET + '/verified-review-rollouts/' + manifest.rollout_id + '/' + filename
            if (item.get('uri') != expected or not re.fullmatch('[0-9]+', str(item.get('generation', '')))
                    or int(item['generation']) <= 0 or not re.fullmatch('[0-9a-f]{64}', item.get('sha256', ''))):
                raise ValueError('regional object target/generation/hash differs')
            path = directory / filename
            if path.exists():
                raise ValueError('never overwrite local backup/evidence')
            cloud.command('storage', 'cp', expected + '#' + item['generation'], str(path))
            if path.stat().st_size != item['size'] or file_sha256(path) != item['sha256']:
                raise ValueError('downloaded immutable backup bytes differ')
        backup = json.loads((directory / 'backup-evidence.json').read_text())
        if backup['backup_sha256'] != result['dump']['sha256'] or backup['backup_bytes'] != result['dump']['size']:
            raise ValueError('exported baseline does not bind downloaded backup')
        backup['object'] = result['dump']
        return backup
    finally:
        cloud.command('storage', 'buckets', 'remove-iam-policy-binding', 'gs://' + BUCKET, '--project=' + PROJECT,
                      '--member=serviceAccount:' + BACKUP_SA, '--role=roles/storage.objectCreator',
                      '--condition=' + condition, '--quiet')
        policy = cloud.command('storage', 'buckets', 'get-iam-policy', 'gs://' + BUCKET,
                               '--project=' + PROJECT, '--format=json', json_output=True)
        expected_expression = "resource.name.startsWith('projects/_/buckets/" + BUCKET + '/objects/verified-review-rollouts/' + manifest.rollout_id + "/')"
        if (not isinstance(policy, dict) or not isinstance(policy.get('bindings', []), list)
                or any(not isinstance(b, dict) or not isinstance(b.get('members', []), list)
                       or (b.get('role') == 'roles/storage.objectCreator'
                           and 'serviceAccount:' + BACKUP_SA in b.get('members', [])
                           and b.get('condition', {}).get('expression') == expected_expression)
                       for b in policy.get('bindings', []))):
            raise ValueError('temporary archive-create access removal unconfirmed')
