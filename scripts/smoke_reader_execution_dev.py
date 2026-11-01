#!/usr/bin/env python3
"""Actual packaged main/parse/environment paths, zero provider/DB requests."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from mfdataindia.review import reader_execution_contract as contract
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review.policy import fingerprint


def smoke(*, root=None, git_sha=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    git_sha = spec.git_sha(git_sha or os.environ.get("DEPLOYMENT_GIT_SHA"))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    images = {kind: {"image": spec.IMAGE_PREFIX.replace("/mfdataindia@", "/" + repository + "@") + "a" * 64,
        "resolved_image": spec.IMAGE_PREFIX.replace("/mfdataindia@", "/" + repository + "@") + "a" * 64,
        "config_digest": "sha256:" + "b" * 64, "git_sha": git_sha,
        "platform": "linux/amd64", "layers_downloaded": 0}
        for kind, repository in (("app", "mfdataindia"), ("tools", "mfdataindia-review-tools"))}
    plan = contract.candidate(operation_id="op-synthetic000000000", git_sha=git_sha, images=images,
        window={"starts_at": (now-timedelta(seconds=1)).isoformat(),
                "ends_at": (now+timedelta(minutes=5)).isoformat(),
                "exclusive_administration": False, "data_schema_custody": False,
                "future_owner_provisioning": False, "dev_no_freeze_risk_accepted": True},
        protected_sha256="c" * 64, probe_codes={"monthly": 1, "multi": 2, "book": 3},
        application_trust_sha256="d" * 64, source_root=root, expected_git=git_sha)
    assert contract.dry_run(plan, source_root=root, expected_git=git_sha)["dispatches"] == 0
    # Exercise the real exported canonical parser/load/main path, not --help.
    # This temporary synthetic input is not an operation root or mutation intent.
    with tempfile.NamedTemporaryFile(mode="wb", prefix="ro02-synthetic-plan-") as input_file:
        input_file.write((contract.canonical(plan) + "\n").encode())
        input_file.flush()
        validated = subprocess.run([sys.executable, str(root / "scripts/reader_execution_dev.py"),
            "validate", "--plan", input_file.name, "--expected-git", git_sha],
            capture_output=True, text=True, timeout=30)
    assert validated.returncode == 0 and not validated.stderr
    assert json.loads(validated.stdout)["state"] == "LOCAL_VALIDATION_PASSED_NO_DISPATCH"
    checks = []
    for kind in contract.KINDS:
        payload = contract.stage_payload(plan, kind, [] if kind == "capture" else [{"synthetic_invalid_predecessor": True}])
        prepared = spec.request(kind, root=contract.operation_root(plan), final_git_sha=git_sha,
            image=images["app"], payload=payload, source_sha256=fingerprint(plan["packaged_source"]),
            operation_id=plan["operation_id"], **contract.package_hashes(root))
        # The TOOLS base is postgres and has PG_* image defaults. Deliberately
        # isolate ONLY this synthetic child's inputs: actual workers continue
        # rejecting every PG_* override before DSN/connect. Do not weaken that
        # runtime guard or accept an earlier failure as this smoke's success.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("BROAD_DIRECT_", "READER_EXECUTION_", "CLOUD_RUN_", "PG"))}
        env.update({row["name"]: row["value"] for row in prepared["task"]["containers"][0]["env"] if "value" in row})
        name = spec.role_for(kind, operation_id=plan["operation_id"]).job
        env.update(MFDATAINDIA_DSN="synthetic-invalid-dsn-no-connection", CLOUD_RUN_JOB=name,
            CLOUD_RUN_EXECUTION=name + "-abcde", CLOUD_RUN_TASK_COUNT="1", CLOUD_RUN_TASK_INDEX="0", CLOUD_RUN_TASK_ATTEMPT="0")
        result = subprocess.run([sys.executable, str(root / "scripts/reader_execution_dev.py")],
            env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 1
        diagnostic = json.loads(result.stderr.strip().splitlines()[-1])
        assert diagnostic["state"] == "WORKER_VALIDATION_FAILED_BEFORE_CONNECT"
        assert diagnostic["stage"] == ("dsn" if kind == "capture" else "validation"), {
            "kind": kind, "stage": diagnostic["stage"], "safe_cause": diagnostic["safe_cause"]}
        assert "synthetic-invalid-dsn" not in result.stdout + result.stderr
        checks.append({"kind": kind, "state": diagnostic["state"], "stage": diagnostic["stage"]})
    return {"packaged_reader_execution_entrypoints": "PASS", "git_sha": git_sha,
            "source_sha256": fingerprint(plan["packaged_source"]),
            "code_sha256": fingerprint(plan["code_binding"]),
            "entrypoint_sha256": hashlib.sha256((root / "scripts/reader_execution_dev.py").read_bytes()).hexdigest(),
            "boundaries": checks, "provider_requests": 0, "database_connections": 0,
            "live_consumer_acceptance": False}


if __name__ == "__main__":
    print(json.dumps(smoke(), sort_keys=True))
