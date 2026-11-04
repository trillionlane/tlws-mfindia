"""Fresh, read-only consumer registry derived from trusted enrollment comments.

Database privileges alone never register a dataset. A direct supported creator
must deliberately enroll it; only that object's real metadata supplies SQL
identifiers. No comment can supply SQL, a function, credentials or a target role.
"""
from __future__ import annotations

import json

from psycopg import sql
import psycopg

from . import policy as p
from mfdataindia.review import reader_access_profile as profiles

PREFIX = "mfdata-consumer-dataset-v1:"
SUPPORTED_CREATORS = frozenset({p.OWNER, "mfdata_ingest"})


def creator_supported(schema, owner):
    # Do not break the accepted RO01 direct-owner guard in its existing sixteen
    # schemas. A second creator is supported for its additional enrolled
    # research schema, not by silently weakening the installed legacy policy.
    return owner in SUPPORTED_CREATORS and (schema not in p.CURRENT_SCHEMAS or owner==p.OWNER)
SENSITIVE_NAMES = frozenset({"password", "passwd", "dsn", "connection_string", "private_key",
                             "access_token", "refresh_token", "credential", "credentials",
                             "secret", "secret_value", "secret_reference", "secret_ref"})
INTERNAL_CONTROL_RELATIONS = frozenset({
    ('mf', 'schema_migrations'), ('mf', 'ingest_checkpoints'),
    ('mf', 'association_tag_idempotency'),
    ('mf', 'nav_remediation_snapshots'), ('mf', 'nav_remediation_decisions'),
    ('mf', 'nav_remediation_proposals'), ('mf', 'nav_correction_approvals'),
    ('mf', 'nav_remediation_operations'), ('mf', 'nav_correction_heads'),
    ('mf', 'nav_correction_commit_checks'), ('mf', 'nav_ingestion_conflicts'),
    ('mf_review', 'events'), ('mf_review', 'certifications'),
    ('mf_analytics', 'calendar_certifications'), ('mf_rolling', 'rolling_certifications'),
    ('mf_live_facts', 'configuration'), ('mf_fact_review', 'events'),
    ('mf_fact_refresh', 'runs'), ('mf_fact_refresh', 'transitions'),
    ('mf_multi_facts', 'configuration'), ('mf_multi_facts', 'runs'), ('mf_multi_facts', 'transitions'),
    ('mf_book_facts', 'configuration'), ('mf_book_facts', 'runs'), ('mf_book_facts', 'transitions'),
})
HELD_FOR_REVIEW = {('mf','source_metadata'):
    'Raw endpoint/request_url/error/notes may contain transport or credential-reference material; no blind consumer exposure. Resolve exclusion or a reviewed sanitized projection.'}

CATALOGUE_SQL = """
SELECT c.oid::bigint AS oid,n.nspname AS schema,c.relname AS relation,
       c.relkind::text AS kind,pg_get_userbyid(c.relowner) AS owner,
       pg_get_userbyid(n.nspowner) AS schema_owner,
       c.relrowsecurity OR c.relforcerowsecurity AS row_security,
       obj_description(c.oid,'pg_class') AS enrollment,
       has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage,
       has_schema_privilege(current_user,n.oid,'CREATE') AS schema_create,
       has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
       (has_table_privilege(current_user,c.oid,'SELECT WITH GRANT OPTION')
         OR has_any_column_privilege(current_user,c.oid,'SELECT WITH GRANT OPTION')) AS grant_option,
       (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
         OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')) AS mutation,
       (EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
               WHERE x.grantee=0 AND x.privilege_type='SELECT')
        OR EXISTS(SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                  WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND x.grantee=0 AND x.privilege_type='SELECT')) AS public_read,
       COALESCE((SELECT jsonb_agg(jsonb_build_object(
           'name',a.attname,'type',t.typname,'type_schema',tn.nspname,
           'nullable',NOT a.attnotnull,'position',a.attnum)
           ORDER BY a.attnum) FROM pg_attribute a
           JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace tn ON tn.oid=t.typnamespace
           WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),'[]'::jsonb) AS columns,
       COALESCE((SELECT jsonb_agg(a.attname ORDER BY k.ordinality)
           FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ordinality)
           JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
           WHERE i.indrelid=c.oid AND i.indisprimary AND i.indisvalid
             AND k.ordinality<=i.indnkeyatts),'[]'::jsonb) AS primary_key
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE (n.nspname='mf' OR n.nspname LIKE 'mf\\_%%' ESCAPE '\\')
  AND c.relkind IN ('r','p','v','m')
  AND strpos(obj_description(c.oid,'pg_class'),'mfdata-consumer-dataset-v1:')>0
ORDER BY n.nspname,c.relname
"""

