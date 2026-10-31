"""Obviously synthetic inputs and fake provider transport, never live approvals."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mfdataindia.review import reader_execution_contract as contract
from mfdataindia.review import reader_execution_application as application
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review.policy import fingerprint
from test_broad_reader_direct_rollout_controller import APP, TOOLS, GIT
from test_verified_review_auth import trust, assertion

ROOT = Path(__file__).resolve().parents[1]


def plan(*, operation="op-0123456789abcdef", protected=None):
    private, verifier = trust()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    window = {"starts_at": (now - timedelta(seconds=2)).isoformat(),
              "ends_at": (now + timedelta(minutes=10)).isoformat(),
              "exclusive_administration": False, "data_schema_custody": False,
              "future_owner_provisioning": False, "dev_no_freeze_risk_accepted": True}
    value = contract.candidate(operation_id=operation, git_sha=GIT,
        images={"app": APP, "tools": TOOLS}, window=window,
        protected_sha256=fingerprint({"fixture": True} if protected is None else protected),
        probe_codes={"monthly": 103174, "multi": 101763, "book": 100001},
        application_trust_sha256=fingerprint(application.public_trust(verifier)),
        source_root=ROOT, expected_git=GIT)
    return value, verifier, private


def environment(value, kind="capture", predecessors=None, *, dsn="synthetic-secret-marker"):
    prepared = contract.prepare_request(value, kind, [] if predecessors is None else predecessors,
                                        source_root=ROOT, expected_git=GIT)
    env = {row["name"]: row["value"] for row in prepared["task"]["containers"][0]["env"] if "value" in row}
    name = spec.role_for(kind, operation_id=value["operation_id"]).job
    env.update(MFDATAINDIA_DSN=dsn, CLOUD_RUN_JOB=name, CLOUD_RUN_EXECUTION=name + "-abcde",
               CLOUD_RUN_TASK_COUNT="1", CLOUD_RUN_TASK_INDEX="0", CLOUD_RUN_TASK_ATTEMPT="0")
    return env


def authority(value):
    return {"format": "mf-reader-bounded-plan-authority-v1", "plan_sha256": fingerprint(value),
            "operation_id": value["operation_id"], "approved_by": spec.OPERATOR,
            "stages": list(contract.KINDS), "window": deepcopy(value["window"]),
            "scope": "ONE_DEV_CAPTURE_DERIVED_GRANT_AUTHENTICATED_READER",
            "direct_human_approval_record_sha256": "a" * 64}


def app_context(value, verifier, private):
    from psycopg.conninfo import make_conninfo
    dsn = make_conninfo(host="/cloudsql/" + spec.CONNECTION, dbname=spec.DATABASE,
                       user=spec.READER_ROLE, password="synthetic-private-marker")
    return application.ApplicationCheck(dsn, verifier,
        assertion(private, scope=["review:read"]), assertion(private))


class FakeProvider:
    """Full actual Job/Execution validators still run on these synthetic exports."""
    def __init__(self, plan, kind, repo, prepared):
        self.plan, self.kind, self.prepared = plan, kind, prepared
        self.created = self.ran = False
        self.calls = []
        self.name = spec.role_for(kind, operation_id=plan["operation_id"]).job + "-abcde"
        self.created_at, self.executed_at = "2026-10-31T12:00:00Z", "2026-10-31T12:01:00Z"
        self.fail_at = None
        self.result = None

    def preflight(self, plan, prepared, completed):
        assert plan == self.plan and prepared == self.prepared
        self.calls.append("preflight")

    def protected(self):
        return {"fixture": True}

    def job(self, kind):
        assert kind == self.kind and self.created
        value = deepcopy(self.prepared["request"])
        value["metadata"].update(namespace=spec.PROJECT_NUMBER, uid="a" * 16,
            creationTimestamp=self.created_at, generation=1,
            labels={"cloud.googleapis.com/location": spec.REGION},
            annotations={"run.googleapis.com/creator": spec.OPERATOR})
        value["status"] = {"executionCount": int(self.ran), "conditions": [{"type": "Ready", "status": "True"}]}
        if self.ran:
            value["status"]["latestCreatedExecution"] = {"name": self.name, "creationTimestamp": self.executed_at}
        return value

    def execution(self, kind, name):
        assert kind == self.kind and name == self.name and self.ran
        return {"apiVersion": "run.googleapis.com/v1", "kind": "Execution",
            "metadata": {"name": name, "namespace": spec.PROJECT_NUMBER, "uid": "b" * 16,
                "creationTimestamp": self.executed_at, "generation": 1,
                "labels": {"cloud.googleapis.com/location": spec.REGION,
                    "run.googleapis.com/job": spec.role_for(kind, operation_id=self.plan["operation_id"]).job,
                    "run.googleapis.com/jobUid": "a" * 16, "run.googleapis.com/jobGeneration": "1"},
                "annotations": {"run.googleapis.com/creator": spec.OPERATOR, **self.prepared["annotations"]}},
            "spec": deepcopy(self.job(kind)["spec"]["template"]["spec"]),
            "status": {"conditions": [{"type": "Completed", "status": "True"}], "succeededCount": 1}}

    def executions(self, kind):
        return [self.execution(kind, self.name)] if self.ran else []

    def create(self, kind, body):
        assert not self.created and body == self.prepared["body"]
        self.calls.append("create")
        self.created = True
        if self.fail_at == "create":
            raise TimeoutError("synthetic-secret-marker")
        return self.job(kind)

    def execute(self, kind):
        assert self.created and not self.ran
        self.calls.append("execute")
        self.ran = True
        if self.fail_at == "execute":
            raise TimeoutError("synthetic-secret-marker")
        return self.execution(kind, self.name)

    def certificate(self, kind, name):
        assert kind == self.kind and name == self.name
        self.calls.append("certificate")
        if self.fail_at == "certificate":
            raise TimeoutError("synthetic-secret-marker")
        assert self.result is not None
        return deepcopy(self.result)
