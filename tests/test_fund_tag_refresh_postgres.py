"""Hosted-only native proof for independently attested factual-tag refresh."""

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.store.catalogue_lock import catalogue_publication_lock
from mfdataindia.tagging import refresh
from test_fund_tagging_postgres import (
    append_source_receipt,
    tagging_db as tagging_db,
    writer,
)
from test_ro01_reader_policy_postgres import connect, ro01_db as ro01_db, values

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_legacy_full_waits_for_first_receipt_then_publishes_once(tagging_db):
    dsn = tagging_db
    baseline = values(dsn)

    def connector(value, **kwargs):
        assert value == "postgresql://separate-tag-writer"
        assert kwargs["autocommit"] is True
        return writer(dsn)

    environment = {refresh.DSN_ENV: "postgresql://separate-tag-writer"}
    with connect(dsn, autocommit=False) as owner:
        # The existing FULL snapshot is deliberately legacy: no synthetic
        # backfill receipt may authorize a factual publication.
        owner.execute("DELETE FROM mf_identity.tag_input_publications")
    unavailable = refresh.publish_verified(environ=environment, connector=connector)
    assert unavailable["state"] == "SOURCE_RECEIPT_UNAVAILABLE"
    with connect(dsn) as owner:
        assert owner.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 0

    with connect(dsn, autocommit=False) as owner:
        receipt = append_source_receipt(owner)
    first = refresh.publish_verified(environ=environment, connector=connector)
    second = refresh.publish_verified(environ=environment, connector=connector)
    assert first["state"] == "PUBLISHED"
    assert second["state"] == "UNCHANGED"
    assert first["attempt_id"] == second["attempt_id"]
    assert first["tag_snapshot_id"] == second["tag_snapshot_id"]
    assert first["source_publication_id"] == receipt["publication_id"]
    assert first["source_publication_sequence"] == receipt["publication_sequence"]
    assert first["source_ingestion_outcome"] == second["source_ingestion_outcome"] == "PRESERVED"
    assert values(dsn) == baseline


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_wrong_principal_failure_isolated_without_tag_or_source_write(tagging_db):
    dsn = tagging_db
    baseline = values(dsn)

    def owner_connector(value, **kwargs):
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)

    result = refresh.publish_verified(
        environ={refresh.DSN_ENV: "postgresql://wrong-owner"},
        connector=owner_connector,
    )
    assert result["state"] == "FAILED" and result["reason"] == "TAG_REFRESH_FAILED"
    assert result["source_ingestion_outcome"] == "PRESERVED"
    with connect(dsn) as owner:
        assert owner.execute("SELECT count(*) AS n FROM mf_tagging.snapshots").fetchone()["n"] == 0
    assert values(dsn) == baseline


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_catalogue_session_lock_precedes_snapshot_and_busy_writes_nothing(tagging_db):
    dsn = tagging_db

    def connector(value, **kwargs):
        assert value == "postgresql://separate-tag-writer"
        return writer(dsn)

    environment = {refresh.DSN_ENV: "postgresql://separate-tag-writer"}
    with connect(dsn, autocommit=True) as owner:
        with catalogue_publication_lock(owner, wait=True):
            with owner.transaction():
                owner.execute(
                    "UPDATE mf.funds SET scheme_name=scheme_name || ' Revised' "
                    "WHERE amfi_scheme_code=1"
                )
                receipt = append_source_receipt(owner)
                busy = refresh.publish_verified(environ=environment, connector=connector)
                assert busy["state"] == "SOURCE_BUSY"
                with connect(dsn) as observer:
                    assert (
                        observer.execute(
                            "SELECT count(*) AS n FROM mf_tagging.snapshots"
                        ).fetchone()["n"]
                        == 0
                    )

    published = refresh.publish_verified(environ=environment, connector=connector)
    assert published["state"] == "PUBLISHED"
    assert published["source_publication_id"] == receipt["publication_id"]
    assert published["source_publication_sequence"] == receipt["publication_sequence"]
