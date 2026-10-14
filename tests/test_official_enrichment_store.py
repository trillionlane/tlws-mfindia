"""Disposable PostgreSQL 18 evidence, privilege, replay and API preservation proofs."""

from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mfdataindia.enrichment import evidence, operations, store
from mfdataindia.review import store as review
from mfdataindia.review.app import create_enrichment_app, create_historical_calculator_app
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_operations import capture_review
from test_official_enrichment_evidence import ISIN, document, intake
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import assertion, trust
from test_verified_review_store import ROLES, connection, source_state
from test_verified_review_store import verified_db as _verified_db

ROOT = Path(__file__).resolve().parents[1]
verified_db = _verified_db


def install(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return operations.install(conn, ROOT)


def setup(dsn):
    with connection(dsn) as conn:
        conn.execute(
            "UPDATE mf.funds SET isin_growth_or_div_payout=%s WHERE amfi_scheme_code=1", (ISIN,)
        )
    return install(dsn)


def append(dsn, content=None, **changes):
    payload = content or document()
    with psycopg.connect(runtime_dsn(dsn, ROLES[0]), row_factory=dict_row) as conn:
        return store.append(conn, intake(payload, **changes), payload)


def test_profile_is_additive_and_dormant_without_trust():
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    assert len(create_historical_calculator_app(dsn).openapi()["paths"]) == 11
    app = create_enrichment_app(dsn)
    assert len(app.openapi()["paths"]) == 15
    with TestClient(app) as client:
        assert client.get("/api/enrichment/v1/documents").status_code == 503
        assert client.get("/api/enrichment/v1/funds/1/coverage").status_code == 503
        assert client.post("/api/enrichment/v1/documents", json={}).status_code == 405


@pytest.mark.postgres
def test_install_replay_preserves_source_prior_layers_and_production_defaults(verified_db):
    with connection(verified_db, readonly=True) as conn:
        before, prior = source_state(conn), capture_review(conn)
    result = install(verified_db)
    assert result["source_rows_changed"] == 0
    assert install(verified_db)["status"] == "ALREADY_INSTALLED"
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before
        latest = capture_review(conn)
        assert set(latest["policies"]) - set(prior["policies"]) == {fingerprint(evidence.policy())}
        for table in prior:
            if table != "policies":
                assert latest[table] == prior[table]
        for table in ("blobs", "documents", "mappings"):
            assert not conn.execute(
                "SELECT has_table_privilege('mfdata_ingest',%s,'SELECT,INSERT,UPDATE,DELETE') AS allowed",
                ("mf_enrichment." + table,),
            ).fetchone()["allowed"]
        assert conn.execute(
            "SELECT has_table_privilege('mfdata_ingest','mf.verified_default_control','INSERT') AS allowed"
        ).fetchone()["allowed"]
    for role in ROLES:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            store.verify_acl(conn)
            with pytest.raises(ValueError, match="direct migration owner"):
                operations.install(conn, ROOT)


@pytest.mark.postgres
def test_atomic_replay_immutable_originals_and_read_side_coverage(verified_db):
    setup(verified_db)
    with connection(verified_db, readonly=True) as conn:
        before, prior = source_state(conn), capture_review(conn)
    first = append(verified_db)
    assert append(verified_db) == first
    assert first["mappings"][0]["state"] == "matched_candidate"
    assert first["accepted_facts_published"] == 0
    for role in ROLES[1:]:
        with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            actual = store.coverage(conn, 1)
            assert actual["state"] == "matched_candidate" and actual["facts_available"] is False
            assert store.get_document(conn, first["document"]["document_id"]) == first["document"]
            assert store.coverage(conn, 2)["state"] == "not_captured"
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT content FROM mf_enrichment.blobs")
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before and capture_review(conn) == prior
        assert (
            conn.execute("SELECT count(*) AS n FROM mf_enrichment.documents").fetchone()["n"] == 1
        )
        assert conn.execute("SELECT count(*) AS n FROM mf_enrichment.mappings").fetchone()["n"] == 1
    for table in ("blobs", "documents", "mappings"):
        with connection(verified_db) as conn:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute(sql.SQL("DELETE FROM mf_enrichment.{}").format(sql.Identifier(table)))


@pytest.mark.postgres
def test_revisions_conflicts_unsupported_and_stale_identity_are_retained(verified_db):
    setup(verified_db)
    first = append(verified_db)
    second = append(verified_db, document(plan="DIRECT"), observed_at="2026-10-14T11:00:00Z")
    with connection(verified_db, readonly=True) as conn:
        assert store.coverage(conn, 1)["reason"] == "plan_mismatch"
        assert (
            store.coverage(conn, 1, document_id=first["document"]["document_id"])["state"]
            == "matched_candidate"
        )
        page = store.documents(conn, limit=1)
        assert page["next_after"] is not None
        assert len(store.documents(conn, after=page["next_after"], limit=1)["items"]) == 1
    pdf = append(
        verified_db,
        b"%PDF-1.7\nfixture",
        media_type="application/pdf",
        observed_at="2026-10-14T12:00:00Z",
    )
    assert pdf["mappings"][0]["reason"] == "pdf_parser_not_implemented"
    with connection(verified_db) as conn:
        # Synthetic identity drift only, never a DEV correction.
        conn.execute("UPDATE mf.funds SET scheme_name='Changed' WHERE amfi_scheme_code=1")
    with connection(verified_db, readonly=True) as conn:
        assert store.coverage(conn, 1)["state"] == "identity_changed"
        assert (
            store.get_document(conn, second["document"]["document_id"])["parser_state"] == "parsed"
        )
    with pytest.raises(review.Conflict, match="retained candidate"):
        append(verified_db)


@pytest.mark.postgres
def test_failed_cohort_and_bad_bytes_leave_no_partial_evidence(verified_db):
    setup(verified_db)
    with pytest.raises(review.NotFound):
        append(verified_db, codes=[1, 999])
    with pytest.raises(ValueError, match="checksum"):
        append(verified_db, expected_sha256="a" * 64)
    with connection(verified_db, readonly=True) as conn:
        for table in ("blobs", "documents", "mappings"):
            assert (
                conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM mf_enrichment.{}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()["n"]
                == 0
            )


@pytest.mark.postgres
def test_sql_seals_and_unintended_capabilities_rejected(verified_db):
    setup(verified_db)
    record = append(verified_db)["document"]
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO mf_enrichment.blobs(content_hash,content) VALUES(%s,%s)",
                ("0" * 64, b"wrong"),
            )
    with connection(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="seal"):
            bad = {**record, "document_id": "0" * 64}
            conn.execute(
                "INSERT INTO mf_enrichment.documents(document_id,content_hash,policy_hash,document) VALUES(%s,%s,%s,%s)",
                (bad["document_id"], bad["content_hash"], bad["policy_hash"], Jsonb(bad)),
            )
    pdf = append(verified_db, b"%PDF-1.7\nfixture", media_type="application/pdf")
    with connection(verified_db) as conn:
        candidate = {**pdf["mappings"][0], "state": "matched_candidate", "reason": None}
        wrapped = {"document_id": pdf["document"]["document_id"], "mapping": candidate}
        with pytest.raises(psycopg.errors.RaiseException, match="mapping seal"):
            conn.execute(
                "INSERT INTO mf_enrichment.mappings(mapping_id,document_id,amfi_scheme_code,document) VALUES(%s,%s,%s,%s)",
                (fingerprint(wrapped), wrapped["document_id"], 1, Jsonb(wrapped)),
            )
    with connection(verified_db) as conn:
        conn.execute("GRANT SELECT(content) ON mf_enrichment.blobs TO mfdata_verified_reader")
    with connection(verified_db, readonly=True) as conn:
        with pytest.raises(ValueError, match="grants"):
            store.verify_acl(conn, principal=ROLES[1])


