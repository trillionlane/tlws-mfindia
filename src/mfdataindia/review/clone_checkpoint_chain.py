"""Fail-closed three-execution admission; never an activation permission."""

import hashlib
import re

from mfdataindia.review import clone_checkpoint_v2 as cp
from mfdataindia.review.rollout import GEN_SA, verify_evidence
from mfdataindia.review.rollout_cloud import verify_network


def _execution(entry, manifest, phase, image):
    document = entry["execution_description"]
    name = document["metadata"]["name"]
    status = document["status"]
    task = document["spec"]["template"]["spec"]
    if (
        name != entry["execution"]
        or not name.startswith("mf-book-clone-" + manifest.rollout_id + "-" + phase + "-")
        or not status.get("completionTime")
        or type(status.get("succeededCount")) is not int
        or status["succeededCount"] != 1
        or any(
            status.get(k, 0)
            for k in ("failedCount", "retriedCount", "cancelledCount", "runningCount")
        )
        or not any(v["type"] == "Completed" and v["status"] == "True" for v in status["conditions"])
        or document["spec"].get("taskCount") != 1
        or task.get("serviceAccountName") != GEN_SA
        or task.get("maxRetries") != 0
        or str(task.get("timeoutSeconds")) != "900"
        or task.get("volumes", [])
        or len(task["containers"]) != 1
    ):
        raise ValueError("three exact terminal bounded secret-free immutable executions required")
    container = task["containers"][0]
    verify_network(document["metadata"].get("annotations", {}))
    arguments = container.get("args", [])
    encoded = (
        re.fullmatch(r"exec\(bytes.fromhex\('([0-9a-f]+)'\)\)", arguments[1])
        if len(arguments) == 2
        else None
    )
    if (
        container.get("image") != image
        or container.get("command") != ["python"]
        or not encoded
        or arguments[0] != "-c"
        or len(arguments[1].encode()) >= 131072
        or hashlib.sha256(bytes.fromhex(encoded[1])).hexdigest() != entry["program_sha256"]
        or container.get("resources", {}).get("limits")
        not in ({"cpu": "2", "memory": "4Gi"}, {"cpu": "2000m", "memory": "4Gi"})
        or len(container.get("env", [])) != 2
        or {e["name"]: e.get("value") for e in container["env"]}
        != {"DEPLOYMENT_GIT_SHA": manifest.git_sha, "REVIEW_MANIFEST": manifest.model_dump_json()}
        or any("valueFrom" in e for e in container["env"])
    ):
        raise ValueError("immutable program/image/environment/profile differs")
    return name


