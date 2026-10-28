"""Actual fixed collector/worker paths with synthetic driver recording only."""
import hashlib
import json
from pathlib import Path
import runpy
import signal
import threading
from copy import deepcopy

import pytest

from function_attestation_fixtures import Connection, Cursor
from mfdataindia.review import reader_function_attestation as collector
from mfdataindia.review import reader_function_attestation_inventory as inv
from mfdataindia.review import reader_function_attestation_spec as spec
from mfdataindia.review.evidence_transport import decode_evidence, render_evidence
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review.rollout import verify_evidence, seal_evidence

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DSN = ("host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev "
                 "dbname=mfdataindia user=mfdata_admin password=SYNTHETIC_PRIVATE_DSN")


def test_fixed_public_collector_complete_read_and_confirmed_rollback():
    conn = Connection()
    instance = collector.Capture()
    value = instance.run(conn)
    spec.validate_capture_result(value)
    assert value["attestation"] == conn.doc
    assert conn.calls == [*inv.CONTROLS, inv.CONTEXT_SQL, inv.ATTESTATION_SQL,
                          inv.ATTESTATION_SQL, inv.CONTEXT_SQL, inv.TERMINAL]
    assert all(value[k] is False for k in spec.DENIALS)
    assert value["capture_sha256"] == fingerprint(
        {k: value[k] for k in ("context", "attestation", "completions")})
    with pytest.raises(ValueError):
        instance.run(conn)
    assert conn.calls.count(inv.TERMINAL) == 1


@pytest.mark.parametrize("defect", ["doc_drift", "context_drift", "columns", "rowcount",
                                   "tag", "reader_oid", "private_field", "version"])
def test_fail_closed_before_return_with_one_confirmed_rollback(defect):
    conn = Connection()
    if defect == "reader_oid":
        conn.doc["reader"][0]["oid"] = 40033
    elif defect == "private_field":
        conn.doc["functions"][0]["proconfig"] = ["PRIVATE_SENTINEL"]
    elif defect == "version":
        conn.ctx["version"] = 180007

    def hook(statement):
        if statement == inv.ATTESTATION_SQL:
            if defect == "doc_drift" and conn.document_reads == 1:
                conn.doc["functions"] = []
            if defect in ("columns", "rowcount", "tag"):
                cursor = Cursor([{"value": conn.doc}], ["bad"] if defect == "columns" else ["value"])
                if defect == "rowcount":
                    cursor.rowcount = 2
                if defect == "tag":
                    cursor.statusmessage = "SELECT 2"
                return cursor
        if statement == inv.CONTEXT_SQL and conn.context_reads == 1 and defect == "context_drift":
            conn.ctx["snapshot"] = "13:13:"
    conn.execute_hook = hook
    with pytest.raises(ValueError):
        collector.Capture().run(conn)
    assert conn.calls[-1] == inv.TERMINAL and conn.calls.count(inv.TERMINAL) == 1


@pytest.mark.parametrize("original", ["query", "validation"])
def test_rollback_failure_dominates_and_is_not_retried(original):
    conn = Connection()
    if original == "validation":
        conn.doc["reader"] = []

    def hook(statement):
        if statement == inv.TERMINAL or (original == "query" and statement == inv.CONTEXT_SQL):
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")
    conn.execute_hook = hook
    with pytest.raises(collector.AbortUnconfirmed):
        collector.Capture().run(conn)
    assert conn.calls.count(inv.TERMINAL) == 1


def test_spent_wall_has_no_more_io_including_rollback():
    conn = Connection()
    ticks = iter([0, 0, 16])
    with pytest.raises(collector.WallAbortUnconfirmed), collector._capture(
        conn, clock=lambda: next(ticks)):
        pytest.fail("spent wall cannot yield")
    assert conn.calls == [inv.CONTROLS[0]]


