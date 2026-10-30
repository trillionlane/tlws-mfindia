"""Provider-free exact no-freeze certificate and predecessor mode contracts."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from mfdataindia.review import broad_reader_direct_capacity as capacity
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_grant as direct
from mfdataindia.review import broad_reader_direct_rollout_controller as rollout
from mfdataindia.review.policy import canonical, fingerprint
from test_broad_reader_direct_rollout_controller import _approval, SOURCE


def _window():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {"starts_at": (now - timedelta(seconds=1)).isoformat(),
            "ends_at": (now + timedelta(minutes=5)).isoformat(),
            "exclusive_administration": False, "data_schema_custody": False,
            "future_owner_provisioning": False,
            "dev_no_freeze_risk_accepted": True}


def _prepared(bound):
    return {"payload_sha256": "a" * 64,
            "task": {"containers": [{"env": []}]}}


def _certificate(kind, evidence, prepared):
    return spec.seal_certificate(payload_sha256=prepared["payload_sha256"],
                                 stage=rollout.STAGES[kind], evidence=evidence)


def test_grant_certificate_accepts_only_exact_no_freeze_marker(monkeypatch):
    marker = direct.custody_marker(_window())
    before = {"plan": {"full_before_sha256": "b" * 64,
                       "full_after_sha256": "c" * 64,
                       "delta": {"relations": []},
                       "relation_scope": {"materialized_unlocked": []}}}
    bound = {"window": _window(), "before_snapshot": before,
             "packaged_source": {"synthetic": True}}
    prepared = _prepared(bound)
    monkeypatch.setattr(spec, "payload_from_variables", lambda _: bound)
    monkeypatch.setattr(capacity, "prove_capacity", lambda *_a, **_k: {"bounded": True})
    monkeypatch.setattr(rollout.witness, "verify_phases",
                        lambda *_a, **_k: {"baseline": [], "tables": [], "views": [],
                                            "target_pid": 1, "observer_pid": 2,
                                            "database_oid": 3})
    lock = {"baseline": [], "tables": [], "views": [],
            "target_pid": 1, "observer_pid": 2, "database_oid": 3}
    receipt = {"format": "mf-broad-reader-direct-transaction-receipt-v2-no-freeze",
               "stage": "COMMITTED_STATE_VERIFIED",
               "request_sha256": prepared["payload_sha256"],
               "before_sha256": before["plan"]["full_before_sha256"],
               "after_sha256": before["plan"]["full_after_sha256"],
               "source_sha256": fingerprint(bound["packaged_source"]),
               "lock_proof": lock,
               "pregrant_equality": {"before_sha256": fingerprint(before),
                                     "after_sha256": fingerprint(before)},
               "postgrant_proof": {"planned_after_sha256": "c" * 64},
               "delta": before["plan"]["delta"],
               "materialized_unlocked": [],
               "postcommit_permanence_guarantee": False,
               "authenticated_reader_verified": False,
               "ti_verified": False, "permission_enabled": False,
               "custody": marker}
    evidence = {"transaction_receipt": receipt, "capacity_proof": {"bounded": True}}
    assert rollout._evidence("grant", _certificate("grant", evidence, prepared),
                             prepared) == evidence
    for variant in ("missing", "forged"):
        bad = deepcopy(evidence)
        if variant == "missing":
            bad["transaction_receipt"].pop("custody")
        else:
            bad["transaction_receipt"]["custody"] = {**marker, "mode": "CUSTODY_V1"}
        with pytest.raises(rollout.DeliveryError,
                           match="INCOMPLETE_DIRECT_GRANT_CERTIFICATE"):
            rollout._evidence("grant", _certificate("grant", bad, prepared), prepared)


def test_reader_certificate_accepts_only_exact_no_freeze_marker(monkeypatch):
    marker = direct.custody_marker(_window())
    bound = {"window": _window(), "grant_receipt_sha256": "d" * 64,
             "planned_after": {"synthetic": True}}
    prepared = _prepared(bound)
    monkeypatch.setattr(spec, "payload_from_variables", lambda _: bound)
    checked = {"context": {}, "namespaces": [], "relations": [], "columns": [],
               "permission_enabled": False, "ti_verified": False}
    monkeypatch.setattr(rollout.reader_check, "validate",
                        lambda *_a, **_k: checked)
    result = {**checked, "representative_select_completed": True,
              "custody": marker}
    evidence = {"reader_result": result, "grant_receipt_sha256": "d" * 64}
    assert rollout._evidence("reader", _certificate("reader", evidence, prepared),
                             prepared) == evidence
    for variant in ("missing", "forged"):
        bad = deepcopy(evidence)
        if variant == "missing":
            bad["reader_result"].pop("custody")
        else:
            bad["reader_result"]["custody"] = {**marker, "mode": "CUSTODY_V1"}
        with pytest.raises(rollout.DeliveryError,
                           match="UNMATCHED_AUTHENTICATED_READER_CAPABILITIES"):
            rollout._evidence("reader", _certificate("reader", bad, prepared),
                              prepared)


def test_no_freeze_grant_to_reader_predecessor_rejects_mixed_marker(
        monkeypatch, tmp_path):
    monkeypatch.setattr(rollout, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    marker = direct.custody_marker(_window())
    capture, _, _ = _approval(tmp_path, no_freeze=True)
    prior = deepcopy(capture)
    prior["kind"] = "grant"
    prior["root"] = str(tmp_path / (rollout.ROOT_NAMES["grant"] + "abcdef"))
    root = Path(prior["root"])
    root.mkdir()
    prior["request_sha256"] = "a" * 64
    prior["payload"] = {"window": _window(),
                        "packaged_source": capture["payload"]["packaged_source"],
                        "git_sha": capture["payload"]["git_sha"],
                        "image_binding": capture["image_binding"],
                        "before_snapshot": {"plan": {"after": {"synthetic": True}}}}
    (root / "approval.json").write_bytes((canonical(prior) + "\n").encode())
    receipt = {"stage": "COMMITTED_STATE_VERIFIED", "custody": marker}
    result = {"format": "mf-broad-reader-direct-delivery-completion-v1",
              "kind": "grant", "approval_sha256": fingerprint(prior),
              "request_sha256": "a" * 64,
              "job": {}, "execution": {}, "certificate": {},
              "evidence": {"transaction_receipt": receipt},
              "protected_sha256": prior["protected_hash"],
              "permission_enabled": False, "ti_verified": False}
    (root / "result.json").write_bytes((canonical(result) + "\n").encode())
    monkeypatch.setattr(rollout, "validate_approval",
                        lambda *_a, **_k: {"request_sha256": "a" * 64})
    monkeypatch.setattr(rollout, "_evidence",
                        lambda kind, *_a: {"transaction_receipt": receipt}
                        if kind == "grant" else pytest.fail("unexpected kind"))
    current = deepcopy(prior)
    current["kind"] = "reader"
    current["root"] = str(tmp_path / (rollout.ROOT_NAMES["reader"] + "abcdef"))
    current["payload"] = {"window": _window(),
                          "packaged_source": prior["payload"]["packaged_source"],
                          "git_sha": prior["payload"]["git_sha"],
                          "image_binding": prior["image_binding"],
                          "grant_receipt_sha256": fingerprint(receipt),
                          "planned_after": {"synthetic": True},
                          "grant_custody": marker}
    current["predecessor"] = {"root": str(root), "result_sha256": hashlib.sha256(
        (canonical(result) + "\n").encode()).hexdigest()}
    assert rollout.predecessor(current, source_root=SOURCE)["completion"] == result
    for variant in ("missing", "forged", "mixed"):
        bad = deepcopy(current)
        if variant == "missing":
            bad["payload"].pop("grant_custody")
        elif variant == "forged":
            bad["payload"]["grant_custody"] = {**marker, "mode": "CUSTODY_V1"}
        else:
            bad["payload"]["window"].pop("dev_no_freeze_risk_accepted")
            bad["payload"]["window"].update(
                exclusive_administration=True, data_schema_custody=True,
                future_owner_provisioning=True)
        with pytest.raises(rollout.DeliveryError):
            rollout.predecessor(bad, source_root=SOURCE)
