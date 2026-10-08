"""Checkpoint ordering tests for the resumable AMFI history job."""

from __future__ import annotations

from datetime import date

from mfdataindia.ingest.amfi_client import AmfiClient
from mfdataindia.jobs.backfill_nav import backfill_nav_history


class _Store:
    def __init__(self, *, should_run: bool) -> None:
        self.should_run = should_run
        self.started: list[str] = []

    def in_scope_codes(self) -> set[int]:
        return {100001}

    def checkpoint_should_run(self, source: str, kind: str, key: str) -> bool:
        return self.should_run

    def checkpoint_start(self, source: str, kind: str, key: str) -> None:
        self.started.append(key)


class _Client:
    iter_nav_history_ranges = staticmethod(AmfiClient.iter_nav_history_ranges)

    def __init__(self) -> None:
        self.fetches: list[tuple[date, date, int]] = []

    def fetch_nav_history(self, from_date: date, to_date: date, tp: int):
        self.fetches.append((from_date, to_date, tp))
        raise AssertionError("a skipped or bounded chunk must not be fetched")


def test_completed_checkpoints_are_skipped_before_network_fetch():
    store = _Store(should_run=False)
    client = _Client()

    report = backfill_nav_history(
        store, client, date(2026, 10, 8), date(2026, 10, 8)
    )

    assert report.chunks_skipped == 3
    assert client.fetches == []
    assert store.started == []


def test_zero_chunk_bound_stops_before_network_fetch():
    store = _Store(should_run=True)
    client = _Client()

    report = backfill_nav_history(
        store, client, date(2026, 10, 8), date(2026, 10, 8), max_chunks=0
    )

    assert report.chunks_done == 0
    assert client.fetches == []
    assert store.started == []


def test_history_ranges_validate_and_split_without_fetching():
    ranges = list(
        AmfiClient.iter_nav_history_ranges(
            date(2026, 1, 1), date(2026, 1, 3), chunk_days=2, tps=(1,)
        )
    )
    assert ranges == [
        (date(2026, 1, 1), date(2026, 1, 2), 1),
        (date(2026, 1, 3), date(2026, 1, 3), 1),
    ]
