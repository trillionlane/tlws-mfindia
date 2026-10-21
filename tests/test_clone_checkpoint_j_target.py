"""Explicit J only; historical failed attempts remain evidence, never reusable."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from mfdataindia.review import clone_checkpoint_v2 as cp
from mfdataindia.review.clone_final_snapshot import create_rehearsal_output
from test_clone_checkpoint import Storage
from test_clone_checkpoint_i_target import bound, Connection

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("phase", ["acquire", "rehearsal"])
def test_explicit_J_and_all_historical_positive_targets(phase):
    for attempt in ("261020g", "261020h", "261020i", "261020j"):
        context = bound(phase, attempt)
        assert cp.validate_context(context) == context


@pytest.mark.parametrize("phase", ["acquire", "rehearsal"])
@pytest.mark.parametrize("attempt", ["261019j", "261021j", "261020k", "261020f"])
def test_no_implicit_date_or_future_letter_authority(phase, attempt):
    with pytest.raises(ValueError):
        cp.validate_context(bound(phase, attempt))


@pytest.mark.parametrize("phase", ["acquire", "rehearsal"])
@pytest.mark.parametrize("prior", ["261019f", "261020g", "261020h", "261020i", "261021j"])
def test_J_cannot_reuse_any_failed_backup_or_other_date(phase, prior):
    context = bound(phase, "261020j")
    context["backup"]["uri"] = context["backup"]["uri"].replace("261020j", prior)
    with pytest.raises(ValueError):
        cp.validate_context(context)


@pytest.mark.parametrize("defect", ["phase", "prefix", "missing_parent", "boolean_parent"])
def test_J_phase_and_predecessor_guards(defect):
    context = bound("rehearsal", "261020j")
    if defect == "phase":
        context["phase"] = "validate"
    elif defect == "prefix":
        context["checkpoint_prefix"] = context["checkpoint_prefix"].replace("-rehearsal-", "-acquire-")
    else:
        context["previous_execution_sha256"] = None if defect == "missing_parent" else True
    with pytest.raises(ValueError):
        cp.validate_context(context)


def test_J_exact_same_15_catalogue_categories_16_selects_and_readonly():
    old = Connection("mfdata_fs13_final_image_261020i")
    fresh = Connection("mfdata_fs13_final_image_261020j")
    assert cp.logical_metadata(old) == cp.logical_metadata(fresh)
    assert old.queries == fresh.queries and len(fresh.queries) == 16
    assert all(q.lstrip().startswith("SELECT") and "LIMIT" not in q for q in fresh.queries)
    readwrite = Connection("mfdata_fs13_final_image_261020j", readonly="off")
    with pytest.raises(ValueError, match="RR READONLY"):
        cp.logical_metadata(readwrite)


@pytest.mark.parametrize("host,port,database", [
    ("remote",55540,"mfdata_fs13_final_image_261020j"),
    ("127.0.0.1",5432,"mfdata_fs13_final_image_261020j"),
    ("127.0.0.1",55540,"mfdata_fs13_final_image_261021j"),
    ("127.0.0.1",55540,"mfdata_fs13_final_image_261020k"),
    ("/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev",55540,"mfdataindia"),
])
def test_J_remote_future_or_live_DB_rejected_before_SQL(host, port, database):
    conn = Connection(database, host=host, port=port)
    with pytest.raises(ValueError, match="never live"):
        cp.logical_metadata(conn)
    assert conn.queries == []


def test_J_real_transport_two_handoffs_preserves_input_and_predecessor(tmp_path):
    storage = Storage()
    first = tmp_path / "phase1"
    first.mkdir()
    (first / "checkpoint.dump").write_bytes(b"SYNTHETIC-not-live-J")
    context = bound("acquire", "261020j")
    state = {"all_originals": [2**70], "synthetic_only": True}
    seals = {"complete_metadata": {"roles": "all", "financial_queries": 4}}
    certificate = cp.export_checkpoint(storage, "synthetic", first, context, state, seals)
    incoming = tmp_path / "phase2"
    incoming.mkdir()
    assert cp.import_checkpoint(storage,"synthetic",incoming,context,certificate)["state"] == state
    hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in incoming.iterdir()}
    output = create_rehearsal_output(incoming)
    (output / "checkpoint.dump").write_bytes(b"SYNTHETIC-J-FINAL")
    later = bound("rehearsal", "261020j")
    later["previous_checkpoint_sha256"] = cp.digest(certificate)
    later["previous_execution_sha256"] = "4" * 64
    final_state = {**state,"source_rehearsal_certificate":{"sha256":"5"*64}}
    second = cp.export_checkpoint(storage,"synthetic",output,later,final_state,seals)
    third = tmp_path / "phase3"
    third.mkdir()
    restored = cp.import_checkpoint(storage,"synthetic",third,later,second)
    assert restored["state"] == final_state and cp.require_restored_seals(seals,restored["logical_seals"])
    assert hashes == {name:hashlib.sha256((incoming/name).read_bytes()).hexdigest() for name in hashes}
    assert [call[0] for call in storage.calls] == ["post","post","get","get"] * 2
    altered = deepcopy(later)
    altered["previous_execution_sha256"] = "6" * 64
    with pytest.raises(ValueError):
        cp.verify_certificate(altered,second)


def test_only_two_literal_guards_label_and_three_native_pins_change():
    source = Path(cp.__file__).read_text()
    old = "(?:[0-9]{6}g|261020h|261020i)"
    new = "(?:[0-9]{6}g|261020h|261020i|261020j)"
    assert source.count(new) == 2
    normalized = source.replace(new,old).replace("G/H/I/J stage-specific","G/H/I stage-specific")
    assert hashlib.sha256(normalized.encode()).hexdigest() == "c98854bb2afc8fcceca518b31696bfc658af34916502fe6daf67ccc7ede66916"
    smoke = (ROOT / "scripts/smoke_clone_checkpoint_v2.py").read_text()
    assert smoke.count("261020j") == 3
    assert hashlib.sha256(smoke.replace("261020j","261020i").encode()).hexdigest() == "be67f53d3e296e7636d7fd692bcd06f1c2a8e5f512b48f5185d3b7983df1a06c"


def test_proposed_J_window_has_not_allocated_a_root_or_launch_authority():
    proposal = json.loads((ROOT / "docs/FS13_EXACT_J_TARGET_DESIGN.json").read_text())["proposed"]
    assert proposal["new_root"] is None and "PROPOSAL ONLY" in proposal["status"]
    start = datetime.fromisoformat(proposal["start_utc"].replace("Z","+00:00"))
    end = datetime.fromisoformat(proposal["end_utc_exclusive"].replace("Z","+00:00"))
    assert (end-start).total_seconds() == 900
