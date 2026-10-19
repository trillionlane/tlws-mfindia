"""Read-only candidate proof followed by an exact, short, locked publication.

Only the registered source-method revision of an identical projection is
eligible. No externally supplied certificate bypasses the preparation step.
"""

import hashlib

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus

from mfdataindia.enrichment import (
    activation_fence, book_core_projection, book_preservation_v2 as preservation,
    book_projection, book_sources, book_store, book_upgrade,
    current_facts_store as current,
)
from mfdataindia.enrichment.current_graph_read import DEPENDENCIES, ORDER, QUOTED, reuse
from mfdataindia.enrichment.monthly_projection import owner, seal_configuration
from mfdataindia.review.policy import fingerprint


def substitute_configuration(definition):
    pieces = QUOTED.split(definition.rstrip().removesuffix(";"))
    import re

    pattern = re.compile(r"(?<![\w.])mf_live_facts\.configuration(?![\w.])")
    count = 0
    for index in range(0, len(pieces), 2):
        pieces[index], changed = pattern.subn("activation_configuration", pieces[index])
        count += changed
    if count != 1:
        raise ValueError("exact single active-authority dependency required")
    return "".join(pieces)


class Preview:
    """Statement-local copies of sealed views, not independently coded formulas."""

    def __init__(self, prior, policy):
        self.prior = prior
        self.policy = policy
        self.version_body = substitute_configuration(prior["view_definitions"]["field_versions"])
        for name in ORDER[1:]:
            if any("mf_live_facts.configuration" in piece
                   for piece in QUOTED.split(prior["view_definitions"][name])[::2]):
                raise ValueError("unhandled active-authority dependency")

    def graph(self, conn, codes, *, complete=False):
        if (not isinstance(codes, list) or not 1 <= len(codes) <= 4096
                or any(type(c) is not int or not 0 < c <= 2147483647 for c in codes)
                or codes != sorted(set(codes))):
            raise ValueError("whole ordered candidate population required")
        ctes = [sql.SQL("activation_configuration AS MATERIALIZED (SELECT {}::integer AS generation,{}::text AS policy_hash)").format(
            sql.Literal(self.prior["generation"] + 1), sql.Literal(fingerprint(self.policy))),
            sql.SQL("field_versions AS MATERIALIZED (SELECT * FROM ({}) candidate_versions WHERE code=ANY(%s))").format(sql.SQL(self.version_body))]
        for name in ORDER[1:] if complete else ORDER[1:3]:
            ctes.append(sql.SQL("{} AS MATERIALIZED ({})").format(
                sql.Identifier(name), sql.SQL(reuse(self.prior["view_definitions"][name], DEPENDENCIES[name]))))
        return sql.SQL("WITH ") + sql.SQL(",").join(ctes) + sql.SQL(" ")

    def fields(self, conn, code):
        rows = conn.execute(self.graph(conn, [code]) + sql.SQL("""SELECT h.field,
            h.distinct_values,h.usable,s.as_of,s.value,s.sources
            FROM field_heads h LEFT JOIN selected_fields s USING(code,field) ORDER BY h.field"""), ([code],)).fetchall()
        return {r["field"]: {
            "state": "available" if r["usable"] and r["distinct_values"] == 1 else "withheld",
            "value": r["value"], "disclosure_as_of": str(r["as_of"]), "sources": r["sources"] or [],
        } for r in rows}


def _request(root, reviewed, expected):
    version = book_sources.CURRENT_VERSION
    if version < 5 or preservation.revised_keys(version) == []:
        raise ValueError("registered source-method revision required for fenced activation")
    projection = book_projection.PROJECTIONS[version]
    if projection != book_projection.PROJECTIONS[version - 1]:
        raise ValueError("fenced activation cannot change installed projection semantics")
    if hashlib.sha256((root / "sql/optional" / projection[0]).read_bytes()).hexdigest() != projection[1]:
        raise ValueError("book projection differs from reviewed pin")
    book_core_projection.source(root)
    return {"format": "official-book-fenced-activation-v1", "prior_hash": expected,
            "reviewed_hash": fingerprint(reviewed), "source_version": version,
            "source_hash": fingerprint(book_sources.configuration(version)),
            "policy_hash": fingerprint(book_sources.current_policy(version)),
            "projection": list(projection), "core_projection_hash": book_core_projection.SHA256}


