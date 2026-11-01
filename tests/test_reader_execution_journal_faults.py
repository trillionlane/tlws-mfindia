"""Actual CLI/controller + real journals; provider/certificate data are synthetic.

Only deep catalogue-proof construction is replaced here. Actual certificate
seals/bindings, requests, write-ahead journal and dispatch boundaries still run.
The unchanged hosted connected chain supplies the real PostgreSQL semantics.
"""
import importlib.util
from functools import partial
from pathlib import Path
import os

import pytest
from cryptography.hazmat.primitives import serialization

from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import reader_execution_application as application
from mfdataindia.review import reader_execution_contract as contract
from mfdataindia.review import reader_execution_controller as controller
from mfdataindia.review import reader_execution_diagnostics as diagnostics
from mfdataindia.review import reader_execution_evidence as evidence
from mfdataindia.review.reader_execution_journal import Journal, ProgramLock
from reader_execution_fixtures import ROOT, GIT, FakeProvider, plan, authority
from reader_execution_fixtures import execution_custody as execution_custody

SECRET = "synthetic-secret-marker"


@pytest.fixture
def harness(monkeypatch, tmp_path):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(controller, "execute", partial(controller.execute, journal_base=tmp_path))
    monkeypatch.setattr(controller, "inspect", partial(controller.inspect, journal_base=tmp_path))
    value, verifier, _ = plan()
    approved = authority(value)
    found = importlib.util.spec_from_file_location("_ro02_journal_fault_cli", ROOT / "scripts/reader_execution_dev.py")
    cli = importlib.util.module_from_spec(found)
    found.loader.exec_module(cli)
    public = application.public_trust(verifier)
    public["public_key"] = verifier.key.public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(cli, "load", lambda path: value if str(path) == "synthetic-plan" else public)
    monkeypatch.setattr(cli, "Authority", lambda: type("SyntheticAuthority", (), {"record": approved})())
    monkeypatch.setattr(application.ApplicationCheck, "validate", lambda *_: {})
    monkeypatch.setattr(application.ApplicationCheck, "check", lambda *_: {
        "state": "APPLICATION_CHECKS_VERIFIED_CONSUMER_PENDING", "consumer_acceptance": "PENDING_RO04"})
    # The controller still reads and verifies the real sealed envelope. Deep
    # SQL/catalogue proofs are supplied by the separate hosted native chain.
    monkeypatch.setattr(evidence, "verify_chain", lambda _p, kind, _preds: None if kind == "capture" else {
        "capture": {}, "prior_effect": diagnostics.VERIFIED})
    monkeypatch.setattr(evidence, "prove_capacity", lambda *_: {})
    monkeypatch.setattr(evidence, "restore_capture", lambda *_: {})
    monkeypatch.setattr(evidence, "verify_grant", lambda *_: None)
    monkeypatch.setattr(evidence, "verify_reader", lambda p, predecessors, cert:
        evidence.read_certificate(p, "reader", predecessors, cert))
    adapters = {}
    class Provider(FakeProvider):
        def certificate(self, kind, name):
            self.calls.append("certificate")
            variables = {row["name"]: row["value"] for row in
                self.prepared["task"]["containers"][0]["env"] if "value" in row}
            predecessors = spec.payload_from_variables(variables)["predecessors"]
            state = {"capture": evidence.CAPTURED, "grant": evidence.GRANTED, "reader": evidence.READ}[kind]
            return evidence.certificate(value, kind, predecessors, state=state,
                effect=diagnostics.NO_GRANT if kind == "capture" else diagnostics.VERIFIED, data={})
    def factory(p, kind, repo, prepared):
        if kind not in adapters:
            adapters[kind] = Provider(p, kind, repo, prepared)
        return adapters[kind]
    monkeypatch.setattr(cli.backend, "Backend", factory)
    cli.synthetic_provider = Provider
    monkeypatch.setattr(cli.backend, "Inspector", lambda *_: pytest.fail("inspection must not reach provider"))
    monkeypatch.setattr(cli.sys, "argv", ["reader_execution_dev.py", "execute", "--plan", "synthetic-plan",
        "--trust", "synthetic-trust", "--expected-git", GIT])
    return cli, value, adapters