DEPENDENCIES_SQL = """
WITH RECURSIVE edges(parent,child) AS (
 SELECT w.ev_class,d.refobjid FROM pg_rewrite w JOIN pg_depend d
 ON d.classid='pg_rewrite'::regclass AND d.objid=w.oid
 AND d.refclassid='pg_class'::regclass
 UNION SELECT inhparent,inhrelid FROM pg_inherits
), reach(root,oid) AS (
 SELECT x,x FROM unnest(%s::oid[]) x
 UNION SELECT r.root,e.child FROM reach r JOIN edges e ON e.parent=r.oid
), functions(root,oid) AS (
 SELECT r.root,d.refobjid FROM reach r JOIN pg_rewrite w ON w.ev_class=r.oid
 JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=w.oid
 AND d.refclassid='pg_proc'::regclass
 UNION
 SELECT r.root,d.refobjid FROM reach r JOIN pg_attribute a ON a.attrelid=r.oid
 AND a.attgenerated='v' JOIN pg_attrdef def ON def.adrelid=a.attrelid AND def.adnum=a.attnum
 JOIN pg_depend d ON d.classid='pg_attrdef'::regclass AND d.objid=def.oid
 AND d.refclassid='pg_proc'::regclass
)
SELECT DISTINCT root::bigint FROM (
 SELECT r.root FROM reach r JOIN pg_class c ON c.oid=r.oid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind NOT IN ('r','p','v','m') OR c.relrowsecurity OR c.relforcerowsecurity
 OR pg_get_userbyid(c.relowner)<>ALL(%s)
 OR NOT(n.nspname='mf' OR n.nspname LIKE 'mf\\_%%' ESCAPE '\\')
 UNION
 SELECT f.root FROM functions f JOIN pg_proc p ON p.oid=f.oid
 JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname<>'pg_catalog' OR p.prosecdef OR p.provolatile='v'
 OR p.proname IN ('current_setting','query_to_xml','database_to_xml','schema_to_xml')
) unsafe
"""


def decode_enrollment(raw):
    p.require(type(raw) is str and len(raw.encode()) <= 65536 and raw.count(PREFIX) == 1,
              "INVALID_DATASET_ENROLLMENT")
    p.require(raw.startswith(PREFIX) or '\n' + PREFIX in raw, 'INVALID_DATASET_ENROLLMENT')
    data = raw.split(PREFIX, 1)[1]
    p.require(len(data.encode()) <= 8192, 'INVALID_DATASET_ENROLLMENT')
    try:
        value = json.loads(data, object_pairs_hook=p._unique)
    except ValueError:
        raise p.DatasetError("INVALID_DATASET_ENROLLMENT") from None
    p.require(type(value) is dict and {'format','classification','title'} <= set(value)
              and set(value) <= {'format','classification','title','key_columns'}
              and value["format"] == p.VERSION and type(value["classification"]) is str
              and value["classification"] in p.CLASSIFICATIONS
              and type(value["title"]) is str and 0 < len(value["title"]) <= 160
              and all(ord(c) >= 32 for c in value["title"]), "INVALID_DATASET_ENROLLMENT")
    hint = value.get('key_columns',[])
    p.require(type(hint) is list and len(hint)<=8
              and all(type(k) is str and p.IDENTIFIER.fullmatch(k) for k in hint)
              and len(hint)==len(set(hint)), 'INVALID_DATASET_KEY')
    return value


