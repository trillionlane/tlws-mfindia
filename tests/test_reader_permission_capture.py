"""Synthetic protocol/identity/timeout proofs, no database/provider/credential IO."""

from copy import deepcopy
import hashlib
from pathlib import Path
import runpy
import signal
from types import SimpleNamespace

from psycopg.pq import TransactionStatus
import pytest

from mfdataindia.review import reader_permission_capture as c
from mfdataindia.review import reader_permission_capture_inventory as inv
from mfdataindia.review import reader_permission_capture_spec as spec
from test_reader_access_correction import frozen_state
from test_verified_review_rollout import manifest

ROOT = Path(__file__).resolve().parents[1]


def context():
    return dict(
        database="mfdataindia",
        principal="mfdata_admin",
        session="mfdata_admin",
        version=180006,
        isolation="repeatable read",
        readonly="on",
        statement_timeout="5s",
        lock_timeout="5s",
        transaction_timeout="15s",
        search_path="pg_catalog",
        timezone="UTC",
        pid=1,
        snapshot="1:2:",
        transaction_started_at="2026-10-25T22:00:00.000000Z",
    )


def sample():
    state = deepcopy(frozen_state())
    for group in state:
        order = (
            ("roleid", "member", "grantor")
            if group == "memberships"
            else ("oid", "attnum")
            if group == "columns"
            else ("oid",)
        )
        state[group].sort(key=lambda r: tuple(r[k] for k in order))
    candidate = [{k: r[k] for k in inv.CANDIDATE_COLUMNS} for r in state["relations"]]
    return {
        "context": context(),
        "state": state,
        "candidate": candidate,
        "completions": [
            {
                "id": n,
                **dict.fromkeys(
                    ("command_rows", "driver_rows", "fetched_rows"),
                    len(candidate) if n == "candidate" else 1,
                ),
            }
            for n in inv.inventory()["sequence"]
        ],
    }


def result_fixture():
    return spec.result(sample())


class Cursor:
    def __init__(self, rows, columns, tag):
        self.rows, self.description = rows, [SimpleNamespace(name=n) for n in columns]
        self.rowcount, self.statusmessage = len(rows), tag

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def fetchall(self):
        return deepcopy(self.rows)


class Connection:
    autocommit = True
    info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def __init__(self, data=None, transform=None):
        self.data = deepcopy(sample() if data is None else data)
        self.calls, self.transform = [], transform

    def execute(self, statement, *, prepare):
        assert prepare is False
        self.calls.append(statement)
        if statement in inv.CONTROLS or statement == inv.TERMINAL:
            cur = Cursor(
                [],
                [],
                "BEGIN"
                if statement.startswith("BEGIN")
                else "ROLLBACK"
                if statement == inv.TERMINAL
                else "SET",
            )
        else:
            definition = next(q for q in inv.inventory()["queries"] if q["sql"] == statement)
            n = definition["id"]
            rows = (
                [{"value": self.data["state"]}]
                if n == "state"
                else [self.data[n]]
                if n == "context"
                else self.data[n]
            )
            cur = Cursor(rows, definition["columns"], "SELECT " + str(len(rows)))
        if self.transform:
            self.transform(self, statement, cur)
        return cur


def worker():
    return runpy.run_path(str(ROOT / "scripts/collect_reader_permission_capture_dev.py"))


def environment():
    m = manifest(
        rollout_id=spec.IDENTITY,
        actor="rollout-certification:" + spec.IDENTITY,
        cohort=[154117],
        canary_code=154117,
    )
    env = {
        "REVIEW_MANIFEST": m.model_dump_json(),
        "DEPLOYMENT_GIT_SHA": m.git_sha,
        "CLOUD_RUN_JOB": spec.JOB,
        "CLOUD_RUN_EXECUTION": spec.JOB + "-abcde",
        "CLOUD_RUN_TASK_INDEX": "0",
        "CLOUD_RUN_TASK_ATTEMPT": "0",
        "CLOUD_RUN_TASK_COUNT": "1",
    }
    for key, p in (
        ("PROGRAM", ROOT / "scripts/collect_reader_permission_capture_dev.py"),
        ("SPEC", Path(spec.__file__)),
        ("INVENTORY", Path(inv.__file__)),
    ):
        env["READER_PERMISSION_CAPTURE_" + key + "_SHA256"] = hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
    return m, env


