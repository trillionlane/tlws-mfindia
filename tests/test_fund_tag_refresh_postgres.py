"""Hosted-only native proof for independently attested factual-tag refresh."""

from dataclasses import replace
from datetime import datetime, timezone
import importlib
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.ingest.amfi_client import FetchResult
from mfdataindia.ingest.amfi_navall import ParseReport
from mfdataindia.store.catalogue_lock import catalogue_publication_lock
from mfdataindia.tagging import refresh, source_evidence
from test_fund_tagging_postgres import (
    append_source_receipt,
    tagging_db as tagging_db,
    writer,
)
from test_ro01_reader_policy_postgres import connect, ro01_db as ro01_db, values
from test_nav_family_continuity import DAY, scheme

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_legacy_full_waits_for_new_full_v2_receipt_then_publishes_once(
    tagging_db, monkeypatch, capsys
):
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

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    daily = importlib.import_module("refresh_daily_v2")
    schemes = [
        scheme(1, "Fund 1", None, amc="Verified fixture"),
        replace(
            scheme(2, "Fund 2", None, amc="Verified fixture"),
            option="IDCW",
        ),
        scheme(3, "New Fund 3", "INFTEST00003", amc="Verified fixture"),
    ]
    fetch = FetchResult(
        "https://portal.amfiindia.com/spages/NAVAll.txt",
        200,
        "fixture",
        "f" * 64,
        1,
        datetime(2026, 11, 14, 12, tzinfo=timezone.utc),
        0,
        "FIXTURE",
    )
    monkeypatch.setattr(daily, "resolve_dsn", lambda *args, **kwargs: dsn)
    monkeypatch.setattr(
        daily,
        "AmfiClient",
        lambda *args, **kwargs: type("Client", (), {"fetch_navall": lambda self: fetch})(),
    )
    monkeypatch.setattr(
        daily,
        "parse_navall",
        lambda text: (
            schemes,
            ParseReport(data_rows=3, distinct_amcs=1, distinct_categories=1),
        ),
    )
    monkeypatch.setattr(daily, "validate_feed", lambda *args, **kwargs: DAY)
    monkeypatch.setattr(
        daily,
        "validate_full_snapshot_completeness",
        lambda *args, **kwargs: {"fixture": "actual-v2-publication"},
    )
    assert daily.main([]) == 0
    capsys.readouterr()
    with connect(dsn) as owner:
        receipt = source_evidence.latest(owner)
        assert receipt["source_kind"] == "full-catalogue"
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