def verify_reader(conn):
    row = conn.execute("""SELECT current_database() AS database,current_user AS principal,
        session_user AS session,r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,
        r.rolreplication,r.rolbypassrls,
        EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS membership,
        has_database_privilege(current_user,current_database(),'CREATE') AS database_create,
        current_setting('transaction_read_only') AS readonly,
        current_setting('transaction_isolation') AS isolation
        FROM pg_roles r WHERE r.rolname=current_user""").fetchone()
    p.require(row is not None and row["database"] == "mfdataindia"
              and row["principal"] == row["session"] == p.READER
              and not any(row[k] for k in ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb",
                  "rolreplication", "rolbypassrls", "membership", "database_create")),
              "DATASET_READER_IDENTITY_UNSAFE")
    p.require(row['readonly'] == 'on' and row['isolation'] == 'repeatable read',
              'DATASET_READONLY_TRANSACTION_REQUIRED')
    # Retain the installed RO01 profile's full current-domain/helper checks.
    # New eligible schemas add target/dependency checks below, not a replacement
    # interpretation of existing raw-source permissions. Exactly once per API
    # request; never once per item or nested source guard.
    try:
        profiles.verify_selection(conn,profiles.BROAD)
    except profiles.ReaderAccessError:
        raise p.DatasetError('DATASET_INSTALLED_READER_PROFILE_UNSAFE') from None


def checked(row):
    enrollment = decode_enrollment(row["enrollment"])
    p.require((row['schema'], row['relation']) not in INTERNAL_CONTROL_RELATIONS,
              'EXCLUDED_INTERNAL_CONTROL')
    p.require((row['schema'], row['relation']) not in HELD_FOR_REVIEW,
              'DATASET_ARCHITECTURAL_REVIEW_REQUIRED')
    p.require(row["owner"] in SUPPORTED_CREATORS, "UNSUPPORTED_DATASET_CREATOR")
    p.require(creator_supported(row['schema'],row['owner']), 'DATASET_CREATOR_SCHEMA_POLICY_CONFLICT')
    p.require(row['schema_owner'] in SUPPORTED_CREATORS, 'UNSUPPORTED_DATASET_SCHEMA_OWNER')
    p.require(row["kind"] in ("r", "p", "v", "m") and not row["row_security"],
              "UNSUPPORTED_DATASET_RELATION")
    p.require(not row.get('unsafe_dependencies', False), 'UNREVIEWED_DATASET_DEPENDENCY')
    p.require(all(c["type_schema"] == "pg_catalog" and c["type"] in p.TYPES
                  for c in row["columns"]), "UNSUPPORTED_DATASET_TYPE")
    p.require(not any(c["name"].lower() in SENSITIVE_NAMES for c in row["columns"]),
              "SENSITIVE_DATASET_COLUMN")
    p.require(row["schema_usage"] and row["can_select"], "DATASET_READ_GRANTS_MISSING")
    p.require(not row["schema_create"] and not row["mutation"] and not row["grant_option"],
              "UNSAFE_DATASET_CAPABILITY")
    p.require(not row['public_read'], 'PUBLIC_DATASET_READ_NOT_ALLOWED')
    hint = enrollment.get('key_columns',[])
    keys = row['primary_key']
    source = 'physical_primary_key' if keys else 'canonical_offset'
    if hint:
        p.require((bool(keys) and hint==keys) or (not keys and row['kind'] in ('v','m')), 'INVALID_DATASET_KEY')
        if not keys:
            keys, source = hint, 'declared_view_key'
    value = p.descriptor({"format": p.VERSION, "id": p.dataset_id(row["schema"], row["relation"]),
        "schema": row["schema"], "relation": row["relation"], "owner": row["owner"],
        "classification": enrollment["classification"],
        "columns": [{k: c[k] for k in ("name", "type", "nullable")} for c in row["columns"]],
        "key_columns": keys, 'key_source':source})
    return value, enrollment["title"]


