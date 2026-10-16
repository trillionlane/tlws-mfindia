"""Bounded official monthly refresh; immutable archive precedes atomic publication."""

import hashlib
import json
import re
from datetime import datetime, timezone

import psycopg
import requests
from psycopg.rows import dict_row

from mfdataindia.enrichment import (
    facts,
    monthly_facts,
    monthly_schemas,
    monthly_store,
    refresh_acquisition,
    refresh_archive,
    refresh_policy,
    refresh_store,
)
from mfdataindia.enrichment.schemas import Intake
from mfdataindia.review.policy import fingerprint
from mfdataindia.store.locks import acquire_nav_data_operation_lock

JOB = "tlws-mf-official-facts-refresh-dev"
IDENTITY_DOCUMENT = "76888d30ebbb0c10f3d39d27d3e9734d2d9db17fd2642fd8b388102f53b7be50"


def receipt(conn, execution):
    row = conn.execute(
        "SELECT document FROM mf_fact_refresh.runs WHERE execution_id=%s", (execution,)
    ).fetchone()
    return row["document"] if row else None


def publish(conn, *, execution, observed_at, catalogs, document, content, archived):
    refresh_store.verify_acl(conn)
    acquire_nav_data_operation_lock(conn)
    previous = receipt(conn, execution)
    if previous:
        return previous
    catalog_ids = [
        refresh_store.append_catalog(conn, c["source_url"], c["media_type"], c["content"])
        for c in catalogs
    ]
    # Only the landing and the chosen catalog establish the selected book;
    # retain a previous empty current-year catalog too, without counting it.
    selected_catalog_ids = [catalog_ids[0], catalog_ids[-1]]
    checksum = hashlib.sha256(content).hexdigest()
    detail, changes, as_of = refresh_acquisition.pages(content)
    if as_of > observed_at.date():
        raise ValueError("future disclosed facts are not usable")
    retained = conn.execute(
        "SELECT intake FROM mf_monthly_facts.sources WHERE content_hash=%s", (checksum,)
    ).fetchone()
    intake = (
        Intake.model_validate(retained["intake"])
        if retained
        else Intake(
            source_url=document.url,
            final_url=document.url,
            official_hosts=["mutualfund.adityabirlacapital.com"],
            amc=facts.AMC,
            observed_at=observed_at,
            document_as_of=as_of,
            media_type="application/pdf",
            expected_sha256=checksum,
            codes=sorted(monthly_facts.COHORT),
            use_basis="operator_reported_authorization",
            authority_reference="User-approved official AMC factual extraction for distributors; preserve originals; no factsheet redistribution",
        )
    )
    if intake.document_as_of != as_of:
        raise ValueError("retained original disclosure date differs")
    snapshots = monthly_store.publish(
        conn,
        monthly_schemas.Selection(
            intake=intake,
            identity_document_id=IDENTITY_DOCUMENT,
            detail_page=detail,
            changes_page=changes,
            identity_page=28,
        ),
        content,
    )
    ids = sorted(s["snapshot_id"] for s in snapshots)
    published = conn.execute(
        "SELECT 1 FROM mf_fact_refresh.publications WHERE content_hash=%s "
        "AND disclosure_as_of=%s AND snapshot_ids @> %s::jsonb AND %s::jsonb @> snapshot_ids",
        (checksum, as_of, json.dumps(ids), json.dumps(ids)),
    ).fetchone()
    gaps = sorted(
        {
            field
            for s in snapshots
            for field in (
                "benchmark_name",
                "fund_managers",
                "base_expense_ratio",
                "risk_level",
                "aum",
            )
            if s["fields"][field]["status"] != "accepted"
        }
    )
    return refresh_store.append_run(
        conn,
        {
            "format": "official-facts-refresh-run-v1",
            "execution_id": execution,
            "observed_at": observed_at.isoformat(),
            "policy_hash": fingerprint(refresh_policy.policy()),
            "mode": "scheduled_refresh",
            "status": "unchanged"
            if published
            else "completed_with_field_gaps"
            if gaps
            else "updated",
            "catalog_ids": selected_catalog_ids,
            "retained_catalog_ids": catalog_ids,
            "source_url": document.url,
            "publication_period": document.period,
            "content_hash": checksum,
            "disclosure_as_of": as_of.isoformat(),
            "snapshot_ids": ids,
            "archive": archived,
            "field_gaps": gaps,
            "verification_status": "needs_verification",
            "source_rows_changed": 0,
            "canonical_analytics_replaced": False,
            "all_amc_coverage": False,
        },
    )


def execute(
    dsn, execution, *, now=None, transport=refresh_acquisition.fetch, archive=refresh_archive.retain
):
    if not re.fullmatch(re.escape(JOB) + r"-[a-z0-9-]{1,40}", execution):
        raise ValueError("exact source refresh execution name required")
    observed = now or datetime.now(timezone.utc)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        refresh_store.verify_acl(conn)
        previous = receipt(conn, execution)
        if previous:
            return previous
    phase, archived = "discovery", None
    try:
        catalogs, document = refresh_acquisition.discover(observed, transport=transport)
        phase = "acquisition"
        book = transport(document.url, book=True)
        phase = "archive"
        with requests.Session() as session:
            session.trust_env = False
            archived = archive(session, book["content"])
        phase = "publication"
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET LOCAL statement_timeout='120s'")
            conn.execute("SET LOCAL transaction_timeout='240s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            result = publish(
                conn,
                execution=execution,
                observed_at=observed,
                catalogs=catalogs,
                document=document,
                content=book["content"],
                archived=archived,
            )
        return result
    except Exception as error:
        # Never retain source bodies, SQL, URLs from exceptions or credentials.
        # Reconcile an uncertain commit before attempting a failure receipt.
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            previous = receipt(conn, execution)
            if previous:
                return previous
            return refresh_store.append_run(
                conn,
                {
                    "format": "official-facts-refresh-run-v1",
                    "execution_id": execution,
                    "observed_at": observed.isoformat(),
                    "policy_hash": fingerprint(refresh_policy.policy()),
                    "mode": "scheduled_refresh",
                    "status": "failed",
                    "phase": phase,
                    "error_type": type(error).__name__
                    if type(error).__name__
                    in {
                        "ValueError",
                        "TimeoutError",
                        "Conflict",
                        "OperationalError",
                        "QueryCanceled",
                        "InsufficientPrivilege",
                        "ReadTimeout",
                        "ConnectionError",
                    }
                    else "RefreshError",
                    "archive": archived,
                    "source_rows_changed": 0,
                    "current_heads_advanced": False,
                    "all_amc_coverage": False,
                },
            )
