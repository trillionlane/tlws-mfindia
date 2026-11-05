"""Trusted creator lifecycle hook, separate from ingestion/publication values.

Call after supported DDL/enrollment, on its existing creator connection. Every
grant is creator/object/schema scoped and uses a savepoint. A failed reader
reconciliation is observable but does not roll back unrelated ingestion. This
module never opens a connection, changes role attributes, commits or retries.
"""
from __future__ import annotations

import json

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

from . import catalogue as registry, policy as p


def enrollment_comment(*, classification, title, key_columns=None):
    value = {"format": p.VERSION, "classification": classification, "title": title}
    if key_columns is not None:
        value['key_columns'] = key_columns
    raw = registry.PREFIX + p.canonical(value).decode()
    registry.decode_enrollment(raw)
    return raw


def enroll(conn, schema, relation, *, classification, title, key_columns=None):
    """Deliberate creator metadata, not an HTTP-exposed registration method."""
    dataset = p.dataset_id(schema, relation)
    caller = conn.execute("SELECT current_user AS principal,session_user AS session").fetchone()
    p.require(caller['principal'] == caller['session'] and caller['principal'] in registry.SUPPORTED_CREATORS,
              'DATASET_ENROLLMENT_CREATOR_REQUIRED')
    p.require(conn.info.transaction_status == TransactionStatus.INTRANS,
              'DATASET_ENROLLMENT_OUTER_TRANSACTION_REQUIRED')
    target = conn.execute("""SELECT pg_get_userbyid(c.relowner) AS owner,c.relkind::text AS kind,
        obj_description(c.oid,'pg_class') AS existing_comment
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relname=%s""", (schema, relation)).fetchone()
    p.require(target and target['owner'] == caller['principal'] and target['kind'] in ('r','p','v','m'),
              'DATASET_ENROLLMENT_DIRECT_OWNER_REQUIRED')
    p.require(registry.creator_supported(schema,caller['principal']), 'DATASET_CREATOR_SCHEMA_POLICY_CONFLICT')
    # A comment is the current trusted eligibility declaration. It contains no
    # credentials, row values, SQL, role grant, public ACL or executable action.
    raw = enrollment_comment(classification=classification, title=title, key_columns=key_columns)
    existing = target['existing_comment']
    if existing:
        if registry.PREFIX in existing:
            registry.decode_enrollment(existing)  # Ambiguous markers cannot be overwritten.
            prefix = existing.split(registry.PREFIX, 1)[0]
            raw = prefix + raw
        else:
            raw = existing + '\n' + raw
    if raw == existing:
        return {'dataset_id': dataset, 'registered': True, 'consumer_ready': False,
                'reason': 'PERMISSIONS_AND_API_VERIFICATION_PENDING', 'metadata_writes': 0}
    conn.execute(sql.SQL('COMMENT ON {} {}.{} IS {}').format(
        sql.SQL('MATERIALIZED VIEW' if target['kind'] == 'm' else 'VIEW' if target['kind'] == 'v' else 'TABLE'),
        sql.Identifier(schema), sql.Identifier(relation), sql.Literal(raw)))
    return {'dataset_id': dataset, 'registered': True, 'consumer_ready': False,
            'reason': 'PERMISSIONS_AND_API_VERIFICATION_PENDING', 'metadata_writes': 1}


