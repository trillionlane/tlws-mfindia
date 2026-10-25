#!/usr/bin/env python3
"""Actual packaged owner worker/source binding; synthetic, no secret/DB/provider."""
import hashlib
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

from mfdataindia.review import reader_access_transport_controller as core
from mfdataindia.review import reader_access_transport_spec as spec


def main():
    path = Path(__file__).resolve().parent / "apply_reader_access_correction_dev.py"
    worker = runpy.run_path(str(path))
    git = os.environ["DEPLOYMENT_GIT_SHA"]
    env = {"DEPLOYMENT_GIT_SHA": git, "CLOUD_RUN_JOB": spec.JOB,
           "CLOUD_RUN_EXECUTION": spec.JOB + "-abcde", "CLOUD_RUN_TASK_INDEX": "0",
           "CLOUD_RUN_TASK_ATTEMPT": "0", "CLOUD_RUN_TASK_COUNT": "1"}
    manifest = SimpleNamespace(rollout_id=spec.IDENTITY, cohort=[154117], canary_code=154117,
                               git_sha=git, image="CI_SYNTHETIC_NOT_PROVIDER_PROVENANCE")
    source = hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest()
    result = worker["packaged_binding"](manifest, source, env)
    assert result["packaged_engine_sha256"] == spec.ENGINE_SHA
    assert result["packaged_profile_sha256"] == spec.PROFILE_SHA
    for key in env:
        changed = {**env, key: "unapproved"}
        try:
            worker["packaged_binding"](manifest, source, changed)
        except ValueError:
            pass
        else:
            raise AssertionError("platform/Git binding drift admitted")
    old = SimpleNamespace(**{**vars(manifest), "rollout_id": "dev-core-aclmeta-261024b"})
    try:
        worker["packaged_binding"](old, source, env)
    except ValueError:
        pass
    else:
        raise AssertionError("consumed metadata identity admitted")
    class NoSecret(dict):
        def __getitem__(self, key):
            assert key != "MFDATAINDIA_DSN", "secret touched on missing approval"
            return super().__getitem__(key)
    try:
        worker["run_worker"](NoSecret(env))
    except (ValueError, KeyError):
        pass
    else:
        raise AssertionError("missing exact grant approval admitted")
    print(json.dumps({"packaged_owner_grant_binding_smoke": "PASS", "provider_requests": 0,
                      "database_writes": 0, "secret_reads": 0, "live_acceptance": False}), flush=True)


if __name__ == "__main__":
    main()
