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
import time

from mfdataindia.review import certification_input_transport as t
from mfdataindia.review.policy import fingerprint

MANIFEST = "a" * 64
EXECUTION = "tlws-mf-official-facts-refresh-dev-synthetic-native"
CONTEXT = dict(manifest_hash=MANIFEST, phase="08-candidate", execution_id=EXECUTION)
HEADROOM_RECEIPT = b"native-startup-headroom-v1"


class NativeSmokeFailure(ValueError):
    """Only static stage codes may reach CI logs; never child output or input."""


def padded_program(program):
    if len(program.encode()) > 131000:
        raise NativeSmokeFailure("launcher_size")
    return program + "p" * (131000 - len(program.encode()))


def startup_padding(environment, prefix, required_bytes):
    """Add EXACT headroom bytes, counting names, equals and terminating NULs."""
    if required_bytes not in (t.STARTUP_RESERVE_BYTES, t.STARTUP_MARGIN_BYTES):
        raise NativeSmokeFailure("padding_budget")
    result = dict(environment)
    for i in range(4):
        name = f"SYNTHETIC_STARTUP_{prefix}_{i}"
        size = required_bytes // 4 - len(name.encode()) - 2
        if name in result or not 0 < size <= t.MAX_VALUE_BYTES:
            raise NativeSmokeFailure("padding_entry")
        result[name] = "r" * size
    added = sum(len(k.encode()) + len(v.encode()) + 2
                for k, v in result.items() if k not in environment)
    if added != required_bytes:
        raise NativeSmokeFailure("padding_size")
    return result


def native_startup(submitted, launcher):
    """Prove actual kernel capacity, without changing any resource/stack limit.

    Some libcs report a fixed ARG_MAX floor, not the kernel's current exec
    ceiling. Keep that value as metadata. Do not guess a larger ceiling: the
    kernel must accept the entire conservative budget in a real exec instead.
    BOTH launches together retain the original per-profile 30-second bound.
    """
    argv = [sys.executable, "-c", padded_program(launcher)]
    try:
        budget = t.submission_budget(submitted, argv)
    except ValueError:
        raise NativeSmokeFailure("submission_budget") from None
    exec_env = startup_padding(submitted, "RESERVE", t.STARTUP_RESERVE_BYTES)
    proof_env = startup_padding(exec_env, "MARGIN", t.STARTUP_MARGIN_BYTES)
    proof_argv = [sys.executable, "-c", padded_program(
        "import os;assert 'AMC_BOOK_CERTIFICATION' not in os.environ;"
        "print('native-startup-headroom-v1')\n#")]
    proof_bytes = sum(len(k.encode()) + len(v.encode()) + 2 for k, v in proof_env.items())
    proof_bytes += sum(len(a.encode()) + 1 for a in proof_argv)
    if proof_bytes != budget["startup_required_bytes"]:
        raise NativeSmokeFailure("headroom_size")
    deadline = time.monotonic() + 30

    def launch(arguments, environment, stage):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeSmokeFailure("profile_deadline")
        try:
            result = subprocess.run(arguments, env=environment, capture_output=True,
                                    timeout=remaining)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise NativeSmokeFailure(stage + "_" + type(error).__name__) from None
        if result.returncode:
            raise NativeSmokeFailure(stage + "_nonzero")
        return result

    probe = launch(proof_argv, proof_env, "headroom_exec")
    if probe.stdout.strip() != HEADROOM_RECEIPT:
        raise NativeSmokeFailure("headroom_receipt")
    result = launch(argv, exec_env, "decoder_exec")
    try:
        receipt = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise NativeSmokeFailure("decoder_receipt") from None
    return receipt, budget, {
        "kernel_exec_headroom": "PASS", "physical_string_bytes": proof_bytes,
        "pointer_overhead": 8 * (len(proof_env) + len(proof_argv) + 2),
        "reserve_bytes": t.STARTUP_RESERVE_BYTES, "margin_bytes": t.STARTUP_MARGIN_BYTES,
        "profile_deadline_seconds": 30,
    }


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
        raise NativeSmokeFailure("linux_or_secret_guard")
    arg_max = os.sysconf("SC_ARG_MAX")
    if type(arg_max) is not int or arg_max <= 0:
        raise NativeSmokeFailure("libc_limit_metadata")
    print(json.dumps({"native_startup_metadata": {"SC_ARG_MAX": arg_max,
                      "acceptance": "actual_kernel_exec_with_full_headroom_required"}}), flush=True)
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
        try:
            submitted = t.encode_environment(base, **CONTEXT)
        except ValueError:
            raise NativeSmokeFailure("encode_preflight") from None
        actual_count = json.loads(submitted[t.HEADER])["count"]
        if actual_count != chunks:
            raise NativeSmokeFailure("chunk_count")
        # Physically exercise aggregate env + a conservative near-limit argv,
        # not just a tiny launcher. The deployed certification wrapper is much
        # smaller; no original argv/resource/stack bound is raised here.
        launcher = ("import runpy,sys;sys.argv=['synthetic','--child'];"
                    f"runpy.run_path({__file__!r},run_name='__main__')\n#")
        receipt, budget, headroom = native_startup(submitted, launcher)
        if (not isinstance(receipt, dict) or receipt.get("native_exec") != "PASS"
                or receipt.get("chunks") != chunks):
            raise NativeSmokeFailure("decoder_profile")
        results.append({**receipt, "budget": budget, "headroom": headroom})
    print(json.dumps({"synthetic_native_input_exec": results, "SC_ARG_MAX": arg_max,
                      "live_coverage": False, "pg_http_certification": False,
                      "source_requests": 0, "database_writes": 0}), flush=True)


if __name__ == "__main__":
    try:
        child() if sys.argv[1:] == ["--child"] else main()
    except Exception as error:
        code = str(error) if isinstance(error, NativeSmokeFailure) else "unclassified"
        print("Native certification input smoke failed: " + type(error).__name__
              + "/" + code, file=sys.stderr)
        raise SystemExit(1) from None
