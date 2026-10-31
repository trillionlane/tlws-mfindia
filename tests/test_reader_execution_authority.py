"""Synthetic custody records; no actual OS/root approval is installed."""
from copy import deepcopy
import os

import pytest

from mfdataindia.review import reader_execution_authority as custody
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import reader_execution_controller as controller
from mfdataindia.review.reader_execution_journal import ProgramLock, JournalError
from reader_execution_fixtures import plan, authority, _CUSTODIED
from reader_execution_fixtures import execution_custody as execution_custody

REAL_CUSTODIED_READ = custody._custodied


def context(monkeypatch, tmp_path):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    value, _, _ = plan()
    approved = authority(value)
    with ProgramLock(tmp_path) as lock:
        lineage = controller._unresolved(lock)
    return value, approved, lineage


def test_one_custodied_plan_binds_full_prior_lineage_and_inventory(monkeypatch, tmp_path):
    value, approved, lineage = context(monkeypatch, tmp_path)
    source = controller.validate_authority(value, approved, lineage=lineage)
    assert source.verify(value, approved, lineage=lineage)
    assert not list(tmp_path.glob("mf-reader-internal-*"))


@pytest.mark.parametrize("field", ["plan", "epoch", "prior_lineage_sha256", "prior_inventory_sha256", "scope", "direct_human_record"])
def test_caller_cannot_change_the_independently_custodied_record(field, monkeypatch, tmp_path):
    value, approved, lineage = context(monkeypatch, tmp_path)
    changed = deepcopy(approved)
    changed[field] = "synthetic-untrusted"
    with pytest.raises(custody.AuthorityError):
        controller.validate_authority(value, changed, lineage=lineage)


@pytest.mark.parametrize("field", ["inventory", "operations", "genesis_sha256", "journal_identity"])
def test_missing_or_changed_local_history_blocks_even_with_empty_cloud(field, monkeypatch, tmp_path):
    value, approved, lineage = context(monkeypatch, tmp_path)
    changed = deepcopy(lineage)
    changed[field] = {"synthetic": "untrusted"}
    with pytest.raises(custody.AuthorityError):
        controller.validate_authority(value, approved, lineage=changed)


def test_replacing_root_record_mid_chain_cannot_change_approved_plan(monkeypatch, tmp_path):
    value, approved, lineage = context(monkeypatch, tmp_path)
    source = controller.validate_authority(value, approved, lineage=lineage)
    _CUSTODIED[custody.APPROVAL]["epoch"] += 1
    with pytest.raises(custody.AuthorityError):
        source.verify(value, approved, lineage=lineage)


def test_missing_genesis_is_not_silently_initialized(monkeypatch, tmp_path):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    with ProgramLock(tmp_path) as lock:
        with pytest.raises(JournalError, match="PROGRAMME_HISTORY_MISSING_NO_AUTOMATIC_RESET"):
            controller._unresolved(lock)
    assert not (tmp_path / ".reader-execution-genesis.json").exists()


def test_actual_os_custody_rejects_executor_owned_file_and_symlink(monkeypatch, tmp_path):
    path = tmp_path / "not-an-independent-approval.json"
    path.write_text("{}\n")
    path.chmod(0o600)
    monkeypatch.setattr(custody, "ANCHOR", path)
    # Real OS descriptors/ownership, no root simulation or root writes. User
    # ownership of this file/ancestor must never become independent custody.
    with pytest.raises((custody.AuthorityError, OSError)):
        REAL_CUSTODIED_READ(path)
    link = tmp_path / "approval-link"
    link.symlink_to(path)
    monkeypatch.setattr(custody, "ANCHOR", link)
    with pytest.raises((custody.AuthorityError, OSError)):
        REAL_CUSTODIED_READ(link)
    assert path.stat().st_uid == os.getuid()
