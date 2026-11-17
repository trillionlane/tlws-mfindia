import pytest

from mfdataindia.nse import contract, sch44_admin


def test_unpinned_review_input_is_not_opened_or_exported():
    with pytest.raises(contract.ImportError, match='PINNED_ADMIN_INPUT_DIFFERS'):
        sch44_admin.export(b'custom-source', b'custom-catalogue', b'custom-queue')


def test_collateral_requires_the_verified_comparison_and_membership(monkeypatch):
    source, catalogue, queue = b'source', b'catalogue', b'queue'
    for field, payload in zip(('SOURCE_SHA', 'CATALOGUE_SHA', 'QUEUE_SHA'),
                              (source, catalogue, queue), strict=True):
        monkeypatch.setattr(sch44_admin, field, sch44_admin._digest(payload))
    monkeypatch.setattr(sch44_admin.sch44, 'parse', lambda _: [])
    monkeypatch.setattr(sch44_admin.sch44_reconcile, '_catalogue', lambda _: ({}, {}))
    monkeypatch.setattr(sch44_admin.sch44, '_json',
                        lambda *_: {'coverage': sch44_admin.COVERAGE, 'rows': [None] * 1883})
    with pytest.raises(contract.ImportError, match='COLLATERAL_COMPARISON_HASH_DIFFERS'):
        sch44_admin.export(source, catalogue, queue, (b'comparison', b'guessed-membership'))
