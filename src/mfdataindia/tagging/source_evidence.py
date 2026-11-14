"""Exact committed inputs for factual tagging and their ordered receipts."""
from __future__ import annotations

from datetime import date

from mfdataindia.review.policy import fingerprint
from . import policy

CATALOGUE_SQL = """SELECT f.amfi_scheme_code AS code,f.amc_id,a.amfi_amc_name AS amc,
 f.scheme_name,f.scheme_type,f.scheme_category,f.category_source,f.plan_type,f.plan_source,
 f.option_type,f.is_etf,f.in_scope,f.last_seen_in_source::text AS last_seen
 FROM mf.funds f JOIN mf.amcs a ON a.amc_id=f.amc_id
 ORDER BY f.amfi_scheme_code LIMIT 50001"""

FAMILY_SQL = """SELECT f.amfi_scheme_code AS code,v.group_key,ff.tlws_mf_id AS family_id
 FROM mf.funds f LEFT JOIN mf.fund_variants v ON v.amfi_scheme_code=f.amfi_scheme_code
 LEFT JOIN mf.fund_family ff ON ff.group_key=v.group_key
 ORDER BY f.amfi_scheme_code LIMIT 50001"""

RECEIPT_COLUMNS = (
    "publication_sequence", "publication_id", "predecessor_publication_id",
    "source_kind", "source_snapshot_run_id",
    "catalogue_observed_on", "observed_on", "catalogue_census_hash",
    "family_projection_hash", "receipt_content_hash",
)


def observations(conn):
    catalogue = [dict(row) for row in conn.execute(CATALOGUE_SQL).fetchall()]
    families = [dict(row) for row in conn.execute(FAMILY_SQL).fetchall()]
    policy.require(0 < len(catalogue) <= policy.MAX_FUNDS, "INVALID_FUND_CENSUS")
    policy.require(len(families) == len(catalogue), "FAMILY_PROJECTION_DIFFERS")
    return {
        "catalogue_census_hash": policy.digest(catalogue),
        "family_projection_hash": policy.digest(families),
    }


def _receipt_document(*, predecessor_publication_id, source_kind, source_snapshot_run_id,
                      catalogue_observed_on,
                      observed_on, catalogue_census_hash, family_projection_hash):
    return {
        "format": "mfdata-tag-input-publication-v1",
        "predecessor_publication_id": predecessor_publication_id,
        "source_kind": source_kind,
        "source_snapshot_run_id": source_snapshot_run_id,
        "catalogue_observed_on": catalogue_observed_on.isoformat(),
        "observed_on": observed_on.isoformat(),
        "catalogue_census_hash": catalogue_census_hash,
        "family_projection_hash": family_projection_hash,
    }


def append(conn, *, source_kind: str, source_snapshot_run_id: int,
           catalogue_observed_on: date, observed_on: date, expected_catalogue_hash=None):
    hashes = observations(conn)
    if expected_catalogue_hash is not None:
        policy.require(hashes["catalogue_census_hash"] == expected_catalogue_hash,
                       "CATALOGUE_CHANGED_DURING_FAMILY_REBUILD")
    columns = ",".join(RECEIPT_COLUMNS)
    predecessor = conn.execute(f"""SELECT {columns}
        FROM mf_identity.tag_input_publications
        ORDER BY publication_sequence DESC LIMIT 1""").fetchone()
    if predecessor is not None:
        predecessor = validate(predecessor)
    if (source_kind == "fund-family-rebuild" and predecessor is not None
            and predecessor["source_snapshot_run_id"] == source_snapshot_run_id
            and predecessor["catalogue_observed_on"] == catalogue_observed_on
            and predecessor["catalogue_census_hash"] == hashes["catalogue_census_hash"]
            and predecessor["family_projection_hash"] == hashes["family_projection_hash"]):
        return describe(predecessor)
    predecessor_id = predecessor["publication_id"] if predecessor else None
    document = _receipt_document(
        predecessor_publication_id=predecessor_id,
        source_kind=source_kind,
        source_snapshot_run_id=source_snapshot_run_id,
        catalogue_observed_on=catalogue_observed_on,
        observed_on=observed_on,
        **hashes,
    )
    publication_id = fingerprint(document)
    row = conn.execute("""INSERT INTO mf_identity.tag_input_publications(
        publication_id,predecessor_publication_id,source_kind,source_snapshot_run_id,
        catalogue_observed_on,observed_on,
        catalogue_census_hash,family_projection_hash,receipt_content_hash)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(publication_id) DO NOTHING
        RETURNING publication_sequence""", (
        publication_id, predecessor_id, source_kind, source_snapshot_run_id,
        catalogue_observed_on, observed_on,
        hashes["catalogue_census_hash"], hashes["family_projection_hash"], publication_id,
    )).fetchone()
    if row is None:
        row = conn.execute("""SELECT publication_sequence FROM mf_identity.tag_input_publications
            WHERE publication_id=%s""", (publication_id,)).fetchone()
    return {"publication_id": publication_id,
            "publication_sequence": row["publication_sequence"], **document}