def inventory(conn, dataset=None):
    verify_reader(conn)
    if dataset is None:
        rows = conn.execute(CATALOGUE_SQL).fetchall()
        return with_dependencies(conn,rows)
    # Opaque-ID lookup filters the relation before building full per-column/ACL
    # metadata. Never rescan every complete dataset descriptor for each page.
    query = CATALOGUE_SQL.replace('ORDER BY n.nspname,c.relname', '''
      AND n.nspname ~ '^[a-z][a-z0-9_]*$' AND c.relname ~ '^[a-z][a-z0-9_]*$'
      AND ('d_' || substring(encode(sha256(convert_to(
          '["' || n.nspname || '","' || c.relname || '"]','UTF8')),'hex') FROM 1 FOR 32))=%s
      ORDER BY n.nspname,c.relname''')
    return with_dependencies(conn,conn.execute(query,(dataset,)).fetchall())


def with_dependencies(conn, rows):
    if not rows:
        return rows
    unsafe = {r['root'] for r in conn.execute(DEPENDENCIES_SQL,
        ([r['oid'] for r in rows], sorted(SUPPORTED_CREATORS))).fetchall()}
    return [{**row, 'unsafe_dependencies': row['oid'] in unsafe} for row in rows]


def locate(conn, dataset):
    p.require(type(dataset) is str and p.DATASET_ID.fullmatch(dataset), "DATASET_NOT_FOUND")
    for row in inventory(conn,dataset):
        if p.dataset_id(row["schema"], row["relation"]) != dataset:
            continue
        if ((row['schema'], row['relation']) in INTERNAL_CONTROL_RELATIONS
                or (row['schema'], row['relation']) in HELD_FOR_REVIEW
                or row["owner"] not in SUPPORTED_CREATORS
                or decode_enrollment(row["enrollment"])["classification"] == "internal_control"):
            break
        return row
    raise p.DatasetError("DATASET_NOT_FOUND")


def paths(dataset):
    return {"metadata_url": "/api/datasets/v1/" + dataset,
            "rows_url": "/api/datasets/v1/" + dataset + "/rows"}


def discovery(conn):
    datasets = []
    for row in inventory(conn):
        try:
            enrollment = decode_enrollment(row["enrollment"])
        except p.DatasetError:
            continue  # Not a valid approved enrollment; private inventory reports it.
        if ((row['schema'], row['relation']) in INTERNAL_CONTROL_RELATIONS
                or (row['schema'], row['relation']) in HELD_FOR_REVIEW
                or enrollment["classification"] == "internal_control" or row["owner"] not in SUPPORTED_CREATORS):
            continue
        dataset = p.dataset_id(row["schema"], row["relation"])
        item = {"id": dataset, "title": enrollment["title"], "classification": enrollment["classification"],
                "state": "unavailable", "reason": None, "schema_version": None, **paths(dataset)}
        try:
            entry, _ = checked(row)
            # The actual reader executes a bounded no-row SQL projection in this
            # API request, not merely a role name or a historical permission bit.
            projection = sql.SQL(',').join(sql.Identifier(c["name"]) for c in entry["columns"])
            with conn.transaction():
                conn.execute(sql.SQL("SELECT {} FROM {}.{} LIMIT 0").format(projection,
                    sql.Identifier(entry["schema"]), sql.Identifier(entry["relation"])))
            item.update(state="available", schema_version=p.fingerprint(entry))
        except p.DatasetError as error:
            item["reason"] = str(error)
        except psycopg.Error:
            item['reason'] = 'DATASET_READ_UNAVAILABLE'
        datasets.append(item)
    return {"format": p.VERSION, "datasets": datasets}


def metadata(conn, dataset):
    row = locate(conn, dataset)
    enrollment = decode_enrollment(row["enrollment"])
    try:
        entry, title = checked(row)
    except p.DatasetError as error:
        return {"format": p.VERSION, "id": dataset, "title": enrollment["title"],
                "classification": enrollment["classification"], "state": "unavailable", "reason": str(error),
                "schema_version": None, "columns": [], "key_columns": [], "rows_url": paths(dataset)["rows_url"]}
    return {"format": p.VERSION, "id": dataset, "title": title, "classification": entry["classification"],
            "state": "available", "reason": None, "schema_version": p.fingerprint(entry),
            "columns": entry["columns"], "key_columns": entry["key_columns"], "rows_url": paths(dataset)["rows_url"]}
