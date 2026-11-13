"""Automatic tag refresh is idempotent, separate, and failure-isolated."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from mfdataindia.tagging import policy, refresh
from test_fund_tagging import row

ROOT = Path(__file__).resolve().parents[1]


def trigger():
    return {
        'source_kind': 'daily-amfi-refresh',
        'source_content_hash': 'a' * 64,
        'observed_on': '2026-11-11',
    }


def test_unconfigured_refresh_is_explicit_and_never_connects():
    def forbidden(*args, **kwargs):
        raise AssertionError('unconfigured refresh attempted a connection')

    result = refresh.after_success(trigger(), environ={}, connector=forbidden)
    assert result['state'] == 'NOT_CONFIGURED'
    assert result['source_ingestion_outcome'] == 'PRESERVED'
    assert result['retry_performed'] is False
    assert result['consumer_acceptance'] == 'NOT_OBSERVED'
    assert result['attempt_id'] and result['tag_snapshot_id'] is None


def test_identical_inputs_are_one_attempt_and_report_unchanged_without_retry():
    connections = []

    class Connection:
        def __enter__(self):
            connections.append(self)
            return self

        def __exit__(self, *args):
            return False

    def connect(dsn, **kwargs):
        assert dsn == 'postgresql://separate-tag-writer'
        assert kwargs['autocommit'] is True
        assert kwargs['connect_timeout'] == 5
        return Connection()

    def published(conn):
        return {'state': 'UNCHANGED', 'snapshot_id': 'b' * 64}
    first = refresh.after_success(
        trigger(), environ={refresh.DSN_ENV: 'postgresql://separate-tag-writer'},
        connector=connect, publisher=published,
    )
    second = refresh.after_success(
        trigger(), environ={refresh.DSN_ENV: 'postgresql://separate-tag-writer'},
        connector=connect, publisher=published,
    )
    assert first == second
    assert first['state'] == 'UNCHANGED' and first['tag_snapshot_id'] == 'b' * 64
    assert len(connections) == 2  # one caller invocation each; no internal retry


def test_revised_source_and_changed_rules_have_traceable_attempt_identities(monkeypatch):
    original = refresh.after_success(trigger(), environ={})
    revised = refresh.after_success({**trigger(), 'source_content_hash': 'c' * 64}, environ={})
    assert revised['attempt_id'] != original['attempt_id']

    evidence = refresh.policy_evidence()
    monkeypatch.setattr(refresh, 'policy_evidence',
                        lambda: {**evidence, 'factual_policy_hash': 'd' * 64})
    changed_rule = refresh.after_success(trigger(), environ={})
    assert changed_rule['attempt_id'] != original['attempt_id']


def test_failure_is_value_safe_and_cannot_reverse_source_success():
    def fail(*args, **kwargs):
        raise RuntimeError('postgresql://secret-host/password=secret')

    result = refresh.after_success(
        trigger(), environ={refresh.DSN_ENV: 'postgresql://secret-writer'},
        connector=fail,
    )
    assert result['state'] == 'FAILED'
    assert result['reason'] == 'TAG_REFRESH_FAILED'
    assert result['source_ingestion_outcome'] == 'PRESERVED'
    assert 'secret' not in repr(result)


def test_new_fund_enters_complete_census_with_explicit_unassessed_dimensions():
    candidate = policy.prepare([row(code=1), row(code=2)])
    new = [value for value in candidate['assessments'] if value['code'] == 2]
    assert len(new) == len(policy.DIMENSIONS)
    assert {value['dimension'] for value in new} == set(policy.DIMENSIONS)
    assert all(value['state'] == 'not-assessed' for value in new
               if value['dimension'] in policy.RESERVED)
    assert all(value['state'] in {'supported', 'insufficient-evidence', 'not-assessed'}
               for value in new
               if value['dimension'] in policy.FIELDS)


def wrapper():
    path = ROOT / 'scripts/refresh_fund_tags_after_success.py'
    spec = importlib.util.spec_from_file_location('refresh_fund_tags_after_success', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wrapper_attempts_tags_only_after_exact_daily_success_receipt():
    module = wrapper()
    order = []
    source = {
        'status': 'DAILY_REFRESH_OK', 'as_of': '2026-11-11',
        'fetch': {'content_sha256': 'e' * 64},
    }

    def runner(*args, **kwargs):
        order.append('source-completed')
        return SimpleNamespace(returncode=0, stdout=json.dumps(source), stderr='source-log\n')

    def refresher(value):
        order.append('tag-attempted')
        assert value == {
            'source_kind': 'daily-amfi-refresh',
            'source_content_hash': 'e' * 64,
            'observed_on': '2026-11-11',
        }
        return {'state': 'UNCHANGED'}

    code, stdout, stderr = module.execute(
        'daily-amfi-refresh', [], runner=runner, refresher=refresher
    )
    assert code == 0 and order == ['source-completed', 'tag-attempted']
    assert json.loads(stdout)['tag_refresh'] == {'state': 'UNCHANGED'}
    assert stderr == 'source-log\n'


def test_wrapper_never_attempts_tags_after_source_failure():
    module = wrapper()

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=7, stdout='source-output', stderr='source-failed')

    def forbidden(*args, **kwargs):
        raise AssertionError('tag refresh ran after failed source')

    assert module.execute(
        'daily-amfi-refresh', [], runner=runner, refresher=forbidden
    ) == (7, 'source-output', 'source-failed')


def test_wrapper_preserves_zero_exit_but_skips_tags_for_invalid_daily_receipt():
    module = wrapper()

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='not-a-source-receipt', stderr='')

    def forbidden(*args, **kwargs):
        raise AssertionError('tag refresh ran without a valid source receipt')

    code, stdout, stderr = module.execute(
        'daily-amfi-refresh', [], runner=runner, refresher=forbidden
    )
    result = json.loads(stdout)
    assert code == 0 and stderr == ''
    assert result['status'] == 'SOURCE_SUCCESS_RECEIPT_INVALID'
    assert result['tag_refresh'] == {'state': 'NOT_REQUESTED_INVALID_SOURCE_RECEIPT'}


def test_family_wrapper_skips_dry_run_and_uses_success_receipt_hash():
    module = wrapper()

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='FUND_FAMILY REPORT: {}\n', stderr='')

    def forbidden(*args, **kwargs):
        raise AssertionError('tag refresh ran during dry run')

    dry = module.execute('fund-family-build', ['--dry-run'], runner=runner,
                         refresher=forbidden)
    assert json.loads(dry[1])['tag_refresh'] == {'state': 'NOT_REQUESTED_DRY_RUN'}

    observed = []
    built = module.execute(
        'fund-family-build', [], runner=runner,
        refresher=lambda value: observed.append(value) or {'state': 'PUBLISHED'},
        now=lambda: datetime(2026, 11, 11, tzinfo=timezone.utc),
    )
    assert built[0] == 0 and json.loads(built[1])['tag_refresh']['state'] == 'PUBLISHED'
    assert observed[0]['source_kind'] == 'fund-family-build'
    assert observed[0]['observed_on'] == '2026-11-11'
