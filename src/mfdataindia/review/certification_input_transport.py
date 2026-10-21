"""Lossless certification INPUT transport; never an alternative to certification.

Only the selected reference document is compressed. Secrets and the rest of the
environment are not encoded. Decoded bytes live in a private mapping, never in
os.environ or a child environment. No cloud operations or fallback are provided.
"""

import base64
from collections.abc import Mapping
import hashlib
import json
import re
import zlib

from mfdataindia.review.policy import fingerprint

FORMAT = "mfdata-amc-certification-input-v1"
RAW = "AMC_BOOK_CERTIFICATION"
HASH = RAW + "_HASH"
HEADER = RAW + "_TRANSPORT"
PREFIX = RAW + "_CHUNK_"
MAX_RAW_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 24000
MAX_CHUNKS = 64
MAX_ENCODED_BYTES = CHUNK_BYTES * MAX_CHUNKS
MAX_DESCRIPTOR_BYTES = 4096
MAX_ENV_BYTES = 1572864
MAX_VALUE_BYTES = 32768
MAX_ARGUMENT_BYTES = 131072  # includes the terminating NUL, never increased
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_ENV_ENTRIES = 1000
STARTUP_RESERVE_BYTES = 131072
STARTUP_MARGIN_BYTES = 131072
PHASES = {
    "08-candidate": (False, False),
    "11-active": (True, False),
    "13-recovery": (False, True),
    "15-restored": (True, False),
    "18-scheduled-http": (True, False),
}
_FIELDS = {
    "format", "codec", "raw_bytes", "raw_sha256", "encoded_bytes",
    "encoded_sha256", "count", "manifest_hash", "phase", "execution_id",
    "certification_hash",
}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate certification input key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("nonfinite certification input")


def _json(raw):
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid certification input JSON") from None


def _environment(environment):
    pairs = environment.items() if isinstance(environment, Mapping) else environment
    result = {}
    for key, value in pairs:
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,255}", key)
                or not isinstance(value, str) or "\0" in value or key in result):
            raise ValueError("invalid or duplicate certification environment entry")
        result[key] = value
    return result


def _context(manifest_hash, phase, execution_id):
    if (not _digest(manifest_hash) or not isinstance(phase, str) or phase not in PHASES
            or not isinstance(execution_id, str)
            or re.fullmatch(r"tlws-mf-official-facts-refresh-dev-[a-z0-9-]{1,40}", execution_id)
            is None):
        raise ValueError("exact certification worker context required")


def submission_budget(environment, arguments=()):
    """Validate ALL plain submitted entries/argv before intent or dispatch.

    Caller must additionally bind the full request and secret references. This
    reserve is not permission to omit those fields from immutable verification.
    Native Linux exec proof is separately mandatory, not inferred from limits.
    """
    env = _environment(environment)
    try:
        sizes = [len(k.encode()) + len(v.encode()) + 2 for k, v in env.items()]
        values = [len(v.encode()) for v in env.values()]
        args = [len(a.encode()) + 1 for a in arguments if isinstance(a, str) and "\0" not in a]
        if len(args) != len(arguments):
            raise ValueError()
    except (UnicodeError, TypeError, ValueError):
        raise ValueError("invalid submitted certification bytes") from None
    if (len(env) > MAX_ENV_ENTRIES or sum(sizes) > MAX_ENV_BYTES
            or any(n > MAX_VALUE_BYTES for n in values)
            or any(n > MAX_ARGUMENT_BYTES for n in args) or sum(args) > MAX_ARGUMENT_BYTES):
        raise ValueError("certification submission exceeds fixed byte budget")
    return {"plain_env_bytes": sum(sizes), "plain_env_count": len(env),
            "argv_bytes": sum(args), "maximum_value_bytes": max(values, default=0),
            "startup_required_bytes": sum(sizes) + sum(args)
            + STARTUP_RESERVE_BYTES + STARTUP_MARGIN_BYTES}


def _logical(raw, certification_hash, manifest_hash, execution_id):
    doc = _json(raw)
    if (doc.get("execution_id") != execution_id or not _digest(certification_hash)
            or fingerprint({"manifest": manifest_hash, "certification": doc})
            != certification_hash):
        raise ValueError("original certification hash or execution binding differs")
    return doc