def emitted_failure(cli, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    raw = output.err or output.out
    result = contract.parse_json(raw.encode())
    assert result["consumer_acceptance"] == "PENDING_RO04"
    assert result["disposition"] == "STOP_INSPECT_ONLY_NO_REPLAY"
    return result


@pytest.mark.parametrize("fault,expected,creates,runs", [
    ("run_unreadable", diagnostics.UNKNOWN, 2, 2),
    ("certificate", diagnostics.VERIFIED, 2, 2),
    ("committed_unverified", diagnostics.COMMITTED, 2, 2),
    ("rollback", diagnostics.ROLLED_BACK, 2, 2),
    ("stage", diagnostics.VERIFIED, 2, 2),
    ("final", diagnostics.VERIFIED, 3, 3),
    ("stopped", diagnostics.UNKNOWN, 2, 2),
    ("journal_cleanup", diagnostics.VERIFIED, 3, 3),
    ("lock_cleanup", diagnostics.VERIFIED, 3, 3),
    ("uncertain_cleanup", diagnostics.UNKNOWN, 2, 2),
    ("run_intent", diagnostics.NO_GRANT, 2, 1),
])
def test_post_dispatch_faults_keep_execution_knowledge_and_never_replay(
        fault, expected, creates, runs, harness, monkeypatch, capsys):
    cli, value, adapters = harness
    original_append, original_execute = Journal.append, FakeProvider.execute
    tripped = []
    def append(journal, **row):
        hit = ((row["state"] == "CERTIFICATE_OBSERVED" and row.get("kind") == "grant"
                and fault in {"certificate", "committed_unverified", "rollback"})
            or (row["state"] == "STAGE_VERIFIED" and row.get("kind") == "grant" and fault == "stage")
            or (row["state"] == "OPERATION_APPLICATION_VERIFIED" and fault == "final")
            or (row["state"].startswith("STOPPED_") and fault == "stopped")
            or (row["state"] == "RUN_OUTCOME_UNKNOWN" and row.get("kind") == "grant" and fault == "run_intent"))
        if hit:
            tripped.append(row["state"])
            if fault in {"certificate", "run_intent"}:
                # Genuine incomplete durable file, not just a mocked events().
                name = f"event-{len(journal.events()):04d}.json"
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=journal.fd)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(b'{"partial":')
            raise OSError(SECRET)
        return original_append(journal, **row)
    monkeypatch.setattr(Journal, "append", append)
    def execute(adapter, kind):
        result = original_execute(adapter, kind)
        if kind == "grant" and fault in {"run_unreadable", "stopped", "uncertain_cleanup"}:
            tripped.append("submitted")
            if fault == "run_unreadable":
                original_events = Journal.events
                def unreadable(journal):
                    original_events(journal)
                    raise OSError(SECRET)
                monkeypatch.setattr(Journal, "events", unreadable)
            if fault == "stopped":
                from psycopg.errors import InsufficientPrivilege
                raise InsufficientPrivilege(SECRET)
            raise TimeoutError(SECRET)
        return result
    monkeypatch.setattr(FakeProvider, "execute", execute)
    if fault in {"committed_unverified", "rollback"}:
        original = cli.synthetic_provider.certificate
        def committed(adapter, kind, name):
            if kind == "grant":
                from mfdataindia.review.broad_reader_direct_grant import GrantError
                diagnostic = diagnostics.report(GrantError("PRECOMMIT_ABORTED_NO_RETRY"), stage="grant",
                    grant_started=True, prior_effect=diagnostics.COMMITTED if fault == "committed_unverified" else diagnostics.NO_GRANT)
                variables = {row["name"]: row["value"] for row in
                    adapter.prepared["task"]["containers"][0]["env"] if "value" in row}
                predecessors = spec.payload_from_variables(variables)["predecessors"]
                return evidence.certificate(value, kind, predecessors,
                    state=diagnostic["state"], effect=diagnostic["grant_effect"], data={}, diagnostic=diagnostic)
            return original(adapter, kind, name)
        monkeypatch.setattr(cli.synthetic_provider, "certificate", committed)
    if fault in {"journal_cleanup", "lock_cleanup", "uncertain_cleanup"}:
        target = Journal if fault == "journal_cleanup" else ProgramLock
        original_exit = target.__exit__
        def cleanup(instance, *args):
            original_exit(instance, *args)
            if adapters.get("grant") and adapters["grant"].ran:
                tripped.append("cleanup")
                raise OSError(SECRET)
        monkeypatch.setattr(target, "__exit__", cleanup)
    result = emitted_failure(cli, capsys)
    assert tripped, result
    assert result["grant_effect"] == expected
    assert result["state"] != "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
    assert result["operation_id"] == value["operation_id"]
    assert result["stage"] in diagnostics.PHASES
    if fault == "stopped":
        assert result["safe_cause"] == "PERMISSION_DENIED"
    assert sum(p.calls.count("create") for p in adapters.values()) == creates
    assert sum(p.calls.count("execute") for p in adapters.values()) == runs
    assert all(p.calls.count("create") == 1 and p.calls.count("execute") <= 1 for p in adapters.values())
    assert not {"sql", "rollback", "commit", "reconnect"}.intersection(
        call for p in adapters.values() for call in p.calls)
    if fault in {"certificate", "run_intent", "run_unreadable"}:
        files = {path.name: path.read_bytes() for path in
            Path(contract.operation_root(value)).iterdir()}
        repeated = emitted_failure(cli, capsys)
        assert repeated["grant_effect"] == diagnostics.UNKNOWN
        assert repeated["state"] != "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
        assert sum(p.calls.count("create") for p in adapters.values()) == creates
        assert sum(p.calls.count("execute") for p in adapters.values()) == runs
        assert files == {path.name: path.read_bytes() for path in
            Path(contract.operation_root(value)).iterdir()}