def reconcile(conn):
    caller = conn.execute("SELECT current_user AS principal,session_user AS session").fetchone()
    p.require(caller['principal'] == caller['session'] and caller['principal'] in registry.SUPPORTED_CREATORS,
              'DATASET_ENROLLMENT_CREATOR_REQUIRED')
    p.require(conn.info.transaction_status == TransactionStatus.INTRANS,
              'DATASET_ENROLLMENT_OUTER_TRANSACTION_REQUIRED')
    actor = caller['principal']
    report = {'format': p.VERSION, 'creator': actor, 'results': [], 'consumer_ready': False,
              'commit_performed': False, 'unrelated_ingestion_values_untouched': True}
    # Catalogue privilege fields must be evaluated for the fixed reader, not
    # mistaken for the current creator's legitimately stronger capabilities.
    query = registry.CATALOGUE_SQL.replace('has_schema_privilege(current_user,',
        "has_schema_privilege('mfdata_verified_reader',").replace('has_table_privilege(current_user,',
        "has_table_privilege('mfdata_verified_reader',").replace('has_any_column_privilege(current_user,',
        "has_any_column_privilege('mfdata_verified_reader',")
    rows = registry.with_dependencies(conn,conn.execute(query).fetchall())
    for row in rows:
        dataset = p.dataset_id(row['schema'], row['relation'])
        result = {'dataset_id': dataset, 'permissions_verified': False, 'consumer_ready': False,
                  'grant_statements': 0, 'reason': None}
        try:
            enrollment = registry.decode_enrollment(row['enrollment'])
            if (enrollment['classification'] == 'internal_control'
                    or (row['schema'], row['relation']) in registry.INTERNAL_CONTROL_RELATIONS):
                result['reason'] = 'EXCLUDED_INTERNAL_CONTROL'
            elif (row['schema'], row['relation']) in registry.HELD_FOR_REVIEW:
                result['reason'] = 'DATASET_ARCHITECTURAL_REVIEW_REQUIRED'
            elif row['owner'] not in registry.SUPPORTED_CREATORS:
                result['reason'] = 'UNSUPPORTED_DATASET_CREATOR'
            elif not registry.creator_supported(row['schema'],row['owner']):
                result['reason'] = 'DATASET_CREATOR_SCHEMA_POLICY_CONFLICT'
            elif row['owner'] != actor:
                result['reason'] = 'RECONCILE_WITH_DIRECT_CREATOR'
            else:
                with conn.transaction():
                    # Reject unsafe grants/types/keys BEFORE issuing anything.
                    expected = {**row, 'schema_usage': True, 'can_select': True}
                    registry.checked(expected)
                    if not row['schema_usage']:
                        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(
                            sql.Identifier(row['schema']), sql.Identifier(p.READER)))
                        result['grant_statements'] += 1
                    if not row['can_select']:
                        conn.execute(sql.SQL('GRANT SELECT ON TABLE {}.{} TO {}').format(
                            sql.Identifier(row['schema']), sql.Identifier(row['relation']), sql.Identifier(p.READER)))
                        result['grant_statements'] += 1
                    # Already compliant rows require no redundant rescan. A
                    # changed row is rechecked through its indexed physical OID,
                    # never by scanning the entire catalogue once per dataset.
                    current = row
                    if result['grant_statements']:
                        point = query.replace('ORDER BY n.nspname,c.relname',
                            'AND c.oid=%s ORDER BY n.nspname,c.relname')
                        current = conn.execute(point, (row['oid'],)).fetchone()
                        p.require(current is not None, 'DATASET_RELATION_CHANGED')
                        current['unsafe_dependencies'] = row['unsafe_dependencies']
                    registry.checked(current)
                    result['permissions_verified'] = True
                    result['reason'] = 'API_VERIFICATION_PENDING'
        except p.DatasetError as error:
            result.update(grant_statements=0, reason=str(error))
        except (psycopg.Error, StopIteration):
            result.update(grant_statements=0, reason='DATASET_PERMISSION_RECONCILIATION_FAILED')
        report['results'].append(result)
    return report


def after_migrations(conn):
    """Existing migration command extension; a savepoint contains failures."""
    try:
        p.require(conn.info.transaction_status == TransactionStatus.INTRANS,
                  'DATASET_ENROLLMENT_OUTER_TRANSACTION_REQUIRED')
        with conn.transaction():
            return reconcile(conn)
    except p.DatasetError as error:
        return {'format': p.VERSION, 'results': [], 'consumer_ready': False,
                'reason': str(error), 'unrelated_ingestion_values_untouched': True,
                'commit_performed': False}
    except psycopg.Error:
        return {'format': p.VERSION, 'results': [], 'consumer_ready': False,
                'reason': 'DATASET_RECONCILIATION_UNAVAILABLE',
                'unrelated_ingestion_values_untouched': True, 'commit_performed': False}


def emit_report(report):
    # This report contains stable IDs and allowlisted reasons, not DSNs/SQL,
    # row values or arbitrary server exception text.
    return json.dumps(report, sort_keys=True)
