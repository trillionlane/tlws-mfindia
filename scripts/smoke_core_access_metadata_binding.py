#!/usr/bin/env python3
"""Synthetic packaged permission-worker binding smoke; no DSN, DB or provider."""
import hashlib
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

from mfdataindia.review import core_access_metadata_spec as spec
from mfdataindia.review import core_access_metadata_inventory as frozen


def main():
    root = Path(__file__).resolve().parents[1]
    worker_path = root / "scripts/collect_core_access_metadata_dev.py"
    worker = runpy.run_path(str(worker_path))
    git = os.environ["DEPLOYMENT_GIT_SHA"]
    environment = {"DEPLOYMENT_GIT_SHA": git,
        "CORE_ACCESS_METADATA_PROGRAM_SHA256": hashlib.sha256(worker_path.read_bytes()).hexdigest(),
        "CORE_ACCESS_METADATA_SPEC_SHA256": hashlib.sha256(Path(spec.__file__).read_bytes()).hexdigest(),
        "CORE_ACCESS_METADATA_INVENTORY_SHA256": hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest()}
    manifest = SimpleNamespace(rollout_id=spec.IDENTITY, cohort=[154117], canary_code=154117,
                               git_sha=git, image="CI_SYNTHETIC_NOT_PROVIDER_PROVENANCE")
    result = worker["worker_binding"](manifest, environment)
    assert result["packaged_spec_sha256"] == environment["CORE_ACCESS_METADATA_SPEC_SHA256"]
    for name in environment:
        changed = {**environment, name: "0" * (40 if name == "DEPLOYMENT_GIT_SHA" else 64)}
        try:
            worker["worker_binding"](manifest, changed)
        except ValueError:
            pass
        else:
            raise AssertionError("mismatched permission program/spec/Git binding accepted")
    print(json.dumps({"packaged_metadata_binding_smoke": "PASS",
                      "application_queries": 0, "provider_requests": 0, "database_writes": 0,
                      "live_acceptance": False}), flush=True)


if __name__ == "__main__":
    main()
