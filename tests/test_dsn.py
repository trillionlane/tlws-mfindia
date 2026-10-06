"""Tests for the DSN guard.

These matter more than they look. A stale PGlite instance listens on 5433
alongside the real PostgreSQL on 5432, and every script used to default to 5433.
A run against it connects, works, and reports success while the live database is
never touched -- so the guard has to fail loudly rather than guess.
"""

from __future__ import annotations

import pytest

from mfdataindia.store.dsn import (
    PGLITE_PORT,
    DsnError,
    describe_dsn,
    dsn_port,
    resolve_dsn,
)

LIVE = "postgresql://postgres:secret@localhost:5432/mfdataindia"
STALE_KV = "host=127.0.0.1 port=5433 user=postgres dbname=postgres sslmode=disable"
STALE_URL = "postgresql://postgres@localhost:5433/postgres"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with MFDATAINDIA_DSN unset."""
    monkeypatch.delenv("MFDATAINDIA_DSN", raising=False)


class TestPortParsing:
    @pytest.mark.parametrize("dsn,want", [
        (STALE_KV, 5433),
        ("host=h port=5432 dbname=d", 5432),
        (LIVE, 5432),
        (STALE_URL, 5433),
        ("postgresql://u@h/db", None),          # unstated -> libpq default
        ("host=h dbname=d", None),
    ])
    def test_dsn_port(self, dsn, want):
        assert dsn_port(dsn) == want

    def test_ipv6_host_bracketed(self):
        assert dsn_port("postgresql://u@[::1]:5433/db") == 5433


class TestResolve:
    def test_explicit_wins(self):
        assert resolve_dsn(LIVE) == LIVE

    def test_env_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("MFDATAINDIA_DSN", LIVE)
        assert resolve_dsn() == LIVE

    def test_explicit_beats_env(self, monkeypatch):
        monkeypatch.setenv("MFDATAINDIA_DSN", "postgresql://u@h:5432/other")
        assert resolve_dsn(LIVE) == LIVE

    def test_missing_dsn_raises_rather_than_guessing(self):
        with pytest.raises(DsnError) as ei:
            resolve_dsn()
        msg = str(ei.value)
        assert "MFDATAINDIA_DSN" in msg, "must tell the user which var to set"
        assert str(PGLITE_PORT) in msg, "must explain why it will not guess"

    def test_empty_string_is_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("MFDATAINDIA_DSN", "")
        with pytest.raises(DsnError):
            resolve_dsn()


class TestStalePortRefusal:
    """The whole point: never silently write to the PGlite dev instance."""

    @pytest.mark.parametrize("stale", [STALE_KV, STALE_URL])
    def test_refuses_pglite_in_either_spelling(self, stale):
        with pytest.raises(DsnError) as ei:
            resolve_dsn(stale)
        assert "5433" in str(ei.value)
        assert "5432" in str(ei.value), "must point at the live port"

    @pytest.mark.parametrize("stale", [STALE_KV, STALE_URL])
    def test_refuses_pglite_from_env_too(self, monkeypatch, stale):
        monkeypatch.setenv("MFDATAINDIA_DSN", stale)
        with pytest.raises(DsnError):
            resolve_dsn()

    @pytest.mark.parametrize("stale", [STALE_KV, STALE_URL])
    def test_allow_pglite_opt_in_for_the_migration_script(self, stale):
        assert resolve_dsn(stale, allow_pglite=True) == stale

    def test_live_port_accepted(self):
        assert resolve_dsn(LIVE) == LIVE

    def test_unstated_port_is_accepted_as_libpq_default(self):
        assert resolve_dsn("postgresql://u@localhost/mfdataindia") \
            == "postgresql://u@localhost/mfdataindia"


class TestDescribe:
    def test_never_leaks_credentials(self):
        label = describe_dsn(LIVE)
        assert "secret" not in label
        assert "5432" in label and "mfdataindia" in label

    def test_keyword_form(self):
        label = describe_dsn(STALE_KV)
        assert "5433" in label and "postgres" in label
