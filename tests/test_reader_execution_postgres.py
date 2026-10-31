"""Normal hosted disposable PG only, never shared DEV or consumer proof."""
from pathlib import Path
import json
import os
import subprocess
from copy import deepcopy
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

import pytest

from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_capture_inventory as inventory
from mfdataindia.review import broad_reader_view_profile as views
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review.policy import fingerprint, canonical
from test_ro01_reader_policy_postgres import ro01_db as ro01_db
from test_reader_source_variants_postgres import source_variant_db as source_variant_db
from test_reader_source_reference_postgres import source_reference_db as source_reference_db
from reader_execution_native_fixture import populate
from reader_execution_fixtures import execution_custody as execution_custody

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


def compilation(dsn):
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        assert conn.execute("SHOW server_version_num").fetchone()["server_version_num"] == "180006"
        state = conn.execute(policy.STATE_SQL).fetchone()["value"]
        candidates = [dict(row) for row in conn.execute(inventory.CANDIDATE_SQL).fetchall()]
    projection = views.definition_projection(state, candidates)
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    head = event["pull_request"]["head"]["sha"] if "pull_request" in event else event["after"]
    assert subprocess.check_output(["git", "rev-parse", head + "^{tree}"], cwd=ROOT, text=True).strip() == tree
    result = {"format": "RO02_HOSTED_SOURCE_VIEW_COMPILATION_V1_NOT_LIVE_ACCEPTANCE",
        "head": head, "checkout": checkout, "tree": tree,
        "source": source.verify_packaged_source(ROOT), "view_projection": projection,
        "view_count": len(projection), "view_projection_sha256": fingerprint(projection),
        "permission_enabled": False, "consumer_acceptance": False}
    return result, state, candidates


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_compile_actual_populated_current_source_graph(ro01_db, monkeypatch, capsys):
    populate(ro01_db, monkeypatch)
    observed, state, candidates = compilation(ro01_db)
    # Bootstrap compilation is not permission acceptance. Until its complete
    # actual projection is reviewed/pinned, the unreviewed graph MUST reject.
    with pytest.raises(views.ViewProfileError):
        views.select(state, candidates, base_authority_sha256=source.BASE_SHA256,
                     variant_authority_sha256=source.VARIANT_SHA256)
    with capsys.disabled():
        print("RO02_SOURCE_VIEW_COMPILATION:" + canonical(observed), flush=True)


@pytest.mark.parametrize("profile", ["BASE44_EXTENDED", "BASE44_BOOK_V2_SINGLE_PASS"])
def test_native_current_source_authority_recompiles_existing_extended_profiles(source_variant_db, profile):
    from reader_source_variants import install_book_variant
    dsn, _ = source_variant_db
    if profile == "BASE44_BOOK_V2_SINGLE_PASS":
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
            install_book_variant(conn, ROOT)
    _, state, candidates = compilation(dsn)
    assert views.select(state, candidates, base_authority_sha256=source.BASE_SHA256,
                        variant_authority_sha256=source.VARIANT_SHA256)["profile"] == profile


