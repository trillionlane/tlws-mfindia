"""Disposable-CI source expectations only. Never operational admission.

All original numeric/opaque metadata remains intact. The portable projection is
separate, reproducible source evidence, not a replacement for live identities.
"""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import signal
import threading
import time

import reader_source_reference as legacy
from mfdataindia.review import broad_reader_capture_inventory as inventory

AUTHORITY_PATH = "docs/CI_SOURCE_REFERENCE_BOOK_VARIANT_AUTHORITY_20261029.json"
AUTHORITY_SHA = "77917a8dda9c41cd3a46c6ee662a13b221d01a7ca9c1eebd6622888c7b6d89ce"
PARENT = "016e63afb7b82aa1369b97bfdc63a729524d0c1d"
PROFILES = ("BASE44_EXTENDED", "BASE44_BOOK_V2_SINGLE_PASS")
MARKERS = {p: "SOURCE_SEMANTIC_REFERENCE_" + p + "_V1:" for p in PROFILES}
ACL = {
    "pg_proc": ("proacl", "proowner", "f", {"EXECUTE"}),
    "pg_type": ("typacl", "typowner", "T", {"USAGE"}),
    "pg_language": ("lanacl", "lanowner", "l", {"USAGE"}),
    "pg_namespace": ("nspacl", "nspowner", "n", {"CREATE", "USAGE"}),
}
OPAQUE = {
    "pg_proc": ("sql_body_seal",),
    "pg_type": ("default_binary_seal",),
    "pg_rewrite": ("qualification_seal", "action_seal"),
    "pg_constraint": ("expression_seal",),
    "pg_attrdef": ("expression_seal",),
}
DEPARSE = {cat: "definition_seal" for cat in OPAQUE} | {"pg_type": "default_text_seal"}
LEGACY_KEYS = set(
    "format base_git authority_sha256 context database objects identities edges aggregates ranges columns memberships conversions runtime_authority permission_acceptance".split()
)
require, canonical, digest = legacy.require, legacy.canonical, legacy.digest


def variant_authority(root):
    root = Path(root).resolve()
    original = legacy.authority(root)
    raw = (root / AUTHORITY_PATH).read_bytes()
    require(digest(raw) == AUTHORITY_SHA)
    value = json.loads(raw)
    require(
        set(value)
        == {
            "format",
            "implementation_parent",
            "base44_authority_sha256",
            "files",
            "total_bytes",
            "execution_order",
            "selection_only_not_invoked",
            "runtime_authority",
        }
    )
    require(value["implementation_parent"] == PARENT and value["runtime_authority"] is False)
    require(value["base44_authority_sha256"] == legacy.AUTHORITY_SHA)
    require(len(value["files"]) == 8 and value["total_bytes"] == 76406)
    require(len({r["path"] for r in value["files"]}) == 8)
    for row in value["files"]:
        require(set(row) == {"path", "bytes", "sha256"})
        path = root / row["path"]
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root))
        body = path.read_bytes()
        require(len(body) == row["bytes"] and digest(body) == row["sha256"])
    require(sum(r["bytes"] for r in value["files"]) == 76406)
    return {"base": original, "variant": value}


def oid(value, *, public=False):
    require(type(value) is int and (0 if public else 1) <= value <= 4294967295)


