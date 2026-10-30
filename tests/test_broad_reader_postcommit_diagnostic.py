"""Provider-free privacy checks for disposable PostgreSQL failure labels."""

import pytest

from broad_reader_postcommit_diagnostic import (
    LABELS, READ_STAGES, compare_failure_label, read_failure_label, stage_wrapper,
)


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


def test_comparison_classifier_emits_only_fixed_category_without_values():
    secret = "SENSITIVE_CATALOGUE_SENTINEL"
    before = {"plan": {"after": {"defaults": []}}, "candidates": [secret]}
    after = {"state": {"defaults": []}, "candidates": ["changed " + secret]}
    label = compare_failure_label(before, after)
    assert label == "CANDIDATES" and label in LABELS and secret not in label
    assert compare_failure_label({"plan": {}}, {"state": None}) in LABELS
