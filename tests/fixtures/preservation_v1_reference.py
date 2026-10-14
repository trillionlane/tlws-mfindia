# Historical read-only reference at bc524956f717dd8927c52b44eea2db4b51cc19de.
# Test-only: preserves old per-relation/named-cursor behavior for full-manifest equivalence.
"""Streaming preservation manifests from a single PostgreSQL snapshot.

No rows, credentials or source bytes are published in evidence. The only allowed
source-ledger addition is the exact opt-in migration; existing entries stay sealed.
"""
from __future__ import annotations

import hashlib
import re
from uuid import uuid4

from psycopg import sql

from mfdataindia.review.policy import fingerprint

MIGRATION = '023_verified_fund_review.sql'


def canonical_constraint(definition):
    """Normalize only associative AND/OR grouping rewritten by PG18 restore.

    Quoted SQL values stay opaque. CASE/BETWEEN/subqueries are conservative raw
    text, not guessed equivalences. No regex stripping of arbitrary parentheses.
    """
    if not definition.startswith('CHECK '):
        return definition
    tokens = re.findall(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"]|\"\")*\"|[A-Za-z_][A-Za-z_0-9$]*|[0-9]+(?:\.[0-9]+)?|::|>=|<=|<>|!=|[^\s]", definition)
    if any(t.upper() in ('CASE', 'BETWEEN', 'SELECT') for t in tokens):
        return definition
    stack = [[]]
    for token in tokens:
        if token == '(':
            stack.append([])
        elif token == ')':
            if len(stack) == 1:
                raise ValueError('unbalanced constraint definition')
            stack[-2].append(stack.pop())
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise ValueError('unbalanced constraint definition')
    def normalize(items):
        if len(items) == 1 and isinstance(items[0], list):
            return normalize(items[0])
        if ',' in [item for item in items if isinstance(item, str)]:
            return ('SQL', [normalize(item) if isinstance(item, list) else item for item in items])
        for operator in ('OR', 'AND'):
            parts, current = [], []
            for item in items:
                if isinstance(item, str) and item.upper() == operator:
                    parts.append(current)
                    current = []
                else:
                    current.append(item)
            if parts:
                parts.append(current)
                normalized = []
                for part in parts:
                    node = normalize(part)
                    normalized.extend(node[1] if node[0] == operator else [node])
                return (operator, normalized)
        return ('SQL', [normalize(item) if isinstance(item, list) else item for item in items])
    return normalize(stack[0])


