"""Unit tests for the fund-family identity (load.fund_family + slugify).

Covers the pure logic: deterministic tlws_mf_id, slugify shape, and tag
construction. The SQL upsert path is exercised against a live DB in
scripts/build_fund_family.py (and integration tests where MF_TEST_DSN is set).
"""

from __future__ import annotations

import uuid

from mfdataindia.load.fund_family import (
    FAMILY_NAMESPACE,
    tags_for,
    tlws_mf_id,
)
from mfdataindia.load.normalise import slugify


class TestSlugify:
    def test_ampersand_and_hyphenation(self):
        assert slugify("Aditya Birla Sun Life Banking & PSU Debt Fund") == \
            "aditya-birla-sun-life-banking-psu-debt-fund"

    def test_diacritics_stripped(self):
        assert slugify("Sundaram AIIMS Healthcare") == "sundaram-aiims-healthcare"

    def test_empty(self):
        assert slugify(None) == ""
        assert slugify("   ") == ""


class TestTlwsMfId:
    def test_deterministic(self):
        gk = "ADITYA BIRLA SUN LIFE BANKING PSU DEBT FUND"
        a, b = tlws_mf_id(gk), tlws_mf_id(gk)
        assert a == b
        # And it is a real, well-formed UUID (v5).
        assert uuid.UUID(a).version == 5

    def test_distinct_keys_distinct_ids(self):
        assert tlws_mf_id("FUND A") != tlws_mf_id("FUND B")

    def test_matches_direct_uuid5(self):
        gk = "KOTAK DIVIDEND YIELD FUND"
        assert tlws_mf_id(gk) == str(uuid.uuid5(FAMILY_NAMESPACE, gk))


class TestTagsFor:
    def test_order_and_dedup(self):
        tags = tags_for("HDFC Mutual Fund", "Equity", "Large Cap", "Moderate")
        assert tags == ["hdfc-mutual-fund", "equity", "large-cap", "moderate"]

    def test_drops_empty_and_none(self):
        assert tags_for(None, "Debt", None, None) == ["debt"]

    def test_duplicates_removed(self):
        assert tags_for("X", "X", "x", None) == ["x"]