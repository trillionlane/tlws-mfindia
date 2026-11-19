"""Production SCH44 source reference, separate from FULL Scheme Master promotion.

File-only preparation recomputes exact identities using the reviewed validator.
An independently reviewed candidate hash is required at the mutation boundary.
No transport, credentials, eligibility decision, or canonical mapping writes.
"""

from collections import Counter
from datetime import datetime
from hashlib import sha256
import re
from uuid import uuid5

from psycopg.types.json import Jsonb

from mfdataindia.datasets import enrollment, policy as datasets
from . import contract as c, sch44, sch44_reconcile as identity, store
from .schema import HEADERS44

PROFILE = "nse-sch44-source-native-v1"
DISPOSITIONS = (
    "MF_EXACT",
    "MF_HELD",
    "MF_UNMATCHED",
    "SIF",
    "OTHER_VERIFIED_OUT_OF_SCOPE",
    "UNCLASSIFIED",
)
MIGRATION = store.MIGRATION.with_name("049_nse_sch44_source_native.sql")
PROVENANCE_KEYS = {
    "channel",
    "filename",
    "retrieved_at_utc",
    "object_uri",
    "object_generation",
    "sha256",
    "byte_length",
    "row_count",
}


def install(conn):
    """Explicit existing direct-owner path; caller owns the outer transaction."""
    store.install(conn)
    expected = sha256(MIGRATION.read_bytes()).hexdigest()
    present = conn.execute("SELECT to_regclass('mf_nse.source_configuration') AS value").fetchone()[
        "value"
    ]
    if not present:
        conn.execute(store.MIGRATION.with_name("048_nse_sch44_uat_publication.sql").read_text())
        conn.execute(MIGRATION.read_text())
        conn.execute("INSERT INTO mf_nse.source_configuration VALUES(true,%s)", (expected,))
    _configuration(conn)
    enrollment.enroll(
        conn,
        "mf_nse",
        "source_routes",
        classification="fund_research",
        title="NSE SCH44 source routes; transaction eligibility not established",
        key_columns=["snapshot_id", "source_line"],
    )
    result = enrollment.reconcile(conn)
    own = next(
        r
        for r in result["results"]
        if r["dataset_id"] == datasets.dataset_id("mf_nse", "source_routes")
    )
    c.require(own["permissions_verified"], "READER_RECONCILIATION_FAILED")
    return own


def _configuration(conn):
    row = conn.execute(
        "SELECT definition_hash FROM mf_nse.source_configuration WHERE singleton"
    ).fetchone()
    c.require(
        row and row["definition_hash"] == sha256(MIGRATION.read_bytes()).hexdigest(),
        "SOURCE_INSTALLATION_DIFFERS",
    )


