"""Lossless bounded certificate transport, not a replacement for seal verification.

Cloud Run stdout entries truncate at 100 KiB. Large owner inspection results
therefore use a deterministic compressed envelope below that entry boundary.
The unchanged inner manifest/stage/hash seal must still pass verify_evidence.
"""

import base64
import hashlib
import json
import zlib

FORMAT = "mfdata-review-evidence-transport-v1"
CERTIFICATE_FORMAT = "mfdata-review-rollout-evidence-v1"
MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 80000


def _certificate(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "manifest_hash", "stage", "evidence", "sha256"}
        or value["format"] != CERTIFICATE_FORMAT
        or not isinstance(value["evidence"], dict)
    ):
        raise ValueError("one complete rollout certificate required")
    return value


def render_evidence(certificate):
    """Return one bounded JSON line, preserving every byte of the inner seal."""
    _certificate(certificate)
    raw = json.dumps(certificate, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_RAW_BYTES:
        raise ValueError("certificate exceeds raw transport bound")
    if len(raw) <= MAX_LOG_BYTES:
        return raw.decode()
    envelope = {
        "format": FORMAT,
        "codec": "zlib-base64",
        "raw_bytes": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "body": base64.b64encode(zlib.compress(raw, 9)).decode(),
    }
    result = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    if len(result.encode()) > MAX_LOG_BYTES:
        raise ValueError("certificate exceeds compressed log transport bound")
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate compressed certificate key")
        result[key] = value
    return result


def decode_evidence(value):
    """Decode recognized evidence only; malformed recognized envelopes fail closed."""
    if not isinstance(value, dict):
        return None
    if value.get("format") == CERTIFICATE_FORMAT:
        return _certificate(value)
    if value.get("format") != FORMAT:
        return None
    if (
        set(value) != {"format", "codec", "raw_bytes", "raw_sha256", "body"}
        or value["codec"] != "zlib-base64"
        or type(value["raw_bytes"]) is not int
        or not 0 < value["raw_bytes"] <= MAX_RAW_BYTES
        or not isinstance(value["raw_sha256"], str)
        or len(value["raw_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in value["raw_sha256"])
        or not isinstance(value["body"], str)
        or not 0 < len(value["body"]) <= MAX_LOG_BYTES
    ):
        raise ValueError("bounded recognized evidence transport required")
    try:
        compressed = base64.b64decode(value["body"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_RAW_BYTES + 1)
    except (ValueError, zlib.error):
        raise ValueError("invalid compressed certificate transport") from None
    if (
        not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or len(raw) != value["raw_bytes"]
        or len(raw) > MAX_RAW_BYTES
        or hashlib.sha256(raw).hexdigest() != value["raw_sha256"]
    ):
        raise ValueError("complete compressed certificate integrity differs")
    try:
        certificate = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid complete certificate JSON") from None
    return _certificate(certificate)


def execution_evidence(logs):
    """Keep the one-certificate rule across legacy and compressed log payloads."""
    certificates = []
    for log in logs:
        value = log.get("jsonPayload")
        if value is None and log.get("textPayload"):
            try:
                value = json.loads(log["textPayload"])
            except ValueError:
                continue
        certificate = decode_evidence(value)
        if certificate is not None:
            certificates.append(certificate)
    if len(certificates) != 1:
        raise ValueError("require exactly one structured execution certificate")
    return certificates[0]
