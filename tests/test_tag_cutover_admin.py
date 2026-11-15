from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_tag_cutover_admin import admission_state, reconcile, validate  # noqa: E402


MEMBER = "serviceAccount:tlws-mf-data-deploy-dev@trillionlane-dev.iam.gserviceaccount.com"
NOW = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)
INGEST = "serviceAccount:tlws-mf-data-ingest-dev@trillionlane-dev.iam.gserviceaccount.com"


def policy(expression: str, *, duplicate: bool = False) -> dict[str, object]:
    grant = {
        "role": "roles/secretmanager.admin",
        "members": [MEMBER],
        "condition": {
            "title": "mfdata-tag-writer-cutover",
            "expression": expression,
        },
    }
    return {"bindings": [grant, grant.copy()] if duplicate else [grant]}


def test_exact_time_only_cutover_window_is_accepted() -> None:
    expiry = NOW + timedelta(minutes=90)
    expression = f"request.time < timestamp('{expiry:%Y-%m-%dT%H:%M:%SZ}')"

    assert validate(policy(expression), MEMBER, NOW) == expiry


@pytest.mark.parametrize(
    "expression",
    [
        "request.time < timestamp('2099-01-01T00:00:00Z')",
        "request.time < timestamp('2026-11-14T19:30:00Z') || true",
        "request.time < timestamp('2026-11-14T17:59:59Z')",
    ],
)
def test_unbounded_widened_or_expired_cutover_is_rejected(expression: str) -> None:
    with pytest.raises(ValueError):
        validate(policy(expression), MEMBER, NOW)


def test_multiple_deployer_admin_bindings_are_rejected() -> None:
    expression = "request.time < timestamp('2026-11-14T19:30:00Z')"

    with pytest.raises(ValueError):
        validate(policy(expression, duplicate=True), MEMBER, NOW)


def test_shared_deployer_admin_binding_is_reconciled_to_deployer_only() -> None:
    expression = "request.time < timestamp('2026-11-14T19:30:00Z')"
    shared = policy(expression)
    shared["bindings"][0]["members"].append("serviceAccount:operator@example.test")

    reconciled, changed = reconcile(shared, MEMBER, NOW)

    assert changed is True
    validate(reconciled, MEMBER, NOW)
    deploy_grant = next(
        binding
        for binding in reconciled["bindings"]
        if binding.get("role") == "roles/secretmanager.admin"
        and MEMBER in binding.get("members", [])
    )
    assert deploy_grant["members"] == [MEMBER]


def test_provisioning_twice_reuses_one_valid_grant_for_admission() -> None:
    initial = {
        "version": 3,
        "etag": "synthetic-etag",
        "bindings": [
            {
                "role": "roles/secretmanager.admin",
                "members": ["serviceAccount:security-operator@example.test"],
            },
            {
                "role": "roles/secretmanager.secretAccessor",
                "members": ["serviceAccount:tag-writer@example.test"],
            },
        ],
    }

    first, first_changed = reconcile(initial, MEMBER, NOW)
    second, second_changed = reconcile(first, MEMBER, NOW + timedelta(minutes=5))

    assert first_changed is True
    assert second_changed is False
    assert second == first
    assert validate(second, MEMBER, NOW + timedelta(minutes=5)) == NOW + timedelta(
        hours=2
    )
    assert second["etag"] == "synthetic-etag"
    assert second["bindings"][0]["members"] == [
        "serviceAccount:security-operator@example.test"
    ]


def test_expired_grant_is_replaced_without_unrelated_binding_loss() -> None:
    expired = policy("request.time < timestamp('2026-11-14T17:00:00Z')")
    expired["etag"] = "synthetic-etag"
    expired["bindings"].append(
        {
            "role": "roles/secretmanager.secretAccessor",
            "members": ["serviceAccount:tag-writer@example.test"],
        }
    )

    replaced, changed = reconcile(expired, MEMBER, NOW)

    assert changed is True
    assert validate(replaced, MEMBER, NOW) == NOW + timedelta(hours=2)
    assert replaced["etag"] == "synthetic-etag"
    assert any(
        binding.get("role") == "roles/secretmanager.secretAccessor"
        for binding in replaced["bindings"]
    )


def test_partial_cutover_with_expired_deployer_binding_fails_admission() -> None:
    partial = policy("request.time < timestamp('2026-11-14T17:00:00Z')")

    with pytest.raises(ValueError):
        admission_state(partial, MEMBER, INGEST, NOW, timedelta(hours=1))


def test_partial_cutover_with_valid_deployer_binding_requires_permission() -> None:
    partial = policy("request.time < timestamp('2026-11-14T19:30:00Z')")

    assert admission_state(partial, MEMBER, INGEST, NOW, timedelta(hours=1)) == "REQUIRED"


def test_completed_cutover_without_deployer_binding_needs_no_permission() -> None:
    completed = {
        "bindings": [
            {
                "role": "roles/secretmanager.secretAccessor",
                "members": ["serviceAccount:tag-writer@example.test"],
            }
        ]
    }

    assert admission_state(completed, MEMBER, INGEST, NOW, timedelta(hours=1)) == "NONE"
