"""Provider-free execution-shape and fail-closed tests, not native SQL proof."""
from dataclasses import FrozenInstanceError, replace

import pytest

from mfdataindia.datasets import policy as p, projections, query_profile as q
from mfdataindia.enrichment import book_sources
from mfdataindia.review.policy import fingerprint


def graph(*, v2=True):
    expressions = [expression for expression,alias,_ in q._SCALARS
                   if v2 or alias not in ('_dataset_format','_dataset_identity_content_role')]
    versions = ('SELECT * FROM mf_book_facts.prior_field_versions UNION ALL '
        'SELECT '+','.join(expression for expression in expressions)+
        (","+expressions[1] if v2 else '')+' FROM mf_book_facts.batches b '
        'WHERE NOT EXISTS(SELECT 1 FROM mf_book_facts.prior_field_versions p)')
    bodies = {'mf_live_facts.field_versions':versions,
        'mf_book_facts.prior_field_versions':'SELECT 1 AS code',
        'mf_live_facts.field_heads':'SELECT * FROM mf_live_facts.field_versions',
        'mf_live_facts.selected_fields':'SELECT * FROM mf_live_facts.field_versions',
        'mf_live_facts.current_values':'SELECT * FROM mf_live_facts.selected_fields',
        'mf_live_facts.resolved':'SELECT * FROM mf_live_facts.current_values',
        'mf.current_fund_facts':'SELECT * FROM mf_live_facts.current_values'}
    return projections.VerifiedGraph(tuple((name,i,body) for i,(name,body) in enumerate(bodies.items(),1)),
        fingerprint(book_sources.current_policy(4)))


def entry(name='mf.current_fund_facts'):
    schema, relation = name.split('.')
    return {'schema':schema,'relation':relation}


@pytest.mark.parametrize('target',sorted(q.TARGETS))
@pytest.mark.parametrize('v2',[False,True])
def test_each_sealed_target_selects_shared_profile_with_no_stored_view_change(target,v2):
    seals = graph(v2=v2)
    before = seals.views
    source = q.source(entry(target),seals)
    assert source.profile == q.VERSION
    body = source.prefix.as_string()
    assert '"prior_field_versions" AS MATERIALIZED' in body
    assert '"selected_fields" AS MATERIALIZED' in body
    assert '"current_values" AS MATERIALIZED' in body
    assert body.count('mf_book_facts.batches b') == 1
    for expression,alias,_ in q._SCALARS:
        if v2 or alias not in ('_dataset_format','_dataset_identity_content_role'):
            assert body.count(expression) == 1
    assert source.relation.as_string() == '"'+entry(target)['relation']+'"'
    assert seals.views == before
    with pytest.raises(FrozenInstanceError):
        seals.policy_hash = 'changed'


def test_generic_datasets_and_other_known_financial_profiles_do_not_select_book_sql():
    for candidate,seals in ((entry(),None),(entry(),projections.VerifiedGraph()),
                            (entry('mf.other'),graph()),
                            (entry(),replace(graph(),policy_hash='unknown'))):
        source = q.source(candidate,seals)
        assert source.profile is None and source.prefix.as_string()==''
        assert source.relation.as_string()=='"'+candidate['schema']+'"."'+candidate['relation']+'"'


