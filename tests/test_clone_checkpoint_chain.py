"""Synthetic three-stage failure matrix, no cloud or acceptance claim."""

from copy import deepcopy
import hashlib
import json
import pytest

from mfdataindia.review import clone_checkpoint_v2 as cp
from mfdataindia.review.clone_checkpoint_chain import require_chain
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import CONNECTION, GEN_SA, seal_evidence
from test_verified_review_rollout import manifest
from test_clone_checkpoint import meta
from test_clone_checkpoint_v2 import context


def inputs():
    m = manifest(
        rollout_id="dev-uti-gap-261020g", actor="rollout-certification:dev-uti-gap-261020g"
    )
    bound = context()
    bound["manifest_sha256"] = cp.digest(m.model_dump(mode="json"))
    bound["git_sha"] = m.git_sha
    bound["app_image"] = m.image

    def certificate(ctx):
        return {
            "context_sha256": cp.digest(ctx),
            "dump": meta(ctx["checkpoint_prefix"] + "snapshot.dump", b"dump"),
            "state": meta(ctx["checkpoint_prefix"] + "state.json", b"state"),
        }

    first = {
        "context": bound,
        "checkpoint": certificate(bound),
        "checkpoint_logical_seal_sha256": "a" * 64,
        "original_backup_sha256": bound["backup"]["sha256"],
        "original_codes_checked": 3220,
        "original_query_relations_checked": 4,
        "level": "ISOLATED_SOURCE_CHECKPOINT_NOT_DEV_ACCEPTANCE",
        "source_database_writes": 0,
        "human_events_created": 0,
        "live_database_credentials_mounted": False,
        "whole_prior_population_original_query_equivalence": True,
        "all_actual_new_pdf_originals_verified": True,
        "no_activation_in_acquisition": True,
        "actual_source_status": "updated",
    }
    first_cert = seal_evidence(m, "clone-acquire", first)
    later = {
        **bound,
        "phase": "rehearsal",
        "checkpoint_prefix": bound["checkpoint_prefix"].replace("-acquire-", "-rehearsal-"),
        "previous_checkpoint_sha256": cp.digest(first["checkpoint"]),
        "previous_execution_sha256": first_cert["sha256"],
    }
    source = {
        key: True
        for key in (
            "full_source_preservation_passed",
            "whole_prior_amc_projection_unchanged",
            "whole_prior_population_original_query_equivalence",
            "whole_population_preview_vs_actual_original_equivalence",
            "actual_recovery_replay_reactivation",
        )
    }
    source.update(source_database_writes=0, human_events_created=0)
    for key in ("fenced_activation", "fenced_reactivation"):
        proof = {"request": {"synthetic": key}, "dependency_fence_sha256": "e" * 64}
        source[key] = {
            "status": "ACTIVATED",
            "transition_id": hashlib.sha256(key.encode()).hexdigest(),
            "fenced_preparation": {**proof, "sha256": fingerprint(proof)},
        }
    source_cert = seal_evidence(m, "source-clone-rehearsal", source)
    second = {
        "context": later,
        "checkpoint": certificate(later),
        "input_checkpoint": first["checkpoint"],
        "input_checkpoint_restore": {
            "complete_checkpoint_logical_seals_matched": True,
            "checkpoint_logical_seal_sha256": "a" * 64,
        },
        "checkpoint_logical_seal_sha256": "b" * 64,
        "source_rehearsal_certificate": source_cert,
        "source_rehearsal_sha256": source_cert["sha256"],
        "original_codes_checked": 3220,
        "level": "ISOLATED_FENCED_REHEARSAL_CHECKPOINT_NOT_DEV_ACCEPTANCE",
        "source_database_writes": 0,
        "human_events_created": 0,
        "live_database_credentials_mounted": False,
    }
    final = {
        **source,
        "checkpoint_certificate": second["checkpoint"],
        "checkpoint_context_sha256": cp.digest(later),
        "checkpoint_restore": {
            "complete_checkpoint_logical_seals_matched": True,
            "checkpoint_logical_seal_sha256": "b" * 64,
        },
        "source_rehearsal_sha256": source_cert["sha256"],
        "preview_original_codes_checked": 3220,
        "level": "FINAL_TOOLS_IMAGE_RESTORED_CLONE_NOT_LIVE_SOURCE_CERTIFICATION",
        "three_phase_checkpoint": True,
        "every_prior_layer_row_preserved": True,
        "full_private_and_core_http_verified": True,
        "private_path_contract_count": 52,
        "core_route_template_count": 19,
        "core_route_seconds": {str(n): 0.01 for n in range(19)},
        "full_core_facade_equivalence": {"rows": 14410, "sha256": "c" * 64},
    }

    def entry(phase, stage, result):
        program = b"pass"
        args = ["-c", "exec(bytes.fromhex('" + program.hex() + "'))"]
        task = {
            "serviceAccountName": GEN_SA,
            "maxRetries": 0,
            "timeoutSeconds": "900",
            "containers": [
                {
                    "image": bound["tools_image"],
                    "command": ["python"],
                    "args": args,
                    "resources": {"limits": {"cpu": "2", "memory": "4Gi"}},
                    "env": [
                        {"name": "DEPLOYMENT_GIT_SHA", "value": m.git_sha},
                        {"name": "REVIEW_MANIFEST", "value": m.model_dump_json()},
                    ],
                }
            ],
        }
        name = "mf-book-clone-" + m.rollout_id + "-" + phase + "-fixture"
        execution = {
            "metadata": {
                "name": name,
                "annotations": {
                    "run.googleapis.com/cloudsql-instances": CONNECTION,
                    "run.googleapis.com/vpc-access-egress": "all-traffic",
                    "run.googleapis.com/network-interfaces": json.dumps(
                        [{"network": "tl-dev-vpc", "subnetwork": "tl-dev-mumbai"}]
                    ),
                },
            },
            "spec": {"taskCount": 1, "template": {"spec": task}},
            "status": {
                "completionTime": "fixture",
                "succeededCount": 1,
                "conditions": [{"type": "Completed", "status": "True"}],
            },
        }
        return {
            "execution": name,
            "execution_description": execution,
            "program_sha256": hashlib.sha256(program).hexdigest(),
            "certificate": seal_evidence(m, stage, result),
            "result": deepcopy(result),
        }

    entries = [
        entry(p, s, v)
        for p, s, v in [
            ("acquire", "clone-acquire", first),
            ("rehearse", "clone-rehearse", second),
            ("validate", "clone", final),
        ]
    ]
    cleanup = {"all_exact_temporary_bindings_absent": True, "bindings_checked": 5}
    return m, entries, cleanup


