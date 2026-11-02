#!/usr/bin/env python3
"""Fast local FS13 feedback or read-only BOTH-job admission; never deployment.

Use --full-local once the candidate is stable. Neither local mode replaces full
native PostgreSQL/image CI. No local PostgreSQL, Docker or source calls occur.
--verify-run requires the planner and BOTH original jobs at one exact SHA; it
does not merge or change repository protection/settings.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
REPO = "trillionlane/tlws-mfindia"
JOBS = {"PostgreSQL tests, contracts, and audit", "Test, audit, and build"}
AGGREGATE_JOB = "Release admission"
TARGETED = (
    "tests/test_certification_input_transport.py",
    "tests/test_certification_input_smoke.py",
    "tests/test_ci_test_partition.py",
    "tests/test_ci_verify_sequence.py",
    "tests/test_prepush_fs13.py",
    "tests/test_clone_checkpoint_j_target.py",
)


def local_checks(*, full=False):
    workflow = (ROOT / ".github/workflows/verify.yml").read_text()
    contracts = re.findall(r"^          (uv run python scripts/export_\S+ --check)$",
                           workflow, re.M)
    if len(contracts) != 21 or len(set(contracts)) != 21:
        raise ValueError("complete reviewed contract inventory required")
    tests = ["uv", "run", "pytest", "-q", "-m", "not postgres"]
    if not full:
        tests.extend(TARGETED)
    return [
        ("lint", ["uv", "run", "ruff", "check", "src", "scripts", "tests"]),
        ("full-local-nonPG" if full else "targeted-local-nonPG", tests),
        *[("contract-" + str(i), command.split()) for i, command in enumerate(contracts, 1)],
        ("dependency-audit", ["uv", "run", "pip-audit"]),
        ("tracked-credential-scan", ["./scripts/scan_tracked_secrets.sh"]),
        ("diff-check", ["git", "diff", "--check"]),
    ]


def run_local(*, full=False):
    started = time.monotonic()
    for label, argv in local_checks(full=full):
        subprocess.run(argv, cwd=ROOT, check=True)
        print(json.dumps({"local_check": label, "status": "PASS"}), flush=True)
    return {"local_only": True, "full_native_CI_still_required": True,
            "mode": "full-local-nonPG" if full else "fast-local-nonPG",
            "seconds": round(time.monotonic() - started, 3)}


def require_whole_verify(document, *, run_id, head_sha):
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha) or type(run_id) is not int or run_id <= 0:
        raise ValueError("exact positive run ID and Git SHA required")
    if (document.get("databaseId") != run_id or document.get("headSha") != head_sha
            or document.get("name") != "Verify" or document.get("status") != "completed"
            or document.get("conclusion") != "success"):
        raise ValueError("whole exact-head Verify must complete successfully")
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("ci_policy_full_admission", ROOT / "scripts/ci_risk_policy.py")
    policy = module_from_spec(spec)
    spec.loader.exec_module(policy)
    jobs = document.get("jobs")
    names = {"Plan CI coverage", *JOBS, AGGREGATE_JOB}
    if (not isinstance(jobs, list) or len(jobs) != 4
            or any(not isinstance(job, dict) for job in jobs)
            or {job.get("name") for job in jobs} != names):
        raise ValueError("complete current four-job Verify required")
    aggregate = next(job for job in jobs if job["name"] == AGGREGATE_JOB)
    if aggregate.get("status") != "completed" or aggregate.get("conclusion") != "success":
        raise ValueError("release-admission aggregate must succeed")
    steps = aggregate.get("steps")
    required = {"Build exact release-admission decision",
                "Retain the current-attempt admission result"}
    if (not isinstance(steps, list) or not required <= {step.get("name") for step in steps}
            or any(step.get("status") != "completed" or step.get("conclusion") != "success"
                   for step in steps)):
        raise ValueError("complete release-admission aggregate steps required")
    # All historical admissions are complete. No legacy-shape fallback is
    # permitted at a current or future head, even with successful old jobs.
    return {"run": run_id, "head_sha": head_sha, "read_only": True,
            "merge_or_deployment_started": False, "source_verified": True,
            "release_candidate_eligible": False,
            **policy.require_full_policy_jobs(
                [job for job in jobs if job.get("name") != AGGREGATE_JOB])}


def inspect_verify(run_id, head_sha):
    # Caller still reconciles full test inventories/logs and completion review.
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha) or run_id <= 0:
        raise ValueError("exact run and SHA required before GitHub read")
    result = subprocess.run(
        ["gh", "run", "view", str(run_id), "--repo", REPO, "--json",
         "databaseId,headSha,name,status,conclusion,jobs"],
        cwd=ROOT, check=True, capture_output=True, text=True)
    return require_whole_verify(json.loads(result.stdout), run_id=run_id, head_sha=head_sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full-local", action="store_true")
    mode.add_argument("--verify-run", type=int)
    parser.add_argument("--head-sha")
    args = parser.parse_args()
    if (args.verify_run is not None) != (args.head_sha is not None):
        parser.error("--verify-run and --head-sha must be supplied together")
    value = inspect_verify(args.verify_run, args.head_sha) if args.verify_run is not None \
        else run_local(full=args.full_local)
    print(json.dumps(value, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
