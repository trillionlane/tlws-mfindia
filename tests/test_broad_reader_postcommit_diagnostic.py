"""Provider-free privacy checks for disposable PostgreSQL failure labels."""

import pytest

from broad_reader_postcommit_diagnostic import LABELS, compare_failure_label, read_failure_label


@pytest.mark.parametrize("error", [
    RuntimeError("SENSITIVE_SECRET=sentinel"),
    ValueError("SELECT private.catalogue_value FROM secret_table"),
    OSError("role/password/connection detail"),
])
def test_read_failure_ignores_exception_type_and_text(error):
    label = read_failure_label(error)
    assert label == "POSTCOMMIT_READ_FAILURE" and label in LABELS
    assert str(error) not in label and type(error).__name__ not in label


def test_comparison_classifier_emits_only_fixed_category_without_values():
    secret = "SENSITIVE_CATALOGUE_SENTINEL"
    before = {"plan": {"after": {"defaults": []}}, "candidates": [secret]}
    after = {"state": {"defaults": []}, "candidates": ["changed " + secret]}
    label = compare_failure_label(before, after)
    assert label == "CANDIDATES" and label in LABELS and secret not in label
    assert compare_failure_label({"plan": {}}, {"state": None}) in LABELS