def prepare(
    raw,
    provenance,
    catalogue,
    earlier_source,
    amfi_first,
    amfi_second,
    product_review,
    product_review_sha256,
):
    """Retain all rows; a unique ISIN alone never becomes an exact link.

    Product decisions are separately hash-pinned reviewed evidence. Unknown rows
    remain unclassified. Names/suffixes never classify a product here.
    """
    c.require(
        type(provenance) is dict and set(provenance) == PROVENANCE_KEYS, "SOURCE_PROVENANCE_INVALID"
    )
    rows = sch44.parse(raw)
    c.require(provenance["channel"] == "NSE_PRODUCTION_WEBSITE", "PRODUCTION_SOURCE_REQUIRED")
    c.require(type(provenance["filename"]) is str, "SOURCE_FILENAME_INVALID")
    c.require(type(provenance["retrieved_at_utc"]) is str, "SOURCE_RETRIEVAL_INVALID")
    match = re.fullmatch(r"NSE_NSEINVEST_ALL_([0-9]{8})\.txt", provenance["filename"], re.I)
    c.require(match is not None, "SOURCE_FILENAME_INVALID")
    try:
        generated = datetime.strptime(match[1], "%d%m%Y").date().isoformat()
        stamp = datetime.fromisoformat(provenance["retrieved_at_utc"].replace("Z", "+00:00"))
        c.require(stamp.utcoffset() is not None, "SOURCE_RETRIEVAL_INVALID")
    except (ValueError, TypeError):
        raise c.ImportError("SOURCE_RETRIEVAL_INVALID") from None
    c.require(
        type(provenance["object_uri"]) is str
        and re.fullmatch(r"gs://[a-z0-9._-]+/[A-Za-z0-9/_.-]+", provenance["object_uri"])
        and type(provenance["object_generation"]) is str
        and re.fullmatch("[1-9][0-9]*", provenance["object_generation"]),
        "SOURCE_ARCHIVE_GENERATION_REQUIRED",
    )
    c.require(
        provenance["sha256"] == sha256(raw).hexdigest()
        and type(provenance["byte_length"]) is int
        and provenance["byte_length"] == len(raw)
        and type(provenance["row_count"]) is int
        and provenance["row_count"] == len(rows),
        "SOURCE_BYTES_DIFFER",
    )
    c.require(
        type(product_review_sha256) is str
        and c.HASH.fullmatch(product_review_sha256)
        and c.digest(product_review) == product_review_sha256
        and type(product_review) is dict
        and set(product_review) == {"source_sha256", "rows"}
        and product_review["source_sha256"] == provenance["sha256"],
        "PRODUCT_REVIEW_UNTRUSTED",
    )
    decisions = product_review["rows"]
    c.require(
        type(decisions) is list
        and len(decisions) == len(rows)
        and all(type(r) is dict for r in decisions)
        and [r.get("source_line") for r in decisions] == list(range(2, len(rows) + 2)),
        "PRODUCT_REVIEW_COVERAGE_DIFFERS",
    )
    report = identity.report(raw, catalogue, earlier_source, amfi_first, amfi_second)
    snapshot, funds = identity._catalogue(catalogue)
    resolved = []
    for line, ((cells, holds), link, decision) in enumerate(
        zip(rows, report["rows"], decisions), 2
    ):
        c.require(
            set(decision) == {"source_line", "disposition", "reason", "evidence_sha256"}
            and decision["disposition"] in DISPOSITIONS
            and type(decision["reason"]) is str
            and c.NAME.fullmatch(decision["reason"])
            and type(decision["evidence_sha256"]) is str
            and c.HASH.fullmatch(decision["evidence_sha256"]),
            "PRODUCT_REVIEW_INVALID",
        )
        product = decision["disposition"]
        # Independent identity evidence is authoritative for links. A product
        # review may withhold a route, but can never promote an identity.
        status = (
            (
                "MF_EXACT"
                if link["status"] == "EXACT"
                else "MF_HELD"
                if link["status"] == "AMBIGUOUS"
                else "MF_UNMATCHED"
            )
            if product in {"MF_EXACT", "MF_HELD", "MF_UNMATCHED"}
            else product
        )
        verified = link if status == "MF_EXACT" else None
        resolved.append(
            {
                "source_line": line,
                "cells": cells,
                "field_holds": {str(k): v for k, v in holds.items()},
                "disposition": status,
                "reason": link["reason"] if verified else decision["reason"],
                "product_evidence_sha256": decision["evidence_sha256"],
                "link": verified,
            }
        )
    metadata = {
        **provenance,
        "format": PROFILE,
        "schema": sch44.PROFILE,
        "generation_date": generated,
        "source_date": generated,  # shared artifact compatibility, not a business-date claim
        "generation_date_basis": "FILENAME",
        "effective_business_date": None,
        "member_scope": "UNVERIFIED",
        "transaction_eligibility": "NOT_ESTABLISHED",
        "catalogue_version": snapshot["dataset_version"],
        "catalogue_sha256": sha256(catalogue).hexdigest(),
        "mapping_input_hash": c.digest([funds[k] for k in sorted(funds)]),
        "identity_evidence": report["input_sha256"],
        "product_review_sha256": product_review_sha256,
        "counts": dict(sorted(Counter(r["disposition"] for r in resolved).items())),
    }
    return {"metadata": metadata, "rows": resolved}


def snapshot_key(plan):
    """Retrieval observations do not create duplicate source business rows."""
    metadata = {
        k: v
        for k, v in plan["metadata"].items()
        if k not in {"retrieved_at_utc", "object_uri", "object_generation"}
    }
    return c.digest({"metadata": metadata, "rows": plan["rows"]})


