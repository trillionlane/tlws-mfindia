"""Certificate-envelope boundaries only; never live source certification."""

from copy import deepcopy

import pytest

from mfdataindia.enrichment import book_sources
from mfdataindia.enrichment.book_certification import expectation
from test_sbi_certification import fixture as old_fixture, bound


def fixture():
    manifest, doc = old_fixture()
    manifest.cohort = [
        101763,
        103173,
        103174,
        103215,
        103216,
        102272,
        100669,
        100668,
        100822,
        100823,
    ]
    doc["prior_projection"]["previous_source_version"] = 3
    for key, marker in (("kotak-expanded", "1"), ("uti-expanded", "2")):
        doc["sources"][key] = {
            "denominator": 208,
            "population_hash": "f" * 64,
            "batch_ids": [marker * 64, ("3" if marker == "1" else "4") * 64],
        }
    values = {k: v for k, v in doc["reviewed"][0]["values"].items() if k != "base_expense_ratio"}
    aliases = next(iter(doc["prior_core_values"].values()))
    for code in (100669, 100668, 100822, 100823):
        doc["reviewed"].append(
            {
                "code": code,
                "batch_id": "2" * 64,
                "as_of": "2026-09-30",
                "values": deepcopy(values),
                "source_values": deepcopy(values),
            }
        )
        doc["prior_core_values"][str(code)] = deepcopy(aliases)
    doc["original_oracles"] = [
        {"code": code, "batch_id": marker * 64, "as_of": day, "source_values": values}
        for marker, day in (("2", "2026-09-30"), ("4", "2026-08-31"))
        for code in (100669, 100822)
    ]
    doc["supplemental_oracles"] = [
        {"code": code, "batch_id": marker * 64, "as_of": day, "value": "1.68"}
        for marker, day in (("2", "2026-09-30"), ("4", "2026-08-31"))
        for code in (100669, 100668, 100822, 100823)
    ]
    return manifest, doc


def test_version4_adds_supplemental_oracles_without_changing_historical_versions():
    assert book_sources.CURRENT_VERSION == 4
    manifest, doc = fixture()
    assert expectation(manifest, bound(manifest, doc)) == doc


@pytest.mark.parametrize(
    "defect",
    [
        "omitted",
        "duplicate",
        "too_few",
        "missing_value",
        "null",
        "numeric",
        "negative",
        "unbounded",
        "foreign_batch",
        "foreign_code",
        "invalid_shape",
    ],
)
def test_no_false_pass_without_distinct_bounded_actual_TER_references(defect):
    manifest, doc = fixture()
    if defect == "omitted":
        del doc["supplemental_oracles"]
    elif defect == "duplicate":
        doc["supplemental_oracles"][1] = deepcopy(doc["supplemental_oracles"][0])
    elif defect == "too_few":
        doc["supplemental_oracles"] = doc["supplemental_oracles"][:2]
    elif defect == "missing_value":
        del doc["supplemental_oracles"][0]["value"]
    elif defect == "foreign_batch":
        doc["supplemental_oracles"][0]["batch_id"] = "a" * 64
    elif defect == "foreign_code":
        doc["supplemental_oracles"][0]["code"] = 1
    elif defect == "invalid_shape":
        doc["supplemental_oracles"][0]["code"] = []
    else:
        doc["supplemental_oracles"][0]["value"] = {
            "null": None,
            "numeric": 1.68,
            "negative": "-1",
            "unbounded": "20",
        }[defect]
    with pytest.raises(ValueError):
        expectation(manifest, bound(manifest, doc))
