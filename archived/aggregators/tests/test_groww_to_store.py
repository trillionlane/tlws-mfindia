"""Tests for the Groww -> mf.* mapping and its non-destructive write contract.

Groww is a *secondary* source: Scripbox owns the shared ``fund_facts`` columns and
its values must never be replaced. Two bug classes are guarded here:

1. **Mapped-but-unwritten.** ``groww_to_facts`` builds a dict and the caller
   projects it through ``GROWW_FUND_FACTS_COLUMNS``; a key missing from that tuple
   is dropped silently, with no error anywhere. Five fields were lost that way.
   Three were genuine and are now persisted (``base_expense_ratio``,
   ``expense_ratio_history``, ``registrar_agent``). The other two turned out not
   to be worth recovering: Groww's ``super_category`` holds the fund *name*, not a
   category, and its real ``category``/``sub_category`` hierarchy is already
   stored as ``asset_class``/``sub_asset_class``. Those two are asserted *absent*
   below, so nobody "fixes" them back into columns that would hold scheme names.
   The guard reads the mapper's own AST, so it catches *any* future field, not
   just the ones a sample payload happens to populate.
2. **Overwrite.** The loader once merged with incoming-wins semantics and replaced
   Scripbox values on 2,087 funds. ``fill_only`` + ``only_if_empty`` are now
   asserted on the actual calls the loader makes.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from mfdataindia.load.groww_to_store import (
    GROWW_EXTRA_COLUMNS,
    GROWW_FUND_FACTS_COLUMNS,
    GROWW_FUND_FACTS_TYPES,
    GROWW_OWNED_COLUMNS,
    groww_to_facts,
    load_groww_fund,
)
from mfdataindia.load.scripbox_to_store import FUND_FACTS_COLUMNS


def _mapped_keys() -> set[str]:
    """Every key ``groww_to_facts`` puts in its row dict, read from its AST.

    Static on purpose: a payload-driven check only covers the fields that payload
    populates, which is exactly how the original five went unnoticed.
    """
    src = Path(inspect.getfile(groww_to_facts)).read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "groww_to_facts")
    keys: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "row" for t in node.targets):
            continue
        if isinstance(node.value, ast.Dict):
            keys |= {k.value for k in node.value.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert keys, "could not find the row dict in groww_to_facts -- guard is stale"
    return keys


class TestMappedButUnwritten:
    # Mapped on purpose but deliberately not persisted. groww_slug is Groww's own
    # URL key, not fund data, and was excluded by decision; it stays in the mapper
    # so a future re-fetch-by-slug can re-enable it by adding one tuple entry.
    INTENTIONALLY_UNWRITTEN = {"groww_slug"}

    # Groww-added columns whose mf.fund_facts type really is text, so the default
    # (no cast) is correct. Anything else must declare a cast or it will mis-store
    # a numeric/jsonb value as text.
    TEXT_COLUMNS = {"crisil_rating", "sub_type", "exit_load_value",
                    "lock_in_period", "risk_rating"}

    def test_every_mapped_field_is_written(self):
        dropped = _mapped_keys() - set(GROWW_FUND_FACTS_COLUMNS)
        unexplained = dropped - self.INTENTIONALLY_UNWRITTEN
        assert not unexplained, (
            f"groww_to_facts maps fields that GROWW_FUND_FACTS_COLUMNS does not "
            f"include, so upsert_table silently drops them: {sorted(unexplained)}")

    #: Groww fields carrying genuinely new information; must be persisted.
    RECOVERED = ("base_expense_ratio", "expense_ratio_history", "registrar_agent")

    #: Columns that must stay NULL forever. Groww's ``super_category`` is the fund
    #: name (``super_category == fund_name`` on every fund probed) and its real
    #: ``sub_category`` is already stored as ``sub_asset_class``.
    DELIBERATELY_DROPPED = ("super_category", "sub_category")

    def test_the_three_recovered_fields_are_present(self):
        for col in self.RECOVERED:
            assert col in GROWW_FUND_FACTS_COLUMNS, f"{col} is being dropped again"
            assert col in GROWW_FUND_FACTS_TYPES, \
                f"{col} has no cast, would store as text"

    def test_the_dropped_category_fields_are_never_written(self):
        """Guard against resurrecting two columns whose source data is wrong.

        These were briefly persisted while chasing the mapped-but-unwritten bug,
        on the assumption that Groww's ``super_category`` was a category. A live
        probe of ``mfServerSideData`` showed it is the fund name. Writing it would
        fill a category column with scheme names on every fund.
        See sql/008_fund_data_status_v2.sql (DROPPED ENTIRELY).
        """
        for col in self.DELIBERATELY_DROPPED:
            assert col not in GROWW_FUND_FACTS_COLUMNS, (
                f"{col} must stay NULL: Groww's value is the fund name, or a "
                f"duplicate of sub_asset_class -- not new information")
            assert col not in _mapped_keys(), \
                f"groww_to_facts maps {col} again; it must not"

    def test_the_real_category_hierarchy_is_still_mapped(self):
        """Dropping those two columns must not cost us the real hierarchy."""
        row = dict(zip(GROWW_FUND_FACTS_COLUMNS, groww_to_facts(_PAYLOAD, 120503)))
        assert row["asset_class"] == "Equity", \
            "Groww's `category` must still land in asset_class"
        assert row["sub_asset_class"] == "Large Cap", \
            "Groww's `sub_category` must still land in sub_asset_class"

    def test_every_groww_added_column_declares_a_cast(self):
        missing = set(GROWW_EXTRA_COLUMNS) - set(GROWW_FUND_FACTS_TYPES)
        unexplained = missing - self.TEXT_COLUMNS
        assert not unexplained, (
            f"columns without an explicit cast fall back to text and will mis-store "
            f"numeric/jsonb values: {sorted(unexplained)}")


class TestOwnership:
    def test_owned_columns_are_written(self):
        assert set(GROWW_OWNED_COLUMNS) <= set(GROWW_FUND_FACTS_COLUMNS)

    def test_owned_columns_never_overlap_scripbox_ownership(self):
        overlap = set(GROWW_OWNED_COLUMNS) & set(FUND_FACTS_COLUMNS)
        assert not overlap, (
            f"Groww claims ownership of Scripbox-owned columns, so a gap-fill merge "
            f"would let it replace Scripbox data: {sorted(overlap)}")

    def test_shared_columns_are_gap_fill_only(self):
        shared = set(GROWW_FUND_FACTS_COLUMNS) & set(FUND_FACTS_COLUMNS)
        assert shared and not (shared & set(GROWW_OWNED_COLUMNS))


class _RecordingStore:
    """Captures the write calls load_groww_fund makes."""

    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.holdings: list[dict] = []
        self.amc_updates: list[tuple] = []

    def upsert_table(self, table, pk, columns, rows, **kw):
        self.upserts.append({"table": table, "pk": pk,
                             "columns": tuple(columns), **kw})

        class R:
            def as_dict(self):
                return {}
        return R()

    def replace_holdings(self, code, rows, **kw):
        self.holdings.append({"code": code, "rows": list(rows), **kw})
        return len(rows)

    def update_amc_info(self, name, row):
        self.amc_updates.append((name, row))


_PAYLOAD = {
    "search_id": "some-fund-growth",
    "fund_name": "Some Fund",
    "category": "Equity", "sub_category": "Large Cap",
    # Groww really does serve the fund name here, not a category. Left as the
    # source sends it so the tests document the trap, not an idealised payload.
    "super_category": "Some Fund", "aum": "1234.5", "expense_ratio": "1.9",
    "fund_manager": "A Manager", "nfo_risk": "Very High",
    "holdings": [{"company_name": "X Ltd", "sector_name": "Energy",
                  "corpus_per": 8.5, "nature_name": "EQUITY"}],
}


class TestNonDestructiveLoad:
    def test_facts_merge_is_gap_fill_not_overwrite(self):
        store = _RecordingStore()
        load_groww_fund(store, _PAYLOAD, 120503)
        call = store.upserts[0]
        assert call["table"] == "fund_facts"
        assert call.get("fill_only") is True, (
            "Groww must gap-fill: Scripbox's value has to win on shared columns")
        assert call.get("coalesce_missing") is not True, (
            "coalesce_missing is incoming-wins and replaces Scripbox data")
        assert tuple(call.get("overwrite_columns") or ()) == GROWW_OWNED_COLUMNS

    def test_the_pk_and_source_tag_are_never_updated(self):
        store = _RecordingStore()
        load_groww_fund(store, _PAYLOAD, 120503)
        updated = set(store.upserts[0]["update_columns"])
        assert "amfi_scheme_code" not in updated
        assert "source" not in updated, "the owning source tag must not be reassigned"

    def test_holdings_are_never_replaced(self):
        store = _RecordingStore()
        load_groww_fund(store, _PAYLOAD, 120503)
        assert store.holdings, "holdings should have been offered"
        assert store.holdings[0].get("only_if_empty") is True, (
            "fund_holdings has no source column, so Groww must not delete a "
            "snapshot it cannot attribute")

    def test_a_payload_without_holdings_writes_no_holdings(self):
        store = _RecordingStore()
        load_groww_fund(store, {k: v for k, v in _PAYLOAD.items()
                                if k != "holdings"}, 120503)
        assert store.holdings == []

    def test_amc_is_enriched_when_the_header_is_given(self):
        store = _RecordingStore()
        load_groww_fund(store, _PAYLOAD, 120503, amc_name="Some Mutual Fund")
        assert store.amc_updates and store.amc_updates[0][0] == "Some Mutual Fund"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
