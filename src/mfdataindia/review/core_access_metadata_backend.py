"""Exact provider adapter for the one approved diagnostic; deny all other writes."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

import requests

from mfdataindia.review import core_access_metadata_controller as core
from mfdataindia.review import core_access_metadata_inventory as frozen
from mfdataindia.review.evidence_transport import execution_evidence, ExecutionEvidenceNotDelivered
from mfdataindia.review.image_provenance import verify_image
from mfdataindia.review.operator import verify_checkout
from mfdataindia.review.policy import canonical

SERVICES = ("tlws-mf-data-dev", "tlws-mf-review-dev")
JOBS = ("tlws-mf-official-facts-refresh-dev", "tlws-mf-data-nav-refresh-dev", "tlws-mf-data-nav-audit-dev")


def metadata_arguments(kind, name=None):
    """No arbitrary argv, filters, projects, identities, targets or secret reads."""
    if kind == "auth" and name is None:
        return ["auth", "list", "--filter=account:" + core.ACCOUNT, "--format=json"]
    if kind == "operator" and name is None:
        args = ["projects", "get-iam-policy", core.PROJECT]
    elif kind == "project" and name is None:
        args = ["projects", "describe", core.PROJECT]
    elif kind in ("service", "serviceIAM") and name in SERVICES:
        args = ["run", "services", "describe" if kind == "service" else "get-iam-policy", name]
    elif kind in ("job", "jobIAM") and name in (*JOBS, core.NAME):
        args = ["run", "jobs", "describe" if kind == "job" else "get-iam-policy", name]
    elif kind == "schedule" and name in JOBS:
        return ["scheduler", "jobs", "describe", name, "--project=" + core.PROJECT,
                "--location=" + core.REGION, "--format=json"]
    elif kind == "bucket" and name is None:
        args = ["storage", "buckets", "get-iam-policy", "gs://tlws_mf_data_source"]
    elif kind == "jobs" and name is None:
        args = ["run", "jobs", "list", "--filter=metadata.name=" + core.NAME, "--limit=2"]
    elif kind == "executions" and name is None:
        args = ["run", "jobs", "executions", "list", "--job=" + core.NAME, "--limit=2"]
    elif kind in ("execution", "logs") and isinstance(name, str) and re.fullmatch(re.escape(core.NAME) + r"-[a-z0-9]{5,10}", name):
        if kind == "execution":
            args = ["run", "jobs", "executions", "describe", name]
        else:
            args = ["logging", "read", 'resource.type="cloud_run_job" AND resource.labels.location="'
                    + core.REGION + '" AND labels."run.googleapis.com/execution_name"="' + name + '"', "--limit=100"]
    else:
        raise ValueError("unapproved diagnostic metadata operation")
    if args[0] == "run":
        args.append("--region=" + core.REGION)
    return [*args, "--project=" + core.PROJECT, "--format=json"]


class Backend:
    account = core.ACCOUNT

    def __init__(self, approval, repo):
        self.approval = approval
        self.repo = Path(repo).resolve()
        self.prepared = core.inputs(approval)
        self.created = False
        self.executed = False
        self.sent_urls = set()
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))

    def command(self, *args):
        # The image verifier may only ask for this account's short-lived token.
        if args != ("auth", "print-access-token"):
            raise ValueError("no arbitrary cloud commands or secret access")
        completed = subprocess.run(["gcloud", "--account=" + core.ACCOUNT, *args],
                                   capture_output=True, text=True, timeout=60)
        if completed.returncode or not completed.stdout.strip() or completed.stderr.strip():
            raise RuntimeError("approved operator token unavailable")
        return completed.stdout.strip()

    def metadata(self, kind, name=None):
        args = metadata_arguments(kind, name)
        completed = subprocess.run(["gcloud", "--account=" + core.ACCOUNT, *args],
                                   capture_output=True, text=True, timeout=60)
        # In particular, a missing/unknown filter field warning is NOT absence.
        if completed.returncode or completed.stderr.strip():
            raise RuntimeError("diagnostic metadata unavailable or ambiguous")
        return json.loads(completed.stdout)

    def preflight(self, approval):
        self._image_resolution = None
        if approval != self.approval:
            raise ValueError("backend approval differs")
        verify_checkout(self.repo, approval.controller_git_sha)
        source = self.repo / "src/mfdataindia/review/core_access_metadata_controller.py"
        if (Path(core.__file__).resolve() != source.resolve()
                or Path(__file__).resolve() != self.repo / "src/mfdataindia/review/core_access_metadata_backend.py"
                or Path(core.permission.__file__).resolve() != self.repo / "src/mfdataindia/review/core_access_metadata_spec.py"
                or hashlib.sha256(source.read_bytes()).hexdigest() != approval.controller_sha256
                or hashlib.sha256((self.repo / core.WORKER).read_bytes()).hexdigest() != core.WORKER_SHA
                or hashlib.sha256((self.repo / "src/mfdataindia/review/core_access_metadata_spec.py").read_bytes()).hexdigest() != core.SPEC_SHA
                or Path(frozen.__file__).resolve() != self.repo / "src/mfdataindia/review/core_access_metadata_inventory.py"
                or hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest() != core.INVENTORY_SHA):
            raise ValueError("reviewed controller/worker bytes differ")
        auth = self.metadata("auth")
        if len(auth) != 1 or auth[0].get("account") != core.ACCOUNT:
            raise ValueError("exact existing operator credentials required")
        iam = self.metadata("operator")
        if not any(b.get("role") == "roles/owner" and not b.get("condition")
                   and "user:" + core.ACCOUNT in b.get("members", []) for b in iam.get("bindings", [])):
            raise ValueError("existing operator authorization unavailable; never add grants")
        if self.metadata("jobs") or self.metadata("executions"):
            raise ValueError("existing job/execution: inspect original only, never duplicate")
        project = self.metadata("project")
        if (project.get("projectId") != core.PROJECT
                or not re.fullmatch(r"[0-9]{6,20}", str(project.get("projectNumber", "")))):
            raise ValueError("explicit projectNumber binding unavailable")
        # Both fresh checks download only immutable manifest/config bytes, never
        # layers; independent image-preparation/recheck evidence still required.
        first = None
        for _ in range(2):
            proof = core.validated_image_resolution(verify_image(
                self, approval.manifest.image, approval.manifest.git_sha,
                include_resolution=True), self.prepared)
            if first is not None and proof != first:
                raise ValueError("independent immutable image resolutions differ")
            first = proof
        self._image_resolution = deepcopy(first)
        return str(project["projectNumber"])

    def image_resolution(self):
        if getattr(self, "_image_resolution", None) is None:
            raise ValueError("two fresh immutable image resolutions required")
        return deepcopy(self._image_resolution)

    def protected(self):
        queries = ([("service", n) for n in SERVICES] + [("serviceIAM", n) for n in SERVICES]
                   + [("job", n) for n in JOBS] + [("jobIAM", n) for n in JOBS]
                   + [("schedule", n) for n in JOBS] + [("bucket", None), ("operator", None)])
        def read(pair):
            kind, name = pair
            value = self.metadata(kind, name)
            if kind == "service":
                value = {"spec": value["spec"], "traffic": value["status"]["traffic"]}
            elif kind == "job":
                value = {"spec": value["spec"]}
            elif kind == "schedule":
                value = {k: v for k, v in value.items() if k not in ("status", "scheduleTime", "lastAttemptTime")}
            return kind + ":" + str(name), value
        with ThreadPoolExecutor(max_workers=4) as pool:
            return dict(pool.map(read, queries))

    def _post(self, url, body):
        allowed = {core.BASE_URL + "/jobs": self.prepared["body"],
                   core.BASE_URL + "/jobs/" + core.NAME + ":run": b"{}"}
        if url not in allowed or body != allowed[url] or url in self.sent_urls:
            raise ValueError("only sole exact approved create/run HTTP bodies permitted")
        self.sent_urls.add(url)  # Consume BEFORE token/HTTP ambiguity; no retry.
        token = self.command("auth", "print-access-token")
        response = self.session.post(url, data=body, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json"},
            timeout=30, allow_redirects=False)
        if response.status_code not in (200, 201):
            raise RuntimeError("diagnostic create/run request failed; never retry")
        return response.json()

    def create(self, body):
        if self.created or body != self.prepared["body"]:
            raise ValueError("sole exact create request required")
        self.created = True  # Set BEFORE ambiguous provider I/O, no retries.
        return self._post(core.BASE_URL + "/jobs", body)

    def execute(self):
        if not self.created or self.executed:
            raise ValueError("sole execution of newly created job required")
        self.executed = True
        return self._post(core.BASE_URL + "/jobs/" + core.NAME + ":run", b"{}")

    def job(self):
        return self.metadata("job", core.NAME)

    def execution(self, name):
        return self.metadata("execution", name)

    def executions(self):
        return self.metadata("executions")

    def certificate(self, name):
        for attempt in range(6):
            try:
                return execution_evidence(self.metadata("logs", name))
            except ExecutionEvidenceNotDelivered:
                if attempt == 5:
                    raise
                time.sleep(5)  # Read-only log delivery only, never a task retry.
        raise AssertionError("bounded evidence inspection exhausted")


def load_approval(path, expected_hash):
    def unique(pairs):
        document = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("duplicate approval member")
            document[key] = value
        return document
    path = Path(path)
    if path.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("exact regular approval file/hash required")
    document = json.loads(path.read_text(), object_pairs_hook=unique)
    approval = core.Approval.model_validate(document)
    if path.resolve() != Path(approval.root) / "approval.json" or core.fingerprint(document) != expected_hash:
        raise ValueError("approval path/content binding differs")
    if canonical(document) != canonical(approval.model_dump(mode="json")):
        raise ValueError("approval cannot silently normalize submitted inputs")
    return approval
