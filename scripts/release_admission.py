#!/usr/bin/env python3
"""Exact-run CI admission for source verification and release consumers.

The workflow producer writes one hash-bound packet.  Consumers fetch that
packet and the matching attempt-specific GitHub job inventory before any
publish, deploy or grant step.  This module never performs a release mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "trillionlane/tlws-mfindia"
REPOSITORY_ID = 1403671867
WORKFLOW_NAME = "Verify"
WORKFLOW_PATH = ".github/workflows/verify.yml"
ARTIFACT_PREFIX = "release-admission"
PACKET_FORMAT = "tlws-mfdata-release-admission-v1"
CORE_JOBS = {
    "plan": "Plan CI coverage",
    "tests": "PostgreSQL tests, contracts, and audit",
    "verify": "Test, audit, and build",
}
AGGREGATE_JOB = "Release admission"
SHA_RE = re.compile(r"[0-9a-f]{40}")


class AdmissionError(ValueError):
    """A concrete fail-closed release prerequisite was not met."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def exact_sha(value, label="SHA"):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise AdmissionError(f"{label} must be an exact lowercase Git SHA")
    return value


def positive_int(value, label):
    if type(value) is not int or value <= 0:
        raise AdmissionError(f"{label} must be a positive integer")
    return value


def _policy():
    sys.path.insert(0, str(ROOT / "scripts"))
    import ci_risk_policy  # noqa: PLC0415
    return ci_risk_policy


def accepted_exclusions(plan):
    policy = _policy()
    inventory = json.loads(policy.INVENTORY.read_text())
    excluded = []
    for job in ("tests", "verify"):
        required = policy.required_ids(job, plan["mode"], inventory)
        labels = {row["id"]: row["label"] for row in inventory[job]}
        excluded.extend({"job": CORE_JOBS[job], "step": labels[key], "reason": plan["mode"]}
                        for key, selected in required.items() if not selected)
    if plan["mode"] == "full":
        return []
    return sorted(excluded, key=lambda row: (row["job"], row["step"]))


def build_packet(plan, context, job_results, *, plan_valid=True, plan_error=None):
    """Create one admission result without performing or implying a release."""
    candidate = exact_sha(plan["head"], "candidate SHA")
    exact_sha(plan["checkout"], "checkout SHA")
    blockers = []
    if plan_error:
        blockers.append(f"CI plan rejected: {plan_error}")
    if not plan_valid:
        blockers.append("CI plan result, mode or binding is incomplete or stale")

    expected = list(CORE_JOBS.values())
    observed = {}
    for key, name in CORE_JOBS.items():
        result = job_results.get(key)
        observed[name] = result
        if result != "success":
            blockers.append(f"required job {name} is {result or 'missing'}")

    event = context.get("event")
    ref = context.get("ref")
    mode = plan.get("mode")
    source_verified = not blockers
    release_candidate = bool(source_verified and event == "push" and ref == "refs/heads/main"
                             and mode == "full" and plan.get("base") is None
                             and plan["checkout"] == candidate)
    if source_verified and not release_candidate:
        scope = f"SOURCE_VERIFIED_{str(mode).upper()}"
    elif release_candidate:
        scope = "MAIN_RELEASE_CANDIDATE_FULL"
    else:
        scope = "REJECTED"

    workflow_ref = context.get("workflow_ref")
    expected_ref_prefix = f"{REPOSITORY}/{WORKFLOW_PATH}@"
    if context.get("repository") != REPOSITORY:
        blockers.append("repository identity mismatch")
    if context.get("repository_id") != REPOSITORY_ID:
        blockers.append("repository numeric identity mismatch")
    if context.get("workflow") != WORKFLOW_NAME:
        blockers.append("workflow name mismatch")
    if not isinstance(workflow_ref, str) or not workflow_ref.startswith(expected_ref_prefix):
        blockers.append("workflow path identity mismatch")
    try:
        exact_sha(context.get("workflow_sha"), "workflow SHA")
    except AdmissionError as exc:
        blockers.append(str(exc))
    if context.get("workflow_sha") != plan["checkout"]:
        blockers.append("workflow checkout identity mismatch")
    if event == "push" and plan["checkout"] != candidate:
        blockers.append("automatic main checkout does not equal candidate head")
    if event not in {"pull_request", "push", "workflow_dispatch"}:
        blockers.append("unsupported workflow event")
    if blockers:
        source_verified = release_candidate = False
        scope = "REJECTED"

    packet = {
        "format": PACKET_FORMAT,
        "repository": {"name": context.get("repository"), "id": context.get("repository_id")},
        "workflow": {
            "name": context.get("workflow"),
            "path": WORKFLOW_PATH,
            "ref": workflow_ref,
            "sha": context.get("workflow_sha"),
        },
        "run": {"id": context.get("run_id"), "attempt": context.get("run_attempt")},
        "event": event,
        "ref": ref,
        "candidate_sha": candidate,
        "head_sha": plan["head"],
        "base_sha": plan.get("base"),
        "checkout_sha": plan["checkout"],
        "ci_plan": plan,
        "ci_plan_binding": _policy().digest(plan),
        "expected_checks": expected,
        "observed_checks": observed,
        "accepted_exclusions": accepted_exclusions(plan),
        "blocking_reasons": blockers,
        "verification_scope": scope,
        "source_verified": source_verified,
        "release_candidate_eligible": release_candidate,
        "permission_enabled": False,
        "consumer_acceptance": "PENDING_RO04",
        "deployment_performed": False,
    }
    packet["packet_sha256"] = digest(packet)
    return packet


