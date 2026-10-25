"""Single-use fixed read-only capture. Private mechanics are not identity authority."""
from contextlib import contextmanager
from copy import deepcopy
import signal
import threading
import time

from psycopg.pq import TransactionStatus

from mfdataindia.review import reader_permission_capture_inventory as inventory
from mfdataindia.review import reader_permission_capture_spec as spec
from mfdataindia.review.policy import canonical


class WallTimeout(TimeoutError):
    """Spent wall: no further database I/O, including cleanup SQL."""


class AbortUnconfirmed(RuntimeError):
    """The sole ROLLBACK outcome is not confirmed; STOP, never retry."""


class WallAbortUnconfirmed(WallTimeout):
    """Spent wall leaves abort unconfirmed; transport teardown only."""


@contextmanager
def _wall():
    spec.require(threading.current_thread() is threading.main_thread())
    spec.require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    previous = signal.getsignal(signal.SIGALRM)

    def spent(*_):
        raise WallTimeout("capture wall spent; STOP_NO_RETRY")

    signal.signal(signal.SIGALRM, spent)
    signal.setitimer(signal.ITIMER_REAL, 15)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextmanager
def _capture(conn, *, clock=time.monotonic):
    """Same fixed SQL mechanics for disposable CI; NOT production profile admission.

    Only Capture.run is the operational entrypoint. No caller SQL, identity or
    query list is accepted here. Native ephemeral OIDs cannot pass public run.
    """
    spec.require(conn.autocommit is True and conn.info.transaction_status == TransactionStatus.IDLE)
    deadline = clock() + 15
    begun, rollback_attempted = False, False
    completions = []

    def remaining():
        if clock() >= deadline:
            raise WallTimeout("capture wall spent; STOP_NO_RETRY")

    def unconfirmed(error):
        if isinstance(error, WallTimeout) or clock() >= deadline:
            raise WallAbortUnconfirmed("CAPTURE_ABORT_UNCONFIRMED_WALL_TIMEOUT_NO_RETRY") from None
        raise AbortUnconfirmed("CAPTURE_ABORT_UNCONFIRMED_NO_RETRY") from None

    def control(sql, tag):
        remaining()
        with conn.execute(sql, prepare=False) as cursor:
            spec.require(cursor.statusmessage == tag)
        remaining()

    def query(definition):
        remaining()
        with conn.execute(definition["sql"], prepare=False) as cursor:
            spec.require([c.name for c in cursor.description] == definition["columns"])
            rows = [dict(r) for r in cursor.fetchall()]
            spec.require(type(cursor.rowcount) is int and cursor.rowcount == len(rows))
            spec.require(cursor.statusmessage == "SELECT " + str(len(rows)))
            completions.append({"id": definition["id"], "command_rows": len(rows),
                                "driver_rows": cursor.rowcount, "fetched_rows": len(rows)})
        remaining()
        return rows

    with _wall():
        try:
            for i, sql in enumerate(inventory.CONTROLS):
                if i == 0:
                    begun = True  # Consume cleanup before ambiguous BEGIN I/O.
                control(sql, "BEGIN" if i == 0 else "SET")
            definitions = {d["id"]: d for d in inventory.inventory()["queries"]}
            values = []
            for name in inventory.inventory()["sequence"]:
                rows = query(definitions[name])
                if name == "context":
                    spec.require(len(rows) == 1)
                    spec.context_shape(rows[0])
                    values.append(rows[0])
                elif name == "state":
                    spec.require(len(rows) == 1 and set(rows[0]) == {"value"})
                    spec.state_shape(rows[0]["value"])
                    values.append(rows[0]["value"])
                else:
                    spec.candidate_shape(rows, values[1])
                    values.append(rows)
            context, state, candidate, state2, candidate2, context2 = values
            spec.require(context == context2 and state == state2 and candidate == candidate2)
            result = deepcopy({"context": context, "state": state, "candidate": candidate,
                               "completions": completions})
            canonical(result)  # Complete serialization is inside the transaction wall.
            remaining()
            yield result  # Public validation/serialization runs under the SAME wall.
            remaining()
            rollback_attempted = True
            control(inventory.TERMINAL, "ROLLBACK")
        except BaseException as error:
            if begun:
                if rollback_attempted or isinstance(error, WallTimeout) or clock() >= deadline:
                    unconfirmed(error)
                rollback_attempted = True
                try:
                    control(inventory.TERMINAL, "ROLLBACK")
                except BaseException as rollback_error:
                    unconfirmed(rollback_error)
            raise


class Capture:
    def __init__(self):
        self._consumed = False

    def run(self, conn):
        spec.require(not self._consumed)
        self._consumed = True
        # Public production profile validation occurs before returning evidence.
        with _capture(conn) as value:
            return spec.result(value)
