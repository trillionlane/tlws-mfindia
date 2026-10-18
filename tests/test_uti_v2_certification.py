"""Method revision must not masquerade as an empty new-AMC certification."""

from copy import deepcopy

import pytest

from mfdataindia.enrichment import book_certification, book_sources
from test_book_preservation_v2 import proof, seal
from test_sbi_certification import bound
from test_uti_certification import fixture as v4_fixture


def fixture():
    manifest, doc = v4_fixture()
    doc["prior_projection"] = proof()
    return manifest, doc


def test_v5_method_revision_envelope_does_not_change_AMC_or_task_count():
    manifest, doc = fixture()
    assert book_certification.expectation(manifest, bound(manifest, doc)) == doc
    assert book_sources.registry()["version"] == 5
    assert book_sources.current_policy()["version"] == 9
    assert len(book_sources.registry()["sources"]) == 4
    assert book_sources.registry()["maximum_sources_per_execution"] == 6
    assert book_sources.registry()["authority_transition"]["source_count_changed"] is False


@pytest.mark.parametrize(
    "defect",
    ["legacy", "no_target", "omit_AMC", "extra_AMC", "wrong_revised", "old_target", "old_version"],
)
def test_scope_cannot_exclude_whole_target_or_untouched_AMC(defect):
    manifest, doc = fixture()
    prior = doc["prior_projection"]
    if defect == "legacy":
        prior["format"] = "official-prior-amc-projection-v1"
    elif defect == "no_target":
        prior["revised"] = {}
        prior["revised_source_keys"] = []
    elif defect == "omit_AMC":
        prior["untouched_source_keys"].remove("kotak-expanded")
    elif defect == "extra_AMC":
        prior["untouched_source_keys"].append("not-an-approved-AMC")
    elif defect == "wrong_revised":
        prior["revised_source_keys"] = ["sbi-expanded"]
        prior["revised"] = {"sbi-expanded": prior["revised"]["uti-expanded"]}
    elif defect == "old_target":
        prior["target_source_version"] = 4
    else:
        prior["previous_source_version"] = 3
    doc["prior_projection"] = seal(prior)
    with pytest.raises(ValueError):
        book_certification.expectation(manifest, bound(manifest, doc))


def test_revision_has_no_added_AMC_but_still_requires_actual_original_queries():
    old = {s["key"] for s in book_sources.registry(4)["sources"]}
    assert {s["key"] for s in book_sources.registry(5)["sources"]} - old == set()

    class Connection:
        def execute(self, query, args):
            assert args == ("a" * 64,)
            raise RuntimeError("actual UTI original was queried")

    expected = {
        "sources": {"uti-expanded": {"batch_ids": ["a" * 64]}},
        "reviewed": [],
        "original_oracles": [],
    }
    with pytest.raises(RuntimeError, match="actual UTI original"):
        book_certification.new_source_oracles(Connection(), deepcopy(expected))


@pytest.mark.parametrize(
    "version,recovery,strip",
    [(5, False, False), (5, True, True), (6, True, False), (4, True, False)],
)
def test_exact_predecessor_wire_shape_not_generic_metadata_downgrade(
    monkeypatch, version, recovery, strip
):
    monkeypatch.setattr(book_sources, "CURRENT_VERSION", version)
    facts = {
        "identity": {"code": 102398},
        "verification_status": "needs_verification",
        "observations": [
            {
                "batch_id": "a" * 64,
                "current_identity_matches": True,
                "result": {
                    "fields": {"aum": {"value": "4094.35"}},
                    "detail_header_evidence": {"text": "ORIGINAL TITLE"},
                },
                "source_key": "uti-expanded",
                "specification_hash": "b" * 64,
                "matches_installed_specification": False,
                "in_latest_successful_run": True,
            }
        ],
    }
    before = deepcopy(facts)
    wire = book_certification.wire_fund(facts, recovery=recovery)
    assert facts == before
    if strip:
        old = deepcopy(facts)
        for key in (
            "source_key",
            "specification_hash",
            "matches_installed_specification",
            "in_latest_successful_run",
        ):
            old["observations"][0].pop(key)
        assert wire == old
        assert wire["observations"][0]["result"] == facts["observations"][0]["result"]
    else:
        assert wire == facts
