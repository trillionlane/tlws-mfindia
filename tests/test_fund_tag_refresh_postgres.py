"""Hosted-only native proof for the post-publication tag refresh hook."""
import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.tagging import refresh
from test_fund_tagging_postgres import tagging_db as tagging_db, writer
from test_ro01_reader_policy_postgres import connect, ro01_db as ro01_db, values

pytestmark = pytest.mark.postgres


def trigger(content='a'):
    return {
        'source_kind': 'daily-amfi-refresh',
        'source_content_hash': content * 64,
        'observed_on': '2026-11-11',
    }


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_separate_writer_publishes_once_then_verifies_unchanged(tagging_db):
    dsn = tagging_db
    baseline = values(dsn)

    def connector(value, **kwargs):
        assert value == 'postgresql://separate-tag-writer'
        assert kwargs['autocommit'] is True
        return writer(dsn)

    environment = {refresh.DSN_ENV: 'postgresql://separate-tag-writer'}
    first = refresh.after_success(trigger(), environ=environment, connector=connector)
    second = refresh.after_success(trigger(), environ=environment, connector=connector)
    assert first['state'] == 'PUBLISHED'
    assert second['state'] == 'UNCHANGED'
    assert first['attempt_id'] == second['attempt_id']
    assert first['tag_snapshot_id'] == second['tag_snapshot_id']
    assert first['source_ingestion_outcome'] == second['source_ingestion_outcome'] == 'PRESERVED'
    assert values(dsn) == baseline


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_wrong_principal_failure_isolated_without_tag_or_source_write(tagging_db):
    dsn = tagging_db
    baseline = values(dsn)

    def owner_connector(value, **kwargs):
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)

    result = refresh.after_success(
        trigger('b'),
        environ={refresh.DSN_ENV: 'postgresql://wrong-owner'},
        connector=owner_connector,
    )
    assert result['state'] == 'FAILED' and result['reason'] == 'TAG_REFRESH_FAILED'
    assert result['source_ingestion_outcome'] == 'PRESERVED'
    with connect(dsn) as owner:
        assert owner.execute('SELECT count(*) AS n FROM mf_tagging.snapshots').fetchone()['n'] == 0
    assert values(dsn) == baseline