def encode_environment(environment, *, manifest_hash, phase, execution_id):
    """Deterministically replace exactly one raw input, retaining its original hash."""
    _context(manifest_hash, phase, execution_id)
    env = _environment(environment)
    if HEADER in env or any(k.startswith(PREFIX) for k in env) or RAW not in env:
        raise ValueError("one raw certification input required before encoding")
    try:
        raw = env[RAW].encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid certification UTF8") from None
    if not 0 < len(raw) <= MAX_RAW_BYTES:
        raise ValueError("certification input exceeds raw byte budget")
    _logical(raw, env.get(HASH), manifest_hash, execution_id)
    body = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    if not 0 < len(body) <= MAX_ENCODED_BYTES:
        raise ValueError("certification input exceeds encoded byte budget")
    count = (len(body) + CHUNK_BYTES - 1) // CHUNK_BYTES
    descriptor = {
        "format": FORMAT, "codec": "zlib-base64", "raw_bytes": len(raw),
        "raw_sha256": _sha(raw), "encoded_bytes": len(body),
        "encoded_sha256": _sha(body.encode("ascii")), "count": count,
        "manifest_hash": manifest_hash, "phase": phase, "execution_id": execution_id,
        "certification_hash": env[HASH],
    }
    result = {k: v for k, v in env.items() if k != RAW}
    result[HEADER] = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    result.update({PREFIX + f"{i:02d}": body[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]
                   for i in range(count)})
    submission_budget(result)
    # The very same strict decoder preflights the exact submitted representation.
    if decode_environment(result, manifest_hash=manifest_hash, phase=phase,
                          execution_id=execution_id) != env:
        raise ValueError("certification input roundtrip differs")
    return result


def decode_environment(environment, *, manifest_hash, phase, execution_id):
    """Require complete unique chunks and return a NEW logical-only mapping."""
    _context(manifest_hash, phase, execution_id)
    env = _environment(environment)
    # os.environ additionally contains image/platform/secret-injected entries.
    # Do NOT pretend those are all submitted plain values, or spend the reserved
    # startup allowance twice. Operator submission retains its stricter cap.
    if (len(env) > MAX_ENV_ENTRIES
            or any(len(v.encode()) > MAX_VALUE_BYTES for v in env.values())
            or sum(len(k.encode()) + len(v.encode()) + 2 for k, v in env.items())
            > MAX_ENV_BYTES + STARTUP_RESERVE_BYTES):
        raise ValueError("certification worker environment exceeds reserved startup budget")
    if RAW in env or HEADER not in env:
        raise ValueError("one chunked certification input required")
    header = env[HEADER]
    if len(header.encode()) > MAX_DESCRIPTOR_BYTES:
        raise ValueError("certification descriptor exceeds byte budget")
    doc = _json(header)
    if (set(doc) != _FIELDS or doc["format"] != FORMAT or doc["codec"] != "zlib-base64"
            or type(doc["raw_bytes"]) is not int or not 0 < doc["raw_bytes"] <= MAX_RAW_BYTES
            or type(doc["encoded_bytes"]) is not int
            or not 0 < doc["encoded_bytes"] <= MAX_ENCODED_BYTES
            or doc["encoded_bytes"] % 4 != 0 or type(doc["count"]) is not int
            or not 1 <= doc["count"] <= MAX_CHUNKS
            or doc["count"] != (doc["encoded_bytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES
            or not _digest(doc["raw_sha256"]) or not _digest(doc["encoded_sha256"])
            or doc["manifest_hash"] != manifest_hash or doc["phase"] != phase
            or doc["execution_id"] != execution_id or doc["certification_hash"] != env.get(HASH)):
        raise ValueError("certification descriptor or worker context differs")
    keys = {PREFIX + f"{i:02d}" for i in range(doc["count"])}
    if {k for k in env if k.startswith(PREFIX)} != keys:
        raise ValueError("exact complete certification chunk indices required")
    parts = [env[PREFIX + f"{i:02d}"] for i in range(doc["count"])]
    if any(len(p) != min(CHUNK_BYTES, doc["encoded_bytes"] - i * CHUNK_BYTES)
           for i, p in enumerate(parts)):
        raise ValueError("certification chunk lengths differ")
    body = "".join(parts)
    try:
        encoded = body.encode("ascii")
        if _sha(encoded) != doc["encoded_sha256"]:
            raise ValueError()
        compressed = base64.b64decode(encoded, validate=True)
        if base64.b64encode(compressed) != encoded:
            raise ValueError()
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_RAW_BYTES + 1)
    except (ValueError, UnicodeError, zlib.error):
        raise ValueError("invalid compressed certification input") from None
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or len(raw) != doc["raw_bytes"] or len(raw) > MAX_RAW_BYTES
            or _sha(raw) != doc["raw_sha256"]):
        raise ValueError("complete certification input integrity differs")
    _logical(raw, env.get(HASH), manifest_hash, execution_id)
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("invalid certification UTF8") from None
    return {**{k: v for k, v in env.items() if k != HEADER and not k.startswith(PREFIX)},
            RAW: text}


