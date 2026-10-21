"""Three-stage transport is synthetic here; never live acceptance."""

from copy import deepcopy
import inspect
import pytest
from mfdataindia.review import clone_checkpoint as old, clone_checkpoint_v2 as cp
from test_clone_checkpoint import meta, Storage, context as old_context


def context(phase="acquire"):
    result = deepcopy(old_context())
    result.update(
        format="mfdata-disposable-clone-checkpoint-v2",
        phase=phase,
        previous_checkpoint_sha256=None if phase == "acquire" else "1" * 64,
        previous_execution_sha256=None if phase == "acquire" else "2" * 64,
    )
    result["backup"]["uri"] = result["backup"]["uri"].replace("261019f", "261020g")
    result["checkpoint_prefix"] = (
        f"verified-review-rollouts/dev-uti-gap-261020g-{phase}-checkpoint/"
    )
    return result


@pytest.mark.parametrize("phase", ["acquire", "rehearsal"])
def test_create_only_roundtrip_both_independent_handoffs(tmp_path, phase):
    source = tmp_path / "export"
    source.mkdir()
    (source / "checkpoint.dump").write_bytes(b"native-fixture-not-live")
    storage = Storage()
    bound = context(phase)
    state = {"original_baseline": {"int": 2**70, "number": "4094.35"}, "stage": phase}
    seals = {"all": {"source": "unaltered"}, "roles": ["reader"]}
    cert = cp.export_checkpoint(storage, "fixture", source, bound, state, seals)
    target = tmp_path / "import"
    target.mkdir()
    observed = cp.import_checkpoint(storage, "fixture", target, bound, cert)
    assert observed["state"] == state and observed["logical_seals"] == seals
    assert [c[0] for c in storage.calls] == ["post", "post", "get", "get"]
    # Retain the original remote generation-0 duplicate-upload control, using
    # fresh local output so it is not intercepted by the new local collision gate.
    duplicate = tmp_path / "duplicate-export"
    duplicate.mkdir()
    (duplicate / "checkpoint.dump").write_bytes(b"native-fixture-not-live")
    with pytest.raises(RuntimeError, match="create-only private archive upload failed"):
        cp.export_checkpoint(storage, "fixture", duplicate, bound, state, seals)


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "validate"),
        ("phase", "rehearsal"),
        ("previous_checkpoint_sha256", "1" * 64),
        ("previous_execution_sha256", "2" * 64),
        ("checkpoint_prefix", "verified-review-rollouts/dev-uti-gap-261019f-clone-checkpoint/"),
        ("format", "mfdata-disposable-clone-checkpoint-v1"),
    ],
)
def test_acquisition_cannot_inherit_or_transplant_stage_authority(field, value):
    bound = context()
    bound[field] = value
    with pytest.raises(ValueError):
        cp.validate_context(bound)


@pytest.mark.parametrize(
    "field,value",
    [
        ("previous_checkpoint_sha256", None),
        ("previous_execution_sha256", None),
        ("previous_checkpoint_sha256", "unsealed"),
        ("previous_execution_sha256", True),
        ("checkpoint_prefix", "verified-review-rollouts/dev-uti-gap-261020g-acquire-checkpoint/"),
    ],
)
def test_rehearsal_requires_exact_parent_and_stage(field, value):
    bound = context("rehearsal")
    bound[field] = value
    with pytest.raises(ValueError):
        cp.validate_context(bound)


def test_certificate_cannot_cross_phase_or_previous_execution():
    bound = context()
    prefix = "gs://" + cp.BUCKET + "/" + bound["checkpoint_prefix"]
    cert = {
        "context_sha256": cp.digest(bound),
        "dump": meta(bound["checkpoint_prefix"] + "snapshot.dump", b"x"),
        "state": meta(bound["checkpoint_prefix"] + "state.json", b"x"),
    }
    cp.verify_certificate(bound, cert)
    with pytest.raises(ValueError):
        cp.verify_certificate(context("rehearsal"), cert)
    later = context("rehearsal")
    cert = {
        "context_sha256": cp.digest(later),
        "dump": meta(later["checkpoint_prefix"] + "snapshot.dump", b"x"),
        "state": meta(later["checkpoint_prefix"] + "state.json", b"x"),
    }
    cp.verify_certificate(later, cert)
    later["previous_execution_sha256"] = "9" * 64
    with pytest.raises(ValueError):
        cp.verify_certificate(later, cert)
    assert prefix.endswith("acquire-checkpoint/")


def test_frozen_catalogue_algorithm_is_retained_without_dropping_any_query():
    assert inspect.getsource(cp.logical_metadata).replace(
        "(?:[0-9]{6}g|261020h|261020i|261020j|261021k)", "[0-9]{6}f"
    ) == inspect.getsource(old.logical_metadata)
    assert inspect.getsource(cp.require_restored_seals) == inspect.getsource(
        old.require_restored_seals
    )


def test_old_context_remains_rejected_by_new_version_and_accepted_by_old():
    old.validate_context(old_context())
    with pytest.raises(ValueError):
        cp.validate_context(old_context())


@pytest.mark.parametrize("phase", ["acquire", "rehearsal"])
def test_fresh_H_requires_own_exact_backup_and_stage_prefix(phase):
    bound = context(phase)
    bound["checkpoint_prefix"] = bound["checkpoint_prefix"].replace("261020g", "261020h")
    with pytest.raises(ValueError):
        cp.validate_context(bound)  # Cannot reuse failed G backup.
    bound["backup"]["uri"] = bound["backup"]["uri"].replace("261020g", "261020h")
    assert cp.validate_context(bound) == bound
    bound["checkpoint_prefix"] = bound["checkpoint_prefix"].replace("261020h", "261020k")
    with pytest.raises(ValueError):
        cp.validate_context(bound)  # No open-ended future attempt authority.
    bound["checkpoint_prefix"] = bound["checkpoint_prefix"].replace("261020k", "261021h")
    with pytest.raises(ValueError):
        cp.validate_context(bound)  # H target is this exact date only.
