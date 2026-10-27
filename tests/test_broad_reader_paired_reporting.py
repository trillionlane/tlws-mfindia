"""Actual T outer handler, public capture rollback and paired provenance gates."""
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
import runpy
import sys

import pytest
import psycopg

from broad_reader_fixtures_v2 import payload, capture_completion, window
from test_broad_capture_paired_entrypoint import entrypoint
from test_broad_reader_transport_v2 import CaptureConnection
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_capture as collector
from mfdataindia.review import broad_reader_capture_controller_v3 as capture
from mfdataindia.review import broad_reader_capture_controller_v2 as consumed_s
from mfdataindia.review import broad_reader_transport_spec_v2 as grant
from mfdataindia.review import broad_reader_transport_spec as old_grant
from mfdataindia.review import broad_reader_owner_connection_v2 as guarded
from mfdataindia.review import broad_reader_transport_controller_v2 as core

ROOT = Path(__file__).resolve().parents[1]
LABELS = (
    "INVALID_BROAD_READER_INPUT", "DUPLICATE_CATALOGUE_IDENTITY",
    "RESTRICTED_SHARED_READER_REQUIRED", "READER_MEMBERSHIPS_NOT_ALLOWED",
    "CORE_DOMAIN_SCHEMA_REQUIRED", "FUTURE_WRITE_OR_GRANT_OPTION_NOT_ALLOWED",
    "PUBLIC_FUTURE_DATA_READ_NOT_ALLOWED", "GLOBAL_TABLE_READ_OUTSIDE_DOMAIN_NOT_ALLOWED",
    "PUBLIC_DATA_READ_NOT_ALLOWED", "READER_SCHEMA_CREATE_NOT_ALLOWED",
    "DIRECT_DOMAIN_OWNER_REQUIRED", "FOREIGN_OR_UNSUPPORTED_RELATION",
    "SOURCE_SEQUENCE_MUTATION_NOT_ALLOWED", "DIRECT_OBJECT_OWNER_REQUIRED",
    "RLS_REQUIRES_SEPARATE_DISPOSITION", "WRITE_OR_GRANT_OPTION_NOT_ALLOWED",
    "COMPLETE_PHYSICAL_COLUMNS_REQUIRED", "COLUMN_WRITE_OR_GRANT_OPTION_NOT_ALLOWED",
    "PRIVILEGED_DOMAIN_FUNCTION_NOT_ALLOWED", "CANONICAL_CATALOGUE_REQUIRED",
)


def actual_outer_handler(monkeypatch, capsys, connection):
    _, _, _, environment = entrypoint()
    monkeypatch.setattr(sys, "argv", ["collect_broad_reader_capture_dev_v3.py"])
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    import psycopg
    calls = []

    def connect(**_):
        calls.append("connect")
        return connection

    connection.close = lambda: calls.append("close")
    monkeypatch.setattr(psycopg, "connect", connect)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(ROOT / "scripts/collect_broad_reader_capture_dev_v3.py"), run_name="__main__")
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""  # No partial/success certificate on any failure.
    assert output.err.count("\n") == 1 and len(output.err.encode()) < 160
    assert calls == ["connect", "close"]
    return output.err


@pytest.mark.parametrize("label", LABELS)
def test_every_fixed_policy_label_through_actual_outer_handler(monkeypatch, capsys, label):
    connection = CaptureConnection()

    def reject(self, conn):
        assert conn is connection
        raise policy.PolicyError(label)

    monkeypatch.setattr(collector.Capture, "run", reject)
    assert actual_outer_handler(monkeypatch, capsys, connection) == "permission capture failed: " + label + ";STOP_NO_RETRY\n"


