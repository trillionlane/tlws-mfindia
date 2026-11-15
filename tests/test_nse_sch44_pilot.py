import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

import pytest

from mfdataindia.nse import sch44_pilot as pilot

SOURCE = (Path(__file__).parent / 'fixtures/nse01/sch44_synthetic.psv').read_bytes()
REV = 'a' * 40
BUCKET = 'private-sch-fixture'


class Reply:
    def __init__(self, status, content):
        self.status_code, self.content = status, content

    def json(self):
        return self.content

    def iter_content(self, _):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Storage:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.public = False

    def get(self, url, *, params=None, **kwargs):
        assert kwargs['allow_redirects'] is False
        if url == pilot.METADATA_TOKEN:
            return Reply(200, {'access_token': 'fake-token'})
        if url.endswith('/b/' + BUCKET):
            return Reply(200, {'name': BUCKET, 'iamConfiguration': {
                'publicAccessPrevention': 'inherited' if self.public else 'enforced',
                'uniformBucketLevelAccess': {'enabled': True},
            }})
        name = unquote(url.rsplit('/o/', 1)[1])
        metadata, content = self.objects[name]
        if params:
            self.reads.append((name, params['generation']))
            assert params == {'alt': 'media', 'generation': metadata['generation']}
            return Reply(200, content)
        return Reply(200, metadata)

    def post(self, _url, *, params, headers, data, **kwargs):
        assert kwargs['allow_redirects'] is False
        assert params['ifGenerationMatch'] == '0'
        name = params['name']
        if name in self.objects:
            return Reply(412, {})
        checksum = base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
        assert headers['Content-MD5'] == checksum
        metadata = {'name': name, 'bucket': BUCKET, 'generation': str(len(self.objects) + 1),
                    'size': str(len(data)), 'md5Hash': checksum}
        self.objects[name] = (metadata, data)
        return Reply(200, metadata)


def fake_capture(data=SOURCE, *, failure=False, observed_at='2026-11-15T14:08:34.084Z', date=None):
    def capture(path):
        path.mkdir()
        receipt = {'format': 'nse01-sch-api-capture-v1', 'environment': 'UAT', 'method': 'POST',
                   'endpoint': '/nsemfdesk/api/v2/reports/MASTER_DOWNLOAD', 'file_type': 'SCH',
                   'report_type': 'CONSOLIDATED_SCHEME_MASTER',
                   'publication': 'NOT_ATTEMPTED', 'connector_revision': REV,
                   'http_status': 403 if failure else 200, 'observed_at_utc': observed_at,
                   'source_scope': 'UNVERIFIED', 'provider_transport_started': True,
                   'post_invoked': True}
        if failure:
            receipt['outcome'] = 'DOWNLOAD_FAILED'
        else:
            (path / 'source.txt').write_bytes(data)
            receipt.update(outcome='RETAINED_FOR_OFFLINE_VALIDATION' if date else 'RETAINED_EXPORT_DATE_UNVERIFIED',
                           header_columns=44, row_count=5,
                           filename='NSE_NSEINVEST_ALL_15112026.txt' if date else None,
                           export_date=date, sha256=hashlib.sha256(data).hexdigest(), byte_length=len(data))
        (path / 'receipt.json').write_text(json.dumps(receipt))
        return int(failure)
    return capture


def execute(archive, run, capture):
    return pilot.run_retrieval(archive, pilot.PILOT_JOB + '-' + run, REV, capture)


def payloads(storage):
    return {name: content for name, (_, content) in storage.objects.items()
            if name.startswith(pilot.PREFIX + 'payloads/')}


def stored_receipts(storage):
    return [json.loads(value) for name, (_, value) in storage.objects.items()
            if name.endswith('/receipt.json')]