@pytest.mark.postgres
def test_private_evidence_http_and_old_contracts_remain_unchanged(verified_db):
    from test_historical_store_api import foundations, install as install_historical

    foundations(verified_db)
    install_historical(verified_db)
    setup(verified_db)
    record = append(verified_db)["document"]
    with connection(verified_db, readonly=True) as conn:
        before, prior = source_state(conn), capture_review(conn)
    key, verifier = trust()
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(
        create_enrichment_app(runtime_dsn(verified_db, ROLES[2]), verifier=verifier)
    ) as client:
        paths = [
            "/api/enrichment/v1/policy",
            "/api/enrichment/v1/documents",
            "/api/enrichment/v1/documents/" + record["document_id"],
            "/api/enrichment/v1/funds/1/coverage",
        ]
        for path in paths:
            assert client.get(path).status_code == 401
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert response.headers["Cache-Control"] == "no-store"
            assert "<table>" not in response.text and 'content":' not in response.text
        assert (
            client.get("/api/enrichment/v1/documents/" + "0" * 64, headers=headers).status_code
            == 404
        )
        assert (
            client.get("/api/enrichment/v1/documents?limit=101", headers=headers).status_code == 422
        )
        assert (
            client.get("/api/enrichment/v1/funds/999/coverage", headers=headers).status_code == 404
        )
        assert client.get("/api/review/v1/funds/1", headers=headers).status_code == 200
        assert (
            client.post("/api/review/v1/funds/1/reviews", json={}, headers=headers).status_code
            == 403
        )
        assert (
            client.post("/api/enrichment/v1/documents", json={}, headers=headers).status_code == 405
        )
    with connection(verified_db, readonly=True) as conn:
        assert source_state(conn) == before and capture_review(conn) == prior