def _stage(conn, parsed, walk, operation_id, expected_head, **unused):
    store._identity(conn)
    store._configuration(conn)
    _configuration(conn)
    plan = walk
    meta = plan["metadata"]
    conn.execute("LOCK TABLE mf.funds,mf.amcs,mf.nav_history IN ACCESS SHARE MODE")
    head = conn.execute(
        "SELECT snapshot_id,generation FROM mf_nse.source_head WHERE singleton FOR UPDATE"
    ).fetchone()
    funds = conn.execute(store.FUNDS).fetchall()
    version = conn.execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()
    c.require(
        c.digest(funds) == meta["mapping_input_hash"]
        and str(version["dataset_version"]) == meta["catalogue_version"],
        "MAPPING_INPUT_DIFFERS",
    )
    work = snapshot_key(plan)
    prior = conn.execute(
        "SELECT snapshot_id,report FROM mf_nse.import_attempts WHERE work_key=%s", (work,)
    ).fetchone()
    c.require(
        not conn.execute(
            "SELECT 1 FROM mf_nse.observations WHERE operation_id=%s", (operation_id,)
        ).fetchone(),
        "OPERATION_SPENT",
    )
    if prior:
        conn.execute(
            "INSERT INTO mf_nse.observations(operation_id,snapshot_id,manifest,disposition) VALUES(%s,%s,%s,%s)",
            (operation_id, prior["snapshot_id"], Jsonb(meta), "UNCHANGED"),
        )
        return {
            **prior["report"],
            "operation_id": operation_id,
            "state": "UNCHANGED",
            "head_advanced": False,
        }
    c.require(
        (str(head["snapshot_id"]) if head["snapshot_id"] else None) == expected_head, "HEAD_CHANGED"
    )
    before = store._fingerprint(conn)
    snapshot_id = uuid5(store.NAMESPACE, work)
    artifact_id = uuid5(store.NAMESPACE, meta["sha256"])
    result = {
        "operation_id": operation_id,
        "snapshot_id": str(snapshot_id),
        "state": "PUBLISHED_WITH_LIMITATIONS",
        "source_rows": len(plan["rows"]),
        "counts": meta["counts"],
        "head_advanced": True,
        "source_sha256": meta["sha256"],
        "transaction_eligibility": "NOT_ESTABLISHED",
    }
    conn.execute(
        "INSERT INTO mf_nse.source_artifacts(artifact_id,content_hash,original_bytes,manifest) VALUES(%s,%s,%s,%s) ON CONFLICT(content_hash) DO NOTHING",
        (artifact_id, meta["sha256"], parsed, Jsonb(meta)),
    )
    conn.execute(
        "INSERT INTO mf_nse.import_attempts(operation_id,work_key,snapshot_id,state,report) VALUES(%s,%s,%s,%s,%s)",
        (operation_id, work, snapshot_id, result["state"], Jsonb(result)),
    )
    conn.execute(
        """INSERT INTO mf_nse.snapshots(snapshot_id,artifact_id,operation_id,source_date,schema_version,
        semantic_hash,mapping_input_hash,crosswalk,manifest,source_rows,exposed_rows,withheld_rows)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            snapshot_id,
            artifact_id,
            operation_id,
            meta["generation_date"],
            PROFILE,
            work,
            meta["mapping_input_hash"],
            Jsonb({}),
            Jsonb(meta),
            len(plan["rows"]),
            meta["counts"].get("MF_EXACT", 0),
            len(plan["rows"]) - meta["counts"].get("MF_EXACT", 0),
        ),
    )
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO mf_nse.sch_rows VALUES(%s,%s,%s,%s,%s)",
            [
                (
                    snapshot_id,
                    r["source_line"] - 1,
                    Jsonb(r["cells"]),
                    Jsonb({}),
                    Jsonb(r["field_holds"]),
                )
                for r in plan["rows"]
            ],
        )
        cursor.executemany(
            "INSERT INTO mf_nse.variant_mappings VALUES(%s,%s,%s,%s,%s)",
            [
                (
                    snapshot_id,
                    r["source_line"] - 1,
                    r["disposition"],
                    r["reason"],
                    Jsonb([r["link"]] if r["link"] else []),
                )
                for r in plan["rows"]
            ],
        )
    conn.execute(
        "UPDATE mf_nse.source_head SET snapshot_id=%s,generation=generation+1 WHERE singleton",
        (snapshot_id,),
    )
    observed = conn.execute(
        "SELECT source_line,original_cells,field_holds,disposition,reason,candidates FROM mf_nse.source_routes ORDER BY source_line"
    ).fetchall()
    expected = [
        {
            "source_line": r["source_line"],
            "original_cells": list(r["cells"]),
            "field_holds": {str(k): v for k, v in r["field_holds"].items()},
            "disposition": r["disposition"],
            "reason": r["reason"],
            "candidates": [r["link"]] if r["link"] else [],
        }
        for r in plan["rows"]
    ]
    c.require(observed == expected, "SOURCE_FULL_READBACK_DIFFERS")
    retained = conn.execute(
        "SELECT original_bytes FROM mf_nse.source_artifacts WHERE artifact_id=%s", (artifact_id,)
    ).fetchone()
    c.require(bytes(retained["original_bytes"]) == parsed, "SOURCE_ORIGINAL_DIFFERS")
    c.require(store._fingerprint(conn) == before, "CANONICAL_PRESERVATION_FAILED")
    conn.execute(
        "INSERT INTO mf_nse.observations(operation_id,snapshot_id,manifest,disposition) VALUES(%s,%s,%s,%s)",
        (operation_id, snapshot_id, Jsonb(meta), result["state"]),
    )
    return result


def publish(conn, raw, plan, reviewed_sha256, operation_id, *, expected_head):
    """One atomic transaction, existing ambiguity handling, no retry or grants.

    `reviewed_sha256` is supplied by trusted operator composition from the
    independently reviewed candidate, never selected from an HTTP caller.
    """
    c.operation(operation_id)
    c.require(
        type(reviewed_sha256) is str
        and c.HASH.fullmatch(reviewed_sha256)
        and c.digest(plan) == reviewed_sha256,
        "SOURCE_CANDIDATE_UNREVIEWED",
    )
    c.require(
        plan["metadata"]["sha256"] == sha256(raw).hexdigest()
        and [tuple(r["cells"]) for r in plan["rows"]] == [cells for cells, _ in sch44.parse(raw)],
        "SOURCE_BYTES_DIFFER",
    )
    return store._publish_transaction(
        conn, raw, plan, operation_id, expected_head=expected_head, stage=_stage
    )


def fund_section(conn, code):
    """Existing private core API boundary; only verified, still-current links."""
    empty = {
        "version": PROFILE,
        "status": "NO_VERIFIED_LINK",
        "routes": [],
        "transaction_eligibility": "NOT_ESTABLISHED",
    }
    if not conn.execute("SELECT to_regclass('mf_nse.source_routes') AS value").fetchone()["value"]:
        return {**empty, "status": "NOT_INSTALLED"}
    rows = conn.execute(
        """SELECT r.* FROM mf_nse.source_routes r JOIN mf.funds f
        ON f.amfi_scheme_code=%s JOIN mf.amcs a USING(amc_id)
        WHERE r.disposition='MF_EXACT' AND r.candidates->0->>'amfi_code'=f.amfi_scheme_code::text
          AND r.candidates->0->>'amfi_issuer'=a.amfi_amc_name
          AND r.candidates->0->>'amfi_plan'=f.plan_type
          AND r.candidates->0->>'amfi_option'=f.option_type AND f.in_scope
          AND ((r.candidates->0->>'amfi_isin_slot'='isin_growth_or_div_payout'
                AND r.original_cells->>4=f.isin_growth_or_div_payout)
            OR (r.candidates->0->>'amfi_isin_slot'='isin_div_reinvest'
                AND r.original_cells->>4=f.isin_div_reinvest))
        AND NOT EXISTS (SELECT 1 FROM mf.funds other
              WHERE other.amfi_scheme_code<>f.amfi_scheme_code
                AND (other.isin_growth_or_div_payout=r.original_cells->>4
                     OR other.isin_div_reinvest=r.original_cells->>4))
        ORDER BY r.source_line""",
        (code,),
    ).fetchall()
    routes = []
    for row in rows:
        link = row["candidates"][0]
        routes.append(
            {
                "source_line": row["source_line"],
                "snapshot_id": str(row["snapshot_id"]),
                "source_sha256": row["source_sha256"],
                "generation_date": row["generation_date"].isoformat(),
                "generation_date_basis": "FILENAME",
                "retrieved_at_utc": row["retrieved_at_utc"],
                "filename": row["filename"],
                "layout": sch44.PROFILE,
                "catalogue_version": row["catalogue_version"],
                "link_status": "VERIFIED_EXACT",
                "amfi_isin_slot": link["amfi_isin_slot"],
                "amfi_plan": link["amfi_plan"],
                "amfi_option": link["amfi_option"],
                "flag_slot_disagreement": link["flag_slot_disagreement"],
                "fields": dict(zip(HEADERS44, row["original_cells"])),
                "field_holds": row["field_holds"],
            }
        )
    return {**empty, "status": "VERIFIED_LINKS" if routes else "NO_VERIFIED_LINK", "routes": routes}
