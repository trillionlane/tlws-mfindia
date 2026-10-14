"""Immutable upload/download pins and regional backup access cleanup."""
import hashlib
import json

import pytest

from mfdataindia.review.regional_backup import BUCKET, BACKUP_SA, cloud_backup, md5_base64, upload_create_only
from mfdataindia.review.rollout import seal_evidence
from test_verified_review_rollout import manifest

IMAGE = 'asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:' + 'd' * 64


class Response:
    status_code = 200
    def __init__(self, data):
        self.data = data
    def json(self):
        return self.data


def test_create_only_upload_checks_bytes_and_never_follows_redirects(tmp_path):
    path = tmp_path / 'fixture.dump'
    path.write_bytes(b'isolated-backup-fixture')
    class Session:
        def post(self, url, **kwargs):
            assert url == 'https://storage.googleapis.com/upload/storage/v1/b/' + BUCKET + '/o'
            assert kwargs['params']['ifGenerationMatch'] == '0'
            assert kwargs['allow_redirects'] is False
            assert kwargs['headers']['Content-MD5'] == md5_base64(path)
            assert kwargs['data'].read() == path.read_bytes()
            return Response({'bucket': BUCKET, 'name': 'approved/fixture.dump', 'generation': '12',
                             'size': str(path.stat().st_size), 'md5Hash': md5_base64(path)})
    result = upload_create_only(Session(), 'fixture-token-only', 'approved/fixture.dump', path)
    assert result['generation'] == '12'
    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('status', [302, 403, 412, 500])
def test_failed_upload_is_terminal_without_retry_or_delete(tmp_path, status):
    path = tmp_path / 'fixture.dump'
    path.write_bytes(b'fixture')
    class Session:
        calls = 0
        def post(self, *args, **kwargs):
            self.calls += 1
            result = Response({})
            result.status_code = status
            return result
    session = Session()
    with pytest.raises(RuntimeError):
        upload_create_only(session, 'fixture-token-only', 'approved/fixture.dump', path)
    assert session.calls == 1 and path.read_bytes() == b'fixture'


class Cloud:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure
        self.data = {}
    def command(self, *args, **kwargs):
        self.calls.append(args)
        if args[:3] == ('storage', 'buckets', 'describe'):
            return {'name': BUCKET, 'public_access_prevention': 'enforced', 'uniform_bucket_level_access': True}
        if args[:3] == ('storage', 'buckets', 'add-iam-policy-binding') and self.failure == 'grant':
            raise RuntimeError('grant may have been applied')
        if args[:3] == ('storage', 'buckets', 'get-iam-policy'):
            return {'bindings': []}
        if args[:2] == ('storage', 'cp'):
            from pathlib import Path
            Path(args[3]).write_bytes(self.data[args[2]])
        return ''
    def job(self, item, name, account, arguments, environment, secrets, directory, *, image):
        if self.failure == 'job':
            raise RuntimeError('job observation failed')
        assert account == BACKUP_SA and image == IMAGE
        assert arguments == ['scripts/backup_verified_review_dev.py']
        assert secrets == ['MFDATAINDIA_DSN=tlws-mf-data-admin-dsn-dev:1']
        dump = b'fixture-restorable-backup'
        backup = json.dumps({'backup_sha256': hashlib.sha256(dump).hexdigest(), 'backup_bytes': len(dump),
                             'baseline': {'fixture': True}, 'roles': ['mfdata_admin']}).encode()
        result = {'level': 'DEV_EXPORTED_MVCC_SNAPSHOT', 'source_writes': 0}
        for key, filename, content in [('dump', 'snapshot.dump', dump), ('baseline', 'backup-evidence.json', backup)]:
            uri = 'gs://' + BUCKET + '/verified-review-rollouts/' + item.rollout_id + '/' + filename
            result[key] = {'uri': uri, 'generation': '15', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
            self.data[uri + '#15'] = content if self.failure != 'bytes' else content + b'corruption'
        return 'actual-execution', seal_evidence(item, 'regional-backup', result)


def patch_images(monkeypatch):
    monkeypatch.setattr('mfdataindia.review.regional_backup.verify_image', lambda *args, **kwargs: {'layers_downloaded': 0})


def test_controller_verifies_immutable_downloads_and_retains_execution_receipt(monkeypatch, tmp_path):
    patch_images(monkeypatch)
    cloud = Cloud()
    backup = cloud_backup(manifest(), cloud, tmp_path, image=IMAGE)
    assert backup['object']['generation'] == '15'
    assert (tmp_path / 'snapshot.dump').read_bytes() == b'fixture-restorable-backup'
    assert (tmp_path / '00-backup-execution.json').is_file()
    assert len([a for a in cloud.calls if a[:3] == ('storage', 'buckets', 'remove-iam-policy-binding')]) == 1
    assert not any('delete' in a for a in cloud.calls)


@pytest.mark.parametrize('failure', ['grant', 'job', 'bytes'])
def test_cleanup_is_armed_before_possible_grant_failure(monkeypatch, tmp_path, failure):
    patch_images(monkeypatch)
    cloud = Cloud(failure)
    with pytest.raises((RuntimeError, ValueError)):
        cloud_backup(manifest(), cloud, tmp_path, image=IMAGE)
    assert len([a for a in cloud.calls if a[:3] == ('storage', 'buckets', 'remove-iam-policy-binding')]) == 1


def test_unapproved_image_causes_no_cloud_calls(tmp_path):
    cloud = Cloud()
    with pytest.raises(ValueError):
        cloud_backup(manifest(), cloud, tmp_path, image='https://other.invalid,KEY=value')
    assert cloud.calls == []