def rejection_packet(context, reason):
    candidate = context.get("candidate_sha")
    if not isinstance(candidate, str) or not SHA_RE.fullmatch(candidate):
        candidate = "0" * 40
    packet = {
        "format": PACKET_FORMAT,
        "repository": {"name": context.get("repository"), "id": context.get("repository_id")},
        "workflow": {"name": context.get("workflow"), "path": WORKFLOW_PATH,
                     "ref": context.get("workflow_ref"), "sha": context.get("workflow_sha")},
        "run": {"id": context.get("run_id"), "attempt": context.get("run_attempt")},
        "event": context.get("event"), "ref": context.get("ref"),
        "candidate_sha": candidate, "head_sha": candidate, "base_sha": None,
        "checkout_sha": candidate, "ci_plan": None, "ci_plan_binding": None,
        "expected_checks": list(CORE_JOBS.values()), "observed_checks": {},
        "accepted_exclusions": [], "blocking_reasons": [str(reason)],
        "verification_scope": "REJECTED", "source_verified": False,
        "release_candidate_eligible": False, "permission_enabled": False,
        "consumer_acceptance": "PENDING_RO04", "deployment_performed": False,
    }
    packet["packet_sha256"] = digest(packet)
    return packet


def context_from_environment(environ=os.environ):
    return {
        "repository": environ.get("GITHUB_REPOSITORY"),
        "repository_id": int(environ["GITHUB_REPOSITORY_ID"]),
        "workflow": environ.get("GITHUB_WORKFLOW"),
        "workflow_ref": environ.get("GITHUB_WORKFLOW_REF"),
        "workflow_sha": environ.get("GITHUB_WORKFLOW_SHA"),
        "run_id": int(environ["GITHUB_RUN_ID"]),
        "run_attempt": int(environ["GITHUB_RUN_ATTEMPT"]),
        "event": environ.get("GITHUB_EVENT_NAME"),
        "ref": environ.get("GITHUB_REF"),
        "candidate_sha": environ.get("GITHUB_SHA"),
    }


def render_summary(packet):
    lines = ["# Release admission", "", f"- Decision: `{packet['verification_scope']}`",
             f"- Candidate: `{packet['candidate_sha']}`",
             f"- Run/attempt: `{packet['run']['id']}/{packet['run']['attempt']}`",
             f"- CI mode: `{(packet.get('ci_plan') or {}).get('mode', 'unavailable')}`",
             f"- Source verified: `{str(packet['source_verified']).lower()}`",
             f"- Release candidate eligible: `{str(packet['release_candidate_eligible']).lower()}`",
             "- Deployment performed: `false`", "- Permission enabled: `false`",
             "- Consumer acceptance: `PENDING_RO04`"]
    if packet["accepted_exclusions"]:
        lines.extend(["", "## Accepted exclusions"])
        lines.extend(f"- {row['job']}: {row['step']} ({row['reason']})"
                     for row in packet["accepted_exclusions"])
    if packet["blocking_reasons"]:
        lines.extend(["", "## Blocking reasons"])
        lines.extend(f"- {reason}" for reason in packet["blocking_reasons"])
    return "\n".join(lines) + "\n"


