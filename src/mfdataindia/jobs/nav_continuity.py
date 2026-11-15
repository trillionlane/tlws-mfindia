"""Publish verified NAV separately from the AMFI catalogue transaction."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date

from psycopg.types.json import Jsonb

from mfdataindia.jobs.nav_calculations import METHODOLOGY_VERSION, recompute
from mfdataindia.load import family_continuity as families
from mfdataindia.load.amfi_to_store import load_parsed_amfi, nav_rows
from mfdataindia.store.locks import acquire_nav_data_operation_lock
from mfdataindia.store.catalogue_lock import catalogue_publication_lock
from mfdataindia.tagging import source_evidence


def _prices(schemes, existing, conflicts):
    unique = {}
    disagree = set()
    for code, day, nav, source, cross_verified in nav_rows(schemes):
        old = existing.get(code)
        if old is None or not old.in_scope or old.defunct or code in conflicts or nav <= 0:
            continue
        key = (code, day)
        row = (code, day, nav, source, cross_verified)
        if key in unique and unique[key] != row:
            disagree.add(key)
        unique[key] = row
    return [row for key, row in sorted(unique.items()) if key not in disagree]


def verified_prices(schemes, existing, incoming, conflicts):
    eligible = {code: old for code, old in existing.items()
                if code in incoming and families.matches(old, incoming[code])}
    return _prices(schemes, eligible, conflicts)


def publish_prices(store, schemes, fetch, *, as_of: date):
    with store.transaction() as conn:
        acquire_nav_data_operation_lock(conn)
        existing, _ = families.read_identities(conn)
        source, conflicts = families.read_source(schemes)
        rows = verified_prices(schemes, existing, source, conflicts)
        if not rows:
            return {"state": "NO_VERIFIED_PRICES", "rows": 0}
        result = store.upsert_nav(iter(rows))
        if result.inserted or result.updated:
            # Keep the catalogue hash and FULL snapshot ledger tied to the last
            # committed catalogue while refreshing the shared API NAV summary.
            store.record_fetch(fetch.as_source_metadata("AMFI", "NAV_HISTORY", f"navall-prices:{as_of}")
                               | {"records_in": len(rows), "records_ok": result.inserted + result.updated})
            store.refresh_dataset_summary("amfi_verified_nav_prices")
        return {"state": "VERIFIED_PRICES", "load": result.as_dict()}


def _recompute_invalidated_calculations(conn):
    # Catalogue changes can bump a NAV source revision independently of prices.
    # The existing version-bound calculation code remains the authority.
    codes = [row["amfi_scheme_code"] for row in conn.execute("""
        SELECT r.amfi_scheme_code FROM mf.nav_source_revisions r
        LEFT JOIN mf.nav_calculations c USING(amfi_scheme_code)
        WHERE (c.amfi_scheme_code IS NULL OR c.source_revision<>r.revision
               OR c.methodology_version<>%s)
          AND EXISTS (SELECT 1 FROM mf.nav_history n WHERE n.amfi_scheme_code=r.amfi_scheme_code)
        ORDER BY r.amfi_scheme_code
    """, (METHODOLOGY_VERSION,))]
    if codes:
        recompute(conn, codes)


def publish_catalogue(store, schemes, report, fetch, *, as_of: date, feed_date: date,
                      scope: str, integrity_manifest, validate_completeness, validate_summary):
    if scope != "FULL":
        raise RuntimeError("family continuity publication requires a FULL catalogue")
    with catalogue_publication_lock(store.connect(), wait=True):
        with store.transaction() as conn:
            acquire_nav_data_operation_lock(conn)
            prior = store.latest_full_snapshot()
            if prior and prior["snapshot_date"] > as_of:
                raise RuntimeError("catalogue observation predates the committed snapshot")
            if (prior and prior["snapshot_date"] == as_of
                    and prior["content_hash"] == fetch.content_sha256):
                return {"state": "CATALOGUE_UNCHANGED", "snapshot": prior}
            existing, keys = families.read_identities(conn)
            source, conflicts = families.read_source(schemes)
            resolution = families.resolve(existing, keys, source, conflicts)
            if resolution.holds:
                conn.execute("""
                    INSERT INTO mf_identity.catalogue_holds (source_hash, source_date, reasons)
                    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """, (fetch.content_sha256, as_of, Jsonb(resolution.holds)))
                return {"state": "CATALOGUE_HELD", "holds": resolution.holds}
            before = integrity_manifest(store)
            completeness = validate_completeness(schemes, report, before=before,
                                                 previous_full=prior)
            families.apply_renames(conn, resolution.renames, source,
                                  source_hash=fetch.content_sha256, snapshot_date=as_of)
            catalogue = load_parsed_amfi(store, schemes, source_date=as_of, include_nav=False)
            families.rebuild(conn)
            # The new catalogue may establish identity for a new in-scope scheme.
            # Prices for existing verified codes were already committed independently.
            verified, _ = families.read_identities(conn)
            additional = {code: row for code, row in verified.items()
                          if code not in existing or not existing[code].in_scope
                          or existing[code].defunct}
            new_prices = verified_prices(schemes, additional, source, conflicts)
            if new_prices:
                store.upsert_nav(iter(new_prices))
            else:
                _recompute_invalidated_calculations(conn)
            after = integrity_manifest(store)
            if (after["funds"] < before["funds"] or after["nav_rows"] < before["nav_rows"]
                    or (before["nav_max_date"] and (not after["nav_max_date"]
                        or after["nav_max_date"] < before["nav_max_date"]))
                    or not after["nav_max_date"] or after["nav_max_date"] < feed_date
                    or any(after[k] for k in (
                        "missing_families", "orphan_families", "default_partition_rows"
                    ))):
                raise RuntimeError("catalogue preservation or integrity check failed")
            source_id = store.record_fetch(
                fetch.as_source_metadata("AMFI", "LATEST_NAV", as_of.isoformat()) | {
                    "records_in": len(schemes), "records_ok": len(schemes),
                    "records_quarantined": report.quarantined,
                    "notes": {"snapshot_scope": scope, "snapshot_date": as_of.isoformat(),
                              "latest_nav_date": feed_date.isoformat(),
                              "family_renames": len(resolution.renames)},
                }
            )
            snapshot = store.record_snapshot_run(
                source_fetch_id=source_id, source="AMFI", snapshot_scope=scope,
                snapshot_date=as_of, latest_nav_date=feed_date,
                content_hash=fetch.content_sha256, records_in=report.data_rows,
                records_ok=len(schemes), records_quarantined=report.quarantined,
                distinct_amcs=report.distinct_amcs,
                distinct_categories=report.distinct_categories,
            )
            summary = store.refresh_dataset_summary(
                "amfi_catalogue_refresh", source_content_hash=fetch.content_sha256
            )
            validate_summary(summary, after, source_content_hash=fetch.content_sha256)
            tag_input = source_evidence.append(
                conn, source_kind="full-catalogue",
                source_snapshot_run_id=snapshot["snapshot_run_id"],
                catalogue_observed_on=as_of, observed_on=as_of,
            )
            return {
                "state": "CATALOGUE_PUBLISHED",
                "renames": [asdict(row) for row in resolution.renames],
                "snapshot": snapshot, "catalogue": catalogue,
                "completeness": completeness, "tag_input": tag_input,
            }


def freshness(store):
    with store.transaction() as conn:
        row = conn.execute("SELECT nav_last, dataset_version FROM mf.dataset_summary WHERE singleton").fetchone()
        catalogue = store.latest_full_snapshot()
        return {"nav_last": row["nav_last"], "dataset_version": row["dataset_version"],
                "catalogue_snapshot_date": catalogue["snapshot_date"] if catalogue else None}


def missing_history_prices(conn, rows, *, from_date: date, to_date: date):
    """Select only missing in-scope rows in the caller's transaction."""
    existing = {row["amfi_scheme_code"] for row in conn.execute(
        "SELECT amfi_scheme_code FROM mf.funds WHERE in_scope"
    )}
    committed = conn.execute("""
        SELECT amfi_scheme_code, nav_date FROM mf.nav_history
        WHERE nav_date BETWEEN %s AND %s
    """, (from_date, to_date)).fetchall()
    seen = {(row["amfi_scheme_code"], row["nav_date"]) for row in committed}
    candidate = defaultdict(set)
    for row in rows:
        code, day, value, *_ = row
        if code in existing and from_date <= day <= to_date and (code, day) not in seen and value > 0:
            candidate[code, day].add(row)
    if any(len(values) != 1 for values in candidate.values()):
        raise RuntimeError("conflicting AMFI history observations")
    return [next(iter(values)) for _, values in sorted(candidate.items())]
