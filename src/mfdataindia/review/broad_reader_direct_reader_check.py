"""Authenticated metadata-only readback for the existing shared DEV reader.

This runs only after a separately verified grant commit. It never grants,
calls a privileged function, exports business rows, or represents the TI
consumer contract. A successful result is necessary but not sufficient for
permission-enabled acceptance.
"""

from copy import deepcopy
import time

from psycopg.pq import TransactionStatus

from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review.evidence_transport import MAX_RAW_BYTES
from mfdataindia.review.policy import canonical


CONTEXT_SQL = """SELECT current_database() AS database,current_user AS principal,
session_user AS session,pg_is_in_recovery() AS in_recovery,
current_setting('transaction_read_only') AS readonly,
current_setting('transaction_isolation') AS isolation"""
SCHEMA_SQL = """SELECT n.oid::bigint AS oid,n.nspname,
has_schema_privilege(current_user,n.oid,'USAGE') AS usage,
has_schema_privilege(current_user,n.oid,'CREATE') AS create_allowed
FROM pg_catalog.pg_namespace n WHERE n.nspname IN (
'mf','mf_analytics','mf_book_facts','mf_distributions','mf_enrichment',
'mf_fact_refresh','mf_fact_review','mf_idcw','mf_live_facts','mf_monthly_facts',
'mf_multi_facts','mf_official_facts','mf_overlap','mf_portfolios','mf_review',
'mf_rolling') ORDER BY n.nspname"""
RELATION_SQL = """SELECT c.oid::bigint AS oid,n.nspname,c.relname,
c.relkind::text AS relkind,
has_table_privilege(current_user,c.oid,'SELECT') AS select_allowed,
has_table_privilege(current_user,c.oid,'INSERT') AS insert_allowed,
has_table_privilege(current_user,c.oid,'UPDATE') AS update_allowed,
has_table_privilege(current_user,c.oid,'DELETE') AS delete_allowed,
has_table_privilege(current_user,c.oid,'TRUNCATE') AS truncate_allowed,
has_table_privilege(current_user,c.oid,'REFERENCES') AS references_allowed,
has_table_privilege(current_user,c.oid,'TRIGGER') AS trigger_allowed
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN (
'mf','mf_analytics','mf_book_facts','mf_distributions','mf_enrichment',
'mf_fact_refresh','mf_fact_review','mf_idcw','mf_live_facts','mf_monthly_facts',
'mf_multi_facts','mf_official_facts','mf_overlap','mf_portfolios','mf_review',
'mf_rolling') AND c.relkind IN ('r','p','v','m')
ORDER BY n.nspname,c.relname,c.oid"""
COLUMN_SQL = """SELECT c.oid::bigint AS oid,a.attnum::int AS attnum,a.attname,
has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS select_allowed,
has_column_privilege(current_user,c.oid,a.attnum,'INSERT') AS insert_allowed,
has_column_privilege(current_user,c.oid,a.attnum,'UPDATE') AS update_allowed,
has_column_privilege(current_user,c.oid,a.attnum,'REFERENCES') AS references_allowed
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
WHERE n.nspname IN (
'mf','mf_analytics','mf_book_facts','mf_distributions','mf_enrichment',
'mf_fact_refresh','mf_fact_review','mf_idcw','mf_live_facts','mf_monthly_facts',
'mf_multi_facts','mf_official_facts','mf_overlap','mf_portfolios','mf_review',
'mf_rolling') AND c.relkind IN ('r','p','v','m')
AND a.attnum>0 AND NOT a.attisdropped
ORDER BY c.oid,a.attnum"""
REPRESENTATIVE_SQL = "SELECT 1 AS readable FROM mf.funds LIMIT 1"


class ReaderCheckError(ValueError):
    """Authenticated reader capability is not completely proved."""


def _require(condition):
    if not condition:
        raise ReaderCheckError("INCOMPLETE_AUTHENTICATED_READER_CHECK")


def validate(context, namespaces, relations, columns, *, planned_after):
    """Require complete exact identities and no reader write capability."""
    _require(context == {"database": policy.DATABASE, "principal": policy.READER,
                         "session": policy.READER, "in_recovery": False,
                         "readonly": "on", "isolation": "repeatable read"})
    _require(type(planned_after) is dict)
    expected_namespaces = {n["oid"]: n for n in planned_after["namespaces"]
                           if n["nspname"] in policy.SCHEMAS}
    expected_relations = {r["oid"]: r for r in planned_after["relations"]
                          if r["relkind"] in ("r", "p", "v", "m")}
    expected_columns = {(c["oid"], c["attnum"]): c for c in planned_after["columns"]
                        if c["oid"] in expected_relations and not c["dropped"]}
    _require(type(namespaces) is list and type(relations) is list and type(columns) is list)
    _require(len(expected_namespaces) == len(namespaces) == len(policy.SCHEMAS))
    _require(len(expected_relations) == len(relations)
             and len(expected_columns) == len(columns))
    _require({r["oid"] for r in namespaces} == set(expected_namespaces))
    _require({r["oid"] for r in relations} == set(expected_relations))
    _require({(r["oid"], r["attnum"]) for r in columns} == set(expected_columns))
    for row in namespaces:
        expected = expected_namespaces[row["oid"]]
        _require(set(row) == {"oid", "nspname", "usage", "create_allowed"}
                 and row["nspname"] == expected["nspname"]
                 and row["usage"] is True and row["create_allowed"] is False)
    for row in relations:
        expected = expected_relations[row["oid"]]
        _require(set(row) == {"oid", "nspname", "relname", "relkind",
                              "select_allowed", "insert_allowed", "update_allowed",
                              "delete_allowed", "truncate_allowed", "references_allowed",
                              "trigger_allowed"})
        _require(all(row[key] == expected[key] for key in
                     ("nspname", "relname", "relkind")))
        _require(row["select_allowed"] is True
                 and all(row[key] is False for key in (
                     "insert_allowed", "update_allowed", "delete_allowed",
                     "truncate_allowed", "references_allowed", "trigger_allowed")))
    for row in columns:
        expected = expected_columns[(row["oid"], row["attnum"])]
        _require(set(row) == {"oid", "attnum", "attname", "select_allowed",
                              "insert_allowed", "update_allowed", "references_allowed"})
        _require(row["attname"] == expected["name"] and row["select_allowed"] is True
                 and all(row[key] is False for key in (
                     "insert_allowed", "update_allowed", "references_allowed")))
    result = {"format": "mf-broad-reader-authenticated-capabilities-v1",
              "context": deepcopy(context), "namespaces": deepcopy(namespaces),
              "relations": deepcopy(relations), "columns": deepcopy(columns),
              "full_planned_after_sha256": policy.fingerprint(planned_after),
              "permission_enabled": False, "ti_verified": False}
    _require(len(canonical(result).encode()) <= MAX_RAW_BYTES)
    return result


