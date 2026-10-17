"""Lossless bounded certificate transport, not a replacement for seal verification.

Cloud Run stdout entries truncate at 100 KiB. Large owner inspection results
therefore use a deterministic compressed envelope or bounded complete chunks.
The unchanged inner manifest/stage/hash seal must still pass verify_evidence.
"""

import base64
import hashlib
import json
import zlib

FORMAT = "mfdata-review-evidence-transport-v1"
CHUNK_FORMAT = "mfdata-review-evidence-chunk-v1"
CERTIFICATE_FORMAT = "mfdata-review-rollout-evidence-v1"
MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 80000
CHUNK_BODY_BYTES = 60000
MAX_CHUNKS = 64


class ExecutionEvidenceNotDelivered(ValueError):
    """Only absence may be re-read; malformed/duplicate evidence never may."""


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
    """Return bounded JSON lines, preserving every byte of one inner seal."""
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
    if len(result.encode()) <= MAX_LOG_BYTES:
        return result
    body = envelope["body"]
    count = (len(body) + CHUNK_BODY_BYTES - 1) // CHUNK_BODY_BYTES
    if not 2 <= count <= MAX_CHUNKS:
        raise ValueError("certificate exceeds bounded multi-entry transport")
    # Every line is independently below the observed Cloud Run truncation
    # boundary. The reader requires the exact complete, unique ordered set and
    # the unchanged complete inner seal; never certify an available subset.
    lines = []
    for index in range(count):
        part = {
            "format": CHUNK_FORMAT,
            "codec": "zlib-base64",
            "raw_bytes": envelope["raw_bytes"],
            "raw_sha256": envelope["raw_sha256"],
            "encoded_bytes": len(body),
            "index": index,
            "count": count,
            "body": body[index * CHUNK_BODY_BYTES : (index + 1) * CHUNK_BODY_BYTES],
        }
        line = json.dumps(part, sort_keys=True, separators=(",", ":"))
        if len(line.encode()) > MAX_LOG_BYTES:
            raise ValueError("certificate chunk exceeds log bound")
        lines.append(line)
    return "\n".join(lines)


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
    return _decode_envelope(value, maximum_encoded_bytes=MAX_LOG_BYTES)


def _decode_envelope(value, *, maximum_encoded_bytes):
    if (
        set(value) != {"format", "codec", "raw_bytes", "raw_sha256", "body"}
        or value["codec"] != "zlib-base64"
        or type(value["raw_bytes"]) is not int
        or not 0 < value["raw_bytes"] <= MAX_RAW_BYTES
        or not isinstance(value["raw_sha256"], str)
        or len(value["raw_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in value["raw_sha256"])
        or not isinstance(value["body"], str)
        or not 0 < len(value["body"]) <= maximum_encoded_bytes
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


def _chunk(value):
    if (
        set(value)
        != {"format", "codec", "raw_bytes", "raw_sha256", "encoded_bytes", "index", "count", "body"}
        or value["codec"] != "zlib-base64"
        or type(value["raw_bytes"]) is not int
        or not 0 < value["raw_bytes"] <= MAX_RAW_BYTES
        or not isinstance(value["raw_sha256"], str)
        or len(value["raw_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in value["raw_sha256"])
        or type(value["encoded_bytes"]) is not int
        or not CHUNK_BODY_BYTES < value["encoded_bytes"] <= MAX_CHUNKS * CHUNK_BODY_BYTES
        or value["encoded_bytes"] % 4 != 0
        or type(value["count"]) is not int
        or not 2 <= value["count"] <= MAX_CHUNKS
        or value["count"] != (value["encoded_bytes"] + CHUNK_BODY_BYTES - 1) // CHUNK_BODY_BYTES
        or type(value["index"]) is not int
        or not 0 <= value["index"] < value["count"]
        or not isinstance(value["body"], str)
        or len(value["body"])
        != min(CHUNK_BODY_BYTES, value["encoded_bytes"] - value["index"] * CHUNK_BODY_BYTES)
    ):
        raise ValueError("bounded complete recognized certificate chunk required")
    try:
        base64.b64decode(value["body"], validate=True)
    except ValueError:
        raise ValueError("invalid recognized certificate chunk encoding") from None
    if value["index"] < value["count"] - 1 and "=" in value["body"]:
        raise ValueError("premature certificate chunk padding")
    return value


def execution_evidence(logs):
    """Keep the one-certificate rule across legacy and compressed log payloads."""
    certificates = []
    chunks = {}
    header = None
    for log in logs:
        value = log.get("jsonPayload")
        if value is None and log.get("textPayload"):
            try:
                value = json.loads(log["textPayload"])
            except ValueError:
                continue
        if isinstance(value, dict) and value.get("format") == CHUNK_FORMAT:
            part = _chunk(value)
            metadata = {k: v for k, v in part.items() if k not in ("index", "body")}
            if (header is not None and metadata != header) or part["index"] in chunks:
                raise ValueError("inconsistent or duplicate execution certificate chunks")
            header = metadata
            chunks[part["index"]] = part["body"]
            continue
        certificate = decode_evidence(value)
        if certificate is not None:
            certificates.append(certificate)
    if chunks:
        if certificates:
            raise ValueError("mixed execution certificate transports")
        if len(chunks) != header["count"]:
            raise ExecutionEvidenceNotDelivered(
                "incomplete structured execution certificate chunks"
            )
        body = "".join(chunks[i] for i in range(header["count"]))
        certificate = _decode_envelope(
            {
                "format": FORMAT,
                "codec": header["codec"],
                "raw_bytes": header["raw_bytes"],
                "raw_sha256": header["raw_sha256"],
                "body": body,
            },
            maximum_encoded_bytes=MAX_CHUNKS * CHUNK_BODY_BYTES,
        )
        return certificate
    if not certificates:
        raise ExecutionEvidenceNotDelivered("require exactly one structured execution certificate")
    if len(certificates) != 1:
        raise ValueError("require exactly one structured execution certificate")
    return certificates[0]
