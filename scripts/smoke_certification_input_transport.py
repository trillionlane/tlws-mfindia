#!/usr/bin/env python3
"""Synthetic native Linux exec-size smoke in BOTH final CI images.

Not PG/private-HTTP certification, provider acceptance, or live coverage. No
source/cloud calls, stack-limit changes, secrets or reconstructed child envs.
"""

import base64
import hashlib
import json
import os
import random
import subprocess
import sys

from mfdataindia.review import certification_input_transport as t
from mfdataindia.review.policy import fingerprint

MANIFEST = "a" * 64
EXECUTION = "tlws-mf-official-facts-refresh-dev-synthetic-native"
CONTEXT = dict(manifest_hash=MANIFEST, phase="08-candidate", execution_id=EXECUTION)


def child():
    before = dict(os.environ)
    logical = t.decode_environment(os.environ, **CONTEXT)
    raw = logical[t.RAW].encode()
    assert len(raw) == int(os.environ["SYNTHETIC_RAW_BYTES"])
    assert hashlib.sha256(raw).hexdigest() == os.environ["SYNTHETIC_RAW_SHA256"]
    assert dict(os.environ) == before and t.RAW not in os.environ
    # Child-after-decode still inherits only the submitted bounded environment.
    program = (
        "import os,hashlib,json;"
        "assert 'AMC_BOOK_CERTIFICATION' not in os.environ;"
        "print(hashlib.sha256(json.dumps(dict(os.environ),sort_keys=True).encode()).hexdigest())"
    )
    result = subprocess.run([sys.executable, "-c", program], env=os.environ,
                            capture_output=True, timeout=15, check=True)
    assert result.stdout.decode().strip() == hashlib.sha256(
        json.dumps(before, sort_keys=True).encode()).hexdigest()
    print(json.dumps({"native_exec": "PASS", "raw_bytes": len(raw),
                      "chunks": json.loads(os.environ[t.HEADER])["count"]}), flush=True)


def main():
    if sys.platform != "linux" or "MFDATAINDIA_DSN" in os.environ:
        raise ValueError("secret-free native Linux CI image required")
    arg_max = os.sysconf("SC_ARG_MAX")
    results = []
    # Deterministic synthetic sizes, not a copy of failed private references.
    for chunks, entropy in ((60, 1060000), (64, 1129000)):
        doc = {"execution_id": EXECUTION, "synthetic_only": True,
               "entropy": base64.b64encode(random.Random(190 + chunks).randbytes(entropy)).decode(),
               "padding": "x" * 620000}
        raw = json.dumps(doc)
        base = {
            **os.environ, t.RAW: raw,
            t.HASH: fingerprint({"manifest": MANIFEST, "certification": doc}),
            "SYNTHETIC_RAW_BYTES": str(len(raw.encode())),
            "SYNTHETIC_RAW_SHA256": hashlib.sha256(raw.encode()).hexdigest(),
        }
        submitted = t.encode_environment(base, **CONTEXT)
        actual_count = json.loads(submitted[t.HEADER])["count"]
        if actual_count != chunks:
            raise ValueError("native synthetic chunk-size fixture differs")
        argv = [sys.executable, __file__, "--child"]
        budget = t.submission_budget(submitted, argv)
        if budget["startup_required_bytes"] > arg_max:
            raise ValueError("native aggregate exec headroom insufficient")
        # Physically exercise conservative secret/platform startup overhead.
        # The separate margin remains unused; never increase stack/resources.
        exec_env = dict(submitted)
        for i in range(4):
            exec_env[f"SYNTHETIC_STARTUP_RESERVE_{i}"] = "r" * 32700
        pointer_overhead = 8 * (len(exec_env) + len(argv) + 2)
        if budget["startup_required_bytes"] + pointer_overhead > arg_max:
            raise ValueError("native aggregate pointer headroom insufficient")
        result = subprocess.run(argv, env=exec_env, capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError("native bounded certification child failed")
        receipt = json.loads(result.stdout)
        assert receipt["native_exec"] == "PASS" and receipt["chunks"] == chunks
        results.append({**receipt, "budget": budget, "pointer_overhead": pointer_overhead})
    print(json.dumps({"synthetic_native_input_exec": results, "SC_ARG_MAX": arg_max,
                      "live_coverage": False, "pg_http_certification": False,
                      "source_requests": 0, "database_writes": 0}), flush=True)


if __name__ == "__main__":
    try:
        child() if sys.argv[1:] == ["--child"] else main()
    except Exception as error:
        print("Native certification input smoke failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
