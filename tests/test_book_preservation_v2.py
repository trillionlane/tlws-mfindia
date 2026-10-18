"""Synthetic whole-cohort proof boundaries, not actual data/source acceptance."""

from copy import deepcopy

from psycopg import sql
import pytest

from mfdataindia.enrichment import book_preservation, book_preservation_v2 as engine
from mfdataindia.review.policy import fingerprint


def seal(value):
    return {
        **{k: v for k, v in value.items() if k != "sha256"},
        "sha256": fingerprint({k: v for k, v in value.items() if k != "sha256"}),
    }


def target():
    return seal(
        {
            "codes": [200, 201],
            "selected_cells": {"200|aum": fingerprint("selected")},
            "selected_cell_count": 1,
            "source_primary_cells": {"200|2026-09-30|aum": fingerprint("primary")},
            "source_primary_cell_count": 1,
            "reported_TER_cells": {"200|2026-09-30": fingerprint("TER")},
            "reported_TER_cell_count": 1,
        }
    )


def reseal_target(value):
    for kind, count in (
        ("selected_cells", "selected_cell_count"),
        ("source_primary_cells", "source_primary_cell_count"),
        ("reported_TER_cells", "reported_TER_cell_count"),
    ):
        value[count] = len(value[kind])
    return seal(value)


def proof():
    payloads = {section: [fingerprint(section)] for section in engine.SECTIONS}
    return seal(
        {
            "format": engine.FORMAT,
            "previous_source_version": 4,
            "target_source_version": 5,
            "codes": [100, 200, 201],
            "untouched_codes": [100],
            "untouched_source_keys": ["absl-expanded", "kotak-expanded", "sbi-expanded"],
            "revised_source_keys": ["uti-expanded"],
            "hashes": {
                section: {"rows": 1, "sha256": fingerprint([{"sha256": h} for h in values])}
                for section, values in payloads.items()
            },
            "payload_hashes": payloads,
            "source_members": {
                section: {payloads[section][0]: [fingerprint("old source assertion")]}
                for section in engine.SECTIONS[:2]
            },
            "revised": {"uti-expanded": target()},
            "source_rows_changed": 0,
        }
    )


def test_all_four_queries_and_target_old_values_preserved_while_provenance_appends():
    before = proof()
    after = deepcopy(before)
    for groups in after["source_members"].values():
        next(iter(groups.values())).append(fingerprint("additional original-source proof"))
    revised = after["revised"]["uti-expanded"]
    revised["selected_cells"]["201|benchmark_name"] = fingerprint("new selected")
    revised["source_primary_cells"]["201|2026-09-30|benchmark_name"] = fingerprint(
        "new dated primary"
    )
    revised["reported_TER_cells"]["201|2026-09-30"] = fingerprint("new TER, not BER")
    after["revised"]["uti-expanded"] = reseal_target(revised)
    after = seal(after)
    unchanged = deepcopy((before, after))
    result = engine.compare(before, after)
    assert (before, after) == unchanged
    assert result["all_four_financial_query_payloads_unchanged"]
    assert result["additional_provenance_members_across_read_results"] == 2
    assert result["revised_source_preservation"]["uti-expanded"] == {
        "supported_primary_cells_preserved": 1,
        "new_primary_cells": 1,
        "supported_primary_period_cells_preserved": 1,
        "new_primary_period_cells": 1,
        "supported_reported_TER_cells_preserved": 1,
        "new_reported_TER_cells": 1,
    }


@pytest.mark.parametrize("section", engine.SECTIONS)
def test_no_financial_payload_change_can_be_hidden_by_new_provenance(section):
    before = proof()
    after = deepcopy(before)
    old = after["payload_hashes"][section][0]
    changed = fingerprint("changed financial row")
    after["payload_hashes"][section] = [changed]
    after["hashes"][section] = {"rows": 1, "sha256": fingerprint([{"sha256": changed}])}
    if section in after["source_members"]:
        after["source_members"][section][changed] = after["source_members"][section].pop(old)
    with pytest.raises(ValueError, match="financial"):
        engine.compare(before, seal(after))