def admit(m, entries, cleanup):
    return require_chain(
        m,
        *[
            seal_evidence(m, s, e)
            for s, e in zip(("clone-acquire", "clone-rehearse", "clone"), entries, strict=True)
        ],
        seal_evidence(m, "clone-iam-cleanup", cleanup),
    )


def test_all_three_distinct_exact_sealed_executions_required():
    m, e, c = inputs()
    result = admit(m, e, c)
    assert result["three_phase_checkpoint_restore_verified"] is True
    assert (
        len(
            {
                result[k]
                for k in ("acquisition_execution", "rehearsal_execution", "validation_execution")
            }
        )
        == 3
    )


@pytest.mark.parametrize("phase", range(3))
@pytest.mark.parametrize(
    "defect",
    [
        "failed",
        "retry",
        "running",
        "timeout",
        "cpu",
        "secret",
        "program",
        "image",
        "network",
        "name",
        "no_terminal",
    ],
)
def test_each_immutable_execution_fails_closed(phase, defect):
    m, e, c = inputs()
    target = e[phase]
    document = target["execution_description"]
    task = document["spec"]["template"]["spec"]
    container = task["containers"][0]
    if defect == "failed":
        document["status"]["failedCount"] = 1
    elif defect == "retry":
        document["status"]["retriedCount"] = 1
    elif defect == "running":
        document["status"]["runningCount"] = 1
    elif defect == "timeout":
        task["timeoutSeconds"] = "901"
    elif defect == "cpu":
        container["resources"]["limits"]["cpu"] = "4"
    elif defect == "secret":
        container["env"].append({"name": "MFDATAINDIA_DSN", "valueFrom": {"secretKeyRef": {}}})
    elif defect == "program":
        target["program_sha256"] = "0" * 64
    elif defect == "image":
        container["image"] = "unreviewed"
    elif defect == "network":
        document["metadata"]["annotations"] = {}
    elif defect == "name":
        target["execution"] = document["metadata"]["name"] = "wrong-phase"
    else:
        document["status"].pop("completionTime")
    with pytest.raises(ValueError):
        admit(m, e, c)


@pytest.mark.parametrize(
    "defect",
    [
        "parent_execution",
        "parent_checkpoint",
        "restore1",
        "restore2",
        "cohort",
        "source_proof",
        "full_http",
        "facade",
        "paths",
        "core",
        "cleanup",
        "bindings",
        "only_two",
        "outer_remote",
    ],
)
def test_logical_chain_and_every_original_gate_fail_closed(defect):
    m, e, c = inputs()
    second = e[1]["result"]
    final = e[2]["result"]
    if defect == "parent_execution":
        second["context"]["previous_execution_sha256"] = "0" * 64
    elif defect == "parent_checkpoint":
        second["input_checkpoint"]["dump"]["generation"] = "999"
    elif defect == "restore1":
        second["input_checkpoint_restore"]["complete_checkpoint_logical_seals_matched"] = False
    elif defect == "restore2":
        final["checkpoint_restore"]["checkpoint_logical_seal_sha256"] = "0" * 64
    elif defect == "cohort":
        final["preview_original_codes_checked"] = 1
    elif defect == "source_proof":
        second["source_rehearsal_sha256"] = "0" * 64
    elif defect == "full_http":
        final["full_private_and_core_http_verified"] = False
    elif defect == "facade":
        final.pop("full_core_facade_equivalence")
    elif defect == "paths":
        final["private_path_contract_count"] = 51
    elif defect == "core":
        final["core_route_template_count"] = 18
    elif defect == "cleanup":
        c["all_exact_temporary_bindings_absent"] = False
    elif defect == "bindings":
        c["bindings_checked"] = 4
    elif defect == "only_two":
        final["three_phase_checkpoint"] = False
    elif defect == "outer_remote":
        e[2]["certificate"]["sha256"] = "0" * 64
    for entry, stage in zip(e, ("clone-acquire", "clone-rehearse", "clone"), strict=True):
        if defect != "outer_remote":
            entry["certificate"] = seal_evidence(m, stage, entry["result"])
    with pytest.raises(ValueError):
        admit(m, e, c)
