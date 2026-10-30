"""Single-use DEV broad-reader transaction with an independent lock observer.

This is a dormant engine, not a cloud job, secret reader, or authority to run.
Its caller must supply the exact reviewed owner and observer connections, a
fresh source-bound package, external custody, and a create-only execution
intent. No caller-defined SQL, role, schema, or lock target is accepted.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import re
import threading
import time

from psycopg.pq import TransactionStatus

from mfdataindia.review import broad_reader_dependency_scope as dependencies
from mfdataindia.review import broad_reader_direct_plan as direct_plan
from mfdataindia.review import broad_reader_live_snapshot as snapshot
from mfdataindia.review import broad_reader_lock_witness as witness
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review import reader_function_attestation as helper_capture


DATABASE_OID_SQL = ("SELECT oid::bigint AS oid FROM pg_catalog.pg_database "
                    "WHERE datname=current_database()")
_SETTINGS = (
    "SET LOCAL statement_timeout='5s'",
    "SET LOCAL lock_timeout='5s'",
    "SET LOCAL transaction_timeout='15s'",
    "SET LOCAL search_path=pg_catalog",
    "SET LOCAL TimeZone='UTC'",
)
_CONTEXT = {"database": policy.DATABASE, "principal": policy.OWNER,
            "session": policy.OWNER, "version": 180006,
            "isolation": "read committed", "readonly": "on",
            "statement_timeout": "5s", "lock_timeout": "5s",
            "transaction_timeout": "15s", "search_path": "pg_catalog",
            "timezone": "UTC"}


class GrantError(RuntimeError):
    """No permission acceptance may be inferred from this failure."""


def _image_binding(value, git_sha):
    policy.require(type(value) is dict and set(value) == {"app", "tools"},
                   "UNBOUND_BROAD_READER_FINAL_IMAGES")
    for kind, repository in (("app", "mfdataindia"),
                             ("tools", "mfdataindia-review-tools")):
        item = value[kind]
        policy.require(type(item) is dict and set(item) == {
            "image", "resolved_image", "config_digest", "git_sha",
            "platform", "layers_downloaded"}, "UNBOUND_BROAD_READER_FINAL_IMAGES")
        prefix = ("asia-south1-docker.pkg.dev/trillionlane-dev/"
                  "tlws-mf-data-dev/" + repository + "@sha256:")
        policy.require(
            all(type(item[key]) is str and re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}",
                                                        item[key]) for key in ("image", "resolved_image"))
            and type(item["config_digest"]) is str
            and re.fullmatch(r"sha256:[0-9a-f]{64}", item["config_digest"])
            and item["git_sha"] == git_sha and item["platform"] == "linux/amd64"
            and type(item["layers_downloaded"]) is int and item["layers_downloaded"] == 0,
            "UNBOUND_BROAD_READER_FINAL_IMAGES")


@dataclass
class Permit:
    """Exact request binding, not a surrogate human approval."""

    document: dict
    binding_sha256: str
    used: bool = False
    _lock: object = field(default_factory=threading.Lock, repr=False, compare=False)

    def consume(self, *, source_root):
        with self._lock:
            policy.require(not self.used, "CONSUMED_BROAD_READER_DIRECT_REQUEST")
            self.used = True
            value = deepcopy(self.document)
            policy.require(policy.fingerprint(value) == self.binding_sha256,
                           "UNBOUND_BROAD_READER_DIRECT_REQUEST")
            policy.require(type(value) is dict and set(value) == {
                "format", "window", "before_snapshot", "helper_attestation",
                "packaged_source", "git_sha", "image_binding", "create_only_intent_sha256",
            }, "INCOMPLETE_BROAD_READER_DIRECT_REQUEST")
            policy.require(value["format"] == "mf-broad-reader-direct-grant-v1")
            policy.require(type(value["git_sha"]) is str
                           and re.fullmatch(r"[0-9a-f]{40}", value["git_sha"])
                           and type(value["create_only_intent_sha256"]) is str
                           and re.fullmatch(r"[0-9a-f]{64}", value["create_only_intent_sha256"]),
                           "UNBOUND_BROAD_READER_DIRECT_REQUEST")
            _image_binding(value["image_binding"], value["git_sha"])
            policy._window(value["window"])
            policy.require(value["before_snapshot"]["plan"]["permission_acceptance"] is False)
            policy.require(value["before_snapshot"]["permission_enabled"] is False)
            source.require_packaged_source(value["packaged_source"], source_root)
            return value


def _ensure_idle(conn):
    policy.require(conn.autocommit is True
                   and conn.info.transaction_status == TransactionStatus.IDLE,
                   "IDLE_DIRECT_OWNER_CONNECTION_REQUIRED")


def _io(conn, statement, *, end, parameters=None):
    if time.monotonic() >= end:
        raise policy.WallExpired("WHOLE_BROAD_OPERATION_WALL_EXPIRED")
    if parameters is None:
        result = conn.execute(statement, prepare=False)
    else:
        result = conn.execute(statement, parameters, prepare=False)
    if time.monotonic() >= end:
        raise policy.WallExpired("WHOLE_BROAD_OPERATION_WALL_EXPIRED")
    return result


def _begin_readonly(conn, *, end):
    _io(conn, "BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY", end=end)
    for statement in _SETTINGS:
        _io(conn, statement, end=end)
    actual = _io(conn, policy.CONTEXT_SQL, end=end).fetchone()
    policy.require(actual == _CONTEXT, "EXACT_DIRECT_OWNER_CONTEXT_REQUIRED")


def _locks(observer, pid, *, end):
    cursor = _io(observer, witness.LOCK_WITNESS_SQL, end=end,
                 parameters=(pid,))
    policy.require([item.name for item in cursor.description] == [
        "pid", "database", "oid", "nspname", "relname", "relkind", "mode", "granted"],
        "LOCK_WITNESS_COLUMNS_DRIFT")
    rows = [dict(row) for row in cursor.fetchall()]
    policy.require(type(cursor.rowcount) is int and cursor.rowcount == len(rows)
                   and cursor.statusmessage == "SELECT " + str(len(rows)),
                   "INCOMPLETE_LOCK_WITNESS")
    return rows


def _rollback_once(target, *, end):
    """Never issue a second ROLLBACK after an ambiguous first attempt."""
    if time.monotonic() >= end:
        raise GrantError("WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO")
    try:
        cursor = _io(target, "ROLLBACK", end=end)
        policy.require(cursor.statusmessage == "ROLLBACK", "ABORT_UNCONFIRMED_NO_RETRY")
    except BaseException:
        raise GrantError("ABORT_UNCONFIRMED_NO_RETRY") from None


def capture_before(observer, *, packaged_source, source_root):
    """Complete read-only input before a writer is even opened.

    The existing helper attestation and full catalogue each retain their own
    fixed 15-second read-only transaction wall. The future grant transaction
    separately retains its unchanged 15-second whole wall.
    """
    source.require_packaged_source(packaged_source, source_root)
    _ensure_idle(observer)
    helper_attestation = helper_capture.Capture().run(observer)
    _ensure_idle(observer)
    with policy._deadline() as end:
        begun = rollback_attempted = False
        try:
            begun = True
            _begin_readonly(observer, end=end)
            before = snapshot.read(
                observer, packaged=packaged_source,
                helper_attestation=helper_attestation)
            rollback_attempted = True
            _rollback_once(observer, end=end)
            begun = False
            return {"format": "mf-broad-reader-prewrite-capture-v1-nonexecutable",
                    "helper_attestation": helper_attestation,
                    "before_snapshot": before,
                    "packaged_source": deepcopy(packaged_source),
                    "capture_sha256": policy.fingerprint(before),
                    "grant_authorized": False}
        except BaseException:
            if begun and rollback_attempted:
                raise GrantError("ABORT_UNCONFIRMED_NO_RETRY") from None
            if begun and time.monotonic() >= end:
                raise GrantError("WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO") from None
            if begun:
                _rollback_once(observer, end=end)
            raise GrantError("PREWRITE_CAPTURE_ABORTED_NO_RETRY") from None


def run(target, observer, permit, *, source_root):
    """Apply the exact add-only plan only after complete prewrite proof.

    The observer must be an explicitly declared separate direct-owner backend.
    The 15-second wall includes target preflight, pristine baseline, both lock
    phases, target-local complete reread, grant, commit and independent
    observer postcommit readback.
    Authenticated reader and TI checks remain separate acceptance gates.
    """
    document = permit.consume(source_root=source_root)  # Before owner I/O.
    _ensure_idle(target)
    _ensure_idle(observer)
    policy.require(target is not observer
                   and target.info.backend_pid != observer.info.backend_pid,
                   "DISTINCT_OWNER_OBSERVER_BACKENDS_REQUIRED")
    before = document["before_snapshot"]
    plan = before["plan"]
    policy.require(plan["before"] == snapshot.canonical_state(before["state"])
                   and plan["raw_full_input_sha256"] == policy.fingerprint(before["state"])
                   and type(plan["sql"]) is list,
                   "UNMATCHED_BROAD_READER_DIRECT_PLAN")
    policy.require(plan.get("reversal_template") == direct_plan.reversal_template(plan),
                   "PREARMED_EXACT_REVERSAL_REQUIRED")
    policy.require(bool(plan["sql"]) and plan["relation_scope"]["materialized_unlocked"]
                   == [r for r in plan["relation_scope"]["grant_roots"]
                       if r["relkind"] == "m"], "UNMATCHED_BROAD_READER_DIRECT_PLAN")
    policy._live(document["window"], remaining=15)

    with policy._deadline() as end:
        target_begun = observer_begun = False
        preflight_rollback_attempted = False
        stage = "BEFORE_BEGIN"
        try:
            # The target preflight is a separate, confirmed read-only txn. It
            # precedes the pristine baseline and cannot itself be a grant.
            target_begun = True
            _begin_readonly(target, end=end)
            pid = target.info.backend_pid
            database_rows = _io(target, DATABASE_OID_SQL, end=end).fetchall()
            policy.require(len(database_rows) == 1 and type(database_rows[0]["oid"]) is int
                           and database_rows[0]["oid"] > 0, "DIRECT_DATABASE_OID_REQUIRED")
            database_oid = database_rows[0]["oid"]
            preflight_rollback_attempted = True
            _rollback_once(target, end=end)
            target_begun = False
            policy.require(target.info.backend_pid == pid
                           and target.info.transaction_status == TransactionStatus.IDLE,
                           "TARGET_BACKEND_CHANGED_AFTER_PREFLIGHT")

            observer_begun = True
            _begin_readonly(observer, end=end)
            observer_pid = observer.info.backend_pid
            policy.require(observer_pid != pid, "DISTINCT_OWNER_OBSERVER_BACKENDS_REQUIRED")
            baseline = _locks(observer, pid, end=end)
            policy.require(not baseline, "TARGET_BASELINE_RELATION_LOCKS_PRESENT")

            target_begun = True
            stage = "GRANT_TRANSACTION_STARTED"
            _io(target, "BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE", end=end)
            for statement in _SETTINGS:
                _io(target, statement, end=end)
            # No target catalogue SELECT occurs between baseline and these
            # phases. Catalog leaves must arise only from the view phase.
            table_sql, view_sql = dependencies.lock_phases(plan["relation_scope"])
            for statement in table_sql:
                _io(target, statement, end=end)
            table_locks = _locks(observer, pid, end=end)
            for statement in view_sql:
                _io(target, statement, end=end)
            view_locks = _locks(observer, pid, end=end)
            lock_proof = witness.verify_phases(
                plan["relation_scope"], baseline=baseline, tables=table_locks,
                views=view_locks, target_pid=pid, observer_pid=observer_pid,
                database_oid=database_oid)

            # A separate backend's pg_get_viewdef can wait on the target's
            # exclusive locks. The target can read its own locked catalogue.
            # Roll back only the read's extra locks before comparing the exact
            # independent phase-two witness; the earlier locks remain held.
            saved = _io(target, "SAVEPOINT broad_reader_pregrant_read", end=end)
            policy.require(saved.statusmessage == "SAVEPOINT",
                           "PREGRANT_READ_SAVEPOINT_UNCONFIRMED")
            pregrant = snapshot.read(
                target, packaged=document["packaged_source"],
                helper_attestation=document["helper_attestation"])
            equality = snapshot.compare(before, pregrant)
            rolled_back_read = _io(
                target, "ROLLBACK TO SAVEPOINT broad_reader_pregrant_read", end=end)
            policy.require(rolled_back_read.statusmessage == "ROLLBACK",
                           "PREGRANT_READ_LOCK_RELEASE_UNCONFIRMED")
            released = _io(target, "RELEASE SAVEPOINT broad_reader_pregrant_read", end=end)
            policy.require(released.statusmessage == "RELEASE",
                           "PREGRANT_READ_SAVEPOINT_RELEASE_UNCONFIRMED")
            policy.require(target.info.backend_pid == pid
                           and target.info.transaction_status == TransactionStatus.INTRANS,
                           "TARGET_BACKEND_CHANGED_AFTER_PREGRANT_READ")
            policy.require(_locks(observer, pid, end=end) == view_locks,
                           "PREGRANT_TARGET_LOCK_WITNESS_DRIFT")
            policy._live(document["window"])
            for statement in plan["sql"]:
                result = _io(target, statement, end=end)
                wanted = ("GRANT" if statement.startswith("GRANT ")
                          else "ALTER DEFAULT PRIVILEGES")
                policy.require(result.statusmessage == wanted,
                               "DIRECT_GRANT_STATUS_DRIFT")
            policy._live(document["window"])
            stage = "COMMIT_OUTCOME_UNKNOWN"
            committed = _io(target, "COMMIT", end=end)
            policy.require(committed.statusmessage == "COMMIT",
                           "COMMIT_OUTCOME_UNKNOWN")
            stage = "COMMITTED_UNVERIFIED"
            target_begun = False
            postgrant = snapshot.read(
                observer, packaged=document["packaged_source"],
                helper_attestation=document["helper_attestation"])
            postproof = snapshot.compare_post(before, postgrant)
            _io(observer, "ROLLBACK", end=end)
            observer_begun = False
            stage = "COMMITTED_STATE_VERIFIED"
            return {"format": "mf-broad-reader-direct-transaction-receipt-v1",
                    "stage": stage, "request_sha256": permit.binding_sha256,
                    "before_sha256": plan["full_before_sha256"],
                    "after_sha256": plan["full_after_sha256"],
                    "source_sha256": policy.fingerprint(document["packaged_source"]),
                    "lock_proof": lock_proof, "pregrant_equality": equality,
                    "postgrant_proof": postproof, "delta": deepcopy(plan["delta"]),
                    "materialized_unlocked": deepcopy(
                        plan["relation_scope"]["materialized_unlocked"]),
                    "postcommit_permanence_guarantee": False,
                    "authenticated_reader_verified": False, "ti_verified": False,
                    "permission_enabled": False}
        except BaseException as error:
            if stage in ("COMMIT_OUTCOME_UNKNOWN", "COMMITTED_UNVERIFIED"):
                raise GrantError(stage) from None
            if target_begun:
                if preflight_rollback_attempted and stage == "BEFORE_BEGIN":
                    raise GrantError("ABORT_UNCONFIRMED_NO_RETRY") from None
                if isinstance(error, policy.WallExpired) or time.monotonic() >= end:
                    raise GrantError("WALL_ABORT_UNCONFIRMED_NO_FURTHER_IO") from None
                _rollback_once(target, end=end)
            if observer_begun and time.monotonic() < end:
                try:
                    _io(observer, "ROLLBACK", end=end)
                except BaseException:
                    raise GrantError("OBSERVER_ABORT_UNCONFIRMED_NO_RETRY") from None
            raise GrantError("PRECOMMIT_ABORTED_NO_RETRY") from None
