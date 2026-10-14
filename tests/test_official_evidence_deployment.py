"""Pinned execution, original-byte transport and no false private-certification PASS."""

import hashlib
from types import SimpleNamespace

import pytest

from mfdataindia.enrichment import deployment, certification
from test_official_enrichment_evidence import intake
from test_official_enrichment_store import verified_db as verified_db, install, append
from test_verified_review_api import runtime_dsn
from test_verified_review_store import connection, ROLES


PDF = b"%PDF-fixture-private-original"


def request():
    return deployment.Source(
        intake=intake(PDF, media_type="application/pdf"),
        object_name="official-evidence-rollouts/dev-test-0001/source.pdf",
        generation="123", byte_length=len(PDF),
    )


def test_source_object_cohort_hash_and_immutable_coordinates():
    manifest = SimpleNamespace(rollout_id="dev-test-0001", cohort=[1, 2])
    source = request()
    assert len(source.bind(manifest)) == 64
    with pytest.raises(ValueError, match="rollout"):
        source.bind(SimpleNamespace(rollout_id="another-test-0001", cohort=[1, 2]))
    with pytest.raises(ValueError, match="cohort"):
        source.bind(SimpleNamespace(rollout_id="dev-test-0001", cohort=[2]))
    with pytest.raises(ValueError, match="binding"):
        deployment.source_request(manifest, {"ENRICHMENT_SOURCE": source.model_dump_json(), "ENRICHMENT_SOURCE_HASH": "0" * 64})


@pytest.mark.parametrize("generation", ["latest", "0", "123,FLAG=true", "-1", "123\n"])
def test_unsafe_or_unpinned_generation_rejected(generation):
    with pytest.raises(ValueError):
        request().model_copy(update={"generation": generation}).model_validate({**request().model_dump(), "generation": generation})


@pytest.mark.parametrize("dsn", [
    "postgresql://mfdata_verified_generator:fixture@localhost/mfdataindia",
    "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=wrong user=mfdata_verified_generator password=fixture",
    "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev hostaddr=127.0.0.1 dbname=mfdataindia user=mfdata_verified_generator password=fixture",
])
def test_pinned_source_principal_never_falls_back(dsn):
    with pytest.raises(ValueError):
        deployment.pinned_dsn(SimpleNamespace(git_sha="a" * 40), {"MFDATAINDIA_DSN": dsn, "DEPLOYMENT_GIT_SHA": "a" * 40}, deployment.store.ROLES[0])


class Response:
    def __init__(self, *, status=200, body=PDF, headers=None, result=None):
        self.status_code, self.body, self.result = status, body, result
        self.headers = {"Content-Length": str(len(PDF)), "x-goog-generation": "123", **(headers or {})}

    def json(self):
        return self.result

    def iter_content(self, size):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(result={"access_token": "synthetic-only"}) if len(self.calls) == 1 else self.response


def test_download_binds_real_original_bytes_generation_and_fixed_google_endpoint():
    session = Session(Response())
    assert deployment.download(request(), session) == PDF
    assert session.trust_env is False
    assert session.calls[1][0].startswith("https://storage.googleapis.com/storage/v1/b/tlws_mf_data_source/o/official-evidence-rollouts%2F")
    assert session.calls[1][1]["params"] == {"alt": "media", "generation": "123"}
    assert session.calls[1][1]["allow_redirects"] is False


@pytest.mark.parametrize("response", [
    Response(status=302), Response(headers={"x-goog-generation": "124"}),
    Response(headers={"Content-Length": "999999999"}), Response(headers={"Content-Encoding": "gzip"}),
    Response(body=PDF[:-1]), Response(body=PDF + b"x"), Response(body=b"X" * len(PDF)),
])
def test_download_cannot_accept_redirect_replacement_truncation_or_encoding(response):
    with pytest.raises(ValueError):
        deployment.download(request(), Session(response))


@pytest.mark.parametrize("recovery", [False, True])
def test_exact_http_replay_denial_and_recovery_codes(recovery):
    headers = {"X-Serverless-Authorization": "Bearer synthetic-iam", "Authorization": "Bearer synthetic-app"}

    class Http:
        def get(self, url, headers, **kwargs):
            status = 404 if recovery else 200 if "Authorization" in headers else 401
            return Response(status=status, headers={"Cache-Control": "no-store"}, result={"expected": True})

        def post(self, *args, **kwargs):
            return Response(status=405)

    certification.check_http(Http(), "https://synthetic.run.app", headers, [("/policy", {"expected": True})], recovery=recovery)


def test_success_status_cannot_hide_wrong_projection_or_missing_cache_control():
    class Http:
        def get(self, url, headers, **kwargs):
            return Response(status=200 if "Authorization" in headers else 401, result={"wrong": True})

    with pytest.raises(ValueError):
        certification.check_http(Http(), "https://synthetic.run.app", {"X-Serverless-Authorization": "x", "Authorization": "y"}, [("/policy", {})])


def test_metadata_sha_fixture_is_not_a_pdf_acceptance_certificate():
    assert hashlib.sha256(PDF).hexdigest() == request().intake.expected_sha256


@pytest.mark.postgres
def test_real_restricted_reader_projects_retained_pdf_without_raw_access_or_fact_claim(verified_db):
    install(verified_db)
    result = append(verified_db, PDF, media_type="application/pdf")
    document_id = result["document"]["document_id"]
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        before = certification.metadata_state(conn)
        cases = certification.projections(conn, request(), document_id)
        assert len(cases) == 4 and cases[-1][1]["state"] == "unmapped"
        assert cases[-1][1]["reason"] == "pdf_parser_not_implemented"
        assert cases[-1][1]["facts_available"] is False
        assert certification.metadata_state(conn) == before
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT content FROM mf_enrichment.blobs")
    with connection(verified_db, readonly=True) as conn:
        assert conn.execute("SELECT count(*) AS n FROM mf_enrichment.documents").fetchone()["n"] == 1
