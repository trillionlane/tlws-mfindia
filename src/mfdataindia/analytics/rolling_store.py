"""Rolling sidecar persistence and certified projection; no source writes."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from datetime import date

from psycopg.types.json import Jsonb

from mfdataindia.analytics import rolling
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def verify_acl(conn, *, principal=None):
    identity = principal or conn.execute("SELECT current_user AS role").fetchone()["role"]
    if identity not in (
        "mfdata_verified_generator",
        "mfdata_verified_reader",
        "mfdata_verified_admin",
    ):
        raise ValueError("dedicated analytics runtime principal required")
    owner = conn.execute(
        "SELECT pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_rolling'"
    ).fetchone()
    if not owner or owner["owner"] not in ("mfdata_admin", "postgres"):
        raise ValueError("analytics namespace not ready or owner differs")
    inventory = conn.execute(
        "SELECT c.relname,pg_get_userbyid(c.relowner) AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mf_rolling' AND c.relkind IN('r','p','m','v','S')"
    ).fetchall()
    if {r["relname"] for r in inventory} != {"rolling_results", "rolling_certifications"} or any(
        r["owner"] != owner["owner"] for r in inventory
    ):
        raise ValueError("analytics relation inventory/ownership drift")
    triggers = conn.execute(
        "SELECT t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mf_rolling' AND NOT t.tgisinternal"
    ).fetchall()
    if {r["tgname"] for r in triggers} != {
        "rolling_result_sealed",
        "rolling_certificate_sealed",
        "rolling_result_immutable",
        "rolling_certificate_immutable",
    } or any(r["tgenabled"] != "O" for r in triggers):
        raise ValueError("analytics sealing/immutability trigger drift")
    functions = conn.execute(
        "SELECT p.proname,p.prosecdef,pg_get_userbyid(p.proowner) AS owner FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf_rolling'"
    ).fetchall()
    if {r["proname"] for r in functions} != {
        "seal_rolling_result",
        "seal_rolling_certification",
    } or any(not r["prosecdef"] or r["owner"] != owner["owner"] for r in functions):
        raise ValueError("analytics function ownership/capability drift")
    objects = conn.execute("""
        WITH objects AS (
          SELECT 'table' AS kind,c.relname AS name,c.relowner AS owner,c.relacl AS acl
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_rolling'
          UNION ALL SELECT 'table',c.relname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_rolling' AND a.attnum>0 AND NOT a.attisdropped
          UNION ALL SELECT 'function',p.proname,p.proowner,COALESCE(p.proacl,acldefault('f'::"char",p.proowner))
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf_rolling'
          UNION ALL SELECT 'schema',nspname,nspowner,nspacl FROM pg_namespace WHERE nspname='mf_rolling'
        ) SELECT kind,name,pg_get_userbyid(owner) AS owner,COALESCE(r.rolname,'PUBLIC') AS grantee,x.privilege_type AS permission
          FROM objects CROSS JOIN LATERAL aclexplode(acl) x LEFT JOIN pg_roles r ON r.oid=x.grantee WHERE x.grantee<>owner
    """).fetchall()
    for grant in objects:
        allowed = set()
        role = grant["grantee"]
        if role in ("mfdata_verified_generator", "mfdata_verified_reader", "mfdata_verified_admin"):
            if grant["kind"] == "schema":
                allowed = {"USAGE"}
            elif grant["kind"] == "table" and grant["name"] in (
                "rolling_results",
                "rolling_certifications",
            ):
                allowed = {"SELECT"} | (
                    {"INSERT"}
                    if role == "mfdata_verified_generator" and grant["name"] == "rolling_results"
                    else set()
                )
        if grant["owner"] != owner["owner"] or grant["permission"] not in allowed:
            raise ValueError("analytics privileges escape declared capabilities")
    for table in ("rolling_results", "rolling_certifications"):
        value = conn.execute(
            "SELECT has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR has_any_column_privilege(%s,%s,'UPDATE') AS rewrite, "
            "has_table_privilege(%s,%s,'INSERT') OR has_any_column_privilege(%s,%s,'INSERT') AS append",
            (identity, "mf_rolling." + table) * 4,
        ).fetchone()
        if value["rewrite"] or value["append"] != (
            identity == "mfdata_verified_generator" and table == "rolling_results"
        ):
            raise ValueError("analytics effective write capabilities differ")
    if conn.execute(
        "SELECT has_schema_privilege(%s,'mf_rolling','CREATE') AS allowed", (identity,)
    ).fetchone()["allowed"]:
        raise ValueError("analytics runtime retains DDL")
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s",
        (fingerprint(rolling.policy()),),
    ).fetchone()
    if not row or row["document"] != rolling.policy():
        raise ValueError("rolling policy registry not installed or differs")


def publish(conn, snapshots):
    with conn.transaction():
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "read committed"
        ):
            raise ValueError("rolling publication requires READ COMMITTED under source lock")
        if (
            not snapshots
            or len(snapshots) > review.MAX_CODES
            or len({s["code"] for s in snapshots}) != len(snapshots)
        ):
            raise ValueError("bounded unique rolling cohort required")
        acquire_nav_data_operation_lock(conn)
        document = rolling.policy()
        ph = fingerprint(document)
        results = []
        for snapshot in sorted(snapshots, key=lambda s: s["code"]):
            if fingerprint(review.capture(conn, snapshot["code"])) != fingerprint(snapshot):
                raise review.Conflict("rolling source/context changed after capture")
            result = rolling.derive(snapshot, document)
            review._insert_exact(
                conn,
                "policies",
                "policy_hash",
                ph,
                document,
                {"policy_id": document["id"], "policy_version": document["version"]},
            )
            review._insert_exact(
                conn,
                "snapshots",
                "input_hash",
                result["input_hash"],
                snapshot,
                {
                    "amfi_scheme_code": snapshot["code"],
                    "source_revision": snapshot["source_revision"],
                },
            )
            conn.execute(
                "INSERT INTO mf_rolling.rolling_results(result_id,input_hash,policy_hash,engine_fingerprint,document) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(result_id) DO NOTHING",
                (
                    result["result_id"],
                    result["input_hash"],
                    ph,
                    result["engine_fingerprint"],
                    Jsonb(result),
                ),
            )
            row = conn.execute(
                "SELECT document FROM mf_rolling.rolling_results WHERE result_id=%s",
                (result["result_id"],),
            ).fetchone()
            if row["document"] != result:
                raise review.Conflict("retained rolling artifact differs from replay")
            results.append(result)
        return results


def present(snapshot, retained, result, certificate):
    current = fingerprint(snapshot)
    base = {
        "format": "rolling-returns-response-v1",
        "code": snapshot["code"],
        "current_input_hash": current,
        "current_dataset_version": snapshot["dataset_version"],
        "current_context": snapshot["context"],
        "policy_hash": fingerprint(rolling.policy()),
        "policy_scope": "provider_contract_conformance_not_fund_correctness",
        "current_comparison_eligible": False,
        "historical_window_reusable": False,
        "withheld_reasons": [],
        "artifact": None,
        "audit_only": False,
    }
    if result is None:
        return {**base, "state": "not_certified", "withheld_reasons": ["no_rolling_certificate"]}
    reasons = []
    if not result["eligible_at_capture"]:
        reasons.append("ineligible_at_capture")
    if (
        result["policy_hash"] != base["policy_hash"]
        or result["engine"]["code_hash"] != rolling.engine_identity()["code_hash"]
    ):
        reasons.append("rolling_policy_or_engine_not_supported")
    historical = [
        p
        for p in snapshot["observations"]
        if result["first_date"]
        and result["as_of"]
        and result["first_date"] <= p["date"] <= result["as_of"]
    ]
    if historical != retained["observations"]:
        reasons.append("historical_observations_changed")
    if {k: v for k, v in snapshot["identity"].items() if k != "last_seen_in_source"} != {
        k: v for k, v in retained["identity"].items() if k != "last_seen_in_source"
    }:
        reasons.append("identity_changed")
    quality = snapshot["context"]["nav_quality"]
    if quality["signals"] is None:
        reasons.append("quality_evidence_unavailable")
    elif quality["signals"]:
        reasons.append("disclosed_quality_signal")
    if quality["methodology_version"] != retained["context"]["nav_quality"]["methodology_version"]:
        reasons.append("quality_methodology_changed")
    exact = current == result["input_hash"]
    reusable = not reasons
    state = (
        "current_artifact_certified"
        if exact
        else "historical_certified"
        if reusable
        else "historical_invalidated"
    )
    return {
        **base,
        "state": state,
        "current_comparison_eligible": exact
        and reusable
        and snapshot["context"]["methodology"]["comparison_eligible"],
        "historical_window_reusable": reusable,
        "withheld_reasons": reasons,
        "artifact": {
            "result_id": result["result_id"],
            "input_hash": result["input_hash"],
            "dataset_version": result["dataset_version"],
            "source_revision": result["source_revision"],
            "first_date": result["first_date"],
            "as_of": result["as_of"],
            "policy_hash": result["policy_hash"],
            "engine": result["engine"],
            "context": result["context"],
            "certificate_id": certificate["certificate_id"],
            "certified_at": certificate["certified_at"],
            "runtime_git_sha": certificate["runtime_git_sha"],
            "rolling": result["rolling"] if reusable else None,
        },
    }


def get_rolling(conn, code, result_id=None):
    snapshot = review.capture(conn, code)
    if result_id:
        row = conn.execute(
            "SELECT r.document FROM mf_rolling.rolling_results r JOIN mf_review.snapshots s USING(input_hash) "
            "WHERE r.result_id=%s AND s.amfi_scheme_code=%s",
            (result_id, code),
        ).fetchone()
        if not row:
            raise review.NotFound("rolling audit artifact absent")
        return {
            "audit_only": True,
            "result": row["document"],
            "current_input_hash": fingerprint(snapshot),
            "current_context": snapshot["context"],
            "input_status": "current"
            if row["document"]["input_hash"] == fingerprint(snapshot)
            else "stale",
        }
    row = conn.execute(
        """
        SELECT c.certificate_id,c.certified_at,c.runtime_git_sha,r.document AS result,s.document AS input
          FROM mf_rolling.rolling_certifications c
          CROSS JOIN LATERAL jsonb_array_elements(c.document->'certificate'->'evidence'->'cohort') member
          JOIN mf_rolling.rolling_results r ON r.result_id=member->>'result_id'
          JOIN mf_review.snapshots s ON s.input_hash=r.input_hash
          WHERE (member->>'code')::integer=%s AND s.amfi_scheme_code=%s AND c.policy_hash=%s
          ORDER BY c.certified_at DESC,c.certificate_id DESC LIMIT 1
    """,
        (code, code, fingerprint(rolling.policy())),
    ).fetchone()
    return (
        present(snapshot, row["input"], row["result"], row)
        if row
        else present(snapshot, None, None, None)
    )


def register(conn, document):
    identity = conn.execute(
        "SELECT session_user AS role,current_user=session_user AS direct"
    ).fetchone()
    if not identity["direct"] or identity["role"] not in ("mfdata_admin", "postgres"):
        raise ValueError("direct migration owner only records verified rolling certificates")
    if document["policy_hash"] != fingerprint(rolling.policy()):
        raise ValueError("rolling certificate policy differs")
    identifier = fingerprint(document)
    conn.execute(
        "INSERT INTO mf_rolling.rolling_certifications(certificate_id,policy_hash,runtime_git_sha,certified_at,document) "
        "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(certificate_id) DO NOTHING",
        (
            identifier,
            document["policy_hash"],
            document["manifest"]["git_sha"],
            document["execution"]["status"]["completionTime"],
            Jsonb(document),
        ),
    )
    if (
        conn.execute(
            "SELECT document FROM mf_rolling.rolling_certifications WHERE certificate_id=%s",
            (identifier,),
        ).fetchone()["document"]
        != document
    ):
        raise review.Conflict("rolling certificate retained record differs")
    return identifier


def page(payload, *, years=1, limit=100, cursor=None):
    """Cursor selects a boundary within the currently certified artifact only.

    It is not an authorization token and cannot select a staged/unregistered
    artifact. A newer selected result requires a fresh page-one request.
    """
    if (
        type(years) is not int
        or years not in (1, 3, 5)
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValueError("unsupported rolling term/page bound")
    value = deepcopy(payload)
    artifact = value["artifact"]
    identifier = artifact["result_id"] if artifact else None
    data = artifact["rolling"] if artifact else None
    rows = []
    if data:
        selected = next(t for t in data["terms"] if t["years"] == years)
        rows = selected["windows"]
    offset = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512:
                raise ValueError("oversized cursor")
            decoded = base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            )
            binding = json.loads(decoded)
            if not isinstance(binding, dict) or set(binding) != {
                "v",
                "code",
                "result_id",
                "years",
                "after",
            }:
                raise ValueError("cursor fields differ")
            if (
                type(binding["v"]) is not int
                or binding["v"] != 1
                or type(binding["code"]) is not int
                or type(binding["years"]) is not int
            ):
                raise ValueError("cursor integer fields differ")
            date.fromisoformat(binding["after"])
            if binding["code"] != value["code"] or binding["years"] != years:
                raise ValueError("cursor fund/term differs")
            if not identifier or binding["result_id"] != identifier or not data:
                raise review.Conflict(
                    "rolling selected artifact or eligibility changed; restart paging"
                )
            positions = [i for i, r in enumerate(rows) if r["end_boundary"] == binding["after"]]
            if len(positions) != 1:
                raise ValueError("cursor boundary absent")
            offset = positions[0] + 1
        except (TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("malformed rolling cursor") from exc
    selected_rows = rows[offset : offset + limit]
    next_cursor = None
    if offset + len(selected_rows) < len(rows):
        binding = {
            "v": 1,
            "code": value["code"],
            "result_id": identifier,
            "years": years,
            "after": selected_rows[-1]["end_boundary"],
        }
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )
    if data:
        data["terms"] = [{"years": t["years"], "summary": t["summary"]} for t in data["terms"]]
    return {
        **value,
        "windows": selected_rows,
        "pagination": {
            "years": years,
            "result_id": identifier,
            "limit": limit,
            "total_windows": len(rows),
            "returned_windows": len(selected_rows),
            "next_cursor": next_cursor,
        },
    }
