"""Narrow fixed-SQL adapter around the byte-frozen grant engine.

The actual-lock check is after lock acquisition: it prevents a grant on an
unexpected closure, not the availability effect of locks already acquired.
The externally exclusive administrative window remains a trust boundary.
"""
from copy import deepcopy

from psycopg import sql

from mfdataindia.review import reader_access_correction as engine
from mfdataindia.review import reader_access_transport_spec as spec

LOCK_WITNESS_SQL = """SELECT c.oid::bigint AS oid,n.nspname,c.relname,c.relkind::text,
c.relowner::bigint AS owner,
CASE WHEN c.relkind='v' THEN jsonb_build_object('is_null',false,
'sha256',encode(sha256(convert_to(pg_get_viewdef(c.oid,false),'UTF8')),'hex'),
'utf8_bytes',octet_length(convert_to(pg_get_viewdef(c.oid,false),'UTF8')))
ELSE jsonb_build_object('is_null',true,'sha256',null,'utf8_bytes',0) END AS definition
FROM pg_catalog.pg_locks l JOIN pg_catalog.pg_class c ON c.oid=l.relation
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE l.pid=pg_backend_pid() AND l.granted AND l.locktype='relation'
AND l.mode='AccessExclusiveLock' ORDER BY c.oid"""


def _render(statement):
    return statement if isinstance(statement, str) else statement.as_string()


def _grants(change):
    return [sql.SQL("GRANT SELECT ({}) ON TABLE {}.{} TO {}").format(
        sql.SQL(",").join(sql.Identifier(c) for c in item["columns"]),
        sql.Identifier("mf"), sql.Identifier(item["relation"]), sql.Identifier(engine.READER)
    ).as_string() for item in change["delta"]]


class _GateConnection:
    """Private mechanics, not a profile bypass or operational entrypoint.

    Production creates this only after strict proposal/platform/source binding;
    disposable native fixtures exercise the same mechanics with ephemeral OIDs.
    """
    def __init__(self, connection, change, expected_locks, *, window):
        self._connection = connection
        spec.validate_window(window)
        self._window = deepcopy(window)
        self._expected_locks = deepcopy(expected_locks)
        self._grants = _grants(change)
        self._grant_index = 0
        self._locks = [sql.SQL("LOCK TABLE ONLY {}.{} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
            sql.Identifier("mf"), sql.Identifier(n)).as_string() for n in sorted(engine.TARGETS)]
        self._lock_index = 0
        self._witness = None
        self._commit_sent = False
        self._commit_returned = False
        self._closed = False

    @property
    def autocommit(self):
        return self._connection.autocommit

    @property
    def info(self):
        return self._connection.info

    @property
    def lock_witness(self):
        return deepcopy(self._witness)

    def execute(self, statement, *, prepare=False):
        spec.require(prepare is False and not self._closed)
        text = _render(statement)
        if text == "BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE":
            try:
                # Recheck after all large plan validation, inside the frozen
                # engine's active 15s timer, immediately before first wire IO.
                spec.live_window(self._window, remaining=15)
            except BaseException:
                self._closed = True
                raise
        controls = {"BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE",
                    "BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY",
                    "SET LOCAL statement_timeout='5s'", "SET LOCAL lock_timeout='5s'",
                    "SET LOCAL transaction_timeout='15s'", "SET LOCAL search_path=pg_catalog",
                    "SET LOCAL TimeZone='UTC'", engine.STATE_SQL, engine.CONTEXT_SQL}
        if self._commit_sent and not self._commit_returned:
            raise engine.ChangeFailure("COMMIT_OUTCOME_UNKNOWN")
        if text in self._locks:
            spec.require(not self._commit_sent and self._lock_index < 2 and text == self._locks[self._lock_index])
            cursor = self._connection.execute(statement, prepare=False)
            self._lock_index += 1
            if self._lock_index == 2:
                with self._connection.execute(LOCK_WITNESS_SQL, prepare=False) as witness:
                    rows = [dict(row) for row in witness.fetchall()]
                spec.require(rows == self._expected_locks)
                self._witness = deepcopy(rows)
            return cursor
        if text in self._grants:
            spec.require(not self._commit_sent and self._witness is not None and self._lock_index == 2)
            spec.require(self._grant_index < len(self._grants) and text == self._grants[self._grant_index])
            self._grant_index += 1  # Consume before ambiguous write I/O.
        elif text == "COMMIT":
            spec.require(not self._commit_sent and self._witness is not None
                         and self._grant_index == len(self._grants))
            self._commit_sent = True  # Never rollback/retry an ambiguous commit.
            cursor = self._connection.execute(statement, prepare=False)
            self._commit_returned = True
            return cursor
        elif text == "ROLLBACK":
            self._closed = True
        elif text in controls:
            spec.require(not self._commit_sent or text != "BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE")
        else:
            raise engine.ChangeFailure("UNAPPROVED_OWNER_TRANSPORT_SQL")
        return self._connection.execute(statement, prepare=False)


def guarded_connection(connection, proposal):
    """No caller-chosen SQL/profile; exact production proposal is mandatory."""
    p = spec.validate_proposal(proposal)
    return _GateConnection(connection, p["change"], p["lock_witness"], window=p["window"])
