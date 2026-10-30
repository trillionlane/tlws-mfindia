#!/usr/bin/env python3
"""Provider-free exact-source binding for both APP and TOOLS images."""

import json
import os
from pathlib import Path

from mfdataindia.review import broad_reader_direct_delivery_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_source_authority as source


ROOT = Path(__file__).resolve().parents[1]


def smoke(*, root=ROOT, git_sha=None):
    git_sha = spec.git_sha(git_sha or os.environ.get("DEPLOYMENT_GIT_SHA"))
    packaged = source.verify_packaged_source(root)
    bindings = {kind: controller.package_hashes(root, kind=kind)
                for kind in spec.ROLES}
    assert set(bindings) == {"capture", "grant", "reader"}
    assert len({row["worker_sha256"] for row in bindings.values()}) == 3
    assert all(row["controller_sha256"] == bindings["capture"]["controller_sha256"]
               and row["spec_sha256"] == bindings["capture"]["spec_sha256"]
               for row in bindings.values())
    return {"packaged_direct_reader_delivery_binding": "PASS",
            "git_sha": git_sha, "source_base_sha256": packaged["base_sha256"],
            "source_variant_sha256": packaged["variant_sha256"],
            "source_files": packaged["base_files"] + packaged["variant_files"],
            "worker_count": len(bindings), "bindings": bindings,
            "provider_requests": 0, "database_queries": 0,
            "permission_enabled": False, "ti_verified": False}


if __name__ == "__main__":
    print(json.dumps(smoke(), sort_keys=True))