def seal(value):
    require(type(value) is dict and set(value) == {"is_null", "sha256", "utf8_bytes"})
    require(
        type(value["is_null"]) is bool
        and type(value["utf8_bytes"]) is int
        and value["utf8_bytes"] >= 0
    )
    if value["is_null"]:
        require(value["sha256"] is None and value["utf8_bytes"] == 0)
    else:
        require(type(value["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", value["sha256"]))


def query(conn, text, params, columns):
    with conn.execute(text, params, prepare=False) as cursor:
        require([c.name for c in cursor.description] == list(columns))
        values = [dict(r) for r in cursor.fetchall()]
        require(
            type(cursor.rowcount) is int
            and cursor.rowcount == len(values)
            and cursor.statusmessage == "SELECT " + str(len(values))
        )
    return values


def acl_query(cat):
    require(cat in ACL)
    field, owner, kind, _ = ACL[cat]
    effective = (
        "pg_catalog.has_function_privilege(%s::oid,r.oid,'EXECUTE')"
        if cat == "pg_proc"
        else "NULL::boolean"
    )
    return (
        "SELECT r.oid::bigint AS oid,(r." + field + " IS NULL) AS raw_acl_is_null,"
        "COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array("
        "a.grantor::bigint,a.grantee::bigint,a.privilege_type,a.is_grantable) "
        "ORDER BY a.grantor,a.grantee,a.privilege_type,a.is_grantable) "
        "FROM pg_catalog.aclexplode(COALESCE(r."
        + field
        + ",pg_catalog.acldefault('"
        + kind
        + '\'::"char",r.'
        + owner
        + "))) a),'[]'::jsonb) AS atoms,"
        + effective
        + " AS reader_execute FROM pg_catalog."
        + cat
        + " r WHERE r.oid=ANY(%s::oid[]) ORDER BY r.oid"
    )


def collect_extended(conn):
    raw = legacy.collect(conn)
    objects = {(o["catalog"], o["oid"]): o["row"] for o in raw["objects"]}
    reader = [
        key[1]
        for key, row in objects.items()
        if key[0] == "pg_authid" and row["rolname"] == "mfdata_verified_reader"
    ]
    require(len(reader) == 1)
    acl = []
    for cat in sorted(ACL):
        ids = sorted(key[1] for key in objects if key[0] == cat)
        params = (reader[0], ids) if cat == "pg_proc" else (ids,)
        values = query(
            conn, acl_query(cat), params, ("oid", "raw_acl_is_null", "atoms", "reader_execute")
        )
        require([v["oid"] for v in values] == ids)
        acl.extend({"catalog": cat, **v} for v in values)
    candidate = query(conn, inventory.CANDIDATE_SQL, (), inventory.CANDIDATE_COLUMNS)
    ids = sorted(key[1] for key in objects if key[0] == "pg_class")
    inheritance = query(
        conn,
        "SELECT inhrelid::bigint AS inhrelid,inhparent::bigint AS inhparent,inhseqno,inhdetachpending "
        "FROM pg_catalog.pg_inherits WHERE inhparent=ANY(%s::oid[]) OR inhrelid=ANY(%s::oid[]) "
        "ORDER BY inhparent,inhrelid,inhseqno",
        (ids, ids),
        ("inhrelid", "inhparent", "inhseqno", "inhdetachpending"),
    )
    result = {
        "format": "extended-CI-source-reference-NOT-admission-v1",
        "legacy": raw,
        "acl_rows": acl,
        "relation_candidate": candidate,
        "inheritance_rows": inheritance,
    }
    validate_extended(result)
    require(len(canonical(result).encode()) <= legacy.MAX_RAW)
    return result


def validate_extended(value):
    require(
        type(value) is dict
        and set(value) == {"format", "legacy", "acl_rows", "relation_candidate", "inheritance_rows"}
    )
    require(value["format"] == "extended-CI-source-reference-NOT-admission-v1")
    raw = value["legacy"]
    require(type(raw) is dict and set(raw) == LEGACY_KEYS)
    require(raw["runtime_authority"] is raw["permission_acceptance"] is False)
    legacy.semantic_reference(raw)  # Complete old declaration/identity schema.
    for obj in raw["objects"]:
        require(set(obj) == {"catalog", "oid", "row"} and obj["catalog"] in legacy.FIELDS)
        oid(obj["oid"])
        require(type(obj["row"]["oid"]) is int and obj["row"]["oid"] == obj["oid"])
        for field in legacy.PRIVATE.get(obj["catalog"], {}):
            seal(obj["row"][field + "_seal"])
    objects = {(o["catalog"], o["oid"]): o["row"] for o in raw["objects"]}
    require(len(objects) == len(raw["objects"]))
    roles = {o["oid"] for o in raw["objects"] if o["catalog"] == "pg_authid"}
    expected = sorted(key for key in objects if key[0] in ACL)
    rows = value["acl_rows"]
    require(type(rows) is list and [(r["catalog"], r["oid"]) for r in rows] == expected)
    for row in rows:
        require(
            type(row) is dict
            and set(row) == {"catalog", "oid", "raw_acl_is_null", "atoms", "reader_execute"}
        )
        cat = row["catalog"]
        oid(row["oid"])
        require(type(row["raw_acl_is_null"]) is bool)
        require(row["raw_acl_is_null"] is (objects[(cat, row["oid"])][ACL[cat][0]] is None))
        require(
            type(row["reader_execute"]) is bool
            if cat == "pg_proc"
            else row["reader_execute"] is None
        )
        require(type(row["atoms"]) is list)
        for atom in row["atoms"]:
            require(type(atom) is list and len(atom) == 4)
            oid(atom[0])
            oid(atom[1], public=True)
            require(atom[0] in roles and (atom[1] == 0 or atom[1] in roles))
            require(type(atom[2]) is str and atom[2] in ACL[cat][3] and type(atom[3]) is bool)
        require(
            row["atoms"] == sorted(row["atoms"])
            and len({tuple(a) for a in row["atoms"]}) == len(row["atoms"])
        )
    candidate = value["relation_candidate"]
    require(type(candidate) is list)
    for row in candidate:
        require(type(row) is dict and set(row) == set(inventory.CANDIDATE_COLUMNS))
        oid(row["oid"])
        oid(row["owner"])
        source = objects[("pg_class", row["oid"])]
        ns = objects[("pg_namespace", source["relnamespace"])]
        require(
            (row["nspname"], row["relname"], row["relkind"], row["owner"])
            == (ns["nspname"], source["relname"], source["relkind"], source["relowner"])
        )
        seal(row["definition"])
        require(row["definition"] == source["view_definition_seal"])
    require([r["oid"] for r in candidate] == sorted({r["oid"] for r in candidate}))
    roots = {
        key[1]
        for key, row in objects.items()
        if key[0] == "pg_class"
        and row["relkind"] in ("r", "p", "v", "m")
        and objects[("pg_namespace", row["relnamespace"])]["nspname"] in legacy.SCHEMAS
    }
    require(roots <= {r["oid"] for r in candidate})
    inheritance = value["inheritance_rows"]
    require(type(inheritance) is list)
    for row in inheritance:
        require(
            type(row) is dict
            and set(row) == {"inhrelid", "inhparent", "inhseqno", "inhdetachpending"}
        )
        for field in ("inhrelid", "inhparent"):
            oid(row[field])
            require(("pg_class", row[field]) in objects)
        require(
            type(row["inhseqno"]) is int
            and row["inhseqno"] > 0
            and type(row["inhdetachpending"]) is bool
        )
    order = [(r["inhparent"], r["inhrelid"], r["inhseqno"]) for r in inheritance]
    require(order == sorted(set(order)))


def portable_source_reference(value):
    validate_extended(value)
    raw = value["legacy"]
    portable = legacy.semantic_reference(raw)
    for key in (
        "raw_reference_sha256",
        "raw_document_must_be_retained",
        "opaque_node_tree_seals_are_instance_bound",
        "portable_admission_not_established",
    ):
        portable.pop(key)
    identities = {(r["catalog"], r["oid"], r["subid"]): r["identity"] for r in raw["identities"]}
    require(
        len(
            {
                (o["catalog"], canonical(identities[(o["catalog"], o["oid"], 0)]))
                for o in raw["objects"]
            }
        )
        == len(raw["objects"])
    )

    def name(cat, value):
        if cat == "pg_authid" and value == 0:
            return {"pseudo_role": "PUBLIC"}
        require((cat, value, 0) in identities)
        return deepcopy(identities[(cat, value, 0)])

    acl = {}
    for row in value["acl_rows"]:
        item = {
            "raw_acl_is_null": row["raw_acl_is_null"],
            "reader_execute": row["reader_execute"],
            "atoms": sorted(
                [[name("pg_authid", a), name("pg_authid", b), p, g] for a, b, p, g in row["atoms"]],
                key=canonical,
            ),
        }
        acl[(row["catalog"], canonical(name(row["catalog"], row["oid"])))] = item
    for obj in portable["objects"]:
        cat, declaration = obj["catalog"], obj["declaration"]
        if cat in ACL:
            declaration.pop(ACL[cat][0])
            declaration["canonical_ACL"] = acl[(cat, canonical(obj["identity"]))]
        shapes = {}
        for field in OPAQUE.get(cat, ()):
            item = declaration.pop(field)
            seal(item)
            counterpart = declaration[DEPARSE[cat]]
            seal(counterpart)
            require(item["is_null"] or not counterpart["is_null"])
            shapes[field] = {"is_null": item["is_null"]}
        if shapes:
            declaration["instance_expression_shapes"] = shapes
    portable["objects"].sort(key=canonical)
    portable["relation_candidate"] = sorted(
        [
            {
                "identity": name("pg_class", r["oid"]),
                "owner": name("pg_authid", r["owner"]),
                **{k: deepcopy(v) for k, v in r.items() if k not in ("oid", "owner")},
            }
            for r in value["relation_candidate"]
        ],
        key=canonical,
    )
    portable["inheritance_rows"] = sorted(
        [
            {
                "child": name("pg_class", r["inhrelid"]),
                "parent": name("pg_class", r["inhparent"]),
                "sequence": r["inhseqno"],
                "detach_pending": r["inhdetachpending"],
            }
            for r in value["inheritance_rows"]
        ],
        key=canonical,
    )
    portable["format"] = "portable-CI-source-expectation-NOT-live-admission-v1"
    return portable


def named_envelope(payload, *, identity):
    profile = payload["profile"]
    require(profile in PROFILES)
    return [
        line.replace(legacy.MARKER, MARKERS[profile], 1)
        for line in legacy.reference_envelope(payload, identity=identity)
    ]


def extract_named(lines, profile):
    require(profile in PROFILES)
    selected = []
    for line in lines:
        if "SOURCE_SEMANTIC_REFERENCE_" in line and legacy.MARKER not in line:
            require(any(marker in line for marker in MARKERS.values()))
        if MARKERS[profile] in line:
            selected.append(line.replace(MARKERS[profile], legacy.MARKER, 1))
    payload = legacy.extract_envelope(selected)
    require(payload["profile"] == profile and payload["variant_authority_sha256"] == AUTHORITY_SHA)
    require(payload["runtime_authority"] is payload["permission_acceptance"] is False)
    require(
        payload["portable_sha256"]
        == digest(canonical(portable_source_reference(payload["raw"])).encode())
    )
    return payload


class DDLAbortUnconfirmed(RuntimeError):
    """No source variant output after an unconfirmed abort/commit."""


@contextmanager
def ddl_transaction(conn):
    require(
        conn.autocommit is True and conn.info.transaction_status == legacy.TransactionStatus.IDLE
    )
    require(conn.info.dbname.startswith("mfdata_reference_") and conn.info.user == "mfdata_admin")
    require(
        threading.current_thread() is threading.main_thread()
        and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    )
    previous = signal.getsignal(signal.SIGALRM)
    bounded = legacy.Bounded(conn, time.monotonic() + 15)

    def expired(*_):
        raise legacy.WallSpent("source variant DDL wall spent")

    def control(text, tag):
        with bounded.execute(text, prepare=False) as cursor:
            require(cursor.statusmessage == tag)

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 15)
    begun = commit_sent = rollback_sent = False
    try:
        begun = True
        control("BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE", "BEGIN")
        for setting in (
            "search_path=pg_catalog",
            "TimeZone='UTC'",
            "statement_timeout='5s'",
            "lock_timeout='5s'",
            "transaction_timeout='15s'",
        ):
            control("SET LOCAL " + setting, "SET")
        context = query(
            bounded,
            "SELECT current_database() AS database,current_user AS actor,session_user AS principal",
            (),
            ("database", "actor", "principal"),
        )
        require(
            context
            == [
                {"database": conn.info.dbname, "actor": "mfdata_admin", "principal": "mfdata_admin"}
            ]
        )
        yield bounded
        bounded.remaining()
        commit_sent = True
        control("COMMIT", "COMMIT")
        require(conn.info.transaction_status == legacy.TransactionStatus.IDLE)
    except BaseException as error:
        if begun:
            if (
                commit_sent
                or rollback_sent
                or isinstance(error, legacy.WallSpent)
                or time.monotonic() >= bounded.deadline
            ):
                raise DDLAbortUnconfirmed(
                    "source variant unconfirmed; no further SQL or output"
                ) from None
            rollback_sent = True
            try:
                control("ROLLBACK", "ROLLBACK")
                require(conn.info.transaction_status == legacy.TransactionStatus.IDLE)
            except BaseException:
                raise DDLAbortUnconfirmed("source variant abort unconfirmed; no retry") from None
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class DDLConnection:
    """Drain each fixed batch completely, including errors after first result."""

    def __init__(self, bounded, root):
        self.bounded = bounded
        self.root = Path(root)
        self.stage = 0
        self.definition = None
        self.tags = []

    def execute(self, statement, parameters=(), *, prepare=False):
        require(prepare is False)
        v2 = (self.root / "sql/optional/amc_book_current_projection_v2.sql.inc").read_text()
        core = (self.root / "sql/optional/amc_book_core_projection_v1.sql.inc").read_text()
        select = "SELECT pg_get_viewdef(%s::regclass,true) AS definition"
        if self.stage == 2:
            require(statement == select and parameters == ("mf_live_facts.resolved",))
            values = query(self.bounded, statement, parameters, ("definition",))
            require(len(values) == 1 and type(values[0]["definition"]) is str)
            self.definition = values[0]["definition"]
            self.stage += 1

            class Result:
                def fetchone(inner):
                    return {"definition": self.definition}

            return Result()
        expected = (
            v2
            if self.stage == 0
            else core
            if self.stage == 1
            else "CREATE OR REPLACE VIEW mf.current_fund_facts AS " + self.definition
            if self.stage == 3
            else None
        )
        require(statement == expected and not parameters)
        tags = []
        with self.bounded.execute(statement, prepare=False) as cursor:
            while True:
                self.bounded.remaining()
                tags.append(cursor.statusmessage)
                require(cursor.description is None)
                if not cursor.nextset():
                    break
        require(tags == (["CREATE VIEW", "DO"] if self.stage == 1 else ["CREATE VIEW"]))
        self.tags.extend(tags)
        self.stage += 1


def install_book_variant(conn, root):
    variant_authority(root)
    from mfdataindia.enrichment import book_core_projection

    with ddl_transaction(conn) as bounded:
        gate = DDLConnection(bounded, root)
        gate.execute(
            (Path(root) / "sql/optional/amc_book_current_projection_v2.sql.inc").read_text()
        )
        book_core_projection.install(gate, Path(root))
        require(
            gate.stage == 4 and gate.tags == ["CREATE VIEW", "CREATE VIEW", "DO", "CREATE VIEW"]
        )
