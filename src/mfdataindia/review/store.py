"""Read exact source snapshots; append isolated results/events. Never source DML."""
from __future__ import annotations

import hashlib
import platform
from datetime import date, datetime
from decimal import Decimal, __libmpdec_version__
from pathlib import Path

from psycopg.types.json import Jsonb

from mfdataindia.api import queries
from mfdataindia.review.engine import calculate
from mfdataindia.review.policy import ENGINE_VERSION, decimal_text, fingerprint, validate_policy
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MAX_CODES = 25
MAX_POINTS = 30000
IDENTITY_COLUMNS = ("amfi_scheme_code", "scheme_name", "amc_id", "scheme_type", "scheme_category",
                    "plan_type", "option_type", "isin_growth_or_div_payout", "isin_div_reinvest",
                    "is_defunct", "last_seen_in_source")
FACT_COLUMNS = ("aum", "expense_ratio", "benchmark", "benchmark_name", "fund_manager_name", "inception_date")


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


def _json_source(value):
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_source(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_source(v) for v in value]
    return value


def capture(conn, code: int) -> dict:
    """Caller provides repeatable-read transaction. All values remain exact."""
    identity = conn.execute(
        "SELECT " + ",".join(IDENTITY_COLUMNS) + " FROM mf.funds WHERE amfi_scheme_code=%s", (code,)
    ).fetchone()
    if not identity:
        raise NotFound("fund not found")
    revision = conn.execute("SELECT revision FROM mf.nav_source_revisions WHERE amfi_scheme_code=%s", (code,)).fetchone()
    points = conn.execute(
        "SELECT nav_date,nav FROM mf.nav_history WHERE amfi_scheme_code=%s ORDER BY nav_date LIMIT %s",
        (code, MAX_POINTS + 1),
    ).fetchall()
    if len(points) > MAX_POINTS:
        raise ValueError("observation budget exceeded")
    facts = conn.execute("SELECT " + ",".join(FACT_COLUMNS) + " FROM mf.fund_facts WHERE amfi_scheme_code=%s", (code,)).fetchone()
    dataset = conn.execute("SELECT dataset_version FROM mf.dataset_summary WHERE singleton").fetchone()
    return {
        "format": "verified-nav-input-v1", "code": code,
        "source_revision": int(revision["revision"]) if revision else 0,
        "dataset_version": int(dataset["dataset_version"]),
        "identity": _json_source(identity), "facts": _json_source(facts) if facts else None,
        "observations": [{"date": p["nav_date"].isoformat(), "nav": decimal_text(p["nav"])} for p in points],
        "context": queries._fund_signals(conn, code, latest_nav_date=points[-1]["nav_date"] if points else None),
    }


def engine_identity() -> dict:
    root = Path(__file__).parent
    return {"version": ENGINE_VERSION,
            "code_hash": hashlib.sha256((root / "engine.py").read_bytes() + (root / "policy.py").read_bytes()).hexdigest(),
            "python": platform.python_version(), "implementation": platform.python_implementation(),
            "decimal_library": __libmpdec_version__}


def derive(snapshot: dict, document: dict) -> dict:
    policy_hash = validate_policy(document)
    engine = engine_identity()
    calculated = calculate(snapshot, document)
    result = {
        "format": "verified-nav-result-v1", "code": snapshot["code"],
        "input_hash": fingerprint(snapshot), "policy_hash": policy_hash,
        "engine_version": ENGINE_VERSION, "engine_fingerprint": fingerprint(engine), "engine": engine,
        "source_revision": snapshot["source_revision"], "dataset_version": snapshot["dataset_version"],
        "first_date": snapshot["observations"][0]["date"] if snapshot["observations"] else None,
        "as_of": snapshot["observations"][-1]["date"] if snapshot["observations"] else None,
        "points": len(snapshot["observations"]), "context": snapshot["context"],
        "eligible_at_capture": snapshot["context"]["methodology"]["comparison_eligible"] and calculated["metrics"]["cumulative_return_pct"] is not None,
        **calculated,
    }
    return {"result_id": fingerprint(result), **result}


def _insert_exact(conn, table: str, key: str, identifier: str, document: dict, columns: dict) -> None:
    # Table/column names are internal constants, never request fields.
    names = [key, *columns, "document"]
    values = [identifier, *columns.values(), Jsonb(document)]
    conn.execute("INSERT INTO mf_review." + table + "(" + ",".join(names) + ") VALUES(" +
                 ",".join(["%s"] * len(values)) + ") ON CONFLICT(" + key + ") DO NOTHING", values)
    row = conn.execute("SELECT document FROM mf_review." + table + " WHERE " + key + "=%s", (identifier,)).fetchone()
    if row["document"] != document:
        raise Conflict("stored immutable artifact differs from replay")


