"""Scratch-only transaction rehearsal. No production apply/rollback command.

Every exit rolls back. Successful rehearsal proves exact-row apply, replay
idempotency and conditional restoration, then verifies the original rows and
dataset version again after rolling back the whole transaction.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from mfdataindia.audit.remediation import canonical, fingerprint, seal, verify
from mfdataindia.audit.remediation_evidence import load_amfi_observations, nav_decimal
from mfdataindia.store.locks import acquire_nav_data_operation_lock

SCRATCH_PREFIX = "mfdata_remediation_rehearsal_"


def _row(conn, change: dict, *, lock: bool = False) -> dict:
    result = conn.execute(
        "SELECT * FROM mf.nav_history WHERE amfi_scheme_code=%s AND nav_date=%s"
        + (" FOR UPDATE" if lock else ""),
        (change["amfi_scheme_code"], change["nav_date"]),
    ).fetchone()
    if result is None:
        raise ValueError("target observation disappeared")
    return dict(result)


def _trial_apply(conn, changes: list[dict], *, replay_images=None, fail_after=None) -> list[dict]:
    originals = [_row(conn, change, lock=True) for change in changes]
    if replay_images is not None and canonical(originals) == canonical(replay_images):
        return originals  # Exact replay images, not merely the same NAV value.
    if any(
        canonical(row) != canonical(change["before"]) for row, change in zip(originals, changes)
    ):
        raise ValueError("before-image conflict; no observation is changed")
    after = []
    for index, change in enumerate(changes, start=1):
        conn.execute(
            "UPDATE mf.nav_history SET nav=%s, source='AMFI_HISTORY', "
            "is_cross_verified=false, ingested_at=now() "
            "WHERE amfi_scheme_code=%s AND nav_date=%s",
            (nav_decimal(change["after_nav"]), change["amfi_scheme_code"], change["nav_date"]),
        )
        after.append(_row(conn, change))
        if fail_after == index:
            raise RuntimeError("simulated interrupted correction")
    return after


def _trial_restore(conn, changes: list[dict], after_images: list[dict]) -> None:
    current = [_row(conn, change, lock=True) for change in changes]
    if canonical(current) != canonical(after_images):
        raise ValueError("rollback conflict; later observation changes are preserved")
    for change in changes:
        before = change["before"]
        conn.execute(
            "UPDATE mf.nav_history SET nav=%s, source=%s, is_cross_verified=%s, ingested_at=%s "
            "WHERE amfi_scheme_code=%s AND nav_date=%s",
            (
                nav_decimal(before["nav"]),
                before["source"],
                before["is_cross_verified"],
                before["ingested_at"],
                change["amfi_scheme_code"],
                change["nav_date"],
            ),
        )


def validate_proposal(proposal: dict, archive) -> None:
    verify(proposal)
    if (
        proposal.get("format") != "mfdata-nav-correction-proposal-v1"
        or proposal.get("status") != "PROPOSED_NOT_APPROVED_OR_APPLIED"
    ):
        raise ValueError("unsupported proposal")
    changes = proposal["changes"]
    revisions = proposal.get("reviewed_source_revisions")
    if (not isinstance(revisions, dict) or not revisions
            or any(not k.isdecimal() or int(k) <= 0 or type(v) is not int or v < 0
                   for k, v in revisions.items())):
        raise ValueError("proposal requires exact reviewed source dependencies")
    if not isinstance(changes, list) or not 0 < len(changes) <= 100:
        raise ValueError("proposal must contain 1..100 exact updates")
    keys = {(c["amfi_scheme_code"], c["nav_date"]) for c in changes}
    if len(keys) != len(changes) or len({c[0] for c in keys}) > 5:
        raise ValueError("duplicate keys or more than 5 schemes")
    if any(str(c[0]) not in revisions for c in keys):
        raise ValueError("proposal changes are outside reviewed source dependencies")
    observations = load_amfi_observations(proposal["evidence"], archive)
    for change in changes:
        before = change["before"]
        if (
            type(change["amfi_scheme_code"]) is not int
            or change["amfi_scheme_code"] <= 0
            or before["amfi_scheme_code"] != change["amfi_scheme_code"]
            or before["nav_date"] != change["nav_date"]
        ):
            raise ValueError("before-image identity differs from the exact target key")
        from datetime import date

        key = (change["amfi_scheme_code"], date.fromisoformat(change["nav_date"]))
        proposed = nav_decimal(change["after_nav"])
        if proposed == nav_decimal(before["nav"]) or observations.get(key) != proposed:
            raise ValueError("replacement differs from authoritative archived evidence")


def rehearse(
    dsn: str,
    proposal: dict,
    archive,
    *,
    confirm_scratch_database: str,
    simulate_failure_after: int | None = None,
) -> dict:
    validate_proposal(proposal, archive)
    if simulate_failure_after is not None and not 1 <= simulate_failure_after <= len(
        proposal["changes"]
    ):
        raise ValueError("simulated interruption must occur within the proposed batch")
    if not confirm_scratch_database.startswith(SCRATCH_PREFIX) or len(
        confirm_scratch_database
    ) <= len(SCRATCH_PREFIX):
        raise ValueError("rehearsal requires an explicitly named scratch database")
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=False) as conn:
        name = conn.execute("SELECT current_database() AS name").fetchone()["name"]
        conn.rollback()
        if name != confirm_scratch_database:
            raise ValueError("connected database differs from the confirmed scratch database")
        before, before_version = None, None
        try:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.execute("SET LOCAL statement_timeout = '120s'")
            acquire_nav_data_operation_lock(conn)
            before_version = conn.execute(
                "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
            ).fetchone()["dataset_version"]
            if before_version != proposal["expected_dataset_version"]:
                raise ValueError("scratch dataset version differs from proposal")
            revisions = {str(r['amfi_scheme_code']): r['revision'] for r in conn.execute(
                "SELECT * FROM mf.nav_source_revisions WHERE amfi_scheme_code=ANY(%s)",
                ([int(c) for c in proposal['reviewed_source_revisions']],),
            ).fetchall()}
            if revisions != proposal['reviewed_source_revisions']:
                raise ValueError("scratch reviewed source dependencies changed")
            before = [_row(conn, change) for change in proposal["changes"]]
            after = _trial_apply(conn, proposal["changes"], fail_after=simulate_failure_after)
            replay = _trial_apply(conn, proposal["changes"], replay_images=after)
            if canonical(after) != canonical(replay):
                raise RuntimeError("replay changed observations")
            version = conn.execute(
                "SELECT dataset_version FROM mf.refresh_dataset_summary('remediation_rehearsal')"
            ).fetchone()["dataset_version"]
            if version != before_version + 1:
                raise RuntimeError("dataset version was not advanced exactly once")
            _trial_restore(conn, proposal["changes"], after)
            if canonical([_row(conn, c) for c in proposal["changes"]]) != canonical(before):
                raise RuntimeError("conditional row restoration failed")
        finally:
            conn.rollback()  # This command has NO successful commit path.
        restored = [_row(conn, c) for c in proposal["changes"]]
        restored_version = conn.execute(
            "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
        ).fetchone()["dataset_version"]
        conn.rollback()
        if canonical(restored) != canonical(before) or restored_version != before_version:
            raise RuntimeError("rehearsal did not restore its baseline")
    return seal(
        {
            "status": "SCRATCH_REHEARSAL_PASSED_AND_ROLLED_BACK",
            "database": name,
            "proposal_sha256": proposal["sha256"],
            "observations_tested": len(before),
            "baseline_sha256": fingerprint(before),
            "restored_sha256": fingerprint(restored),
            "dataset_version": restored_version,
            "exact_replay_verified": True,
            "conditional_restoration_verified": True,
            "production_apply_available": False,
        }
    )
