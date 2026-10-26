"""Fixed broad-policy wire gate; recursive availability and admin custody are external."""
from copy import deepcopy
from psycopg import sql
from mfdataindia.review import broad_reader_policy as engine
from mfdataindia.review import broad_reader_transport_spec as spec
from mfdataindia.review.reader_access_owner_connection import LOCK_WITNESS_SQL

class Gate:
    def __init__(self, connection, proposal):
        self._conn = connection
        self._proposal = spec.validate_proposal(proposal)
        self._statements = self._proposal["change"]["sql"]
        roots = [r for r in self._proposal["change"]["before"]["relations"]
                 if r["relkind"] in ("r", "p", "v")]
        self._locks = [sql.SQL("LOCK TABLE ONLY {}.{} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
            sql.Identifier(r["nspname"]), sql.Identifier(r["relname"])).as_string()
            for r in sorted(roots, key=lambda r: (r["nspname"], r["relname"]))]
        self._index = 0
        self._witness = None
        self._commit_sent = self._commit_returned = self._closed = False

    @property
    def autocommit(self):
        return self._conn.autocommit

    @property
    def info(self):
        return self._conn.info

    @property
    def lock_witness(self):
        return deepcopy(self._witness)

    def execute(self, statement, *, prepare=False):
        spec.require(prepare is False and not self._closed)
        text = statement if type(statement) is str else statement.as_string()
        if self._commit_sent and not self._commit_returned:
            raise engine.PolicyError("COMMIT_OUTCOME_UNKNOWN")
        if text == "BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE":
            spec.require(not self._commit_sent)
            spec.live_window(self._proposal["window"], remaining=15)
        controls = {"BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE",
                    "BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY",
                    "SET LOCAL statement_timeout='5s'", "SET LOCAL lock_timeout='5s'",
                    "SET LOCAL transaction_timeout='15s'", "SET LOCAL search_path=pg_catalog",
                    "SET LOCAL TimeZone='UTC'", engine.CONTEXT_SQL, engine.STATE_SQL}
        if text == engine.STATE_SQL and not self._commit_sent and self._witness is None:
            # Executed inside the unchanged public engine's active 15s timer.
            for lock in self._locks:
                self._conn.execute(lock, prepare=False)
            with self._conn.execute(LOCK_WITNESS_SQL, prepare=False) as cursor:
                rows = [dict(r) for r in cursor.fetchall()]
                spec.require([c.name for c in cursor.description] ==
                             spec.capture_spec.frozen.CANDIDATE_COLUMNS)
                spec.require(type(cursor.rowcount) is int and cursor.rowcount == len(rows)
                             and cursor.statusmessage == "SELECT " + str(len(rows)))
            spec.require(rows == self._proposal["lock_witness"])
            self._witness = deepcopy(rows)
        if text in self._statements:
            spec.require(not self._commit_sent and self._witness is not None)
            spec.require(self._index < len(self._statements) and text == self._statements[self._index])
            self._index += 1  # Consume before ambiguous write IO.
        elif text == "COMMIT":
            spec.require(not self._commit_sent and self._witness is not None
                         and self._index == len(self._statements))
            self._commit_sent = True
            result = self._conn.execute(statement, prepare=False)
            self._commit_returned = True
            return result
        elif text == "ROLLBACK":
            self._closed = True
        elif text not in controls:
            raise engine.PolicyError("UNAPPROVED_BROAD_OWNER_SQL")
        return self._conn.execute(statement, prepare=False)

def guarded_connection(connection, proposal):
    return Gate(connection, proposal)
