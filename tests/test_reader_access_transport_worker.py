"""Complete worker lifecycle with no actual secrets, connections or provider."""

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zlib

import pytest

from mfdataindia.review import reader_access_correction as engine
from mfdataindia.review import reader_access_transport_spec as s
from mfdataindia.review import reader_access_transport_controller as core
from mfdataindia.review.policy import canonical
from test_reader_access_transport import before, environment, payload, worker


def test_terminal_worker_result_binds_exact_external_approval_and_full_request():
    p = payload()
    result = worker().expected_result(p, {"synthetic_binding": True})
    approval = core.approval_document(p)
    assert result["request_sha256"] == approval["request_sha256"]
    assert result["approval_sha256"] == core.fingerprint(approval)
    assert result["payload_sha256"] == core.fingerprint(p)
    assert not {"request_sha256", "approval_sha256"} & set(p)


def test_native_witness_mutation_cannot_change_full_before_state_expectation():
    from test_reader_access_owner_connection_postgres import witness

    plan = {"before": before()}
    original = deepcopy(plan)
    wanted = witness(plan, {"current_fund_facts", "fund_variants"})
    next(r for r in wanted if r["relkind"] == "v")["definition"]["sha256"] = "0" * 64
    assert plan == original


@pytest.mark.parametrize("failure", [None, "COMMIT", "close"])
def test_complete_worker_connects_once_verifies_full_receipt_and_never_retries(
    monkeypatch, failure
):
    from test_reader_access_owner_connection import WitnessConnection
    from mfdataindia.review.evidence_transport import decode_evidence

    w = worker()
    p = payload()
    e = environment(p)
    e["MFDATAINDIA_DSN"] = (
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=mfdataindia user=mfdata_admin password=SYNTHETIC_PRIVATE_DSN"
    )
    conn = WitnessConnection(p["grant_proposal"], fail="COMMIT" if failure == "COMMIT" else None)
    events = []

    def connect(**kwargs):
        events.append("connect")
        assert kwargs["connect_timeout"] == 5 and kwargs["autocommit"] is True
        assert kwargs["user"] == "mfdata_admin" and kwargs["port"] == "5432"
        return conn

    def close():
        events.append("close")
        if failure == "close":
            raise RuntimeError("SYNTHETIC_PRIVATE_DSN")

    conn.close = close
    monkeypatch.setattr(w.psycopg, "connect", connect)
    if failure:
        with pytest.raises(
            engine.ChangeFailure,
            match="COMMIT_OUTCOME_UNKNOWN" if failure == "COMMIT" else "COMMITTED_UNVERIFIED",
        ) as error:
            w.run_worker(e)
        assert "SYNTHETIC_PRIVATE_DSN" not in str(error.value)
        if failure == "COMMIT":
            assert "ROLLBACK" not in conn.calls
    else:
        output = w.run_worker(e)
        cert = decode_evidence(json.loads(output))
        assert cert["evidence"]["engine_receipt"]["stage"] == "COMMITTED_VERIFIED"
        approved = core.Approval.model_validate(core.approval_document(p))
        assert core.validate_result(approved, cert) == cert["evidence"]
        assert "SYNTHETIC_PRIVATE_DSN" not in output
    saved = list(conn.calls)
    with pytest.raises(ValueError):
        w.run_worker(e)
    assert events == ["connect", "close"] and conn.calls == saved


def test_active_timer_or_expired_window_denies_before_secret_lookup(monkeypatch):
    w = worker()
    e = environment(payload())

    class Guard(dict):
        def __getitem__(self, key):
            assert key != "MFDATAINDIA_DSN"
            return super().__getitem__(key)

    monkeypatch.setattr(w.signal, "getitimer", lambda _: (1.0, 0.0))
    with pytest.raises(ValueError):
        w.run_worker(Guard(e))
    monkeypatch.undo()
    p = payload()
    expired = deepcopy(p["grant_proposal"]["window"])
    expired.update(
        starts_at="2026-01-01T00:00:00Z",
        captured_at="2026-01-01T00:00:01Z",
        ends_at="2026-01-01T00:05:00Z",
    )
    p["grant_proposal"] = s.proposal(before(), p["grant_proposal"]["lock_witness"], expired)
    with pytest.raises(ValueError):
        worker().run_worker(Guard(environment(p)))


