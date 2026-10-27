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

from mfdataindia.review import broad_reader_policy as engine
from mfdataindia.review.certification_input_transport import (
    CHUNK_BYTES, MAX_CHUNKS, MAX_RAW_BYTES, submission_budget,
)
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review.rollout import Manifest
from mfdataindia.review import broad_reader_capture_controller_v3 as capture_core
from mfdataindia.review import broad_reader_capture_spec_v3 as capture_spec
from psycopg import sql

IDENTITY = "dev-reader-broad-261027t"
JOB = "mf-reader-broad-access-261027t"
STAGE = "broad-reader-grant-not-fs13-acceptance"
ARTIFACTS = Path("/Users/deviojha/projects/codex/artifacts")
OWNER_SA = "tlws-mf-data-migrate-dev@trillionlane-dev.iam.gserviceaccount.com"
SECRET = "tlws-mf-data-admin-dsn-dev"
ENGINE_SHA = "eec850a112b39b5fb147653dba2e17c8452d29592dcd4042d4dfe36de3267653"
PROFILE_SHA = "5519d0af8782e18d9a3ef81494d2fec4c002f43a2f079a1310f0770f6dc15edf"
CAPTURE_CONTROLLER_SHA = "37dd6114da176da359a0dbeefc4c476328898c2279fb84efa5158297d43d2df6"
HEADER = "BROAD_READER_ACCESS_GRANT_TRANSPORT"
PREFIX = "BROAD_READER_ACCESS_GRANT_CHUNK_"
HASH = "BROAD_READER_ACCESS_PAYLOAD_SHA256"
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
    engine.catalogue(state)


def timestamp(value):
    require(type(value) is str and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value) is not None)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_window(window):
    keys(window, ("reference", "starts_at", "ends_at", "captured_at", "exclusive_admin", "recursive_availability_approved", "data_schema_custody", "future_owner_provisioning"))
    require(type(window["reference"]) is str and re.fullmatch(r"[A-Za-z0-9_:.#/-]{1,160}", window["reference"]) is not None)
    require(all(window[k] is True for k in ("exclusive_admin", "recursive_availability_approved",
                                           "data_schema_custody", "future_owner_provisioning")))
    start, end, capture = [timestamp(window[k]) for k in ("starts_at", "ends_at", "captured_at")]
    require(20 <= (end - start).total_seconds() <= 600 and start <= capture < end)


def live_window(window, *, now=None, remaining=20):
    validate_window(window)
    now = datetime.now(timezone.utc) if now is None else now
    require(isinstance(now, datetime) and now.tzinfo == timezone.utc)
    require(timestamp(window["starts_at"]) <= timestamp(window["captured_at"]) <= now)
    require((timestamp(window["ends_at"]) - now).total_seconds() >= remaining)


def witness_shape(rows, before):
    require(type(rows) is list)
    wanted = [{k: r[k] for k in capture_spec.frozen.CANDIDATE_COLUMNS}
              for r in before["relations"] if r["relkind"] in ("r", "p", "v")]
    require(rows == sorted(wanted, key=lambda r: r["oid"]))


def engine_document(proposal):
    w = proposal["window"]
    window = {"starts_at": timestamp(w["starts_at"]).isoformat(),
              "ends_at": timestamp(w["ends_at"]).isoformat(),
              "exclusive_administration": w["exclusive_admin"],
              "data_schema_custody": w["data_schema_custody"],
              "future_owner_provisioning": w["future_owner_provisioning"]}
    return engine.approval_document(proposal["change"], window=window)


def reversal(change):
    commands = []
    for item in reversed(change["delta"]["defaults"]):
        commands.append(sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE SELECT ON TABLES FROM {} RESTRICT"
        ).format(sql.Identifier(engine.OWNER), sql.Identifier(item["schema"]), sql.Identifier(engine.READER)).as_string())
    for item in reversed(change["delta"]["relations"]):
        commands.append(sql.SQL("REVOKE SELECT ON TABLE {}.{} FROM {} RESTRICT").format(
            sql.Identifier(item["schema"]), sql.Identifier(item["name"]), sql.Identifier(engine.READER)).as_string())
    for item in reversed(change["delta"]["schemas"]):
        commands.append(sql.SQL("REVOKE USAGE ON SCHEMA {} FROM {} RESTRICT").format(
            sql.Identifier(item["schema"]), sql.Identifier(engine.READER)).as_string())
    return {"not_executable": True, "separate_exact_approval_required": True,
            "original_change_sha256": fingerprint(change), "delta": deepcopy(change["delta"]),
            "expected_before_sha256": change["after_sha256"],
            "expected_after_sha256": change["before_sha256"], "sql_template": commands,
            "defaults_reversal_does_not_revoke_inherited_object_grants": True}


