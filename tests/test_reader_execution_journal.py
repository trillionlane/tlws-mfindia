import os

import pytest

from mfdataindia.review import reader_execution_journal as journal


def test_exclusive_hash_chain_and_spent_identity_are_not_reset(tmp_path):
    plan = {"operation_id": "op-0123456789abcdef"}
    with journal.ProgramLock(tmp_path) as lock:
        with lock.create(plan) as records:
            assert records.plan() == plan
            first = records.append(state="CREATE_OUTCOME_UNKNOWN", kind="capture")
            second = records.append(state="JOB_CREATED_NO_EXECUTION", kind="capture")
            assert second["previous_sha256"] == first["sha256"]
            assert records.events() == [first, second]
            assert os.stat(records.root).st_mode & 0o777 == 0o700
            assert os.stat(records.root / "plan.json").st_mode & 0o777 == 0o600
        with pytest.raises(FileExistsError):
            lock.create(plan)


def test_symlinks_and_partial_records_fail_closed(tmp_path):
    plan = {"operation_id": "op-0123456789abcdef"}
    with journal.ProgramLock(tmp_path) as lock:
        with lock.create(plan) as records:
            target = records.root / "event-0000.json"
            target.symlink_to(records.root / "plan.json")
            with pytest.raises(OSError):
                records.events()


def test_directory_fsync_failure_stops_before_returning_mutation_intent(tmp_path, monkeypatch):
    plan = {"operation_id": "op-0123456789abcdef"}
    with journal.ProgramLock(tmp_path) as lock:
        with lock.create(plan) as records:
            original = os.fsync
            def fail_directory(fd):
                if fd == records.fd:
                    raise OSError("SYNTHETIC_SECRET")
                original(fd)
            monkeypatch.setattr(os, "fsync", fail_directory)
            submitted = []
            with pytest.raises(OSError):
                records.append(state="CREATE_OUTCOME_UNKNOWN", kind="capture")
                submitted.append(True)
            assert submitted == []
            assert (records.root / "event-0000.json").exists()  # Never erase spent evidence.