@pytest.mark.parametrize("error,expected", [
    (policy.PolicyError("SYNTHETIC_PRIVATE_SECRET\nSQL schema.table 123"), "POLICY_STAGE_UNRECOGNIZED"),
    (RuntimeError("SYNTHETIC_PRIVATE_DRIVER_SECRET"), "RuntimeError"),
    (psycopg.errors.InsufficientPrivilege("SYNTHETIC_PRIVATE_SERVER_SECRET"), "InsufficientPrivilege"),
    (collector.AbortUnconfirmed("SYNTHETIC_PRIVATE_ABORT"), "AbortUnconfirmed"),
    (collector.WallAbortUnconfirmed("SYNTHETIC_PRIVATE_WALL"), "WallAbortUnconfirmed"),
])
def test_unrecognized_private_policy_and_nonpolicy_messages_are_never_logged(monkeypatch, capsys, error, expected):
    def reject(self, conn):
        raise error

    monkeypatch.setattr(collector.Capture, "run", reject)
    assert actual_outer_handler(monkeypatch, capsys, CaptureConnection()) == "permission capture failed: " + expected + ";STOP_NO_RETRY\n"


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_real_public_capture_policy_failure_one_rollback_ambiguity_overrides_stage(monkeypatch, capsys, rollback_fails):
    connection = CaptureConnection()
    for role in connection.data["state"]["roles"]:
        if role["rolname"] == policy.READER:
            role["rolinherit"] = True
    execute = connection.execute

    def observed(statement, **kwargs):
        if rollback_fails and statement == "ROLLBACK":
            connection.calls.append(statement)
            raise RuntimeError("SYNTHETIC_PRIVATE_ROLLBACK_FAILURE")
        return execute(statement, **kwargs)

    connection.execute = observed
    output = actual_outer_handler(monkeypatch, capsys, connection)
    expected = "AbortUnconfirmed" if rollback_fails else "RESTRICTED_SHARED_READER_REQUIRED"
    assert output == "permission capture failed: " + expected + ";STOP_NO_RETRY\n"
    assert connection.calls.count("ROLLBACK") == 1
    assert not any(sql.startswith(("GRANT", "ALTER", "COMMIT")) for sql in connection.calls)
    assert connection.calls[-1] == "ROLLBACK"


def test_safe_enum_and_both_workers_derive_same_fixed_fresh_sources():
    w, _, _, _ = entrypoint()
    assert w["POLICY_STAGES"] == frozenset(LABELS) and len(LABELS) == 20
    assert capture.IDENTITY == "dev-bread-cap-261027t"
    assert capture.NAME == "mf-reader-broad-capture-261027t"
    assert grant.capture_core is capture and guarded.spec is grant
    assert grant.IDENTITY == core.IDENTITY == "dev-reader-broad-261027t"
    assert grant.CAPTURE_CONTROLLER_SHA == hashlib.sha256(Path(capture.__file__).read_bytes()).hexdigest()
    assert old_grant.capture_core is not capture and consumed_s is not capture


@pytest.mark.parametrize("defect", ["git", "image", "failed", "missing_certificate", "old_s_identity", "old_s_job", "expired", "window601"])
def test_paired_grant_never_admits_stale_failed_consumed_or_extended_capture(defect):
    p = payload()
    c = p["grant_proposal"]["capture_completion"]
    if defect == "git":
        p["manifest"]["git_sha"] = p["controller_git_sha"] = "b" * 40
    elif defect == "image":
        original = p["manifest"]["image"]
        prefix, digest = original.split("@sha256:")
        p["manifest"]["image"] = prefix + "@sha256:" + ("0" if digest[0] != "0" else "1") + digest[1:]
        assert p["manifest"]["image"] != original
    elif defect == "failed":
        c["execution_document"]["status"] = dict(failedCount=1, conditions=[dict(type="Completed", status="False")])
    elif defect == "missing_certificate":
        c.pop("certificate")
    elif defect == "old_s_identity":
        c["approval"]["manifest"].update(rollout_id=consumed_s.IDENTITY, actor="rollout-certification:" + consumed_s.IDENTITY)
    elif defect == "old_s_job":
        c["job"]["metadata"]["name"] = consumed_s.NAME
    else:
        capture_doc = capture_completion()
        w = deepcopy(window(capture_doc))
        if defect == "expired":
            with pytest.raises(ValueError):
                grant.live_window(w, now=grant.timestamp(w["ends_at"]) + timedelta(seconds=1))
        else:
            w["ends_at"] = (grant.timestamp(w["starts_at"]) + timedelta(seconds=601)).strftime("%Y-%m-%dT%H:%M:%SZ")
            with pytest.raises(ValueError):
                grant.validate_window(w)
        return
    with pytest.raises((ValueError, KeyError)):
        grant.validate_payload(p)
