from dataclasses import replace
from datetime import date

import pytest

from mfdataindia.jobs import nav_continuity as nav
from mfdataindia.load import family_continuity as family
from test_nav_family_continuity import DAY, scheme, zerodha
from test_nav_family_continuity_postgres import family_links, fetch, nav_store as nav_store, publish_catalogue, start


def test_zerodha_label_binding_is_limited_to_proven_existing_identities():
    existing, _ = family.read_source(zerodha(False))
    codes = frozenset(existing)
    assert len(codes) == 25 and codes == family.ZERODHA_HELD_CODES
    incoming = family.normalize_issuers(zerodha(False))
    assert family.bind_held_zerodha_issuers(incoming, existing) == zerodha(False)

    first = incoming[0]
    for change in (
        {"amc": "Another AMC"},
        {"amfi_scheme_code": "100033"},
        {"isin_div_payout_or_growth": "INFTEST00099"},
        {"isin_div_reinvestment": "INFTEST00098"},
        {"plan_type": "REGULAR"},
        {"option": "IDCW"},
        {"scheme_name": "Unverified family"},
    ):
        changed = replace(first, **change)
        assert family.bind_held_zerodha_issuers([changed], existing) == [changed]

    ambiguous = {**existing, int(first.amfi_scheme_code):
                 replace(existing[int(first.amfi_scheme_code)], issuer=first.amc)}
    assert family.bind_held_zerodha_issuers(incoming, ambiguous) == incoming


@pytest.mark.postgres
def test_native_issuer_label_binding_clears_exact_25_and_preserves_catalogue_ids(nav_store):
    unrelated = scheme(1, "Stable Fund", "INFTEST00001", amc="Unrelated Mutual Fund")
    original = [unrelated, *zerodha(False)]
    start(nav_store, [replace(row, nav_date=date(2026, 11, 4)) for row in original])
    conn = nav_store.connect()
    amcs_before = conn.execute("SELECT * FROM mf.amcs ORDER BY amc_id").fetchall()
    links_before = family_links(conn)
    code_bindings = ("SELECT amfi_scheme_code,amc_id,isin_growth_or_div_payout,isin_div_reinvest "
                     "FROM mf.funds ORDER BY amfi_scheme_code")
    codes_before = conn.execute(code_bindings).fetchall()
    incoming = family.normalize_issuers(original)
    old, keys = family.read_identities(conn)
    rows, conflicts = family.read_source(incoming)
    assert set(family.resolve(old, keys, rows, conflicts).holds) == {
        (str(code), "IDENTIFIERS_CHANGED") for code in family.ZERODHA_HELD_CODES
    }
    invalid = [replace(incoming[0], amc="Wrong Mutual Fund"), *incoming[1:]]
    held = publish_catalogue(nav_store, invalid)
    assert held["state"] == "CATALOGUE_HELD"
    assert held["holds"] == (("1", "IDENTIFIERS_CHANGED"),)
    assert family_links(conn) == links_before and conn.execute(code_bindings).fetchall() == codes_before

    assert nav.publish_prices(nav_store, incoming, fetch(), as_of=DAY)["load"]["inserted"] == 1
    receipt = publish_catalogue(nav_store, incoming)
    assert receipt["state"] == "CATALOGUE_PUBLISHED" and receipt["renames"] == []
    assert receipt["tag_input"]["source_snapshot_run_id"] == receipt["snapshot"]["snapshot_run_id"]
    assert receipt["tag_input"]["source_kind"] == "full-catalogue"
    assert family_links(conn) == links_before
    assert conn.execute(code_bindings).fetchall() == codes_before
    assert conn.execute("SELECT * FROM mf.amcs ORDER BY amc_id").fetchall() == amcs_before
