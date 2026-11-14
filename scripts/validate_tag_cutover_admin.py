#!/usr/bin/env python3
"""Validate the one-time exact-secret deployer IAM condition."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import sys


TITLE = "mfdata-tag-writer-cutover"
ROLE = "roles/secretmanager.admin"
MAX_WINDOW = timedelta(hours=2)
EXPRESSION = re.compile(
    r"^request\.time < timestamp\('"
    r"(?P<expiry>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)"
    r"'\)$"
)


def validate(policy: dict[str, object], member: str, now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    bindings = policy.get("bindings")
    if not isinstance(bindings, list):
        raise ValueError("policy bindings are unavailable")
    grants = [
        binding
        for binding in bindings
        if isinstance(binding, dict)
        and binding.get("role") == ROLE
        and member in binding.get("members", [])
    ]
    if len(grants) != 1:
        raise ValueError("expected one deployer Secret Manager Admin binding")
    condition = grants[0].get("condition")
    if not isinstance(condition, dict) or condition.get("title") != TITLE:
        raise ValueError("unexpected deployer condition")
    expression = condition.get("expression")
    if not isinstance(expression, str) or (match := EXPRESSION.fullmatch(expression)) is None:
        raise ValueError("cutover condition must be one exact UTC deadline")
    expiry = datetime.strptime(match.group("expiry"), "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    remaining = expiry - now.astimezone(timezone.utc)
    if remaining <= timedelta(0) or remaining > MAX_WINDOW:
        raise ValueError("cutover deadline is expired or exceeds two hours")
    return expiry


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_tag_cutover_admin.py MEMBER")
    policy = json.load(sys.stdin)
    validate(policy, sys.argv[1], datetime.now(timezone.utc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
