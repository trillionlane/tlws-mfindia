"""Disposable-CI source compiler ONLY. No operational admission or provider API.

The raw catalogue keeps every actual ephemeral OID. A separately labelled
symbolic reference is an expectation about compiled committed source, never a
replacement for a live catalogue, certificate, or production identity.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import re
import signal
import threading
import time
import zlib

from psycopg.pq import TransactionStatus

BASE = "c550e3c21482f45684a1b63b7c70fc84b8a62b50"
AUTHORITY_SHA = "efec1d25524362b3caa568a14b096935969de0cc277704d1c04d2ddb95242372"
AUTHORITY_PATH = "docs/READER_SOURCE_REFERENCE_MIGRATION_AUTHORITY_20261029.json"
MAX_RAW = 8 * 1024 * 1024
CHUNK = 60000
MAX_CHUNKS = 64
MARKER = "SOURCE_SEMANTIC_REFERENCE_V1:"
SCHEMAS = (
    "mf", "mf_analytics", "mf_book_facts", "mf_distributions", "mf_enrichment",
    "mf_fact_refresh", "mf_fact_review", "mf_idcw", "mf_live_facts", "mf_monthly_facts",
    "mf_multi_facts", "mf_official_facts", "mf_overlap", "mf_portfolios", "mf_review", "mf_rolling",
)
FUNCTION_NAMES = (
    "dependencies", "canonical_json", "jsonb_build_object", "jsonb_agg", "btrim", "unnest",
    "jsonb_object_agg", "jsonb_array_elements", "jsonb_object_keys", "encode", "sha256",
    "convert_to", "jsonb_typeof", "string_agg", "to_json", "jsonb_each",
)
OPERATOR_NAMES = ("=", "<>", "<", ">", "<=", ">=", "->", "->>", "||", "!~")
TYPE_NAMES = ("int4", "jsonb", "text", "bpchar", "name", "bytea", "bool", "int8",
              "date", "timestamp", "timestamptz", "numeric")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition):
    if not condition:
        raise ValueError("invalid CI-only source reference")


def authority(root):
    """Call BEFORE fixture allocation. One reviewed authority, no wildcard."""
    root = Path(root).resolve()
    raw = (root / AUTHORITY_PATH).read_bytes()
    require(digest(raw) == AUTHORITY_SHA)
    value = json.loads(raw)
    require(value["base"] == BASE and value["total_bytes"] == 783832)
    rows = value["files"]
    require(len(rows) == 44 and len({r["path"] for r in rows}) == 44)
    require(rows[0]["path"] == "sql/000_schema_migrations.sql")
    require(not any(r["path"] == "sql/007_fund_data_status.sql" for r in rows))
    for row in rows:
        require(set(row) == {"path", "sha256", "bytes"})
        path = root / row["path"]
        require(not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(root))
        body = path.read_bytes()
        require(len(body) == row["bytes"] and digest(body) == row["sha256"])
    require(sum(r["bytes"] for r in rows) == value["total_bytes"])
    return value


def seal_sql(expression):
    return ("pg_catalog.jsonb_build_object('is_null',(" + expression + ") IS NULL,"
            "'sha256',CASE WHEN (" + expression + ") IS NULL THEN NULL ELSE "
            "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to((" + expression
            + ")::text,'UTF8')),'hex') END,'utf8_bytes',COALESCE("
            "pg_catalog.octet_length(pg_catalog.convert_to((" + expression + ")::text,'UTF8')),0))")


# Full named fields, not SELECT *. Values with executable/private expressions
# are sealed server-side before they cross the connection boundary.
FIELDS = {
    "pg_class": "oid relnamespace relname relkind relowner relrowsecurity relforcerowsecurity reloptions reltype relam".split(),
    "pg_proc": "oid pronamespace proname proowner prolang prokind prosecdef proargtypes prorettype provariadic prosupport provolatile proparallel proisstrict proleakproof proretset pronargs pronargdefaults proallargtypes proargmodes protrftypes procost prorows proacl".split(),
    "pg_type": "oid typnamespace typname typowner typtype typcategory typispreferred typisdefined typdelim typrelid typelem typarray typbasetype typcollation typinput typoutput typreceive typsend typmodin typmodout typanalyze typsubscript typlen typbyval typalign typstorage typnotnull typtypmod typndims typacl".split(),
    "pg_operator": "oid oprnamespace oprname oprowner oprkind oprleft oprright oprresult oprcode oprrest oprjoin oprcom oprnegate oprcanmerge oprcanhash".split(),
    "pg_cast": "oid castsource casttarget castfunc castcontext castmethod".split(),
    "pg_collation": "oid collnamespace collname collowner collprovider collencoding collisdeterministic collcollate collctype colllocale collversion".split(),
    "pg_language": "oid lanname lanowner lanpltrusted lanispl lanplcallfoid laninline lanvalidator lanacl".split(),
    "pg_opclass": "oid opcnamespace opcname opcowner opcmethod opcintype opckeytype opcfamily opcdefault".split(),
    "pg_opfamily": "oid opfnamespace opfname opfowner opfmethod".split(),
    "pg_am": "oid amname amhandler amtype".split(),
    "pg_amop": "oid amopfamily amoplefttype amoprighttype amopstrategy amopopr amoppurpose amopsortfamily amopmethod".split(),
    "pg_amproc": "oid amprocfamily amproclefttype amprocrighttype amprocnum amproc".split(),
    "pg_namespace": "oid nspname nspowner nspacl".split(),
    "pg_authid": "oid rolname rolsuper rolinherit rolcreaterole rolcreatedb rolcanlogin rolreplication rolbypassrls rolconfig".split(),
    "pg_rewrite": "oid rulename ev_class ev_type ev_enabled is_instead".split(),
    "pg_constraint": "oid conname connamespace contype conrelid contypid conindid conparentid confrelid condeferrable condeferred conenforced convalidated conkey confkey conpfeqop conppeqop conffeqop confupdtype confdeltype confmatchtype conislocal coninhcount connoinherit".split(),
    "pg_attrdef": "oid adrelid adnum".split(),
    "pg_extension": "oid extname extowner extnamespace extrelocatable extversion extconfig".split(),
}
FIELDS["pg_constraint"] += ["conperiod", "confdelsetcols", "conexclop"]
PRIVATE = {
    "pg_proc": {"body": "r.prosrc", "binary": "r.probin", "sql_body": "r.prosqlbody::text",
                "defaults": "pg_catalog.pg_get_expr(r.proargdefaults,0,false)",
                "argument_names": "pg_catalog.array_to_json(r.proargnames)::text",
                "configuration": "pg_catalog.array_to_json(r.proconfig)::text",
                "definition": "CASE WHEN r.prokind IN ('f','p') THEN pg_catalog.pg_get_functiondef(r.oid) ELSE NULL END"},
    "pg_class": {"view_definition": "CASE WHEN r.relkind IN ('v','m') THEN pg_catalog.pg_get_viewdef(r.oid,false) ELSE NULL END",
                 "index_definition": "CASE WHEN r.relkind IN ('i','I') THEN pg_catalog.pg_get_indexdef(r.oid) ELSE NULL END"},
    "pg_type": {"default_binary": "r.typdefaultbin::text", "default_text": "r.typdefault"},
    "pg_collation": {"icu_rules": "r.collicurules"},
    "pg_authid": {"configuration": "pg_catalog.array_to_json(r.rolconfig)::text"},
    "pg_rewrite": {"qualification": "r.ev_qual::text", "action": "r.ev_action::text",
                   "definition": "pg_catalog.pg_get_ruledef(r.oid,false)"},
    "pg_constraint": {"expression": "r.conbin::text",
                      "definition": "pg_catalog.pg_get_constraintdef(r.oid,false)"},
    "pg_attrdef": {"expression": "r.adbin::text",
                   "definition": "pg_catalog.pg_get_expr(r.adbin,r.adrelid,false)"},
    "pg_extension": {"conditions": "pg_catalog.array_to_json(r.extcondition)::text"},
}
# Every declared outgoing object reference, including fields not tracked by
# pg_depend. OID-array/vector fields are expanded individually without guessing.
REFS = {
    "pg_class": {"relnamespace": "pg_namespace", "relowner": "pg_authid", "reltype": "pg_type", "relam": "pg_am"},
    "pg_proc": {"pronamespace": "pg_namespace", "proowner": "pg_authid", "prolang": "pg_language", "proargtypes": "pg_type", "prorettype": "pg_type", "provariadic": "pg_type", "prosupport": "pg_proc", "proallargtypes": "pg_type", "protrftypes": "pg_type"},
    "pg_type": {"typnamespace": "pg_namespace", "typowner": "pg_authid", "typrelid": "pg_class", "typelem": "pg_type", "typarray": "pg_type", "typbasetype": "pg_type", "typcollation": "pg_collation", **{n: "pg_proc" for n in "typinput typoutput typreceive typsend typmodin typmodout typanalyze typsubscript".split()}},
    "pg_operator": {"oprnamespace": "pg_namespace", "oprowner": "pg_authid", **{n: "pg_type" for n in "oprleft oprright oprresult".split()}, **{n: "pg_proc" for n in "oprcode oprrest oprjoin".split()}, "oprcom": "pg_operator", "oprnegate": "pg_operator"},
    "pg_cast": {"castsource": "pg_type", "casttarget": "pg_type", "castfunc": "pg_proc"},
    "pg_collation": {"collnamespace": "pg_namespace", "collowner": "pg_authid"},
    "pg_language": {"lanowner": "pg_authid", **{n: "pg_proc" for n in "lanplcallfoid laninline lanvalidator".split()}},
    "pg_opclass": {"opcnamespace": "pg_namespace", "opcowner": "pg_authid", "opcmethod": "pg_am", "opcintype": "pg_type", "opckeytype": "pg_type", "opcfamily": "pg_opfamily"},
    "pg_opfamily": {"opfnamespace": "pg_namespace", "opfowner": "pg_authid", "opfmethod": "pg_am"},
    "pg_am": {"amhandler": "pg_proc"},
    "pg_amop": {"amopfamily": "pg_opfamily", "amoplefttype": "pg_type", "amoprighttype": "pg_type", "amopopr": "pg_operator", "amopsortfamily": "pg_opfamily", "amopmethod": "pg_am"},
    "pg_amproc": {"amprocfamily": "pg_opfamily", "amproclefttype": "pg_type", "amprocrighttype": "pg_type", "amproc": "pg_proc"},
    "pg_namespace": {"nspowner": "pg_authid"}, "pg_authid": {},
    "pg_rewrite": {"ev_class": "pg_class"},
    "pg_constraint": {"connamespace": "pg_namespace", "conrelid": "pg_class", "contypid": "pg_type", "conindid": "pg_class", "conparentid": "pg_constraint", "confrelid": "pg_class", **{n: "pg_operator" for n in "conpfeqop conppeqop conffeqop".split()}},
    "pg_attrdef": {"adrelid": "pg_class"},
    "pg_extension": {"extowner": "pg_authid", "extnamespace": "pg_namespace", "extconfig": "pg_class"},
}
REFS["pg_constraint"]["conexclop"] = "pg_operator"
ARRAY_REFS = {"proargtypes", "proallargtypes", "protrftypes", "conpfeqop", "conppeqop", "conffeqop", "conexclop", "extconfig"}
REG_REFS = {"prosupport", "oprcode", "oprrest", "oprjoin", "amhandler", "amproc",
            "typinput", "typoutput", "typreceive", "typsend", "typmodin", "typmodout", "typanalyze", "typsubscript"}
AGG_FIELDS = "aggfnoid aggkind aggnumdirectargs aggtransfn aggfinalfn aggcombinefn aggserialfn aggdeserialfn aggmtransfn aggminvtransfn aggmfinalfn aggfinalextra aggmfinalextra aggfinalmodify aggmfinalmodify aggsortop aggtranstype aggmtranstype aggtransspace aggmtransspace".split()
AGG_REFS = {**{n: "pg_proc" for n in "aggfnoid aggtransfn aggfinalfn aggcombinefn aggserialfn aggdeserialfn aggmtransfn aggminvtransfn aggmfinalfn".split()}, "aggsortop": "pg_operator", "aggtranstype": "pg_type", "aggmtranstype": "pg_type"}
RANGE_REFS = {"rngtypid": "pg_type", "rngsubtype": "pg_type", "rngmultitypid": "pg_type",
              "rngcollation": "pg_collation", "rngsubopc": "pg_opclass",
              "rngcanonical": "pg_proc", "rngsubdiff": "pg_proc"}


class WallSpent(TimeoutError):
    """Transport teardown only, never SQL after the fifteen-second wall."""


class AbortUnconfirmed(RuntimeError):
    """Sole rollback could not be confirmed. No retry or reconnect."""


class Bounded:
    def __init__(self, conn, deadline, clock=time.monotonic):
        self.conn, self.deadline, self.clock = conn, deadline, clock

    def remaining(self):
        if self.clock() >= self.deadline:
            raise WallSpent("source-reference whole wall spent")

    def execute(self, *args, **kwargs):
        self.remaining()
        result = self.conn.execute(*args, **kwargs)
        self.remaining()
        return result


@contextmanager
def reference_transaction(conn):
    """Exact original transaction bounds; validation/serialization inside wall."""
    require(threading.current_thread() is threading.main_thread()
            and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    require(conn.autocommit is True and conn.info.transaction_status == TransactionStatus.IDLE)
    previous = signal.getsignal(signal.SIGALRM)

    def expired(*_):
        raise WallSpent("source-reference whole wall spent")

    bounded = Bounded(conn, time.monotonic() + 15)
    begun = attempted = False
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 15)
    try:
        begun = True
        for statement in ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
                          "SET LOCAL search_path=pg_catalog", "SET LOCAL statement_timeout='5s'",
                          "SET LOCAL lock_timeout='5s'", "SET LOCAL transaction_timeout='15s'",
                          "SET LOCAL TimeZone='UTC'"):
            with bounded.execute(statement, prepare=False) as cursor:
                require(cursor.statusmessage == ("BEGIN" if statement.startswith("BEGIN") else "SET"))
        yield bounded
        bounded.remaining()
        attempted = True
        with bounded.execute("ROLLBACK", prepare=False) as cursor:
            require(cursor.statusmessage == "ROLLBACK")
    except BaseException as error:
        if begun:
            if attempted or isinstance(error, WallSpent) or time.monotonic() >= bounded.deadline:
                raise AbortUnconfirmed("source-reference abort unconfirmed; no further SQL") from None
            attempted = True
            try:
                with bounded.execute("ROLLBACK", prepare=False) as cursor:
                    require(cursor.statusmessage == "ROLLBACK")
            except BaseException:
                raise AbortUnconfirmed("source-reference abort unconfirmed; no retry") from None
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def extension_shape(value):
    config = value["extconfig"]
    require(config is None or (type(config) is list and all(type(x) is int and x > 0 for x in config)))
    require(value["conditions_seal"]["is_null"] is (config is None))
    require(value["config_count"] == (None if config is None else len(config)))
    require(value["condition_count"] == value["config_count"])


def range_references(value):
    require(set(value) == set(RANGE_REFS))
    require(all(type(v) is int and 0 <= v <= 4294967295 for v in value.values()))
    require(value["rngtypid"] > 0 and value["rngsubtype"] > 0 and value["rngmultitypid"] > 0)
    return [(target, value[field]) for field, target in RANGE_REFS.items() if value[field]]


def projection(catalog):
    require(catalog in FIELDS)
    expressions = []
    for name in FIELDS[catalog]:
        if name == "rolconfig":
            continue
        value = "r." + name
        if name in ARRAY_REFS:
            value += "::oid[]::bigint[]"
        elif name in REFS[catalog] or name == "oid" or name in REG_REFS:
            value += "::oid::bigint"
        expressions.extend(("'" + name + "'", value))
    for name, expr in PRIVATE.get(catalog, {}).items():
        expressions.extend(("'" + name + "_seal'", seal_sql(expr)))
    if catalog == "pg_collation":
        expressions.extend(("'actual_version'", "pg_catalog.pg_collation_actual_version(r.oid)"))
    if catalog == "pg_extension":
        expressions.extend(("'config_count'", "pg_catalog.cardinality(r.extconfig)",
                            "'condition_count'", "pg_catalog.cardinality(r.extcondition)"))
    return "pg_catalog.jsonb_build_object(" + ",".join(expressions) + ")"


def rows(conn, query, parameters=()):
    with conn.execute(query, parameters, prepare=False) as cursor:
        result = [dict(r) for r in cursor.fetchall()]
        require(cursor.rowcount == len(result) and cursor.statusmessage == "SELECT " + str(len(result)))
        return result


def collect(conn):
    """Exact committed-source metadata in disposable CI only; no admission.

    Caller owns a read-only RR transaction. All catalogue I/O is SELECT. This
    compiler intentionally has no live operator, secret lookup or approval API.
    """
    context = rows(conn, "SELECT current_database() AS database,current_user AS current_user,session_user AS principal,"
                   "current_setting('server_version_num')::int AS version,"
                   "current_setting('transaction_read_only') AS readonly,"
                   "current_setting('transaction_isolation') AS isolation,"
                   "current_setting('search_path') AS search_path,"
                   "current_setting('statement_timeout') AS statement_timeout,"
                   "current_setting('lock_timeout') AS lock_timeout,"
                   "current_setting('transaction_timeout') AS transaction_timeout,"
                   "current_setting('TimeZone') AS timezone")[0]
    require(context["database"].startswith("mfdata_reference_") and context["readonly"] == "on"
            and context["isolation"] == "repeatable read" and 180000 <= context["version"] < 190000)
    require(context["current_user"] == context["principal"] == "mfdata_admin"
            and context["search_path"] == "pg_catalog" and context["timezone"] == "UTC"
            and context["statement_timeout"] == context["lock_timeout"] == "5s"
            and context["transaction_timeout"] == "15s")
    classes = {r["relname"]: r["oid"] for r in rows(conn,
        "SELECT oid::bigint AS oid,relname FROM pg_catalog.pg_class "
        "WHERE relnamespace='pg_catalog'::regnamespace AND relname=ANY(%s)", (list(FIELDS),))}
    require(set(classes) == set(FIELDS))
    class_names = {v: k for k, v in classes.items()}
    pending, objects, identities, edges = set(), {}, {}, set()

    def add(catalog, oid, subid=0, *, origin=None):
        require(catalog in FIELDS and type(oid) is int and 0 <= oid <= 4294967295
                and type(subid) is int and subid >= 0)
        if oid == 0:
            return
        node = (catalog, oid, subid)
        pending.add(node)
        if origin is not None:
            edges.add((*origin, catalog, oid, subid, "declared"))

    seeds = rows(conn, "SELECT oid::bigint AS oid FROM pg_catalog.pg_class "
                 "WHERE relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=ANY(%s)) "
                 "AND relkind IN ('r','p','v','m','S') ORDER BY oid", (list(SCHEMAS),))
    for r in seeds:
        add("pg_class", r["oid"])
    for r in rows(conn, "SELECT oid::bigint AS oid FROM pg_catalog.pg_proc WHERE "
                  "pronamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=ANY(%s)) "
                  "AND proname=ANY(%s) ORDER BY oid",
                  (["pg_catalog", "mf_review", "mf_fact_review"], list(FUNCTION_NAMES))):
        add("pg_proc", r["oid"])
    for r in rows(conn, "SELECT oid::bigint AS oid FROM pg_catalog.pg_operator WHERE "
                  "oprnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=ANY(%s)) "
                  "AND oprname=ANY(%s) ORDER BY oid",
                  (["pg_catalog", "mf_review", "mf_fact_review"], list(OPERATOR_NAMES))):
        add("pg_operator", r["oid"])
    for r in rows(conn, "SELECT oid::bigint AS oid FROM pg_catalog.pg_type WHERE "
                  "typnamespace='pg_catalog'::regnamespace AND typname=ANY(%s)", (list(TYPE_NAMES),)):
        add("pg_type", r["oid"])
    for r in rows(conn, "SELECT oid::bigint AS oid FROM pg_catalog.pg_collation WHERE "
                  "collnamespace='pg_catalog'::regnamespace AND collname IN ('C','default')"):
        add("pg_collation", r["oid"])
    aggregates, ranges, columns, memberships, conversions = {}, {}, {}, [], []
    expanded = set()
    while True:
        todo = sorted(pending - expanded)
        if not todo:
            # Complete cast candidate population among all reached actual and
            # candidate argument/return types; new cast support may grow closure.
            types = sorted(oid for cat, oid in objects if cat == "pg_type")
            for r in rows(conn, "SELECT oid::bigint AS oid FROM pg_catalog.pg_cast "
                          "WHERE castsource=ANY(%s::oid[]) OR casttarget=ANY(%s::oid[])", (types, types)):
                add("pg_cast", r["oid"])
            if not (pending - expanded):
                break
            continue
        require(len(expanded) + len(todo) <= 20000)
        # Batch each catalog to avoid per-object server round trips.
        for catalog in sorted({t[0] for t in todo}):
            nodes = [t for t in todo if t[0] == catalog]
            ids = sorted({t[1] for t in nodes})
            source = "pg_roles" if catalog == "pg_authid" else catalog
            query = "SELECT " + projection(catalog) + " AS value FROM pg_catalog." + source + " r WHERE r.oid=ANY(%s::oid[]) ORDER BY r.oid"
            values = rows(conn, query, (ids,))
            require({r["value"]["oid"] for r in values} == set(ids))
            for item in values:
                value = item["value"]
                key = (catalog, value["oid"])
                if key in objects:
                    require(objects[key] == value)
                objects[key] = value
                if catalog == "pg_extension":
                    extension_shape(value)
                origin = (catalog, value["oid"], 0)
                for field, target in REFS[catalog].items():
                    items = value[field] if field in ARRAY_REFS else [value[field]]
                    for oid in items or []:
                        add(target, oid, origin=origin)
            # pg_identify_object is metadata deparsing, never target invocation.
            identity_rows = rows(conn,
                "SELECT pair.id::bigint AS oid,pair.sub AS subid,pg_catalog.to_jsonb(i) AS value "
                "FROM pg_catalog.unnest(%s::oid[],%s::int[]) AS pair(id,sub) "
                "CROSS JOIN LATERAL pg_catalog.pg_identify_object(%s::oid,pair.id,pair.sub) i "
                "ORDER BY pair.id,pair.sub", ([n[1] for n in nodes], [n[2] for n in nodes], classes[catalog]))
            require(len(identity_rows) == len(nodes))
            for item in identity_rows:
                cat, oid, subid = catalog, item["oid"], item["subid"]
                identity = item["value"]
                require(type(identity["identity"]) is str and bool(identity["identity"]))
                if cat == "pg_collation":
                    identity["encoding"] = objects[(cat, oid)]["collencoding"]
                identities[(cat, oid, subid)] = identity
                expanded.add((cat, oid, subid))
            for dep in rows(conn, "SELECT classid::bigint,objid::bigint,objsubid,refclassid::bigint,"
                            "refobjid::bigint,refobjsubid,deptype::text FROM pg_catalog.pg_depend "
                            "WHERE classid=%s::oid AND objid=ANY(%s::oid[]) "
                            "ORDER BY classid,objid,objsubid,refclassid,refobjid,refobjsubid,deptype",
                            (classes[catalog], ids)):
                require(dep["refclassid"] in class_names and dep["deptype"] in ("n", "a", "i", "P", "S", "e"))
                src = (catalog, dep["objid"], dep["objsubid"])
                target = class_names[dep["refclassid"]]
                add(catalog, dep["objid"], dep["objsubid"])
                add(target, dep["refobjid"], dep["refobjsubid"])
                edges.add((*src, target, dep["refobjid"], dep["refobjsubid"], dep["deptype"]))
            if catalog == "pg_class":
                for child, field in (("pg_rewrite", "ev_class"), ("pg_constraint", "conrelid"), ("pg_attrdef", "adrelid")):
                    for r in rows(conn, "SELECT oid::bigint AS oid," + field + "::bigint AS parent FROM pg_catalog." + child + " WHERE " + field + "=ANY(%s::oid[])", (ids,)):
                        add(child, r["oid"], origin=(catalog, r["parent"], 0))
                for r in rows(conn, "SELECT indexrelid::bigint AS oid,indrelid::bigint AS parent FROM pg_catalog.pg_index WHERE indrelid=ANY(%s::oid[])", (ids,)):
                    add("pg_class", r["oid"], origin=(catalog, r["parent"], 0))
                for r in rows(conn, "SELECT attrelid::bigint,attnum,attname,attisdropped,atttypid::bigint,atttypmod,attcollation::bigint,attnotnull,attgenerated::text,attidentity::text FROM pg_catalog.pg_attribute WHERE attrelid=ANY(%s::oid[]) AND attnum>0 ORDER BY attrelid,attnum", (ids,)):
                    columns[(r["attrelid"], r["attnum"])] = r
                    add("pg_type", r["atttypid"], origin=(catalog, r["attrelid"], r["attnum"]))
                    add("pg_collation", r["attcollation"], origin=(catalog, r["attrelid"], r["attnum"]))
                    if not r["attisdropped"]:
                        add(catalog, r["attrelid"], r["attnum"])
            elif catalog == "pg_proc":
                fields = []
                for f in AGG_FIELDS:
                    fields.extend(("'" + f + "'", "a." + f + ("::oid::bigint" if f in AGG_REFS else "")))
                for f in ("agginitval", "aggminitval"):
                    fields.extend(("'" + f + "_seal'", seal_sql("a." + f)))
                for item in rows(conn, "SELECT pg_catalog.jsonb_build_object(" + ",".join(fields) + ") AS value FROM pg_catalog.pg_aggregate a WHERE aggfnoid=ANY(%s::oid[]) ORDER BY aggfnoid", (ids,)):
                    a = item["value"]
                    aggregates[a["aggfnoid"]] = a
                    for f, target in AGG_REFS.items():
                        add(target, a[f], origin=(catalog, a["aggfnoid"], 0))
            elif catalog == "pg_opfamily":
                for child, field in (("pg_amop", "amopfamily"), ("pg_amproc", "amprocfamily")):
                    for r in rows(conn, "SELECT oid::bigint AS oid," + field + "::bigint AS parent FROM pg_catalog." + child + " WHERE " + field + "=ANY(%s::oid[])", (ids,)):
                        add(child, r["oid"], origin=(catalog, r["parent"], 0))
            elif catalog == "pg_type":
                range_columns = ",".join("r." + f + "::oid::bigint AS " + f for f in RANGE_REFS)
                for value in rows(conn, "SELECT " + range_columns + " FROM pg_catalog.pg_range r WHERE rngtypid=ANY(%s::oid[]) OR rngmultitypid=ANY(%s::oid[]) ORDER BY rngtypid", (ids, ids)):
                    ranges[value["rngtypid"]] = value
                    for target, oid in range_references(value):
                        add(target, oid, origin=(catalog, value["rngtypid"], 0))
                for r in rows(conn, "SELECT oid::bigint AS oid,opcintype::bigint AS parent FROM pg_catalog.pg_opclass WHERE opcdefault AND opcintype=ANY(%s::oid[])", (ids,)):
                    add("pg_opclass", r["oid"], origin=(catalog, r["parent"], 0))
                for r in rows(conn, "SELECT oid::bigint AS oid,contypid::bigint AS parent FROM pg_catalog.pg_constraint WHERE contypid=ANY(%s::oid[])", (ids,)):
                    add("pg_constraint", r["oid"], origin=(catalog, r["parent"], 0))
    require(pending == expanded)
    for r in rows(conn, "SELECT roleid::bigint,member::bigint,grantor::bigint,admin_option,inherit_option,set_option FROM pg_catalog.pg_auth_members WHERE roleid=ANY(%s::oid[]) OR member=ANY(%s::oid[]) ORDER BY roleid,member,grantor", (sorted(k[1] for k in objects if k[0] == "pg_authid"),) * 2):
        memberships.append(r)
    for (cat, oid), value in sorted(objects.items()):
        if cat == "pg_type" and value["typtype"] == "d":
            conversions.append({"kind": "domain_base", "source": oid, "target": value["typbasetype"], "typmod": value["typtypmod"], "not_null": value["typnotnull"]})
    # Automatic I/O conversions are explicit candidate rules, not invented pg_cast rows.
    type_rows = [(key[1], row) for key, row in sorted(objects.items()) if key[0] == "pg_type"]
    for oid, value in type_rows:
        for target, other in type_rows:
            if target != oid and (value["typcategory"] == "S" or other["typcategory"] == "S"):
                conversions.append({"kind": "automatic_io_candidate", "source": oid, "target": target, "output": value["typoutput"], "input": other["typinput"], "source_typmod": value["typtypmod"], "target_typmod": other["typtypmod"]})
    database = rows(conn, "SELECT oid::bigint,datname,encoding,datlocprovider::text,datcollate,datctype,datlocale,datcollversion,pg_catalog.pg_database_collation_actual_version(oid) AS actual_collation_version," + seal_sql("daticurules") + " AS icu_rules_seal FROM pg_catalog.pg_database WHERE datname=current_database()")[0]
    result = {"format": "mf-reader-raw-CI-source-reference-v1", "base_git": BASE,
              "authority_sha256": AUTHORITY_SHA, "context": context, "database": database,
              "objects": [{"catalog": cat, "oid": oid, "row": row} for (cat, oid), row in sorted(objects.items())],
              "identities": [{"catalog": cat, "oid": oid, "subid": sub, "identity": ident} for (cat, oid, sub), ident in sorted(identities.items())],
              "edges": [list(e) for e in sorted(edges)], "aggregates": [aggregates[k] for k in sorted(aggregates)],
              "ranges": [ranges[k] for k in sorted(ranges)],
              "columns": [columns[k] for k in sorted(columns)], "memberships": memberships,
              "conversions": conversions, "runtime_authority": False, "permission_acceptance": False}
    require(len(canonical(result).encode()) <= MAX_RAW)
    return result


def reference_envelope(raw_reference, *, identity):
    require(type(identity) is str and re.fullmatch(r"[0-9a-f]{32}", identity))
    raw = canonical(raw_reference).encode()
    require(0 < len(raw) <= MAX_RAW)
    compressed = gzip.compress(raw, mtime=0)
    encoded = base64.b64encode(compressed).decode()
    count = (len(encoded) + CHUNK - 1) // CHUNK
    require(1 <= count <= MAX_CHUNKS)
    header = {"format": "source-reference-chunks-v1", "identity": identity, "base_git": BASE,
              "authority_sha256": AUTHORITY_SHA, "raw_bytes": len(raw), "raw_sha256": digest(raw),
              "compressed_bytes": len(compressed), "compressed_sha256": digest(compressed),
              "encoded_bytes": len(encoded), "count": count, "chunk_bytes": CHUNK}
    result = [MARKER + canonical({"header": header})]
    for index in range(count):
        value = encoded[index * CHUNK:(index + 1) * CHUNK]
        result.append(MARKER + canonical({"chunk": {"identity": identity, "index": index,
            "count": count, "chars": len(value), "sha256": digest(value.encode()), "value": value}}))
    result.append(MARKER + canonical({"end": {"identity": identity, "raw_sha256": digest(raw)}}))
    return result


def extract_envelope(lines):
    records = []
    for line in lines:
        if MARKER in line:
            # CI timestamp/job prefixes precede marker; suffix must be pure JSON.
            body = line.split(MARKER, 1)[1].strip()
            records.append(json.loads(body, object_pairs_hook=unique))
    require(len(records) >= 3 and set(records[0]) == {"header"} and set(records[-1]) == {"end"})
    h = records[0]["header"]
    require(set(h) == {"format", "identity", "base_git", "authority_sha256", "raw_bytes", "raw_sha256", "compressed_bytes", "compressed_sha256", "encoded_bytes", "count", "chunk_bytes"})
    require(h["format"] == "source-reference-chunks-v1" and h["base_git"] == BASE and h["authority_sha256"] == AUTHORITY_SHA)
    require(all(type(h[k]) is int for k in ("raw_bytes", "compressed_bytes", "encoded_bytes", "count", "chunk_bytes")))
    require(type(h["count"]) is int and 1 <= h["count"] <= MAX_CHUNKS and len(records) == h["count"] + 2)
    require(type(h["raw_bytes"]) is int and 0 < h["raw_bytes"] <= MAX_RAW and h["chunk_bytes"] == CHUNK)
    require(re.fullmatch(r"[0-9a-f]{32}", h["identity"]) is not None)
    parts = []
    for index, item in enumerate(records[1:-1]):
        require(set(item) == {"chunk"})
        c = item["chunk"]
        require(set(c) == {"identity", "index", "count", "chars", "sha256", "value"})
        require(all(type(c[k]) is int for k in ("index", "count", "chars")))
        require(type(c["index"]) is int and c["index"] == index and c["identity"] == h["identity"] and c["count"] == h["count"])
        require(type(c["value"]) is str and c["chars"] == len(c["value"]) and digest(c["value"].encode()) == c["sha256"])
        require(len(c["value"]) == CHUNK if index + 1 < h["count"] else 0 < len(c["value"]) <= CHUNK)
        parts.append(c["value"])
    encoded = "".join(parts)
    require(len(encoded) == h["encoded_bytes"])
    compressed = base64.b64decode(encoded, validate=True)
    require(base64.b64encode(compressed).decode() == encoded and len(compressed) == h["compressed_bytes"] and digest(compressed) == h["compressed_sha256"])
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = decoder.decompress(compressed, MAX_RAW + 1)
    require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail)
    require(len(raw) == h["raw_bytes"] and digest(raw) == h["raw_sha256"])
    require(records[-1]["end"] == {"identity": h["identity"], "raw_sha256": h["raw_sha256"]})
    value = json.loads(raw, object_pairs_hook=unique)
    require(canonical(value).encode() == raw)
    return value


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def semantic_reference(raw):
    """Named expectation ONLY; does not turn synthetic IDs into public IDs.

    Keep the entire raw document alongside this separate symbolic projection.
    No production validator accepts this format or calls this test-only helper.
    """
    require(raw["format"] == "mf-reader-raw-CI-source-reference-v1")
    names = {(r["catalog"], r["oid"], r["subid"]): r["identity"] for r in raw["identities"]}
    require(len(names) == len(raw["identities"]))

    def name(cat, oid, sub=0):
        if oid == 0:
            return None
        require((cat, oid, sub) in names)
        return deepcopy(names[(cat, oid, sub)])

    objects = []
    for obj in raw["objects"]:
        cat, oid, row = obj["catalog"], obj["oid"], deepcopy(obj["row"])
        extra = {"actual_version"} if cat == "pg_collation" else {"config_count", "condition_count"} if cat == "pg_extension" else set()
        require(set(row) == (set(FIELDS[cat]) - {"rolconfig"}) | {k + "_seal" for k in PRIVATE.get(cat, {})} | extra)
        row.pop("oid")
        for field, target in REFS[cat].items():
            values = row[field]
            row[field] = [name(target, v) for v in values] if field in ARRAY_REFS and values is not None else None if values is None else name(target, values)
        objects.append({"catalog": cat, "identity": name(cat, oid), "declaration": row})
    edges = [[name(a, b, c), name(d, e, f), dep] for a, b, c, d, e, f, dep in raw["edges"]]
    aggregate_rows = []
    for original in raw["aggregates"]:
        value = deepcopy(original)
        for field, target in AGG_REFS.items():
            value[field] = name(target, value[field])
        aggregate_rows.append(value)
    range_rows = []
    for original in raw["ranges"]:
        value = deepcopy(original)
        for field, target in RANGE_REFS.items():
            value[field] = name(target, value[field])
        range_rows.append(value)
    column_rows = []
    for original in raw["columns"]:
        value = deepcopy(original)
        value["attrelid"] = name("pg_class", value["attrelid"])
        value["atttypid"] = name("pg_type", value["atttypid"])
        value["attcollation"] = name("pg_collation", value["attcollation"])
        column_rows.append(value)
    member_rows = []
    for original in raw["memberships"]:
        value = deepcopy(original)
        for field in ("roleid", "member", "grantor"):
            value[field] = name("pg_authid", value[field])
        member_rows.append(value)
    conversion_rows = []
    for original in raw["conversions"]:
        value = deepcopy(original)
        for field in ("source", "target"):
            value[field] = name("pg_type", value[field])
        for field in ("input", "output"):
            if field in value:
                value[field] = name("pg_proc", value[field])
        conversion_rows.append(value)
    database = deepcopy(raw["database"])
    # A different source-compilation database name/OID is not production
    # evidence. Preserve the whole original separately; compare locale semantics.
    database.pop("oid")
    database.pop("datname")
    return {"format": "mf-reader-symbolic-CI-source-expectation-v1", "base_git": BASE,
            "authority_sha256": AUTHORITY_SHA, "raw_reference_sha256": digest(canonical(raw).encode()),
            "objects": sorted(objects, key=canonical), "edges": sorted(edges, key=canonical),
            "aggregates": sorted(aggregate_rows, key=canonical),
            "ranges": sorted(range_rows, key=canonical),
            "columns": sorted(column_rows, key=canonical),
            "memberships": sorted(member_rows, key=canonical),
            "conversions": sorted(conversion_rows, key=canonical),
            "database": database, "server_version_num": raw["context"]["version"],
            "runtime_authority": False, "permission_acceptance": False,
            "raw_document_must_be_retained": True,
            "opaque_node_tree_seals_are_instance_bound": True,
            "portable_admission_not_established": True}
