"""Semantic projections of the existing enrolled safe tables, never SQL inputs."""
from __future__ import annotations

import base64
import json
import re

from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from mfdataindia.datasets import policy as datasets, reads as dataset_reads
from . import policy as p, schemas as s

HASH = re.compile(r'[0-9a-f]{64}')
PREFIX = '/api/fund-tags/v1'


def checked(conn, table):
    # Reuse the full direct-reader/RO01 policy, target seal and physical OID lock.
    # Once per required relation, never once per code in a batch.
    entry, source = dataset_reads._locked(conn, datasets.dataset_id('mf_tagging', table))
    expected = {'current': [('code','int4'),('dimension','text'),('document','jsonb')],
                'definitions': [('tag_id','text'),('policy_version','text'),('dimension','text'),('value','text')]}[table]
    p.require(entry['schema'] == 'mf_tagging' and entry['relation'] == table
              and entry['owner'] == p.OWNER and entry['key_source'] == 'physical_primary_key'
              and entry['key_columns'] == ({'current':['code','dimension'],'definitions':['tag_id']}[table])
              and [(c['name'],c['type']) for c in entry['columns']] == expected,
              'TAGGING_READ_MODEL_DIFFERS')
    relation = conn.execute("SELECT relkind::text AS kind FROM pg_class WHERE oid=to_regclass(%s)",
                            ('mf_tagging.'+table,)).fetchone()
    p.require(relation and relation['kind']=='r', 'TAGGING_READ_MODEL_DIFFERS')
    return source


def snapshot(conn):
    row = conn.execute('SELECT document FROM mf_tagging.current ORDER BY code,dimension LIMIT 1').fetchone()
    p.require(row is not None, 'TAGGING_NOT_PUBLISHED')
    try:
        document = s.TagAssessment.model_validate(row['document'])
    except ValidationError:
        raise p.TaggingError('TAGGING_READ_MODEL_DIFFERS') from None
    return document.snapshot_id


def unique(pairs):
    out = {}
    for key,value in pairs:
        p.require(key not in out, 'INVALID_TAG_CURSOR')
        out[key] = value
    return out


def cursor_encode(snapshot_id, query, after):
    return base64.urlsafe_b64encode(p.canonical({'format': s.FORMAT, 'snapshot': snapshot_id,
        'query': p.digest(query), 'after': after})).decode().rstrip('=')


