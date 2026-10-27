"""Full broad capture, exact payloads and wire failures; synthetic providers only."""

from copy import deepcopy
import hashlib
import json

import pytest

from mfdataindia.review import broad_reader_capture as capture
from mfdataindia.review import broad_reader_capture_spec_v3 as cs
from mfdataindia.review import broad_reader_capture_controller_v3 as cc
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_transport_spec_v2 as spec
from mfdataindia.review import broad_reader_transport_controller_v2 as core
from mfdataindia.review import broad_reader_transport_backend_v2 as backend
from mfdataindia.review import broad_reader_owner_connection_v2 as gate
from mfdataindia.review.evidence_transport import decode_evidence
from broad_reader_fixtures_v2 import (
    sample,
    capture_completion,
    payload,
    approval,
    environment,
    worker,
    OwnerConnection,
    Cursor,
    ROOT,
)

ERRORS = (ValueError, policy.PolicyError)


def test_native_private_setting_fixture_uses_admin_setup_but_owner_capture(monkeypatch):
    from contextlib import contextmanager
    import test_broad_reader_transport_postgres_v2 as native

    calls = []

    class Connection:
        def __init__(self, principal):
            self.principal = principal

        def execute(self, statement):
            calls.append((self.principal, statement))
            if statement.startswith("ALTER FUNCTION") and self.principal != "fixture-admin":
                raise native.psycopg.errors.InsufficientPrivilege("synthetic fixture setup denied")

    @contextmanager
    def connect(dsn, *, autocommit=True):
        assert dsn == "SYNTHETIC_NO_CONNECTION" and autocommit is True
        yield Connection("fixture-admin")

    @contextmanager
    def restricted(dsn):
        assert dsn == "SYNTHETIC_NO_CONNECTION"
        yield Connection(policy.OWNER)

    def sealed(value):
        raw = json.dumps([value]).encode()
        return dict(is_null=False, sha256=hashlib.sha256(raw).hexdigest(), utf8_bytes=len(raw))

    state = dict(
        namespaces=[dict(oid=10, nspname="mf_analytics")],
        roles=[
            dict(
                rolname=policy.READER,
                rolconfig=sealed("private.broad_password=SYNTHETIC_ROLE_MARKER"),
            )
        ],
        functions=[
            dict(
                pronamespace=10,
                proname="private_broad",
                proconfig=sealed("private.broad_password=SYNTHETIC_FUNCTION_MARKER"),
            )
        ],
    )

    captures = []

    class OwnerCapture:
        def run(self, connection):
            assert connection.principal == policy.OWNER
            assert calls == [
                (
                    "fixture-admin",
                    "ALTER ROLE mfdata_verified_reader SET private.broad_password='SYNTHETIC_ROLE_MARKER'",
                ),
                (
                    policy.OWNER,
                    "CREATE FUNCTION mf_analytics.private_broad() RETURNS int LANGUAGE SQL AS 'SELECT 1'",
                ),
                (
                    "fixture-admin",
                    "ALTER FUNCTION mf_analytics.private_broad() SET private.broad_password='SYNTHETIC_FUNCTION_MARKER'",
                ),
            ]
            captures.append(connection.principal)
            return dict(state=deepcopy(state))

    monkeypatch.setattr(native.psycopg, "connect", connect)
    monkeypatch.setattr(native, "owner", restricted)
    monkeypatch.setattr(native.capture, "Capture", OwnerCapture)
    monkeypatch.setattr(native, "state", lambda _: deepcopy(state))
    native.test_native_private_settings_in_new_domain_are_sealed_before_connection_boundary(
        "SYNTHETIC_NO_CONNECTION"
    )
    assert captures == [policy.OWNER]


def test_public_candidate_preserves_independent_materialized_definition_seal():
    value = sample()
    row = next(r for r in value["candidate"] if r["relkind"] == "m")
    raw = b"SELECT 7 AS value"
    row["definition"] = dict(
        is_null=False, sha256=hashlib.sha256(raw).hexdigest(), utf8_bytes=len(raw)
    )
    cs.candidate_shape(value["candidate"], value["state"])
    old = next(r for r in value["state"]["relations"] if r["oid"] == row["oid"])
    assert old["definition"] == dict(is_null=True, sha256=None, utf8_bytes=0)
    p = payload()
    assert p["grant_proposal"]["materialized_unlocked"][0]["definition"] == row["definition"]


