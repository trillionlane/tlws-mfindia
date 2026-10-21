"""Exact I target guard/transport controls; synthetic only, never live acceptance."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mfdataindia.review import clone_checkpoint_v2 as current
from mfdataindia.review.clone_final_snapshot import create_rehearsal_output
from test_clone_checkpoint import Storage
from test_clone_checkpoint_v2 import context as historical_context

ROOT = Path(__file__).parents[1]
DESIGN = json.loads((ROOT / "docs/FS13_EXACT_I_TARGET_DESIGN.json").read_text())


@pytest.fixture
def candidate():
    return current

def bound(phase="acquire", attempt="261020i"):
    value = deepcopy(historical_context(phase))
    value["checkpoint_prefix"] = value["checkpoint_prefix"].replace("261020g", attempt)
    value["backup"]["uri"] = value["backup"]["uri"].replace("261020g", attempt)
    return value


class Connection:
    def __init__(self, name, readonly="on", host="127.0.0.1", port=55540):
        self.info = SimpleNamespace(host=host, port=port, dbname=name)
        self.readonly = readonly
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return self

    def fetchone(self):
        return {"isolation": "repeatable read", "readonly": self.readonly}

    def fetchall(self):
        return []


@pytest.mark.parametrize("phase", ["acquire", "rehearsal"])
def test_exact_I_is_an_addition_not_historical_target_replacement(candidate, phase):
    value = bound(phase)
    assert current.validate_context(value) == value
    assert candidate.validate_context(value) == value
    assert candidate.validate_context(historical_context(phase))
    assert candidate.validate_context(bound(phase, "261020h"))


@pytest.mark.parametrize("phase", ["acquire", "rehearsal"])
@pytest.mark.parametrize("attempt", ["261019i", "261021i", "261020k", "261020f"])
def test_other_date_or_letter_is_never_implicitly_approved(candidate, phase, attempt):
    with pytest.raises(ValueError):
        candidate.validate_context(bound(phase, attempt))


@pytest.mark.parametrize("phase", ["acquire", "rehearsal"])
@pytest.mark.parametrize("previous_attempt", ["261019f", "261020g", "261020h", "261021i"])
def test_failed_or_wrong_date_backup_is_rejected(candidate, phase, previous_attempt):
    value = bound(phase)
    value["backup"]["uri"] = value["backup"]["uri"].replace("261020i", previous_attempt)
    with pytest.raises(ValueError):
        candidate.validate_context(value)


@pytest.mark.parametrize("defect", ["prefix_phase", "phase_validate", "previous_missing", "previous_boolean"])
def test_phase_and_predecessors_still_fail_closed(candidate, defect):
    value = bound("rehearsal")
    if defect == "prefix_phase":
        value["checkpoint_prefix"] = value["checkpoint_prefix"].replace("-rehearsal-", "-acquire-")
    elif defect == "phase_validate":
        value["phase"] = "validate"
    elif defect == "previous_missing":
        value["previous_execution_sha256"] = None
    else:
        value["previous_checkpoint_sha256"] = True
    with pytest.raises(ValueError):
        candidate.validate_context(value)


def test_exact_I_uses_identical_full_metadata_queries_to_H(candidate):
    prior = Connection("mfdata_fs13_final_image_261020h")
    after = Connection("mfdata_fs13_final_image_261020i")
    assert current.logical_metadata(prior) == candidate.logical_metadata(after)
    assert prior.queries == after.queries
    assert len(prior.queries) == 16  # settings guard plus all 15 catalogue categories
    assert all(query.lstrip().startswith("SELECT") for query in after.queries)
    assert not any("LIMIT" in query for query in after.queries)


@pytest.mark.parametrize("host,port,name", [
    ("remote", 55540, "mfdata_fs13_final_image_261020i"),
    ("127.0.0.1", 5432, "mfdata_fs13_final_image_261020i"),
    ("/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev", 55540, "mfdataindia"),
    ("127.0.0.1", 55540, "mfdata_fs13_final_image_261021i"),
    ("127.0.0.1", 55540, "mfdata_fs13_final_image_261020k"),
])
def test_metadata_other_target_rejected_before_SQL(candidate, host, port, name):
    connection = Connection(name, host=host, port=port)
    with pytest.raises(ValueError, match="never live"):
        candidate.logical_metadata(connection)
    assert connection.queries == []


def test_exact_I_still_requires_RR_READONLY(candidate):
    connection = Connection("mfdata_fs13_final_image_261020i", readonly="off")
    with pytest.raises(ValueError, match="RR READONLY"):
        candidate.logical_metadata(connection)
    assert len(connection.queries) == 1


def test_actual_transport_two_handoffs_with_fresh_output_preserves_input(candidate, tmp_path):
    storage = Storage()
    first_dir = tmp_path / "synthetic-first"
    first_dir.mkdir()
    (first_dir / "checkpoint.dump").write_bytes(b"SYNTHETIC not pg_dump or live data")
    first_context = bound()
    state = {"original_baseline": {"integer": 2**70}, "note": "synthetic only"}
    seals = {"source": "all retained", "catalogue": "no normalization"}
    first_certificate = candidate.export_checkpoint(storage, "synthetic", first_dir, first_context, state, seals)
    incoming = tmp_path / "synthetic-second"
    incoming.mkdir()
    assert candidate.import_checkpoint(storage, "synthetic", incoming, first_context, first_certificate)["state"] == state
    input_hashes = {name: hashlib.sha256((incoming / name).read_bytes()).hexdigest()
                    for name in ("checkpoint.dump", "checkpoint-state.json")}
    output = create_rehearsal_output(incoming)
    (output / "checkpoint.dump").write_bytes(b"SYNTHETIC final snapshot, no activation")
    later = bound("rehearsal")
    later["previous_checkpoint_sha256"] = candidate.digest(first_certificate)
    later["previous_execution_sha256"] = "4" * 64
    final_state = {**state, "source_rehearsal_certificate": {"sha256": "5" * 64}}
    second_certificate = candidate.export_checkpoint(storage, "synthetic", output, later, final_state, seals)
    third = tmp_path / "synthetic-third"
    third.mkdir()
    observed = candidate.import_checkpoint(storage, "synthetic", third, later, second_certificate)
    assert observed["state"] == final_state and observed["logical_seals"] == seals
    assert candidate.require_restored_seals(seals, observed["logical_seals"])
    assert input_hashes == {name: hashlib.sha256((incoming / name).read_bytes()).hexdigest()
                            for name in input_hashes}
    assert [call[0] for call in storage.calls] == ["post", "post", "get", "get"] * 2
    changed = deepcopy(later)
    changed["previous_execution_sha256"] = "6" * 64
    with pytest.raises(ValueError):
        candidate.verify_certificate(changed, second_certificate)


def test_proposal_window_is_not_allocated_and_exact_seconds_are_required():
    value = DESIGN["proposed"]
    assert value["new_root"] is None and "PROPOSAL ONLY" in value["window_status"]
    start = datetime.fromisoformat(value["start_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(value["end_utc_exclusive"].replace("Z", "+00:00"))
    assert (end - start).total_seconds() == 900
    for when, allowed in [("2026-10-20T12:59:59Z", False), ("2026-10-20T13:00:00Z", True),
                          ("2026-10-20T13:14:59Z", True), ("2026-10-20T13:15:00Z", False),
                          ("2026-10-21T13:00:00Z", False)]:
        instant = datetime.fromisoformat(when.replace("Z", "+00:00"))
        assert (start <= instant < end) is allowed
    assert start.tzinfo == timezone.utc


def test_only_two_exact_target_guards_and_label_changed_since_reviewed_base():
    source = Path(current.__file__).read_text()
    source = source.replace("|261020j)", ")").replace("G/H/I/J stage-specific", "G/H/I stage-specific")
    extended = "(?:[0-9]{6}g|261020h|261020i)"
    original = "(?:[0-9]{6}g|261020h)"
    assert source.count(extended) == 2
    normalized = source.replace(extended, original).replace(
        "fresh approved G/H/I stage-specific", "fresh approved G/H stage-specific"
    )
    assert hashlib.sha256(normalized.encode()).hexdigest() == (
        "942a3db8c710727edfe50069444443eaf11e94e5babedb8f2102d7a9e58327fb"
    )
    smoke = (ROOT / "scripts/smoke_clone_checkpoint_v2.py").read_text()
    smoke = smoke.replace("261020j", "261020i")
    assert 'DATABASE = "mfdata_fs13_final_image_261020i"' in smoke
    assert "dev-uti-gap-pre-261020i/snapshot.dump" in smoke
    assert "dev-uti-gap-261020i-acquire-checkpoint/" in smoke
    assert "DUMP_START" in smoke and "DUMP_EXPORT" in smoke
