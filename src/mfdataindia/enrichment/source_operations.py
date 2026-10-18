"""Deterministic read-only triage of retained monthly-book evidence, never admission."""

import calendar
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from mfdataindia.enrichment import book_facts, book_store
from mfdataindia.review.policy import fingerprint

POLICY_FILE = "source-operations-policy-v1.json"
POLICY_SHA256 = "b03b99199db4f9005abf1e8042c33ffc3b436bd61e12b88e5490ac8b39a1bf73"


def policy():
    root = Path("/app") if (Path("/app/config") / POLICY_FILE).is_file() else Path(__file__).resolve().parents[3]
    raw = (root / "config" / POLICY_FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
        raise ValueError("immutable source operations policy bytes differ")
    doc = json.loads(raw)
    if doc["id"] != "official-source-operations-inventory" or doc["version"] != 1:
        raise ValueError("exact versioned operations policy required")
    return doc


def instant(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("explicit timezone-aware operation time required")
    return result.astimezone(timezone.utc)


def recent_periods(as_of):
    year, month = as_of.year, as_of.month
    result = []
    for _ in range(2):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        result.append(date(year, month, calendar.monthrange(year, month)[1]).isoformat())
    return result


def _sealed(doc, key):
    if not isinstance(doc, dict) or doc.get(key) != fingerprint({k: v for k, v in doc.items() if k != key}):
        raise ValueError("retained operation evidence seal differs")


def _receipt(doc, source_key, as_of, *, successful=False):
    if doc is None:
        return None
    _sealed(doc, "run_id")
    if doc["source_key"] != source_key or doc["status"] not in {"updated", "unchanged", "failed"}:
        raise ValueError("source attempt identity/status differs")
    if successful and doc["status"] == "failed":
        raise ValueError("failed attempt is not successful source evidence")
    observed = instant(doc["observed_at"])
    if observed > as_of:
        raise ValueError("operation time predates retained source attempt")
    result = {"run_id": doc["run_id"], "execution_id": doc["execution_id"],
              "status": doc["status"], "observed_at": observed.isoformat(),
              "age_seconds": int((as_of - observed).total_seconds())}
    if doc["status"] == "failed":
        phase = doc.get("phase")
        result["failure_phase"] = phase if isinstance(phase, str) and re.fullmatch("[a-z][a-z_]{0,63}", phase) else None
        error = doc.get("error_type")
        result["failure_type"] = error if error in {"ValueError", "TimeoutError", "OperationalError", "QueryCanceled", "InsufficientPrivilege", "SourceError"} else "UnrecognizedError"
    return result


def build_inventory(status, batches, *, as_of, nav_dataset_version=None):
    """Bind real receipts and complete retained populations; no financial values emitted."""
    as_of, rules = instant(as_of), policy()
    if nav_dataset_version is not None and (type(nav_dataset_version) is not int or nav_dataset_version < 0):
        raise ValueError("explicit observed NAV dataset version required")
    if status.get("format") != "official-amc-book-status-v1" or not 1 <= len(status["sources"]) <= rules["maximum_sources"]:
        raise ValueError("bounded installed book status required")
    keys = [s["source_key"] for s in status["sources"]]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate registered source")
    expected, sources, queue = recent_periods(as_of), [], []
    for source in sorted(status["sources"], key=lambda s: s["source_key"]):
        key = source["source_key"]
        latest = _receipt(source["latest_run"], key, as_of)
        good = _receipt(source["latest_successful_run"], key, as_of, successful=True)
        if type(source["current_denominator"]) is not int or not 1 <= source["current_denominator"] <= rules["maximum_variants_per_period"]:
            raise ValueError("bounded current source denominator required")
        matches = bool(good and source["latest_successful_run"]["population_hash"] == source["current_population_hash"])
        if source["population_matches_latest_success"] is not matches:
            raise ValueError("population match flag differs from bound source evidence")
        if good and (latest is None or instant(good["observed_at"]) > instant(latest["observed_at"])):
            raise ValueError("latest attempt must not predate last success")
        alerts = []
        if latest is None:
            alerts.append("no_source_attempt")
        elif latest["status"] == "failed":
            alerts.append("latest_source_attempt_failed")
        if good is None:
            alerts.append("no_successful_source_evidence")
        elif good["age_seconds"] > rules["source_check_overdue_after_seconds"]:
            alerts.append("successful_source_check_overdue")
        if not source["population_matches_latest_success"]:
            alerts.append("current_identity_population_not_certified_by_latest_success")
        ids = source["latest_successful_run"]["batch_ids"] if good else []
        if len(ids) != len(set(ids)) or len(ids) > rules["maximum_periods_per_source"]:
            raise ValueError("bounded distinct source batch IDs required")
        if len(source["periods"]) != len(ids):
            raise ValueError("source period evidence omitted or duplicated")
        reports, period_dates = [], []
        for batch_id in ids:
            batch = batches.get(batch_id)
            if batch is None:
                raise ValueError("successful source batch is missing")
            _sealed(batch, "batch_id")
            if batch["batch_id"] != batch_id or batch["source_key"] != key:
                raise ValueError("source batch edge differs")
            rows = batch["observations"]
            if not 1 <= len(rows) <= rules["maximum_variants_per_period"]:
                raise ValueError("bounded complete variant population required")
            identities = [row["identity"] for row in rows]
            codes = [i["code"] for i in identities]
            if codes != sorted(set(codes)) or fingerprint(identities) != batch["population_hash"]:
                raise ValueError("complete ordered population seal differs")
            if any(type(code) is not int or not 0 < code <= 2147483647 for code in codes):
                raise ValueError("exact integer variant identities required")
            if batch["population_hash"] != source["latest_successful_run"]["population_hash"]:
                raise ValueError("batch population differs from successful source receipt")
            if matches and len(rows) != source["current_denominator"]:
                raise ValueError("current denominator differs from complete source population")
            day = date.fromisoformat(batch["disclosure_as_of"])
            if day.day != calendar.monthrange(day.year, day.month)[1] or day > as_of.date():
                raise ValueError("actual month-end source period required")
            coverage = book_facts.coverage(batch)
            if source["periods"].count(coverage) != 1:
                raise ValueError("whole-period coverage differs from retained batch")
            field_gaps, classes = defaultdict(Counter), Counter()
            for row in rows:
                outcome, identity = row["result"], row["identity"]
                if outcome["status"] not in {"usable", "gap"}:
                    raise ValueError("unknown variant outcome")
                for field, value in outcome["fields"].items():
                    if value["status"] != "accepted":
                        field_gaps[field][value.get("reason") or "reason_not_recorded"] += 1
                supplemental = outcome.get("reported_total_expense_ratio")
                if supplemental is not None and supplemental["status"] != "accepted":
                    field_gaps["reported_total_expense_ratio"][supplemental.get("reason") or "reason_not_recorded"] += 1
                if outcome["status"] == "gap":
                    reason = outcome.get("reason") or "reason_not_recorded"
                    category = rules["reason_classes"].get(reason, rules["unknown_reason"])
                    classes[category] += 1
                    body = {"source_key": key, "batch_id": batch_id, "period": day.isoformat(),
                            "code": identity["code"], "identity_hash": fingerprint(identity),
                            "reason": reason, "triage_class": category,
                            "population_matches_latest_success": source["population_matches_latest_success"],
                            "automatic_resolution_permitted": False}
                    queue.append({**body, "case_id": fingerprint(body)})
            acquisition = batch.get("source_acquisition")
            if acquisition and acquisition.get("mode") == "reviewed_known_document_not_live_catalogue":
                alerts.append("known_document_not_live_discovery")
                if as_of.date() >= date.fromisoformat(acquisition["valid_before"]):
                    alerts.append("known_document_authority_expired")
            reports.append({"period": day.isoformat(), "batch_id": batch_id,
                            "denominator": len(rows), "statuses": coverage["statuses"],
                            "accepted_fields": coverage["accepted_fields"],
                            "supplemental_accepted_fields": coverage.get("supplemental_accepted_fields", {}),
                            "primary_gap_classes": dict(sorted(classes.items())),
                            "field_gap_counts_separate_from_variant_gaps": {
                                field: dict(sorted(reasons.items())) for field, reasons in sorted(field_gaps.items())
                            },
                            "source_acquisition": acquisition})
            period_dates.append(day.isoformat())
        if len(set(period_dates)) != len(period_dates):
            raise ValueError("distinct source reporting periods required")
        missing = [p for p in expected if p not in period_dates]
        if missing:
            alerts.append("recent_reporting_period_not_observed_not_deadline_failure")
        sources.append({"source_key": key, "current_denominator": source["current_denominator"],
                        "current_population_hash": source["current_population_hash"],
                        "latest_attempt": latest, "latest_success": good,
                        "population_matches_latest_success": source["population_matches_latest_success"],
                        "expected_recent_periods": expected, "missing_recent_periods": missing,
                        "alerts": sorted(set(alerts)), "periods": reports})
    queue.sort(key=lambda r: (r["source_key"], r["period"], r["code"], r["case_id"]))
    body = {"format": "official-source-operations-inventory-v1", "as_of": as_of.isoformat(),
            "policy": rules, "policy_hash": fingerprint(rules),
            "configuration_hash": status["configuration_hash"], "nav_dataset_version": nav_dataset_version,
            "source_check_revision_is_independent_of_NAV_dataset": True, "sources": sources,
            "primary_gap_cases": queue, "primary_gap_case_count": len(queue),
            "source_rows_changed": 0, "human_events_created": 0,
            "financial_values_generated": False, "source_correctness_certified": False,
            "cloud_scheduler_health_certified": False, "all_amc_data_complete": False,
            "adjusted_idcw_analytics_enabled": False, "verification_status": "needs_verification"}
    return {**body, "inventory_hash": fingerprint(body)}


def inventory(conn, *, as_of):
    if conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] != "on":
        raise ValueError("read-only source operations transaction required")
    if conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] != "repeatable read":
        raise ValueError("repeatable-read source operations transaction required")
    identity = conn.execute("SELECT current_user AS effective, session_user AS authenticated").fetchone()
    if identity != {"effective": "mfdata_verified_reader", "authenticated": "mfdata_verified_reader"}:
        raise ValueError("restricted authenticated reader required")
    status = book_store.status(conn)  # Existing complete ACL and installed-policy checks.
    ids = sorted({b for s in status["sources"] for b in (s["latest_successful_run"] or {}).get("batch_ids", [])})
    rows = conn.execute(
        "SELECT batch_id,document FROM mf_book_facts.batches WHERE batch_id=ANY(%s) ORDER BY batch_id",
        (ids,),
    ).fetchall()
    dataset = conn.execute("SELECT dataset_version FROM mf.dataset_summary WHERE singleton").fetchone()
    if dataset is None or type(dataset["dataset_version"]) is not int:
        raise ValueError("actual current dataset version unavailable")
    return build_inventory(status, {r["batch_id"]: r["document"] for r in rows}, as_of=as_of,
                           nav_dataset_version=dataset["dataset_version"])


def engine_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