def emit(output, summary, environ=os.environ):
    try:
        context = context_from_environment(environ)
        policy = _policy()
        plan = policy.environment_plan(environ)
        plan_error = None
        try:
            policy.verify_plan(plan, environ)
            valid = True
        except (KeyError, ValueError) as exc:
            valid, plan_error = False, str(exc)
        packet = build_packet(plan, context, {
            "plan": environ.get("CI_PLAN_RESULT"),
            "tests": environ.get("CI_TESTS_RESULT"),
            "verify": environ.get("CI_VERIFY_RESULT"),
        }, plan_valid=valid, plan_error=plan_error)
    except Exception as exc:  # preserve a concrete failed packet for final reporting
        fallback = {
            "repository": environ.get("GITHUB_REPOSITORY"),
            "repository_id": int(environ.get("GITHUB_REPOSITORY_ID", "0")),
            "workflow": environ.get("GITHUB_WORKFLOW"),
            "workflow_ref": environ.get("GITHUB_WORKFLOW_REF"),
            "workflow_sha": environ.get("GITHUB_WORKFLOW_SHA"),
            "run_id": int(environ.get("GITHUB_RUN_ID", "0")),
            "run_attempt": int(environ.get("GITHUB_RUN_ATTEMPT", "0")),
            "event": environ.get("GITHUB_EVENT_NAME"), "ref": environ.get("GITHUB_REF"),
            "candidate_sha": environ.get("GITHUB_SHA"),
        }
        packet = rejection_packet(fallback, f"admission construction failed: {exc}")
    output.write_text(json.dumps(packet, sort_keys=True, indent=2) + "\n")
    summary.write_text(render_summary(packet))
    return packet


