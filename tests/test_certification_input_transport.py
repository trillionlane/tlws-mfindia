"""Synthetic full-input and adversarial boundaries, never DEV acceptance."""

import base64
from copy import deepcopy
import hashlib
import json
import os
import zlib

import pytest

from mfdataindia.enrichment import book_certification, book_preservation_v2
from mfdataindia.review import certification_input_transport as t
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence
from test_book_preservation_v2 import seal
from test_sbi_certification import bound
from test_uti_v2_certification import fixture as old_fixture
from test_verified_review_rollout import manifest as rollout_manifest


def fixture():
    old, doc = old_fixture()
    return rollout_manifest(cohort=old.cohort, canary_code=old.cohort[0]), doc


def full_fixture(count=3220, *, high_entropy=True):
    manifest, doc = fixture()
    prior = doc["prior_projection"]
    prior["codes"] = list(range(1, count + 1))
    prior["untouched_codes"] = [c for c in prior["codes"] if c not in (200, 201)]
    for section in book_preservation_v2.SECTIONS:
        # Both are complete, valid synthetic maps. The low-entropy case tests
        # the maximum code count; the high-entropy case separately tests refusal
        # when the complete document cannot fit. Never trim a real map to fit.
        hashes = sorted((fingerprint([section, code]) if high_entropy
                         else f"{code:064x}") for code in prior["untouched_codes"])
        prior["payload_hashes"][section] = hashes
        prior["hashes"][section] = {
            "rows": len(hashes), "sha256": fingerprint([{"sha256": h} for h in hashes])}
        if section in prior["source_members"]:
            prior["source_members"][section] = {
                h: [fingerprint([h, "source"]) if high_entropy else h] for h in hashes}
    doc["prior_projection"] = seal(prior)
    return manifest, doc


def context(manifest, doc, phase="08-candidate"):
    return {"manifest_hash": manifest.seal, "phase": phase, "execution_id": doc["execution_id"]}


@pytest.fixture
def encoded():
    manifest, doc = fixture()
    ctx = context(manifest, doc)
    return manifest, doc, ctx, t.encode_environment(bound(manifest, doc), **ctx)


@pytest.mark.parametrize("count", [3220, 4096])
@pytest.mark.parametrize("phase", t.PHASES)
def test_complete_whole_population_exact_raw_roundtrip_and_original_expectation(count, phase):
    manifest, doc = full_fixture(count, high_entropy=(count == 3220))
    raw_env = {**bound(manifest, doc), "SECRET_SENTINEL": "not-to-be-compressed", "OTHER": "₹"}
    raw_env[t.RAW] = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    before = deepcopy(raw_env)
    process = dict(os.environ)
    ctx = context(manifest, doc, phase)
    submitted = t.encode_environment(raw_env, **ctx)
    assert raw_env == before and dict(os.environ) == process
    assert t.RAW not in submitted and submitted[t.HASH] == raw_env[t.HASH]
    assert all(len(v.encode()) <= t.MAX_VALUE_BYTES for v in submitted.values())
    assert "not-to-be-compressed" not in submitted[t.HEADER]
    # Env serialization order is not evidence; numeric index order is.
    logical = t.decode_environment(list(reversed(list(submitted.items()))), **ctx)
    assert logical == raw_env and logical[t.RAW].encode() == before[t.RAW].encode()
    assert book_certification.expectation(manifest, logical) == doc
    assert len(json.loads(logical[t.RAW])["prior_projection"]["codes"]) == count
    assert not any(k.startswith(t.PREFIX) for k in logical) and t.HEADER not in logical
    assert t.encode_environment(raw_env, **ctx) == submitted


def test_valid_high_entropy_maximum_population_still_refuses_oversize_without_sampling():
    manifest, doc = full_fixture(4096)
    env = bound(manifest, doc)
    before = deepcopy(env)
    assert book_certification.expectation(manifest, env) == doc
    with pytest.raises(ValueError, match="encoded byte budget"):
        t.encode_environment(env, **context(manifest, doc))
    assert env == before and len(doc["prior_projection"]["codes"]) == 4096