def certify_from_environment(manifest, environment, *, phase, execution_id):
    """Decode before any DB/HTTP access, then invoke the UNCHANGED certifier."""
    from mfdataindia.enrichment.book_certification import certify

    logical = decode_environment(environment, manifest_hash=manifest.seal,
                                 phase=phase, execution_id=execution_id)
    active, recovery = PHASES[phase]
    return certify(manifest, logical, active=active, recovery=recovery)


def worker_program(*, manifest_hash, phase, execution_id):
    """Shared future-operator entry; expected context is literal program metadata.

    This does not dispatch anything or modify any historical operator/root.
    The original stdout certificate transport and seals remain unchanged.
    """
    _context(manifest_hash, phase, execution_id)
    return (
        "import os\n"
        "from mfdataindia.review.rollout import Manifest\n"
        "from mfdataindia.review.evidence_transport import render_evidence\n"
        "from mfdataindia.review.certification_input_transport import certify_from_environment\n"
        "manifest=Manifest.model_validate_json(os.environ['REVIEW_MANIFEST'])\n"
        f"if manifest.seal != {manifest_hash!r}: raise ValueError('worker manifest differs')\n"
        f"print(render_evidence(certify_from_environment(manifest,os.environ,phase={phase!r},"
        f"execution_id={execution_id!r})),flush=True)\n"
    )


def prepare_submission(manifest, environment, *, phase, execution_id):
    """Pure future-operator seam: preflight complete env/argv, NEVER dispatch.

    All five certification call sites must adopt this together in a separately
    reviewed fresh operator. Historical helpers/receipts are never rewritten.
    """
    env = _environment(environment)
    from mfdataindia.review.rollout import Manifest

    if Manifest.model_validate_json(env["REVIEW_MANIFEST"]).seal != manifest.seal:
        raise ValueError("submitted manifest differs")
    submitted = encode_environment(env, manifest_hash=manifest.seal,
                                  phase=phase, execution_id=execution_id)
    from mfdataindia.enrichment.book_certification import expectation

    # Same full original scope validation before any future dispatch intent;
    # the worker independently repeats it in unchanged certify(). No DB here.
    expectation(manifest, env)
    program = worker_program(manifest_hash=manifest.seal, phase=phase, execution_id=execution_id)
    arguments = ["-c", "exec(bytes.fromhex(" + repr(program.encode().hex()) + "))"]
    budget = submission_budget(submitted, arguments)
    return submitted, arguments, budget


def require_execution_inputs(container, *, environment, arguments, secret_entries=()):
    """Compare the WHOLE immutable args/env, including caller-pinned secret refs.

    Secret values are never accepted here. Mounted-secret volumes and the rest
    of the execution profile still require the original exact profile verifier.
    """
    submission_budget(environment, arguments)
    if container.get("args") != list(arguments):
        raise ValueError("immutable certification arguments differ")
    expected = [{"name": k, "value": v} for k, v in _environment(environment).items()]
    if any(not isinstance(e, dict) or set(e) != {"name", "valueFrom"} for e in secret_entries):
        raise ValueError("secret entries must be pinned references, never values")
    expected.extend(secret_entries)

    def entries(records):
        result = {}
        if not isinstance(records, (list, tuple)) or len(records) > MAX_ENV_ENTRIES:
            raise ValueError("bounded complete immutable environment required")
        for record in records:
            if (not isinstance(record, dict) or not isinstance(record.get("name"), str)
                    or record["name"] in result or set(record) not in (
                        {"name", "value"}, {"name", "valueFrom"})):
                raise ValueError("invalid or duplicate immutable environment entry")
            if "valueFrom" in record:
                ref = record["valueFrom"]
                if (not isinstance(ref, dict) or set(ref) != {"secretKeyRef"}
                        or not isinstance(ref["secretKeyRef"], dict)
                        or set(ref["secretKeyRef"]) != {"name", "key"}
                        or any(not isinstance(v, str) or not v for v in ref["secretKeyRef"].values())):
                    raise ValueError("exact immutable secret reference required")
            result[record["name"]] = record
        return result

    if entries(container.get("env")) != entries(expected):
        raise ValueError("whole immutable certification environment differs")


def preflight_request(request):
    """Bound the FULL serialized future request before its dispatch intent.

    This local conservative cap is not a provider-quota or startup-success
    claim. Caller must still validate the exact profile and env/argv separately.
    """
    try:
        if not isinstance(request, dict):
            raise ValueError()
        raw = json.dumps(request, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid complete certification request") from None
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("complete certification request exceeds byte budget")
    return {"request_bytes": len(raw), "request_sha256": _sha(raw)}
