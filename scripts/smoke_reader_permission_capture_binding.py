#!/usr/bin/env python3
"""Actual packaged source/platform smoke; no DSN, credentials, DB or provider."""
import hashlib
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

from mfdataindia.review import reader_permission_capture_spec as spec
from mfdataindia.review import reader_permission_capture_inventory as frozen


def main():
    path = Path(__file__).resolve().parent / "collect_reader_permission_capture_dev.py"
    worker = runpy.run_path(str(path))
    git = os.environ["DEPLOYMENT_GIT_SHA"]
    env = {"DEPLOYMENT_GIT_SHA": git, "CLOUD_RUN_JOB": spec.JOB,
           "CLOUD_RUN_EXECUTION": spec.JOB + "-abcde", "CLOUD_RUN_TASK_INDEX": "0",
           "CLOUD_RUN_TASK_ATTEMPT": "0", "CLOUD_RUN_TASK_COUNT": "1"}
    for key, p in (("PROGRAM", path), ("SPEC", Path(spec.__file__)), ("INVENTORY", Path(frozen.__file__))):
        env["READER_PERMISSION_CAPTURE_" + key + "_SHA256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    m = SimpleNamespace(rollout_id=spec.IDENTITY, cohort=[154117], canary_code=154117,
                        git_sha=git, image="SYNTHETIC_NO_PROVENANCE_OR_AUTHORITY")
    assert worker["worker_binding"](m, env)["manifest_git_sha"] == git
    for key in env:
        try:
            worker["worker_binding"](m, {**env, key: "unexpected"})
        except ValueError:
            pass
        else:
            raise AssertionError("packaged capture platform/source drift accepted")
    m.rollout_id = "dev-core-aclmeta-261024b"
    try:
        worker["worker_binding"](m, env)
    except ValueError:
        pass
    else:
        raise AssertionError("consumed identity accepted")
    try:
        worker["run_worker"]({})
    except KeyError:
        pass
    else:
        raise AssertionError("missing manifest reached secret/connection")
    print(json.dumps({"packaged_reader_capture_binding": "PASS", "provider_requests": 0,
        "database_queries": 0, "live_acceptance": False}), flush=True)


if __name__ == "__main__":
    main()
