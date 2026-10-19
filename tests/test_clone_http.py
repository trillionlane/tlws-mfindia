import ast
import json
import socket
import subprocess
import sys

import pytest
import requests

from mfdataindia.review.clone_http import (
    MAX_ATTEMPTS,
    MAX_DIAGNOSTIC_BYTES,
    StartupFailed,
    owned_server,
    safe_child_evidence,
    wait_ready,
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, n):
        self.now += n


class Child:
    def __init__(self, code=None):
        self.code = code
        self.terminated = False
        self.waits = []

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def wait(self, timeout):
        self.waits.append(timeout)
        return self.code


@pytest.fixture
def log(tmp_path):
    result = tmp_path / "child.log"
    result.write_bytes(b"")
    return result


def test_existing_f_startup_failure_has_no_cleanup_or_child_evidence():
    # Reproduce the existing exact loop with synthetic process/clock/request only.
    source = """for attempt in range(30):
    try:
        response=http.get('http://127.0.0.1:8081/api/enrichment/v9/policy',headers=auth,timeout=1)
        if response.status_code!=200:raise ValueError('clone server policy failed')
        break
    except requests.ConnectionError:
        if server.poll() is not None or attempt==29:raise ValueError('clone HTTP startup failed')
        time.sleep(.2)
"""
    tree = ast.parse(source)
    loop = next(
        n for n in tree.body if isinstance(n, ast.For) and ast.unparse(n.iter) == "range(30)"
    )

    class Requests:
        ConnectionError = ConnectionError

    class HTTP:
        def get(self, *args, **kwargs):
            raise ConnectionError("not ready")

    child = Child()
    clock = Clock()
    with pytest.raises(ValueError, match="clone HTTP startup failed") as error:
        exec(
            compile(ast.Module(body=[loop], type_ignores=[]), "<original-loop>", "exec"),
            {"requests": Requests, "http": HTTP(), "server": child, "auth": {}, "time": clock},
        )
    assert child.terminated is False
    assert not hasattr(error.value, "evidence")


def test_not_ready_keeps_original_attempt_and_strict_wall_bounds(log):
    clock = Clock()
    child = Child()
    calls = []

    def probe(timeout):
        calls.append(timeout)
        raise ConnectionError("credential not exported")

    with pytest.raises(StartupFailed) as error:
        with owned_server(child):
            wait_ready(child, probe, log, clock=clock, sleep=clock.sleep)
    e = error.value.evidence
    assert e["state"] == "child_not_ready"
    assert e["attempts"] == MAX_ATTEMPTS and e["seconds"] <= 6
    assert all(0 < n <= 1 for n in calls)
    assert child.terminated and child.waits == [14]
    assert "credential" not in json.dumps(e)


def test_crashed_child_is_distinct_and_never_probed(log):
    log.write_bytes(b"ValueError: postgresql://user:secret@private/db\n")
    child = Child(3)
    with pytest.raises(StartupFailed) as error:
        with owned_server(child):
            wait_ready(child, lambda timeout: pytest.fail("no probe"), log)
    assert error.value.evidence["state"] == "child_exited"
    assert error.value.evidence["child_returncode"] == 3
    assert error.value.evidence["error_types"] == ["ValueError"]
    assert "secret" not in str(error.value) + json.dumps(error.value.evidence)
    assert not child.terminated and child.waits == [15]


@pytest.mark.parametrize("status", [301, 401, 422, 500, True, "200", None])
def test_unexpected_status_cannot_pass(log, status):
    child = Child()
    with pytest.raises(StartupFailed) as error:
        with owned_server(child):
            wait_ready(child, lambda _: status, log)
    assert not error.value.evidence["ready"]
    assert child.terminated


@pytest.mark.parametrize("seconds", [6.001, 7, 30, 900, 0, -1, True, float("inf"), float("nan")])
def test_cannot_raise_budget(log, seconds):
    with pytest.raises(ValueError):
        wait_ready(Child(), lambda _: 200, log, seconds=seconds)


def test_ready_never_bypasses_child_liveness(log):
    child = Child()

    def probe(_):
        child.code = 1
        return 200

    with pytest.raises(StartupFailed) as error:
        wait_ready(child, probe, log)
    assert error.value.evidence["state"] == "child_exited"


def test_ready_child_still_cleaned_up_after_consumer_failure(log):
    child = Child()
    with pytest.raises(RuntimeError):
        with owned_server(child):
            assert wait_ready(child, lambda _: 200, log)["ready"]
            raise RuntimeError("later route check failed")
    assert child.terminated and child.waits == [14]