@pytest.mark.parametrize("phase", t.PHASES)
def test_shared_entry_calls_unchanged_certifier_with_exact_flags_not_process_environment(
    encoded, monkeypatch, phase
):
    manifest, doc, _, _ = encoded
    ctx = context(manifest, doc, phase)
    raw_env = bound(manifest, doc)
    submitted = t.encode_environment(raw_env, **ctx)
    before = dict(os.environ)
    marker = object()

    def certify(actual_manifest, environment, **flags):
        assert actual_manifest is manifest
        assert environment == raw_env and environment is not os.environ
        assert book_certification.expectation(manifest, environment) == doc
        assert flags == dict(zip(("active", "recovery"), t.PHASES[phase]))
        assert dict(os.environ) == before
        return marker

    monkeypatch.setattr(book_certification, "certify", certify)
    assert t.certify_from_environment(manifest, submitted, phase=phase,
                                      execution_id=doc["execution_id"]) is marker
    assert dict(os.environ) == before


@pytest.mark.parametrize("field,value", [
    ("format", "v0"), ("codec", "gzip"), ("count", True), ("count", 0), ("count", 65),
    ("raw_bytes", True), ("raw_bytes", 0), ("raw_bytes", t.MAX_RAW_BYTES + 1),
    ("encoded_bytes", True), ("encoded_bytes", 0),
    ("encoded_bytes", t.MAX_ENCODED_BYTES + 4), ("encoded_bytes", 3),
    ("raw_sha256", "A" * 64), ("raw_sha256", "0" * 64),
    ("encoded_sha256", "0" * 64), ("manifest_hash", "0" * 64),
    ("phase", "11-active"), ("execution_id", "other"),
    ("certification_hash", "0" * 64), ("extra", "unapproved"),
])
def test_bad_descriptor_fails_before_any_certifier_call(encoded, monkeypatch, field, value):
    manifest, doc, ctx, env = encoded
    header = json.loads(env[t.HEADER])
    header[field] = value
    env[t.HEADER] = json.dumps(header)
    calls = []
    monkeypatch.setattr(book_certification, "certify", lambda *a, **kw: calls.append(True))
    with pytest.raises(ValueError):
        t.certify_from_environment(manifest, env, phase=ctx["phase"], execution_id=doc["execution_id"])
    assert not calls


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate", "mixed", "wrong_index",
                                  "tampered", "short", "missing_header", "missing_hash",
                                  "header_duplicate", "header_missing", "header_oversize"])
def test_malformed_environment_is_never_partial_evidence(encoded, defect):
    _, _, ctx, env = encoded
    key = t.PREFIX + "00"
    if defect == "missing":
        del env[key]
    elif defect == "extra":
        env[t.PREFIX + "64"] = env[key]
    elif defect == "duplicate":
        env = [*env.items(), (key, env[key])]
    elif defect == "mixed":
        env[t.RAW] = "{}"
    elif defect == "wrong_index":
        env[t.PREFIX + "0"] = env.pop(key)
    elif defect == "tampered":
        env[key] = "A" + env[key][1:]
    elif defect == "short":
        env[key] = env[key][:-1]
    elif defect == "missing_header":
        del env[t.HEADER]
    elif defect == "missing_hash":
        del env[t.HASH]
    elif defect == "header_duplicate":
        env[t.HEADER] = env[t.HEADER][:-1] + ',"format":"duplicate"}'
    elif defect == "header_missing":
        header = json.loads(env[t.HEADER])
        del header["format"]
        env[t.HEADER] = json.dumps(header)
    else:
        env[t.HEADER] += " " * t.MAX_DESCRIPTOR_BYTES
    with pytest.raises(ValueError):
        t.decode_environment(env, **ctx)


