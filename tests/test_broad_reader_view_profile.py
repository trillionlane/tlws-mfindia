"""Pinned CI source-view variants; these are not live DEV evidence."""

from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_view_profile as profile


_VIEWS = [
    ("mf", "nav_last_5y", "86e36ed6a6aa82cc37338b58816bcb5eef3260309dc1ef14bc348cbe9e71f1d7", 521),
    ("mf", "v_partition_health", "3c214e9242b0d1b0dc437689a658c35f032a42ab61b155ec3817618b8dedcd24", 477),
    ("mf", "v_coverage", "989430b2405490c84221c4edadbbbd69b31cd2868b650413cb29bbc9c83fe148", 828),
    ("mf", "v_nav_coverage", "2fe9431ed2208ab870832a241cf97488232e0b55d92dfe9b292bbf1148329bdd", 598),
    ("mf", "v_enrichment_coverage", "8de9dea5a7ff4686f884ba2a5c97bdc92a1979ca815ef2a992aa4165e182d2b7", 559),
    ("mf", "v_fund_data_status", "77c30e089520b091841ab5199050112986daadaa3cab6ad845c6ee617bc136c1", 8050),
    ("mf", "current_fund_facts", "50500eb1a09bd0d6d988600672e9b8ca1aea0f37e780fcc4f42ccc0cfb3cbf9e", 2957),
    ("mf", "current_risk_profile", "89998cd74e4f6534d5803036d7a989854821586159565501344a0d5b4423e108", 692),
    ("mf_live_facts", "baseline", "50500eb1a09bd0d6d988600672e9b8ca1aea0f37e780fcc4f42ccc0cfb3cbf9e", 2957),
    ("mf_live_facts", "field_versions", "cef893b1c54a3574d43b6fb291fb4af4e7610e0bee88dc3e222b79536c3ffc1e", 3764),
    ("mf_live_facts", "field_heads", "ba4ccba16ac9c3ecfab8879bd4438e0500aabc0d5a4280ad494cca47f64b428b", 190),
    ("mf_live_facts", "selected_fields", "e5cc3ce3e72e4a1c9ee38eaab590092248f432b3418bf2e430760a7056ec1312", 233),
    ("mf_live_facts", "current_values", "d98b244805e307be48e3e1557d99fda0bd9463738bbf33901c59d2bfb97ef8cd", 1212),
    ("mf_live_facts", "resolved", "a16ea3bd3eb93bb8598095d872d5bcaada699c94a946915a78d85f27808016e0", 2270),
    ("mf_book_facts", "prior_field_versions", "f0c37481c34f17b59eaac476cec73c157d98ca40eabe1e84c9672cd5d56f63a2", 3758),
]
_BOOK_CHANGES = {
    ("mf", "current_fund_facts"): ("ede28a9cf8cff6a85b54de7e431187c0c03f81ab4a3f170a42c5137af589af6e", 5757),
    ("mf_live_facts", "field_versions"): ("318916df9be3b501f5f85e4a103d6ff846553fafb94f934ed3a58b1591e7eba4", 6551),
    ("mf_live_facts", "current_values"): ("381477ebce9ab4c67827283adbbd644d07fd0a054b19225d0e78051234299d9c", 1351),
    ("mf_live_facts", "resolved"): ("ede28a9cf8cff6a85b54de7e431187c0c03f81ab4a3f170a42c5137af589af6e", 5757),
}


def _case(book=False):
    rows = []
    for index, (schema, name, sha, size) in enumerate(_VIEWS):
        if book:
            sha, size = _BOOK_CHANGES.get((schema, name), (sha, size))
        rows.append({"oid": 10000 + index, "nspname": schema, "relname": name,
                     "relkind": "v", "owner": 42,
                     "definition": {"is_null": False, "sha256": sha, "utf8_bytes": size}})
    return {"relations": rows}, deepcopy(rows)


def _legacy_case():
    state, candidates = _case()
    prior = ("mf_book_facts", "prior_field_versions")
    state["relations"] = [row for row in state["relations"]
                          if (row["nspname"], row["relname"]) != prior]
    candidates = [row for row in candidates
                  if (row["nspname"], row["relname"]) != prior]
    assert len(state["relations"]) == len(candidates) == 14
    return state, candidates


def test_exact_legacy_base44_source_view_set_matches_retained_native_reference():
    state, candidates = _legacy_case()
    result = profile.select(state, candidates,
                            base_authority_sha256=profile.BASE_AUTHORITY_SHA256,
                            variant_authority_sha256=profile.VARIANT_AUTHORITY_SHA256)
    assert result["profile"] == "LEGACY_BASE44" and result["views"] == 14
    assert result["views_sha256"] == "57fba3c06dbae98db62038f3a1654f323ac13739ce100e2a18ab095ed06e6d4b"
    assert result["source_only_candidate"] is True
    assert result["live_admission"] is False and result["permission_enabled"] is False


@pytest.mark.parametrize("defect", ["missing", "extra", "drift", "candidate"])
def test_legacy_base44_profile_rejects_incomplete_or_unreviewed_views(defect):
    state, candidates = _legacy_case()
    if defect == "missing":
        state["relations"].pop()
    elif defect == "extra":
        state["relations"].append(deepcopy(state["relations"][0]))
    elif defect == "drift":
        state["relations"][0]["definition"]["sha256"] = "0" * 64
    else:
        candidates[0]["oid"] = 99999
    with pytest.raises(profile.ViewProfileError, match="UNMATCHED_BROAD_READER_SOURCE_PROFILE"):
        profile.select(state, candidates,
                       base_authority_sha256=profile.BASE_AUTHORITY_SHA256,
                       variant_authority_sha256=profile.VARIANT_AUTHORITY_SHA256)


@pytest.mark.parametrize("book,expected", [(False, "BASE44_EXTENDED"),
                                            (True, "BASE44_BOOK_V2_SINGLE_PASS")])
def test_exact_complete_source_view_set_selects_one_variant(book, expected):
    state, candidates = _case(book)
    result = profile.select(state, candidates,
                            base_authority_sha256=profile.BASE_AUTHORITY_SHA256,
                            variant_authority_sha256=profile.VARIANT_AUTHORITY_SHA256)
    assert result["profile"] == expected and result["views"] == 15
    assert result["source_only_candidate"] is True and result["live_admission"] is False
    assert result["permission_enabled"] is False


@pytest.mark.parametrize("defect", ["missing", "extra", "drift", "candidate", "authority"])
def test_source_profile_rejects_incomplete_or_unreviewed_views(defect):
    state, candidates = _case()
    base = profile.BASE_AUTHORITY_SHA256
    if defect == "missing":
        state["relations"].pop()
    elif defect == "extra":
        state["relations"].append(deepcopy(state["relations"][0]))
    elif defect == "drift":
        state["relations"][0]["definition"]["sha256"] = "0" * 64
    elif defect == "candidate":
        candidates[0]["oid"] = 99999
    else:
        base = "0" * 64
    with pytest.raises(profile.ViewProfileError, match="UNMATCHED_BROAD_READER_SOURCE_PROFILE"):
        profile.select(state, candidates, base_authority_sha256=base,
                       variant_authority_sha256=profile.VARIANT_AUTHORITY_SHA256)