def test_log_is_bounded_and_never_contains_raw_messages(log):
    log.write_bytes(
        b"private stuff\n" * 3000 + b"RuntimeError: bearer SECRET\nUnknownUserError: SECRET\n"
    )
    evidence = safe_child_evidence(log)
    assert evidence["tail_bytes"] == MAX_DIAGNOSTIC_BYTES
    assert evidence["error_types"] == ["RuntimeError"]
    assert "SECRET" not in json.dumps(evidence)


def test_real_synthetic_crash_reports_only_standard_error_type(log):
    with log.open("wb") as output:
        child = subprocess.Popen(
            [sys.executable, "-c", "raise RuntimeError('DSN-fixture-only-secret')"],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        child.wait(timeout=5)
    with pytest.raises(StartupFailed) as error:
        with owned_server(child):
            wait_ready(child, lambda _: 200, log)
    assert error.value.evidence["error_types"] == ["RuntimeError"]
    assert error.value.evidence["child_returncode"] == 1
    assert "DSN-fixture" not in json.dumps(error.value.evidence)


def test_only_known_code_locations_and_error_classes_exported(log):
    log.write_bytes(
        b'  File "/secret-root/site-packages/mfdataindia/enrichment/book_store.py", line 123, in secret_function\n'
        b"psycopg.errors.InsufficientPrivilege: sensitive SQL DSN\n"
        b'  File "/secret-root/private_file.py", line 17, in sensitive_actor\n'
    )
    evidence = safe_child_evidence(log)
    assert evidence["code_locations"] == ["mfdataindia/enrichment/book_store.py:123"]
    assert evidence["error_types"] == ["InsufficientPrivilege"]
    assert all(
        value not in json.dumps(evidence)
        for value in ("secret-root", "secret_function", "sensitive", "DSN", "private_file")
    )


def test_real_synthetic_live_child_is_cleaned_on_readiness_failure(log):
    with log.open("wb") as output:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        with pytest.raises(StartupFailed) as error:
            with owned_server(child):

                def probe(_):
                    raise ConnectionError("synthetic loopback unavailable")

                wait_ready(child, probe, log, seconds=0.03)
    assert error.value.evidence["state"] == "child_not_ready"
    assert child.poll() is not None


@pytest.mark.parametrize(
    "exception,state",
    [
        (requests.ConnectionError, "child_not_ready"),
        (requests.ReadTimeout, "probe_timeout"),
    ],
)
def test_actual_requests_exceptions_are_classified(log, exception, state):
    child = Child()
    clock = Clock()

    def probe(_):
        raise exception("sensitive-DSN-body")

    with pytest.raises(StartupFailed) as error:
        with owned_server(child):
            wait_ready(child, probe, log, clock=clock, sleep=clock.sleep)
    assert error.value.evidence["state"] == state
    assert "sensitive" not in json.dumps(error.value.evidence)
    assert child.terminated


def test_stuck_owned_child_is_killed_within_existing_cleanup_budget():
    class Stuck(Child):
        killed = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.waits.append(timeout)
            if not self.killed:
                raise subprocess.TimeoutExpired("not-retained-sensitive-argv", timeout)
            return -9

        def kill(self):
            self.killed = True
            self.code = -9

    child = Stuck()
    with pytest.raises(RuntimeError, match="route check failed"):
        with owned_server(child):
            raise RuntimeError("route check failed")
    assert child.terminated and child.killed
    assert child.waits == [14, 1]
    assert sum(child.waits) == 15


def test_real_review_app_cold_import_and_dormant_start_are_not_accepted(log):
    # Actual repo app + actual uvicorn, but verifier=None prevents ALL DB calls.
    # This is a synthetic harness test, NOT authenticated PG18/DEV coverage.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    launcher = (
        "import uvicorn;from mfdataindia.review.app import create_amc_books_app;"
        "uvicorn.run(create_amc_books_app("
        '"postgresql://fixture:fixture@127.0.0.1:1/fixture",verifier=None),'
        f'host="127.0.0.1",port={port},access_log=False)'
    )
    session = requests.Session()
    session.trust_env = False
    with log.open("wb") as output:
        child = subprocess.Popen(
            [sys.executable, "-c", launcher], stdout=output, stderr=subprocess.STDOUT
        )
        with pytest.raises(StartupFailed) as error:
            with owned_server(child):

                def probe(timeout):
                    return session.get(
                        f"http://127.0.0.1:{port}/api/enrichment/v9/policy",
                        timeout=timeout,
                        allow_redirects=False,
                    ).status_code

                wait_ready(child, probe, log)
    assert error.value.evidence["state"] == "unexpected_http_status"
    assert error.value.evidence["http_status"] == 503
    assert child.poll() is not None