def repack(env, raw, *, compressed=None, body=None):
    """Attacker can reseal outer hashes; inner binding and parser still required."""
    env = dict(env)
    header = json.loads(env[t.HEADER])
    body = body if body is not None else base64.b64encode(
        zlib.compress(raw) if compressed is None else compressed).decode()
    header.update(raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest(),
                  encoded_bytes=len(body), encoded_sha256=hashlib.sha256(body.encode()).hexdigest(),
                  count=(len(body) + t.CHUNK_BYTES - 1) // t.CHUNK_BYTES)
    env = {k: v for k, v in env.items() if not k.startswith(t.PREFIX)}
    env[t.HEADER] = json.dumps(header)
    env.update({t.PREFIX + f"{i:02d}": body[i*t.CHUNK_BYTES:(i+1)*t.CHUNK_BYTES]
                for i in range(header["count"])})
    return env


@pytest.mark.parametrize("defect", ["trailing", "concatenated", "truncated", "bomb", "base64",
                                  "padding", "utf8", "utf16", "json", "duplicate", "nan",
                                  "list", "inner_hash", "inner_execution"])
def test_compressed_parser_and_inner_binding_even_with_resealed_outer_header(encoded, defect):
    _, doc, ctx, env = encoded
    raw = json.dumps(doc).encode()
    compressed = zlib.compress(raw)
    kwargs = {}
    if defect == "trailing":
        kwargs["compressed"] = compressed + b"trailing"
    elif defect == "concatenated":
        kwargs["compressed"] = compressed + zlib.compress(b"{}")
    elif defect == "truncated":
        kwargs["compressed"] = compressed[:-1]
    elif defect == "bomb":
        raw = b"x" * (t.MAX_RAW_BYTES + 1)
    elif defect in ("base64", "padding"):
        body = base64.b64encode(compressed).decode()
        kwargs["body"] = ("!" + body[1:]) if defect == "base64" else body + "===="
    elif defect == "utf8":
        raw = b'{"value":"\xff"}'
    elif defect == "utf16":
        raw = json.dumps(doc).encode("utf-16")
    elif defect == "json":
        raw = b"{"
    elif defect == "duplicate":
        raw = raw[:-1] + b',"execution_id":"duplicate"}'
    elif defect == "nan":
        raw = b'{"value":NaN}'
    elif defect == "list":
        raw = b"[]"
    elif defect == "inner_hash":
        raw = json.dumps({**doc, "prior_core_values": {}}).encode()
    else:
        raw = json.dumps({**doc, "execution_id": "substituted"}).encode()
    with pytest.raises(ValueError):
        t.decode_environment(repack(env, raw, **kwargs), **ctx)


@pytest.mark.parametrize("defect", ["raw", "encoded", "env", "value", "argument", "argv",
                                  "nul", "bad_name", "nonstring", "duplicate", "surrogate"])
def test_encoder_or_complete_submission_budget_fails_closed(encoded, monkeypatch, defect):
    manifest, doc, ctx, env = encoded
    raw_env = bound(manifest, doc)
    args = []
    if defect == "raw":
        raw_env[t.RAW] = "x" * (t.MAX_RAW_BYTES + 1)
    elif defect == "encoded":
        monkeypatch.setattr(t, "MAX_ENCODED_BYTES", 4)
    elif defect == "env":
        env.update({f"EXTRA_{i}": "x" * 32768 for i in range(49)})
    elif defect == "value":
        env["OTHER"] = "x" * 32769
    elif defect == "argument":
        args = ["x" * t.MAX_ARGUMENT_BYTES]
    elif defect == "argv":
        args = ["x" * 70000] * 2
    elif defect == "nul":
        env["OTHER"] = "private\0value"
    elif defect == "bad_name":
        env["BAD=NAME"] = "x"
    elif defect == "nonstring":
        env["OTHER"] = 1
    elif defect == "duplicate":
        env = [*env.items(), (t.HASH, env[t.HASH])]
    else:
        env["OTHER"] = "\ud800"
    with pytest.raises(ValueError):
        if defect in ("raw", "encoded"):
            t.encode_environment(raw_env, **ctx)
        else:
            t.submission_budget(env, args)


def test_chunks_cannot_be_swapped_or_mixed_between_documents():
    manifest, doc = full_fixture()
    ctx = context(manifest, doc)
    env = t.encode_environment(bound(manifest, doc), **ctx)
    env[t.PREFIX + "00"], env[t.PREFIX + "01"] = env[t.PREFIX + "01"], env[t.PREFIX + "00"]
    with pytest.raises(ValueError):
        t.decode_environment(env, **ctx)


@pytest.mark.parametrize("phase", t.PHASES)
def test_future_operator_shared_submission_is_pure_and_has_no_oversized_argv(phase):
    manifest, doc = fixture()
    env = {**bound(manifest, doc), "REVIEW_MANIFEST": manifest.model_dump_json()}
    before = deepcopy(env)
    submitted, arguments, budget = t.prepare_submission(manifest, env, phase=phase,
                                                        execution_id=doc["execution_id"])
    assert env == before and t.RAW not in submitted
    assert budget["argv_bytes"] < t.MAX_ARGUMENT_BYTES
    assert t.RAW not in arguments[1]
    compile(t.worker_program(**context(manifest, doc, phase)), "<future-worker>", "exec")
    assert t.decode_environment(submitted, **context(manifest, doc, phase)) == env


@pytest.mark.parametrize("defect", ["none", "extra", "missing", "duplicate", "secret", "argument",
                                  "value", "secret_value", "secret_shape"])
def test_full_immutable_execution_inputs_not_subset_or_order_inference(encoded, defect):
    _, _, _, env = encoded
    secret = {"name": "MFDATAINDIA_DSN", "valueFrom": {
        "secretKeyRef": {"name": "synthetic-reader-ref", "key": "1"}}}
    records = [{"name": k, "value": v} for k, v in env.items()] + [secret]
    actual = {"args": ["-c", "literal"], "env": deepcopy(records[::-1])}
    if defect == "extra":
        actual["env"].append({"name": "EXTRA", "value": "changed"})
    elif defect == "missing":
        actual["env"].pop()
    elif defect == "duplicate":
        actual["env"].append(deepcopy(actual["env"][0]))
    elif defect == "secret":
        actual["env"][0]["valueFrom"]["secretKeyRef"]["key"] = "2"
    elif defect == "argument":
        actual["args"] = ["-c", "different"]
    elif defect == "value":
        actual["env"][-1]["value"] = "tampered"
    elif defect == "secret_value":
        actual["env"][0] = {"name": "MFDATAINDIA_DSN", "value": "not-an-immutable-ref"}
    elif defect == "secret_shape":
        actual["env"][0]["valueFrom"]["extra"] = True
    if defect == "none":
        t.require_execution_inputs(actual, environment=env, arguments=["-c", "literal"],
                                   secret_entries=[secret])
    else:
        with pytest.raises(ValueError):
            t.require_execution_inputs(actual, environment=env, arguments=["-c", "literal"],
                                       secret_entries=[secret])


@pytest.mark.parametrize("task_request", [[], {"bad": float("nan")}, {"bad": object()},
                                   {"bad": "\ud800"}, {"large": "x" * t.MAX_REQUEST_BYTES}])
def test_full_serialized_request_is_bounded_before_any_dispatch(task_request):
    with pytest.raises(ValueError):
        t.preflight_request(task_request)


def test_request_digest_covers_all_fields_and_environment_count_is_bounded():
    one = t.preflight_request({"task": {"image": "synthetic", "env": []}})
    two = t.preflight_request({"task": {"image": "different", "env": []}})
    assert one["request_sha256"] != two["request_sha256"]
    with pytest.raises(ValueError):
        t.submission_budget({f"VAR_{i}": "x" for i in range(1001)})


def test_small_declared_length_cannot_hide_a_zlib_bomb(encoded):
    _, doc, ctx, env = encoded
    malicious = repack(env, json.dumps(doc).encode(),
                       compressed=zlib.compress(b"x" * (t.MAX_RAW_BYTES + 1)))
    with pytest.raises(ValueError, match="integrity differs"):
        t.decode_environment(malicious, **ctx)


@pytest.mark.parametrize("phase", t.PHASES)
def test_generated_worker_executes_shared_entry_and_unchanged_stdout_renderer(encoded, monkeypatch, capsys, phase):
    manifest, doc, _, _ = encoded
    env = {**bound(manifest, doc), "REVIEW_MANIFEST": manifest.model_dump_json()}
    submitted, _, _ = t.prepare_submission(manifest, env, phase=phase,
                                          execution_id=doc["execution_id"])
    certificate = seal_evidence(manifest, "synthetic-worker-only", {"no_live_coverage": True})
    calls = []

    def certify(m, logical, **flags):
        assert m == manifest and logical == env
        assert flags == dict(zip(("active", "recovery"), t.PHASES[phase]))
        calls.append(True)
        return certificate

    monkeypatch.setattr(book_certification, "certify", certify)
    monkeypatch.setattr(os, "environ", submitted)
    before = deepcopy(submitted)
    exec(compile(t.worker_program(**context(manifest, doc, phase)), "<worker>", "exec"), {})
    assert json.loads(capsys.readouterr().out) == certificate
    assert calls == [True] and submitted == before and t.RAW not in submitted


def test_pure_preflight_rejects_incomplete_financial_reference_before_dispatch(encoded):
    manifest, doc, _, _ = encoded
    doc.pop("prior_projection")
    env = {**bound(manifest, doc), "REVIEW_MANIFEST": manifest.model_dump_json()}
    with pytest.raises(ValueError):
        t.prepare_submission(manifest, env, phase="08-candidate", execution_id=doc["execution_id"])
