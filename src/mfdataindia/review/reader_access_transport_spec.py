"""Fixed nonsecret grant proposal and finite A->E->B->L input transport.

A proposal or matching hash does not authenticate a human or authorize a write.
No provider/DSN/database operation or filesystem write occurs in constructors.
"""
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import zlib

from mfdataindia.review import reader_access_correction as engine
from mfdataindia.review.certification_input_transport import (
    CHUNK_BYTES, MAX_CHUNKS, MAX_RAW_BYTES, submission_budget,
)
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review.rollout import Manifest

IDENTITY = "dev-reader-acl-261025a"
JOB = "mf-reader-access-correction-261025a"
STAGE = "reader-column-grant-not-fs13-acceptance"
ARTIFACTS = Path("/Users/deviojha/projects/codex/artifacts")
OWNER_SA = "tlws-mf-data-migrate-dev@trillionlane-dev.iam.gserviceaccount.com"
SECRET = "tlws-mf-data-admin-dsn-dev"
ENGINE_SHA = "863fd37dfa45be74642cf731f44bbd7cc182e263c95309a7d48a4c91af315d00"
PROFILE_SHA = "5d54dbee9631c806b039306599ad0de0e07533fe4be54e93c6d9a817bcf5fdb9"
HEADER = "READER_ACCESS_GRANT_TRANSPORT"
PREFIX = "READER_ACCESS_GRANT_CHUNK_"
HASH = "READER_ACCESS_PAYLOAD_SHA256"
SCHEMAS = {
    "relations": "oid namespace_oid nspname relname relkind owner attribute_count row_security force_row_security options definition acl capabilities sequence_capabilities",
    "columns": "oid attnum name dropped type_oid type_modifier collation_oid not_null acl capabilities",
    "namespaces": "oid nspname owner acl usage create_allowed",
    "databases": "oid datname owner acl connect_allowed create_allowed temporary_allowed",
    "functions": "oid pronamespace proname proowner prosecdef prokind proargtypes prorettype proconfig private_body_sha256 acl execute_allowed",
    "defaults": "oid defaclrole defaclnamespace defaclobjtype acl",
    "roles": "oid rolname rolsuper rolinherit rolcreaterole rolcreatedb rolcanlogin rolreplication rolbypassrls rolconfig",
    "memberships": "roleid member grantor admin_option inherit_option set_option",
    "policies": "oid polrelid polname polcmd polpermissive polroles qual_hash check_hash",
}
BOOLS = set("row_security force_row_security dropped not_null usage create_allowed connect_allowed temporary_allowed prosecdef execute_allowed rolsuper rolinherit rolcreaterole rolcreatedb rolcanlogin rolreplication rolbypassrls admin_option inherit_option set_option polpermissive".split())
INTEGERS = set("oid namespace_oid owner attribute_count attnum type_oid type_modifier collation_oid pronamespace proowner prorettype defaclrole defaclnamespace roleid member grantor polrelid".split())


def require(ok):
    if not ok:
        raise ValueError("invalid fixed owner transport input")


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected))


