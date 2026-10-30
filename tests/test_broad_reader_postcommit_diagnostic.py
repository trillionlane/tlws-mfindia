"""Provider-free privacy checks for disposable PostgreSQL failure labels."""

import pytest

from broad_reader_postcommit_diagnostic import (
    LABELS, READ_STAGES, compare_failure_label, install_plan_stage_labels,
    read_failure_label, stage_wrapper,
)
from mfdataindia.review import broad_reader_direct_plan as planner
from test_broad_reader_direct_plan import _case as pure_plan_case


@pytest.mark.parametrize("error", [
    RuntimeError("SENSITIVE_SECRET=sentinel"),
    ValueError("SELECT private.catalogue_value FROM secret_table"),
    OSError("role/password/connection detail"),
])
def test_read_failure_ignores_exception_type_and_text(error):
    label = read_failure_label(error)
    assert label == "POSTCOMMIT_READ_FAILURE" and label in LABELS
    assert str(error) not in label and type(error).__name__ not in label
    for stage in READ_STAGES:
        assert read_failure_label(error, stage=stage) == stage
        assert str(error) not in stage and type(error).__name__ not in stage
    assert read_failure_label(error, stage=str(error)) == "POSTCOMMIT_READ_FAILURE"


def test_read_stage_wrapper_forwards_once_and_reraises_original_error():
    error = RuntimeError("SENSITIVE_CATALOGUE_SENTINEL")
    calls = []
    diagnostic = {"post_read_active": True, "label": "POSTCOMMIT_READ_FAILURE"}

    def failing(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    wrapped = stage_wrapper(diagnostic, "READ_LEDGER", failing)
    with pytest.raises(RuntimeError) as caught:
        wrapped("exact", key="same")
    assert caught.value is error
    assert calls == [(('exact',), {"key": "same"})]
    assert diagnostic["label"] == "READ_LEDGER"
    assert "SENSITIVE" not in read_failure_label(error, stage=diagnostic["label"])


def test_read_stage_wrapper_completed_and_inactive_paths_are_exact():
    calls = []
    diagnostic = {"post_read_active": True, "label": "POSTCOMMIT_READ_FAILURE"}

    def succeeding(value, *, key):
        calls.append((value, key))
        return (value, key)

    wrapped = stage_wrapper(diagnostic, "PREPARE_PLAN", succeeding,
                            completed="READ_FINAL")
    assert wrapped("exact", key="same") == ("exact", "same")
    assert calls == [("exact", "same")]
    assert diagnostic["label"] == "READ_FINAL"
    diagnostic["post_read_active"] = False
    assert wrapped("other", key="other") == ("other", "other")
    assert diagnostic["label"] == "READ_FINAL"
    assert calls == [("exact", "same"), ("other", "other")]


def test_nested_historical_catalogue_keeps_historical_label(monkeypatch):
    state, attestation, candidates, leaves = pure_plan_case()
    diagnostic = {"post_read_active": True, "historical_prepare_active": False,
                  "label": "PREPARE_PLAN"}
    original = planner.policy.prepare

    def fail_after_nested_catalogue(value):
        original(value)
        raise RuntimeError("SENSITIVE_CATALOGUE_SENTINEL")

    monkeypatch.setattr(planner.policy, "prepare", fail_after_nested_catalogue)
    install_plan_stage_labels(monkeypatch, diagnostic)
    with pytest.raises(RuntimeError, match="SENSITIVE_CATALOGUE_SENTINEL"):
        planner.prepare(state, helper_attestation=attestation,
                        candidates=candidates, system_leaves=leaves)
    assert diagnostic["label"] == "PLAN_HISTORICAL"
    assert diagnostic["historical_prepare_active"] is False
    assert (read_failure_label(RuntimeError("private"), stage=diagnostic["label"])
            == "PLAN_HISTORICAL")


def test_pure_plan_subphase_wrappers_preserve_result_and_inactive_path(monkeypatch):
    state, attestation, candidates, leaves = pure_plan_case()
    expected = planner.prepare(state, helper_attestation=attestation,
                               candidates=candidates, system_leaves=leaves)
    diagnostic = {"post_read_active": True, "historical_prepare_active": False,
                  "label": "PREPARE_PLAN"}
    install_plan_stage_labels(monkeypatch, diagnostic)
    assert planner.prepare(state, helper_attestation=attestation,
                           candidates=candidates, system_leaves=leaves) == expected
    assert diagnostic["label"] == "PLAN_REVERSAL"
    diagnostic["post_read_active"] = False
    diagnostic["label"] = "POSTCOMMIT_READ_FAILURE"
    assert planner.prepare(state, helper_attestation=attestation,
                           candidates=candidates, system_leaves=leaves) == expected
    assert diagnostic["label"] == "POSTCOMMIT_READ_FAILURE"


def test_comparison_classifier_emits_only_fixed_category_without_values():
    secret = "SENSITIVE_CATALOGUE_SENTINEL"
    before = {"plan": {"after": {"defaults": []}}, "candidates": [secret]}
    after = {"state": {"defaults": []}, "candidates": ["changed " + secret]}
    label = compare_failure_label(before, after)
    assert label == "CANDIDATES" and label in LABELS and secret not in label
    assert compare_failure_label({"plan": {}}, {"state": None}) in LABELS
