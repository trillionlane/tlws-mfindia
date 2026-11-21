from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from mfdataindia.nse import sch44_website as capture
from mfdataindia.nse.sch44_pilot import PilotError

SOURCE = (Path(__file__).parent / 'fixtures/nse01/sch44_synthetic.psv').read_bytes()
NOW = datetime(2026, 11, 17, 19, 31, tzinfo=timezone.utc)


class Archive:
    def __init__(self):
        self.objects = {}

    def create(self, key, body, *, deduplicate=False):
        if key in self.objects and not deduplicate:
            raise PilotError('ARCHIVE_CREATE_OR_READ_FAILED')
        if key in self.objects:
            assert self.objects[key][0] == body
            return {'name': key, 'generation': self.objects[key][1], 'reused': True}
        generation = str(len(self.objects) + 1)
        self.objects[key] = (body, generation)
        return {'name': key, 'generation': generation, 'reused': False}


def test_previous_date_and_identical_bytes_have_distinct_receipts():
    archive = Archive()
    def run(execution, day):
        return capture.run_capture(archive, capture.JOB + '-' + execution,
                                   lambda: (f'NSE_NSEINVEST_ALL_{day}112026.txt', SOURCE),
                                   lambda: NOW)
    first, second = run('clock1', 17), run('clock2', 18)
    assert first['result'] == second['result'] == 'CAPTURED'
    assert (first['generation_date'], first['freshness']) == ('2026-11-17', 'PRIOR_DATE')
    assert (second['generation_date'], second['freshness']) == ('2026-11-18', 'SAME_IST_DATE')
    assert first['retrieved_at_utc'] == NOW.isoformat()
    assert first['publication'] == 'NOT_ATTEMPTED'
    assert first['effective_business_date'] is None and first['source_scope'] == 'UNVERIFIED'
    assert first['row_count'] == 5 and first['sha256'] == sha256(SOURCE).hexdigest()
    assert second['payload']['reused'] and first['payload']['generation'] == second['payload']['generation']
    assert len(archive.objects) == 5


@pytest.mark.parametrize(('filename', 'payload', 'reason'), [
    ('latest.txt', SOURCE, 'NSE_FILENAME_UNRECOGNIZED'),
    ('NSE_NSEINVEST_ALL_31112026.txt', SOURCE, 'NSE_GENERATION_DATE_INVALID'),
    ('NSE_NSEINVEST_ALL_19112026.txt', SOURCE, 'NSE_GENERATION_DATE_IN_FUTURE'),
    ('NSE_NSEINVEST_ALL_18112026.txt', b'error', 'SCH44_INVALID_HEADER'),
    ('NSE_NSEINVEST_ALL_18112026.txt', SOURCE + b'bad\n', 'INVALID_COLUMN_COUNT'),
])
def test_rejects_bad_input_with_terminal_receipt(filename, payload, reason):
    archive = Archive()
    receipt = capture.run_capture(archive, capture.JOB + '-invalid', lambda: (filename, payload), lambda: NOW)
    assert receipt['result'] == 'FAILED' and receipt['reason'] == reason
    assert len(archive.objects) == 2
    assert json.loads(archive.objects['retrievals/' + capture.JOB + '-invalid/receipt.json'][0]) == receipt


def test_transport_failure_and_reexecution_do_not_resubmit():
    archive = Archive()
    calls = []
    def unavailable():
        calls.append(1)
        raise PilotError('NSE_FORM_UNAVAILABLE')
    execution = capture.JOB + '-unavailable'
    assert capture.run_capture(archive, execution, unavailable)['reason'] == 'NSE_FORM_UNAVAILABLE'
    with pytest.raises(PilotError, match='ARCHIVE_CREATE_OR_READ_FAILED'):
        capture.run_capture(archive, execution, unavailable)
    assert len(calls) == 1


def test_daily_schedule_including_weekends_targets_only_website_capture():
    config = json.loads(Path('config/nse_sch44_production_website.json').read_bytes())
    assert config['job']['name'] == capture.JOB
    assert config['job']['maxRetries'] == config['schedule']['retryCount'] == 0
    assert config['schedule']['cron'] == '0 1 * * *'
    assert config['schedule']['timeZone'] == 'Asia/Kolkata'
    assert config['schedule']['target'].endswith('/jobs/' + capture.JOB + ':run')
    assert config['archive_prefix'] == capture.PRODUCTION_WEB_PREFIX
    assert config['publication'] == 'NOT_ATTEMPTED'


def test_issuer_form_post_cannot_follow_redirect():
    from types import SimpleNamespace
    outcomes = []
    class Route:
        request = SimpleNamespace(url=capture.URL, method='POST')

        def fetch(self, **kwargs):
            assert kwargs == {'max_redirects': 0, 'timeout': 30000}
            return SimpleNamespace(status=307)

        def abort(self):
            outcomes.append('ABORTED')

    capture._route_issuer_request(Route())
    assert outcomes == ['ABORTED']
    assert not capture._issuer_origin('http://www.nseinvest.com/nsemfdesk/schememaster.htm')
    assert not capture._issuer_origin('https://www.nseinvest.com.example.test/')