@pytest.mark.parametrize("mode", ["removed", "replaced", "deduplicated"])
def test_old_assertion_multiset_not_just_distinct_values_or_counts(mode):
    before = proof()
    section = engine.SECTIONS[0]
    key = before["payload_hashes"][section][0]
    if mode == "deduplicated":
        before["source_members"][section][key] *= 2
        before = seal(before)
    after = deepcopy(before)
    if mode == "removed":
        after["source_members"][section][key] = []
    elif mode == "replaced":
        after["source_members"][section][key] = [fingerprint("not the original assertion")]
    else:
        after["source_members"][section][key].pop()
    with pytest.raises(ValueError):
        engine.compare(before, seal(after))


@pytest.mark.parametrize("kind", ["selected_cells", "source_primary_cells", "reported_TER_cells"])
@pytest.mark.parametrize("mode", ["remove", "change"])
def test_every_old_selected_and_historical_primary_and_TER_value_survives(kind, mode):
    before = target()
    after = deepcopy(before)
    key = next(iter(after[kind]))
    if mode == "remove":
        after[kind].pop(key)
    else:
        after[kind][key] = fingerprint("wrong value/date/unit/expense basis")
    with pytest.raises(ValueError, match="value/date"):
        engine.compare_target(before, reseal_target(after))


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_seal",
        "missing_query",
        "missing_group",
        "empty_members",
        "omitted_payload",
        "bad_count",
        "false_zero",
        "unordered_codes",
        "foreign_code",
        "wrong_scope",
        "target_count",
        "target_code",
        "target_field",
        "target_seal",
    ],
)
def test_omitted_whole_population_or_malformed_proof_is_not_a_pass(defect):
    value = proof()
    section = engine.SECTIONS[0]
    if defect == "wrong_seal":
        value["sha256"] = "f" * 64
    elif defect == "missing_query":
        value["hashes"].pop(section)
    elif defect == "missing_group":
        value["source_members"][section].clear()
    elif defect == "empty_members":
        next(iter(value["source_members"][section].values())).clear()
    elif defect == "omitted_payload":
        value["payload_hashes"][section] = []
    elif defect == "bad_count":
        value["hashes"][section]["rows"] = 2
    elif defect == "false_zero":
        value["source_rows_changed"] = False
    elif defect == "unordered_codes":
        value["codes"].reverse()
    elif defect == "foreign_code":
        value["untouched_codes"] = [999]
    elif defect == "wrong_scope":
        value["revised_source_keys"] = []
    elif defect == "target_count":
        value["revised"]["uti-expanded"]["selected_cell_count"] = 9
    elif defect == "target_code":
        value["revised"]["uti-expanded"]["codes"] = [201]
    elif defect == "target_field":
        inner = value["revised"]["uti-expanded"]
        inner["selected_cells"] = {"200|not_a_supported_field": fingerprint("bad")}
        value["revised"]["uti-expanded"] = reseal_target(inner)
    elif defect == "target_seal":
        value["revised"]["uti-expanded"]["sha256"] = "f" * 64
    if defect != "wrong_seal":
        value = seal(value)
    with pytest.raises(ValueError):
        engine.validate(value)


def test_scope_cannot_be_narrowed_to_a_few_successful_canaries():
    before = proof()
    after = deepcopy(before)
    after["codes"] = [100, 200]
    after = seal(after)
    with pytest.raises(ValueError, match="population"):
        engine.compare(before, after)


def test_sql_hashes_whole_original_queries_without_private_helper_or_float(monkeypatch):
    captured = []

    class Rows:
        def fetchall(self):
            return []

    class Connection:
        def execute(self, query, args):
            captured.append((query.as_string(None), args))
            return Rows()

    monkeypatch.setattr(
        book_preservation, "sealed_core_read_relation", lambda conn: "mf_live_facts.resolved"
    )
    monkeypatch.setattr(
        engine, "graph", lambda conn, codes, complete: sql.SQL("WITH graph_fixture AS (SELECT 1) ")
    )
    codes = list(range(1, 101))
    assert engine._rows(Connection(), codes) == []
    query, args = captured[0]
    assert args == (codes, codes)
    assert "to_jsonb(t)-'sources'" in query and "sha256" in query
    assert all(
        '"' + name + '" t' in query
        for name in ("field_versions", "selected_fields", "current_values", "resolved")
    )
    assert "LIMIT" not in query and "float" not in query and "mf_review.canonical_json" not in query