def test_validation_failure_is_truthful_and_spends_no_operation(harness, monkeypatch, capsys, tmp_path):
    cli, value, adapters = harness
    value["unknown"] = SECRET
    result = emitted_failure(cli, capsys)
    assert result["state"] == "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
    assert result["grant_effect"] == diagnostics.NO_GRANT
    assert not adapters and not list(tmp_path.glob("mf-reader-internal-*"))


def test_inspect_unreadable_journal_never_asserts_original_no_dispatch(harness, monkeypatch, capsys):
    cli, value, adapters = harness
    with ProgramLock(spec.ARTIFACTS) as lock, lock.create(value) as journal:
        journal.append(state="BOUNDED_PLAN_STARTED")
    monkeypatch.setattr(Journal, "events", lambda *_: (_ for _ in ()).throw(OSError(SECRET)))
    monkeypatch.setattr(cli.sys, "argv", ["reader_execution_dev.py", "inspect", "--operation-root",
        contract.operation_root(value)])
    result = emitted_failure(cli, capsys)
    assert result["grant_effect"] == diagnostics.UNKNOWN
    assert result["state"] != "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
    assert result["operation_id"] == value["operation_id"]
    assert not adapters


def test_create_intent_partial_write_prevents_all_submission_and_is_not_erased(harness, monkeypatch, capsys):
    cli, value, adapters = harness
    original = Journal._exclusive
    def partial(journal, name, document):
        if document.get("state") == "CREATE_OUTCOME_UNKNOWN":
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=journal.fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b'{"partial":')
            raise OSError(SECRET)
        return original(journal, name, document)
    monkeypatch.setattr(Journal, "_exclusive", partial)
    result = emitted_failure(cli, capsys)
    assert result["grant_effect"] == diagnostics.NO_GRANT
    assert result["state"] == "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
    assert all(not p.created and not p.ran for p in adapters.values())
    repeated = emitted_failure(cli, capsys)
    assert repeated["grant_effect"] == diagnostics.UNKNOWN
    assert all(not p.created and not p.ran for p in adapters.values())
    with Journal(contract.operation_root(value)) as journal:
        with pytest.raises(ValueError):
            journal.events()


@pytest.mark.parametrize("command", ["execute", "inspect"])
def test_context_free_execution_escape_is_unknown_not_local(harness, monkeypatch, capsys, command):
    cli, _, adapters = harness
    monkeypatch.setattr(controller, command, lambda *a, **kw: (_ for _ in ()).throw(OSError(SECRET)))
    if command == "inspect":
        monkeypatch.setattr(cli.sys, "argv", ["reader_execution_dev.py", "inspect", "--operation-root", "/synthetic"])
    result = emitted_failure(cli, capsys)
    assert result["grant_effect"] == diagnostics.UNKNOWN
    assert result["state"] != "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
    assert not adapters


