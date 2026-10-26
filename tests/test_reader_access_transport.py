"""Provider-free owner transport contracts. No actual secret/DB/cloud access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

from broad_transport_preservation import restore_ci_parent_file as restore_parent_file
import zlib

import pytest

from mfdataindia.review import reader_access_transport_spec as s
from mfdataindia.review import reader_access_transport_controller as core
from mfdataindia.review.policy import canonical, fingerprint
from test_reader_access_correction import frozen_state
from test_verified_review_rollout import manifest

ROOT = Path(__file__).resolve().parents[1]


def before():
    value = frozen_state()
    for group, rows in value.items():
        ordering = (
            ("roleid", "member", "grantor")
            if group == "memberships"
            else ("oid", "attnum")
            if group == "columns"
            else ("oid",)
        )
        rows.sort(key=lambda r: tuple(r[k] for k in ordering))
    return value


def window():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    def fmt(value):
        return value.isoformat().replace("+00:00", "Z")
    return {
        "reference": "SYNTHETIC_OWNER_ADMIN_WINDOW",
        "starts_at": fmt(now - timedelta(seconds=10)),
        "ends_at": fmt(now + timedelta(seconds=500)),
        "captured_at": fmt(now - timedelta(seconds=5)),
        "exclusive_admin": True,
        "recursive_availability_approved": True,
    }


def proposal():
    state = before()
    witness = [
        {k: r[k] for k in ("oid", "nspname", "relname", "relkind", "owner", "definition")}
        for r in state["relations"]
    ]
    return s.proposal(state, witness, window())


def payload(**changes):
    m = manifest(
        rollout_id=s.IDENTITY,
        actor="rollout-certification:" + s.IDENTITY,
        cohort=[154117],
        canary_code=154117,
    )
    return {
        "manifest": m.model_dump(mode="json"),
        "controller_git_sha": m.git_sha,
        "controller_sha256": hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
        "protected_hash": fingerprint({"protected": True}),
        "root": str(s.ARTIFACTS / "mf-reader-access-correction-20261025-aBc123"),
        "grant_proposal": proposal(),
        "g5_reference": "SYNTHETIC_NOT_LIVE_APPROVAL",
        **changes,
    }


def approval(**changes):
    return core.Approval.model_validate(core.approval_document(payload(**changes)))


def worker():
    spec = importlib.util.spec_from_file_location("reader_owner_worker_tests", ROOT / core.WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def environment(p):
    m = p["manifest"]
    return {
        **s.encode_payload(p),
        "DEPLOYMENT_GIT_SHA": m["git_sha"],
        "REVIEW_MANIFEST": canonical(m),
        "CLOUD_RUN_JOB": s.JOB,
        "CLOUD_RUN_EXECUTION": s.JOB + "-abcde",
        "CLOUD_RUN_TASK_INDEX": "0",
        "CLOUD_RUN_TASK_ATTEMPT": "0",
        "CLOUD_RUN_TASK_COUNT": "1",
    }


def test_finite_request_graph_full_payload_roundtrip_and_proposal_not_authority():
    p = payload()
    saved = deepcopy(p)
    a = core.approval_document(p)
    typed = core.Approval.model_validate(a)
    prepared = core.inputs(typed)
    assert p == saved and a["payload"] == p
    assert set(a) == {"payload", "request_sha256"}
    assert hashlib.sha256(prepared["body"]).hexdigest() == a["request_sha256"]
    assert s.decode_payload(prepared["environment"]) == p
    assert "request_sha256" not in p and "request" not in p
    assert a["request_sha256"] not in canonical(p)
    assert p["grant_proposal"]["proposal_only"] is True
    assert p["grant_proposal"]["change"]["runtime_or_write_approval"] is False
    assert p["grant_proposal"]["reversal_template"]["not_executable"] is True
    assert [len(x["columns"]) for x in p["grant_proposal"]["change"]["delta"]] == [84, 2]
    assert prepared["task"]["serviceAccountName"] == s.OWNER_SA
    assert prepared["task"]["containers"][0]["env"][-1]["valueFrom"]["secretKeyRef"] == {
        "name": s.SECRET,
        "key": "1",
    }
    a["payload"]["grant_proposal"]["change"]["delta"].clear()
    assert len(p["grant_proposal"]["change"]["delta"]) == 2


@pytest.mark.parametrize(
    "defect",
    [
        "request_hash",
        "extra_envelope",
        "payload_extra",
        "recursive_request",
        "change_extra",
        "delta",
        "owner",
        "profile",
        "raw_setting",
        "unknown_role_field",
        "unknown_column_field",
        "lock_missing",
        "lock_duplicate",
        "lock_type",
        "window_admin",
        "window_duration",
        "root",
        "g5_reference",
    ],
)
def test_every_unapproved_input_fails_before_secret_or_provider(defect):
    p = payload()
    if defect == "request_hash":
        a = core.approval_document(p)
        a["request_sha256"] = "0" * 64
        with pytest.raises(ValueError):
            core.Approval.model_validate(a)
        return
    if defect == "extra_envelope":
        a = core.approval_document(p)
        a["execute_now"] = True
        with pytest.raises(ValueError):
            core.Approval.model_validate(a)
        return
    q = p["grant_proposal"]
    if defect in ("payload_extra", "recursive_request"):
        p["extra" if defect == "payload_extra" else "request"] = {}
    elif defect == "change_extra":
        q["change"]["execute_now"] = True
    elif defect == "delta":
        q["change"]["delta"][1]["columns"].append("base_scheme_name")
    elif defect == "owner":
        q["change"]["owner"] = "postgres"
    elif defect == "profile":
        q["change"]["profile_sha256"] = "0" * 64
    elif defect == "raw_setting":
        q["change"]["before"]["roles"][0]["rolconfig"] = ["private=SYNTHETIC_PRIVATE_SETTING"]
    elif defect == "unknown_role_field":
        q["change"]["before"]["roles"][0]["private"] = "SYNTHETIC_PRIVATE_SETTING"
    elif defect == "unknown_column_field":
        q["change"]["before"]["columns"][0]["private"] = "SYNTHETIC_PRIVATE_SETTING"
    elif defect == "lock_missing":
        q["lock_witness"].pop()
    elif defect == "lock_duplicate":
        q["lock_witness"].append(q["lock_witness"][0])
    elif defect == "lock_type":
        q["lock_witness"][0]["relkind"] = "f"
    elif defect == "window_admin":
        q["window"]["exclusive_admin"] = False
    elif defect == "window_duration":
        q["window"]["ends_at"] = "2030-01-01T00:00:00Z"
    elif defect == "root":
        p["root"] = "/tmp/any-root"
    else:
        p["g5_reference"] = ""
    with pytest.raises((ValueError, KeyError)):
        core.approval_document(p)


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "extra",
        "raw_hash",
        "encoded_hash",
        "raw_size",
        "encoded_size",
        "bool_count",
        "git",
        "identity",
        "codec",
        "duplicate_json",
        "nonfinite",
        "trailing_stream",
    ],
)
def test_lossless_codec_rejects_corruption_or_context_mix(defect):
    e = s.encode_payload(payload())
    d = s.parse(e[s.HEADER])
    if defect == "missing":
        del e[s.PREFIX + "00"]
    elif defect == "extra":
        e[s.PREFIX + "99"] = "extra"
    elif defect == "raw_hash":
        d["raw_sha256"] = "0" * 64
    elif defect == "encoded_hash":
        d["encoded_sha256"] = "0" * 64
    elif defect == "raw_size":
        d["raw_bytes"] += 1
    elif defect == "encoded_size":
        d["encoded_bytes"] += 1
    elif defect == "bool_count":
        d["count"] = True
    elif defect == "git":
        d["git_sha"] = "b" * 40
    elif defect == "identity":
        d["identity"] = "dev-core-aclmeta-261024b"
    elif defect == "codec":
        d["codec"] = "plain"
    elif defect == "duplicate_json":
        e[s.HEADER] = '{"count":1,"count":1}'
        with pytest.raises(ValueError):
            s.decode_payload(e)
        return
    elif defect == "nonfinite":
        e[s.HEADER] = '{"count":NaN}'
        with pytest.raises(ValueError):
            s.decode_payload(e)
        return
    else:
        body = base64.b64encode(
            base64.b64decode(e[s.PREFIX + "00"]) + zlib.compress(b"{}")
        ).decode()
        assert len(body) < s.CHUNK_BYTES
        e[s.PREFIX + "00"] = body
        d["encoded_bytes"] = len(body)
        d["encoded_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    e[s.HEADER] = canonical(d)
    with pytest.raises((ValueError, KeyError, zlib.error)):
        s.decode_payload(e)


@pytest.mark.parametrize(
    "field,value",
    [
        ("CLOUD_RUN_JOB", "old-job"),
        ("CLOUD_RUN_TASK_ATTEMPT", "1"),
        ("CLOUD_RUN_TASK_INDEX", "1"),
        ("CLOUD_RUN_TASK_COUNT", "2"),
        ("CLOUD_RUN_EXECUTION", "ambiguous"),
        ("DEPLOYMENT_GIT_SHA", "0" * 40),
        ("PGOPTIONS", "-c role=postgres"),
    ],
)
def test_worker_fails_binding_before_secret_lookup(field, value, monkeypatch):
    w = worker()
    e = environment(payload())
    e[field] = value

    class Guard(dict):
        def __getitem__(self, key):
            assert key != "MFDATAINDIA_DSN", "secret accessed before failed gate"
            return super().__getitem__(key)

    monkeypatch.setattr(w.psycopg, "connect", lambda **_: pytest.fail("connection attempted"))
    with pytest.raises(ValueError):
        w.run_worker(Guard(e))
    with pytest.raises(ValueError):
        w.run_worker(Guard(e))


def test_worker_preflights_complete_output_before_secret_lookup(monkeypatch):
    w = worker()
    e = environment(payload())
    events = []

    def overflow(_):
        events.append("render")
        raise ValueError("SYNTHETIC_OUTPUT_OVERFLOW")

    monkeypatch.setattr(w, "render_evidence", overflow)

    class Guard(dict):
        def __getitem__(self, key):
            assert key != "MFDATAINDIA_DSN"
            return super().__getitem__(key)

    with pytest.raises(ValueError):
        w.run_worker(Guard(e))
    assert events == ["render"]


@pytest.mark.parametrize(
    "option",
    [
        "options='-c role=postgres'",
        "service=unexpected",
        "hostaddr=127.0.0.1",
        "port=5555",
        "user=postgres",
        "dbname=postgres",
        "host=localhost",
        "sslmode=require",
    ],
)
def test_exact_dsn_rejects_libpq_overrides_without_connection(option):
    dsn = (
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=mfdataindia user=mfdata_admin password=synthetic-only "
        + option
    )
    with pytest.raises(ValueError):
        worker().connection_arguments(dsn)


def test_all_old_files_fullbytes_and_workflow_fullprefix_preserved():
    a = json.loads((ROOT / "docs/READER_ACCESS_TRANSPORT_BASE_AUTHORITY_20261025.json").read_text())
    assert len(a["files"]) == 804
    for name, sha in a["files"].items():
        if name not in (".github/workflows/verify.yml", "tests/test_ci_verify_sequence.py"):
            actual = restore_parent_file(name, (ROOT / name).read_bytes())
            if name == ".github/workflows/rollout-verified-review-dev.yml":
                guard_plan = json.loads(
                    (ROOT / "docs/IMAGE_PREPARATION_REVISION_GUARD_LITERAL_PLAN_20261025.json").read_text()
                )
                for insertion in reversed(guard_plan["insertions"]):
                    addition = insertion["before"].encode()
                    assert actual.count(addition) == insertion["count"] == 1
                    actual = actual.replace(addition, b"", 1)
            assert hashlib.sha256(actual).hexdigest() == sha, name
    old = gzip.decompress(base64.b64decode(a["workflow_full_prior_bytes"]["gzip_base64"]))
    from test_reader_permission_capture_preservation import original_parent_bytes
    actual = original_parent_bytes(".github/workflows/verify.yml")
    assert (
        actual.startswith(old)
        and hashlib.sha256(old).hexdigest() == a["files"][".github/workflows/verify.yml"]
    )
    plan = json.loads((ROOT / "docs/READER_ACCESS_TRANSPORT_CI_LITERAL_PLAN_20261025.json").read_text())
    restored = original_parent_bytes(plan["path"]).decode()
    for sub in reversed(plan["substitutions"]):
        assert restored.count(sub["new"]) == sub["count"] == 1
        restored = restored.replace(sub["new"], sub["old"])
    assert hashlib.sha256(restored.encode()).hexdigest() == plan["original_full_sha256"]
    suffix = actual[len(old):].decode()
    assert suffix.count("      - name:") == 1 and suffix.startswith("\n      - name: Verify packaged fixed owner grant transport in both images\n")
    assert "smoke_reader_access_transport_binding.py" in suffix
