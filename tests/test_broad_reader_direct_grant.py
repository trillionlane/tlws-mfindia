"""Provider-free transaction sequencing; no DB connection or live permission."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg.pq import TransactionStatus

from mfdataindia.review import broad_reader_direct_grant as grant
from mfdataindia.review import broad_reader_direct_plan as direct_plan
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_source_authority as source
from test_broad_reader_live_snapshot import _bundle


ROOT = Path(__file__).resolve().parents[1]


class _Column:
    def __init__(self, name):
        self.name = name


class _Cursor:
    def __init__(self, rows=(), *, status="SELECT 0", columns=()):
        self._rows = deepcopy(list(rows))
        self.rowcount = len(self._rows)
        self.statusmessage = status
        self.description = [_Column(name) for name in columns]

    def fetchone(self):
        return deepcopy(self._rows[0])

    def fetchall(self):
        return deepcopy(self._rows)


class _Info:
    def __init__(self, pid):
        self.backend_pid = pid
        self.transaction_status = TransactionStatus.IDLE


class _Connection:
    def __init__(self, pid, *, lock_rows=(), commit_fails=False):
        self.autocommit = True
        self.info = _Info(pid)
        self.statements = []
        self.lock_rows = list(lock_rows)
        self.commit_fails = commit_fails

    def execute(self, statement, parameters=None, *, prepare=False):
        assert prepare is False
        self.statements.append((statement, parameters))
        if statement.startswith("BEGIN "):
            self.info.transaction_status = TransactionStatus.INTRANS
            return _Cursor(status="BEGIN")
        if statement == "ROLLBACK":
            self.info.transaction_status = TransactionStatus.IDLE
            return _Cursor(status="ROLLBACK")
        if statement == "COMMIT":
            if self.commit_fails:
                raise OSError("synthetic unknown wire result")
            self.info.transaction_status = TransactionStatus.IDLE
            return _Cursor(status="COMMIT")
        if statement.startswith("SET LOCAL "):
            return _Cursor(status="SET")
        if statement == policy.CONTEXT_SQL:
            return _Cursor([grant._CONTEXT], status="SELECT 1")
        if statement == grant.DATABASE_OID_SQL:
            return _Cursor([{"oid": 42}], status="SELECT 1")
        if statement == grant.witness.LOCK_WITNESS_SQL:
            rows = self.lock_rows.pop(0)
            return _Cursor(rows, status="SELECT " + str(len(rows)), columns=(
                "pid", "database", "oid", "nspname", "relname", "relkind", "mode", "granted"))
        if statement.startswith("LOCK TABLE "):
            return _Cursor(status="LOCK TABLE")
        if statement.startswith("GRANT "):
            return _Cursor(status="GRANT")
        if statement.startswith("ALTER DEFAULT PRIVILEGES "):
            return _Cursor(status="ALTER DEFAULT PRIVILEGES")
        raise AssertionError("unexpected SQL")


def _case(monkeypatch, *, drift=False, commit_fails=False):
    before, after_state = _bundle()
    before["plan"].update({
        "before": deepcopy(before["state"]),
        "raw_full_input_sha256": policy.fingerprint(before["state"]),
        "permission_acceptance": False,
        "sql": ["GRANT SELECT ON TABLE mf.t TO mfdata_verified_reader"],
        "delta": {"schemas": [], "relations": [{"schema": "mf", "name": "t"}],
                  "defaults": []},
        "full_before_sha256": policy.fingerprint(before["state"]),
        "relation_scope": {"grant_roots": [
            {"oid": 4, "relkind": "r"}, {"oid": 5, "relkind": "v"}],
            "materialized_unlocked": []},
    })
    before["plan"]["reversal_template"] = direct_plan.reversal_template(before["plan"])
    after = deepcopy(before)
    after["state"] = after_state
    after["plan"] = {"after": after_state, "delta": {"relations": []}}
    if drift:
        before_reread = deepcopy(before)
        before_reread["ledger"].append({"migration_name": "unreviewed"})
    else:
        before_reread = deepcopy(before)
    snapshots = iter((before_reread, after))
    monkeypatch.setattr(grant.snapshot, "read", lambda *_args, **_kwargs: next(snapshots))
    monkeypatch.setattr(grant.dependencies, "lock_phases",
                        lambda _scope: (["LOCK TABLE ONLY mf.t IN ACCESS EXCLUSIVE MODE NOWAIT"],
                                        ["LOCK TABLE ONLY mf.v IN ACCESS SHARE MODE NOWAIT"]))
    monkeypatch.setattr(grant.witness, "verify_phases",
                        lambda *_args, **_kwargs: {"observed": True})
    now = datetime.now(timezone.utc).replace(microsecond=0)
    window = {"starts_at": (now - timedelta(seconds=1)).isoformat(),
              "ends_at": (now + timedelta(minutes=5)).isoformat(),
              "exclusive_administration": True, "data_schema_custody": True,
              "future_owner_provisioning": True}
    document = {"format": "mf-broad-reader-direct-grant-v1", "window": window,
                "before_snapshot": before, "helper_attestation": {},
                "packaged_source": source.verify_packaged_source(ROOT), "git_sha": "a" * 40,
                "image_binding": {kind: {
                    "image": ("asia-south1-docker.pkg.dev/trillionlane-dev/"
                              "tlws-mf-data-dev/" + repo + "@sha256:" + "c" * 64),
                    "resolved_image": ("asia-south1-docker.pkg.dev/trillionlane-dev/"
                                       "tlws-mf-data-dev/" + repo + "@sha256:" + "d" * 64),
                    "config_digest": "sha256:" + "e" * 64,
                    "git_sha": "a" * 40, "platform": "linux/amd64",
                    "layers_downloaded": 0}
                    for kind, repo in (("app", "mfdataindia"),
                                       ("tools", "mfdataindia-review-tools"))},
                "create_only_intent_sha256": "b" * 64}
    permit = grant.Permit(document, policy.fingerprint(document))
    target = _Connection(100, commit_fails=commit_fails)
    observer = _Connection(200, lock_rows=[[], [], [], []])
    return target, observer, permit


def test_exact_two_phase_write_has_separate_observer_and_still_needs_consumer():
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        target, observer, permit = _case(patch)
        receipt = grant.run(target, observer, permit, source_root=ROOT)
    sql = [statement for statement, _ in target.statements]
    assert sql.count("ROLLBACK") == 1  # confirmed preflight only
    assert sql.index("LOCK TABLE ONLY mf.t IN ACCESS EXCLUSIVE MODE NOWAIT") < sql.index(
        "LOCK TABLE ONLY mf.v IN ACCESS SHARE MODE NOWAIT") < sql.index(
            "GRANT SELECT ON TABLE mf.t TO mfdata_verified_reader") < sql.index("COMMIT")
    assert sql.count(policy.CONTEXT_SQL) == 1
    assert receipt["stage"] == "COMMITTED_STATE_VERIFIED"
    assert receipt["permission_enabled"] is receipt["ti_verified"] is False
    assert permit.used is True and observer.lock_rows == []
    with pytest.raises(policy.PolicyError, match="CONSUMED"):
        grant.run(target, observer, permit, source_root=ROOT)


def test_full_pregrant_drift_aborts_before_grant_and_confirms_rollback():
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        target, observer, permit = _case(patch, drift=True)
        with pytest.raises(grant.GrantError, match="PRECOMMIT_ABORTED"):
            grant.run(target, observer, permit, source_root=ROOT)
    sql = [statement for statement, _ in target.statements]
    assert sql.count("ROLLBACK") == 2  # preflight plus sole grant abort
    assert not any(statement.startswith("GRANT ") for statement in sql)
    assert "COMMIT" not in sql and observer.info.transaction_status == TransactionStatus.IDLE


def test_unknown_commit_never_rolls_back_or_retries():
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        target, observer, permit = _case(patch, commit_fails=True)
        with pytest.raises(grant.GrantError, match="COMMIT_OUTCOME_UNKNOWN"):
            grant.run(target, observer, permit, source_root=ROOT)
    sql = [statement for statement, _ in target.statements]
    assert sql.count("COMMIT") == 1 and sql.count("ROLLBACK") == 1
    assert observer.info.transaction_status == TransactionStatus.INTRANS


def test_complete_prewrite_capture_is_readonly_and_closes_before_writer():
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        class _Capture:
            def run(self, conn):
                assert conn.info.transaction_status == TransactionStatus.IDLE
                return {"attested": True}

        patch.setattr(grant.helper_capture, "Capture", _Capture)
        patch.setattr(grant.snapshot, "read", lambda *_args, **_kwargs: {"complete": True})
        observer = _Connection(200)
        result = grant.capture_before(
            observer, packaged_source=source.verify_packaged_source(ROOT),
            source_root=ROOT)
    assert result["grant_authorized"] is False
    assert result["before_snapshot"] == {"complete": True}
    assert [sql for sql, _ in observer.statements].count("ROLLBACK") == 1
    assert observer.info.transaction_status == TransactionStatus.IDLE
    assert not any(sql.startswith("GRANT ") for sql, _ in observer.statements)


@pytest.mark.parametrize("defect", ["wrong_git", "wrong_platform", "layers", "missing_tools"])
def test_final_image_binding_fails_before_any_owner_io(defect):
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        target, observer, permit = _case(patch)
        changed = deepcopy(permit.document)
        if defect == "wrong_git":
            changed["image_binding"]["app"]["git_sha"] = "f" * 40
        elif defect == "wrong_platform":
            changed["image_binding"]["app"]["platform"] = "linux/arm64"
        elif defect == "layers":
            changed["image_binding"]["tools"]["layers_downloaded"] = 1
        else:
            changed["image_binding"].pop("tools")
        permit = grant.Permit(changed, policy.fingerprint(changed))
        with pytest.raises(policy.PolicyError, match="UNBOUND_BROAD_READER_FINAL_IMAGES"):
            grant.run(target, observer, permit, source_root=ROOT)
    assert target.statements == observer.statements == []


def test_forged_matching_source_ledger_cannot_open_owner_connection():
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        target, observer, permit = _case(patch)
        changed = deepcopy(permit.document)
        changed["packaged_source"]["migration_ledger"][0]["content_sha256"] = "0" * 64
        permit = grant.Permit(changed, policy.fingerprint(changed))
        with pytest.raises(source.SourceAuthorityError,
                           match="UNMATCHED_BROAD_READER_SOURCE_BYTES"):
            grant.run(target, observer, permit, source_root=ROOT)
    assert permit.used is True
    assert target.statements == observer.statements == []
