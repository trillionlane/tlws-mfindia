"""Bounded dataset projections; no arbitrary SQL or write-capable operations."""
from __future__ import annotations

import base64
import re
from urllib.parse import urlencode

from psycopg import sql

from . import catalogue as registry, policy as p


def _safe_value(value):
    if isinstance(value, dict):
        p.require(not any(registry.is_credential_field(k) for k in value),
                  "SENSITIVE_DATASET_VALUE")
        for child in value.values():
            _safe_value(child)
    elif isinstance(value, list):
        for child in value:
            _safe_value(child)
    elif isinstance(value, str):
        p.require(not re.search(r'(?i)(?:postgres(?:ql)?://|-----BEGIN[^\n]*PRIVATE KEY|\b(?:password|passwd|access_token|refresh_token|dsn)\s*[:=])', value),
                  'SENSITIVE_DATASET_VALUE')


def projection(entry):
    atoms = []
    for column in entry["columns"]:
        name = sql.Identifier(column["name"])
        if column["type"] == "bytea":
            atom = sql.SQL("CASE WHEN {} IS NULL THEN NULL ELSE jsonb_build_object('kind','binary','bytes',octet_length({}),'sha256',encode(sha256({}),'hex')) END").format(name, name, name)
        elif column["type"] in {"numeric", "int8", "float4", "float8", "date", "timestamp", "timestamptz", "uuid"}:
            atom = sql.SQL("to_jsonb({}::text)").format(name)
        else:
            atom = sql.SQL("to_jsonb({})").format(name)
        atoms.extend((sql.Literal(column["name"]), atom))
    return sql.SQL("jsonb_build_object({})").format(sql.SQL(',').join(atoms))


def _locked(conn, dataset):
    row = registry.locate(conn, dataset)
    entry, _ = registry.checked(row)
    # Keep the observed physical relation stable until this read transaction
    # ends. A same-name replacement is not accepted as the approved object.
    conn.execute(sql.SQL("SELECT 1 FROM {}.{} LIMIT 0").format(
        sql.Identifier(entry["schema"]), sql.Identifier(entry["relation"])))
    actual = conn.execute("SELECT to_regclass(%s)::oid::bigint AS oid",
        (sql.Identifier(entry["schema"], entry["relation"]).as_string(conn),)).fetchone()
    p.require(actual["oid"] == row["oid"], "DATASET_RELATION_CHANGED")
    return entry