def _read(conn, statement, columns, *, end):
    if time.monotonic() >= end:
        raise ReaderCheckError("READER_WALL_SPENT_NO_FURTHER_IO")
    result = conn.execute(statement, prepare=False)
    _require([column.name for column in result.description] == columns)
    rows = [dict(row) for row in result.fetchall()]
    _require(type(result.rowcount) is int and result.rowcount == len(rows)
             and result.statusmessage == "SELECT " + str(len(rows)))
    if time.monotonic() >= end:
        raise ReaderCheckError("READER_WALL_SPENT_NO_FURTHER_IO")
    return rows


def _control(conn, statement, *, end):
    if time.monotonic() >= end:
        raise ReaderCheckError("READER_WALL_SPENT_NO_FURTHER_IO")
    result = conn.execute(statement, prepare=False)
    if time.monotonic() >= end:
        raise ReaderCheckError("READER_WALL_SPENT_NO_FURTHER_IO")
    return result


class _BoundedProfileConnection:
    """Reuse the SAME reader wall for every selected-profile query."""
    def __init__(self, connection, end):
        self.connection, self.end = connection, end

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, *args, **kwargs):
        if time.monotonic() >= self.end:
            raise ReaderCheckError("READER_WALL_SPENT_NO_FURTHER_IO")
        value = self.connection.execute(*args, **kwargs)
        if time.monotonic() >= self.end:
            raise ReaderCheckError("READER_WALL_SPENT_NO_FURTHER_IO")
        return value


def run(conn, *, planned_after, reader_profile=None):
    """One bounded direct-reader metadata check; no privileged function call."""
    policy.require(conn.autocommit is True
                   and conn.info.transaction_status == TransactionStatus.IDLE,
                   "IDLE_READER_CONNECTION_REQUIRED")
    with policy._deadline() as end:
        begun = rollback_attempted = False
        try:
            begun = True
            _control(conn, "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY", end=end)
            for statement in ("SET LOCAL statement_timeout='5s'",
                              "SET LOCAL lock_timeout='5s'",
                              "SET LOCAL transaction_timeout='15s'",
                              "SET LOCAL search_path=pg_catalog",
                              "SET LOCAL TimeZone='UTC'"):
                _control(conn, statement, end=end)
            context = _read(conn, CONTEXT_SQL, [
                "database", "principal", "session", "in_recovery", "readonly", "isolation"],
                end=end)
            _require(len(context) == 1)
            namespaces = _read(conn, SCHEMA_SQL,
                               ["oid", "nspname", "usage", "create_allowed"], end=end)
            relations = _read(conn, RELATION_SQL, [
                "oid", "nspname", "relname", "relkind", "select_allowed",
                "insert_allowed", "update_allowed", "delete_allowed",
                "truncate_allowed", "references_allowed", "trigger_allowed"], end=end)
            columns = _read(conn, COLUMN_SQL, [
                "oid", "attnum", "attname", "select_allowed", "insert_allowed",
                "update_allowed", "references_allowed"], end=end)
            result = validate(context[0], namespaces, relations, columns,
                              planned_after=planned_after)
            if reader_profile is not None:
                from .privileges import verify_runtime
                from .reader_access_profile import contract
                contract(reader_profile)
                verify_runtime(_BoundedProfileConnection(conn, end), "api", reader_profile=reader_profile)
                result.update(reader_profile=reader_profile, installed_policy_verified=True)
            # Bounded current-fund read proves a real SELECT wire path but
            # exports no fund identifier, NAV, or other business value.
            representative = _read(conn, REPRESENTATIVE_SQL, ["readable"], end=end)
            _require(len(representative) <= 1
                     and all(row == {"readable": 1} for row in representative))
            rollback_attempted = True
            rolled_back = _control(conn, "ROLLBACK", end=end)
            _require(rolled_back.statusmessage == "ROLLBACK")
            begun = False
            result["representative_select_completed"] = True
            return result
        except BaseException:
            if begun and rollback_attempted:
                raise ReaderCheckError("READER_ABORT_UNCONFIRMED_NO_RETRY") from None
            if begun and time.monotonic() >= end:
                raise ReaderCheckError("READER_WALL_SPENT_NO_FURTHER_IO") from None
            if begun:
                rollback_attempted = True
                try:
                    rolled_back = _control(conn, "ROLLBACK", end=end)
                    _require(rolled_back.statusmessage == "ROLLBACK")
                except BaseException:
                    raise ReaderCheckError("READER_ABORT_UNCONFIRMED_NO_RETRY") from None
            raise ReaderCheckError("READER_CHECK_FAILED_NO_RETRY") from None
