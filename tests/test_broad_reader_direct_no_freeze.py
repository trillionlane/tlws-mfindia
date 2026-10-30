"""Explicit no-freeze DEV admission; no provider, secret or database access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from mfdataindia.review import broad_reader_direct_grant as direct
from mfdataindia.review import broad_reader_policy as policy
from test_broad_reader_direct_grant import _case, ROOT


def _window():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "starts_at": (now - timedelta(seconds=1)).isoformat(),
        "ends_at": (now + timedelta(minutes=5)).isoformat(),
        "exclusive_administration": False,
        "data_schema_custody": False,
        "future_owner_provisioning": False,
        "dev_no_freeze_risk_accepted": True,
    }


def test_direct_only_explicit_no_freeze_never_admits_historical_policy():
    window = _window()
    direct.direct_window(window)
    direct.direct_live(window, remaining=15)
    assert direct.custody_marker(window) == {
        "mode": "DEV_NO_FREEZE_V1",
        "exclusive_administration": False,
        "data_schema_custody": False,
        "future_owner_provisioning": False,
        "dev_no_freeze_risk_accepted": True,
        "postcommit_permanence_guarantee": False,
    }
    with pytest.raises(policy.PolicyError):
        policy._window(window)
    with pytest.raises(policy.PolicyError):
        policy._live(window)


@pytest.mark.parametrize("mutation", (
    lambda w: w.pop("dev_no_freeze_risk_accepted"),
    lambda w: w.update(dev_no_freeze_risk_accepted=False),
    lambda w: w.update(data_schema_custody=True),
    lambda w: w.update(exclusive_administration=0),
    lambda w: w.update(extra=True),
    lambda w: w.update(starts_at="2020-01-01T00:00:00+00:00"),
    lambda w: w.update(ends_at="2026-01-01T00:00:00+00:00"),
))
def test_invalid_no_freeze_shape_or_time_is_fail_closed(mutation):
    window = _window()
    mutation(window)
    with pytest.raises(policy.PolicyError):
        direct.direct_live(window)


def test_exact_no_freeze_permit_and_receipt_retain_every_lock_check(monkeypatch):
    target, observer, permit = _case(monkeypatch)
    permit.document["window"] = _window()
    marker = direct.custody_marker(permit.document["window"])
    permit.document["capture_custody"] = marker
    permit.binding_sha256 = policy.fingerprint(permit.document)
    receipt = direct.run(target, observer, permit, source_root=ROOT)
    assert receipt["format"] == "mf-broad-reader-direct-transaction-receipt-v2-no-freeze"
    assert receipt["custody"] == marker
    assert receipt["postcommit_permanence_guarantee"] is False
    assert receipt["lock_proof"] == {"observed": True}
    assert [sql for sql, _ in target.statements].count("COMMIT") == 1
    assert permit.used is True


def test_mixed_or_unbound_no_freeze_permit_stops_before_sql(monkeypatch):
    for variant in ("missing", "mixed", "false_risk"):
        target, observer, permit = _case(monkeypatch)
        permit.document["window"] = _window()
        marker = direct.custody_marker(permit.document["window"])
        if variant == "mixed":
            permit.document["capture_custody"] = {**marker, "mode": "CUSTODY_V1"}
        elif variant == "false_risk":
            permit.document["capture_custody"] = deepcopy(marker)
            permit.document["window"]["dev_no_freeze_risk_accepted"] = False
        permit.binding_sha256 = policy.fingerprint(permit.document)
        with pytest.raises(policy.PolicyError):
            direct.run(target, observer, permit, source_root=ROOT)
        assert target.statements == observer.statements == []