def test_native_current_source_authority_recompiles_unchanged_legacy_graph(source_reference_db):
    dsn, _ = source_reference_db
    _, state, candidates = compilation(dsn)
    assert views.select(state, candidates, base_authority_sha256=source.BASE_SHA256,
                        variant_authority_sha256=source.VARIANT_SHA256)["profile"] == "LEGACY_BASE44"


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_complete_entrypoint_chain_application_and_generator_coexistence(
        ro01_db, monkeypatch, capsys, tmp_path):
    from mfdataindia.review import reader_execution_controller as controller
    from mfdataindia.review import reader_execution_worker as worker
    from mfdataindia.review import reader_execution_application as application
    from mfdataindia.review import reader_access_profile as profiles
    from mfdataindia.review import broad_reader_direct_delivery_spec as spec
    from mfdataindia.review import reader_function_attestation_spec as attestation
    from mfdataindia.review import app as real_app
    from mfdataindia.review.evidence_transport import execution_evidence
    from mfdataindia.enrichment import portfolio_store, distribution_store, idcw_store
    from broad_reader_native_oid_isomorphism import validate as native_oids
    from test_ro01_reader_policy_postgres import connect, PASSWORD, generator_capabilities
    from test_distribution_observations import inputs as distribution_inputs
    from test_idcw_catalog import inputs as idcw_inputs
    from test_verified_review_auth import assertion
    from reader_execution_fixtures import plan, authority, FakeProvider, GIT
    from fastapi.testclient import TestClient

    inputs = populate(ro01_db, monkeypatch)
    # Only the already-reviewed ephemeral-OID bijection reaches the original
    # fixed-DEV assertion validator. Actual wire/catalogue OIDs stay untouched.
    original_validation = attestation.validate_capture_result
    monkeypatch.setattr(attestation, "validate_capture_result", lambda value: native_oids(value, original_validation))
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    value, verifier, private = plan()
    native_connect = psycopg.connect
    original_pool = real_app.ConnectionPool
    local_reader = make_conninfo(ro01_db, user=policy.READER, password=PASSWORD)
    def public_dsn(kind):
        return make_conninfo(host="/cloudsql/" + spec.CONNECTION, dbname=spec.DATABASE,
            user=policy.READER if kind == "reader" else policy.OWNER, password=PASSWORD)
    def connector(**arguments):
        assert arguments["host"] == "/cloudsql/" + spec.CONNECTION
        assert arguments["dbname"] == policy.DATABASE and arguments["password"] == PASSWORD
        assert arguments["user"] in (policy.OWNER, policy.READER)
        options = {key: val for key, val in arguments.items()
                   if key not in ("host", "port", "sslmode", "dbname", "user", "password")}
        return native_connect(make_conninfo(ro01_db, user=arguments["user"], password=PASSWORD), **options)
    def pool(*, conninfo, **options):
        assert conninfo == public_dsn("reader")
        return original_pool(conninfo=local_reader, **options)
    monkeypatch.setattr(real_app, "ConnectionPool", pool)
    # Real DB connect only: a synthetic fixed Cloud SQL transport is mapped to
    # the hosted disposable server, with the same direct reader/owner identity.
    monkeypatch.setattr(worker.psycopg, "connect", connector)
    adapters, phase_calls = {}, []
    class NativeProvider(FakeProvider):
        def execute(self, kind):
            assert not self.ran
            self.calls.append("execute")
            self.ran = True
            for key in list(os.environ):
                if key.startswith(("BROAD_DIRECT_", "READER_EXECUTION_", "CLOUD_RUN_")):
                    monkeypatch.delenv(key)
            env = {row["name"]: row["value"] for row in self.prepared["task"]["containers"][0]["env"] if "value" in row}
            env.update(MFDATAINDIA_DSN=public_dsn(kind), CLOUD_RUN_JOB=self.name.rsplit("-", 1)[0],
                CLOUD_RUN_EXECUTION=self.name, CLOUD_RUN_TASK_COUNT="1", CLOUD_RUN_TASK_INDEX="0", CLOUD_RUN_TASK_ATTEMPT="0")
            for key, val in env.items():
                monkeypatch.setenv(key, val)
            monkeypatch.setattr(worker, "_worker", worker.Worker())
            self.worker_exit = 0
            with capsys.disabled():
                # Capture only synthetic worker output locally, not pytest's
                # business-row logs; feed the exact existing certificate codec.
                import contextlib
                import io
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        worker.main(source_root=ROOT)
                    except SystemExit as stopped:
                        self.worker_exit = stopped.code
                assert PASSWORD not in out.getvalue() + err.getvalue()
                self.result = execution_evidence([{"textPayload": line} for line in out.getvalue().splitlines()])
            phase_calls.append((kind, self.worker_exit, self.result["evidence"]["state"]))
            return self.execution(kind, self.name)
        def execution(self, kind, name):
            value = super().execution(kind, name)
            if getattr(self, "worker_exit", 0):
                value["status"] = {"conditions": [{"type": "Completed", "status": "False"}], "failedCount": 1}
            return value
    def factory(p, kind, repo, prepared):
        key = p["operation_id"], kind
        if key not in adapters:
            adapters[key] = NativeProvider(p, kind, repo, prepared)
        else:
            assert adapters[key].prepared == prepared
        return adapters[key]
    context = application.ApplicationCheck(public_dsn("reader"), verifier,
        assertion(private, scope=["review:read"]), assertion(private))
    # Store helpers used by the native assertions must keep real connection
    # semantics; only the worker's kwargs call is the fixed synthetic transport.
    def connect_dispatch(*args, **kwargs):
        return connector(**kwargs) if not args and kwargs.get("host", "").startswith("/cloudsql/") else native_connect(*args, **kwargs)
    monkeypatch.setattr(worker.psycopg, "connect", connect_dispatch)
    generators = generator_capabilities(ro01_db)
    result = controller.execute(value, authority(value), context, source_root=ROOT, expected_git=GIT,
        backend_factory=factory, journal_base=tmp_path, sleep=lambda _: pytest.fail("fake provider has no wait"))
    assert result["state"] == "OPERATION_APPLICATION_VERIFIED", result
    assert result["grant_effect"] == "COMMITTED_ACL_VERIFIED"
    assert result["consumer_acceptance"] == "PENDING_RO04"
    assert [kind for kind, code, _ in phase_calls] == ["capture", "grant", "reader"]
    assert all(code == 0 for _, code, _ in phase_calls)
    assert all(a.calls.count("create") == a.calls.count("execute") == 1 for a in adapters.values())
    with connect(ro01_db, policy.READER) as conn:
        profiles.verify_selection(conn, profiles.BROAD)
        for relation in ("mf_monthly_facts.sources", "mf_book_facts.sources", "mf_multi_facts.sources", "mf_fact_refresh.catalogs"):
            assert conn.execute("SELECT has_column_privilege(current_user,%s,'content','SELECT') AS allowed", (relation,)).fetchone()["allowed"]
            conn.execute("SELECT content FROM " + relation + " LIMIT 1").fetchall()
        for statement in ("INSERT INTO mf.funds DEFAULT VALUES", "UPDATE mf.funds SET scheme_name=scheme_name", "DELETE FROM mf.funds",
                          "CREATE TABLE mf.ro02_forbidden(n integer)", "SET ROLE mfdata_admin", "SELECT nextval('mf.amcs_amc_id_seq')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    # Explicit query-count comparison to the same RO-01 real app/flags/routes.
    # No cache, increased timeout, security guard bypass or invented latency SLA.
    original_catalogue = profiles._catalogue
    counts = {"calls": 0}
    def catalogue(conn):
        counts["calls"] += 1
        return original_catalogue(conn)
    monkeypatch.setattr(profiles, "_catalogue", catalogue)
    baseline_app = real_app.create_review_app(public_dsn("reader"), verifier=verifier,
        reader_profile=profiles.BROAD, **application.FLAGS)
    with TestClient(baseline_app) as client:
        for name, route in application.ROUTES.items():
            path = route if name == "refresh" else route.format(code=value["probe_codes"][name])
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer " + context.read_assertion}).status_code == 200
        for token in (context.read_assertion, context.admin_assertion):
            assert client.post("/api/review/v1/funds/1/reviews", json={}, headers={"Authorization": "Bearer " + token}).status_code == 403
    baseline_queries = counts["calls"]
    counts["calls"] = 0
    app_result = context.check(value)
    assert counts["calls"] == baseline_queries
    assert len(app_result["routes"]) == 4 and len(app_result["denied_mutation_scopes"]) == 2
    # Actual post-adoption generator publication and replay, no caller BROAD.
    with connect(ro01_db, "mfdata_verified_generator", autocommit=False) as conn:
        first = portfolio_store.publish(conn, inputs["portfolio_selection"])
        assert portfolio_store.publish(conn, inputs["portfolio_selection"]) == first
    selected, raw, _ = distribution_inputs(monkeypatch)
    with connect(ro01_db, "mfdata_verified_generator", autocommit=False) as conn:
        first = distribution_store.append(conn, selected, raw)
        assert distribution_store.append(conn, selected, raw) == first
    selected, originals, _ = idcw_inputs(monkeypatch)
    with connect(ro01_db, "mfdata_verified_generator", autocommit=False) as conn:
        first = idcw_store.append_batch(conn, selected, originals)
        assert len(first) == 4 and idcw_store.append_batch(conn, selected, originals) == first
    assert generator_capabilities(ro01_db) == generators
    # A second distinct operation on already-compliant state must skip grant,
    # not manufacture a COMMIT or reuse a spent identity/certificate.
    other = deepcopy(value)
    other["operation_id"] = "op-fedcba9876543210"
    counts["calls"] = 0
    second = controller.execute(other, authority(other), context, source_root=ROOT, expected_git=GIT,
        backend_factory=factory, journal_base=tmp_path, sleep=lambda _: pytest.fail("no wait"))
    assert second["state"] == "OPERATION_APPLICATION_VERIFIED"
    assert second["grant_effect"] == "NO_GRANT_SUBMITTED"
    assert (other["operation_id"], "grant") not in adapters
    assert counts["calls"] <= baseline_queries + 2
    # Missing/unsafe permissions are observed and fail closed independently.
    with connect(ro01_db) as conn:
        conn.execute("REVOKE SELECT ON mf_monthly_facts.sources FROM mfdata_verified_reader")
    with connect(ro01_db, policy.READER) as conn:
        with pytest.raises(profiles.ReaderAccessError):
            profiles.verify_selection(conn, profiles.BROAD)
    with capsys.disabled():
        print("RO02 native connected entrypoints: capture/grant/reader actual DB; real14-feature lifespan+4authenticated routes;2mutation scopes403;generator publication/replay unchanged;reader DML/DDL denied;fresh noop skipsgrant;consumerPENDING_RO04", flush=True)
        print("RO02_NATIVE_COST:" + canonical({"baseline_catalogues": baseline_queries,
              "noop_operation_catalogues": counts["calls"], "startup_seconds": app_result["startup_seconds"],
              "route_seconds": [r["authenticated"]["seconds"] for r in app_result["routes"]]}), flush=True)
