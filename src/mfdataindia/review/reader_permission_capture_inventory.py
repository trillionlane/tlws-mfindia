"""Fixed catalogue-only read program; never an executable grant proposal."""
from copy import deepcopy

from mfdataindia.review import reader_access_correction as engine

CONTEXT_SQL = ("SELECT q.*,pg_backend_pid()::int AS pid,pg_current_snapshot()::text AS snapshot,"
               "to_char(transaction_timestamp() AT TIME ZONE 'UTC',"
               "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS transaction_started_at FROM ("
               + engine.CONTEXT_SQL + ") q")
CANDIDATE_SQL = """WITH RECURSIVE walk(oid,descend) AS (
 SELECT c.oid,false FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='mf' AND c.relname IN ('current_fund_facts','fund_variants')
 UNION
 SELECT e.oid,e.descend FROM walk w JOIN pg_catalog.pg_class c ON c.oid=w.oid
 CROSS JOIN LATERAL (
  SELECT d.refobjid AS oid,true AS descend FROM pg_catalog.pg_rewrite r
  JOIN pg_catalog.pg_depend d ON d.classid='pg_catalog.pg_rewrite'::regclass
   AND d.objid=r.oid AND d.refclassid='pg_catalog.pg_class'::regclass
  WHERE c.relkind='v' AND r.ev_class=c.oid AND r.rulename='_RETURN' AND d.refobjid<>c.oid
  UNION
  SELECT i.inhrelid,true FROM pg_catalog.pg_inherits i WHERE w.descend AND i.inhparent=c.oid
 ) e
)
SELECT c.oid::bigint AS oid,n.nspname,c.relname,c.relkind::text,c.relowner::bigint AS owner,
 CASE WHEN c.relkind='v' THEN jsonb_build_object('is_null',false,
 'sha256',encode(sha256(convert_to(pg_get_viewdef(c.oid,false),'UTF8')),'hex'),
 'utf8_bytes',octet_length(convert_to(pg_get_viewdef(c.oid,false),'UTF8')))
 ELSE jsonb_build_object('is_null',true,'sha256',null,'utf8_bytes',0) END AS definition
FROM (SELECT DISTINCT oid FROM walk) w JOIN pg_catalog.pg_class c ON c.oid=w.oid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace ORDER BY c.oid"""
CONTEXT_COLUMNS = ("database principal session version isolation readonly statement_timeout lock_timeout "
                   "transaction_timeout search_path timezone pid snapshot transaction_started_at").split()
CANDIDATE_COLUMNS = "oid nspname relname relkind owner definition".split()
CONTROLS = (
    "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
    "SET LOCAL statement_timeout='5s'", "SET LOCAL lock_timeout='5s'",
    "SET LOCAL transaction_timeout='15s'", "SET LOCAL search_path=pg_catalog", "SET LOCAL TimeZone='UTC'",
)
TERMINAL = "ROLLBACK"
LABEL = "CONSERVATIVE_CATALOG_DEPENDENCY_CANDIDATE_NOT_ACTUAL_LOCK_WITNESS"


def inventory():
    return deepcopy({"format": "reader-permission-capture-inventory-v1", "controls": list(CONTROLS),
        "terminal": TERMINAL, "wall_seconds": 15, "candidate_semantics": LABEL,
        "queries": [{"id": "context", "sql": CONTEXT_SQL, "columns": CONTEXT_COLUMNS},
                    {"id": "state", "sql": engine.STATE_SQL, "columns": ["value"]},
                    {"id": "candidate", "sql": CANDIDATE_SQL, "columns": CANDIDATE_COLUMNS}],
        "sequence": ["context", "state", "candidate", "state", "candidate", "context"]})
