"""One current adapter; original fixed DEV HTTP/task/resource limits retained."""
from copy import deepcopy
import json
import re
import subprocess

from . import broad_reader_direct_delivery_backend as old
from . import broad_reader_direct_delivery_spec as spec
from . import reader_execution_contract as contract
from .policy import fingerprint
from .operator import verify_checkout
from .image_provenance import verify_image


class Backend(old.Backend):
    def __init__(self, plan, kind, repo, prepared):
        self.plan = deepcopy(plan)
        super().__init__({"kind": kind}, repo, prepared, operation_id=plan["operation_id"])

    def metadata(self, kind, name=None, *, role=None):
        if kind == "programme_jobs":
            contract.require(name is None and role is None, "PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION")
            args = ["run", "jobs", "list", "--filter=metadata.name~^mf-reader-internal-", "--limit=1001",
                "--region=" + spec.REGION, "--project=" + spec.PROJECT, "--format=json"]
        elif kind in ("programme_job", "programme_executions"):
            match = re.fullmatch(r"mf-reader-internal-(capture|grant|reader)-(op-[a-z0-9]{12,24})", name or "")
            contract.require(match is not None and role is None, "PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION")
            stage, operation = match.groups()
            args = old.metadata_arguments("job" if kind == "programme_job" else "executions",
                name if kind == "programme_job" else None, role=stage, operation_id=operation)
        else:
            return super().metadata(kind, name, role=role)
        result = subprocess.run(["gcloud", "--account=" + spec.OPERATOR, *args],
            capture_output=True, text=True, timeout=60)
        contract.require(result.returncode == 0 and not result.stderr.strip()
            and 0 < len(result.stdout.encode()) <= spec.MAX_PAYLOAD_BYTES,
            "PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION")
        return json.loads(result.stdout)

    def recheck_programme_inventory(self, *, current=None):
        expected = {**self._expected_programme, **({} if current is None else current)}
        contract.require(current is None or not set(current) & set(self._expected_programme),
                         "PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION")
        prior = None
        for _ in range(2):
            rows = self.metadata("programme_jobs")
            contract.require(type(rows) is list and len(rows) <= 1000
                and all(type(row) is dict and type(row.get("metadata")) is dict for row in rows),
                "PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION")
            names = [row["metadata"].get("name") for row in rows]
            contract.require(len(names) == len(set(names)) and set(names) == set(expected),
                "PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION")
            observed = {}
            for name in sorted(names):
                observed[name] = {"job_sha256": fingerprint(self.metadata("programme_job", name)),
                    "execution_sha256": fingerprint(self.metadata("programme_executions", name))}
            contract.require(observed == expected and (prior is None or observed == prior),
                "PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION")
            prior = observed

    def preflight(self, plan, prepared, completed, *, settled=None):
        self.current_preflight_verified = False
        self._images = None
        self._expected_programme = deepcopy({} if settled is None else settled)
        contract.require(plan == self.plan and prepared == self.prepared,
                         "CURRENT_BACKEND_INPUT_DRIFT")
        verify_checkout(self.repo, plan["git_sha"])
        auth = self.metadata("auth")
        contract.require(type(auth) is list and len(auth) == 1
                         and auth[0].get("account") == spec.OPERATOR,
                         "EXACT_CURRENT_OPERATOR_REQUIRED")
        iam = self.metadata("operator")
        contract.require(any(row.get("role") == "roles/owner" and not row.get("condition")
            and "user:" + spec.OPERATOR in row.get("members", [])
            for row in iam.get("bindings", [])), "EXISTING_OPERATOR_IAM_REQUIRED")
        project = self.metadata("project")
        contract.require(project.get("projectId") == spec.PROJECT
            and str(project.get("projectNumber")) == spec.PROJECT_NUMBER, "FIXED_TARGET_DRIFT")
        for kind in contract.KINDS:
            jobs, executions = self.metadata("jobs", role=kind), self.metadata("executions", role=kind)
            if kind not in completed:
                contract.require(jobs == executions == [], "CURRENT_TARGET_NOT_EMPTY")
                continue
            record = completed[kind]
            contract.require(type(jobs) is list and len(jobs) == 1
                and type(executions) is list and len(executions) == 1, "PREDECESSOR_INVENTORY_DRIFT")
            actual = self.metadata("job", spec.role_for(kind, operation_id=self.operation_id).job)
            seal = spec.validate_job_export(kind, actual, record["prepared"],
                namespace=spec.PROJECT_NUMBER, expected_executions=1, operation_id=self.operation_id)
            contract.require(seal == record["job_seal"], "PREDECESSOR_JOB_DRIFT")
            observed = self.metadata("execution", record["identity"]["name"], role=kind)
            for value in (executions[0], observed):
                spec.validate_execution_export(kind, value, record["prepared"], job=seal,
                    image=plan["image_binding"]["app"], original_identity=record["identity"],
                    operation_id=self.operation_id)
            contract.require(fingerprint(record["certificate"]) == record["certificate_sha256"],
                             "PREDECESSOR_CERTIFICATE_DRIFT")
            self._expected_programme[spec.role_for(kind, operation_id=self.operation_id).job] = {
                "job_sha256": fingerprint(actual), "execution_sha256": fingerprint(executions)}
        self.recheck_programme_inventory()
        previous = None
        for _ in range(2):
            pair = {name: verify_image(self, plan["image_binding"][name]["image"],
                plan["git_sha"], include_resolution=True) for name in ("app", "tools")}
            contract.require(pair == plan["image_binding"] and (previous is None or pair == previous),
                             "CURRENT_IMAGE_BINDING_DRIFT")
            previous = pair
        contract.require(fingerprint(self.protected()) == plan["protected_sha256"],
                         "PROTECTED_BASELINE_DRIFT")
        self._images = deepcopy(previous)
        self.current_preflight_verified = True


class Inspector:
    """Read-only facade: no create/run/token/HTTP-post methods are exposed."""
    def __init__(self, plan, kind, repo, prepared):
        self._adapter = Backend(plan, kind, repo, prepared)

    def job(self, kind):
        return self._adapter.job(kind)

    def executions(self, kind):
        return self._adapter.executions(kind)

    def execution(self, kind, name):
        return self._adapter.execution(kind, name)

    def certificate(self, kind, name):
        return self._adapter.certificate(kind, name)

    def protected(self):
        return self._adapter.protected()
