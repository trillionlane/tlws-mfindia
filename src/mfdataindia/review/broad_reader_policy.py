"""Broad fund/research READ policy; dormant until exact live-write authorization.

No connection, secret lookup, CLI, installer wiring or provider calls. A hash
binds input bytes, not human approval. The caller owns the reviewed runtime,
fresh input package, administrative window and create-only execution intent.
Historical explicit-column profiles and private executors are not repurposed.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import signal
import threading
import time

from psycopg import sql
from psycopg.pq import TransactionStatus

from .reader_access_correction import CONTEXT_SQL, STATE_SQL as NARROW_STATE_SQL

DATABASE, OWNER, READER = "mfdataindia", "mfdata_admin", "mfdata_verified_reader"
SCHEMAS = (
    "mf",
    "mf_analytics",
    "mf_book_facts",
    "mf_distributions",
    "mf_enrichment",
    "mf_fact_refresh",
    "mf_fact_review",
    "mf_idcw",
    "mf_live_facts",
    "mf_monthly_facts",
    "mf_multi_facts",
    "mf_official_facts",
    "mf_overlap",
    "mf_portfolios",
    "mf_review",
    "mf_rolling",
)


class PolicyError(RuntimeError):
    """Fixed-stage error; never contains server/credential/private value text."""


class WallExpired(PolicyError):
    """No further database I/O after this signal or monotonic deadline."""


def require(ok, stage="INVALID_BROAD_READER_INPUT"):
    if not ok:
        raise PolicyError(stage)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def profile():
    """Fresh fixed policy value, never a caller-defined schema or role list."""
    return dict(
        format="mf-fund-research-read-v1",
        database=DATABASE,
        owner=OWNER,
        reader=READER,
        schemas=list(SCHEMAS),
        relation_kinds=["r", "p", "v", "m"],
        current_privileges=["SCHEMA_USAGE", "RELATION_SELECT"],
        future_creator=OWNER,
        future_privilege="SCHEMA_LOCAL_TABLE_SELECT",
        lifetime="PERSISTENT_CURRENT_AND_FUTURE_COLUMNS_AND_OWNER_CREATED_OBJECTS",
        all_rows_all_reader_consumers=True,
        grant_option=False,
        public_access=False,
        writes=False,
        administrative_access=False,
    )


PROFILE_SHA256 = fingerprint(profile())
_old_rel = "n.nspname IN ('mf','mf_review')"
_old_ns = "n.nspname IN ('mf','mf_review','public')"
assert NARROW_STATE_SQL.count(_old_rel) == 2 and NARROW_STATE_SQL.count(_old_ns) == 1
_schema_sql = ",".join("'" + s + "'" for s in SCHEMAS)  # Fixed literals only.
STATE_SQL = NARROW_STATE_SQL.replace(_old_rel, "n.nspname IN (" + _schema_sql + ")").replace(
    _old_ns, "n.nspname IN (" + _schema_sql + ",'public')"
)

_KEYS = {
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
_BOOLS = set(
    "row_security force_row_security dropped not_null usage create_allowed connect_allowed temporary_allowed prosecdef execute_allowed rolsuper rolinherit rolcreaterole rolcreatedb rolcanlogin rolreplication rolbypassrls admin_option inherit_option set_option polpermissive".split()
)
_OIDS = set(
    "oid namespace_oid owner type_oid collation_oid pronamespace proowner prorettype defaclrole defaclnamespace roleid member grantor polrelid".split()
)
_PRIVILEGES = set(
    "SELECT INSERT UPDATE DELETE TRUNCATE REFERENCES TRIGGER MAINTAIN USAGE CREATE CONNECT TEMPORARY EXECUTE SET".split()
) | {"ALTER SYSTEM"}
_SORT = {k: ("oid",) for k in _KEYS}
_SORT.update(
    columns=("oid", "attnum"),
    memberships=("roleid", "member", "grantor"),
    defaults=("defaclrole", "defaclnamespace", "defaclobjtype"),
)


def _seal(value):
    require(type(value) is dict and set(value) == {"is_null", "sha256", "utf8_bytes"})
    require(type(value["is_null"]) is bool and type(value["utf8_bytes"]) is int)
    if value["is_null"]:
        require(value["sha256"] is None and value["utf8_bytes"] == 0)
    else:
        require(
            type(value["sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", value["sha256"])
            and value["utf8_bytes"] > 0
        )


def _acl(atoms):
    require(type(atoms) is list)
    for atom in atoms:
        require(type(atom) is list and len(atom) == 4)
        require(all(type(v) is int and 0 <= v <= 4294967295 for v in atom[:2]))
        require(type(atom[2]) is str and atom[2] in _PRIVILEGES and type(atom[3]) is bool)
    require(atoms == sorted(atoms) and len({tuple(a) for a in atoms}) == len(atoms))


def _caps(value, length):
    require(type(value) is list and len(value) == length and all(type(x) is bool for x in value))


def catalogue(document):
    """Validate complete metadata and normalize ONLY default-ACL row OIDs.

    Default rows are keyed by (creator, schema, object type); their own row OID is
    server-allocated on insertion. All target/schema/role/column OIDs stay exact.
    The returned document is deepcopied and contains no source/application rows.
    """
    require(type(document) is dict and set(document) == set(_KEYS))
    value = deepcopy(document)
    for group, rows in value.items():
        require(type(rows) is list)
        seen = set()
        for row in rows:
            require(type(row) is dict)
            keys = set(_KEYS[group].split())
            if group == "defaults" and "oid" not in row:
                keys.remove("oid")  # Accept our own documented normalized form.
            require(set(row) == keys)
            for key, atom in row.items():
                if key in _BOOLS:
                    require(type(atom) is bool)
                elif key in _OIDS:
                    require(type(atom) is int and 0 <= atom <= 4294967295)
                elif key in {"attnum", "attribute_count", "type_modifier"}:
                    require(type(atom) is int)
                elif key in {
                    "rolname",
                    "nspname",
                    "relname",
                    "relkind",
                    "datname",
                    "name",
                    "proname",
                    "prokind",
                    "proargtypes",
                    "polname",
                    "polcmd",
                }:
                    require(type(atom) is str and "\x00" not in atom)
            if "acl" in row:
                _acl(row["acl"])
            for key in ("definition", "proconfig", "rolconfig"):
                if key in row:
                    _seal(row[key])
            if group == "defaults":
                require(
                    type(row["defaclobjtype"]) is str
                    and row["defaclobjtype"] in ("r", "S", "f", "T", "n", "L")
                )
                row.pop("oid", None)
            identity = tuple(row[k] for k in _SORT[group])
            require(identity not in seen, "DUPLICATE_CATALOGUE_IDENTITY")
            seen.add(identity)
        rows.sort(key=lambda row: tuple(row[k] for k in _SORT[group]))
    roles = {r["rolname"]: r for r in value["roles"]}
    require(len(roles) == len(value["roles"]) and OWNER in roles and READER in roles)
    owner, reader = roles[OWNER]["oid"], roles[READER]
    require(
        reader["rolcanlogin"]
        and not any(
            reader[k]
            for k in (
                "rolsuper",
                "rolinherit",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
                "rolbypassrls",
            )
        ),
        "RESTRICTED_SHARED_READER_REQUIRED",
    )
    require(
        not any(r["member"] == reader["oid"] for r in value["memberships"]),
        "READER_MEMBERSHIPS_NOT_ALLOWED",
    )
    namespaces = {r["oid"]: r for r in value["namespaces"]}
    require(len({n["nspname"] for n in namespaces.values()}) == len(namespaces))
    require({n["nspname"] for n in namespaces.values()} <= set(SCHEMAS) | {"public"})
    require("mf" in {n["nspname"] for n in namespaces.values()}, "CORE_DOMAIN_SCHEMA_REQUIRED")
    domain_oids = {n["oid"] for n in namespaces.values() if n["nspname"] in SCHEMAS}
    for default in value["defaults"]:
        if default["defaclrole"] != owner or default["defaclnamespace"] not in domain_oids | {0}:
            continue
        for _, grantee, privilege, grant_option in default["acl"]:
            if grantee not in {0, reader["oid"]}:
                continue
            kind = default["defaclobjtype"]
            unsafe = (
                (kind == "r" and (privilege != "SELECT" or grant_option))
                or (kind == "S" and (privilege in {"USAGE", "UPDATE"} or grant_option))
                or (kind == "n" and (privilege == "CREATE" or grant_option))
            )
            require(not unsafe, "FUTURE_WRITE_OR_GRANT_OPTION_NOT_ALLOWED")
    for default in value["defaults"]:
        if default["defaclnamespace"] == 0 and default["defaclobjtype"] == "r":
            require(
                not any(
                    grantee in {0, reader["oid"]} and privilege == "SELECT"
                    for _, grantee, privilege, _ in default["acl"]
                ),
                "GLOBAL_TABLE_READ_OUTSIDE_DOMAIN_NOT_ALLOWED",
            )
    for row in value["relations"] + value["columns"]:
        require(
            not any(
                grantee == 0 and privilege == "SELECT" for _, grantee, privilege, _ in row["acl"]
            ),
            "PUBLIC_DATA_READ_NOT_ALLOWED",
        )
    for ns in namespaces.values():
        require(not ns["create_allowed"], "READER_SCHEMA_CREATE_NOT_ALLOWED")
        if ns["nspname"] in SCHEMAS:
            require(ns["owner"] == owner, "DIRECT_DOMAIN_OWNER_REQUIRED")
    require(len(value["databases"]) == 1 and value["databases"][0]["datname"] == DATABASE)
    require(
        value["databases"][0]["connect_allowed"] and not value["databases"][0]["create_allowed"]
    )
    relations = {r["oid"]: r for r in value["relations"]}
    for rel in relations.values():
        require(
            rel["namespace_oid"] in namespaces
            and namespaces[rel["namespace_oid"]]["nspname"] == rel["nspname"]
            and rel["nspname"] in SCHEMAS
        )
        require(type(rel["relname"]) is str and rel["relname"] and "\x00" not in rel["relname"])
        require(rel["relkind"] in ("r", "p", "v", "m", "S"), "FOREIGN_OR_UNSUPPORTED_RELATION")
        if rel["relkind"] == "S":
            require(rel["capabilities"] is None)
            _caps(rel["sequence_capabilities"], 3)
            require(
                not rel["sequence_capabilities"][0] and not rel["sequence_capabilities"][2],
                "SOURCE_SEQUENCE_MUTATION_NOT_ALLOWED",
            )
        else:
            require(rel["owner"] == owner, "DIRECT_OBJECT_OWNER_REQUIRED")
            require(
                not rel["row_security"] and not rel["force_row_security"],
                "RLS_REQUIRES_SEPARATE_DISPOSITION",
            )
            require(rel["sequence_capabilities"] is None)
            _caps(rel["capabilities"], 4)
            require(
                not rel["capabilities"][1] and not rel["capabilities"][2],
                "WRITE_OR_GRANT_OPTION_NOT_ALLOWED",
            )
        require(rel["attribute_count"] >= 0)
        columns = [c for c in value["columns"] if c["oid"] == rel["oid"]]
        require(
            [c["attnum"] for c in columns] == list(range(1, rel["attribute_count"] + 1)),
            "COMPLETE_PHYSICAL_COLUMNS_REQUIRED",
        )
    for col in value["columns"]:
        require(col["oid"] in relations and type(col["name"]) is str and col["attnum"] > 0)
        if col["dropped"] or relations[col["oid"]]["relkind"] == "S":
            require(col["capabilities"] is None)
        else:
            _caps(col["capabilities"], 4)
            require(not any(col["capabilities"][1:]), "COLUMN_WRITE_OR_GRANT_OPTION_NOT_ALLOWED")
    for func in value["functions"]:
        require(
            func["pronamespace"] in namespaces
            and namespaces[func["pronamespace"]]["nspname"] in SCHEMAS
        )
        require(
            type(func["private_body_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", func["private_body_sha256"])
        )
        require(
            not (func["prosecdef"] and func["execute_allowed"]),
            "PRIVILEGED_DOMAIN_FUNCTION_NOT_ALLOWED",
        )
    # Reject a non-JSON value/NaN without retaining private input in an error.
    try:
        fingerprint(value)
    except (ValueError, TypeError):
        raise PolicyError("CANONICAL_CATALOGUE_REQUIRED") from None
    return value


def _add(atoms, atom):
    if atom in atoms:
        return False
    atoms.append(atom)
    atoms.sort()
    return True


def prepare(document):
    """Pure, complete schema-wide policy plan. No caller targets or connection."""
    before = catalogue(document)
    after = deepcopy(before)
    roles = {r["rolname"]: r["oid"] for r in before["roles"]}
    atom = [roles[OWNER], roles[READER], "SELECT", False]
    delta = dict(schemas=[], relations=[], defaults=[])
    commands = []
    present = []
    for ns in after["namespaces"]:
        name = ns["nspname"]
        if name not in SCHEMAS:
            continue
        present.append(name)
        if _add(ns["acl"], [roles[OWNER], roles[READER], "USAGE", False]):
            delta["schemas"].append(dict(oid=ns["oid"], schema=name))
            commands.append(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}")
                .format(sql.Identifier(name), sql.Identifier(READER))
                .as_string()
            )
        ns["usage"] = True
    for rel in sorted(after["relations"], key=lambda r: (r["nspname"], r["relname"])):
        if rel["relkind"] == "S":
            continue
        if _add(rel["acl"], deepcopy(atom)):
            delta["relations"].append(
                dict(oid=rel["oid"], schema=rel["nspname"], name=rel["relname"])
            )
            commands.append(
                sql.SQL("GRANT SELECT ON TABLE {}.{} TO {}")
                .format(
                    sql.Identifier(rel["nspname"]),
                    sql.Identifier(rel["relname"]),
                    sql.Identifier(READER),
                )
                .as_string()
            )
        rel["capabilities"][0] = rel["capabilities"][3] = True
    for col in after["columns"]:
        if col["capabilities"] is not None:
            col["capabilities"][0] = True
    for ns in sorted(
        (n for n in after["namespaces"] if n["nspname"] in SCHEMAS), key=lambda n: n["nspname"]
    ):
        found = next(
            (
                d
                for d in after["defaults"]
                if (d["defaclrole"], d["defaclnamespace"], d["defaclobjtype"])
                == (roles[OWNER], ns["oid"], "r")
            ),
            None,
        )
        existing = found is not None
        if found is None:
            found = dict(
                defaclrole=roles[OWNER], defaclnamespace=ns["oid"], defaclobjtype="r", acl=[]
            )
            after["defaults"].append(found)
        if _add(found["acl"], deepcopy(atom)):
            delta["defaults"].append(
                dict(
                    creator_oid=roles[OWNER],
                    namespace_oid=ns["oid"],
                    schema=ns["nspname"],
                    object_type="r",
                    preexisting=existing,
                )
            )
            commands.append(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}"
                )
                .format(
                    sql.Identifier(OWNER), sql.Identifier(ns["nspname"]), sql.Identifier(READER)
                )
                .as_string()
            )
    after = catalogue(after)
    return dict(
        format="mf-broad-reader-plan-v1",
        profile_sha256=PROFILE_SHA256,
        before=before,
        after=after,
        before_sha256=fingerprint(before),
        after_sha256=fingerprint(after),
        delta=delta,
        sql=commands,
        coverage=dict(
            present_schemas=sorted(present),
            absent_schemas=sorted(set(SCHEMAS) - set(present)),
            future_creator=OWNER,
            foreign_objects_covered=False,
            other_creators_covered=False,
            new_schemas_covered=False,
        ),
        runtime_or_human_approval=False,
    )


def require_current_policy(document):
    """Pure provisioning admission; absent schemas are NOT claimed enrolled."""
    plan = prepare(document)
    require(not any(plan["delta"].values()), "BROAD_READER_POLICY_NOT_ENROLLED")
    return deepcopy(plan["coverage"])


def _window(value):
    require(
        type(value) is dict
        and set(value)
        == {
            "starts_at",
            "ends_at",
            "exclusive_administration",
            "data_schema_custody",
            "future_owner_provisioning",
        }
    )
    require(
        all(
            value[k] is True
            for k in (
                "exclusive_administration",
                "data_schema_custody",
                "future_owner_provisioning",
            )
        ),
        "ADMINISTRATIVE_AND_SCHEMA_CUSTODY_ATTESTATION_REQUIRED",
    )
    parsed = []
    for k in ("starts_at", "ends_at"):
        require(
            type(value[k]) is str
            and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", value[k])
        )
        try:
            parsed.append(datetime.fromisoformat(value[k]))
        except ValueError:
            raise PolicyError("UTC_WINDOW_REQUIRED") from None
    require(
        0 < (parsed[1] - parsed[0]).total_seconds() <= 900, "BOUNDED_ADMINISTRATIVE_WINDOW_REQUIRED"
    )
    return parsed


def approval_document(plan, *, window):
    """Canonical binding constructor, explicitly NOT human approval."""
    require(
        type(plan) is dict and "before" in plan and plan == prepare(plan["before"]),
        "EXACT_GENERATED_PLAN_REQUIRED",
    )
    _window(window)
    return dict(format="mf-broad-reader-inputs-v1", plan=deepcopy(plan), window=deepcopy(window))


@dataclass
class Permit:
    document: dict
    approved_fingerprint: str
    used: bool = False
    _lock: object = field(default_factory=threading.Lock, repr=False, compare=False)

    def consume(self):
        with self._lock:
            require(not self.used, "CONSUMED_BROAD_READER_INPUTS")
            self.used = True
            value = deepcopy(self.document)
            require(fingerprint(value) == self.approved_fingerprint, "UNBOUND_BROAD_READER_INPUTS")
            return value


def _live(window, remaining=0):
    start, end = _window(window)
    now = datetime.now(timezone.utc)
    require(
        start <= now < end and (end - now).total_seconds() >= remaining,
        "ADMINISTRATIVE_WINDOW_NOT_LIVE",
    )


@contextmanager
def _deadline():
    require(
        threading.current_thread() is threading.main_thread()
        and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
        "EXCLUSIVE_MAIN_THREAD_TIMER_REQUIRED",
    )
    previous = signal.getsignal(signal.SIGALRM)

    def expired(*_):
        raise WallExpired("WHOLE_BROAD_OPERATION_WALL_EXPIRED")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 15)
    try:
        yield time.monotonic() + 15
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def apply(conn, permit):
    """Dormant single-use owner executor; no transport or approval shortcuts."""
    document = permit.consume()
    require(type(document) is dict and set(document) == {"format", "plan", "window"})
    require(document == approval_document(document["plan"], window=document["window"]))
    require(
        conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE,
        "IDLE_DIRECT_OWNER_CONNECTION_REQUIRED",
    )
    plan, window = document["plan"], document["window"]
    with _deadline() as end:
        stage, begun = "BEFORE_BEGIN", False

        def io(statement):
            if time.monotonic() >= end:
                raise WallExpired("WHOLE_BROAD_OPERATION_WALL_EXPIRED")
            return conn.execute(statement, prepare=False)

        def begin(readonly):
            io(
                "BEGIN ISOLATION LEVEL READ COMMITTED "
                + ("READ ONLY" if readonly else "READ WRITE")
            )
            for setting in (
                "statement_timeout='5s'",
                "lock_timeout='5s'",
                "transaction_timeout='15s'",
                "search_path=pg_catalog",
                "TimeZone='UTC'",
            ):
                io("SET LOCAL " + setting)
            expected = dict(
                database=DATABASE,
                principal=OWNER,
                session=OWNER,
                version=180006,
                isolation="read committed",
                readonly="on" if readonly else "off",
                statement_timeout="5s",
                lock_timeout="5s",
                transaction_timeout="15s",
                search_path="pg_catalog",
                timezone="UTC",
            )
            require(io(CONTEXT_SQL).fetchone() == expected, "DIRECT_OWNER_CONTEXT_REQUIRED")

        try:
            _live(window, remaining=15)
            begun = True  # BEGIN may have reached the server even if transport fails.
            begin(False)
            require(
                catalogue(io(STATE_SQL).fetchone()["value"]) == plan["before"],
                "FRESH_BEFORE_CATALOGUE_DRIFT",
            )
            for statement in plan["sql"]:
                _live(window)
                io(statement)
            require(
                catalogue(io(STATE_SQL).fetchone()["value"]) == plan["after"],
                "EXACT_BROAD_READ_DELTA_MISMATCH",
            )
            _live(window)
            stage = "COMMIT_OUTCOME_UNKNOWN"
            io("COMMIT")
            stage = "COMMITTED_UNVERIFIED"
            begin(True)
            require(
                catalogue(io(STATE_SQL).fetchone()["value"]) == plan["after"],
                "FINAL_BROAD_READ_STATE_DRIFT",
            )
            io("ROLLBACK")  # The final verification transaction is read-only.
            result = dict(
                stage="COMMITTED_VERIFIED",
                profile_sha256=PROFILE_SHA256,
                inputs_sha256=fingerprint(document),
                plan_sha256=fingerprint(plan),
                before_sha256=plan["before_sha256"],
                after_sha256=plan["after_sha256"],
                delta=deepcopy(plan["delta"]),
                coverage=deepcopy(plan["coverage"]),
                defaults_reversal_does_not_revoke_inherited_object_grants=True,
                administrative_and_data_schema_custody_are_external_trust=True,
                postcommit_permanence_guarantee=False,
                performance_or_FS13_acceptance=False,
            )
            result["receipt_sha256"] = fingerprint(result)
            return result
        except BaseException as error:
            if stage in ("COMMIT_OUTCOME_UNKNOWN", "COMMITTED_UNVERIFIED"):
                raise PolicyError(stage) from None
            if not begun:
                raise PolicyError("BEFORE_BEGIN_REJECTED_NO_IO") from None
            if isinstance(error, WallExpired) or time.monotonic() >= end:
                raise PolicyError("WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO") from None
            try:
                io("ROLLBACK")
            except BaseException as cleanup:
                label = (
                    "WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO"
                    if isinstance(cleanup, WallExpired)
                    else "ABORT_UNCONFIRMED_NO_RETRY"
                )
                raise PolicyError(label) from None
            raise PolicyError("PRECOMMIT_ABORTED_NO_RETRY") from None