@pytest.mark.parametrize("defect", ["null", "empty", "sha", "owner", "name", "extra", "omitted"])
def test_independent_materialized_seal_does_not_relax_exact_metadata_or_root_coverage(defect):
    value = sample()
    row = next(r for r in value["candidate"] if r["relkind"] == "m")
    if defect == "null":
        row["definition"] = dict(is_null=True, sha256=None, utf8_bytes=0)
    elif defect == "empty":
        row["definition"]["utf8_bytes"] = 0
    elif defect == "sha":
        row["definition"]["sha256"] = "malformed"
    elif defect == "owner":
        row["owner"] += 1
    elif defect == "name":
        row["relname"] += "_relabelled"
    elif defect == "extra":
        row["unknown"] = True
    else:
        value["candidate"].remove(row)
    with pytest.raises(ValueError):
        cs.candidate_shape(value["candidate"], value["state"])


def test_fixed_query_enumerates_and_seals_materialized_root_dependencies():
    text = cs.frozen.CANDIDATE_SQL
    assert "WHERE c.relkind IN ('v','m')" in text
    assert "CASE WHEN c.relkind IN ('v','m')" in text


def test_fixed_query_descends_inheritance_from_every_root():
    assert "SELECT c.oid,true FROM pg_catalog.pg_class c" in cs.frozen.CANDIDATE_SQL


class CaptureConnection:
    autocommit = True
    info = OwnerConnection.info

    def __init__(self, transform=None):
        self.data = sample()
        self.calls = []
        self.transform = transform

    def execute(self, statement, *, prepare):
        assert prepare is False
        self.calls.append(statement)
        inv = cs.frozen
        if statement in inv.CONTROLS or statement == inv.TERMINAL:
            result = Cursor(
                [],
                [],
                "BEGIN"
                if statement.startswith("BEGIN")
                else "ROLLBACK"
                if statement == "ROLLBACK"
                else "SET",
            )
        else:
            q = next(q for q in inv.inventory()["queries"] if q["sql"] == statement)
            rows = (
                [{"value": self.data["state"]}]
                if q["id"] == "state"
                else [self.data["context"]]
                if q["id"] == "context"
                else self.data["candidate"]
            )
            result = Cursor(rows, q["columns"])
        if self.transform:
            self.transform(statement, result)
        return result


def test_public_broad_capture_exact_six_completions_full_sixteen_and_rollback():
    conn, c = CaptureConnection(), capture.Capture()
    value = c.run(conn)
    assert len(policy.prepare(value["state"])["coverage"]["present_schemas"]) == 16
    assert value["candidate"] == conn.data["candidate"] and any(
        r["relkind"] == "m" for r in value["candidate"]
    )
    assert conn.calls == [
        *cs.frozen.CONTROLS,
        cs.frozen.CONTEXT_SQL,
        policy.STATE_SQL,
        cs.frozen.CANDIDATE_SQL,
        policy.STATE_SQL,
        cs.frozen.CANDIDATE_SQL,
        cs.frozen.CONTEXT_SQL,
        "ROLLBACK",
    ]
    assert all(value[k] is False for k in cs.DENIALS)
    with pytest.raises(ValueError):
        c.run(conn)


@pytest.mark.parametrize("schema", policy.SCHEMAS)
def test_each_domain_schema_is_captured_not_frozen_to_old_two(schema):
    value = capture.Capture().run(CaptureConnection())
    assert any(n["nspname"] == schema for n in value["state"]["namespaces"])
    assert any(r["nspname"] == schema for r in value["candidate"])


