from dataclasses import replace
from datetime import date
import json
from pathlib import Path

from mfdataindia.ingest.amfi_navall import AmfiScheme
from mfdataindia.jobs.nav_continuity import verified_prices
from mfdataindia.load import family_continuity as family

RENAMES = json.loads((Path(__file__).parent / "fixtures/nav_continuity/zerodha_case_renames.json").read_text())
DAY = date(2026, 11, 11)


def scheme(code, name, isin, *, amc="Zerodha Mutual Fund", plan="REGULAR"):
    return AmfiScheme(str(code), name, 10.5, DAY, "Open Ended Schemes",
                      "Equity Scheme - Large Cap Fund", amc, isin, None, plan,
                      option="GROWTH", in_scope=plan == "REGULAR", plan_source="COLUMN")


def zerodha(before):
    return [
        scheme(row["amfi_scheme_code"],
               row["old_scheme_name"] if before else row["new_amfi_scheme_name"],
               row["isin_growth_or_payout"],
               amc="Zerodha Mutual Fund" if before else "Zerodha CASE Mutual Fund",
               plan="DIRECT")
        for row in RENAMES
    ]


def test_zerodha_rebrand_has_25_one_to_one_id_preserving_moves():
    original, _ = family.read_source(zerodha(True))
    keys = {row.key: str(row.code) for row in original.values()}
    incoming, conflicts = family.read_source(family.normalize_issuers(zerodha(False)))
    result = family.resolve(original, keys, incoming, conflicts)
    assert not result.holds
    assert len(result.renames) == 25
    assert all(r.codes == (int(r.family_id),) for r in result.renames)
    assert {r.new_key for r in result.renames} == {r.key for r in incoming.values()}
    current = {row.key: str(row.code) for row in incoming.values()}
    assert family.resolve(incoming, current, incoming, conflicts) == family.Resolution((), ())


def test_split_or_conflicting_isin_holds_catalogue_while_verified_prices_remain():
    source = [
        scheme(1, "Old Index Fund", "INFTEST00001"),
        scheme(2, "Old Index Fund", "INFTEST00002", plan="DIRECT"),
        scheme(3, "Stable Equity Fund", "INFTEST00003"),
    ]
    before, _ = family.read_source(source)
    keys = {row.key: row.key for row in before.values()}
    changed = [replace(source[0], scheme_name="New Index Fund", isin_div_payout_or_growth="INFTEST00099"),
               source[1], source[2]]
    after, conflicts = family.read_source(changed)
    decision = family.resolve(before, keys, after, conflicts)
    assert not decision.renames
    assert {reason for _, reason in decision.holds} == {
        "RENAME_SPLIT_MERGE_OR_IDENTITY_CONFLICT", "IDENTIFIERS_CHANGED",
    }
    assert [row[0] for row in verified_prices(changed, before, after, conflicts)] == [3]


def test_whole_family_rename_requires_complete_membership():
    old_source = [scheme(1, "Old Fund", "INFTEST00001"),
                  scheme(2, "Old Fund", "INFTEST00002", plan="DIRECT")]
    before, _ = family.read_source(old_source)
    after, conflicts = family.read_source([replace(old_source[0], scheme_name="New Fund")])
    decision = family.resolve(before, {"OLD FUND": "same-id"}, after, conflicts)
    assert not decision.renames and decision.holds == (
        ("OLD FUND", "RENAME_SPLIT_MERGE_OR_IDENTITY_CONFLICT"),
    )


def test_conflicting_nav_price_observations_are_withheld():
    old = scheme(1, "Fund", "INFTEST00001")
    before, _ = family.read_source([old])
    duplicate = replace(old, nav=20.25)
    after, conflicts = family.read_source([old, duplicate])
    assert verified_prices([old, duplicate], before, after, conflicts) == []