def publish(conn, snapshots: list[dict], document: dict) -> list[dict]:
    """One bounded atomic batch. Caller owns transaction; no old publisher calls."""
    # Nested savepoint ensures a caller catching an exception cannot commit a
    # partial result batch. The outer transaction remains caller-controlled.
    with conn.transaction():
        return _publish(conn, snapshots, document)


def _publish(conn, snapshots: list[dict], document: dict) -> list[dict]:
    if conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] != "read committed":
        raise ValueError("publication requires READ COMMITTED under the NAV operation lock")
    if not snapshots or len(snapshots) > MAX_CODES or len({s["code"] for s in snapshots}) != len(snapshots):
        raise ValueError("require 1-25 unique funds")
    policy_hash = validate_policy(document)
    acquire_nav_data_operation_lock(conn)
    results = []
    for snapshot in sorted(snapshots, key=lambda s: s["code"]):
        if fingerprint(capture(conn, snapshot["code"])) != fingerprint(snapshot):
            raise Conflict("source/context changed after capture; obtain a fresh snapshot")
        result = derive(snapshot, document)
        _insert_exact(conn, "policies", "policy_hash", policy_hash, document,
                      {"policy_id": document["id"], "policy_version": document["version"]})
        _insert_exact(conn, "snapshots", "input_hash", result["input_hash"], snapshot,
                      {"amfi_scheme_code": snapshot["code"], "source_revision": snapshot["source_revision"]})
        _insert_exact(conn, "results", "result_id", result["result_id"], result,
                      {"input_hash": result["input_hash"], "policy_hash": policy_hash,
                       "engine_version": ENGINE_VERSION, "engine_fingerprint": result["engine_fingerprint"]})
        results.append(result)
    return results


def get_result(conn, code: int, result_id: str | None = None) -> dict:
    snapshot = capture(conn, code)
    row = conn.execute(
        "SELECT r.document FROM mf_review.results r JOIN mf_review.snapshots s USING(input_hash) "
        "WHERE s.amfi_scheme_code=%s AND (%s::text IS NULL OR r.result_id=%s) "
        "ORDER BY r.published_at DESC,r.result_id DESC LIMIT 1", (code, result_id, result_id),
    ).fetchone()
    if not row:
        raise NotFound("no published analytics result for this fund")
    document = row["document"]
    current = fingerprint(snapshot) == document["input_hash"]
    return {"result": document, "input_status": "current" if current else "stale",
            "current_input_hash": fingerprint(snapshot),
            "comparison_eligible": current and document["eligible_at_capture"] and snapshot["context"]["methodology"]["comparison_eligible"]}


def get_review(conn, code: int, *, after_version: int = 0, limit: int = 50) -> dict:
    snapshot = capture(conn, code)
    head = conn.execute("SELECT version FROM mf_review.events WHERE amfi_scheme_code=%s ORDER BY version DESC LIMIT 1", (code,)).fetchone()
    # Proposals advance optimistic concurrency but never renew a human review.
    # Query independently of the requested history page and latest event/result.
    latest_review = conn.execute(
        "SELECT e.status,r.input_hash FROM mf_review.events e JOIN mf_review.results r USING(result_id) "
        "WHERE e.amfi_scheme_code=%s AND e.kind='review' ORDER BY e.version DESC LIMIT 1", (code,),
    ).fetchone()
    rows = conn.execute(
        "SELECT version,kind,status,result_id,caller,actor,document,recorded_at FROM mf_review.events "
        "WHERE amfi_scheme_code=%s AND version>%s ORDER BY version LIMIT %s", (code, after_version, limit),
    ).fetchall()
    current_hash = fingerprint(snapshot)
    return {"code": code, "version": head["version"] if head else 0,
            "status": latest_review["status"] if latest_review else "pending_review",
            "needs_revalidation": latest_review is not None and latest_review["input_hash"] != current_hash,
            "current_input_hash": current_hash, "identity": snapshot["identity"],
            "original_facts": snapshot["facts"], "context": snapshot["context"],
            "history": [_json_source(r) for r in rows],
            "next_after_version": rows[-1]["version"] if len(rows) == limit else None}