def digest(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def parse(raw):
    def invalid(_):
        raise ValueError("nonfinite owner transport input")
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    require(type(value) is dict)
    return value


def seal(value):
    keys(value, ("is_null", "sha256", "utf8_bytes"))
    require(type(value["is_null"]) is bool and type(value["utf8_bytes"]) is int)
    if value["is_null"]:
        require(value["sha256"] is None and value["utf8_bytes"] == 0)
    else:
        digest(value["sha256"])
        require(value["utf8_bytes"] >= 0)


def state_shape(state):
    """Reject unknown fields even on unrelated catalog rows, before persistence."""
    keys(state, SCHEMAS)
    for group, columns in SCHEMAS.items():
        rows = state[group]
        require(type(rows) is list)
        for row in rows:
            keys(row, columns.split())
            for key, value in row.items():
                if key in BOOLS:
                    require(type(value) is bool)
                elif key in INTEGERS:
                    require(type(value) is int and -1 <= value <= 4294967295)
                elif key in ("definition", "rolconfig", "proconfig"):
                    seal(value)
                elif key == "acl":
                    require(type(value) is list)
                elif key in ("capabilities", "sequence_capabilities"):
                    require(value is None or (type(value) is list and len(value) in (3, 4)
                                              and all(type(v) is bool for v in value)))
                elif key == "options":
                    require(value is None or (type(value) is list and all(type(v) is str for v in value)))
                elif key.endswith("_sha256") or key in ("qual_hash", "check_hash"):
                    digest(value)
                else:
                    require(type(value) is str)
        ordering = ("roleid", "member", "grantor") if group == "memberships" else \
                   ("oid", "attnum") if group == "columns" else ("oid",)
        markers = [tuple(row[k] for k in ordering) for row in rows]
        require(markers == sorted(set(markers)))
    engine._validate_acl_atoms(state)
    engine._validate_settings_seals(state)


def timestamp(value):
    require(type(value) is str and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value) is not None)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_window(window):
    keys(window, ("reference", "starts_at", "ends_at", "captured_at", "exclusive_admin", "recursive_availability_approved"))
    require(type(window["reference"]) is str and re.fullmatch(r"[A-Za-z0-9_:.#/-]{1,160}", window["reference"]) is not None)
    require(window["exclusive_admin"] is True and window["recursive_availability_approved"] is True)
    start, end, capture = [timestamp(window[k]) for k in ("starts_at", "ends_at", "captured_at")]
    require(20 <= (end - start).total_seconds() <= 600 and start <= capture < end)


def live_window(window, *, now=None, remaining=20):
    validate_window(window)
    now = datetime.now(timezone.utc) if now is None else now
    require(isinstance(now, datetime) and now.tzinfo == timezone.utc)
    require(timestamp(window["starts_at"]) <= timestamp(window["captured_at"]) <= now)
    require((timestamp(window["ends_at"]) - now).total_seconds() >= remaining)


def witness_shape(rows, before):
    require(type(rows) is list and len(rows) >= 2)
    for row in rows:
        keys(row, ("oid", "nspname", "relname", "relkind", "owner", "definition"))
        require(type(row["oid"]) is int and 0 < row["oid"] <= 4294967295)
        require(type(row["owner"]) is int and 0 < row["owner"] <= 4294967295)
        require(all(type(row[k]) is str and bool(row[k]) for k in ("nspname", "relname")))
        require(row["relkind"] in ("r", "p", "v"))
        seal(row["definition"])
        require(row["definition"]["is_null"] is (row["relkind"] != "v"))
    require([r["oid"] for r in rows] == sorted({r["oid"] for r in rows}))
    for root in before["relations"]:
        if root["nspname"] == "mf" and root["relname"] in engine.TARGETS:
            expected = {k: root[k] for k in rows[0]}
            require(expected in rows)


def proposal(before, lock_witness, window):
    state_shape(before)
    validate_window(window)
    change = engine.prepare_change(before, window_reference=window["reference"])
    require(change["engine_sha256"] == ENGINE_SHA)
    witness_shape(lock_witness, before)
    require(bool(change["delta"]))
    return {"format": "reader-owner-grant-proposal-v1", "proposal_only": True,
            "change": change, "lock_witness": deepcopy(lock_witness), "window": deepcopy(window),
            "reversal_template": {"not_executable": True, "operation": "REVOKE_SELECT_COLUMNS_RESTRICT",
                "original_change_sha256": fingerprint(change), "expected_before_sha256": fingerprint(change["after"]),
                "expected_after_sha256": fingerprint(change["before"]), "delta": deepcopy(change["delta"]),
                "owner": engine.OWNER, "reader": engine.READER, "separate_exact_approval_required": True}}


def validate_proposal(value):
    keys(value, ("format", "proposal_only", "change", "lock_witness", "window", "reversal_template"))
    expected = proposal(value["change"]["before"], value["lock_witness"], value["window"])
    require(canonical(value) == canonical(expected))
    return deepcopy(expected)


