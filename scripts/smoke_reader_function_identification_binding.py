#!/usr/bin/env python3
"""Actual packaged successor approval entrypoint; no DSN, provider or SQL."""

import hashlib
import json
import os
from pathlib import Path
import runpy
from unittest.mock import patch

from mfdataindia.review import reader_function_identification_controller as core
from mfdataindia.review import reader_function_identification_spec as spec
from mfdataindia.review.policy import fingerprint, policy
from mfdataindia.review.rollout import Manifest, CONNECTION


def synthetic_inputs(git):
    root = Path(__file__).resolve().parents[1]
    manifest = Manifest.model_validate(
        dict(
            format="mfdata-review-dev-rollout-v1",
            rollout_id=spec.IDENTITY,
            actor="rollout-certification:" + spec.IDENTITY,
            project="trillionlane-dev",
            region="asia-south1",
            source_connection=CONNECTION,
            database="mfdataindia",
            git_sha=git,
            image="asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:"
            + "b" * 64,
            policy_hash=fingerprint(policy()),
            contract_hash=hashlib.sha256(
                (root / "contracts/mfdataindia-verified-review-v1.json").read_bytes()
            ).hexdigest(),
            migration_hash=hashlib.sha256(
                (root / "sql/optional/023_verified_fund_review.sql").read_bytes()
            ).hexdigest(),
            trust_secret="tlws-mf-review-trust-dev",
            trust_version=1,
            trust_hash="c" * 64,
            signer_secret="trillion-insights-mf-review-signer-dev",
            signer_version=1,
            source_secret_version=1,
            cohort=[154117],
            canary_code=154117,
            allow_canary_events=True,
            required_coverage=["growth_eligible", "unsupported_option"],
        )
    )
    approval = core.Approval(
        manifest=manifest,
        controller_git_sha=git,
        controller_sha256=hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
        protected_hash=fingerprint({"SYNTHETIC_NOT_READINESS_OR_APPROVAL": True}),
        root=str(spec.ARTIFACTS / "mf-reader-function-identification-20261028-Smoke123"),
    )
    environment = core.inputs(approval)["environment"] | dict(
        CLOUD_RUN_JOB=core.NAME,
        CLOUD_RUN_EXECUTION=core.NAME + "-abcde",
        CLOUD_RUN_TASK_INDEX="0",
        CLOUD_RUN_TASK_ATTEMPT="0",
        CLOUD_RUN_TASK_COUNT="1",
    )
    return approval, environment


def main():
    path = Path(__file__).resolve().parent / "collect_reader_function_identification_dev.py"
    approval, environment = synthetic_inputs(os.environ["DEPLOYMENT_GIT_SHA"])
    assert "MFDATAINDIA_DSN" not in environment
    worker = runpy.run_path(str(path))
    calls = []

    def denied(**_):
        calls.append("connect")
        raise AssertionError("packaged smoke must never connect")

    with patch.object(worker["psycopg"], "connect", denied):
        # Real worker binding, strict parser, Approval, canonical equality and
        # full input request precede this intentionally absent DSN boundary.
        try:
            worker["run_worker"](environment)
        except KeyError as error:
            assert error.args == ("MFDATAINDIA_DSN",)
        else:
            raise AssertionError("complete approval did not reach exact missing-DSN boundary")
        try:
            worker["run_worker"](environment)
        except ValueError:
            pass
        else:
            raise AssertionError("worker reuse admitted")
        for old in ("dev-bread-cap-261026r", "dev-reader-cap-261025b", "dev-bread-cap-261027s", "dev-bread-cap-261027t"):
            old_manifest = approval.manifest.model_copy(update={"rollout_id": old})
            try:
                worker["worker_binding"](old_manifest, environment)
            except ValueError:
                pass
            else:
                raise AssertionError("consumed/old identity admitted")
    assert calls == []
    print(
        json.dumps(
            dict(
                packaged_reader_function_identification_entrypoint="PASS",
                strict_approval_parse_before_missing_DSN=True,
                provider_requests=0,
                database_queries=0,
                runtime_or_write_authority=False,
            )
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
