"""Disposable PG18 proofs for the complete original row and no partial commits."""

from copy import deepcopy

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import book_store
from mfdataindia.review.policy import fingerprint
from test_uti_v2_store import prepare_v4, inputs_v2
import test_uti_store as old
from test_verified_review_store import verified_db as verified_db


@pytest.mark.postgres
def test_physical_closing_aum_rejects_invalid_provenance_and_shape_atomically(verified_db, monkeypatch):
    identities, calls, *_ = prepare_v4(verified_db, monkeypatch, canonical=True, aum_rows=True)
    valid = inputs_v2(identities, calls, canonical=True, aum_rows=True)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        before = book_store.capture(conn)
    defects = ["missing", "row_hash", "source_hash", "page", "extra", "method", "dimension_type", "region",
               "span_type", "span_extra", "duplicate", "row_y", "order", "font", "currency", "unit",
               "amount", "wrong_value", "raw_mismatch", "field_source", "field_page"]
    for index, defect in enumerate(defects):
        inputs = deepcopy(valid)
        batch = inputs[0][0]
        result = next(o['result'] for o in batch['observations'] if o['identity']['code'] == 102398)
        evidence = result['detail_aum_row_evidence']
        row, field = evidence['row'], result['fields']['aum']
        if defect == 'missing':
            result.pop('detail_aum_row_evidence')
        elif defect == 'row_hash':
            evidence['row_hash'] = 'f' * 64
        elif defect == 'source_hash':
            evidence['source_hash'] = 'f' * 64
        elif defect == 'page':
            evidence['detail_page'] += 1
        elif defect == 'extra':
            evidence['extra'] = True
        elif defect == 'method':
            row['method'] = 'nearest_number'
        elif defect == 'dimension_type':
            row['page_width'] = 594
        elif defect == 'region':
            row['boundary_x'] = '594'
        elif defect == 'span_type':
            row['spans'][0]['x'] = 19
        elif defect == 'span_extra':
            row['spans'][0]['inferred'] = True
        elif defect == 'duplicate':
            row['spans'].append(deepcopy(row['spans'][-2]))
        elif defect == 'row_y':
            row['spans'][-2]['y'] = '425'
        elif defect == 'order':
            row['spans'][-2]['x'] = '18'
        elif defect == 'font':
            row['spans'][0]['font_points'] = '17'
        elif defect == 'currency':
            row['spans'][-3]['text'] = '$'
        elif defect == 'unit':
            row['spans'][-1]['text'] = 'million'
        elif defect == 'amount':
            row['spans'][-2]['text'] = '4,09.35'
        elif defect == 'wrong_value':
            field['value'] = '4207.67'
        elif defect == 'raw_mismatch':
            field['raw_value'] = '4,207.67'
        elif defect == 'field_source':
            field['content_hash'] = 'f' * 64
        else:
            field['detail_page'] += 1
        if defect != 'row_hash':
            evidence['row_hash'] = fingerprint(row)
        batch['batch_id'] = fingerprint({k:v for k,v in batch.items() if k != 'batch_id'})
        with pytest.raises((psycopg.Error, ValueError)):
            old.publish(verified_db, identities, inputs, execution='v4-reject-'+str(index))
        with psycopg.connect(verified_db, row_factory=dict_row) as conn:
            assert book_store.capture(conn) == before
    run = old.publish(verified_db, identities, valid, execution='v4-after-all-rejections')
    assert run['status'] in ('published', 'updated', 'unchanged')


def test_all_frozen_migration_tail_and_unaccepted_attempts_are_retained():
    from test_amc_book_facts import ROOT
    from mfdataindia.enrichment import book_sources

    assert book_sources.current_policy()['version'] == 11
    assert book_sources.registry()['sources'][-1]['adapter'] == 'uti-english-monthly-book-v4'
    assert book_sources.registry()['maximum_sources_per_execution'] == 6
    old_sql = (ROOT/'sql/optional/043_uti_canonical_title_evidence.sql').read_text()
    new_sql = book_store.migration_source(version=5)
    marker = '    -- Prior source versions 1-3 remain byte-for-byte frozen below.\n'
    assert old_sql.split(marker,1)[1] == new_sql.split(marker,1)[1]
    assert new_sql.count('\nBEGIN;\n') == new_sql.count('COMMIT;') == 1
