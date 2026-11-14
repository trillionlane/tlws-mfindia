"""Hosted-only native proof for the custom values read boundary."""

from time import perf_counter

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
import psycopg
import pytest

from mfdataindia.datasets import enrollment
from mfdataindia.tagging import (
    exposure_reads,
    reads as tag_reads,
    store as tag_store,
    values_app,
    values_reads,
)
from test_fund_exposure_postgres import adopt
from test_fund_tagging_postgres import tagging_db as tagging_db, writer
from test_ro01_reader_policy_postgres import (
    PASSWORD,
    connect,
    generator_capabilities,
    ro01_db as ro01_db,
    values,
)

pytestmark = pytest.mark.postgres


def enroll(dsn):
    with connect(dsn, autocommit=False) as owner:
        enrollment.enroll(
            owner,
            "mf_portfolios",
            "snapshots",
            classification="fund_research",
            title="Accepted dated complete portfolios",
        )


def client(dsn, role="mfdata_verified_reader"):
    return TestClient(values_app.create_app(make_conninfo(dsn, user=role, password=PASSWORD)))


@pytest.mark.parametrize("ro01_db", ["generator"], indirect=True)
def test_native_values_boundary_is_read_only_and_claims_no_live_positive_assignments(
    ro01_db, capsys
):
    dsn = ro01_db
    before_capabilities = generator_capabilities(dsn)
    adopt(dsn)
    enroll(dsn)
    baseline = values(dsn)
    started = perf_counter()

    with client(dsn) as api:
        discovery = api.get(values_reads.PREFIX)
        assert discovery.status_code == 200
        assert discovery.json()["activity_mapping_records"] == 0
        assert discovery.json()["positive_live_assignments_claimed"] is False
        assert discovery.json()["certification_claimed"] is False

        summary = api.get(values_reads.PREFIX + "/funds/1")
        assert summary.status_code == 200
        body = summary.json()
        assert body["exposure_state"] == "available"
        assert body["portfolio_as_of"] == "2026-08-31"
        assert {item["state"] for item in body["assessments"]} == {"not-assessed"}
        assert all(
            item["reasons"] == ["SCREEN_NOT_ACTIVE_AT_PORTFOLIO_DATE"]
            for item in body["assessments"]
        )
        assert body["positive_live_assignments_claimed"] is False

        # The composed app keeps the prior tag and exposure surfaces unchanged.
        assert api.get("/api/funds/1").status_code == 200
        assert api.get(exposure_reads.PREFIX + "/funds/1").status_code == 200
        for method in ("post", "put", "patch", "delete"):
            assert getattr(api, method)(values_reads.PREFIX + "/funds/1").status_code == 405

    assert generator_capabilities(dsn) == before_capabilities
    assert values(dsn) == baseline
    with connect(dsn, "mfdata_verified_reader") as reader:
        for statement in (
            "DELETE FROM mf_portfolios.snapshots",
            "UPDATE mf.funds SET scheme_name='denied'",
            "CREATE TABLE mf_portfolios.values_denied(id int)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(statement)

    print(
        {
            "native_values_reads_seconds": round(perf_counter() - started, 3),
            "source_rows_changed": False,
            "generator_capabilities_changed": False,
            "positive_live_assignments_claimed": False,
        }
    )
    assert "native_values_reads_seconds" in capsys.readouterr().out


@pytest.mark.parametrize("ro01_db", ["generator"], indirect=True)
def test_native_values_boundary_fails_closed_without_dataset_enrollment(ro01_db):
    dsn = ro01_db
    adopt(dsn)
    with client(dsn) as api:
        discovery = api.get(values_reads.PREFIX)
        summary = api.get(values_reads.PREFIX + "/funds/1")
        assert discovery.status_code == summary.status_code == 503
        assert discovery.json()["detail"] == summary.json()["detail"] == "DATASET_NOT_FOUND"


@pytest.mark.parametrize("ro01_db", ["generator"], indirect=True)
def test_packaged_values_factory_reads_tags_exposure_and_values_from_one_reader(tagging_db):
    dsn = tagging_db
    enroll(dsn)
    with writer(dsn) as conn:
        published = tag_store.publish(conn)

    with client(dsn) as api:
        tags = api.get(tag_reads.PREFIX + "/funds/1")
        exposure = api.get(exposure_reads.PREFIX + "/funds/1")
        screened = api.get(values_reads.PREFIX + "/funds/1")

    assert tags.status_code == exposure.status_code == screened.status_code == 200
    assert tags.json()["snapshot_id"] == published["snapshot_id"]
    assert tags.json()["funds"][0]["code"] == 1
    assert exposure.json()["portfolio_as_of"] == "2026-08-31"
    assert screened.json()["portfolio_as_of"] == "2026-08-31"
    assert screened.json()["positive_live_assignments_claimed"] is False


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_packaged_values_factory_keeps_factual_tags_available_without_portfolios(tagging_db):
    dsn = tagging_db
    with writer(dsn) as conn:
        published = tag_store.publish(conn)

    with client(dsn) as api:
        tags = api.get(tag_reads.PREFIX + "/funds/1")
        exposure = api.get(exposure_reads.PREFIX + "/funds/1")
        screened = api.get(values_reads.PREFIX + "/funds/1")

    assert tags.status_code == 200
    assert tags.json()["snapshot_id"] == published["snapshot_id"]
    assert tags.json()["funds"][0]["code"] == 1
    assert exposure.status_code == screened.status_code == 503
    assert exposure.json()["detail"] == screened.json()["detail"] == "DATASET_NOT_FOUND"
