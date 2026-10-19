"""Scratch-only child readiness diagnostics; never print raw child output."""

from contextlib import contextmanager
import hashlib
import re
import subprocess
import time
import requests

MAX_ATTEMPTS = 30
MAX_SECONDS = 6.0
REQUEST_SECONDS = 1.0
POLL_SECONDS = 0.2
MAX_DIAGNOSTIC_BYTES = 16384
ERROR_NAMES = frozenset(
    {
        "AssertionError",
        "AttributeError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ImportError",
        "ModuleNotFoundError",
        "NameError",
        "OSError",
        "PermissionError",
        "RuntimeError",
        "SyntaxError",
        "TimeoutError",
        "TypeError",
        "ValueError",
        "InsufficientPrivilege",
        "UndefinedTable",
        "QueryCanceled",
        "PoolTimeout",
        "ConnectionTimeout",
        "OperationalError",
        "ProgrammingError",
        "InvalidSchemaName",
    }
)


class StartupFailed(RuntimeError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__("isolated child readiness failed: " + evidence["state"])


def safe_child_evidence(log_path):
    """Allowlisted structure only; never return source text, DSN, argv or body."""
    with log_path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - MAX_DIAGNOSTIC_BYTES))
        tail = stream.read(MAX_DIAGNOSTIC_BYTES)
    names = set(re.findall(rb"(?m)^([A-Za-z][A-Za-z0-9_.]{0,80}):", tail))
    errors = sorted({name.decode().split(".")[-1] for name in names} & ERROR_NAMES)
    locations = re.findall(
        rb'File "[^"\r\n]*(mfdataindia/(?:review|enrichment|analytics|calculators)/[a-z_]+\.py)", line ([0-9]{1,6})',
        tail,
    )
    return {
        "log_bytes": size,
        "tail_bytes": len(tail),
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
        "error_types": errors,
        "code_locations": list(
            dict.fromkeys(path.decode() + ":" + line.decode() for path, line in locations)
        )[-8:],
    }


def wait_ready(
    server, probe, log_path, *, seconds=MAX_SECONDS, clock=time.monotonic, sleep=time.sleep
):
    if type(seconds) not in (int, float) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError("startup budget cannot exceed original six seconds")
    started = clock()
    attempts = 0
    state = "child_not_ready"
    status = None
    transport = None
    for _ in range(MAX_ATTEMPTS):
        returncode = server.poll()
        if returncode is not None:
            state = "child_exited"
            break
        remaining = seconds - (clock() - started)
        if remaining <= 0:
            break
        attempts += 1
        try:
            # Caller supplies the fixed localhost-only request, no redirects.
            status = probe(min(REQUEST_SECONDS, remaining))
        except (ConnectionError, requests.ConnectionError):
            transport = "connection_refused_or_unavailable"
        except (TimeoutError, requests.Timeout):
            state = "probe_timeout"
            break
        else:
            if type(status) is not int:
                state = "invalid_probe_status"
                break
            if status == 200:
                # Child must still be alive when admitting ready.
                if server.poll() is None:
                    return {
                        "ready": True,
                        "attempts": attempts,
                        "seconds": round(clock() - started, 6),
                    }
                state = "child_exited"
                break
            state = "unexpected_http_status"
            break
        remaining = seconds - (clock() - started)
        if remaining <= 0:
            break
        sleep(min(POLL_SECONDS, remaining))
    returncode = server.poll()
    if returncode is not None:
        state = "child_exited"
    evidence = {
        "ready": False,
        "state": state,
        "attempts": attempts,
        "seconds": round(clock() - started, 6),
        "child_returncode": returncode,
        "http_status": status if type(status) is int else None,
        "transport": transport,
        **safe_child_evidence(log_path),
    }
    raise StartupFailed(evidence)


@contextmanager
def owned_server(server):
    """Cleanup covers startup too; only a child created by this harness."""
    try:
        yield server
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=14)
            except subprocess.TimeoutExpired:
                # Same total fifteen-second cleanup bound, not an extension.
                server.kill()
                server.wait(timeout=1)
        else:
            server.wait(timeout=15)
