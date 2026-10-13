"""DATA-02 investigation snapshots and review ledger. Never repairs source data."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from mfdataindia.audit import nav_integrity as audit
from mfdataindia.store.locks import acquire_nav_data_operation_lock

FORMAT = "mfdata-nav-remediation-register-v1"
SIGNALS = {audit.SIGNAL_CONSTANT, audit.SIGNAL_DUPLICATE, audit.SIGNAL_TERMINAL_RESET}
DISPOSITIONS = {
    "verified_legitimate",
    "confirmed_nav_discrepancy",
    "identity_review_required",
    "insufficient_evidence",
}


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"cannot serialize {type(value).__name__}")


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_json_default, allow_nan=False
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def seal(value: dict) -> dict:
    if "sha256" in value:
        raise ValueError("document already has a hash")
    return json.loads(canonical(value | {"sha256": fingerprint(value)}))


def require_hash(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected a SHA-256")


def verify(document: dict) -> None:
    require_hash(document.get("sha256"))
    content = {k: v for k, v in document.items() if k != "sha256"}
    if fingerprint(content) != document["sha256"]:
        raise ValueError("document content does not match its hash")


def read_document(path) -> dict:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(path.read_text(), object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    verify(value)
    return value


def build_register(
    rows: list[dict], *, dataset_version: int, expected_findings_sha256: str, source_manifest: dict
) -> dict:
    """Group duplicate counterparts into cases without assigning a verdict.

    Callers supply the complete *current* assessment table on one locked,
    read-only transaction. The hash reproduces the certified audit findings.
    """
    require_hash(expected_findings_sha256)
    if type(dataset_version) is not int or dataset_version <= 0:
        raise ValueError("expected a positive dataset version")
    by_code = {int(r["amfi_scheme_code"]): r for r in rows}
    if not rows or len(by_code) != len(rows):
        raise ValueError("empty or duplicate assessment identities")
    for row in rows:
        if (
            row["assessed_dataset_version"] != dataset_version
            or row["methodology_version"] != audit.METHODOLOGY_VERSION
        ):
            raise ValueError("assessment is stale or has an unsupported methodology")
        signals = row["signals"]
        if len(signals) != len(set(signals)) or not set(signals) <= SIGNALS:
            raise ValueError("invalid audit signals")
        if set(row["evidence"]) != set(signals):
            raise ValueError("signals and evidence differ")
    findings = {
        str(code): {"signals": sorted(row["signals"]), "evidence": row["evidence"]}
        for code, row in sorted(by_code.items())
        if row["signals"]
    }
    if fingerprint(findings) != expected_findings_sha256:
        raise ValueError("findings do not match the certified audit hash")

    cases = {}
    for code, row in sorted(by_code.items()):
        for signal in sorted(row["signals"]):
            codes = [code]
            family = None
            if signal == audit.SIGNAL_DUPLICATE:
                ev = row["evidence"][signal]
                counterparts = ev["counterpart_codes"]
                if (
                    not counterparts
                    or code in counterparts
                    or len(set(counterparts)) != len(counterparts)
                ):
                    raise ValueError("invalid duplicate counterparts")
                codes = sorted([code, *counterparts])
                family = ev["family"]
                for other_code in codes:
                    other = by_code.get(other_code, {}).get("evidence", {}).get(signal)
                    if (
                        other is None
                        or other["family"] != family
                        or other["fingerprint"] != ev["fingerprint"]
                        or sorted([other_code, *other["counterpart_codes"]]) != codes
                    ):
                        raise ValueError("duplicate cluster is asymmetric or incomplete")
                    identity = by_code[other_code]["identity"]
                    current_family = {
                        "amc_id": identity["amc_id"],
                        "scheme_type": identity["scheme_type"],
                        "scheme_category": identity["scheme_category"],
                        "group_key": identity["group_key"],
                    }
                    if current_family != family:
                        raise ValueError("duplicate family identity changed after assessment")
            case_id = fingerprint({"signal": signal, "scheme_codes": codes, "family": family})
            if case_id in cases:
                continue
            members = []
            for member in codes:
                item = by_code[member]
                members.append(
                    {
                        "amfi_scheme_code": member,
                        "identity": item["identity"],
                        "observation_count": item["observation_count"],
                        "first_nav_date": item["first_nav_date"],
                        "last_nav_date": item["last_nav_date"],
                        "all_signals": sorted(item["signals"]),
                        "evidence": item["evidence"][signal],
                    }
                )
            priority = (
                0
                if signal == audit.SIGNAL_TERMINAL_RESET
                else 1
                if signal == audit.SIGNAL_CONSTANT
                else 2
                if any(m["identity"]["option_type"] == "GROWTH" for m in members)
                else 3
            )
            cases[case_id] = seal(
                {
                    "case_id": case_id,
                    "dataset_version": dataset_version,
                    "methodology_version": audit.METHODOLOGY_VERSION,
                    "certified_findings_sha256": expected_findings_sha256,
                    "source_manifest_sha256": fingerprint(source_manifest),
                    "signal": signal,
                    "scheme_codes": codes,
                    "reviewed_source_revisions": {
                        str(code): by_code[code].get("assessed_source_revision") for code in codes
                    },
                    "family": family,
                    "priority": priority,
                    "review_state": "pending_review",
                    "members": members,
                }
            )
    signal_counts = {
        signal: sum(signal in r["signals"] for r in rows) for signal in sorted(SIGNALS)
    }
    return seal(
        {
            "format": FORMAT,
            "dataset_version": dataset_version,
            "methodology_version": audit.METHODOLOGY_VERSION,
            "findings_sha256": expected_findings_sha256,
            "source_manifest": source_manifest,
            "schemes_assessed": len(rows),
            "flagged_schemes": len(findings),
            "signal_counts": signal_counts,
            "duplicate_clusters": sum(
                c["signal"] == audit.SIGNAL_DUPLICATE for c in cases.values()
            ),
            "case_count": len(cases),
            "decisions_recorded": 0,
            "cases": sorted(cases.values(), key=lambda c: (c["priority"], c["case_id"])),
        }
    )


def export_register(conn, *, expected_dataset_version: int, expected_findings_sha256: str) -> dict:
    """Connection must be in a new transaction; DB enforces source read-only."""
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    conn.execute("SET LOCAL statement_timeout = '120s'")
    acquire_nav_data_operation_lock(conn)
    manifest = conn.execute(
        "SELECT dataset_version, source_content_hash, nav_first, nav_last, nav_rows "
        "FROM mf.dataset_summary WHERE singleton"
    ).fetchone()
    if manifest is None or manifest["dataset_version"] != expected_dataset_version:
        raise ValueError("live dataset differs from the expected version")
    rows = conn.execute(
        """
        SELECT a.*, jsonb_build_object(
            'scheme_name', f.scheme_name, 'amc_id', f.amc_id,
            'amc_name', am.amfi_amc_name, 'scheme_type', f.scheme_type,
            'scheme_category', f.scheme_category, 'plan_type', f.plan_type,
            'option_type', f.option_type, 'periodicity', f.periodicity,
            'isin_primary', f.isin_primary, 'isin_payout', f.isin_growth_or_div_payout,
            'isin_reinvest', f.isin_div_reinvest, 'is_defunct', f.is_defunct,
            'last_seen_in_source', f.last_seen_in_source, 'group_key', fv.group_key
        ) AS identity
        FROM mf.nav_quality_assessments a
        JOIN mf.funds f USING (amfi_scheme_code)
        JOIN mf.amcs am USING (amc_id)
        LEFT JOIN mf.fund_variants fv USING (amfi_scheme_code)
        ORDER BY a.amfi_scheme_code
        """
    ).fetchall()
    population = conn.execute(
        "SELECT count(DISTINCT amfi_scheme_code) AS schemes, count(*) AS nav_rows FROM mf.nav_history"
    ).fetchone()
    if conn.execute(
        "SELECT 1 FROM mf.nav_source_revisions r LEFT JOIN mf.nav_quality_assessments q USING(amfi_scheme_code) "
        "WHERE q.assessed_source_revision IS DISTINCT FROM r.revision "
        "AND EXISTS(SELECT 1 FROM mf.nav_history n WHERE n.amfi_scheme_code=r.amfi_scheme_code) LIMIT 1"
    ).fetchone():
        raise ValueError("source dependencies changed after assessment; reassess before export")
    if len(rows) != population["schemes"] or population["nav_rows"] != manifest["nav_rows"]:
        raise ValueError("assessment population or source manifest is incomplete")
    return build_register(
        rows,
        dataset_version=expected_dataset_version,
        expected_findings_sha256=expected_findings_sha256,
        source_manifest=dict(manifest),
    )


def register_snapshots(conn, register: dict) -> int:
    verify(register)
    if register["format"] != FORMAT or register["case_count"] != len(register["cases"]):
        raise ValueError("invalid register format or case count")
    added = 0
    for case in register["cases"]:
        verify(case)
        if case["dataset_version"] != register["dataset_version"]:
            raise ValueError("case and register versions differ")
        cur = conn.execute(
            "INSERT INTO mf.nav_remediation_snapshots "
            "(snapshot_sha256, case_id, dataset_version, payload) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (snapshot_sha256) DO NOTHING",
            (case["sha256"], case["case_id"], case["dataset_version"], Jsonb(case)),
        )
        added += cur.rowcount
    return added


def _lock_review_snapshot(conn, snapshot_sha256: str) -> None:
    row = conn.execute(
        "SELECT pg_try_advisory_xact_lock(hashtext(%s), 2) AS acquired", (snapshot_sha256,)
    ).fetchone()
    if row["acquired"] is not True:
        raise ValueError("another reviewer holds this case; refresh before appending")


def record_decision(conn, decision: dict) -> str:
    """Serialize the supersession chain; decisions are human evidence claims,
    never source-write approval or a change to comparison eligibility.
    """
    verify(decision)
    required = {
        "sha256",
        "case_snapshot_sha256",
        "supersedes_sha256",
        "disposition",
        "reviewer_reference",
        "reason",
        "evidence",
    }
    if set(decision) != required or decision["disposition"] not in DISPOSITIONS:
        raise ValueError("invalid decision shape or disposition")
    from mfdataindia.audit.remediation_evidence import validate_evidence_reference

    if not isinstance(decision["evidence"], list):
        raise ValueError("decision evidence must be a list")
    for reference in decision["evidence"]:
        validate_evidence_reference(reference)
    if decision["disposition"] == "confirmed_nav_discrepancy" and not any(
        ev["source"] == "AMFI_NAV_HISTORY" for ev in decision["evidence"]
    ):
        raise ValueError("NAV discrepancy decisions require AMFI history evidence")
    for key in ("reviewer_reference", "reason"):
        if (
            not isinstance(decision[key], str)
            or not decision[key].strip()
            or len(decision[key]) > 2000
        ):
            raise ValueError("decision requires a bounded reviewer reference and reason")
    if decision["disposition"] in {"verified_legitimate", "confirmed_nav_discrepancy"}:
        if not decision["evidence"]:
            raise ValueError("a conclusive decision requires archived evidence")
    _lock_review_snapshot(conn, decision["case_snapshot_sha256"])
    snapshot = conn.execute(
        "SELECT payload FROM mf.nav_remediation_snapshots WHERE snapshot_sha256=%s",
        (decision["case_snapshot_sha256"],),
    ).fetchone()
    if snapshot is None:
        raise ValueError("unknown case snapshot")
    existing = conn.execute(
        "SELECT payload FROM mf.nav_remediation_decisions WHERE decision_sha256=%s",
        (decision["sha256"],),
    ).fetchone()
    if existing:
        if existing["payload"] != decision:
            raise ValueError("decision hash collision")
        return "ALREADY_RECORDED"
    latest = conn.execute(
        """SELECT d.decision_sha256 FROM mf.nav_remediation_decisions d
        WHERE d.snapshot_sha256=%s AND NOT EXISTS (
            SELECT 1 FROM mf.nav_remediation_decisions n WHERE n.supersedes_sha256=d.decision_sha256
        )""",
        (decision["case_snapshot_sha256"],),
    ).fetchone()
    expected = latest["decision_sha256"] if latest else None
    if decision["supersedes_sha256"] != expected:
        raise ValueError("decision chain changed; refresh before appending")
    conn.execute(
        "INSERT INTO mf.nav_remediation_decisions "
        "(decision_sha256,snapshot_sha256,supersedes_sha256,disposition,payload) "
        "VALUES (%s,%s,%s,%s,%s)",
        (
            decision["sha256"],
            decision["case_snapshot_sha256"],
            expected,
            decision["disposition"],
            Jsonb(decision),
        ),
    )
    return "RECORDED"


def record_proposal(conn, proposal: dict) -> str:
    verify(proposal)
    if proposal.get("format") != "mfdata-nav-correction-proposal-v1":
        raise ValueError("invalid proposal format")
    _lock_review_snapshot(conn, proposal["case_snapshot_sha256"])
    decision = conn.execute(
        "SELECT d.payload, s.payload AS case_payload FROM mf.nav_remediation_decisions d "
        "JOIN mf.nav_remediation_snapshots s ON s.snapshot_sha256=d.snapshot_sha256 "
        "WHERE d.decision_sha256=%s "
        "AND NOT EXISTS (SELECT 1 FROM mf.nav_remediation_decisions n "
        "WHERE n.supersedes_sha256=d.decision_sha256)",
        (proposal["decision_sha256"],),
    ).fetchone()
    if (
        decision is None
        or decision["payload"]["disposition"] != "confirmed_nav_discrepancy"
        or decision["payload"]["case_snapshot_sha256"] != proposal["case_snapshot_sha256"]
        or proposal["evidence"] not in decision["payload"]["evidence"]
    ):
        raise ValueError("proposal is not bound to its recorded discrepancy decision")
    case = decision["case_payload"]
    if proposal["expected_dataset_version"] != case["dataset_version"]:
        raise ValueError("proposal version differs from reviewed case")
    revisions = proposal.get("reviewed_source_revisions")
    if (not isinstance(revisions, dict) or set(revisions) != {str(c) for c in case['scheme_codes']}
            or any(type(v) is not int or v < 0 for v in revisions.values())
            or revisions != case.get("reviewed_source_revisions")):
        raise ValueError("proposal dependencies differ from reviewed case")
    changes = proposal["changes"]
    keys = {(c["amfi_scheme_code"], c["nav_date"]) for c in changes}
    if (
        not 0 < len(changes) <= 100
        or len(keys) != len(changes)
        or len({code for code, _ in keys}) > 5
        or any(code not in case["scheme_codes"] for code, _ in keys)
    ):
        raise ValueError("proposal is outside the reviewed case or pilot limits")
    conn.execute(
        "INSERT INTO mf.nav_remediation_proposals (proposal_sha256, decision_sha256, payload) "
        "VALUES (%s,%s,%s) ON CONFLICT (proposal_sha256) DO NOTHING",
        (proposal["sha256"], proposal["decision_sha256"], Jsonb(proposal)),
    )
    return "RECORDED_NOT_APPROVED_OR_APPLIED"