def test_identical_undated_downloads_reuse_payload_and_keep_each_receipt():
    storage = Storage()
    archive = pilot.PrivateArchive(storage, BUCKET)
    first = pilot.run_retrieval(archive, pilot.PILOT_JOB + '-one', REV, fake_capture(),
                                publish=lambda *_: pytest.fail('undated must never reach publication'))
    assert first['publication'] == 'HELD'
    assert first['publication_reason'] == 'SCH44_EXPORT_DATE_UNVERIFIED'
    second = execute(archive, 'two', fake_capture(observed_at='2026-11-16T01:30:10Z'))
    assert first['result'] == second['result'] == 'CAPTURED'
    assert first['export_date'] is None and second['export_date'] is None
    assert first['retrieved_at_utc'] != second['retrieved_at_utc']
    assert first['sha256'] == second['sha256']
    assert first['payload']['name'] == second['payload']['name']
    assert first['payload']['generation'] == second['payload']['generation']
    assert first['payload']['reused'] is False and second['payload']['reused'] is True
    assert list(payloads(storage).values()) == [SOURCE]
    assert stored_receipts(storage) == [first, second]
    assert storage.reads == [(first['payload']['name'], first['payload']['generation'])]
    with pytest.raises(pilot.PilotError, match='ARCHIVE_CREATE_OR_READ_FAILED'):
        execute(archive, 'one', lambda _: pytest.fail('claimed execution must not contact NSE again'))


def test_changed_file_preserves_both_versions_even_with_same_export_date():
    storage = Storage()
    archive = pilot.PrivateArchive(storage, BUCKET)
    altered = SOURCE.replace(b'SYNTHETIC GROWTH', b'SYNTHETIC EQUITY')
    assert altered != SOURCE
    first = execute(archive, 'one', fake_capture(date='2026-11-15'))
    second = execute(archive, 'two', fake_capture(altered, date='2026-11-15'))
    assert first['export_date'] == second['export_date'] == '2026-11-15'
    assert first['result'] == second['result'] == 'CAPTURED'
    assert first['sha256'] != second['sha256']
    assert set(payloads(storage).values()) == {SOURCE, altered}
    assert len(stored_receipts(storage)) == 2


def test_failed_capture_and_bad_layout_leave_active_snapshot_untouched():
    storage = Storage()
    archive = pilot.PrivateArchive(storage, BUCKET)
    storage.objects['active/snapshot.json'] = ({}, b'{"active":"before"}')
    denied = execute(archive, 'denied', fake_capture(failure=True))
    assert denied['result'] == 'FAILED' and denied['http_status'] == 403
    assert not payloads(storage)
    malformed = SOURCE.replace(b'UNIQUE SR NO', b'UNREVIEWED')
    bad = execute(archive, 'bad', fake_capture(malformed))
    assert bad['result'] == 'FAILED' and bad['reason'] == 'SCH44_INVALID_HEADER'
    assert stored_receipts(storage) == [denied, bad]
    assert all(r['publication'] == 'NOT_ATTEMPTED' for r in (denied, bad))
    assert storage.objects['active/snapshot.json'][1] == b'{"active":"before"}'


def test_mismatched_existing_payload_is_not_overwritten():
    storage = Storage()
    archive = pilot.PrivateArchive(storage, BUCKET)
    first = execute(archive, 'one', fake_capture())
    name = first['payload']['name']
    metadata, content = storage.objects[name]
    corrupted = content[::-1]
    storage.objects[name] = (metadata, corrupted)
    second = execute(archive, 'two', fake_capture())
    assert second['result'] == 'FAILED'
    assert second['reason'] == 'ARCHIVE_OBJECT_DIFFERS'
    assert storage.objects[name][1] == corrupted


def test_schedule_contract_is_disabled_and_has_no_task_retry():
    plan = json.loads((Path(__file__).resolve().parents[1] / 'config/nse_sch44_uat_pilot.json').read_bytes())
    assert plan['enabled'] is False and plan['publication'] == 'UAT_APPROVED_FULL_SNAPSHOT_ONLY'
    assert plan['job']['name'] == pilot.PILOT_JOB and plan['job']['maxRetries'] == 0
    assert plan['schedule']['cron'] == '0 7 * * 1-5'
    assert plan['schedule']['timeZone'] == 'Asia/Kolkata' and plan['schedule']['retryCount'] == 0
    storage = Storage()
    storage.public = True
    with pytest.raises(pilot.PilotError, match='ARCHIVE_PRIVACY_UNVERIFIED'):
        pilot.PrivateArchive(storage, BUCKET)
