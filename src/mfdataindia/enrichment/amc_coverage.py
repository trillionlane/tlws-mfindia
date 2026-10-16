"""Read-only directory/database reconciliation, not acquisition or all-AMC readiness."""

from collections import Counter, defaultdict
from datetime import date

from mfdataindia.enrichment import current_facts_store as current
from mfdataindia.enrichment import monthly_store, store as evidence
from mfdataindia.enrichment.amc_register import load_register, normalized, policy, register_hash
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.store import NotFound, Conflict


def capture(conn):
    if conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] != "on":
        raise ValueError("read-only coverage census required")
    if (
        conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
        != "repeatable read"
    ):
        raise ValueError("repeatable-read coverage snapshot required")
    verify_runtime(conn, "api")
    evidence.verify_acl(conn)
    monthly_store.verify_acl(conn)
    current.verify_acl(conn)
    amcs = conn.execute(
        "SELECT amc_id,amfi_amc_name FROM mf.amcs ORDER BY amc_id LIMIT 401"
    ).fetchall()
    funds = conn.execute(
        "SELECT amfi_scheme_code,amc_id,scheme_name,plan_type,option_type,in_scope,is_defunct,is_active FROM mf.funds ORDER BY amfi_scheme_code LIMIT 50001"
    ).fetchall()
    sources = conn.execute(
        "SELECT content_hash,intake FROM mf_monthly_facts.sources ORDER BY content_hash LIMIT 10001"
    ).fetchall()
    snapshots = conn.execute(
        "SELECT snapshot_id,amfi_scheme_code,disclosure_as_of,content_hash,(SELECT count(*) FROM jsonb_each(document->'fields')) AS field_slots,(SELECT count(*) FROM jsonb_each(document->'fields') f WHERE f.value->>'status'='accepted') AS accepted_field_observations FROM mf_monthly_facts.snapshots ORDER BY amfi_scheme_code,disclosure_as_of,snapshot_id LIMIT 10001"
    ).fetchall()
    selected = conn.execute(
        "SELECT code,field,as_of,value FROM mf_live_facts.selected_fields ORDER BY code,field LIMIT 50001"
    ).fetchall()
    if (
        len(amcs) > 400
        or len(funds) > 50000
        or len(sources) > 10000
        or len(snapshots) > 10000
        or len(selected) > 50000
    ):
        raise ValueError("coverage bound exceeded; never report truncated totals")
    dataset = conn.execute(
        "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
    ).fetchone()
    register = load_register()
    active = current.projection_state(conn)
    inputs = {
        "dataset_version": dataset["dataset_version"] if dataset else None,
        "register": register_hash(register),
        "amcs": amcs,
        "funds": funds,
        "sources": sources,
        "snapshots": snapshots,
        "selected": selected,
        "projection_active": active,
        "current_configuration": current.configuration(conn),
    }

    # Dates are converted explicitly; the policy fingerprint accepts JSON only.
    def serial(value):
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): serial(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [serial(v) for v in value]
        return value

    return build(
        register,
        amcs,
        funds,
        sources,
        serial(snapshots),
        serial(selected),
        active=active,
        dataset_version=dataset["dataset_version"] if dataset else None,
        revision=fingerprint(serial(inputs)),
    )


def build(
    register, amcs, funds, sources, snapshots, selected, *, active, dataset_version, revision
):
    """Pure deterministic projection; isolated tests cannot count as live coverage."""
    by_name = defaultdict(list)
    for amc in amcs:
        by_name[normalized(amc["amfi_amc_name"])].append(amc)
    groups = []
    mapped_ids = set()
    for member in register.members:
        matches = by_name[normalized(member.name)]
        match = matches[0] if len(matches) == 1 else None
        if match:
            mapped_ids.add(match["amc_id"])
        groups.append(
            {
                "amc_key": "amfi-" + member.member_id,
                "directory_name": member.name,
                "member_id": member.member_id,
                "database_amc_id": match["amc_id"] if match else None,
                "database_amc_name": match["amfi_amc_name"] if match else None,
                "mapping_state": "exact_name_match"
                if match
                else "ambiguous_database_name"
                if matches
                else "not_in_database",
                "listed_factsheet_url": member.listed_factsheet_url,
                "source_state": member.source_state,
                "url_review_reasons": member.url_review_reasons,
                "planned_wave": member.planned_wave,
                "acquisition_authorized_by_register": False,
            }
        )
    for amc in amcs:
        if amc["amc_id"] not in mapped_ids:
            groups.append(
                {
                    "amc_key": "db-" + str(amc["amc_id"]),
                    "directory_name": None,
                    "member_id": None,
                    "database_amc_id": amc["amc_id"],
                    "database_amc_name": amc["amfi_amc_name"],
                    "mapping_state": "database_only_unresolved",
                    "listed_factsheet_url": None,
                    "source_state": "not_reconciled",
                    "url_review_reasons": ["directory_identity_not_reconciled"],
                    "planned_wave": "SOURCE-RESOLUTION",
                    "acquisition_authorized_by_register": False,
                }
            )
    if len({f["amfi_scheme_code"] for f in funds}) != len(funds) or any(
        f["amc_id"] not in {a["amc_id"] for a in amcs} for f in funds
    ):
        raise ValueError("inconsistent source denominator")
    by_amc = defaultdict(list)
    by_snapshot = defaultdict(list)
    by_source = defaultdict(set)
    by_selected = defaultdict(list)
    for row in snapshots:
        by_snapshot[row["amfi_scheme_code"]].append(row)
    for row in sources:
        for code in row["intake"]["codes"]:
            by_source[int(code)].add(row["content_hash"])
    for row in selected:
        by_selected[row["code"]].append(row)
    for fund in funds:
        code = fund["amfi_scheme_code"]
        records = by_snapshot[code]
        by_amc[fund["amc_id"]].append(
            {
                **fund,
                "operational_target": fund["in_scope"] and not fund["is_defunct"],
                "monthly_archived_books": len(by_source[code]),
                "monthly_snapshots": len(records),
                "reporting_periods": sorted({r["disclosure_as_of"] for r in records}),
                "accepted_field_observations": sum(
                    r["accepted_field_observations"] for r in records
                ),
                "current_projection_fields": sorted(r["field"] for r in by_selected[code])
                if active
                else [],
                "current_field_dates": {r["field"]: r["as_of"] for r in by_selected[code]}
                if active
                else {},
                "extraction_state": "observed_snapshots"
                if records
                else "no_monthly_snapshot_evidence",
                "human_verification": "separate_not_asserted",
                "entire_factsheet_coverage": False,
            }
        )
    pages = {}
    for group in groups:
        rows = by_amc[group["database_amc_id"]]
        pages[group["amc_key"]] = rows
        group["counts"] = {
            "retained_schemes": len(rows),
            "in_scope": sum(r["in_scope"] for r in rows),
            "in_scope_non_defunct": sum(r["operational_target"] for r in rows),
            "out_of_scope": sum(not r["in_scope"] for r in rows),
            "with_monthly_archived_book": sum(r["monthly_archived_books"] > 0 for r in rows),
            "with_monthly_snapshot": sum(r["monthly_snapshots"] > 0 for r in rows),
            "with_current_projection_fields": sum(
                bool(r["current_projection_fields"]) for r in rows
            ),
        }
        group["reporting_periods"] = sorted({d for r in rows for d in r["reporting_periods"]})
        group["all_scheme_data_complete"] = False
    # Unmatched database AMCs are included, never removed to improve a percentage.
    if sum(g["counts"]["retained_schemes"] for g in groups) != len(funds):
        raise ValueError("database schemes double-counted or omitted")
    summary = {
        "format": "official-amc-coverage-v1",
        "policy_hash": fingerprint(policy()),
        "register_hash": register_hash(register),
        "coverage_revision": revision,
        "dataset_version": dataset_version,
        "directory_observed_at": register.observed_at.isoformat(),
        "directory_sha256": register.directory_sha256,
        "directory_population_basis": register.population_basis,
        "directory_entries": len(register.members),
        "directory_entries_with_link": sum(m.source_state == "listed" for m in register.members),
        "database_amcs": len(amcs),
        "database_only_amcs": sum(g["mapping_state"] == "database_only_unresolved" for g in groups),
        "retained_schemes": len(funds),
        "in_scope_schemes": sum(f["in_scope"] for f in funds),
        "scope_policy": "existing_generated_in_scope_unchanged",
        "amcs": groups,
        "wave_membership": {
            wave: [g["amc_key"] for g in groups if g["planned_wave"] == wave]
            for wave in sorted({g["planned_wave"] for g in groups})
        },
        "mapping_counts": dict(Counter(g["mapping_state"] for g in groups)),
        "all_amc_data_complete": False,
        "current_month_coverage_certified": False,
        "new_acquisition_authority": False,
        "human_verification_created": False,
        "source_rows_changed": 0,
    }
    return summary, pages


def summary(conn):
    return capture(conn)[0]


def schemes(conn, amc_key, *, after_code=0, limit=50, expected_revision=None):
    if not 1 <= limit <= 100 or after_code < 0 or (after_code and expected_revision is None):
        raise ValueError("bounded scheme page required")
    report, pages = capture(conn)
    if expected_revision is not None and expected_revision != report["coverage_revision"]:
        raise Conflict("coverage changed; restart pagination")
    if amc_key not in pages:
        raise NotFound("AMC not in reconciled census")
    rows = sorted(
        (r for r in pages[amc_key] if r["amfi_scheme_code"] > after_code),
        key=lambda r: r["amfi_scheme_code"],
    )
    page = rows[:limit]
    return {
        "format": "official-amc-schemes-v1",
        "amc_key": amc_key,
        "coverage_revision": report["coverage_revision"],
        "items": page,
        "next_after_code": page[-1]["amfi_scheme_code"] if len(rows) > limit else None,
        "all_amc_data_complete": False,
    }
