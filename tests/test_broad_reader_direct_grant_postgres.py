"""Disposable PG18 end-to-end direct grant only; never a live DB/prod test."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from broad_reader_native_oid_isomorphism import validate as validate_native_oids
from mfdataindia.review import broad_reader_direct_grant as grant
from mfdataindia.review import broad_reader_direct_reader_check as reader_check
from mfdataindia.review import broad_reader_embedded_authority as embedded
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review import reader_function_attestation_spec as attestation_spec
from mfdataindia.store.postgres import PostgresStore


pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
NAME = policy.DATABASE
PASSWORD = "disposable-broad-reader-ci-only"
ROLES = ("mfdata_admin", "mfdata_verified_generator", "mfdata_verified_reader",
         "mfdata_verified_admin")


@pytest.fixture
def exact_source_db(pg_dsn):
    """Fresh exact-named disposable source database, never an existing DEV DB."""
    if not pg_dsn:
        pytest.skip("normal disposable CI PostgreSQL DSN required")
    packaged = source.verify_packaged_source(ROOT)
    base = json.loads(embedded.document("base"))
    core = tuple(Path(row["path"]).name for row in base["files"]
                 if Path(row["path"]).parent == Path("sql"))
    optional = tuple(Path(row["path"]).name for row in base["files"]
                     if Path(row["path"]).parent == Path("sql/optional"))
    assert len(core) == len(optional) == 22
    owner_dsn = make_conninfo(pg_dsn, dbname=NAME, user=policy.OWNER,
                              password=PASSWORD)
    reader_dsn = make_conninfo(pg_dsn, dbname=NAME, user=policy.READER,
                               password=PASSWORD)
    created = []
    database_created = False
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        assert admin.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                             (NAME,)).fetchone() is None
        assert admin.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)",
                             (list(ROLES),)).fetchone() is None
        try:
            for role in ROLES:
                admin.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE "
                    "NOCREATEDB NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(PASSWORD)))
                created.append(role)
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(NAME), sql.Identifier(policy.OWNER)))
            database_created = True
            with PostgresStore(owner_dsn, use_copy=False) as store:
                assert store.apply_migrations(ROOT / "sql", files=core) == list(core)
                assert store.apply_migrations(ROOT / "sql/optional", files=optional) == list(optional)
            yield owner_dsn, reader_dsn, packaged
        finally:
            if database_created:
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(NAME)))
            for role in reversed(created):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _connection(dsn):
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                           connect_timeout=5)


def _permit(before, packaged):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    window = {"starts_at": (now - timedelta(seconds=1)).isoformat(),
              "ends_at": (now + timedelta(minutes=5)).isoformat(),
              "exclusive_administration": True, "data_schema_custody": True,
              "future_owner_provisioning": True}
    git_sha = "a" * 40  # Synthetic CI-only image identity; no provider claim.
    images = {kind: {
        "image": ("asia-south1-docker.pkg.dev/trillionlane-dev/"
                  "tlws-mf-data-dev/" + repository + "@sha256:" + "c" * 64),
        "resolved_image": ("asia-south1-docker.pkg.dev/trillionlane-dev/"
                           "tlws-mf-data-dev/" + repository + "@sha256:" + "d" * 64),
        "config_digest": "sha256:" + "e" * 64, "git_sha": git_sha,
        "platform": "linux/amd64", "layers_downloaded": 0}
        for kind, repository in (("app", "mfdataindia"),
                                 ("tools", "mfdataindia-review-tools"))}
    document = {"format": "mf-broad-reader-direct-grant-v1", "window": window,
                "before_snapshot": before["before_snapshot"],
                "helper_attestation": before["helper_attestation"],
                "packaged_source": packaged, "git_sha": git_sha,
                "image_binding": images, "create_only_intent_sha256": "b" * 64}
    return grant.Permit(document, policy.fingerprint(document))


@pytest.fixture
def native_identity(monkeypatch):
    """OID renaming only in this disposable test; live validation is untouched."""
    original = attestation_spec.validate_capture_result
    monkeypatch.setattr(attestation_spec, "validate_capture_result",
                        lambda value: validate_native_oids(value, original))


def test_native_actual_grant_commit_full_readback_and_authenticated_reader(exact_source_db,
                                                                            native_identity):
    owner_dsn, reader_dsn, packaged = exact_source_db
    with _connection(owner_dsn) as observer:
        before = grant.capture_before(observer, packaged_source=packaged,
                                      source_root=ROOT)
        assert before["grant_authorized"] is False
        with _connection(owner_dsn) as target:
            receipt = grant.run(target, observer, _permit(before, packaged),
                                source_root=ROOT)
        assert receipt["stage"] == "COMMITTED_STATE_VERIFIED"
        assert receipt["postgrant_proof"]["permission_enabled"] is False
        assert receipt["authenticated_reader_verified"] is False
        with _connection(reader_dsn) as reader:
            check = reader_check.run(reader,
                                     planned_after=before["before_snapshot"]["plan"]["after"])
        assert check["representative_select_completed"] is True
        assert check["permission_enabled"] is False and check["ti_verified"] is False


def test_native_nowait_denial_has_no_grant_or_partial_default_acl(exact_source_db,
                                                                   native_identity):
    owner_dsn, reader_dsn, packaged = exact_source_db
    with _connection(owner_dsn) as observer:
        before = grant.capture_before(observer, packaged_source=packaged,
                                      source_root=ROOT)
        with _connection(owner_dsn) as blocker, _connection(owner_dsn) as target:
            blocker.execute("BEGIN", prepare=False)
            blocker.execute("SELECT 1 FROM mf.funds LIMIT 1", prepare=False)
            try:
                with pytest.raises(grant.GrantError, match="PRECOMMIT_ABORTED_NO_RETRY"):
                    grant.run(target, observer, _permit(before, packaged),
                              source_root=ROOT)
            finally:
                blocker.execute("ROLLBACK", prepare=False)
        after = grant.capture_before(observer, packaged_source=packaged,
                                     source_root=ROOT)
        assert after["before_snapshot"] == before["before_snapshot"]
        with _connection(reader_dsn) as reader:
            reader.execute("BEGIN READ ONLY", prepare=False)
            try:
                assert reader.execute(
                    "SELECT has_table_privilege(current_user,'mf.funds','SELECT') AS allowed",
                    prepare=False).fetchone()["allowed"] is False
            finally:
                reader.execute("ROLLBACK", prepare=False)
