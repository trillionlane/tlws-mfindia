"""Fixed DEV provider adapter for one reviewed direct-reader delivery stage.

This adapter has no generic job creator, secret reader, SQL executor or IAM
writer. Every create/run URL and body is exact and consumed before token/HTTP
ambiguity. The controller owns durable create-only intents and receipts.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import time

import requests

from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_rollout_controller as rollout
from mfdataindia.review.evidence_transport import (
    execution_evidence, ExecutionEvidenceNotDelivered)
from mfdataindia.review.image_provenance import verify_image
from mfdataindia.review.operator import verify_checkout


SERVICES = ("tlws-mf-data-dev", "tlws-mf-review-dev")
PROTECTED_JOBS = ("tlws-mf-official-facts-refresh-dev",
                  "tlws-mf-data-nav-refresh-dev",
                  "tlws-mf-data-nav-audit-dev")
BASE_URL = (f"https://{spec.REGION}-run.googleapis.com/apis/run.googleapis.com/v1/"
            f"namespaces/{spec.PROJECT}")


def metadata_arguments(kind, name=None, *, role=None, operation_id=None):
    """No caller-controlled project, resource pattern, secret or SQL command."""
    roles = {key: spec.role_for(key, operation_id=operation_id) for key in spec.ROLES}
    if kind == "auth" and name is None:
        return ["auth", "list", "--filter=account:" + spec.OPERATOR,
                "--format=json"]
    if kind == "operator" and name is None:
        args = ["projects", "get-iam-policy", spec.PROJECT]
    elif kind == "project" and name is None:
        args = ["projects", "describe", spec.PROJECT]
    elif kind in ("service", "serviceIAM") and name in SERVICES:
        args = ["run", "services", "describe" if kind == "service" else
                "get-iam-policy", name]
    elif kind in ("job", "jobIAM") and name in (*PROTECTED_JOBS, *(r.job for r in roles.values())):
        args = ["run", "jobs", "describe" if kind == "job" else
                "get-iam-policy", name]
    elif kind == "schedule" and name in PROTECTED_JOBS:
        return ["scheduler", "jobs", "describe", name,
                "--project=" + spec.PROJECT, "--location=" + spec.REGION,
                "--format=json"]
    elif kind == "bucket" and name is None:
        args = ["storage", "buckets", "get-iam-policy",
                "gs://tlws_mf_data_source"]
    elif kind in ("jobs", "executions") and role in spec.ROLES and name is None:
        if kind == "jobs":
            args = ["run", "jobs", "list",
                    "--filter=metadata.name=" + roles[role].job, "--limit=2"]
        else:
            args = ["run", "jobs", "executions", "list",
                    "--job=" + roles[role].job, "--limit=2"]
    elif kind in ("execution", "logs") and role in spec.ROLES \
            and type(name) is str and re.fullmatch(
                re.escape(roles[role].job) + r"-[a-z0-9]{5,10}", name):
        if kind == "execution":
            args = ["run", "jobs", "executions", "describe", name]
        else:
            args = ["logging", "read",
                    'resource.type="cloud_run_job" AND resource.labels.location="'
                    + spec.REGION + '" AND labels."run.googleapis.com/execution_name"="'
                    + name + '"', "--limit=100"]
    else:
        raise ValueError("UNAPPROVED_DIRECT_DELIVERY_METADATA_OPERATION")
    if args[0] == "run":
        args.append("--region=" + spec.REGION)
    return [*args, "--project=" + spec.PROJECT, "--format=json"]


class Backend:
    """One kind, one fixed job, one request and one original execution."""

    def __init__(self, approval, repo, prepared, *, operation_id=None):
        spec.require(type(approval) is dict and approval.get("kind") in spec.ROLES,
                     "EXACT_DIRECT_BACKEND_APPROVAL_REQUIRED")
        self.approval = deepcopy(approval)
        self.repo = Path(repo).resolve()
        self.prepared = deepcopy(prepared)
        self.kind = approval["kind"]
        self.operation_id = operation_id
        self.role = spec.role_for(self.kind, operation_id=operation_id)
        self.current_preflight_verified = False
        self.created = False
        self.executed = False
        self.sent_urls = set()
        self._images = None
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))

    def command(self, *args):
        if args != ("auth", "print-access-token"):
            raise ValueError("NO_ARBITRARY_DIRECT_CLOUD_COMMANDS")
        completed = subprocess.run(
            ["gcloud", "--account=" + spec.OPERATOR, *args],
            capture_output=True, text=True, timeout=60)
        if completed.returncode or not completed.stdout.strip() or completed.stderr.strip():
            raise RuntimeError("EXACT_DIRECT_OPERATOR_TOKEN_UNAVAILABLE")
        return completed.stdout.strip()

    def metadata(self, kind, name=None, *, role=None):
        args = metadata_arguments(kind, name, role=role, operation_id=self.operation_id)
        completed = subprocess.run(
            ["gcloud", "--account=" + spec.OPERATOR, *args],
            capture_output=True, text=True, timeout=60)
        if completed.returncode or completed.stderr.strip():
            raise RuntimeError("DIRECT_METADATA_UNAVAILABLE_OR_AMBIGUOUS")
        return json.loads(completed.stdout)

    def preflight(self, approval, prepared):
        if self.operation_id is not None:
            raise ValueError("CURRENT_OPERATION_PREFLIGHT_REQUIRED")
        if approval != self.approval or prepared != self.prepared:
            raise ValueError("DIRECT_BACKEND_PREPARED_INPUT_DRIFT")
        verify_checkout(self.repo, approval["final_git_sha"])
        auth = self.metadata("auth")
        if type(auth) is not list or len(auth) != 1 or auth[0].get("account") != spec.OPERATOR:
            raise ValueError("EXACT_EXISTING_DIRECT_OPERATOR_REQUIRED")
        iam = self.metadata("operator")
        if not any(b.get("role") == "roles/owner" and not b.get("condition")
                   and "user:" + spec.OPERATOR in b.get("members", [])
                   for b in iam.get("bindings", [])):
            raise ValueError("EXISTING_DIRECT_OPERATOR_IAM_UNAVAILABLE")
        project = self.metadata("project")
        if project.get("projectId") != spec.PROJECT or str(
                project.get("projectNumber")) != spec.PROJECT_NUMBER:
            raise ValueError("EXACT_DEV_PROJECT_NUMBER_DRIFT")
        prior = rollout.predecessor(approval, source_root=self.repo)
        prior_kinds = (() if self.kind == "capture" else
                       ("capture",) if self.kind == "grant" else
                       ("capture", "grant"))
        for role in spec.ROLES:
            jobs = self.metadata("jobs", role=role)
            executions = self.metadata("executions", role=role)
            if role not in prior_kinds:
                if jobs or executions:
                    raise ValueError("DIRECT_JOB_OR_EXECUTION_ALREADY_EXISTS")
                continue
            preceding = (prior if role == prior["kind"] else
                         rollout.predecessor(prior["approval"],
                                             source_root=self.repo))
            if type(jobs) is not list or len(jobs) != 1 or type(executions) is not list \
                    or len(executions) != 1:
                raise ValueError("EXACT_SOLE_DIRECT_PREDECESSOR_REQUIRED")
            original = preceding["completion"]
            original_seal = spec.validate_job_export(
                role, original["job"], preceding["prepared"],
                namespace=spec.PROJECT_NUMBER, expected_executions=1)
            current = self.metadata("job", spec.ROLES[role].job)
            current_seal = spec.validate_job_export(
                role, current, preceding["prepared"],
                namespace=spec.PROJECT_NUMBER, expected_executions=1)
            if current_seal != original_seal:
                raise ValueError("DIRECT_PREDECESSOR_JOB_PROFILE_DRIFT")
            name = original["execution"]["metadata"]["name"]
            if executions[0].get("metadata", {}).get("name") != name:
                raise ValueError("DIRECT_PREDECESSOR_EXECUTION_INVENTORY_DRIFT")
            identity = spec.validate_execution_export(
                role, original["execution"], preceding["prepared"],
                job=original_seal, image=approval["image_binding"]["app"])
            actual = self.metadata("execution", name, role=role)
            spec.validate_execution_export(
                role, actual, preceding["prepared"], job=original_seal,
                image=approval["image_binding"]["app"],
                original_identity=identity)
        first = None
        for _ in range(2):
            pair = {name: verify_image(
                self, approval["image_binding"][name]["image"],
                approval["final_git_sha"], include_resolution=True)
                for name in ("app", "tools")}
            for name in ("app", "tools"):
                spec.image_binding(pair[name],
                                   expected_git_sha=approval["final_git_sha"],
                                   repository=("mfdataindia" if name == "app"
                                               else "mfdataindia-review-tools"))
            if pair != approval["image_binding"] or (first is not None and pair != first):
                raise ValueError("TWO_FINAL_DIRECT_IMAGE_RESOLUTIONS_DIFFER")
            first = pair
        self._images = deepcopy(first)

    def image(self, kind):
        if self._images is None or kind not in ("app", "tools"):
            raise ValueError("TWO_FINAL_DIRECT_IMAGES_NOT_RESOLVED")
        return deepcopy(self._images[kind])

    def protected(self):
        queries = ([('service', name) for name in SERVICES]
                   + [('serviceIAM', name) for name in SERVICES]
                   + [('job', name) for name in PROTECTED_JOBS]
                   + [('jobIAM', name) for name in PROTECTED_JOBS]
                   + [('schedule', name) for name in PROTECTED_JOBS]
                   + [('bucket', None), ('operator', None)])

        def read(pair):
            kind, name = pair
            value = self.metadata(kind, name)
            if kind == 'service':
                value = {'spec': value['spec'], 'traffic': value['status']['traffic']}
            elif kind == 'job':
                value = {'spec': value['spec']}
            elif kind == 'schedule':
                value = {key: item for key, item in value.items()
                         if key not in ('status', 'scheduleTime', 'lastAttemptTime')}
            return kind + ':' + str(name), value

        with ThreadPoolExecutor(max_workers=4) as pool:
            return dict(pool.map(read, queries))

    def absent(self, kind):
        if kind != self.kind:
            raise ValueError("UNAPPROVED_DIRECT_ABSENCE_TARGET")
        return self.metadata("jobs", role=kind) == [] and self.metadata(
            "executions", role=kind) == []

    def _post(self, url, body):
        if self.operation_id is not None and not self.current_preflight_verified:
            raise ValueError("CURRENT_OPERATION_PREFLIGHT_REQUIRED")
        name = self.role.job
        allowed = {BASE_URL + "/jobs": self.prepared["body"],
                   BASE_URL + "/jobs/" + name + ":run": b"{}"}
        if url not in allowed or body != allowed[url] or url in self.sent_urls:
            raise ValueError("SOLE_EXACT_DIRECT_CREATE_RUN_REQUIRED")
        self.sent_urls.add(url)  # BEFORE token/HTTP ambiguity. Never retry.
        token = self.command("auth", "print-access-token")
        response = self.session.post(
            url, data=body,
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"},
            timeout=30, allow_redirects=False)
        if response.status_code not in (200, 201):
            raise RuntimeError("DIRECT_CREATE_RUN_FAILED_NO_RETRY")
        return response.json()

    def create(self, kind, body):
        if kind != self.kind or self.created or body != self.prepared["body"]:
            raise ValueError("SOLE_DIRECT_CREATE_BODY_REQUIRED")
        self.created = True
        return self._post(BASE_URL + "/jobs", body)

    def execute(self, kind):
        if kind != self.kind or not self.created or self.executed:
            raise ValueError("SOLE_DIRECT_EXECUTION_REQUIRED")
        self.executed = True
        return self._post(BASE_URL + "/jobs/" + self.role.job + ":run", b"{}")

    def job(self, kind):
        if kind != self.kind:
            raise ValueError("UNAPPROVED_DIRECT_JOB_READ")
        return self.metadata("job", self.role.job)

    def executions(self, kind):
        if kind != self.kind:
            raise ValueError("UNAPPROVED_DIRECT_INVENTORY_READ")
        return self.metadata("executions", role=kind)

    def execution(self, kind, name):
        if kind != self.kind:
            raise ValueError("UNAPPROVED_DIRECT_EXECUTION_READ")
        return self.metadata("execution", name, role=kind)

    def certificate(self, kind, name):
        if kind != self.kind:
            raise ValueError("UNAPPROVED_DIRECT_CERTIFICATE_READ")
        for attempt in range(6):
            try:
                return execution_evidence(self.metadata("logs", name, role=kind))
            except ExecutionEvidenceNotDelivered:
                if attempt == 5:
                    raise
                time.sleep(5)  # Log-delivery read only, not a task retry.
        raise AssertionError("BOUNDED_DIRECT_EVIDENCE_EXHAUSTED")
