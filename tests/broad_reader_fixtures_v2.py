"""Synthetic full-16 protocol fixtures; never live evidence or identity authority."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace

from psycopg.pq import TransactionStatus
from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import broad_reader_capture_spec_v3 as cs
from mfdataindia.review import broad_reader_capture_controller_v3 as cc
from mfdataindia.review import broad_reader_transport_spec_v2 as spec
from mfdataindia.review import broad_reader_transport_controller_v2 as core
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence
from test_reader_access_correction import frozen_state
from test_verified_review_rollout import manifest

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "123456789012"
JOB_UID, EXEC_UID = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"


def state(materialized=True):
    value = deepcopy(frozen_state())
    table = next(r for r in value["relations"] if r["relkind"] == "r")
    columns = [c for c in value["columns"] if c["oid"] == table["oid"]]
    for i, name in enumerate(policy.SCHEMAS):
        if name == "mf":
            continue
        ns, oid = 500000 + i, 600000 + i
        value["namespaces"].append(
            dict(oid=ns, nspname=name, owner=16587, acl=[], usage=False, create_allowed=False)
        )
        r = deepcopy(table)
        r.update(oid=oid, namespace_oid=ns, nspname=name, relname="synthetic_fund_data")
        value["relations"].append(r)
        for c in columns:
            value["columns"].append(deepcopy(c) | {"oid": oid})
    if materialized:
        r = deepcopy(table)
        r.update(oid=700000, relkind="m", relname="synthetic_materialized")
        value["relations"].append(r)
        for c in columns:
            value["columns"].append(deepcopy(c) | {"oid": 700000})
    for name, rows in value.items():
        order = (
            ("roleid", "member", "grantor")
            if name == "memberships"
            else ("oid", "attnum")
            if name == "columns"
            else ("oid",)
        )
        rows.sort(key=lambda r: tuple(r[k] for k in order))
    return value


def context():
    now = datetime.now(timezone.utc) - timedelta(seconds=5)
    return dict(
        database=policy.DATABASE,
        principal=policy.OWNER,
        session=policy.OWNER,
        version=180006,
        isolation="repeatable read",
        readonly="on",
        statement_timeout="5s",
        lock_timeout="5s",
        transaction_timeout="15s",
        search_path="pg_catalog",
        timezone="UTC",
        pid=1,
        snapshot="1:2:",
        transaction_started_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )


def sample():
    before = state()
    candidate = [
        {k: r[k] for k in cs.frozen.CANDIDATE_COLUMNS}
        for r in before["relations"]
        if r["relkind"] in ("r", "p", "v", "m")
    ]
    for row in candidate:
        if row["relkind"] == "m":
            raw = b"SELECT 7 AS value"
            row["definition"] = dict(
                is_null=False, sha256=hashlib.sha256(raw).hexdigest(), utf8_bytes=len(raw)
            )
    return dict(
        context=context(),
        state=before,
        candidate=candidate,
        completions=[
            dict(
                id=n,
                command_rows=len(candidate) if n == "candidate" else 1,
                driver_rows=len(candidate) if n == "candidate" else 1,
                fetched_rows=len(candidate) if n == "candidate" else 1,
            )
            for n in cs.frozen.inventory()["sequence"]
        ],
    )


def capture_approval():
    m = manifest(
        rollout_id=cc.IDENTITY,
        actor="rollout-certification:" + cc.IDENTITY,
        cohort=[154117],
        canary_code=154117,
    )
    return cc.Approval.model_validate(
        dict(
            manifest=m,
            controller_git_sha=m.git_sha,
            controller_sha256=hashlib.sha256(Path(cc.__file__).read_bytes()).hexdigest(),
            protected_hash=fingerprint({"protected": True}),
            root=str(cc.ARTIFACTS / "mf-reader-broad-capture-20261027-aBc123"),
        )
    )


def metadata(name, *, execution=False, annotations=None):
    value = dict(
        name=name,
        namespace=NAMESPACE,
        uid=EXEC_UID if execution else JOB_UID,
        creationTimestamp="2026-10-26T18:00:01Z" if execution else "2026-10-26T18:00:00Z",
        generation=1,
        labels={"cloud.googleapis.com/location": core.REGION},
        annotations={
            "run.googleapis.com/creator": core.ACCOUNT,
            "run.googleapis.com/lastModifier": core.ACCOUNT,
            **(annotations or {}),
        },
    )
    if execution:
        value["labels"].update(
            {
                "run.googleapis.com/job": cc.NAME,
                "run.googleapis.com/jobUid": JOB_UID,
                "run.googleapis.com/jobGeneration": "1",
            }
        )
    return value


def capture_completion():
    a = capture_approval()
    prepared = cc.inputs(a)
    raw = cs.result(sample())
    raw["worker_binding"] = dict(
        packaged_controller_sha256=a.controller_sha256,
        packaged_program_sha256=cc.WORKER_SHA,
        packaged_spec_sha256=cc.SPEC_SHA,
        packaged_inventory_sha256=cc.INVENTORY_SHA,
        manifest_image=a.manifest.image,
        manifest_git_sha=a.manifest.git_sha,
    )
    raw["input_binding"] = dict(
        root=a.root,
        approval_sha256=fingerprint(a.model_dump(mode="json")),
        request_sha256=hashlib.sha256(prepared["body"]).hexdigest(),
        controller_git_sha=a.controller_git_sha,
        controller_sha256=a.controller_sha256,
    )
    execution = cc.NAME + "-abcde"
    job = dict(
        apiVersion="run.googleapis.com/v1",
        kind="Job",
        metadata=metadata(cc.NAME),
        spec=deepcopy(prepared["request"]["spec"]),
        status=dict(
            executionCount=1,
            latestCreatedExecution=dict(name=execution, creationTimestamp="2026-10-26T18:00:01Z"),
            conditions=[dict(type="Ready", status="True")],
        ),
    )
    executed = dict(
        apiVersion="run.googleapis.com/v1",
        kind="Execution",
        metadata=metadata(execution, execution=True, annotations=prepared["annotations"]),
        spec=dict(taskCount=1, parallelism=1, template=dict(spec=deepcopy(prepared["task"]))),
        status=dict(succeededCount=1, conditions=[dict(type="Completed", status="True")]),
    )
    result = dict(
        approval=a.model_dump(mode="json"),
        namespace=NAMESPACE,
        execution=execution,
        certificate=seal_evidence(a.manifest, cc.STAGE, raw),
        job=job,
        execution_document=executed,
        approval_sha256=raw["input_binding"]["approval_sha256"],
        request_sha256=raw["input_binding"]["request_sha256"],
        diagnostic_only=True,
        acceptance=False,
        image_resolution=dict(
            image=a.manifest.image,
            resolved_image=a.manifest.image,
            config_digest="sha256:" + "d" * 64,
            git_sha=a.manifest.git_sha,
            platform="linux/amd64",
            layers_downloaded=0,
        ),
    )
    cc.validate_completion(result)
    return result


def window(capture):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    capture_time = capture["certificate"]["evidence"]["context"]["transaction_started_at"]
    return dict(
        reference="SYNTHETIC_G5_WINDOW",
        starts_at=(now - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ends_at=(now + timedelta(seconds=500)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        captured_at=datetime.fromisoformat(capture_time.replace("Z", "+00:00")).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        exclusive_admin=True,
        recursive_availability_approved=True,
        data_schema_custody=True,
        future_owner_provisioning=True,
    )


def payload():
    capture = capture_completion()
    m = manifest(
        rollout_id=spec.IDENTITY,
        actor="rollout-certification:" + spec.IDENTITY,
        cohort=[154117],
        canary_code=154117,
    )
    return dict(
        manifest=m.model_dump(mode="json"),
        controller_git_sha=m.git_sha,
        controller_sha256=hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
        protected_hash=fingerprint({"protected": True}),
        root=str(spec.ARTIFACTS / "mf-reader-broad-access-20261027-aBc123"),
        grant_proposal=spec.proposal(capture, window(capture)),
        g5_reference="SYNTHETIC_NOT_HUMAN_APPROVAL",
    )


def approval():
    return core.Approval.model_validate(core.approval_document(payload()))


def worker(path="apply_broad_reader_access_dev_v2.py"):
    return SimpleNamespace(**runpy.run_path(str(ROOT / "scripts" / path)))


def environment(p):
    a = core.Approval.model_validate(core.approval_document(p))
    return core.inputs(a)["environment"] | dict(
        CLOUD_RUN_JOB=spec.JOB,
        CLOUD_RUN_EXECUTION=spec.JOB + "-abcde",
        CLOUD_RUN_TASK_INDEX="0",
        CLOUD_RUN_TASK_ATTEMPT="0",
        CLOUD_RUN_TASK_COUNT="1",
        MFDATAINDIA_DSN="host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=mfdataindia user=mfdata_admin password=SYNTHETIC_ONLY",
    )


class Cursor:
    def __init__(self, rows, columns, tag=None):
        self.rows = deepcopy(rows)
        self.description = [SimpleNamespace(name=c) for c in columns]
        self.rowcount = len(rows)
        self.statusmessage = tag or "SELECT " + str(len(rows))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def fetchall(self):
        return deepcopy(self.rows)

    def fetchone(self):
        return deepcopy(self.rows[0])


class OwnerConnection:
    autocommit = True
    info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def __init__(self, p, fail=None, witness=None):
        self.p = p
        self.fail = fail
        self.witness = deepcopy(p["lock_witness"] if witness is None else witness)
        self.calls = []
        self.snapshots = 0
        self.readonly = False
        self.closed = 0

    def execute(self, statement, *, prepare):
        from mfdataindia.review.reader_access_owner_connection import LOCK_WITNESS_SQL

        assert prepare is False
        text = statement if type(statement) is str else statement.as_string()
        self.calls.append(text)
        if text == self.fail:
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")
        if text.startswith("BEGIN"):
            self.readonly = "READ ONLY" in text
        if text == LOCK_WITNESS_SQL:
            return Cursor(self.witness, cs.frozen.CANDIDATE_COLUMNS)
        if text == policy.CONTEXT_SQL:
            return Cursor(
                [
                    dict(
                        database=policy.DATABASE,
                        principal=policy.OWNER,
                        session=policy.OWNER,
                        version=180006,
                        isolation="read committed",
                        readonly="on" if self.readonly else "off",
                        statement_timeout="5s",
                        lock_timeout="5s",
                        transaction_timeout="15s",
                        search_path="pg_catalog",
                        timezone="UTC",
                    )
                ],
                [],
            )
        if text == policy.STATE_SQL:
            value = self.p["change"]["before"] if self.snapshots == 0 else self.p["change"]["after"]
            self.snapshots += 1
            return Cursor([{"value": value}], ["value"])
        return Cursor([], [], "ROLLBACK" if text == "ROLLBACK" else "SET")

    def close(self):
        self.closed += 1
