#!/usr/bin/env python3
"""Validate the one-time exact-secret deployer IAM condition."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from copy import deepcopy
import argparse
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


def validate(
    policy: dict[str, object],
    member: str,
    now: datetime,
    minimum_remaining: timedelta = timedelta(0),
) -> datetime:
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
    if grants[0].get("members") != [member]:
        raise ValueError("deployer cutover binding must contain only the deployer")
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
    if remaining <= timedelta(0) or remaining < minimum_remaining or remaining > MAX_WINDOW:
        raise ValueError("cutover deadline is expired, too short, or exceeds two hours")
    return expiry


def reconcile(
    policy: dict[str, object],
    member: str,
    now: datetime,
    minimum_remaining: timedelta = timedelta(0),
) -> tuple[dict[str, object], bool]:
    try:
        validate(policy, member, now, minimum_remaining)
    except ValueError:
        pass
    else:
        return deepcopy(policy), False

    result = deepcopy(policy)
    bindings = result.get("bindings")
    if not isinstance(bindings, list):
        bindings = []
    reconciled: list[object] = []
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("role") != ROLE:
            reconciled.append(binding)
            continue
        members = binding.get("members")
        if not isinstance(members, list) or member not in members:
            reconciled.append(binding)
            continue
        retained = [value for value in members if value != member]
        if retained:
            binding["members"] = retained
            reconciled.append(binding)
    expiry = now.astimezone(timezone.utc) + MAX_WINDOW
    reconciled.append(
        {
            "role": ROLE,
            "members": [member],
            "condition": {
                "title": TITLE,
                "description": "Expires after the one-time legacy NAV cutover",
                "expression": f"request.time < timestamp('{expiry:%Y-%m-%dT%H:%M:%SZ}')",
            },
        }
    )
    result["bindings"] = reconciled
    result["version"] = max(3, int(result.get("version", 0)))
    validate(result, member, now, minimum_remaining)
    return result, True


def admission_state(
    policy: dict[str, object],
    deploy_member: str,
    ingest_member: str,
    now: datetime,
    minimum_remaining: timedelta,
) -> str:
    bindings = policy.get("bindings")
    if not isinstance(bindings, list):
        raise ValueError("policy bindings are unavailable")
    ingest_access = any(
        isinstance(binding, dict)
        and binding.get("role") == "roles/secretmanager.secretAccessor"
        and ingest_member in binding.get("members", [])
        for binding in bindings
    )
    deploy_admin = any(
        isinstance(binding, dict)
        and binding.get("role") == ROLE
        and deploy_member in binding.get("members", [])
        for binding in bindings
    )
    if not ingest_access and not deploy_admin:
        return "NONE"
    validate(policy, deploy_member, now, minimum_remaining)
    return "REQUIRED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--admission-state", action="store_true")
    parser.add_argument("--ingest-member")
    parser.add_argument("--minimum-seconds", type=int, default=0)
    parser.add_argument("member")
    args = parser.parse_args()
    if args.reconcile and args.admission_state:
        parser.error("choose reconcile or admission-state")
    if args.minimum_seconds < 0:
        parser.error("minimum-seconds cannot be negative")
    policy = json.load(sys.stdin)
    now = datetime.now(timezone.utc)
    minimum = timedelta(seconds=args.minimum_seconds)
    if args.reconcile:
        result, _ = reconcile(policy, args.member, now, minimum)
        json.dump(result, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    elif args.admission_state:
        if not args.ingest_member:
            parser.error("admission-state requires ingest-member")
        print(admission_state(policy, args.member, args.ingest_member, now, minimum))
    else:
        validate(policy, args.member, now, minimum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