def test_public_capture_exact_six_queries_full_schema_and_rollback_single_use():
    conn, capture = Connection(), c.Capture()
    value = capture.run(conn)
    assert value == result_fixture()
    assert conn.calls == list(inv.CONTROLS) + [
        next(q["sql"] for q in inv.inventory()["queries"] if q["id"] == n)
        for n in inv.inventory()["sequence"]
    ] + ["ROLLBACK"]
    assert all(value[k] is False for k in spec.DENIALS)
    value["state"]["roles"].clear()
    assert conn.data["state"]["roles"]
    with pytest.raises(ValueError):
        capture.run(conn)
    assert len(conn.calls) == 13


@pytest.mark.parametrize(
    "defect",
    [
        "columns",
        "count",
        "bool_count",
        "tag",
        "context",
        "state",
        "candidate",
        "private",
        "profile",
    ],
)
def test_malformed_cursor_evidence_or_profile_fails_and_rolls_back_once(defect):
    def change(conn, statement, cursor):
        if (
            statement != inv.CONTEXT_SQL
            and statement != inv.CANDIDATE_SQL
            and statement != inv.engine.STATE_SQL
        ):
            return
        if defect == "columns":
            cursor.description.append(SimpleNamespace(name="unexpected"))
        if defect == "count":
            cursor.rowcount += 1
        if defect == "bool_count":
            cursor.rowcount = True
        if defect == "tag":
            cursor.statusmessage = "SELECT 01"
        if defect == "context" and statement == inv.CONTEXT_SQL:
            cursor.rows[0]["readonly"] = "off"
        if defect == "state" and statement == inv.engine.STATE_SQL:
            cursor.rows[0]["value"]["roles"][0]["unknown"] = 1
        if defect == "candidate" and statement == inv.CANDIDATE_SQL:
            cursor.rows[0]["owner"] += 1
        if defect == "private" and statement == inv.engine.STATE_SQL:
            cursor.rows[0]["value"]["roles"][0]["rolconfig"] = ["private.password=never-retain"]
        if defect == "profile" and statement == inv.engine.STATE_SQL:
            cursor.rows[0]["value"]["relations"][0]["oid"] += 1

    conn = Connection(transform=change)
    with pytest.raises((ValueError, inv.engine.ChangeFailure)):
        c.Capture().run(conn)
    assert conn.calls[-1] == "ROLLBACK" and conn.calls.count("ROLLBACK") == 1


@pytest.mark.parametrize(
    "phase", [inv.CONTROLS[0], inv.CONTEXT_SQL, inv.engine.STATE_SQL, inv.CANDIDATE_SQL, "ROLLBACK"]
)
def test_actual_wall_signal_marker_all_phases_has_no_further_sql(phase):
    def spent(conn, statement, _):
        if statement == phase:
            raise c.WallTimeout("synthetic wall")

    conn = Connection(transform=spent)
    previous = signal.getsignal(signal.SIGALRM)
    with pytest.raises(c.WallTimeout):
        c.Capture().run(conn)
    assert conn.calls[-1] == phase
    assert (
        signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        and signal.getsignal(signal.SIGALRM) == previous
    )


def test_monotonic_wall_exhaustion_does_not_cleanup_sql():
    now = [0]

    def spent(_, statement, __):
        if statement == inv.CONTEXT_SQL:
            now[0] = 15

    conn = Connection(transform=spent)
    with pytest.raises(c.WallTimeout), c._capture(conn, clock=lambda: now[0]):
        pytest.fail("spent wall yielded")
    assert conn.calls[-1] == inv.CONTEXT_SQL


def test_rollback_ambiguity_is_once_and_nonidle_connection_is_not_touched():
    def fail(_, statement, __):
        if statement == "ROLLBACK":
            raise RuntimeError("PRIVATE_SERVER_SQL_DSN")

    conn = Connection(transform=fail)
    with pytest.raises(RuntimeError):
        c.Capture().run(conn)
    assert conn.calls.count("ROLLBACK") == 1
    conn = Connection()
    conn.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
    with pytest.raises(ValueError):
        c.Capture().run(conn)
    assert not conn.calls


@pytest.mark.parametrize("key", list(context()))
def test_every_context_field_drift_rejected(key):
    row = context()
    row[key] = False
    with pytest.raises(ValueError):
        spec.context_shape(row)


@pytest.mark.parametrize(
    "defect", ["missing", "duplicate", "order", "outside", "foreign", "definition", "unknown"]
)
def test_candidate_is_complete_typed_sorted_in_scope_and_not_actual_witness(defect):
    s = sample()
    rows = s["candidate"]
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows.append(rows[0])
    elif defect == "order":
        rows.reverse()
    elif defect == "outside":
        rows[0]["nspname"] = "outside"
    elif defect == "foreign":
        rows[0]["relkind"] = "f"
    elif defect == "definition":
        rows[0]["definition"] = "private body"
    else:
        rows[0]["unknown"] = "private"
    with pytest.raises(ValueError):
        spec.candidate_shape(rows, s["state"])