def test_wall_expiry_during_original_error_dominates_without_rollback():
    conn = Connection()
    spent = False

    def hook(statement):
        nonlocal spent
        if statement == inv.CONTEXT_SQL:
            spent = True
            raise RuntimeError("SYNTHETIC_PRIVATE_DRIVER")
    conn.execute_hook = hook
    with pytest.raises(collector.WallAbortUnconfirmed), collector._capture(
        conn, clock=lambda: 16 if spent else 0):
        pytest.fail("query failure cannot yield")
    assert inv.TERMINAL not in conn.calls


def test_existing_timer_is_never_replaced():
    conn = Connection()
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        with pytest.raises(ValueError):
            collector.Capture().run(conn)
        assert conn.calls == [] and signal.getitimer(signal.ITIMER_REAL)[0] > 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def test_nonmain_thread_cannot_make_database_io():
    conn, errors = Connection(), []

    def run():
        try:
            collector.Capture().run(conn)
        except Exception as error:
            errors.append(error)
    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert len(errors) == 1 and isinstance(errors[0], ValueError) and conn.calls == []


@pytest.mark.parametrize("field", ["format", "capture_sha256", "inventory_sha256",
                                  "source_hashes", "completions", "permission_authority"])
def test_result_tampering_never_becomes_permission_or_capture_evidence(field):
    value = collector.Capture().run(Connection())
    value[field] = True if field == "permission_authority" else "UNAPPROVED"
    with pytest.raises(ValueError):
        spec.validate_capture_result(value)


def test_distinct_document_and_certificate_capacities_keep_full_rows_or_fail_with_rollback():
    conn = Connection()
    assert len(canonical(conn.doc).encode()) < 131000
    value = collector.Capture().run(conn)
    worker, approval, _ = entrypoint()
    core = worker["core"]
    value["worker_binding"] = {
        "packaged_program_sha256": core.WORKER_SHA, "packaged_spec_sha256": core.SPEC_SHA,
        "packaged_inventory_sha256": core.INVENTORY_SHA,
        "packaged_controller_sha256": approval.controller_sha256,
        "manifest_image": approval.manifest.image, "manifest_git_sha": approval.manifest.git_sha,
    }
    value["input_binding"] = {
        "root": approval.root, "approval_sha256": fingerprint(approval.model_dump(mode="json")),
        "request_sha256": hashlib.sha256(core.inputs(approval)["body"]).hexdigest(),
        "controller_git_sha": approval.controller_git_sha,
        "controller_sha256": approval.controller_sha256,
    }
    certificate = seal_evidence(approval.manifest, core.STAGE, value)
    core.validate_result(approval, certificate)
    assert spec.MAX_RAW_BYTES == 8 * 1024 * 1024
    assert len(canonical(certificate).encode()) < spec.MAX_RAW_BYTES
    assert decode_evidence(json.loads(render_evidence(certificate))) == certificate
    for mutation in ("duplicate_target", "private_oversize"):
        rejected = Connection()
        if mutation == "duplicate_target":
            rejected.doc["functions"].append(deepcopy(rejected.doc["functions"][0]))
        else:
            rejected.doc["functions"][0]["body"] = "PRIVATE_SENTINEL" * 12000
        with pytest.raises(ValueError):
            collector.Capture().run(rejected)
        assert rejected.calls[-1] == inv.TERMINAL and rejected.calls.count(inv.TERMINAL) == 1
    with pytest.raises(ValueError):
        spec.validation.parse_document(b" " * 131001)
    assert conn.calls[-1] == inv.TERMINAL and conn.calls.count(inv.TERMINAL) == 1


def entrypoint():
    smoke = runpy.run_path(str(ROOT / "scripts/smoke_reader_function_attestation_binding.py"))
    approval, env = smoke["synthetic_inputs"]("a" * 40)
    env["MFDATAINDIA_DSN"] = SYNTHETIC_DSN
    worker = runpy.run_path(str(ROOT / "scripts/collect_reader_function_attestation_dev.py"))
    return worker, approval, env