def require_chain(manifest, acquisition, rehearsal, final, cleanup):
    entries = []
    for outer, stage in (
        (acquisition, "clone-acquire"),
        (rehearsal, "clone-rehearse"),
        (final, "clone"),
    ):
        entry = verify_evidence(manifest, stage, outer)
        result = verify_evidence(manifest, stage, entry["certificate"])
        if result != entry["result"]:
            raise ValueError("complete remote and outer evidence differ")
        entries.append((entry, result))
    (a, ar), (r, rr), (f, fr) = entries
    bound = cp.validate_context(ar["context"])
    if (
        bound["phase"] != "acquire"
        or bound["manifest_sha256"] != cp.digest(manifest.model_dump(mode="json"))
        or bound["git_sha"] != manifest.git_sha
        or bound["app_image"] != manifest.image
        or bound["checkpoint_prefix"]
        != "verified-review-rollouts/" + manifest.rollout_id + "-acquire-checkpoint/"
    ):
        raise ValueError("first checkpoint manifest binding differs")
    image = bound["tools_image"]
    names = [
        _execution(e, manifest, p, image)
        for (e, _), p in zip(entries, ("acquire", "rehearse", "validate"), strict=True)
    ]
    if len(set(names)) != 3:
        raise ValueError("three distinct immutable executions required")
    expected = {
        **bound,
        "phase": "rehearsal",
        "checkpoint_prefix": bound["checkpoint_prefix"].replace(
            "-acquire-checkpoint/", "-rehearsal-checkpoint/"
        ),
        "previous_checkpoint_sha256": cp.digest(ar["checkpoint"]),
        "previous_execution_sha256": a["certificate"]["sha256"],
    }
    cp.verify_certificate(bound, ar["checkpoint"])
    cp.verify_certificate(expected, rr["checkpoint"])
    if (
        rr["context"] != expected
        or rr["input_checkpoint"] != ar["checkpoint"]
        or rr["input_checkpoint_restore"]
        != {
            "complete_checkpoint_logical_seals_matched": True,
            "checkpoint_logical_seal_sha256": ar["checkpoint_logical_seal_sha256"],
        }
        or fr["checkpoint_certificate"] != rr["checkpoint"]
        or fr["checkpoint_context_sha256"] != cp.digest(expected)
        or fr["checkpoint_restore"]
        != {
            "complete_checkpoint_logical_seals_matched": True,
            "checkpoint_logical_seal_sha256": rr["checkpoint_logical_seal_sha256"],
        }
    ):
        raise ValueError("both complete exact logical restores and parent chain required")
    source = verify_evidence(manifest, "source-clone-rehearsal", rr["source_rehearsal_certificate"])
    source_hash = rr["source_rehearsal_certificate"]["sha256"]
    if (
        source_hash != rr["source_rehearsal_sha256"]
        or source_hash != fr["source_rehearsal_sha256"]
        or ar.get("level") != "ISOLATED_SOURCE_CHECKPOINT_NOT_DEV_ACCEPTANCE"
        or rr.get("level") != "ISOLATED_FENCED_REHEARSAL_CHECKPOINT_NOT_DEV_ACCEPTANCE"
        or fr.get("level") != "FINAL_TOOLS_IMAGE_RESTORED_CLONE_NOT_LIVE_SOURCE_CERTIFICATION"
        or type(ar.get("original_codes_checked")) is not int
        or ar["original_codes_checked"] <= 0
        or ar["original_codes_checked"] != rr.get("original_codes_checked")
        or ar["original_codes_checked"] != fr.get("preview_original_codes_checked")
        or ar.get("original_query_relations_checked") != 4
        or ar.get("original_backup_sha256") != bound["backup"]["sha256"]
    ):
        raise ValueError("actual whole-cohort source rehearsal proof/context differs")
    for result, keys in (
        (
            ar,
            (
                "whole_prior_population_original_query_equivalence",
                "all_actual_new_pdf_originals_verified",
                "no_activation_in_acquisition",
            ),
        ),
        (
            source,
            (
                "full_source_preservation_passed",
                "whole_prior_amc_projection_unchanged",
                "whole_prior_population_original_query_equivalence",
                "whole_population_preview_vs_actual_original_equivalence",
                "actual_recovery_replay_reactivation",
            ),
        ),
        (
            fr,
            (
                "full_source_preservation_passed",
                "whole_prior_amc_projection_unchanged",
                "whole_prior_population_original_query_equivalence",
                "whole_population_preview_vs_actual_original_equivalence",
                "actual_recovery_replay_reactivation",
                "three_phase_checkpoint",
                "every_prior_layer_row_preserved",
                "full_private_and_core_http_verified",
            ),
        ),
    ):
        if any(result.get(k) is not True for k in keys):
            raise ValueError("every original proof remains mandatory")
    for value in (ar, rr, source, fr):
        if (
            value.get("source_database_writes") != 0
            or value.get("human_events_created") != 0
            or value.get("live_database_credentials_mounted", False) is not False
        ):
            raise ValueError("isolated zero-live-writes clone only")
    for key in ("fenced_activation", "fenced_reactivation"):
        actual = source[key]
        preparation = actual["fenced_preparation"]
        body = {k: v for k, v in preparation.items() if k != "sha256"}
        from mfdataindia.review.policy import fingerprint

        if (
            actual.get("status") != "ACTIVATED"
            or not re.fullmatch("[0-9a-f]{64}", actual.get("transition_id", ""))
            or preparation.get("sha256") != fingerprint(body)
        ):
            raise ValueError("actual sealed fenced activation/recovery IDs required")
    clean = verify_evidence(manifest, "clone-iam-cleanup", cleanup)
    if (
        clean.get("all_exact_temporary_bindings_absent") is not True
        or clean.get("bindings_checked") != 5
        or fr.get("private_path_contract_count") != 52
        or fr.get("core_route_template_count") != 19
        or len(fr.get("core_route_seconds", {})) < 19
        or not fr.get("full_core_facade_equivalence")
    ):
        raise ValueError("all IAM cleanup/private/core/full facade proofs required")
    return {
        "three_phase_checkpoint_restore_verified": True,
        "two_phase_checkpoint_restore_verified": True,
        "acquisition_execution": names[0],
        "rehearsal_execution": names[1],
        "validation_execution": names[2],
        "first_checkpoint": ar["checkpoint"],
        "second_checkpoint": rr["checkpoint"],
        "source_rehearsal_sha256": source_hash,
    }