@pytest.mark.parametrize("field", ["program", "spec", "adapter", "controller", "engine", "profile"])
def test_actual_source_byte_tamper_denies_before_secret_lookup(monkeypatch, field):
    w = worker()
    e = environment(payload())
    targets = {
        "program": Path(w.__file__).resolve(),
        "spec": Path(s.__file__).resolve(),
        "adapter": Path(w.guarded.__file__).resolve(),
        "controller": Path(core.__file__).resolve(),
        "engine": Path(w.engine.__file__).resolve(),
        "profile": Path(w.profile.__file__).resolve(),
    }
    original = Path.read_bytes

    def read(path):
        raw = original(path)
        return raw + b"\n" if path.resolve() == targets[field] else raw

    monkeypatch.setattr(Path, "read_bytes", read)

    class Guard(dict):
        def __getitem__(self, key):
            assert key != "MFDATAINDIA_DSN"
            return super().__getitem__(key)

    with pytest.raises(ValueError):
        w.run_worker(Guard(e))


def test_decompression_bomb_and_descriptor_overflow_fail_closed():
    e = s.encode_payload(payload())
    d = s.parse(e[s.HEADER])
    raw = b"x" * (s.MAX_RAW_BYTES + 1)
    body = base64.b64encode(zlib.compress(raw, 9)).decode()
    assert len(body) <= s.CHUNK_BYTES
    e[s.PREFIX + "00"] = body
    d.update(
        raw_bytes=s.MAX_RAW_BYTES,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        encoded_bytes=len(body),
        encoded_sha256=hashlib.sha256(body.encode()).hexdigest(),
        count=1,
    )
    e[s.HEADER] = canonical(d)
    with pytest.raises(ValueError):
        s.decode_payload(e)
    e[s.HEADER] = " " * 4097
    with pytest.raises(ValueError):
        s.decode_payload(e)


def test_window_rechecked_at_first_wire_BEGIN_after_large_plan_validation(monkeypatch):
    from test_reader_access_owner_connection import WitnessConnection

    w = worker()
    p = payload()
    e = environment(p)
    e["MFDATAINDIA_DSN"] = (
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=mfdataindia user=mfdata_admin password=synthetic-only"
    )
    conn = WitnessConnection(p["grant_proposal"])
    conn.close = lambda: None
    monkeypatch.setattr(w.psycopg, "connect", lambda **_: conn)
    count = []

    def bounded(*_, **__):
        count.append(True)
        if len(count) == 4:
            raise ValueError("SYNTHETIC_WINDOW_EXPIRED_BEFORE_FIRST_SQL")

    monkeypatch.setattr(s, "live_window", bounded)
    with pytest.raises(engine.ChangeFailure, match="PRECOMMIT_ROLLBACK_UNCONFIRMED"):
        w.run_worker(e)
    assert len(count) == 4 and conn.calls == []


def test_complete_multi_chunk_input_roundtrip_without_omitting_catalog_rows():
    import random

    p = payload()
    state = before()
    rng = random.Random(7)
    role = deepcopy(state["roles"][0])
    for i in range(800):
        state["roles"].append(
            {
                **role,
                "oid": 50000 + i,
                "rolname": "synthetic_" + "".join(rng.choices("abcdef0123456789", k=50)),
            }
        )
    p["grant_proposal"] = s.proposal(
        state, p["grant_proposal"]["lock_witness"], p["grant_proposal"]["window"]
    )
    encoded = s.encode_payload(p)
    assert s.parse(encoded[s.HEADER])["count"] >= 2
    assert s.decode_payload(encoded) == p