@pytest.mark.parametrize(
    "defect",
    [
        "columns",
        "count",
        "boolean_count",
        "tag",
        "state",
        "private",
        "outside",
        "omitted_m",
        "unsupported",
    ],
)
def test_malformed_complete_capture_aborts_once_without_omission(defect):
    def transform(statement, cursor):
        if statement == cs.frozen.CONTEXT_SQL:
            if defect == "columns":
                cursor.description = []
            elif defect == "count":
                cursor.rowcount += 1
            elif defect == "boolean_count":
                cursor.rowcount = True
            elif defect == "tag":
                cursor.statusmessage = "SELECT 01"
        if statement == policy.STATE_SQL:
            if defect == "state":
                cursor.rows[0]["value"]["roles"][0]["unknown"] = "bad"
            elif defect == "private":
                cursor.rows[0]["value"]["roles"][0]["rolconfig"] = ["private.secret=MARKER"]
        if statement == cs.frozen.CANDIDATE_SQL:
            if defect == "outside":
                cursor.rows[0]["nspname"] = "public"
            elif defect == "unsupported":
                cursor.rows[0]["relkind"] = "f"
            elif defect == "omitted_m":
                cursor.rows = [r for r in cursor.rows if r["relkind"] != "m"]
                cursor.rowcount = len(cursor.rows)
                cursor.statusmessage = "SELECT " + str(len(cursor.rows))

    conn = CaptureConnection(transform)
    with pytest.raises(ERRORS):
        capture.Capture().run(conn)
    assert conn.calls.count("ROLLBACK") == 1
    assert not any(s.startswith(("GRANT", "ALTER DEFAULT", "LOCK")) for s in conn.calls)


def test_validated_capture_completion_binds_full_request_sources_and_unlocked_m():
    c = capture_completion()
    result = cc.validate_completion(c)
    p = payload()
    proposal = p["grant_proposal"]
    assert proposal["materialized_unlocked"] == [
        r for r in result["candidate"] if r["relkind"] == "m"
    ]
    assert not any(r["relkind"] == "m" for r in proposal["lock_witness"])
    assert spec.validate_payload(p) == p
    assert spec.decode_payload(spec.encode_payload(p)) == p
    assert set(proposal["reversal_template"]["delta"]) == {"schemas", "relations", "defaults"}
    assert (
        proposal["reversal_template"]["defaults_reversal_does_not_revoke_inherited_object_grants"]
        is True
    )
    assert all(s.endswith("RESTRICT") for s in proposal["reversal_template"]["sql_template"])


@pytest.mark.parametrize("field", ["approval_sha256", "request_sha256", "execution", "namespace"])
def test_complete_capture_cannot_relabel_hash_root_or_execution(field):
    c = capture_completion()
    c[field] = "0" * 64
    with pytest.raises(ERRORS):
        cc.validate_completion(c)


@pytest.mark.parametrize(
    "field",
    [
        "data_schema_custody",
        "future_owner_provisioning",
        "exclusive_admin",
        "recursive_availability_approved",
    ],
)
def test_write_proposal_never_invents_external_custody(field):
    p = payload()
    p["grant_proposal"]["window"][field] = False
    with pytest.raises(ERRORS):
        spec.validate_payload(p)


@pytest.mark.parametrize(
    "field",
    [
        "manifest_image",
        "manifest_git_sha",
        "packaged_controller_sha256",
        "packaged_spec_sha256",
        "packaged_inventory_sha256",
        "packaged_program_sha256",
    ],
)
def test_capture_packaged_binding_cannot_be_substituted(field):
    from mfdataindia.review.rollout import Manifest, seal_evidence

    c = capture_completion()
    body = deepcopy(c["certificate"]["evidence"])
    body["worker_binding"][field] = "0" * 64
    c["certificate"] = seal_evidence(
        Manifest.model_validate(c["approval"]["manifest"]), cc.STAGE, body
    )
    with pytest.raises(ERRORS):
        cc.validate_completion(c)


def test_write_profile_cannot_drop_materialized_unlocked_or_capture_provenance():
    p = payload()
    for field in ("materialized_unlocked", "capture_completion"):
        q = deepcopy(p)
        del q["grant_proposal"][field]
        with pytest.raises(ERRORS):
            spec.validate_payload(q)