class GitHubAPI:
    """Small read-only client; one request per evidence resource."""

    def __init__(self, token, *, api_url="https://api.github.com"):
        if not token:
            raise AdmissionError("GitHub read token required")
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _request(self, path, *, accept="application/vnd.github+json"):
        url = path if path.startswith("https://") else self.api_url + "/" + path.lstrip("/")
        expected = urllib.parse.urlparse(self.api_url)
        actual = urllib.parse.urlparse(url)
        if actual.scheme != "https" or actual.netloc != expected.netloc:
            raise AdmissionError("GitHub evidence URL left the configured API origin")
        request = urllib.request.Request(url, headers={
            "Accept": accept, "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tlws-mfdata-release-admission",
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise AdmissionError(f"GitHub evidence request failed with HTTP {exc.code}") from exc

    def json(self, path):
        try:
            return json.loads(self._request(path))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AdmissionError("GitHub returned malformed JSON evidence") from exc

    def bytes(self, path):
        return self._request(path, accept="application/vnd.github+json")


def _packet_from_archive(raw):
    if len(raw) > 2 * 1024 * 1024:
        raise AdmissionError("oversize release-admission artifact")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if names != ["release-admission.json"]:
                raise AdmissionError("release-admission artifact has unexpected contents")
            if archive.getinfo(names[0]).file_size > 1024 * 1024:
                raise AdmissionError("oversize release-admission packet")
            payload = archive.read(names[0])
    except zipfile.BadZipFile as exc:
        raise AdmissionError("release-admission artifact is not a valid archive") from exc
    if len(payload) > 1024 * 1024:
        raise AdmissionError("oversize release-admission packet")
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AdmissionError("release-admission packet is malformed") from exc


def validate_packet(packet, *, run_id, run_attempt, candidate_sha):
    if not isinstance(packet, dict) or packet.get("format") != PACKET_FORMAT:
        raise AdmissionError("unsupported release-admission packet")
    signature = packet.get("packet_sha256")
    unsigned = {key: value for key, value in packet.items() if key != "packet_sha256"}
    if signature != digest(unsigned):
        raise AdmissionError("release-admission packet digest mismatch")
    if packet.get("repository") != {"name": REPOSITORY, "id": REPOSITORY_ID}:
        raise AdmissionError("packet repository mismatch")
    if packet.get("run") != {"id": run_id, "attempt": run_attempt}:
        raise AdmissionError("packet run or attempt mismatch")
    workflow = packet.get("workflow")
    if not isinstance(workflow, dict) or workflow.get("name") != WORKFLOW_NAME \
            or workflow.get("path") != WORKFLOW_PATH \
            or workflow.get("ref") != f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/main" \
            or workflow.get("sha") != candidate_sha:
        raise AdmissionError("packet workflow identity mismatch")
    for key in ("candidate_sha", "head_sha", "checkout_sha"):
        if packet.get(key) != candidate_sha:
            raise AdmissionError(f"packet {key} mismatch")
    plan = packet.get("ci_plan")
    if not isinstance(plan, dict) or digest(plan) != packet.get("ci_plan_binding"):
        raise AdmissionError("packet CI plan binding mismatch")
    if plan != {"version": 1, "event": "push", "checkout": candidate_sha,
                "head": candidate_sha, "base": None, "paths": [], "mode": "full"}:
        raise AdmissionError("release requires the complete automatic main-push plan")
    if packet.get("event") != "push" or packet.get("ref") != "refs/heads/main":
        raise AdmissionError("release requires automatic main verification")
    if packet.get("expected_checks") != list(CORE_JOBS.values()) \
            or packet.get("observed_checks") != {name: "success" for name in CORE_JOBS.values()}:
        raise AdmissionError("packet check aggregation is incomplete")
    if packet.get("accepted_exclusions") != [] or packet.get("blocking_reasons") != []:
        raise AdmissionError("release packet contains exclusions or blockers")
    if packet.get("verification_scope") != "MAIN_RELEASE_CANDIDATE_FULL" \
            or packet.get("source_verified") is not True \
            or packet.get("release_candidate_eligible") is not True:
        raise AdmissionError("packet is not a main release candidate")
    if packet.get("permission_enabled") is not False \
            or packet.get("consumer_acceptance") != "PENDING_RO04" \
            or packet.get("deployment_performed") is not False:
        raise AdmissionError("CI packet improperly claims live state")
    return packet


def consume(api, *, repository, run_id, run_attempt, candidate_sha):
    if repository != REPOSITORY:
        raise AdmissionError("consumer repository mismatch")
    positive_int(run_id, "run ID")
    positive_int(run_attempt, "run attempt")
    exact_sha(candidate_sha, "candidate SHA")
    encoded_repo = "/".join(urllib.parse.quote(part, safe="") for part in repository.split("/"))
    run = api.json(f"repos/{encoded_repo}/actions/runs/{run_id}")
    if run.get("id") != run_id or run.get("run_attempt") != run_attempt:
        raise AdmissionError("GitHub run/attempt mismatch")
    if run.get("name") != WORKFLOW_NAME or run.get("path") != WORKFLOW_PATH \
            or run.get("workflow_id") is None:
        raise AdmissionError("GitHub workflow identity mismatch")
    repository_doc = run.get("repository") or {}
    head_repository = run.get("head_repository") or {}
    if repository_doc.get("full_name") != REPOSITORY \
            or head_repository.get("full_name") != REPOSITORY:
        raise AdmissionError("GitHub run repository mismatch")
    if run.get("event") != "push" or run.get("head_branch") != "main" \
            or run.get("head_sha") != candidate_sha:
        raise AdmissionError("GitHub run is not the exact automatic main candidate")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise AdmissionError("GitHub run is incomplete or unsuccessful")

    job_doc = api.json(
        f"repos/{encoded_repo}/actions/runs/{run_id}/attempts/{run_attempt}/jobs?per_page=100")
    jobs = job_doc.get("jobs")
    expected_names = set(CORE_JOBS.values()) | {AGGREGATE_JOB}
    if job_doc.get("total_count") != len(expected_names) or not isinstance(jobs, list) \
            or len(jobs) != len(expected_names) or {job.get("name") for job in jobs} != expected_names:
        raise AdmissionError("attempt does not contain the complete distinct job set")
    for job in jobs:
        if job.get("run_attempt") != run_attempt or job.get("head_sha") != candidate_sha \
                or job.get("status") != "completed" or job.get("conclusion") != "success":
            raise AdmissionError(f"required job {job.get('name')} is stale, partial or unsuccessful")
    core = [job for job in jobs if job.get("name") in set(CORE_JOBS.values())]
    _policy().require_full_policy_jobs(core)

    artifact_name = f"{ARTIFACT_PREFIX}-{run_id}-{run_attempt}"
    artifact_doc = api.json(f"repos/{encoded_repo}/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = artifact_doc.get("artifacts")
    if not isinstance(artifacts, list) or artifact_doc.get("total_count", 0) > 100:
        raise AdmissionError("complete artifact inventory unavailable")
    matches = [row for row in artifacts if row.get("name") == artifact_name]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdmissionError("exact current-attempt release-admission artifact required")
    artifact_run = matches[0].get("workflow_run") or {}
    if artifact_run.get("id") != run_id or artifact_run.get("head_sha") != candidate_sha:
        raise AdmissionError("mixed-run release-admission artifact")
    packet = _packet_from_archive(api.bytes(matches[0]["archive_download_url"]))
    validate_packet(packet, run_id=run_id, run_attempt=run_attempt, candidate_sha=candidate_sha)

    ref_doc = api.json(f"repos/{encoded_repo}/git/ref/heads/main")
    if (ref_doc.get("object") or {}).get("sha") != candidate_sha:
        raise AdmissionError("candidate is stale relative to current main")
    return {
        "release_admitted": True,
        "candidate_sha": candidate_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "verification_scope": packet["verification_scope"],
        "permission_enabled": False,
        "consumer_acceptance": "PENDING_RO04",
        "deployment_performed": False,
        "packet_sha256": packet["packet_sha256"],
    }


def guarded_mutation(admission, operation):
    """Call an injected mutation only after a complete release decision."""
    if admission.get("release_admitted") is not True:
        raise AdmissionError("release mutation blocked by admission")
    return operation(admission)


def consume_command(args):
    api = GitHubAPI(os.environ.get("GITHUB_TOKEN"))
    result = consume(api, repository=args.repository, run_id=args.run_id,
                     run_attempt=args.run_attempt, candidate_sha=args.candidate_sha)
    Path(args.output).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    with Path(args.summary).open("a") as stream:
        stream.write("# Consumed release admission\n\n")
        stream.write(f"- Candidate: `{result['candidate_sha']}`\n")
        stream.write(f"- Verify run/attempt: `{result['run_id']}/{result['run_attempt']}`\n")
        stream.write("- Release mutation performed by this check: `false`\n")
        stream.write("- Permission enabled: `false`\n")
        stream.write("- Consumer acceptance: `PENDING_RO04`\n")
    if args.github_output:
        with Path(args.github_output).open("a") as stream:
            stream.write(f"candidate_sha={result['candidate_sha']}\n")
            stream.write(f"packet_sha256={result['packet_sha256']}\n")
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    producer = sub.add_parser("emit")
    producer.add_argument("--output", type=Path, required=True)
    producer.add_argument("--summary", type=Path, required=True)
    consumer = sub.add_parser("consume")
    consumer.add_argument("--repository", default=REPOSITORY)
    consumer.add_argument("--run-id", type=int, required=True)
    consumer.add_argument("--run-attempt", type=int, required=True)
    consumer.add_argument("--candidate-sha", required=True)
    consumer.add_argument("--output", required=True)
    consumer.add_argument("--summary", required=True)
    consumer.add_argument("--github-output")
    args = parser.parse_args()
    if args.operation == "emit":
        packet = emit(args.output, args.summary)
        print(json.dumps(packet, sort_keys=True))
        raise SystemExit(0 if packet["source_verified"] else 1)
    consume_command(args)


if __name__ == "__main__":
    main()
