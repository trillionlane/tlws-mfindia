"""Synthetic closed-registry and reference-identity policy tests; no materializer."""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from mfdataindia.producer_contract import policy as p


def semantic_fixture():
    path = Path(__file__).resolve().parent / "fixtures/producer_contract/build_fixtures.py"
    spec = importlib.util.spec_from_file_location("semantic_fixtures", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    manifest, records = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")])
    return records, manifest["sections"][1]["executionMetadata"]


@pytest.mark.parametrize("field,value", [("sourceAsOf", "2026-01-12T09:00:00Z"),
                                         ("snapshotId", "mfref-v1:" + "0" * 64)])
def test_mapping_semantics_excludes_record_envelope_but_wire_digest_retains_time(field, value):
    records, meta = semantic_fixture()
    original = p.mapping_content_digest(records, meta)
    changed = deepcopy(records)
    for item in changed:
        item[field] = value
    assert p.mapping_content_digest(changed, meta) == original
    if field == "sourceAsOf":
        assert p.section_content_digest(changed) != p.section_content_digest(records)


@pytest.mark.parametrize("field,value", [("acceptedAt", "2026-01-12T09:00:00Z"),
                                         ("nseFilenameDate", "2026-01-12"),
                                         ("nseSource", {"snapshotId": "new-source", "digest": "b" * 64}),
                                         ("canonicalUniverse", {"snapshotId": "new-canonical", "digest": "b" * 64}),
                                         ("mappingPolicyVersion", "synthetic-unsupported-policy"),
                                         ("codeRegistryVersion", "synthetic-unsupported-registry")])
def test_mapping_digest_does_not_turn_decision_pins_or_raw_provenance_into_semantic_change(field, value):
    records, meta = semantic_fixture()
    # Pure projection only; successful transition tests independently reject unsupported pins.
    assert p.mapping_content_digest(records, {**meta, field: value}) == p.mapping_content_digest(records, meta)


def test_mapping_projection_is_exact_allowlist_and_record_order_independent():
    records, meta = semantic_fixture()
    expected = {
        "version": "mfdata-mapping-semantics-v1",
        "records": [{"variant": item["variant"], "mapping": item["mapping"],
                     "providerReference": item["providerReference"]}
                    for item in sorted(records, key=p.record_sort_key)],
        "population": {key: meta[key] for key in ("universe", "recordStatusCounts", "variantOutcomeCounts",
                                                   "exclusions", "withheldReasons")},
    }
    assert p.mapping_content_digest(records, meta) == p.digest(expected)
    assert p.mapping_content_digest(list(reversed(records)), meta) == p.digest(expected)
    assert len({item["providerReference"]["schemeCode"] for item in records}) == 2


@pytest.mark.parametrize("change", ["route", "isin", "variant", "slot-option", "status", "reason",
                                    "route-removal", "universe", "record-counts", "variant-counts",
                                    "exclusions", "withheld"])
def test_semantic_projection_includes_each_decision_and_population_component(change):
    records, meta = semantic_fixture()
    before = p.mapping_content_digest(records, meta)
    items, population = deepcopy(records), deepcopy(meta)
    if change == "route":
        items[0]["providerReference"]["schemeCode"] = "SYNTHETIC-ROUTE-03"
    elif change == "isin":
        items[0]["variant"]["isin"] = "INZZ00000027"
    elif change == "variant":
        items[0]["variant"]["amfiSchemeCode"] += 1
    elif change == "slot-option":
        items[0]["variant"].update(matchedIsinSlot="DIV_REINVEST", optionCode="IDCW_REINVEST")
    elif change in {"status", "reason"}:
        items[0]["mapping"] = {"status": "CONFLICT", "reasonCodes": ["ISIN_CONFLICT"]}
        items[0]["providerReference"] = None
        if change == "reason":
            different = deepcopy(items)
            different[0]["mapping"]["reasonCodes"] = ["OPTION_CONFLICT"]
            assert p.mapping_content_digest(items, population) != p.mapping_content_digest(different, population)
    elif change == "route-removal":
        items.pop()
    elif change == "universe":
        population["universe"].update(inputVariants=3, excludedVariants=2)
    elif change == "record-counts":
        population["recordStatusCounts"]["MAPPED"] += 1
    elif change == "variant-counts":
        population["variantOutcomeCounts"]["MAPPED"] += 1
    elif change == "exclusions":
        population["exclusions"] = {"INACTIVE": 1}
    else:
        population["withheldReasons"] = {"SOURCE_SNAPSHOT_INCOMPLETE": 1}
    # Projection sensitivity only; full-section tests reject inconsistent counts/IDs.
    assert p.mapping_content_digest(items, population) != before


VARIANT = {"amfiSchemeCode": 123456, "matchedIsinSlot": "GROWTH_OR_DIV_PAYOUT",
           "isin": "INZZ00000019", "planCode": "REGULAR", "optionCode": "GROWTH"}


@pytest.mark.parametrize("plan,option,slot,expected", [
    ("REGULAR", "GROWTH", "GROWTH_OR_DIV_PAYOUT", "GROWTH"),
    ("REGULAR", "IDCW", "GROWTH_OR_DIV_PAYOUT", "IDCW_PAYOUT"),
    ("REGULAR", "IDCW", "DIV_REINVEST", "IDCW_REINVEST"),
])
def test_exact_registry_derivations(plan, option, slot, expected):
    assert p.derive_option(plan, option, slot) == expected


@pytest.mark.parametrize("plan,option,slot,reason", [
    ("DIRECT", "GROWTH", "GROWTH_OR_DIV_PAYOUT", "PLAN_OUT_OF_SCOPE"),
    ("regular", "GROWTH", "GROWTH_OR_DIV_PAYOUT", "CANONICAL_PLAN_UNRESOLVED"),
    ("Regular Plan", "Growth", "GROWTH_OR_DIV_PAYOUT", "CANONICAL_PLAN_UNRESOLVED"),
    ("REGULAR", "Growth", "GROWTH_OR_DIV_PAYOUT", "CANONICAL_OPTION_UNRESOLVED"),
    ("REGULAR", "IDCW Reinvestment", "DIV_REINVEST", "CANONICAL_OPTION_UNRESOLVED"),
    ("REGULAR", "GROWTH", "DIV_REINVEST", "OPTION_CONFLICT"),
    ("REGULAR", "IDCW", "Y", "CANONICAL_ISIN_UNRESOLVED"),
    ("REGULAR", "IDCW", "", "CANONICAL_ISIN_UNRESOLVED"),
])
def test_registry_never_guesses_from_labels_or_provider_flags(plan, option, slot, reason):
    with pytest.raises(p.PolicyError, match="^" + reason + "$"):
        p.derive_option(plan, option, slot)


def test_unmapped_requires_complete_accepted_universe_and_coarse_hold_is_not_translated():
    with pytest.raises(p.PolicyError, match="^SOURCE_SNAPSHOT_INCOMPLETE$"):
        p.record_decision("NO_EXACT_NSE_REFERENCE", complete_accepted_universe=False)
    assert p.record_decision("NO_EXACT_NSE_REFERENCE", complete_accepted_universe=True) == (
        "UNMAPPED", ["NO_EXACT_NSE_REFERENCE"])
    for raw in ("MF_HELD", "ISSUER_NOT_CORROBORATED", "CATALOGUE_PLAN_OR_OPTION_NOT_ESTABLISHED",
                "SYNTHETIC_SECRET_MARKER"):
        with pytest.raises(p.PolicyError) as error:
            p.record_decision(raw, complete_accepted_universe=True)
        assert str(error.value) == "POLICY_EVIDENCE_UNCLASSIFIED"


@pytest.mark.parametrize("reason", sorted(p.CONFLICT_REASONS))
def test_specific_conflict_evidence_stays_specific(reason):
    assert p.record_decision(reason, complete_accepted_universe=True) == ("CONFLICT", [reason])


def test_same_variant_has_distinct_stable_route_ids_independent_of_snapshot_or_line():
    route_one = p.mapped_id(VARIANT, "SYNTHETIC-ROUTE-01")
    route_two = p.mapped_id(VARIANT, "SYNTHETIC-ROUTE-02")
    assert route_one != route_two
    assert route_one == p.mapped_id(dict(reversed(list(VARIANT.items()))), "SYNTHETIC-ROUTE-01")
    assert route_one.startswith("mfer-route-v1:") and len(route_one) < 128
    for value in ("01", "1", "01-L1"):
        assert p.mapped_id(VARIANT, value) != route_one
    changed = deepcopy(VARIANT)
    changed["isin"] = "INZZ00000027"
    assert p.mapped_id(changed, "SYNTHETIC-ROUTE-01") != route_one
    changed = {**VARIANT, "optionCode": "IDCW_PAYOUT"}
    assert p.mapped_id(changed, "SYNTHETIC-ROUTE-01") != route_one


def test_assessment_id_is_variant_key_and_is_replaced_when_mapping_arrives():
    assessment = p.assessment_id(VARIANT["amfiSchemeCode"], VARIANT["matchedIsinSlot"])
    for status in ("AMBIGUOUS", "UNMAPPED", "CONFLICT"):
        item = {"variant": VARIANT, "mapping": {"status": status}, "providerReference": None}
        assert p.expected_reference_id(item) == assessment
    routes = {p.mapped_id(VARIANT, code) for code in ("SYNTHETIC-ROUTE-01", "SYNTHETIC-ROUTE-02")}
    assert assessment not in routes and len(routes) == 2
    assert p.assessment_id(123457, VARIANT["matchedIsinSlot"]) != assessment


def test_exact_tuple_id_pins():
    assert p.mapped_id(VARIANT, "SYNTHETIC-ROUTE-01") == (
        "mfer-route-v1:412f3d52aa7b5859de20f9683e1e88c6327359fd1fd846b5e1e555fe5f8b2767")
    assert p.assessment_id(123456, "GROWTH_OR_DIV_PAYOUT") == (
        "mfer-assessment-v1:14fc5b0de70d0e287b9cc7163e1f2ccd2ac6c2ad5aa2c6c787eca1764d182e03")


@pytest.mark.parametrize("field,value", [("planCode", ["REGULAR"]), ("optionCode", ["GROWTH"]),
                                         ("amfiSchemeCode", True), ("matchedIsinSlot", 1)])
def test_id_helpers_reject_nonprimitive_identity_fields(field, value):
    with pytest.raises(p.PolicyError):
        p.mapped_id({**VARIANT, field: value}, "SYNTHETIC-ROUTE-01")


@pytest.mark.parametrize("change,expected", [
    ({}, None), ({"plan": "DIRECT"}, "PLAN_OUT_OF_SCOPE"),
    ({"product_type": "SIF"}, "PRODUCT_TYPE_OUT_OF_SCOPE"),
    ({"product_type": "OTHER_PRODUCT"}, "PRODUCT_TYPE_OUT_OF_SCOPE"),
    ({"product_type": "UNCONFIRMED"}, "POPULATION_EVIDENCE_UNRESOLVED"),
    ({"active": False}, "INACTIVE"), ({"active": None}, "POPULATION_EVIDENCE_UNRESOLVED"),
    ({"canonical": False}, "NON_CANONICAL"), ({"in_scope": False}, "CANONICAL_SCOPE_EXCLUDED"),
    ({"plan": ""}, "CANONICAL_PLAN_UNRESOLVED"),
    ({"option": "unconfirmed"}, "CANONICAL_OPTION_UNRESOLVED"),
    ({"isin": ""}, "CANONICAL_ISIN_UNRESOLVED"),
])
def test_population_is_accounted_without_forcing_an_execution_record(change, expected):
    facts = {"active": True, "canonical": True, "in_scope": True, "product_type": "CONVENTIONAL_MF",
             "plan": "REGULAR", "option": "GROWTH", "slot": "GROWTH_OR_DIV_PAYOUT", "isin": "INZZ00000019"}
    assert p.population_exclusion(**{**facts, **change}) == expected


def test_population_flags_never_coerce_and_multiple_exclusions_have_one_bucket():
    facts = {"active": True, "canonical": True, "in_scope": True, "product_type": "CONVENTIONAL_MF",
             "plan": "REGULAR", "option": "GROWTH", "slot": "GROWTH_OR_DIV_PAYOUT", "isin": "INZZ00000019"}
    with pytest.raises(p.PolicyError, match="^POPULATION_EVIDENCE_INVALID$"):
        p.population_exclusion(**{**facts, "active": 1})
    assert p.population_exclusion(**{**facts, "active": False, "canonical": False}) == "NON_CANONICAL"
