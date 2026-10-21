"""Full unchanged certifier over disposable PG18 and actual loopback API apps.

Synthetic source mechanics only. Endpoint routing, not financial/HTTP checks,
is replaced: no external provider, source request or DEV acceptance claim.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
import json
import socket
import threading
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest
import requests
import uvicorn

from mfdataindia.api.app import create_app
from mfdataindia.analytics import overlap_operations
from mfdataindia.enrichment import (
    book_certification as certification, book_deployment, book_preservation,
    book_projection, book_sources, book_store, multi_store, refresh_store,
    portfolio_operations, distribution_operations, idcw_operations,
)
from mfdataindia.enrichment.current_facts_certification import CORE_ORIGIN
from mfdataindia.review.app import create_amc_books_app
from mfdataindia.review import certification_input_transport as transport
from mfdataindia.review.evidence_transport import execution_evidence
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import CONNECTION, verify_evidence
from mfdataindia.store.locks import acquire_nav_data_operation_lock
from test_uti_v2_store import prepare_v4, inputs_v2, old, ROOT
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust
from test_verified_review_rollout import manifest as fixture_manifest
from test_verified_review_store import ROLES, source_state, verified_db as verified_db
from test_historical_store_api import foundations, install as install_historical

EXECUTION = "tlws-mf-official-facts-refresh-dev-transport-native"
METADATA_HOST = "metadata.google.internal"


def same_execution_fixture(conn):
    """Synthetic receipts, appended by real validators; never real-run evidence."""
    acquire_nav_data_operation_lock(conn)
    books = {}
    for key in sorted(s["key"] for s in book_sources.registry()["sources"]):
        doc = conn.execute(
            "SELECT document FROM mf_book_facts.runs WHERE source_key=%s AND status<>'failed' "
            "ORDER BY observed_at DESC,run_id DESC LIMIT 1", (key,),
        ).fetchone()["document"]
        body = {k: v for k, v in doc.items() if k != "run_id"}
        body.update(execution_id=EXECUTION, observed_at=(old.NOW + timedelta(seconds=3)).isoformat(),
                    status="unchanged")
        books[key] = book_store.append_run(conn, body)
    for schema, store in (("mf_fact_refresh", refresh_store), ("mf_multi_facts", multi_store)):
        doc = conn.execute("SELECT document FROM " + schema + ".runs WHERE status<>'failed' "
                           "ORDER BY observed_at DESC,run_id DESC LIMIT 1").fetchone()["document"]
        body = {k: v for k, v in doc.items() if k != "run_id"}
        body.update(execution_id=EXECUTION, observed_at=(old.NOW + timedelta(seconds=3)).isoformat(),
                    status="unchanged")
        store.append_run(conn, body)
    return books


def references(conn, batches, books, prior):
    # The existing synthetic fixture reference helper binds fixture outputs.
    # This is an integration mechanic, NOT independent issuer/source evidence;
    # real deployment must still bind separately reviewed original references.
    reviewed = old.references(batches[0])
    assert len(reviewed) == 6
    sbi = books["sbi-expanded"]["batch_ids"][0]
    sbi_values = {"benchmark_name": "BSE 500 (TRI)", "fund_managers": ["Mr. Anup Upadhyay"],
                  "base_expense_ratio": "1.4", "aum": "22109.87", "risk_level": "Very High"}
    reviewed.append({"code": 103215, "batch_id": sbi, "as_of": "2026-09-30",
                     "values": sbi_values, "source_values": {**sbi_values, "base_expense_ratio": "1.40"}})
    oracles = [
        {k: v for k, v in sample.items() if k != "values"}
        for batch in batches for sample in old.references(batch)
        if sample["code"] in (100669, 100822)
    ]
    supplemental = [{"code": code, "batch_id": batch["batch_id"], "as_of": batch["disclosure_as_of"],
                     "value": "1.68"} for batch in batches for code in (100669, 100668, 100822, 100823)]
    return {
        "execution_id": EXECUTION, "reviewed": reviewed, "original_oracles": oracles,
        "supplemental_oracles": supplemental, "prior_projection": prior,
        "prior_core_values": {str(r["code"]): book_deployment.core_reference(conn, r["code"])
                              for r in reviewed},
        "preserved_core_values": {str(code): book_deployment.core_reference(conn, code)
                                  for code in (101763, 103173, 103174)},
        "sources": {key: {"population_hash": value["population_hash"],
                           "denominator": value["coverage"][0]["denominator"],
                           "batch_ids": value["batch_ids"]} for key, value in books.items()},
    }


@pytest.mark.postgres
def test_shared_generated_worker_complete_certifier_real_pg_private_and_core_http(
    verified_db, monkeypatch, tmp_path, capsys
):
    # The full 52-path API starts every retained profile, unlike the narrower
    # book-store fixture. Install its real prior foundations before capturing
    # the source/book baseline; never bypass any API startup verification.
    foundations(verified_db)
    install_historical(verified_db)
    identities, calls, baseline, prior, _, _ = prepare_v4(verified_db, monkeypatch, aum_rows=True)
    # Source-store fixtures intentionally omit independent prior API layers.
    # The full real API needs all of them; use actual guarded installers, not
    # mocked ACL/startup checks or a reduced route factory.
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        for installer in (portfolio_operations, overlap_operations,
                          distribution_operations, idcw_operations):
            assert installer.install(conn, ROOT)["source_rows_changed"] == 0
    inputs = inputs_v2(identities, calls, aum_rows=True)
    old.publish(verified_db, identities, inputs, execution="native-transport-v5")
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        books = same_execution_fixture(conn)
        assert book_preservation.capture(conn, 4) == prior
        doc = references(conn, inputs[0], books, prior)
        original = source_state(conn)

    private_key, verifier = trust(frozenset({"review:read"}))
    trust_doc = {"public_key": private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
        "issuer": verifier.issuer, "audience": verifier.audience, "caller": verifier.caller,
        "allowed_scopes": ["review:read"]}
    trust_file, signer_file = tmp_path / "trust.json", tmp_path / "signer.pem"
    trust_file.write_text(json.dumps(trust_doc))
    signer_file.write_bytes(private_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    signer_file.chmod(0o600)
    codes = [r["code"] for r in doc["reviewed"]] + [101763, 103173, 103174]
    manifest = fixture_manifest(cohort=codes, canary_code=codes[0], trust_hash=fingerprint(trust_doc))
    reader = runtime_dsn(verified_db, ROLES[1])
    synthetic_pinned = make_conninfo(host="/cloudsql/" + CONNECTION, dbname="mfdataindia",
                                     user=ROLES[1], password="synthetic-not-live")
    connect = psycopg.connect

    def routed_connect(dsn="", *args, **kwargs):
        return connect(reader if dsn == synthetic_pinned else dsn, *args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", routed_connect)
    monkeypatch.setattr(certification, "Path", lambda value: {
        "/trust/config.json": trust_file, "/signer/key.pem": signer_file}[value])
    private_app = create_amc_books_app(reader, verifier=verifier)
    # The separately owned core facade has its own existing fixture principal.
    # Do not grant private verified_reader access to unrelated core relations.
    core_app = create_app(verified_db)
    metadata_app = FastAPI()
    metadata_app.add_api_route("/_fixture_metadata", lambda: PlainTextResponse("synthetic-iam-only"))

    @asynccontextmanager
    async def lifespan(_app):
        async with private_app.router.lifespan_context(private_app):
            async with core_app.router.lifespan_context(core_app):
                yield

    lifecycle = FastAPI(lifespan=lifespan)

    async def dispatch(scope, receive, send):
        if scope["type"] == "lifespan":
            target = lifecycle
        elif scope["path"] == "/_fixture_metadata":
            target = metadata_app
        else:
            target = core_app if scope["path"].startswith("/api/funds") else private_app
        await target(scope, receive, send)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(dispatch, host="127.0.0.1", port=port,
                                           access_log=False, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    send = requests.Session.send
    requests_seen = []

    def routed_send(session, request, **kwargs):
        parsed = urlsplit(request.url)
        hosts = {urlsplit(certification.REVIEW_ORIGIN).hostname, urlsplit(CORE_ORIGIN).hostname,
                 METADATA_HOST}
        if parsed.hostname not in hosts:
            raise AssertionError("synthetic certifier attempted nonfixture network")
        mapped = request.copy()
        path = "/_fixture_metadata" if parsed.hostname == METADATA_HOST else parsed.path
        mapped.url = f"http://127.0.0.1:{port}" + path + ("?" + parsed.query if parsed.query else "")
        kwargs["proxies"] = {}
        requests_seen.append((request.method, parsed.hostname, parsed.path))
        return send(session, mapped, **kwargs)

    monkeypatch.setattr(requests.Session, "send", routed_send)
    env = {"REVIEW_MANIFEST": manifest.model_dump_json(), "DEPLOYMENT_GIT_SHA": manifest.git_sha,
           "MFDATAINDIA_DSN": synthetic_pinned, "REVIEW_ORIGIN": certification.REVIEW_ORIGIN,
           "REVIEW_AUDIENCE": certification.REVIEW_ORIGIN,
           transport.RAW: json.dumps(doc),
           transport.HASH: fingerprint({"manifest": manifest.seal, "certification": doc})}
    thread.start()
    try:
        deadline = time.monotonic() + 6
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started and thread.is_alive()
        for phase in ("08-candidate", "11-active", "15-restored", "18-scheduled-http"):
            if phase == "11-active":
                with connect(verified_db, row_factory=dict_row) as conn:
                    # Keep the caller IDLE. The fixture already captured this
                    # exact prior configuration before source installation;
                    # do not open an implicit lookup transaction here.
                    book_projection.activate(conn, ROOT, doc["reviewed"],
                        expected_configuration_hash=fingerprint(baseline))
            submitted, _, _ = transport.prepare_submission(manifest, env, phase=phase, execution_id=EXECUTION)
            original_env = deepcopy(submitted)
            # Generated production entry, not a replacement certifier or decoder mock.
            with monkeypatch.context() as worker:
                worker.setattr(__import__("os"), "environ", submitted)
                exec(compile(transport.worker_program(manifest_hash=manifest.seal,
                    phase=phase, execution_id=EXECUTION), "<native-certification-worker>", "exec"), {})
            logs = [{"textPayload": line} for line in capsys.readouterr().out.splitlines()]
            certificate = execution_evidence(logs)
            result = verify_evidence(manifest, "amc-book-http", certificate)
            assert result["projection_active"] is (phase != "08-candidate")
            assert result["independent_supplemental_oracles_checked"] is True
            assert result["prior_projection_preservation"]["all_four_financial_query_payloads_unchanged"]
            assert result["source_rows_changed"] == result["human_events_created"] == 0
            assert result["core_responses_checked"] == 20 and result["metadata_responses_checked"] > 10
            assert submitted == original_env and transport.RAW not in submitted
        assert any(host == METADATA_HOST for _, host, _ in requests_seen)
        assert any(host == urlsplit(CORE_ORIGIN).hostname for _, host, _ in requests_seen)
        assert any(method == "POST" for method, _, _ in requests_seen)
        with connect(verified_db, row_factory=dict_row) as conn:
            assert source_state(conn) == original
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive()
