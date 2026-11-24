"""Operation-local immutable construction; native SQL proof is separate."""
import json
from collections import Counter
from contextvars import Context
from pathlib import Path

import pytest

from mfdataindia.enrichment import book_sources
from mfdataindia.review.policy import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def test_all_eight_complete_policy_documents_and_configuration_hashes_are_unchanged():
    expected = json.loads((ROOT / 'tests/fixtures/hdfc/policies-before-installer-reuse.json').read_bytes())
    for version in range(1, 9):
        row = expected[str(version)]
        assert book_sources.current_policy(version) == row['policy']
        assert fingerprint(book_sources.current_policy(version)) == fingerprint(row['policy'])
        assert book_sources.configuration(version) == row['configuration']


def test_installer_scope_constructs_each_policy_once_and_resets_after_error(monkeypatch):
    original = book_sources._construct_current_policy
    calls = Counter()
    def construct(version):
        calls[version] += 1
        return original(version)
    monkeypatch.setattr(book_sources, '_construct_current_policy', construct)
    with pytest.raises(RuntimeError, match='controlled'):
        with book_sources.reuse_current_policies():
            assert calls == {v: 1 for v in range(1, 9)}
            for _ in range(3):
                book_sources.current_policies()
            assert calls == {v: 1 for v in range(1, 9)}
            raise RuntimeError('controlled')
    before = calls.copy()
    with book_sources.reuse_current_policies():
        assert calls == before + Counter({v: 1 for v in range(1, 9)})


def test_reuse_returns_isolated_documents_and_does_not_cross_contexts():
    expected = book_sources.current_policy(8)
    with book_sources.reuse_current_policies():
        damaged = book_sources.current_policy(8)
        damaged.clear()
        assert book_sources.current_policy(8) == expected
        assert Context().run(book_sources._prepared_policies.get) is None
        with book_sources.select_registry(7):
            assert book_sources.current_policy() == book_sources.current_policy(7)
        assert book_sources.current_policy() == book_sources.current_policy(5)
        for version in (True, 0, 9, '8'):
            with pytest.raises(ValueError):
                book_sources.current_policy(version)
    assert book_sources._prepared_policies.get() is None


@pytest.mark.parametrize('name',['book_sources','hdfc_install'])
def test_exact_predecessor_inspection_is_idempotent_and_unknown_bytes_still_reject(name):
    from mfdataindia.enrichment import hdfc_source_revision
    path=ROOT/f'src/mfdataindia/enrichment/{name}.py'
    previous=hdfc_source_revision.previous(path)
    assert hdfc_source_revision.previous(path,previous)==previous
    for raw in (path.read_bytes()+b'\n# unknown',previous+b'\n# unknown'):
        with pytest.raises(ValueError):
            hdfc_source_revision.previous(path,raw)