def test_final_stdout_failure_keeps_verified_commit(harness, monkeypatch, capsys):
    cli, value, adapters = harness
    class Broken:
        def write(self, _value):
            raise OSError(SECRET)
        def flush(self):
            raise OSError(SECRET)
    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, "stdout", Broken())
        with pytest.raises(SystemExit) as stopped:
            cli.main()
        assert stopped.value.code == 1
    result = contract.parse_json(capsys.readouterr().err.encode())
    assert result["grant_effect"] == diagnostics.VERIFIED
    assert result["operation_id"] == value["operation_id"]
    assert result["stage"] == "application"
    assert sum(p.calls.count("execute") for p in adapters.values()) == 3
    assert SECRET not in contract.canonical(result)


@pytest.mark.parametrize("dispatched", [False, True])
def test_stderr_failure_exits_unsuccessfully_without_replay(harness, monkeypatch, dispatched):
    cli, value, adapters = harness
    if not dispatched:
        value["unknown"] = SECRET
    else:
        original = FakeProvider.execute
        def submitted(adapter, kind):
            result = original(adapter, kind)
            if kind == "grant":
                raise TimeoutError(SECRET)
            return result
        monkeypatch.setattr(FakeProvider, "execute", submitted)
        original_exit = ProgramLock.__exit__
        def cleanup(lock, *args):
            original_exit(lock, *args)
            raise OSError(SECRET)  # Force the actual CLI typed-error/stderr path.
        monkeypatch.setattr(ProgramLock, "__exit__", cleanup)
    writes = []
    class Broken:
        def write(self, _value):
            writes.append("attempted")
            raise OSError(SECRET)
        def flush(self):
            raise OSError(SECRET)
    monkeypatch.setattr(cli.sys, "stderr", Broken())
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    assert writes
    assert sum(p.calls.count("create") for p in adapters.values()) == (2 if dispatched else 0)
    assert sum(p.calls.count("execute") for p in adapters.values()) == (2 if dispatched else 0)


@pytest.mark.parametrize("fault", ["persistence", "cleanup", "inventory"])
def test_inspect_known_commit_survives_later_failure_without_mutations(harness, monkeypatch, capsys, fault):
    cli, value, adapters = harness
    cli.main()
    capsys.readouterr()
    before = {kind: list(p.calls) for kind, p in adapters.items()}
    monkeypatch.setattr(cli.sys, "argv", ["reader_execution_dev.py", "inspect", "--operation-root",
        contract.operation_root(value)])
    monkeypatch.setattr(cli.backend, "Inspector", lambda _p, kind, *_args: adapters[kind])
    if fault == "persistence":
        original = Journal.append
        def append(journal, **row):
            if row["state"].startswith("INSPECTED_"):
                raise OSError(SECRET)
            return original(journal, **row)
        monkeypatch.setattr(Journal, "append", append)
    elif fault == "cleanup":
        original = Journal.__exit__
        def cleanup(journal, *args):
            original(journal, *args)
            raise OSError(SECRET)
        monkeypatch.setattr(Journal, "__exit__", cleanup)
    else:
        monkeypatch.setattr(controller, "inventory", lambda *a, **kw: (_ for _ in ()).throw(OSError(SECRET)))
    result = emitted_failure(cli, capsys)
    assert result["grant_effect"] == diagnostics.VERIFIED
    assert result["operation_id"] == value["operation_id"]
    assert all(p.calls.count("create") == before[kind].count("create") == 1
        and p.calls.count("execute") == before[kind].count("execute") == 1 for kind, p in adapters.items())


def test_history_lock_failure_is_not_proof_of_original_no_grant(harness, monkeypatch, capsys):
    cli, value, adapters = harness
    def unavailable(lock):
        lock.close()
        raise OSError(SECRET)
    monkeypatch.setattr(ProgramLock, "__enter__", unavailable)
    result = emitted_failure(cli, capsys)
    assert result["grant_effect"] == diagnostics.UNKNOWN
    assert result["state"] != "LOCAL_VALIDATION_FAILED_NO_DISPATCH"
    assert result["operation_id"] == value["operation_id"]
    assert not adapters
