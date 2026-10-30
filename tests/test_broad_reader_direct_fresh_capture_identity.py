"""Provider-free proof that the failed 13:15 capture identity is never reused."""

from pathlib import Path

import pytest

from mfdataindia.review import broad_reader_direct_delivery_spec as spec


OLD_JOB = "mf-reader-direct-capture-261029a"
NEW_JOB = "mf-reader-direct-capture-261030b"
OLD_ROOT = "mf-reader-direct-capture-20261029-Freeze1315B"
NEW_ROOT = "mf-reader-direct-capture-20261030-NowDevB"


def test_fresh_capture_identity_and_old_consumed_job_are_disjoint():
    capture = spec.ROLES["capture"]
    assert capture.rollout_id == "dev-bread-direct-cap-261030b"
    assert capture.job == NEW_JOB
    assert spec.ROOT_PREFIXES["capture"] == "mf-reader-direct-capture-20261030-"
    assert OLD_JOB in spec.OLD_CONSUMED_JOBS
    assert capture.job not in spec.OLD_CONSUMED_JOBS
    assert spec.ROLES["grant"].job == "mf-reader-direct-grant-261029a"
    assert spec.ROLES["reader"].job == "mf-reader-direct-check-261029a"


def test_new_root_and_intent_bind_new_job_not_consumed_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    payload = {"create_only_intent_sha256": "0" * 64,
               "synthetic_provider_free": True}
    root = str(tmp_path / NEW_ROOT)
    assert not Path(root).exists()
    preimage, digest = spec.intent_preimage("capture", root, payload)
    assert preimage["job"] == NEW_JOB and preimage["root"] == root
    payload["create_only_intent_sha256"] = digest
    assert spec.require_intent("capture", root, payload) == (preimage, digest)
    for bad_root, kind in ((str(tmp_path / OLD_ROOT), "capture"),
                           (root, "grant")):
        with pytest.raises(spec.DeliverySpecError,
                           match="EXACT_NEW_DIRECT_EVIDENCE_ROOT_REQUIRED"):
            spec.intent_preimage(kind, bad_root, payload)


def test_old_capture_job_is_rejected_before_request_or_provider(monkeypatch):
    old = spec.Role("dev-bread-direct-cap-261029a", OLD_JOB,
                    spec.ROLES["capture"].worker,
                    spec.ROLES["capture"].service_account,
                    spec.ROLES["capture"].secret)
    with monkeypatch.context() as context:
        context.setitem(spec.ROLES, "capture", old)
        with pytest.raises(spec.DeliverySpecError,
                           match="CONSUMED_OR_ALIASED_JOB_IDENTITY"):
            spec.request("capture", root="", final_git_sha="", image={},
                         payload={}, worker_sha256="", controller_sha256="",
                         spec_sha256="", source_sha256="")