def validate_payload(value):
    keys(value, ("manifest", "controller_git_sha", "controller_sha256", "protected_hash", "root", "grant_proposal", "g5_reference"))
    m = Manifest.model_validate(value["manifest"])
    require(canonical(value["manifest"]) == canonical(m.model_dump(mode="json")))
    require(m.rollout_id == IDENTITY and m.cohort == [154117] and m.canary_code == 154117)
    require(value["controller_git_sha"] == m.git_sha)
    digest(value["controller_sha256"])
    digest(value["protected_hash"])
    require(type(value["root"]) is str)
    root = Path(value["root"])
    require(root.parent == ARTIFACTS and root.resolve() == root and not root.is_symlink())
    require(re.fullmatch(r"mf-reader-access-correction-20261025-[A-Za-z0-9]{6,16}", root.name) is not None)
    require(type(value["g5_reference"]) is str and re.fullmatch(r"[A-Za-z0-9_:.#/-]{1,160}", value["g5_reference"]) is not None)
    validate_proposal(value["grant_proposal"])
    return deepcopy(value)


def encode_payload(payload):
    payload = validate_payload(payload)
    raw = canonical(payload).encode()
    require(0 < len(raw) <= MAX_RAW_BYTES)
    body = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    count = (len(body) + CHUNK_BYTES - 1) // CHUNK_BYTES
    require(1 <= count <= MAX_CHUNKS)
    descriptor = {"format": "reader-owner-input-v1", "codec": "zlib-base64", "raw_bytes": len(raw),
                  "raw_sha256": hashlib.sha256(raw).hexdigest(), "encoded_bytes": len(body),
                  "encoded_sha256": hashlib.sha256(body.encode()).hexdigest(), "count": count,
                  "identity": IDENTITY, "git_sha": payload["controller_git_sha"]}
    env = {HEADER: canonical(descriptor), HASH: fingerprint(payload)}
    env.update({PREFIX + f"{i:02d}": body[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES] for i in range(count)})
    submission_budget(env)
    require(decode_payload(env) == payload)
    return env


def decode_payload(environment):
    require(type(environment[HEADER]) is str and len(environment[HEADER].encode()) <= 4096)
    d = parse(environment[HEADER])
    keys(d, ("format", "codec", "raw_bytes", "raw_sha256", "encoded_bytes", "encoded_sha256", "count", "identity", "git_sha"))
    require(d["format"] == "reader-owner-input-v1" and d["codec"] == "zlib-base64" and d["identity"] == IDENTITY)
    require(type(d["count"]) is int and 1 <= d["count"] <= MAX_CHUNKS)
    require(type(d["raw_bytes"]) is int and 0 < d["raw_bytes"] <= MAX_RAW_BYTES)
    require(type(d["encoded_bytes"]) is int and 0 < d["encoded_bytes"] <= MAX_CHUNKS * CHUNK_BYTES)
    digest(d["raw_sha256"])
    digest(d["encoded_sha256"])
    wanted = {PREFIX + f"{i:02d}" for i in range(d["count"])}
    require({k for k in environment if k.startswith(PREFIX)} == wanted)
    require({k for k in environment if k.startswith("READER_ACCESS_")} == wanted | {HEADER, HASH})
    parts = [environment[PREFIX + f"{i:02d}"] for i in range(d["count"])]
    require(all(type(v) is str and 0 < len(v) <= CHUNK_BYTES for v in parts))
    require(all(len(v) == CHUNK_BYTES for v in parts[:-1]))
    body = "".join(parts)
    require(len(body) == d["encoded_bytes"] and hashlib.sha256(body.encode()).hexdigest() == d["encoded_sha256"])
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(body, validate=True), MAX_RAW_BYTES + 1)
    require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail)
    require(len(raw) == d["raw_bytes"] and hashlib.sha256(raw).hexdigest() == d["raw_sha256"])
    payload = validate_payload(parse(raw))
    require(canonical(payload).encode() == raw and payload["controller_git_sha"] == d["git_sha"])
    require(fingerprint(payload) == environment[HASH])
    return payload


def expected_engine_receipt(payload):
    p = validate_payload(payload)["grant_proposal"]
    result = {"stage": "COMMITTED_VERIFIED", "change": deepcopy(p["change"]),
              "administrative_window_is_external_trust_boundary": True,
              "postcommit_permanence_guarantee": False, "performance_or_FS13_acceptance": False}
    result["receipt_sha256"] = fingerprint(result)
    return result