@pytest.mark.parametrize('before,after',[
    ('mf_book_facts.batches b','mf_book_facts.batches changed'),
    ('mf_book_facts.prior_field_versions p','mf_book_facts.prior_field_versions_again p'),
    ("b.document ->> 'format'::text","b.document ->> 'unknown_format'::text"),
    ('SELECT * FROM mf_live_facts.selected_fields','SELECT * FROM mf_live_facts.unreviewed'),
    ('SELECT * FROM mf_live_facts.field_versions',"SELECT 'FROM mf_live_facts.field_versions' AS note"),
    ('SELECT * FROM mf_live_facts.field_versions','SELECT * FROM mf_live_facts.field_versions -- synthetic-secret-marker'),
    ('SELECT * FROM mf_live_facts.field_versions','SELECT $$FROM mf_live_facts.field_versions$$'),
    ('SELECT * FROM mf_live_facts.field_versions','SELECT 1 AS _mf_dataset_batch_inputs'),
    ('SELECT * FROM mf_live_facts.field_versions','WITH field_versions AS (SELECT 1) SELECT * FROM mf_live_facts.field_versions'),
    ('SELECT * FROM mf_live_facts.field_versions','WITH "field_versions" AS (SELECT 1) SELECT * FROM mf_live_facts.field_versions'),
    ('SELECT * FROM mf_live_facts.field_versions','SELECT 1 AS "_mf_dataset_batch_inputs"'),
    ('SELECT * FROM mf_live_facts.field_versions','WITH field_versions(x) AS (SELECT 1) SELECT * FROM mf_live_facts.field_versions'),
    ('SELECT * FROM mf_live_facts.field_versions','WITH FiElD_VeRsIoNs(x,y) as (SELECT 1,2) SELECT * FROM mf_live_facts.field_versions'),
    ('SELECT * FROM mf_live_facts.field_versions','WITH "field_versions"("x") AS MATERIALIZED (SELECT 1) SELECT * FROM mf_live_facts.field_versions'),
    ('SELECT * FROM mf_live_facts.field_versions','WITH "FIELD_VERSIONS" (x) AS NOT MATERIALIZED (SELECT 1) SELECT * FROM mf_live_facts.field_versions'),
    ('SELECT * FROM mf_live_facts.field_versions','WITH RECURSIVE other(x) AS (SELECT 1), field_versions ("x", y) AS (SELECT 1,2) SELECT * FROM mf_live_facts.field_versions'),
])
def test_unsupported_shape_is_not_a_silent_generic_fallback_or_quoted_rewrite(before,after):
    original = graph()
    changed = replace(original,views=tuple((n,i,b.replace(before,after)) for n,i,b in original.views))
    assert changed != original
    with pytest.raises(p.DatasetError) as stopped:
        q.source(entry(),changed)
    assert str(stopped.value)=='DATASET_QUERY_PROFILE_UNSUPPORTED'
    assert 'synthetic-secret-marker' not in str(stopped.value)


def test_finite_substitution_preserves_every_other_byte_and_refuses_string_context():
    body = "SELECT 'unchanged literal' FROM mf_live_facts.field_versions f\n WHERE f.code=1;"
    changed = q._reference(body,'mf_live_facts.field_versions','field_versions',count=1)
    assert changed.replace('FROM field_versions','FROM mf_live_facts.field_versions')==body
    with pytest.raises(p.DatasetError,match='DATASET_QUERY_PROFILE_UNSUPPORTED'):
        q._reference("SELECT 'FROM mf_live_facts.field_versions'",'mf_live_facts.field_versions','field_versions')


@pytest.mark.parametrize('literal',[r"'regex\s+$'",r"E'escaped\\value'",
                                   "'a doubled ''quote'''",'"quoted identifier"'])
def test_untouched_postgres_quoted_values_are_not_reinterpreted(literal):
    body = 'SELECT '+literal+' FROM mf_live_facts.field_versions f'
    changed = q._reference(body,'mf_live_facts.field_versions','field_versions',count=1)
    assert changed == 'SELECT '+literal+' FROM field_versions f'


def test_absent_retained_prior_seal_cannot_authorize_optimized_projection():
    original = graph()
    changed = replace(original,views=tuple(row for row in original.views
                                         if row[0]!='mf_book_facts.prior_field_versions'))
    with pytest.raises(p.DatasetError,match='DATASET_QUERY_PROFILE_UNSUPPORTED'):
        q.source(entry(),changed)


@pytest.mark.parametrize('literal',["'field_versions(x) AS (SELECT 1)'",
                                   r"E'escaped\\value field_versions(x) AS (SELECT 1)'",
                                   "'doubled ''quote field_versions(x) AS (SELECT 1)'"])
def test_cte_capture_scanner_never_treats_literal_contents_as_declarations(literal):
    body = 'WITH ordinary(x) AS (SELECT '+literal+') SELECT * FROM mf_live_facts.field_versions'
    q._code_starts(body)
    q._reject_captures(body,{'field_versions'})
    assert literal in q._reference(body,'mf_live_facts.field_versions','field_versions',count=1)