def proposal(capture_completion, window):
    capture = capture_core.validate_completion(capture_completion)
    validate_window(window)
    require(timestamp(window["captured_at"]) == datetime.fromisoformat(
        capture["context"]["transaction_started_at"].replace("Z", "+00:00")).replace(microsecond=0))
    before = capture["state"]
    state_shape(before)
    change = engine.prepare(before)
    require(change["profile_sha256"] == PROFILE_SHA and bool(change["sql"]))
    locks = [deepcopy(r) for r in capture["candidate"] if r["relkind"] in ("r", "p", "v")]
    witness_shape(locks, change["before"])
    materialized = [deepcopy(r) for r in capture["candidate"] if r["relkind"] == "m"]
    return {"format": "broad-reader-owner-grant-proposal-v1", "proposal_only": True,
            "capture_completion": deepcopy(capture_completion), "change": change,
            "lock_witness": locks, "materialized_unlocked": materialized,
            "window": deepcopy(window), "reversal_template": reversal(change)}


def validate_proposal(value):
    keys(value, ("format", "proposal_only", "capture_completion", "change", "lock_witness",
                 "materialized_unlocked", "window", "reversal_template"))
    expected = proposal(value["capture_completion"], value["window"])
    require(canonical(value) == canonical(expected))
    engine_document(value)
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
    require(re.fullmatch(r"mf-reader-broad-access-20261027-[A-Za-z0-9]{6,16}", root.name) is not None)
    require(type(value["g5_reference"]) is str and re.fullmatch(r"[A-Za-z0-9_:.#/-]{1,160}", value["g5_reference"]) is not None)
    p = validate_proposal(value["grant_proposal"])
    captured = capture_core.Approval.model_validate(p["capture_completion"]["approval"])
    require(captured.manifest.git_sha == m.git_sha and captured.manifest.image == m.image)
    require(captured.controller_sha256 == CAPTURE_CONTROLLER_SHA)
    return deepcopy(value)


def encode_payload(payload):
    payload = validate_payload(payload)
    raw = canonical(payload).encode()
    require(0 < len(raw) <= MAX_RAW_BYTES)
    body = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    count = (len(body) + CHUNK_BYTES - 1) // CHUNK_BYTES
    require(1 <= count <= MAX_CHUNKS)
    descriptor = {"format": "broad-reader-owner-input-v1", "codec": "zlib-base64", "raw_bytes": len(raw),
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
    require(d["format"] == "broad-reader-owner-input-v1" and d["codec"] == "zlib-base64" and d["identity"] == IDENTITY)
    require(type(d["count"]) is int and 1 <= d["count"] <= MAX_CHUNKS)
    require(type(d["raw_bytes"]) is int and 0 < d["raw_bytes"] <= MAX_RAW_BYTES)
    require(type(d["encoded_bytes"]) is int and 0 < d["encoded_bytes"] <= MAX_CHUNKS * CHUNK_BYTES)
    digest(d["raw_sha256"])
    digest(d["encoded_sha256"])
    wanted = {PREFIX + f"{i:02d}" for i in range(d["count"])}
    require({k for k in environment if k.startswith(PREFIX)} == wanted)
    require({k for k in environment if k.startswith("BROAD_READER_ACCESS_")} == wanted | {HEADER, HASH})
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
    document = engine_document(p)
    change = p["change"]
    result = {"stage": "COMMITTED_VERIFIED", "profile_sha256": PROFILE_SHA,
              "inputs_sha256": engine.fingerprint(document), "plan_sha256": engine.fingerprint(change),
              "before_sha256": change["before_sha256"], "after_sha256": change["after_sha256"],
              "delta": deepcopy(change["delta"]), "coverage": deepcopy(change["coverage"]),
              "defaults_reversal_does_not_revoke_inherited_object_grants": True,
              "administrative_and_data_schema_custody_are_external_trust": True,
              "postcommit_permanence_guarantee": False, "performance_or_FS13_acceptance": False}
    result["receipt_sha256"] = engine.fingerprint(result)
    return result