def cursor_decode(value, snapshot_id, query, kind):
    if value is None:
        return None
    p.require(type(value) is str and 0 < len(value) <= 2048
              and re.fullmatch(r'[A-Za-z0-9_-]+',value), 'INVALID_TAG_CURSOR')
    try:
        raw = base64.urlsafe_b64decode(value+'='*(-len(value)%4))
        document = json.loads(raw,object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise p.TaggingError('INVALID_TAG_CURSOR') from None
    p.require(type(document) is dict and set(document) == {'format','snapshot','query','after'}
              and document['format'] == s.FORMAT and type(document['snapshot']) is str
              and HASH.fullmatch(document['snapshot']) and document['query'] == p.digest(query),
              'INVALID_TAG_CURSOR')
    p.require(document['snapshot'] == snapshot_id, 'TAG_SNAPSHOT_CHANGED')
    after = document['after']
    p.require((kind == 'code' and type(after) is int and 0 < after < 2**31)
              or (kind == 'tag' and type(after) is str and HASH.fullmatch(after)), 'INVALID_TAG_CURSOR')
    return after


def bounded(document):
    dataset_reads._safe_value(document)
    p.require(len(p.canonical(document)) <= datasets.MAX_RESPONSE_BYTES, 'TAG_RESPONSE_TOO_LARGE')
    return document


def discovery(conn):
    checked(conn,'current')
    snap = snapshot(conn)
    counts = conn.execute("""SELECT document->>'state' AS state,count(*) AS assessments,
        count(DISTINCT code) AS funds,min(document->>'snapshot_id') AS first_snapshot,
        max(document->>'snapshot_id') AS last_snapshot,
        bool_and(COALESCE(document->>'state'=(%s::jsonb ->> (document->>'reason'))
            AND document->>'policy_version'=%s AND document->>'dimension'=dimension
            AND document->>'code'=code::text,false)) AS valid
        FROM mf_tagging.current GROUP BY document->>'state'""",(Jsonb(s.STATE_FOR_REASON),p.VERSION)).fetchall()
    p.require(all(r['state'] in {'supported','contradicted','insufficient-evidence','not-assessed','not-applicable'}
                  and r['first_snapshot'] == r['last_snapshot'] == snap and r['valid'] is True
                  for r in counts), 'TAGGING_READ_MODEL_DIFFERS')
    funds = conn.execute('SELECT count(DISTINCT code) AS funds FROM mf_tagging.current').fetchone()['funds']
    p.require(0 < funds <= p.MAX_FUNDS and sum(r['assessments'] for r in counts) == funds*len(p.DIMENSIONS),
              'TAGGING_READ_MODEL_DIFFERS')
    return {'format':s.FORMAT,'policy_version':p.VERSION,'snapshot_id':snap,
            'dimensions':list(p.DIMENSIONS),'ethical_screening_enabled':False,'currency':'not_certified',
            'definitions_url':PREFIX+'/definitions','funds_url':PREFIX+'/funds','batch_url':PREFIX+'/batch',
            'published_funds':funds,'assessment_counts':{r['state']:r['assessments'] for r in counts}}


def definitions(conn, *, dimension=None, limit=50, cursor=None):
    p.require(dimension is None or dimension in p.DIMENSIONS, 'INVALID_TAG_QUERY')
    p.require(type(limit) is int and 1 <= limit <= 100, 'INVALID_TAG_QUERY')
    # Both relations are needed here; validate each once, retaining their locks.
    checked(conn,'definitions')
    checked(conn,'current')
    snap = snapshot(conn)
    query = {'operation':'definitions','dimension':dimension,'limit':limit}
    after = cursor_decode(cursor,snap,query,'tag')
    rows = conn.execute('''SELECT tag_id,policy_version,dimension,value FROM mf_tagging.definitions
        WHERE policy_version=%s AND (%s::text IS NULL OR dimension=%s)
        AND (%s::text IS NULL OR tag_id>%s) ORDER BY tag_id LIMIT %s''',
        (p.VERSION,dimension,dimension,after,after,limit+1)).fetchall()
    try:
        values = [s.TagDefinition.model_validate(dict(r)).model_dump() for r in rows[:limit]]
    except ValidationError:
        raise p.TaggingError('TAGGING_READ_MODEL_DIFFERS') from None
    return bounded({'format':s.FORMAT,'snapshot_id':snap,'definitions':values,'limit':limit,
        'next_cursor':cursor_encode(snap,query,values[-1]['tag_id']) if len(rows)>limit else None})


def bundle(conn, codes, snap):
    rows = conn.execute('SELECT code,dimension,document FROM mf_tagging.current WHERE code=ANY(%s) ORDER BY code,dimension', (codes,)).fetchall()
    lookup = {code:[] for code in codes}
    for row in rows:
        try:
            record = s.TagAssessment.model_validate(row['document'])
        except ValidationError:
            raise p.TaggingError('TAGGING_READ_MODEL_DIFFERS') from None
        p.require(record.code == row['code'] and record.dimension == row['dimension']
                  and record.snapshot_id == snap, 'TAGGING_READ_MODEL_DIFFERS')
        lookup[row['code']].append(record.model_dump())
    funds = []
    for code in codes:
        records = lookup[code]
        p.require(not records or (len(records) == len(p.DIMENSIONS)
                  and {r['dimension'] for r in records} == set(p.DIMENSIONS)), 'TAGGING_READ_MODEL_DIFFERS')
        funds.append({'code':code,'state':'found' if records else 'not-found','assessments':records})
    return funds


def validate_codes(codes):
    p.require(type(codes) is list and 0 < len(codes) <= 50
              and all(type(c) is int and 0 < c < 2**31 for c in codes)
              and len(set(codes)) == len(codes), 'INVALID_TAG_QUERY')


def batch(conn, codes):
    validate_codes(codes)
    checked(conn,'current')
    snap = snapshot(conn)
    return bounded({'format':s.FORMAT,'snapshot_id':snap,'funds':bundle(conn,codes,snap)})


def validate_filters(tags,match,limit):
    p.require(type(tags) in (tuple,list) and len(tags) <= 10
              and all(type(t) is str and HASH.fullmatch(t) for t in tags)
              and len(tags) == len(set(tags))
              and match in {'all','any'} and type(limit) is int and 1 <= limit <= 50,
              'INVALID_TAG_QUERY')


def funds(conn, *, tags=(), match='all', limit=20, cursor=None):
    validate_filters(tags,match,limit)
    checked(conn,'current')
    snap = snapshot(conn)
    tags = sorted(tags)
    query = {'operation':'funds','tags':tags,'match':match,'limit':limit}
    after = cursor_decode(cursor,snap,query,'code')
    having = sql.SQL('TRUE')
    parameters = [after,after]
    if tags:
        having = sql.SQL("count(DISTINCT CASE WHEN document->>'state'='supported' AND document->>'tag_id'=ANY(%s) THEN document->>'tag_id' END) {} %s").format(sql.SQL('=') if match=='all' else sql.SQL('>='))
        parameters += [tags,len(tags) if match=='all' else 1]
    parameters.append(limit+1)
    rows = conn.execute(sql.SQL('''SELECT code FROM mf_tagging.current
        WHERE (%s::int IS NULL OR code>%s) GROUP BY code HAVING {} ORDER BY code LIMIT %s''').format(having), parameters).fetchall()
    codes = [r['code'] for r in rows[:limit]]
    return bounded({'format':s.FORMAT,'snapshot_id':snap,'funds':bundle(conn,codes,snap) if codes else [],
        'next_cursor':cursor_encode(snap,query,codes[-1]) if len(rows)>limit else None,
        'limit':limit,'consistency':'snapshot_bound_fresh_repeatable_read_each_page'})
