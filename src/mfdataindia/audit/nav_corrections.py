"""DATA-03/04: finite, approved corrections with atomic API-ready publication.

Caller owns the transaction. There is no implicit approval or cloud action.
"""
from __future__ import annotations

from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from mfdataindia.audit import nav_integrity
from mfdataindia.audit.remediation import _lock_review_snapshot, canonical, fingerprint, seal, verify
from mfdataindia.audit.remediation_rehearsal import _row, validate_proposal
from mfdataindia.jobs.nav_calculations import recompute
from mfdataindia.store.locks import acquire_nav_data_operation_lock

WRITER = "mfdata_nav_correction_writer"
REVIEWER = "mfdata_remediation_review"


def require_principal(conn, expected: str) -> None:
    role = conn.execute(
        "SELECT current_user AS name,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
        "FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if not role or role["name"] != expected or any(role[k] for k in role if k != "name"):
        raise ValueError("operation requires the restricted dedicated principal")
    if conn.execute(
        "SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)"
    ).fetchone():
        raise ValueError("principal retains inherited role membership")
    if conn.execute(
        "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='mf' AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)"
    ).fetchone():
        raise ValueError("runtime principal must not own mf relations")
    if conn.execute("SELECT has_schema_privilege(current_user,'mf','CREATE') AS allowed").fetchone()["allowed"]:
        raise ValueError("runtime principal must not have schema CREATE")
    if conn.execute("SELECT has_database_privilege(current_user,current_database(),'CREATE') AS allowed").fetchone()['allowed']:
        raise ValueError('runtime principal must not have database CREATE')


def validate_approval(approval: dict, proposal: dict, restore: dict) -> None:
    for value in (approval, proposal, restore):
        verify(value)
    if approval.get("format") != "mfdata-nav-correction-approval-v1" or approval.get("status") != "APPROVED_FINITE_BATCH":
        raise ValueError("explicit finite-batch approval required")
    if approval.get("proposal_sha256") != proposal["sha256"] or approval.get("restore_proof_sha256") != restore["sha256"]:
        raise ValueError("approval is not bound to the proposal and restore proof")
    for key in ("approver_reference", "reason"):
        if not isinstance(approval.get(key), str) or not 0 < len(approval[key].strip()) <= 2000:
            raise ValueError("approval needs a bounded approver reference and reason")
    if (
        restore.get("format") != "mfdata-nav-restore-proof-v1"
        or restore.get("status") != "RESTORE_AND_REHEARSAL_VERIFIED"
        or restore.get("proposal_sha256") != proposal["sha256"]
        or restore.get("dataset_version") != proposal["expected_dataset_version"]
        or not str(restore.get("restored_database", "")).startswith("mfdata_remediation_rehearsal_")
        or not restore.get("backup_id")
        or not restore.get("independent_verifier_reference")
    ):
        raise ValueError("independently verified operational restore proof required")
    rehearsal = restore.get("rehearsal", {})
    verify(rehearsal)
    if (
        rehearsal.get("status") != "SCRATCH_REHEARSAL_PASSED_AND_ROLLED_BACK"
        or rehearsal.get("proposal_sha256") != proposal["sha256"]
        or rehearsal.get("database") != restore["restored_database"]
        or rehearsal.get("dataset_version") != restore["dataset_version"]
        or rehearsal.get("baseline_sha256") != rehearsal.get("restored_sha256")
        or rehearsal.get("exact_replay_verified") is not True
        or rehearsal.get("conditional_restoration_verified") is not True
    ):
        raise ValueError("restore proof has no matching successful rehearsal")


def record_approval(conn, approval: dict, proposal: dict, restore: dict, archive) -> str:
    require_principal(conn, REVIEWER)
    validate_proposal(proposal, archive)
    validate_approval(approval, proposal, restore)
    _lock_review_snapshot(conn, proposal["case_snapshot_sha256"])
    row = conn.execute(
        "SELECT payload FROM mf.nav_remediation_proposals WHERE proposal_sha256=%s", (proposal["sha256"],)
    ).fetchone()
    if not row or row["payload"] != proposal:
        raise ValueError("proposal must first be recorded by the evidence reviewer")
    existing = conn.execute(
        "SELECT payload FROM mf.nav_correction_approvals WHERE proposal_sha256=%s", (proposal["sha256"],)
    ).fetchone()
    payload = seal({"approval": approval, "restore_proof": restore,
                    "proposal_sha256": proposal["sha256"], "status": approval["status"]})
    if existing:
        if existing["payload"] != payload:
            raise ValueError("a different approval already exists")
        return "ALREADY_APPROVED"
    conn.execute("INSERT INTO mf.nav_correction_approvals(proposal_sha256,payload) VALUES(%s,%s)",
                 (proposal["sha256"], Jsonb(payload)))
    return "APPROVAL_RECORDED_NO_SOURCE_WRITES"


def execute(conn, proposal: dict, archive, *, expected_version: int,
            approval_sha256: str, rollback: bool = False, fail_at: str | None = None) -> dict:
    """One source/derived/audit/receipt transaction; exceptions MUST roll back.

    fail_at is a library-only fault injection seam. It is never a CLI flag.
    Successful publication does not constitute external Insights certification.
    """
    require_principal(conn, WRITER)
    validate_proposal(proposal, archive)
    acquire_nav_data_operation_lock(conn)
    _lock_review_snapshot(conn, proposal["case_snapshot_sha256"])
    conn.execute("SET LOCAL statement_timeout = '120s'")
    if conn.execute("SELECT has_table_privilege(current_user,'mf.nav_history','INSERT,UPDATE,DELETE,TRUNCATE') "
                    "OR has_any_column_privilege(current_user,'mf.nav_history','INSERT,UPDATE') AS allowed").fetchone()["allowed"]:
        raise ValueError("correction principal must have no direct source write privileges")
    if conn.execute("SELECT has_function_privilege(current_user,'mf.correct_nav_observation(text,integer,date,bigint,boolean)','EXECUTE') AS allowed").fetchone()['allowed']:
        raise ValueError('row correction primitive must not be granted to the runtime')
    stored = conn.execute(
        "SELECT p.payload AS proposal,a.payload AS approval FROM mf.nav_remediation_proposals p "
        "JOIN mf.nav_correction_approvals a USING(proposal_sha256) WHERE proposal_sha256=%s", (proposal["sha256"],)
    ).fetchone()
    if not stored or stored["proposal"] != proposal:
        raise ValueError("unknown recorded approved proposal")
    verify(stored["approval"])
    approval = stored["approval"]["approval"]
    validate_approval(approval, proposal, stored["approval"]["restore_proof"])
    if approval["sha256"] != approval_sha256:
        raise ValueError("approval hash differs from the confirmed operation")
    action = "rollback" if rollback else "apply"
    operations = conn.execute(
        "SELECT action,payload FROM mf.nav_remediation_operations WHERE proposal_sha256=%s", (proposal["sha256"],)
    ).fetchall()
    receipts = {r["action"]: r["payload"] for r in operations}
    if action == "apply" and "rollback" in receipts:
        raise ValueError("rolled-back proposal cannot be reapplied; obtain a fresh review")
    codes = sorted({c["amfi_scheme_code"] for c in proposal["changes"]})
    current_revision = {
        str(r["amfi_scheme_code"]): r["revision"] for r in conn.execute(
            "SELECT * FROM mf.nav_source_revisions WHERE amfi_scheme_code=ANY(%s)", (codes,)
        ).fetchall()
    }
    if action in receipts:
        receipt = receipts[action]
        verify(receipt)
        images = [_row(conn, c) for c in proposal["changes"]]
        if canonical(images) != canonical(receipt["after_images"]) or current_revision != receipt["source_revisions"]:
            raise ValueError("operation replay conflicts with later source changes")
        published = conn.execute(
            "SELECT * FROM mf.nav_calculations WHERE amfi_scheme_code=ANY(%s)", (receipt['calculated_codes'],)
        ).fetchall()
        calculated = {str(r['amfi_scheme_code']): {'source_revision':r['source_revision'],
            'as_of':r['as_of'].isoformat(), 'metrics':r['metrics'], 'profile':r['profile']} for r in published}
        if any(r['methodology_version'] != '1' for r in published) or fingerprint(calculated) != receipt['calculation_sha256']:
            raise ValueError('operation replay conflicts with later calculation changes')
        return receipt  # Historical receipt; not a claim of fresh global certification.
    if rollback and "apply" not in receipts:
        raise ValueError("rollback requires an applied receipt")
    # Preserve later observations at OTHER keys. Restore only exact applied
    # target images, then rebuild calculations from the latest complete series.
    live_version = conn.execute("SELECT dataset_version FROM mf.dataset_summary WHERE singleton").fetchone()["dataset_version"]
    if live_version != expected_version or (not rollback and live_version != proposal["expected_dataset_version"]):
        raise ValueError("dataset version changed; operation rejected")
    if not rollback:
        case = conn.execute("SELECT payload FROM mf.nav_remediation_snapshots WHERE snapshot_sha256=%s",
                            (proposal['case_snapshot_sha256'],)).fetchone()['payload']
        case_codes = case['scheme_codes']
        reviewed = proposal['reviewed_source_revisions']
        revisions = {str(r['amfi_scheme_code']): r['revision'] for r in conn.execute(
            "SELECT * FROM mf.nav_source_revisions WHERE amfi_scheme_code=ANY(%s)", (case_codes,)
        ).fetchall()}
        if reviewed != case.get('reviewed_source_revisions') or revisions != reviewed:
            raise ValueError('reviewed source dependencies changed; obtain a fresh review')
        if conn.execute(
            "SELECT 1 FROM mf.nav_source_revisions r LEFT JOIN mf.nav_quality_assessments q USING(amfi_scheme_code) "
            "WHERE r.amfi_scheme_code=ANY(%s) AND (q.assessed_source_revision IS DISTINCT FROM r.revision "
            "OR q.assessed_dataset_version IS DISTINCT FROM %s OR q.methodology_version IS DISTINCT FROM '1') LIMIT 1",
            (case_codes, live_version),
        ).fetchone():
            raise ValueError('reviewed source dependencies changed; refresh audit and review')
        identities = conn.execute(
            "SELECT f.amfi_scheme_code,jsonb_build_object('scheme_name',f.scheme_name,'amc_id',f.amc_id,"
            "'amc_name',am.amfi_amc_name,'scheme_type',f.scheme_type,'scheme_category',f.scheme_category,"
            "'plan_type',f.plan_type,'option_type',f.option_type,'periodicity',f.periodicity,"
            "'isin_primary',f.isin_primary,'isin_payout',f.isin_growth_or_div_payout,'isin_reinvest',f.isin_div_reinvest,"
            "'is_defunct',f.is_defunct,'last_seen_in_source',f.last_seen_in_source,'group_key',fv.group_key) AS identity "
            "FROM mf.funds f JOIN mf.amcs am USING(amc_id) LEFT JOIN mf.fund_variants fv USING(amfi_scheme_code) "
            "WHERE f.amfi_scheme_code=ANY(%s)", (case_codes,),
        ).fetchall()
        if {r['amfi_scheme_code']:r['identity'] for r in identities} != {m['amfi_scheme_code']:m['identity'] for m in case['members']}:
            raise ValueError('reviewed fund identity changed; obtain a new case review')
    # The dedicated principal has no NAV UPDATE rights (including FOR UPDATE).
    # The SQL capability rechecks and row-locks each exact approved observation.
    before_images = [_row(conn, c) for c in proposal["changes"]]
    conn.execute('SELECT mf.correct_nav_batch(%s,%s,%s)', (proposal['sha256'],live_version,rollback))
    if fail_at == 'source':
        raise RuntimeError('simulated failure after source correction')
    # Include dirty counterpart dependencies; their NAVs are NEVER changed.
    affected = sorted(set(codes) | {r['amfi_scheme_code'] for r in conn.execute(
        "SELECT r.amfi_scheme_code FROM mf.nav_source_revisions r LEFT JOIN mf.nav_calculations c USING(amfi_scheme_code) "
        "WHERE (c.amfi_scheme_code IS NULL OR c.source_revision<>r.revision OR c.methodology_version<>'1') "
        "AND EXISTS(SELECT 1 FROM mf.nav_history n WHERE n.amfi_scheme_code=r.amfi_scheme_code)"
    ).fetchall()})
    calculations = recompute(conn, affected)
    if fail_at == "calculations":
        raise RuntimeError("simulated failure after calculations")
    version = conn.execute("SELECT dataset_version FROM mf.refresh_dataset_summary(%s)",
                           ("nav_remediation_" + action,)).fetchone()["dataset_version"]
    if version != live_version + 1:
        raise RuntimeError("dataset version did not advance exactly once")
    audit = nav_integrity.assess(conn, expected_dataset_version=version, verify_current=True)
    if fail_at == "audit":
        raise RuntimeError("simulated failure after audit")
    after = [_row(conn, c) for c in proposal["changes"]]
    receipt = seal({
        "format": "mfdata-nav-correction-receipt-v1", "action": action,
        "transaction_id": conn.execute("SELECT pg_current_xact_id()::text AS id").fetchone()['id'],
        "proposal_sha256": proposal["sha256"], "approval_sha256": approval_sha256,
        "before_dataset_version": live_version, "dataset_version": version,
        "before_images": before_images, "after_images": after,
        "source_revisions": {c: r["source_revision"] for c,r in calculations.items() if int(c) in codes},
        "calculation_sha256": fingerprint(calculations), "audit": audit,
        "calculations": calculations,
        "calculated_codes": affected,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": "SOURCE_AND_DERIVED_DATA_COMMITTED_TOGETHER",
        "external_consumer_certified": False,
    })
    conn.execute("INSERT INTO mf.nav_remediation_operations(proposal_sha256,action,payload) VALUES(%s,%s,%s)",
                 (proposal["sha256"], action, Jsonb(receipt)))
    if fail_at == "receipt":
        raise RuntimeError("simulated failure after receipt")
    return receipt