@pytest.mark.parametrize("failure", [None, "COMMIT", "close"])
def test_full_worker_one_connection_exact_receipt_or_ambiguity_no_retry(monkeypatch, failure):
    p = payload()
    w = worker()
    conn = OwnerConnection(p["grant_proposal"], fail="COMMIT" if failure == "COMMIT" else None)
    calls = []

    def connect(**kw):
        assert (
            kw["connect_timeout"] == 5 and kw["user"] == policy.OWNER and kw["autocommit"] is True
        )
        calls.append("connect")
        return conn

    def close():
        calls.append("close")
        if failure == "close":
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")

    conn.close = close
    monkeypatch.setattr(w.psycopg, "connect", connect)
    e = environment(p)
    if failure:
        with pytest.raises(
            policy.PolicyError,
            match="COMMIT_OUTCOME_UNKNOWN" if failure == "COMMIT" else "COMMITTED_UNVERIFIED",
        ) as error:
            w.run_worker(e)
        assert "SYNTHETIC_PRIVATE_ERROR" not in str(error.value)
        if failure == "COMMIT":
            assert "ROLLBACK" not in conn.calls
    else:
        result = w.run_worker(e)
        cert = decode_evidence(json.loads(result))
        a = core.Approval.model_validate(core.approval_document(p))
        assert core.validate_result(a, cert) == cert["evidence"]
        assert "SYNTHETIC_ONLY" not in result
    with pytest.raises(ValueError):
        w.run_worker(e)
    assert calls == ["connect", "close"]
    assert not any("synthetic_materialized" in s and s.startswith("LOCK") for s in conn.calls)


@pytest.mark.parametrize("defect", ["missing", "owner", "definition", "extra"])
def test_actual_lock_witness_mismatch_aborts_before_any_grant(defect):
    p = payload()["grant_proposal"]
    wanted = deepcopy(p["lock_witness"])
    if defect == "missing":
        wanted.pop()
    elif defect == "owner":
        wanted[0]["owner"] += 1
    elif defect == "definition":
        row = next(r for r in wanted if r["relkind"] == "v")
        row["definition"]["sha256"] = "0" * 64
    else:
        wanted.append(deepcopy(wanted[-1]))
    conn = OwnerConnection(p, witness=wanted)
    doc = spec.engine_document(p)
    with pytest.raises(policy.PolicyError, match="PRECOMMIT_ABORTED_NO_RETRY"):
        policy.apply(gate.guarded_connection(conn, p), policy.Permit(doc, policy.fingerprint(doc)))
    assert not any(s in p["change"]["sql"] for s in conn.calls)
    assert conn.calls[-1] == "ROLLBACK"


@pytest.mark.parametrize("which", ["header", "chunk", "extra", "duplicate", "raw_size"])
def test_bounded_transport_rejects_incomplete_or_malformed_input(which):
    p = payload()
    e = spec.encode_payload(p)
    if which == "header":
        e[spec.HEADER] = "{}"
    elif which == "chunk":
        e.pop(spec.PREFIX + "00")
    elif which == "extra":
        e[spec.PREFIX + "63"] = "x"
    elif which == "duplicate":
        e[spec.HEADER] = '{"format":"x","format":"x"}'
    else:
        d = json.loads(e[spec.HEADER])
        d["raw_bytes"] = spec.MAX_RAW_BYTES + 1
        e[spec.HEADER] = json.dumps(d)
    with pytest.raises((ValueError, KeyError)):
        spec.decode_payload(e)


@pytest.mark.parametrize(
    "kind,name",
    [
        ("capture-job", "other"),
        ("capture-execution", "other-abcde"),
        ("capture-logs", 'bad" OR true'),
        ("secret", None),
        ("delete", None),
    ],
)
def test_new_provider_capture_read_targets_are_fixed_and_never_secret(kind, name):
    with pytest.raises(ValueError):
        backend.metadata_arguments(kind, name)


def test_backend_actual_capture_certificate_is_reconciled_without_any_POST(monkeypatch):
    a = approval()
    b = backend.Backend(a, ROOT)
    c = a.payload["grant_proposal"]["capture_completion"]
    b._image_resolution = c["image_resolution"]
    b._namespace = c["namespace"]
    monkeypatch.setattr(
        b,
        "metadata",
        lambda kind, name=None: deepcopy(
            c["job"]
            if kind == "capture-job"
            else [c["execution_document"]]
            if kind == "capture-executions"
            else c["execution_document"]
            if kind == "capture-execution"
            else [{"jsonPayload": c["certificate"]}]
        ),
    )
    b.verify_capture_source()
    assert not b.created and not b.executed and not b.sent_urls
    monkeypatch.setattr(b, "metadata", lambda *a, **k: [])
    with pytest.raises(ERRORS):
        b.verify_capture_source()
    b.session.close()