def describe(row):
    document = _receipt_document(
        predecessor_publication_id=row["predecessor_publication_id"],
        source_kind=row["source_kind"],
        source_snapshot_run_id=row["source_snapshot_run_id"],
        catalogue_observed_on=row["catalogue_observed_on"],
        observed_on=row["observed_on"],
        catalogue_census_hash=row["catalogue_census_hash"],
        family_projection_hash=row["family_projection_hash"],
    )
    return {"publication_id": row["publication_id"],
            "publication_sequence": row["publication_sequence"], **document}


def validate(row):
    policy.require(row is not None and set(row) == set(RECEIPT_COLUMNS),
                   "TAGGING_SOURCE_RECEIPT_UNAVAILABLE")
    try:
        catalogue_date = row["catalogue_observed_on"]
        observed = row["observed_on"]
        predecessor_id = row["predecessor_publication_id"]
        policy.require(type(catalogue_date) is date and type(observed) is date
                       and observed >= catalogue_date, "TAGGING_SOURCE_RECEIPT_DIFFERS")
        policy.require(predecessor_id is None or (
            type(predecessor_id) is str and len(predecessor_id) == 64
            and all(character in "0123456789abcdef" for character in predecessor_id)
        ), "TAGGING_SOURCE_RECEIPT_DIFFERS")
        document = _receipt_document(
            predecessor_publication_id=predecessor_id,
            source_kind=row["source_kind"],
            source_snapshot_run_id=row["source_snapshot_run_id"],
            catalogue_observed_on=catalogue_date,
            observed_on=observed,
            catalogue_census_hash=row["catalogue_census_hash"],
            family_projection_hash=row["family_projection_hash"],
        )
        expected = fingerprint(document)
        policy.require(row["publication_id"] == row["receipt_content_hash"] == expected,
                       "TAGGING_SOURCE_RECEIPT_DIFFERS")
    except (TypeError, ValueError):
        raise policy.TaggingError("TAGGING_SOURCE_RECEIPT_DIFFERS") from None
    return row


def latest(conn):
    columns = ",".join(RECEIPT_COLUMNS)
    row = conn.execute(f"""SELECT {columns} FROM mf_identity.tag_input_publications
        ORDER BY publication_sequence DESC LIMIT 1""").fetchone()
    row = validate(row)
    predecessor = conn.execute("""SELECT publication_id
        FROM mf_identity.tag_input_publications
        WHERE publication_sequence < %s
        ORDER BY publication_sequence DESC LIMIT 1""", (
            row["publication_sequence"],
        )).fetchone()
    expected = predecessor["publication_id"] if predecessor else None
    policy.require(row["predecessor_publication_id"] == expected,
                   "TAGGING_SOURCE_RECEIPT_DIFFERS")
    return row