def rows(conn, dataset, *, limit=50, cursor=None):
    p.require(type(limit) is int and 1 <= limit <= p.MAX_PAGE, "INVALID_DATASET_LIMIT")
    entry = _locked(conn, dataset)
    after = p.decode_cursor(cursor, entry) if cursor is not None else None
    keys = entry["key_columns"]
    relation = sql.Identifier(entry["schema"], entry["relation"])
    parameters = []
    if keys:
        columns = {c["name"]: c for c in entry["columns"]}
        names = sql.SQL(',').join(sql.Identifier(k) for k in keys)
        predicate = sql.SQL('')
        if after:
            if not any(columns[k]['nullable'] for k in keys):
                typed = sql.SQL(',').join(sql.SQL('%s::{}').format(sql.Identifier('pg_catalog', columns[k]['type'])) for k in keys)
                predicate = sql.SQL(' WHERE ROW({}) > ROW({})').format(names, typed)
                parameters.extend(after)
            else:
                # Explicit lexicographic NULLS LAST ordering for a declared
                # view key. NULLs cannot be silently lost at a page boundary.
                terms = []
                for position,key in enumerate(keys):
                    equal = []
                    for previous,value in zip(keys[:position],after[:position]):
                        equal.append(sql.SQL('{} IS NOT DISTINCT FROM %s::{}').format(
                            sql.Identifier(previous),sql.Identifier('pg_catalog',columns[previous]['type'])))
                        parameters.append(value)
                    if after[position] is None:
                        greater = sql.SQL('FALSE')
                    else:
                        greater = sql.SQL('({} > %s::{} OR {} IS NULL)').format(sql.Identifier(key),
                            sql.Identifier('pg_catalog',columns[key]['type']),sql.Identifier(key))
                        parameters.append(after[position])
                    terms.append(sql.SQL('({})').format(sql.SQL(' AND ').join([*equal,greater])))
                predicate = sql.SQL(' WHERE {}').format(sql.SQL(' OR ').join(terms))
        key_document = sql.SQL('jsonb_build_array({})').format(sql.SQL(',').join(
            sql.SQL('{}::text').format(sql.Identifier(k)) for k in keys))
        query = sql.SQL('SELECT {} AS document,{} AS keys FROM {}{} ORDER BY {} LIMIT %s').format(
            projection(entry), key_document, relation, predicate, names)
        parameters.append(limit + 1)
        order = 'keyset'
    else:
        # Views/tables without a declared physical unique key use an explicit
        # canonical offset. This is NOT a retained cross-page snapshot or a
        # claim of immutable/unique view identities. No user-selected sort SQL.
        offset = int(after[0]) if after else 0
        query = sql.SQL('SELECT document FROM (SELECT {} AS document FROM {}) d ORDER BY document::text COLLATE "C" LIMIT %s OFFSET %s').format(projection(entry), relation)
        parameters.extend((limit + 1, offset))
        order = 'canonical_offset'
    records = conn.execute(query, parameters).fetchall()
    if keys and entry['key_source']=='declared_view_key':
        # Check the extra look-ahead row too: a duplicate straddling the limit
        # must stop before emitting a cursor that would skip its second row.
        observed = [p.canonical(r['keys']) for r in records]
        p.require(len(observed)==len(set(observed)), 'NONUNIQUE_DATASET_PAGINATION_KEY')
    page = []
    documents = []
    remaining = p.MAX_RESPONSE_BYTES - 16384  # Full envelope/cursor headroom.
    for record in records[:limit]:
        document = record['document']
        _safe_value(document)
        for column in entry['columns']:
            binary = document[column['name']]
            if column['type'] != 'bytea' or binary is None:
                continue
            if not keys:
                raise p.DatasetError('BINARY_DATASET_KEY_REQUIRED')
            selector = p.encode_cursor(entry, record['keys'])
            binary['content_url'] = registry.paths(dataset)['metadata_url'] + '/content/' + column['name'] + '?' + urlencode({'row': selector})
        size = len(p.canonical(document)) + 1
        if size > remaining and documents:
            break  # Ordinary pagination, not truncated columns or lost rows.
        p.require(size <= remaining, 'DATASET_ROW_TOO_LARGE')
        remaining -= size
        page.append(record)
        documents.append(document)
    next_cursor = None
    if len(records) > len(page):
        next_keys = page[-1]['keys'] if keys else [str((int(after[0]) if after else 0) + len(page))]
        next_cursor = p.encode_cursor(entry, next_keys)
    result = {'format': p.VERSION, 'dataset_id': dataset, 'schema_version': p.fingerprint(entry),
              'state': 'available' if documents else 'empty', 'reason': None,
              'rows': documents, 'next_cursor': next_cursor, 'limit': limit,
              'ordering': order, 'consistency': 'fresh_repeatable_read_each_page'}
    p.require(len(p.canonical(result)) <= p.MAX_RESPONSE_BYTES, 'DATASET_RESPONSE_TOO_LARGE')
    return result


def content(conn, dataset, column, *, row, offset=0, length=p.MAX_BINARY_CHUNK):
    p.require(type(offset) is int and 0 <= offset < 2**31 and type(length) is int
              and 1 <= length <= p.MAX_BINARY_CHUNK, 'INVALID_CONTENT_RANGE')
    entry = _locked(conn, dataset)
    columns = {c['name']: c for c in entry['columns']}
    p.require(column in columns and columns[column]['type'] == 'bytea', 'DATASET_NOT_FOUND')
    keys = entry['key_columns']
    p.require(bool(keys), 'BINARY_DATASET_KEY_REQUIRED')
    values = p.decode_cursor(row, entry)
    predicates = sql.SQL(' AND ').join(sql.SQL('{} IS NOT DISTINCT FROM %s::{}').format(
        sql.Identifier(k), sql.Identifier('pg_catalog', columns[k]['type'])) for k in keys)
    name = sql.Identifier(column)
    query = sql.SQL('SELECT octet_length({}) AS bytes,encode(sha256({}),\'hex\') AS sha256,substring({} FROM %s FOR %s) AS chunk FROM {} WHERE {} LIMIT 2').format(
        name, name, name, sql.Identifier(entry['schema'], entry['relation']), predicates)
    records = conn.execute(query, [offset + 1, length, *values]).fetchall()
    p.require(len(records)<=1, 'NONUNIQUE_DATASET_PAGINATION_KEY')
    result = records[0] if records else None
    p.require(result is not None and result['bytes'] is not None, 'DATASET_NOT_FOUND')
    p.require(offset <= result['bytes'], 'INVALID_CONTENT_RANGE')
    raw = bytes(result['chunk'])
    next_offset = offset + len(raw)
    return {'format': p.VERSION, 'dataset_id': dataset, 'column': column,
            'schema_version': p.fingerprint(entry), 'bytes': result['bytes'],
            'sha256': result['sha256'], 'offset': offset, 'encoding': 'base64',
            'content': base64.b64encode(raw).decode(),
            'next_offset': next_offset if next_offset < result['bytes'] else None}