def test_actual_worker_full_roundtrip_no_parser_or_collector_mock(monkeypatch):
    worker, approval, env = entrypoint()
    conn, attempts = Connection(), []

    def connect(**arguments):
        attempts.append(arguments)
        assert arguments == worker["connection_arguments"](SYNTHETIC_DSN)
        return conn
    monkeypatch.setattr(worker["psycopg"], "connect", connect)
    text = worker["run_worker"](env)
    certificate = decode_evidence(json.loads(text))
    result = verify_evidence(approval.manifest, spec.STAGE, certificate)
    spec.validate_result(result)
    assert result["attestation"] == conn.doc
    assert result["input_binding"]["approval_sha256"] == fingerprint(
        approval.model_dump(mode="json"))
    assert result["input_binding"]["request_sha256"] == hashlib.sha256(
        worker["core"].inputs(approval)["body"]).hexdigest()
    assert conn.closed == 1 and len(attempts) == 1 and conn.calls[-1] == inv.TERMINAL
    assert "SYNTHETIC_PRIVATE_DSN" not in text
    with pytest.raises(ValueError):
        worker["run_worker"](env)
    assert conn.closed == 1 and len(attempts) == 1


class NoSecret(dict):
    def __getitem__(self, key):
        assert key != "MFDATAINDIA_DSN", "invalid input reached secret lookup"
        return super().__getitem__(key)


@pytest.mark.parametrize("defect", ["malformed", "duplicate", "nan", "coerced", "extra",
                                   "old_identity", "old_root", "platform", "program", "pg_env"])
def test_actual_worker_rejects_bad_binding_before_secret_or_connection(monkeypatch, defect):
    w, a, env = entrypoint()
    doc = a.model_dump(mode="json")
    raw = canonical(doc)
    if defect == "malformed":
        raw = raw[:-1]
    elif defect == "duplicate":
        raw = '{"root":"ignored",' + raw[1:]
    elif defect == "nan":
        raw = raw.replace('"cohort":[154117]', '"cohort":[NaN]')
    elif defect == "coerced":
        doc["manifest"]["allow_canary_events"] = 1
        raw = canonical(doc)
    elif defect == "extra":
        doc["unexpected"] = True
        raw = canonical(doc)
    elif defect == "old_identity":
        doc["manifest"]["rollout_id"] = "dev-bread-cap-261027t"
        raw = canonical(doc)
    elif defect == "old_root":
        doc["root"] = "/Users/deviojha/projects/codex/artifacts/mf-reader-broad-capture-20261027-b147a894"
        raw = canonical(doc)
    elif defect == "platform":
        env["CLOUD_RUN_TASK_ATTEMPT"] = "1"
    elif defect == "program":
        env["READER_FUNCTION_ATTESTATION_PROGRAM_SHA256"] = "0" * 64
    else:
        env["PGHOST"] = "unapproved"
    env["READER_FUNCTION_ATTESTATION_APPROVAL"] = raw
    monkeypatch.setattr(w["psycopg"], "connect", lambda **_: pytest.fail("unexpected connect"))
    with pytest.raises(ValueError):
        w["run_worker"](NoSecret(env))
    with pytest.raises(ValueError):
        w["run_worker"](NoSecret(env))


@pytest.mark.parametrize("failure", ["connect", "query", "rollback", "close"])
def test_worker_original_failure_consumed_without_reconnect(monkeypatch, failure):
    w, _, env = entrypoint()
    conn, attempts = Connection(), []

    def hook(statement):
        if (failure == "query" and statement == inv.CONTEXT_SQL) or (
                failure == "rollback" and statement == inv.TERMINAL):
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")
    conn.execute_hook = hook

    def close():
        conn.closed += 1
        if failure == "close":
            raise RuntimeError("SYNTHETIC_PRIVATE_CLOSE")
    conn.close = close

    def connect(**_):
        attempts.append("connect")
        if failure == "connect":
            raise RuntimeError("SYNTHETIC_PRIVATE_CONNECT")
        return conn
    monkeypatch.setattr(w["psycopg"], "connect", connect)
    with pytest.raises(RuntimeError):
        w["run_worker"](env)
    with pytest.raises(ValueError):
        w["run_worker"](env)
    assert attempts == ["connect"]
    assert conn.closed == (0 if failure == "connect" else 1)
