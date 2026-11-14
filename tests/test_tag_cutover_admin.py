from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_tag_cutover_admin import validate  # noqa: E402


MEMBER = "serviceAccount:tlws-mf-data-deploy-dev@trillionlane-dev.iam.gserviceaccount.com"
NOW = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)


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
