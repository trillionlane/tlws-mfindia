"""Hosted disposable PostgreSQL and real semantic factory; no live/IAM proof."""

from time import perf_counter

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.datasets import catalogue
from mfdataindia.tagging import app, policy as p, reads, store
from test_fund_tagging_postgres import (
    append_source_receipt,
    tagging_db as tagging_db,
    writer,
)
from test_ro01_reader_policy_postgres import (
    ro01_db as ro01_db,
    connect,
    PASSWORD,
    generator_capabilities,
)

pytestmark = pytest.mark.postgres


def client(dsn, role=p.READER):
    return TestClient(app.create_app(make_conninfo(dsn, user=role, password=PASSWORD)))


def publish(dsn):
    with writer(dsn) as conn:
        return store.publish(conn)


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_semantic_discovery_definitions_filters_batch_and_existing_core(tagging_db):
    before = generator_capabilities(tagging_db)
    report = publish(tagging_db)
    with client(tagging_db) as api:
        root = api.get(reads.PREFIX)
        assert root.status_code == 200 and root.json()["snapshot_id"] == report["snapshot_id"]
        assert root.json()["ethical_screening_enabled"] is False
        assert root.json()["published_funds"] == 2
        assert sum(root.json()["assessment_counts"].values()) == 2 * len(p.DIMENSIONS)
        assert set(root.json()["dimensions"]) == set(p.DIMENSIONS)
        first = api.get(
            reads.PREFIX + "/definitions", params={"dimension": "option", "limit": 1}
        ).json()
        assert len(first["definitions"]) == 1 and first["next_cursor"]
        second = api.get(
            reads.PREFIX + "/definitions",
            params={"dimension": "option", "limit": 1, "cursor": first["next_cursor"]},
        ).json()
        assert second["next_cursor"] is None
        definitions = first["definitions"] + second["definitions"]
        assert {d["value"] for d in definitions} == {"GROWTH", "IDCW"}
        tags = [d["tag_id"] for d in definitions]
        all_result = api.get(
            reads.PREFIX + "/funds", params=[("tag", t) for t in tags] + [("match", "all")]
        )
        any_result = api.get(
            reads.PREFIX + "/funds", params=[("tag", t) for t in tags] + [("match", "any")]
        )
        assert all_result.status_code == any_result.status_code == 200
        assert all_result.json()["funds"] == []
        assert [f["code"] for f in any_result.json()["funds"]] == [1, 2]
        assert api.get(reads.PREFIX + "/funds", params={"tag": "0" * 64}).json()["funds"] == []
        details = api.get(reads.PREFIX + "/batch?codes=2,999,1")
        assert details.status_code == 200
        funds = details.json()["funds"]
        assert [f["code"] for f in funds] == [2, 999, 1]
        assert funds[1] == {"code": 999, "state": "not-found", "assessments": []}
        assert len(funds[0]["assessments"]) == len(p.DIMENSIONS)
        assert all(
            a["state"] == "not-assessed" and a["value"] is None
            for a in funds[0]["assessments"]
            if a["dimension"] in p.RESERVED
        )
        assert api.get(reads.PREFIX + "/funds/1").json()["funds"][0]["code"] == 1
        assert api.get("/api/funds/1").status_code == 200
        assert api.get("/api/datasets/v1").status_code == 200
        for verb in ("post", "put", "delete", "patch"):
            assert getattr(api, verb)(reads.PREFIX + "/funds").status_code == 405
    assert generator_capabilities(tagging_db) == before


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_keyset_pages_bind_query_and_real_republication(tagging_db):
    publish(tagging_db)
    with client(tagging_db) as api:
        first = api.get(reads.PREFIX + "/funds?limit=1").json()
        token = first["next_cursor"]
        assert first["funds"][0]["code"] == 1 and token
        second = api.get(reads.PREFIX + "/funds", params={"limit": 1, "cursor": token})
        assert second.status_code == 200
        assert second.json()["funds"][0]["code"] == 2 and second.json()["next_cursor"] is None
        wrong = api.get(reads.PREFIX + "/funds", params={"limit": 2, "cursor": token})
        assert wrong.status_code == 422 and wrong.json()["detail"] == "INVALID_TAG_CURSOR"
        with connect(tagging_db, autocommit=False) as owner:
            owner.execute("UPDATE mf.funds SET last_seen_in_source='2026-11-01'")
            append_source_receipt(owner)
        publish(tagging_db)
        stale = api.get(reads.PREFIX + "/funds", params={"limit": 1, "cursor": token})
        assert stale.status_code == 409 and stale.json() == {"detail": "TAG_SNAPSHOT_CHANGED"}
        assert (
            api.get(reads.PREFIX + "/funds?limit=1").json()["snapshot_id"] != first["snapshot_id"]
        )


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_unpublished_missing_and_unsafe_grants_cannot_be_ready(tagging_db):
    with client(tagging_db) as api:
        assert api.get(reads.PREFIX).json() == {"detail": "TAGGING_NOT_PUBLISHED"}
    publish(tagging_db)
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute("GRANT UPDATE ON mf_tagging.current TO mfdata_verified_reader")
    with client(tagging_db) as api:
        response = api.get(reads.PREFIX + "/batch?codes=1")
        assert (
            response.status_code == 503 and response.json()["detail"] == "UNSAFE_DATASET_CAPABILITY"
        )
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute("REVOKE UPDATE,SELECT ON mf_tagging.current FROM mfdata_verified_reader")
    with client(tagging_db) as api:
        assert api.get(reads.PREFIX).status_code == 503
        response = api.get(reads.PREFIX + "/funds/1")
        assert (
            response.status_code == 503
            and response.json()["detail"] == "DATASET_READ_GRANTS_MISSING"
        )


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_generator_cannot_self_select_reader_and_reader_cannot_write(tagging_db):
    publish(tagging_db)
    with client(tagging_db, "mfdata_verified_generator") as api:
        response = api.get(
            reads.PREFIX, headers={"X-Reader-Profile": "broad", "X-Database-Role": p.READER}
        )
        assert (
            response.status_code == 503
            and response.json()["detail"] == "DATASET_READER_IDENTITY_UNSAFE"
        )
    with client(tagging_db) as api:
        assert api.get(reads.PREFIX + "?profile=broad").status_code == 422
    with connect(tagging_db, p.READER) as conn:
        import psycopg

        for statement in (
            "DELETE FROM mf_tagging.current",
            "UPDATE mf.funds SET scheme_name=scheme_name",
            "CREATE TABLE mf_tagging.denied(id int)",
            "ALTER ROLE mfdata_verified_reader CREATEDB",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_batch_security_work_is_constant_not_per_fund(
    tagging_db, monkeypatch, record_property
):
    publish(tagging_db)
    original = catalogue.verify_reader
    calls = []

    def counted(conn):
        calls.append(1)
        return original(conn)

    monkeypatch.setattr(catalogue, "verify_reader", counted)
    timings = {}
    with client(tagging_db) as api:
        for label, codes in (("one", [1]), ("fifty", list(range(1, 51)))):
            before = len(calls)
            start = perf_counter()
            response = api.get(reads.PREFIX + "/batch", params={"codes": ",".join(map(str, codes))})
            timings[label] = {
                "seconds": perf_counter() - start,
                "profile_checks": len(calls) - before,
            }
            assert response.status_code == 200 and timings[label]["profile_checks"] == 1
            assert len(response.json()["funds"]) == len(codes)
        before = len(calls)
        assert api.get(reads.PREFIX + "/definitions").status_code == 200
        assert len(calls) - before == 2  # Two independent enrolled relations, no cache.
    record_property("semantic_batch_observed_timings", timings)


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
@pytest.mark.parametrize(
    "dimension,change",
    [("amc", {"value": "synthetic-secret-marker"}), ("risk", {"state": "contradicted"})],
)
def test_native_changed_or_incomplete_projection_is_not_silently_served(
    tagging_db, dimension, change
):
    from psycopg.types.json import Jsonb

    publish(tagging_db)
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute(
            "UPDATE mf_tagging.current SET document=document || %s WHERE code=1 AND dimension=%s",
            (Jsonb(change), dimension),
        )
    with client(tagging_db) as api:
        assert api.get(reads.PREFIX).status_code == 503
        response = api.get(reads.PREFIX + "/funds/1")
        assert response.status_code == 503 and response.json() == {
            "detail": "TAGGING_READ_MODEL_DIFFERS"
        }
        assert "synthetic-secret-marker" not in response.text