def _prepare(conn, root, reviewed, expected):
    request = _request(root, reviewed, expected)
    # Separate connection: the READ COMMITTED owner's transaction is never
    # stretched, committed by this helper, or given the inspection timeouts.
    # Uncommitted caller writes cannot become certified inputs in this snapshot.
    # ConnectionInfo.dsn deliberately excludes the password. Pass libpq's
    # in-memory credential explicitly; never serialize it into any evidence.
    with psycopg.connect(conn.info.dsn, password=conn.info.password, row_factory=dict_row) as reader:
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        reader.execute("SET LOCAL statement_timeout='300s'")
        reader.execute("SET LOCAL transaction_timeout='420s'")
        who = reader.execute("SELECT session_user AS name,current_user=session_user AS direct").fetchone()
        if not who["direct"] or who["name"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct projection owner required for candidate preparation")
        current.verify_acl(reader)
        book_store.verify_acl(reader, principal="mfdata_verified_reader")
        prior = dict(current.configuration(reader))
        if fingerprint(prior) != expected:
            raise ValueError("approved prior configuration changed before preparation")
        if book_store.installed_configuration(reader)["document"] != book_sources.configuration():
            raise ValueError("latest reviewed source authority required")
        if not current.projection_state(reader):
            raise ValueError("accepted active graph required")
        fence = activation_fence.capture(reader)
        if prior["policy_hash"] == request["policy_hash"]:
            book_projection.check(reader, reviewed)
            return {"request": request, "prior": prior, "fence": fence, "already_active": True}
        if prior["policy_hash"] != fingerprint(book_sources.current_policy(book_sources.CURRENT_VERSION - 1)):
            raise ValueError("accepted previous active book baseline required")
        status = book_store.status(reader)
        if any(not s["population_matches_latest_success"] or len(s["periods"]) != 2 for s in status["sources"]):
            raise ValueError("complete current two-period source receipts required")
        preview = Preview(prior, book_sources.current_policy())
        before = preservation.capture(reader, book_sources.CURRENT_VERSION - 1)
        after = preservation.capture(reader, book_sources.CURRENT_VERSION - 1, preview=preview)
        checked = preservation.compare(before, after)
        book_projection.check(reader, reviewed, preview=preview)
        upgrade = book_upgrade.prior_upgrade(reader, book_sources.CURRENT_VERSION)
        if upgrade["prior_current_configuration_hash"] != fingerprint(prior):
            raise ValueError("source upgrade reviewed against a different prior graph")
        return {"request": request, "prior": prior, "fence": fence, "already_active": False,
                "before": before, "after": after, "checked": checked, "upgrade": upgrade}


def activate(conn, root, reviewed, *, expected_configuration_hash):
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("fenced activation requires an idle connection; caller must finish its own transaction")
    with conn.transaction():
        who = conn.execute("SELECT session_user AS name,current_user=session_user AS direct").fetchone()
        if not who["direct"] or who["name"] not in ("postgres", "mfdata_admin"):
            raise ValueError("direct projection owner required")
        if conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] != "read committed":
            raise ValueError("short activation requires READ COMMITTED")
    prepared = _prepare(conn, root, reviewed, expected_configuration_hash)
    with conn.transaction():
        owner(conn)
        if prepared["request"] != _request(root, reviewed, expected_configuration_hash):
            raise ValueError("activation request changed after preparation")
        fence_hash = activation_fence.lock_and_verify(conn, prepared["fence"])
        current.verify_acl(conn)
        book_store.verify_acl(conn, principal="mfdata_verified_reader")
        prior = prepared["prior"]
        if dict(current.configuration(conn)) != prior:
            raise ValueError("approved prior configuration changed before activation")
        if prepared["already_active"]:
            return {"status": "ALREADY_ACTIVE", "source_rows_changed": 0,
                    "preparation_fence_sha256": fence_hash}
        evidence = {"request": prepared["request"], "dependency_fence_sha256": fence_hash,
                    "before_sha256": prepared["before"]["sha256"],
                    "after_sha256": prepared["after"]["sha256"], "preservation": prepared["checked"]}
        evidence["sha256"] = fingerprint(evidence)
        intent = book_projection.transition(conn, {
            "format": "official-amc-book-transition-v1", "mode": "activate",
            "prior_configuration": prior,
            "prior_core_definition": current.view_definition(conn, "mf.current_fund_facts"),
            "to_generation": prior["generation"] + 1,
            "source_upgrade_intent": prepared["upgrade"],
            "target_value_preservation": prepared["before"]["revised"],
            "fenced_preparation": evidence,
        })
        # Same views, same policy semantics: only the registered source authority
        # becomes active. Avoid exclusive hot-view DDL and expensive graph reads.
        seal_configuration(conn, intent["to_generation"], prior["baseline_definition"],
                           prepared["request"]["policy_hash"], prior["view_definitions"])
        current.verify_acl(conn)
        return {"status": "ACTIVATED", "transition_id": intent["transition_id"],
                "generation": intent["to_generation"], "source_rows_changed": 0,
                "target_value_preservation": prepared["checked"]["revised_source_preservation"],
                "fenced_preparation": evidence}
