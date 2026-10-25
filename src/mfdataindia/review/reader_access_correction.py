"""Dormant exact-column correction engine. No CLI, DSN, cloud or activation path.

An approved scope is not an approved write. A separately reviewed transport must
bind fresh inputs, attest the administrative/recursive-lock window, persist its
create-only intent and receive exact human approval before calling this module.
Private helpers are not operational entrypoints or identity-bypass authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import signal
import threading

from psycopg import sql
from psycopg.pq import TransactionStatus

from .reader_access_profile_v1 import profile

TARGETS = ("current_fund_facts", "fund_variants")
READER = "mfdata_verified_reader"
OWNER = "mfdata_admin"
RECONCILER_SHA256 = "b0b2bab8a7ece0384b96f658f6570fe2f4339bbda9862011381b470e5f000e38"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


PROFILE_SHA256 = "0377a4fadec994556d2aa6fe042b224a377f6ed8bd0eabfa986fe69b7eb0f28d"

# All observations are catalog/privilege inquiries, never application row reads.
# ACL ordering is canonical, including grantor/options; NULL column ACL == [].
# NULL object ACL is expanded with acldefault, not discarded as 'no privileges'.
ACL = """COALESCE((SELECT jsonb_agg(jsonb_build_array(x.grantor::bigint,x.grantee::bigint,
 x.privilege_type,x.is_grantable) ORDER BY x.grantor,x.grantee,x.privilege_type,x.is_grantable)
 FROM pg_catalog.aclexplode({}) x),'[]'::jsonb)"""
# Custom role/function settings may hold credentials. Only this server-side
# null/hash/UTF8-byte seal can cross the connection boundary, never the array.
SETTINGS_SEAL = """jsonb_build_object('is_null',{value} IS NULL,
 'sha256',CASE WHEN {value} IS NULL THEN NULL ELSE
 encode(sha256(convert_to(to_jsonb({value})::text,'UTF8')),'hex') END,
 'utf8_bytes',CASE WHEN {value} IS NULL THEN 0 ELSE
 octet_length(convert_to(to_jsonb({value})::text,'UTF8')) END)"""
STATE_SQL = """
WITH rels AS (
 SELECT c.*,n.nspname FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname IN ('mf','mf_review') AND c.relkind IN ('r','p','v','m','S','f')
), relations AS (
 SELECT c.oid::bigint AS oid,c.relnamespace::bigint AS namespace_oid,c.nspname,
 c.relname,c.relkind::text,c.relowner::bigint AS owner,c.relnatts::int AS attribute_count,
 c.relrowsecurity AS row_security,c.relforcerowsecurity AS force_row_security,
 c.reloptions AS options,
 CASE WHEN c.relkind='v' THEN jsonb_build_object('is_null',false,
 'sha256',encode(sha256(convert_to(pg_get_viewdef(c.oid,false),'UTF8')),'hex'),
 'utf8_bytes',octet_length(convert_to(pg_get_viewdef(c.oid,false),'UTF8')))
 ELSE jsonb_build_object('is_null',true,'sha256',null,'utf8_bytes',0) END AS definition,
 {relation_acl} AS acl,
 CASE WHEN c.relkind='S' THEN null ELSE
 jsonb_build_array(has_table_privilege('{reader}',c.oid,'SELECT'),
 has_table_privilege('{reader}',c.oid,'SELECT WITH GRANT OPTION'),
 has_table_privilege('{reader}',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'),
 has_any_column_privilege('{reader}',c.oid,'SELECT')) END AS capabilities,
 CASE WHEN c.relkind='S' THEN jsonb_build_array(
 has_sequence_privilege('{reader}',c.oid,'USAGE'),has_sequence_privilege('{reader}',c.oid,'SELECT'),
 has_sequence_privilege('{reader}',c.oid,'UPDATE')) ELSE null END AS sequence_capabilities
 FROM rels c
), columns AS (
 SELECT a.attrelid::bigint AS oid,a.attnum::int AS attnum,a.attname AS name,
 a.attisdropped AS dropped,a.atttypid::bigint AS type_oid,a.atttypmod AS type_modifier,
 a.attcollation::bigint AS collation_oid,a.attnotnull AS not_null,{column_acl} AS acl,
 CASE WHEN a.attisdropped OR c.relkind='S' THEN null ELSE jsonb_build_array(
 has_column_privilege('{reader}',a.attrelid,a.attnum,'SELECT'),
 has_column_privilege('{reader}',a.attrelid,a.attnum,'SELECT WITH GRANT OPTION'),
 has_column_privilege('{reader}',a.attrelid,a.attnum,'INSERT,UPDATE,REFERENCES'),
 has_column_privilege('{reader}',a.attrelid,a.attnum,'INSERT WITH GRANT OPTION,UPDATE WITH GRANT OPTION,REFERENCES WITH GRANT OPTION')) END AS capabilities
 FROM pg_catalog.pg_attribute a JOIN rels c ON c.oid=a.attrelid WHERE a.attnum>0
), namespaces AS (
 SELECT n.oid::bigint,n.nspname,n.nspowner::bigint AS owner,{schema_acl} AS acl,
 has_schema_privilege('{reader}',n.oid,'USAGE') AS usage,
 has_schema_privilege('{reader}',n.oid,'CREATE') AS create_allowed
 FROM pg_catalog.pg_namespace n WHERE n.nspname IN ('mf','mf_review','public')
), databases AS (
 SELECT d.oid::bigint,d.datname,d.datdba::bigint AS owner,{database_acl} AS acl,
 has_database_privilege('{reader}',d.oid,'CONNECT') AS connect_allowed,
 has_database_privilege('{reader}',d.oid,'CREATE') AS create_allowed,
 has_database_privilege('{reader}',d.oid,'TEMPORARY') AS temporary_allowed
 FROM pg_catalog.pg_database d WHERE d.datname=current_database()
), functions AS (
 SELECT p.oid::bigint,p.pronamespace::bigint,p.proname,p.proowner::bigint,
 p.prosecdef,p.prokind::text,p.proargtypes::text,p.prorettype::bigint,{function_settings} AS proconfig,
 encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') AS private_body_sha256,
 {function_acl} AS acl,has_function_privilege('{reader}',p.oid,'EXECUTE') AS execute_allowed
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname IN ('mf','mf_review')
), defaults AS (
 SELECT d.oid::bigint,d.defaclrole::bigint,d.defaclnamespace::bigint,d.defaclobjtype::text,
 {default_acl} AS acl FROM pg_catalog.pg_default_acl d
), roles AS (
 SELECT oid::bigint,rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,
 rolcanlogin,rolreplication,rolbypassrls,{role_settings} AS rolconfig FROM pg_catalog.pg_roles
), memberships AS (
 SELECT roleid::bigint,member::bigint,grantor::bigint,admin_option,inherit_option,set_option
 FROM pg_catalog.pg_auth_members
), policies AS (
 SELECT p.oid::bigint,p.polrelid::bigint,p.polname,p.polcmd::text,p.polpermissive,p.polroles::text,
 encode(sha256(convert_to(COALESCE(p.polqual::text,''),'UTF8')),'hex') AS qual_hash,
 encode(sha256(convert_to(COALESCE(p.polwithcheck::text,''),'UTF8')),'hex') AS check_hash
 FROM pg_catalog.pg_policy p JOIN rels c ON c.oid=p.polrelid
)
SELECT jsonb_build_object(
 'relations',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY oid) FROM relations r),'[]'),
 'columns',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY oid,attnum) FROM columns r),'[]'),
 'namespaces',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY oid) FROM namespaces r),'[]'),
 'databases',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY oid) FROM databases r),'[]'),
 'functions',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY oid) FROM functions r),'[]'),
 'defaults',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY oid) FROM defaults r),'[]'),
 'roles',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY oid) FROM roles r),'[]'),
 'memberships',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY roleid,member,grantor) FROM memberships r),'[]'),
 'policies',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY oid) FROM policies r),'[]')
) AS value
""".format(
    reader=READER,
    relation_acl=ACL.format("COALESCE(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 's'::\"char\" ELSE 'r'::\"char\" END,c.relowner))"),
    column_acl=ACL.format("a.attacl"), schema_acl=ACL.format("COALESCE(n.nspacl,acldefault('n',n.nspowner))"),
    database_acl=ACL.format("COALESCE(d.datacl,acldefault('d',d.datdba))"),
    function_acl=ACL.format("COALESCE(p.proacl,acldefault('f',p.proowner))"),
    default_acl=ACL.format("d.defaclacl"),
    function_settings=SETTINGS_SEAL.format(value="p.proconfig"),
    role_settings=SETTINGS_SEAL.format(value="rolconfig"))

CONTEXT_SQL = """SELECT current_database() AS database,current_user AS principal,
 session_user AS session, current_setting('server_version_num')::int AS version,
 current_setting('transaction_isolation') AS isolation,
 current_setting('transaction_read_only') AS readonly,
 current_setting('statement_timeout') AS statement_timeout,
 current_setting('lock_timeout') AS lock_timeout,
 current_setting('transaction_timeout') AS transaction_timeout,
 current_setting('search_path') AS search_path,current_setting('TimeZone') AS timezone"""


class ChangeFailure(RuntimeError):
    """Only safe fixed stages, never SQL/server/DSN error text."""


class WallTimeout(ChangeFailure):
    """The wall budget is spent; no further database I/O is permitted."""


@dataclass
class Permit:
    """Single-use binding, NOT proof that a human or live gate approved it."""
    document: dict
    approved_fingerprint: str
    used: bool = False
    _lock: object = field(default_factory=threading.Lock, repr=False, compare=False)

    def consume(self):
        with self._lock:
            document = deepcopy(self.document)
            if self.used or fingerprint(document) != self.approved_fingerprint:
                raise ChangeFailure("UNAPPROVED_OR_CONSUMED_INPUTS")
            self.used = True
            return document


def _index(rows, key):
    result = {tuple(r[k] for k in key): r for r in rows}
    if len(result) != len(rows):
        raise ChangeFailure("DUPLICATE_METADATA")
    return result


def _atom(row):
    return [row["grantor"], row["grantee"], row["privilege"], row["grantable"]]


def _validate_settings_seals(before):
    for group, setting_field in (("roles", "rolconfig"), ("functions", "proconfig")):
        for row in before[group]:
            seal = row.get(setting_field)
            if (not isinstance(seal, dict) or set(seal) != {"is_null", "sha256", "utf8_bytes"}
                    or type(seal["is_null"]) is not bool or type(seal["utf8_bytes"]) is not int
                    or (seal["is_null"] and (seal["sha256"] is not None or seal["utf8_bytes"] != 0))
                    or (not seal["is_null"] and (seal["utf8_bytes"] < 2 or not isinstance(seal["sha256"], str)
                                                or not re.fullmatch("[0-9a-f]{64}", seal["sha256"])))):
                raise ChangeFailure("PRIVATE_SETTINGS_SEAL_REQUIRED")


def _validate_acl_atoms(before):
    for group in ("relations", "columns", "namespaces", "databases", "functions", "defaults"):
        for row in before[group]:
            atoms = row["acl"]
            if (not isinstance(atoms, list) or any(not isinstance(a, list) or len(a) != 4
                    or type(a[0]) is not int or not 0 <= a[0] <= 4294967295
                    or type(a[1]) is not int or not 0 <= a[1] <= 4294967295
                    or not isinstance(a[2], str) or not a[2] or type(a[3]) is not bool for a in atoms)
                    or atoms != sorted(atoms) or len({tuple(a) for a in atoms}) != len(atoms)):
                raise ChangeFailure("CANONICAL_NUMERIC_ACL_ATOMS_REQUIRED")


def validate_frozen(before):
    """Production OIDs, every column and before ACL are non-configurable."""
    p = profile()
    if fingerprint(p) != PROFILE_SHA256:
        raise ChangeFailure("PROFILE_DRIFT")
    _validate_settings_seals(before)
    _validate_acl_atoms(before)
    roles = _index(before["roles"], ("rolname",))
    reader = roles.get((READER,))
    expected = {"oid": 40032, "rolname": READER, "rolsuper": False, "rolinherit": False,
                "rolcreaterole": False, "rolcreatedb": False, "rolcanlogin": True,
                "rolreplication": False, "rolbypassrls": False,
                "rolconfig": {"is_null": True, "sha256": None, "utf8_bytes": 0}}
    if reader != expected or any(m["member"] == 40032 for m in before["memberships"]):
        raise ChangeFailure("READER_IDENTITY_DRIFT")
    if (any(r["owner"] == 40032 or (r["capabilities"] and r["capabilities"][2])
            or (r["sequence_capabilities"] and (r["sequence_capabilities"][0] or r["sequence_capabilities"][2]))
            for r in before["relations"])
            or any(c["capabilities"] and any(c["capabilities"][1:]) for c in before["columns"])
            or any(n["create_allowed"] or n["owner"] == 40032 for n in before["namespaces"])
            or any(d["create_allowed"] or d["owner"] == 40032 for d in before["databases"])
            or any(f["proowner"] == 40032 or (f["prosecdef"] and f["execute_allowed"]) for f in before["functions"])):
        raise ChangeFailure("READER_WRITE_DDL_OR_ELEVATED_FUNCTION_CAPABILITY")
    if not any(n["nspname"] == "mf" and n["usage"] for n in before["namespaces"]):
        raise ChangeFailure("READER_SCHEMA_USAGE_REQUIRED")
    rels = _index(before["relations"], ("nspname", "relname"))
    cols = _index(before["columns"], ("oid", "attnum"))
    for item in p["objects"]:
        ident, definition = item["expected_identity"], item["relation_definition_and_security"]
        r = rels.get(("mf", ident["relname"]))
        expected_rel = dict(oid=ident["reloid"], namespace_oid=ident["nspoid"], nspname="mf",
                            relname=ident["relname"], relkind=ident["expected_kind"], owner=16587,
                            attribute_count=definition["attribute_count"],
                            row_security=definition["row_security"],
                            force_row_security=definition["force_row_security"],
                            options=definition["relation_options"], definition=definition["private_view_definition"])
        if not r or any(r[k] != v for k, v in expected_rel.items()):
            raise ChangeFailure("TARGET_IDENTITY_DEFINITION_DRIFT")
        acl_rows = item["direct_relation_and_all_column_ACLs_before"]
        root_acl = next(a for a in acl_rows if a["object_kind"] == "relation")
        if r["acl"] != sorted(_atom(a) for a in root_acl["normalized_acl"]):
            raise ChangeFailure("ROOT_ACL_DRIFT")
        for old in item["all_physical_columns"]:
            c = cols.get((r["oid"], old["attnum"]))
            expected_col = {k: v for k, v in old.items() if k != "relation_oid"}
            expected_col["oid"] = r["oid"]
            acl = next(a for a in acl_rows if a["object_kind"] == "column" and a["sub_id"] == old["attnum"])
            if (not c or any(c[k] != v for k, v in expected_col.items())
                    or c["acl"] != sorted(_atom(a) for a in acl["normalized_acl"])):
                raise ChangeFailure("COLUMN_TYPE_OR_ACL_DRIFT")
        if len([c for c in before["columns"] if c["oid"] == r["oid"]]) != len(item["all_physical_columns"]):
            raise ChangeFailure("COLUMN_POPULATION_DRIFT")
    owner = roles.get((OWNER,))
    if not owner or owner["oid"] != 16587 or owner["rolsuper"] or owner["rolbypassrls"] or owner["rolreplication"]:
        raise ChangeFailure("OWNER_DRIFT")


def _project(before, selected):
    """Exact normalized delta; never remove pre-existing column privileges."""
    _validate_acl_atoms(before)
    _validate_settings_seals(before)
    after, delta = deepcopy(before), []
    rels = _index(after["relations"], ("nspname", "relname"))
    roles = _index(after["roles"], ("rolname",))
    owner, reader = roles[(OWNER,)]["oid"], roles[(READER,)]["oid"]
    for name in TARGETS:
        rel = rels[("mf", name)]
        if rel["owner"] != owner or rel["capabilities"][0:3] != [False, False, False]:
            raise ChangeFailure("UNEXPECTED_EXISTING_TARGET_PRIVILEGES")
        columns = {c["name"]: c for c in after["columns"] if c["oid"] == rel["oid"] and not c["dropped"]}
        missing = []
        for name_col in selected[name]:
            c = columns[name_col]
            atom = [owner, reader, "SELECT", False]
            if c["capabilities"][1:] != [False, False, False]:
                raise ChangeFailure("UNEXPECTED_COLUMN_CAPABILITY")
            if atom not in c["acl"]:
                c["acl"] = sorted([*c["acl"], atom])
                missing.append(name_col)
            c["capabilities"][0] = True
        rel["capabilities"][3] = True
        if missing:
            delta.append({"relation": name, "columns": missing})
    return after, delta


def _selected():
    return {i["expected_identity"]["relname"]: [c["name"] for c in i["columns_in_actual_attnum_order"]]
            for i in profile()["objects"]}


def prepare_change(before, *, window_reference):
    """Offline only; output must later be separately approved, not auto-admitted."""
    validate_frozen(before)
    if not isinstance(window_reference, str) or not window_reference.strip():
        raise ChangeFailure("EXCLUSIVE_ADMIN_AND_RECURSIVE_LOCK_WINDOW_REQUIRED")
    after, delta = _project(before, _selected())
    return {"format": "reader-column-change-v1", "operation": "GRANT", "database": "mfdataindia",
            "owner": OWNER, "reader": READER, "profile_sha256": PROFILE_SHA256,
            "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "lifetime": "PERSISTENT_PRESENT_COLUMNS_ONLY", "window_reference": window_reference,
            "credential_reference": "tlws-mf-data-admin-dsn-dev:1",
            "service_account": "tlws-mf-data-migrate-dev@trillionlane-dev.iam.gserviceaccount.com",
            "before": deepcopy(before), "after": after, "delta": delta,
            "runtime_or_write_approval": False}


def prepare_reversal(receipt, *, window_reference):
    if receipt.get("stage") != "COMMITTED_VERIFIED" or receipt.get("receipt_sha256") != fingerprint(
            {k: v for k, v in receipt.items() if k != "receipt_sha256"}):
        raise ChangeFailure("EXACT_COMMITTED_RECEIPT_REQUIRED")
    original = receipt["change"]
    if original != prepare_change(original["before"], window_reference=original["window_reference"]):
        raise ChangeFailure("ORIGINAL_CHANGE_DRIFT")
    result = deepcopy(original)
    result.update(operation="REVOKE", before=deepcopy(original["after"]), after=deepcopy(original["before"]),
                  window_reference=window_reference, original_receipt_sha256=receipt["receipt_sha256"])
    if not isinstance(window_reference, str) or not window_reference.strip():
        raise ChangeFailure("EXCLUSIVE_ADMIN_AND_RECURSIVE_LOCK_WINDOW_REQUIRED")
    return result


def require_provisioning_admission(state, *, reconciler_bytes, explicitly_selected_profile):
    """Guard future composition, never execute/reapply the old broad reconciler."""
    if (hashlib.sha256(reconciler_bytes).hexdigest() != RECONCILER_SHA256
            or explicitly_selected_profile != PROFILE_SHA256):
        raise ChangeFailure("UNAPPROVED_PROVISIONING_COMPOSITION")
    prior = deepcopy(state)
    for name, names in _selected().items():
        r = next(r for r in prior["relations"] if r["nspname"] == "mf" and r["relname"] == name)
        rows = [c for c in prior["columns"] if c["oid"] == r["oid"] and c["name"] in names]
        if len(rows) != len(names) or any(c["capabilities"] != [True, False, False, False] for c in rows):
            raise ChangeFailure("PROFILE_ERASED_OR_UNAPPROVED_NO_AUTOMATIC_REAPPLY")
        r["capabilities"][3] = False
        for c in rows:
            atom = [16587, 40032, "SELECT", False]
            if atom not in c["acl"]:
                raise ChangeFailure("EXACT_OWNER_COLUMN_PROFILE_REQUIRED")
            c["acl"].remove(atom)
            c["capabilities"][0] = False
    validate_frozen(prior)
    if _project(prior, _selected())[0] != state:
        raise ChangeFailure("UNAPPROVED_PROVISIONING_COMPOSITION")


@contextmanager
def _deadline():
    if (threading.current_thread() is not threading.main_thread()
            or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)):
        raise ChangeFailure("EXCLUSIVE_MAIN_THREAD_WALL_TIMER_REQUIRED")
    previous = signal.getsignal(signal.SIGALRM)

    def timeout(*_):
        raise WallTimeout("WHOLE_OPERATION_15S_TIMEOUT")

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 15)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _begin(conn, *, readonly=False):
    conn.execute("BEGIN ISOLATION LEVEL READ COMMITTED " + ("READ ONLY" if readonly else "READ WRITE"), prepare=False)
    for setting in ("statement_timeout='5s'", "lock_timeout='5s'", "transaction_timeout='15s'",
                    "search_path=pg_catalog", "TimeZone='UTC'"):
        conn.execute("SET LOCAL " + setting, prepare=False)
    context = conn.execute(CONTEXT_SQL, prepare=False).fetchone()
    expected = dict(database="mfdataindia", principal=OWNER, session=OWNER, version=180006,
                    isolation="read committed", readonly="on" if readonly else "off",
                    statement_timeout="5s", lock_timeout="5s", transaction_timeout="15s",
                    search_path="pg_catalog", timezone="UTC")
    if context != expected:
        raise ChangeFailure("DIRECT_OWNER_CONTEXT_REQUIRED")


def _snapshot(conn):
    return conn.execute(STATE_SQL, prepare=False).fetchone()["value"]


def _execute(conn, change):
    """Internal mechanics; production calls only through run_change admission."""
    with _deadline():
        return _transact(conn, change)


def _transact(conn, change):
    stage = "BEFORE_BEGIN"
    try:
        _begin(conn)
        stage = "PRECOMMIT"
        # Direct GRANT/REVOKE and DDL conflict. Schema-wide ACL operations
        # are NOT universally excluded; exact comparison + admin window.
        for name in sorted(TARGETS):
            conn.execute(sql.SQL("LOCK TABLE ONLY {}.{} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
                sql.Identifier("mf"), sql.Identifier(name)), prepare=False)
        if _snapshot(conn) != change["before"]:
            raise ChangeFailure("FRESH_BEFORE_STATE_DRIFT")
        for item in change["delta"]:
            command = "GRANT SELECT ({}) ON TABLE {}.{} TO {}" if change["operation"] == "GRANT" else \
                      "REVOKE SELECT ({}) ON TABLE {}.{} FROM {} RESTRICT"
            conn.execute(sql.SQL(command).format(
                sql.SQL(",").join(sql.Identifier(c) for c in item["columns"]),
                sql.Identifier("mf"), sql.Identifier(item["relation"]), sql.Identifier(READER)), prepare=False)
        if _snapshot(conn) != change["after"]:
            raise ChangeFailure("EXACT_DELTA_OR_ADMINISTRATIVE_DRIFT")
        stage = "COMMIT_OUTCOME_UNKNOWN"
        conn.execute("COMMIT", prepare=False)
        stage = "COMMITTED_UNVERIFIED"
        _begin(conn, readonly=True)
        if _snapshot(conn) != change["after"]:
            raise ChangeFailure("FINAL_STATE_DRIFT")
        conn.execute("ROLLBACK", prepare=False)  # Read-only verification only.
        result = {"stage": "COMMITTED_VERIFIED", "change": deepcopy(change),
                  "administrative_window_is_external_trust_boundary": True,
                  "postcommit_permanence_guarantee": False,
                  "performance_or_FS13_acceptance": False}
        result["receipt_sha256"] = fingerprint(result)
        return result
    except BaseException as error:
        if stage in ("COMMIT_OUTCOME_UNKNOWN", "COMMITTED_UNVERIFIED"):
            raise ChangeFailure(stage) from None
        if isinstance(error, WallTimeout):
            raise ChangeFailure("PRECOMMIT_ABORT_UNCONFIRMED_WALL_TIMEOUT_NO_RETRY") from None
        try:
            conn.execute("ROLLBACK", prepare=False)
        except BaseException:
            raise ChangeFailure("PRECOMMIT_ROLLBACK_UNCONFIRMED") from None
        raise ChangeFailure("PRECOMMIT_ABORTED_NO_RETRY") from None


def run_change(conn, permit, *, reversal_receipt=None):
    """Dormant owner-only engine; no transport/approval-generation shortcuts.

    Matching a fingerprint binds bytes; it does not establish human authority.
    The calling operator must satisfy the separately reviewed live gates.
    """
    change = permit.consume()  # Consumed before connection I/O, even on failure.
    if reversal_receipt is None:
        expected = prepare_change(change["before"], window_reference=change["window_reference"])
    else:
        expected = prepare_reversal(reversal_receipt, window_reference=change["window_reference"])
    if change != expected:
        raise ChangeFailure("EXACT_APPROVED_CHANGE_REQUIRED")
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise ChangeFailure("IDLE_AUTOCOMMIT_CONNECTION_REQUIRED")
    return _execute(conn, change)
