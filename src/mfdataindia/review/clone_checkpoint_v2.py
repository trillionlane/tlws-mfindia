"""Versioned create-only handoffs for three disposable PG18 clone jobs.

Not an activation certificate, a live database client or a restore waiver.
The parent must additionally verify ALL THREE immutable executions and every logical
restore seal. Each phase retains its existing 900s/2CPU/4Gi bounded profile.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import quote

from mfdataindia.review.regional_backup import upload_create_only

BUCKET = "tlws_mf_data_source"
MAX_DUMP = 500 * 1024 * 1024
MAX_STATE = 32 * 1024 * 1024
CONTEXT_KEYS = {
    "format",
    "manifest_sha256",
    "backup",
    "app_image",
    "tools_image",
    "git_sha",
    "helper_hashes",
    "reference_sha256",
    "checkpoint_prefix",
    "phase",
    "previous_checkpoint_sha256",
    "previous_execution_sha256",
}
OBJECT_KEYS = {"uri", "generation", "size", "sha256", "md5"}


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("exact SHA256 required")
    return value


def _json_types(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for member in value:
            _json_types(member)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for member in value.values():
            _json_types(member)
        return
    raise ValueError("lossless JSON types required; never coerce checkpoint values")


def json_bytes(value):
    _json_types(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate checkpoint JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("nonfinite checkpoint JSON")

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    _json_types(value)
    return value


def validate_context(context):
    if not isinstance(context, dict) or set(context) != CONTEXT_KEYS:
        raise ValueError("complete exact checkpoint bindings required")
    if context["format"] != "mfdata-disposable-clone-checkpoint-v2":
        raise ValueError("unknown checkpoint format")
    _sha(context["manifest_sha256"])
    _sha(context["reference_sha256"])
    if not re.fullmatch("[0-9a-f]{40}", context["git_sha"]):
        raise ValueError("exact merged Git SHA required")
    if not re.fullmatch(
        r"verified-review-rollouts/dev-uti-gap-(?:[0-9]{6}g|261020h)-(acquire|rehearsal)-checkpoint/",
        context["checkpoint_prefix"],
    ):
        raise ValueError("fresh approved G/H stage-specific private checkpoint prefix required")
    phase = context["phase"]
    if phase not in ("acquire", "rehearsal") or not context["checkpoint_prefix"].endswith(
        "-" + phase + "-checkpoint/"
    ):
        raise ValueError("checkpoint phase and exact private prefix differ")
    previous = (context["previous_checkpoint_sha256"], context["previous_execution_sha256"])
    if phase == "acquire":
        if previous != (None, None):
            raise ValueError("first checkpoint cannot carry predecessor authority")
    else:
        for value in previous:
            _sha(value)
    rollout = context["checkpoint_prefix"].split("/")[1].removesuffix("-" + phase + "-checkpoint")
    backup_prefix = (
        "gs://"
        + BUCKET
        + "/verified-review-rollouts/"
        + rollout.replace("dev-uti-gap-", "dev-uti-gap-pre-", 1)
        + "/"
    )
    validate_object(context["backup"], backup_prefix, "snapshot.dump", MAX_DUMP)
    for key, repository in (
        ("app_image", "mfdataindia"),
        ("tools_image", "mfdataindia-review-tools"),
    ):
        if not re.fullmatch(
            "asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/"
            + repository
            + "@sha256:[0-9a-f]{64}",
            context[key],
        ):
            raise ValueError("exact immutable image required")
    helpers = context["helper_hashes"]
    if (
        not isinstance(helpers, dict)
        or not helpers
        or any(not re.fullmatch("[a-z][a-z0-9_]*\\.(?:py|json)", name) for name in helpers)
    ):
        raise ValueError("complete frozen helper bindings required")
    for value in helpers.values():
        _sha(value)
    json_bytes(context)
    return context


def validate_object(meta, prefix, filename, maximum):
    if (
        not isinstance(meta, dict)
        or set(meta) != OBJECT_KEYS
        or meta["uri"] != prefix + filename
        or type(meta["generation"]) is not str
        or not re.fullmatch("[1-9][0-9]{0,30}", meta["generation"])
        or type(meta["size"]) is not int
        or not 0 < meta["size"] <= maximum
    ):
        raise ValueError("exact generation-pinned bounded checkpoint object required")
    _sha(meta["sha256"])
    try:
        decoded = base64.b64decode(meta["md5"], validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("exact checkpoint MD5 required") from error
    if len(decoded) != 16 or base64.b64encode(decoded).decode() != meta["md5"]:
        raise ValueError("exact checkpoint MD5 required")
    return meta


def _prefix(context):
    validate_context(context)
    return "gs://" + BUCKET + "/" + context["checkpoint_prefix"]


def verify_certificate(context, certificate):
    prefix = _prefix(context)
    if (
        not isinstance(certificate, dict)
        or set(certificate) != {"context_sha256", "dump", "state"}
        or certificate["context_sha256"] != digest(context)
    ):
        raise ValueError("checkpoint certificate context differs")
    validate_object(certificate["dump"], prefix, "snapshot.dump", MAX_DUMP)
    validate_object(certificate["state"], prefix, "state.json", MAX_STATE)
    return certificate


def export_checkpoint(session, token, directory: Path, context, state, logical_seals):
    """Two create-only objects, no retry and no credential/state logging."""
    prefix = _prefix(context)
    if (
        not isinstance(state, dict)
        or not state
        or not isinstance(logical_seals, dict)
        or not logical_seals
    ):
        raise ValueError("complete continuation state and logical restore seals required")
    json_bytes(state)
    json_bytes(logical_seals)
    dump = directory / "checkpoint.dump"
    if dump.is_symlink() or not dump.is_file() or not 0 < dump.stat().st_size <= MAX_DUMP:
        raise ValueError("bounded actual local checkpoint dump required")
    uploaded = upload_create_only(
        session, token, context["checkpoint_prefix"] + "snapshot.dump", dump
    )
    validate_object(uploaded, prefix, "snapshot.dump", MAX_DUMP)
    envelope = {
        "context": context,
        "dump": uploaded,
        "state": state,
        "logical_seals": logical_seals,
    }
    raw = json_bytes(envelope)
    if len(raw) > MAX_STATE:
        raise ValueError("bounded complete checkpoint state required; no truncation")
    state_path = directory / "checkpoint-state.json"
    with state_path.open("xb") as handle:
        handle.write(raw)
    state_object = upload_create_only(
        session, token, context["checkpoint_prefix"] + "state.json", state_path
    )
    result = {"context_sha256": digest(context), "dump": uploaded, "state": state_object}
    return verify_certificate(context, result)


def download(session, token, meta, prefix, filename, maximum, destination: Path):
    validate_object(meta, prefix, filename, maximum)
    name = meta["uri"].removeprefix("gs://" + BUCKET + "/")
    response = session.get(
        "https://storage.googleapis.com/storage/v1/b/" + BUCKET + "/o/" + quote(name, safe=""),
        params={"alt": "media", "generation": meta["generation"]},
        headers={"Authorization": "Bearer " + token},
        stream=True,
        timeout=180,
        allow_redirects=False,
    )
    try:
        if response.status_code != 200:
            raise ValueError("exact private checkpoint generation unavailable")
        size = 0
        sha = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        with destination.open("xb") as handle:
            for block in response.iter_content(65536):
                size += len(block)
                if size > meta["size"]:
                    raise ValueError("checkpoint exceeded pinned original bytes")
                sha.update(block)
                md5.update(block)
                handle.write(block)
        if (
            size != meta["size"]
            or sha.hexdigest() != meta["sha256"]
            or base64.b64encode(md5.digest()).decode() != meta["md5"]
        ):
            raise ValueError("checkpoint original bytes differ")
    finally:
        response.close()


def import_checkpoint(session, token, directory: Path, context, certificate):
    verify_certificate(context, certificate)
    prefix = _prefix(context)
    state_path = directory / "checkpoint-state.json"
    download(session, token, certificate["state"], prefix, "state.json", MAX_STATE, state_path)
    envelope = strict_json(state_path.read_bytes())
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"context", "dump", "state", "logical_seals"}
        or json_bytes(envelope["context"]) != json_bytes(context)
        or envelope["dump"] != certificate["dump"]
        or not isinstance(envelope["state"], dict)
        or not envelope["state"]
        or not isinstance(envelope["logical_seals"], dict)
        or not envelope["logical_seals"]
    ):
        raise ValueError("checkpoint complete state or source bindings differ")
    download(
        session,
        token,
        certificate["dump"],
        prefix,
        "snapshot.dump",
        MAX_DUMP,
        directory / "checkpoint.dump",
    )
    return envelope


def require_restored_seals(expected, actual):
    """Never normalize/reseal differences or accept a subset of retained data."""
    if (
        not isinstance(expected, dict)
        or not expected
        or not isinstance(actual, dict)
        or json_bytes(expected) != json_bytes(actual)
    ):
        raise ValueError("complete restored checkpoint logical seals differ")
    return {
        "complete_checkpoint_logical_seals_matched": True,
        "checkpoint_logical_seal_sha256": digest(expected),
    }


def logical_metadata(conn):
    """Complete named local-cluster authority; physical OIDs are not portable.

    Row/byte and source seals are separately mandatory. This never edits a view,
    role or privilege and must be called in the same RR READONLY seal snapshot.
    """
    if (
        conn.info.host != "127.0.0.1"
        or conn.info.port != 55540
        or not re.fullmatch("mfdata_fs13_final_image_(?:[0-9]{6}g|261020h)", conn.info.dbname)
    ):
        raise ValueError("fresh approved local checkpoint database only; never live")
    settings = conn.execute(
        "SELECT current_setting('transaction_isolation') AS isolation,"
        "current_setting('transaction_read_only') AS readonly"
    ).fetchone()
    if settings != {"isolation": "repeatable read", "readonly": "on"}:
        raise ValueError("checkpoint metadata requires RR READONLY")
    queries = {
        "roles": """SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,
            rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil::text,rolconfig
            FROM pg_roles ORDER BY rolname""",
        "memberships": """SELECT roleid::regrole::text AS role,member::regrole::text AS member,
            grantor::regrole::text AS grantor,admin_option,inherit_option,set_option
            FROM pg_auth_members ORDER BY role,member,grantor""",
        "database": """SELECT datname,pg_get_userbyid(datdba) AS owner,encoding,
            datcollate,datctype,datlocprovider,datlocale,datallowconn,datconnlimit,
            CASE WHEN datacl IS NULL THEN NULL ELSE ARRAY(SELECT x::text FROM unnest(datacl) x ORDER BY x::text) END AS acl
            FROM pg_database WHERE datname=current_database()""",
        "defaults": """SELECT defaclrole::regrole::text AS role,
            CASE WHEN defaclnamespace=0 THEN '' ELSE defaclnamespace::regnamespace::text END AS schema,
            defaclobjtype,ARRAY(SELECT x::text FROM unnest(defaclacl) x ORDER BY x::text) AS acl
            FROM pg_default_acl ORDER BY role,schema,defaclobjtype""",
        "schemas": """SELECT nspname,pg_get_userbyid(nspowner) AS owner,
            CASE WHEN nspacl IS NULL THEN NULL ELSE ARRAY(SELECT x::text FROM unnest(nspacl) x ORDER BY x::text) END AS acl
            FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'
            ORDER BY nspname""",
        "relations": """SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner,
            CASE WHEN c.relacl IS NULL THEN NULL ELSE ARRAY(SELECT x::text FROM unnest(c.relacl) x ORDER BY x::text) END AS acl,
            c.reloptions,c.relrowsecurity,c.relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
            ORDER BY n.nspname,c.relname""",
        "columns": """SELECT n.nspname,c.relname,a.attname,a.attnum,
            format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,a.attgenerated,a.attidentity,
            CASE WHEN a.attacl IS NULL THEN NULL ELSE ARRAY(SELECT x::text FROM unnest(a.attacl) x ORDER BY x::text) END AS acl,
            pg_get_expr(d.adbin,d.adrelid) AS default_expression
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
              AND a.attnum>0 AND NOT a.attisdropped ORDER BY n.nspname,c.relname,a.attnum""",
        "views": """SELECT n.nspname,c.relname,pg_get_viewdef(c.oid,true) AS definition
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
              AND c.relkind IN('v','m') ORDER BY n.nspname,c.relname""",
        "functions": """SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) AS args,
            pg_get_userbyid(p.proowner) AS owner,
            CASE WHEN p.proacl IS NULL THEN NULL ELSE ARRAY(SELECT x::text FROM unnest(p.proacl) x ORDER BY x::text) END AS acl,p.proconfig,
            pg_get_functiondef(p.oid) AS definition FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
              AND p.prokind IN('f','p') ORDER BY n.nspname,p.proname,args""",
        "triggers": """SELECT n.nspname,c.relname,t.tgname,t.tgenabled,
            pg_get_triggerdef(t.oid,true) AS definition FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
              AND NOT t.tgisinternal ORDER BY n.nspname,c.relname,t.tgname""",
        "policies": """SELECT schemaname,tablename,policyname,permissive,roles,cmd,qual,with_check
            FROM pg_policies ORDER BY schemaname,tablename,policyname""",
        "settings": """SELECT COALESCE(d.datname,'') AS database,
            CASE WHEN s.setrole=0 THEN '' ELSE s.setrole::regrole::text END AS role,s.setconfig
            FROM pg_db_role_setting s LEFT JOIN pg_database d ON d.oid=s.setdatabase
            ORDER BY database,role""",
        "indexes": """SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname NOT LIKE 'pg_%' AND schemaname<>'information_schema'
            ORDER BY schemaname,tablename,indexname""",
        "constraints": """SELECT n.nspname,c.relname,k.conname,k.contype,
            pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k
            JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
            ORDER BY n.nspname,c.relname,k.conname""",
        "sequences": """SELECT schemaname,sequencename,sequenceowner,data_type::text,
            start_value,min_value,max_value,increment_by,cycle,cache_size,last_value
            FROM pg_sequences WHERE schemaname NOT LIKE 'pg_%'
              AND schemaname<>'information_schema' ORDER BY schemaname,sequencename""",
    }
    result = {key: conn.execute(query).fetchall() for key, query in queries.items()}
    from mfdataindia.review.preservation import canonical_constraint
    from mfdataindia.review.policy import fingerprint
    from psycopg import sql

    for row in result["constraints"]:
        row["definition"] = fingerprint(canonical_constraint(row["definition"]))
    for row in result["sequences"]:
        row["state"] = conn.execute(
            sql.SQL("SELECT last_value,is_called FROM {}.{}").format(
                sql.Identifier(row["schemaname"]), sql.Identifier(row["sequencename"])
            )
        ).fetchone()
    json_bytes(result)
    return result