def record_event(conn, code: int, request: dict, *, kind: str, caller: str, actor: str, idempotency_key: str) -> dict:
    """Signed identity supplied by API auth, not editable request body fields."""
    if conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] != "read committed":
        raise ValueError("review mutation requires READ COMMITTED under the NAV operation lock")
    acquire_nav_data_operation_lock(conn)
    request_hash = fingerprint({"code": code, "kind": kind, "actor": actor, "request": request})
    replay = conn.execute("SELECT request_hash,version,kind,status,result_id FROM mf_review.events WHERE caller=%s AND idempotency_key=%s", (caller, idempotency_key)).fetchone()
    if replay:
        if replay["request_hash"] != request_hash:
            raise Conflict("idempotency key already used for a different request or actor")
        return {k: v for k, v in replay.items() if k != "request_hash"}
    current = get_result(conn, code, request["result_id"])
    if current["input_status"] != "current" or current["current_input_hash"] != request["expected_input_hash"]:
        raise Conflict("reviewed inputs changed; refresh before recording a decision")
    head = conn.execute("SELECT version FROM mf_review.events WHERE amfi_scheme_code=%s ORDER BY version DESC LIMIT 1", (code,)).fetchone()
    version = head["version"] if head else 0
    if request["expected_version"] != version:
        raise Conflict("review version changed")
    latest_review = conn.execute(
        "SELECT status FROM mf_review.events WHERE amfi_scheme_code=%s AND kind='review' ORDER BY version DESC LIMIT 1", (code,),
    ).fetchone() if kind == "proposal" else None
    status = request["status"] if kind == "review" else latest_review["status"] if latest_review else "pending_review"
    payload = {**request, "execution": "not_executable"} if kind == "proposal" else request
    # Status validation happens in the typed API and table constraints. The
    # owner-executed function independently enforces replay/version/dependency
    # checks; runtime cannot append arbitrary ledger records directly.
    if kind not in {"review", "proposal"}:
        raise ValueError("unsupported event kind")
    if status not in {"pending_review", "in_review", "reviewed", "needs_evidence"}:
        raise ValueError("unsupported review status")
    row = conn.execute("SELECT mf_review.append_event(%s,%s,%s,%s,%s,%s,%s) AS event",
                       (code, kind, caller, actor, idempotency_key, request_hash, Jsonb(payload))).fetchone()
    return row["event"]


def queue(conn, *, after_code: int, limit: int, status: str | None, signal: str | None) -> dict:
    # One set-based query; no per-fund NAV reads/calculation or N+1 history fetch.
    rows = conn.execute(
        "SELECT f.amfi_scheme_code AS code,f.scheme_name,f.option_type,"
        "COALESCE(e.version,0) AS version,COALESCE(reviewed.status,'pending_review') AS status,"
        "q.signals,r.document->>'input_hash' AS reviewed_input_hash,"
        "CASE WHEN reviewed.version IS NULL THEN NULL ELSE "
        "s.source_revision IS DISTINCT FROM rev.revision OR "
        "(s.document->>'dataset_version')::bigint IS DISTINCT FROM ds.dataset_version OR "
        "s.document->'identity' IS DISTINCT FROM jsonb_build_object(" +
        ",".join("'"+c+"',f."+c for c in IDENTITY_COLUMNS) + ") OR "
        "s.document->'facts' IS DISTINCT FROM CASE WHEN ff.amfi_scheme_code IS NULL THEN 'null'::jsonb "
        "ELSE jsonb_build_object(" + ",".join("'"+c+"',CASE WHEN ff."+c+" IS NULL THEN NULL ELSE ff."+c+"::text END" for c in FACT_COLUMNS) + ") END "
        "OR (s.document->'context'->'nav_quality'->>'assessed_at')::timestamptz IS DISTINCT FROM q.assessed_at END AS dependency_changed "
        "FROM mf.funds f CROSS JOIN mf.dataset_summary ds "
        "LEFT JOIN LATERAL(SELECT version FROM mf_review.events WHERE amfi_scheme_code=f.amfi_scheme_code ORDER BY version DESC LIMIT 1)e ON true "
        "LEFT JOIN LATERAL(SELECT version,status,result_id FROM mf_review.events WHERE amfi_scheme_code=f.amfi_scheme_code "
        "AND kind='review' ORDER BY version DESC LIMIT 1)reviewed ON true "
        "LEFT JOIN mf_review.results r ON r.result_id=reviewed.result_id LEFT JOIN mf_review.snapshots s USING(input_hash) "
        "LEFT JOIN mf.nav_source_revisions rev ON rev.amfi_scheme_code=f.amfi_scheme_code "
        "LEFT JOIN mf.nav_quality_assessments q ON q.amfi_scheme_code=f.amfi_scheme_code "
        "LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code=f.amfi_scheme_code "
        "WHERE ds.singleton AND f.amfi_scheme_code>%s AND (%s::text IS NULL OR COALESCE(reviewed.status,'pending_review')=%s) "
        "AND (%s::text IS NULL OR %s=ANY(q.signals)) ORDER BY f.amfi_scheme_code LIMIT %s",
        (after_code, status, status, signal, signal, limit),
    ).fetchall()
    items = []
    for row in rows:
        changed = row.pop("dependency_changed")
        items.append({**_json_source(row), "needs_revalidation": True if changed else None})
    return {"items": items, "next_after_code": rows[-1]["code"] if len(rows) == limit else None,
            "revalidation_basis": "summary only; null means unchecked; detail performs exact input fingerprint check"}