@pytest.mark.parametrize(
    "key",
    [
        "DEPLOYMENT_GIT_SHA",
        "CLOUD_RUN_JOB",
        "CLOUD_RUN_EXECUTION",
        "CLOUD_RUN_TASK_INDEX",
        "CLOUD_RUN_TASK_ATTEMPT",
        "CLOUD_RUN_TASK_COUNT",
        "READER_PERMISSION_CAPTURE_PROGRAM_SHA256",
        "READER_PERMISSION_CAPTURE_SPEC_SHA256",
        "READER_PERMISSION_CAPTURE_INVENTORY_SHA256",
        "PGOPTIONS",
    ],
)
def test_packaged_binding_rejects_platform_source_or_inherited_drift_before_secret(key):
    m, env = environment()
    env[key] = "unexpected"
    with pytest.raises(ValueError):
        worker()["worker_binding"](m, env)


@pytest.mark.parametrize(
    "option",
    [
        "options=-crole=x",
        "hostaddr=127.0.0.1",
        "service=other",
        "user=other",
        "sslmode=require",
        "port=5433",
        "dbname=other",
    ],
)
def test_dsn_rejects_all_unapproved_connection_capabilities(option):
    dsn = (
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=mfdataindia user=mfdata_admin password=synthetic-only "
        + option
    )
    with pytest.raises(ValueError):
        worker()["connection_arguments"](dsn)


def test_worker_consumes_before_missing_manifest_without_secret_or_connect(monkeypatch):
    w = worker()
    monkeypatch.setattr(w["psycopg"], "connect", lambda **_: pytest.fail("unapproved connect"))
    with pytest.raises(KeyError):
        w["run_worker"]({})
    with pytest.raises(ValueError):
        w["run_worker"]({})


def test_unknown_result_fields_false_claims_and_private_settings_never_serialize():
    result = result_fixture()
    result["permission_authority"] = True
    with pytest.raises(ValueError):
        spec.validate_capture_result(result)
    result = result_fixture()
    result["extra"] = "private"
    with pytest.raises(ValueError):
        spec.validate_capture_result(result)


def test_complete_output_overflow_is_terminal_without_sampling(monkeypatch):
    monkeypatch.setattr(spec, "MAX_RAW_BYTES", 1000)
    conn = Connection()
    with pytest.raises(ValueError):
        c.Capture().run(conn)
    assert conn.calls[-1] == "ROLLBACK" and conn.calls.count("ROLLBACK") == 1


def test_actual_source_bytes_drift_fails_before_secret_lookup(monkeypatch):
    w = worker()
    _, env = environment()
    monkeypatch.setitem(spec.SOURCE_HASHES, "src/mfdataindia/review/reader_permission_capture.py", "0" * 64)

    class NoSecret(dict):
        def __getitem__(self, key):
            assert key != "MFDATAINDIA_DSN", "secret lookup after source drift"
            return super().__getitem__(key)

    monkeypatch.setattr(w["psycopg"], "connect", lambda **_: pytest.fail("unapproved connect"))
    with pytest.raises(ValueError):
        w["run_worker"](NoSecret(env))


def test_one_connection_closes_after_fixed_readonly_capture_and_cannot_be_retried(monkeypatch):
    from mfdataindia.review.rollout import verify_evidence
    from mfdataindia.review.evidence_transport import decode_evidence
    import json

    w = worker()
    m, env = environment()
    env["MFDATAINDIA_DSN"] = (
        "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev "
        "dbname=mfdataindia user=mfdata_admin password=synthetic-only"
    )
    conn = Connection()
    events = []
    conn.close = lambda: events.append("close")

    def connect(**arguments):
        events.append("connect")
        assert arguments["autocommit"] is True and arguments["connect_timeout"] == 5
        return conn

    monkeypatch.setattr(w["psycopg"], "connect", connect)
    rendered = w["run_worker"](env)
    value = verify_evidence(m, spec.STAGE, decode_evidence(json.loads(rendered)))
    spec.validate_result(value)
    assert value["state"] == sample()["state"] and events == ["connect", "close"]
    assert conn.calls[0].endswith("READ ONLY") and conn.calls[-1] == "ROLLBACK"
    with pytest.raises(ValueError):
        w["run_worker"](env)
    assert events == ["connect", "close"]


def test_existing_signal_timer_rejected_without_transaction_io():
    signal.setitimer(signal.ITIMER_REAL, 30)
    conn = Connection()
    try:
        with pytest.raises(ValueError):
            c.Capture().run(conn)
        assert not conn.calls
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
