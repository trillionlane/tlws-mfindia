"""Keep the same API scalar comparison without megabytes of unused metadata."""

from mfdataindia.api import queries
from mfdataindia.enrichment import book_deployment, book_sources


def test_owner_reference_uses_real_api_query_and_all_supported_aliases(monkeypatch):
    aliases = {
        a for group in book_sources.current_policy()["supported_fields"].values() for a in group
    }
    facts = {a: None for a in aliases}
    facts["unused_raw_source_metadata"] = "x" * 2000000
    calls = []

    def detail(conn, code):
        calls.append((conn, code))
        return {"facts": facts}

    monkeypatch.setattr(queries, "fund_detail", detail)
    connection = object()
    result = book_deployment.core_reference(connection, 112090)
    assert result == {a: facts.get(a) for a in aliases}
    assert calls == [(connection, 112090)]
    assert len(repr(result)) < 20000
    assert "unused_raw_source_metadata" not in result


def test_missing_core_facts_are_explicit_nulls_not_a_default_value(monkeypatch):
    monkeypatch.setattr(queries, "fund_detail", lambda *a: {"facts": None})
    result = book_deployment.core_reference(object(), 112090)
    assert result and all(value is None for value in result.values())