def capture_preservation(conn) -> dict:
    if conn.execute('SHOW transaction_isolation').fetchone()['transaction_isolation'] != 'repeatable read':
        raise ValueError('preservation capture requires a consistent repeatable-read snapshot')
    conn.execute("SET LOCAL TimeZone='UTC'")
    conn.execute("SET LOCAL DateStyle='ISO, YMD'")
    conn.execute("SET LOCAL IntervalStyle='postgres'")
    conn.execute('SET LOCAL search_path=pg_catalog')
    if conn.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN('mf','mf_review','pg_catalog','information_schema') "
                    "AND n.nspname NOT LIKE 'pg_%' AND c.relkind IN('r','p','m') AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=session_user) LIMIT 1").fetchone():
        raise ValueError('unmodelled administrator-owned source relation outside mf; expand preservation inventory first')
    relations = conn.execute(
        "SELECT c.oid,c.relname,c.relkind,c.relispartition FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mf' AND c.relkind IN('r','p','v','m','S') ORDER BY c.relname COLLATE \"C\""
    ).fetchall()
    if not relations or not {'funds', 'nav_history', 'schema_migrations'} <= {r['relname'] for r in relations}:
        raise ValueError('source relation inventory is incomplete')
    tables, structure, sequences = {}, {}, {}
    for relation in relations:
        name = relation['relname']
        columns = conn.execute(
            "SELECT attname,format_type(atttypid,atttypmod) AS type,attnotnull,attgenerated,attidentity,"
            "pg_get_expr(d.adbin,d.adrelid) AS default_expression FROM pg_attribute a "
            "LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
            "WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum", (relation['oid'],)
        ).fetchall()
        constraints = conn.execute("SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=%s ORDER BY conname", (relation['oid'],)).fetchall()
        constraints = [{**c, 'definition': canonical_constraint(c['definition'])} for c in constraints]
        indexes = conn.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='mf' AND tablename=%s ORDER BY indexname", (name,)).fetchall()
        definition = conn.execute('SELECT pg_get_viewdef(%s::oid,true) AS definition', (relation['oid'],)).fetchone()['definition'] if relation['relkind'] in ('v', 'm') else None
        structure[name] = {'kind': relation['relkind'], 'partition': relation['relispartition'],
                           'columns': columns, 'constraints': constraints, 'indexes': indexes, 'view': definition}
        if relation['relkind'] == 'S':
            sequences[name] = conn.execute(sql.SQL('SELECT last_value,is_called FROM mf.{}').format(sql.Identifier(name))).fetchone()
        # Parent tables cover their partitions once. Views contain no independent
        # stored records; materialized views do and must not be silently omitted.
        if relation['relispartition'] or relation['relkind'] not in ('r', 'p', 'm'):
            continue
        primary = conn.execute(
            "SELECT a.attname,a.attcollation FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,position) "
            "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
            "WHERE i.indrelid=%s AND i.indisprimary ORDER BY k.position", (relation['oid'],)
        ).fetchall()
        ordering = sql.SQL(',').join(sql.SQL('{} COLLATE "C"').format(sql.Identifier(r['attname'])) if r['attcollation'] else sql.Identifier(r['attname'])
                                     for r in primary) if primary else sql.SQL('to_jsonb(t)::text COLLATE "C"')
        condition = sql.SQL(' WHERE migration_name <> {}').format(sql.Literal(MIGRATION)) if name == 'schema_migrations' else sql.SQL('')
        query = sql.SQL('SELECT to_jsonb(t)::text AS value FROM mf.{} t{} ORDER BY {}').format(sql.Identifier(name), condition, ordering)
        digest, count = hashlib.sha256(), 0
        with conn.cursor(name='preservation_' + uuid4().hex) as cursor:
            cursor.itersize = 1000
            cursor.execute(query)
            for row in cursor:
                encoded = row['value'].encode('utf-8')
                digest.update(len(encoded).to_bytes(8, 'big'))
                digest.update(encoded)
                count += 1
        tables[name] = {'rows': count, 'sha256': digest.hexdigest()}
    functions = conn.execute(
        "SELECT p.oid::regprocedure::text AS name,pg_get_functiondef(p.oid) AS definition FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf' AND p.prokind IN('f','p') ORDER BY p.oid::regprocedure::text COLLATE \"C\""
    ).fetchall()
    triggers = conn.execute(
        "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"
    ).fetchall()
    migration = conn.execute('SELECT to_jsonb(t)::text AS value FROM mf.schema_migrations t WHERE migration_name=%s', (MIGRATION,)).fetchone()
    # New dedicated roles may acquire declared reads. Every other explicit grant
    # must remain unchanged, including column ACLs and PUBLIC privileges.
    privileges = conn.execute("""
        WITH acl AS (
          SELECT 'relation:'||c.relname AS object,COALESCE(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,c.relowner)) AS value,c.relowner AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf'
          UNION ALL SELECT 'column:'||c.relname||':'||a.attname,a.attacl,c.relowner FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf' AND a.attnum>0 AND NOT a.attisdropped
          UNION ALL SELECT 'function:'||p.oid::regprocedure::text,COALESCE(p.proacl,acldefault('f'::"char",p.proowner)),p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf'
          UNION ALL SELECT 'schema:'||nspname,COALESCE(nspacl,acldefault('n'::"char",nspowner)),nspowner FROM pg_namespace WHERE nspname IN('mf','public')
        ) SELECT object,COALESCE(r.rolname,'PUBLIC') AS grantee,g.rolname AS grantor,x.privilege_type,x.is_grantable
          FROM acl CROSS JOIN LATERAL aclexplode(value) x LEFT JOIN pg_roles r ON r.oid=x.grantee
          JOIN pg_roles g ON g.oid=x.grantor WHERE COALESCE(r.rolname,'PUBLIC') NOT IN
            ('mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin')
          AND x.grantee<>acl.owner
          ORDER BY object,grantee,grantor,x.privilege_type,x.is_grantable
    """).fetchall()
    # Owners always retain implicit rights (acldefault('S') only spells USAGE,
    # while REVOKE can materialize owner SELECT/UPDATE). Normalize those redundant
    # ACL entries, but bind ownership itself independently so it cannot drift.
    ownership = conn.execute("""
        SELECT 'relation:'||c.relname AS object,pg_get_userbyid(c.relowner) AS owner
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf'
        UNION ALL SELECT 'function:'||p.oid::regprocedure::text,pg_get_userbyid(p.proowner)
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf'
        UNION ALL SELECT 'schema:'||nspname,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname IN('mf','public')
        ORDER BY object
    """).fetchall()
    return {'format': 'mfdata-preservation-v1', 'tables': tables, 'structure_hash': fingerprint(structure),
            'functions_hash': fingerprint(functions), 'triggers_hash': fingerprint(triggers),
            'sequences_hash': fingerprint(sequences),
            'ownership_hash': fingerprint(ownership),
            'privileges_hash': fingerprint(privileges),
            'optional_migration': migration['value'] if migration else None}


def compare_preservation(before: dict, after: dict, migration_checksum: str) -> None:
    import json
    if before.get('format') != 'mfdata-preservation-v1' or after.get('format') != before['format']:
        raise ValueError('invalid preservation evidence')
    for key in ('tables', 'structure_hash', 'functions_hash', 'triggers_hash', 'privileges_hash', 'sequences_hash', 'ownership_hash'):
        if not before.get(key) or before[key] != after.get(key):
            raise ValueError('pre-existing source data or structure changed: ' + key)
    previous, current = before['optional_migration'], after['optional_migration']
    if previous is not None:
        if current != previous:
            raise ValueError('existing optional migration entry changed')
    elif current is not None:
        entry = json.loads(current)
        if entry.get('migration_name') != MIGRATION or entry.get('content_sha256') != migration_checksum:
            raise ValueError('unexpected optional migration entry')
