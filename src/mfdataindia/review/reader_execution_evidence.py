"""Complete bounded stage evidence; only regenerable SQL/plan is omitted on wire."""
from copy import deepcopy

from . import broad_reader_direct_delivery_spec as spec
from . import broad_reader_direct_capacity as capacity
from . import broad_reader_direct_grant as direct
from . import broad_reader_direct_plan as planner
from . import broad_reader_live_snapshot as snapshot
from . import broad_reader_function_closure as functions
from . import broad_reader_lock_witness as witness
from . import broad_reader_source_authority as source
from . import broad_reader_view_profile as views
from . import broad_reader_direct_reader_check as reader
from . import reader_access_profile as profiles
from . import reader_execution_contract as contract
from . import reader_execution_diagnostics as diagnostics
from .policy import canonical, fingerprint

require = contract.require
CAPTURED = "CAPTURE_COMPLETE_NO_GRANT"
NOOP = "ACL_ALREADY_COMPLIANT_NO_NEW_GRANT"
GRANTED = "COMMITTED_ACL_VERIFIED"
READ = "AUTHENTICATED_READER_VERIFIED"
SUCCESS = frozenset({CAPTURED, NOOP, GRANTED, READ})
_CAPTURE_KEYS = frozenset({"format", "helper_attestation", "before_snapshot", "packaged_source",
                           "capture_sha256", "grant_authorized", "custody"})
_SNAPSHOT_KEYS = frozenset({"format", "state", "candidates", "system_leaves", "function_rows",
                            "ledger", "migration_proof", "function_proof", "view_profile",
                            "grant_authorized", "permission_enabled"})


def compact_capture(value):
    require(type(value) is dict and set(value) == _CAPTURE_KEYS, "INCOMPLETE_CAPTURE_PROOF")
    wire = deepcopy(value)
    before = wire["before_snapshot"]
    require(type(before) is dict and set(before) == _SNAPSHOT_KEYS | {"plan"},
            "INCOMPLETE_CAPTURE_PROOF")
    wire["derived_plan_sha256"] = fingerprint(before.pop("plan"))
    wire["format"] = "mf-reader-execution-capture-wire-v1"
    return wire


def restore_capture(wire, plan):
    require(type(wire) is dict and set(wire) == _CAPTURE_KEYS | {"derived_plan_sha256"}
            and wire["format"] == "mf-reader-execution-capture-wire-v1"
            and wire["packaged_source"] == plan["packaged_source"]
            and wire["grant_authorized"] is False
            and wire["custody"] == direct.custody_marker(plan["window"]), "INCOMPLETE_CAPTURE_PROOF")
    value = deepcopy(wire)
    before = value["before_snapshot"]
    require(type(before) is dict and set(before) == _SNAPSHOT_KEYS
            and before["format"] == "mf-broad-reader-live-snapshot-v1-nonexecutable"
            and before["grant_authorized"] is False and before["permission_enabled"] is False,
            "INCOMPLETE_CAPTURE_PROOF")
    state = before["state"]
    require(before["migration_proof"] == source.verify_live_ledger(before["ledger"], plan["packaged_source"])
            and before["function_proof"] == functions.verify(before["function_rows"], state)
            and before["view_profile"] == views.select(
                state, before["candidates"], base_authority_sha256=plan["packaged_source"]["base_sha256"],
                variant_authority_sha256=plan["packaged_source"]["variant_sha256"]),
            "CAPTURE_SOURCE_PROOF_MISMATCH")
    leaves = {row["relname"]: row for row in before["system_leaves"]}
    require(len(leaves) == len(before["system_leaves"]) == 3, "INCOMPLETE_CAPTURE_PROOF")
    derived = planner.prepare(state, helper_attestation=value["helper_attestation"],
                              candidates=before["candidates"] + before["system_leaves"],
                              system_leaves=leaves)
    selected = profiles.prepare(state)
    require(all(derived[key] == selected[key] for key in ("before", "after", "sql", "delta")),
            "SELECTED_POLICY_PLAN_MISMATCH")
    require(fingerprint(derived) == value.pop("derived_plan_sha256"), "DERIVED_PLAN_MISMATCH")
    before["plan"] = derived
    require(fingerprint(before) == value["capture_sha256"], "COMPLETE_CAPTURE_HASH_MISMATCH")
    value["format"] = "mf-broad-reader-prewrite-capture-v2-no-freeze-nonexecutable"
    return value


def certificate(plan, kind, predecessors, *, state, effect, data, diagnostic=None):
    payload = contract.stage_payload(plan, kind, predecessors)
    status = (diagnostic if diagnostic is not None else {
        "state": state, "reason": "OK", "safe_cause": "NONE", "stage": kind,
        "grant_effect": effect, "disposition": "ADVANCE", "consumer_acceptance": "PENDING_RO04"})
    require(status["grant_effect"] == effect and status["state"] == state,
            "OUTCOME_EFFECT_MISMATCH")
    body = {"format": "mf-reader-execution-result-v1", "kind": kind,
            "operation_id": plan["operation_id"], "plan_sha256": fingerprint(plan),
            "predecessor_sha256": fingerprint(predecessors[-1]) if predecessors else None,
            "git_sha": plan["git_sha"], "source_sha256": fingerprint(plan["packaged_source"]),
            "code_sha256": fingerprint(plan["code_binding"]),
            "images_sha256": fingerprint(plan["image_binding"]), "data": deepcopy(data), **status}
    return spec.seal_certificate(payload_sha256=fingerprint(payload),
                                 stage=contract.STAGES[kind], evidence=body)


def read_certificate(plan, kind, predecessors, value):
    payload = contract.stage_payload(plan, kind, predecessors)
    body = spec.verify_certificate(value, payload_sha256=fingerprint(payload),
                                    stage=contract.STAGES[kind])
    require(type(body) is dict and set(body) == {
        "format", "kind", "operation_id", "plan_sha256", "predecessor_sha256", "git_sha",
        "source_sha256", "code_sha256", "images_sha256", "data", "state", "reason", "safe_cause",
        "stage", "grant_effect", "disposition", "consumer_acceptance"}, "EXACT_RESULT_REQUIRED")
    require(body["format"] == "mf-reader-execution-result-v1" and body["kind"] == kind
            and body["operation_id"] == plan["operation_id"] and body["plan_sha256"] == fingerprint(plan)
            and body["predecessor_sha256"] == (fingerprint(predecessors[-1]) if predecessors else None)
            and body["git_sha"] == plan["git_sha"]
            and body["source_sha256"] == fingerprint(plan["packaged_source"])
            and body["code_sha256"] == fingerprint(plan["code_binding"])
            and body["images_sha256"] == fingerprint(plan["image_binding"])
            and body["consumer_acceptance"] == "PENDING_RO04"
            and body["grant_effect"] in diagnostics.EFFECTS
            and body["stage"] in diagnostics.PHASES and type(body["data"]) is dict,
            "PREDECESSOR_BINDING_MISMATCH")
    if body["state"] in SUCCESS:
        require(body["reason"] == "OK" and body["safe_cause"] == "NONE"
                and body["disposition"] == "ADVANCE" and body["stage"] == kind,
                "INVALID_SUCCESS_RESULT")
    else:
        require(body["state"] in diagnostics.STATES and body["reason"] == body["state"]
                and body["safe_cause"] in diagnostics.CAUSES
                and body["disposition"] == "STOP_INSPECT_ONLY_NO_REPLAY"
                and body["data"] == {}, "INVALID_FAILURE_RESULT")
    return body


def direct_document(plan, captured, *, intent_sha256):
    require(captured["packaged_source"] == plan["packaged_source"], "CAPTURE_SOURCE_PROOF_MISMATCH")
    return {"format": "mf-broad-reader-direct-grant-v1", "window": deepcopy(plan["window"]),
            "before_snapshot": deepcopy(captured["before_snapshot"]),
            "helper_attestation": deepcopy(captured["helper_attestation"]),
            "packaged_source": deepcopy(plan["packaged_source"]), "git_sha": plan["git_sha"],
            "image_binding": deepcopy(plan["image_binding"]),
            "create_only_intent_sha256": spec.sha(intent_sha256),
            "capture_custody": direct.custody_marker(plan["window"])}


def prove_capacity(plan, captured, predecessors):
    payload = contract.stage_payload(plan, "grant", predecessors)
    document = direct_document(plan, captured, intent_sha256=payload["create_only_intent_sha256"])
    old = capacity.prove_capacity(document, payload_sha256=fingerprint(document))
    proof = {"format": "mf-reader-execution-capacity-v1-nonexecutable", "legacy_bound": old,
             "maximal_complete_raw_bytes": capacity.MAX_SAFE_RAW_CERTIFICATE_BYTES,
             "maximal_reader_raw_bytes": capacity.MAX_SAFE_RAW_CERTIFICATE_BYTES,
             "maximal_reader_request_bytes": spec.MAX_REQUEST_BYTES,
             "reader_request_margin_bytes": 16384, "grant_authorized": False}
    maximum = capacity.maximal_grant_receipt(document)
    maximum.update(format="mf-broad-reader-direct-transaction-receipt-v2-no-freeze",
                   custody=direct.custody_marker(plan["window"]))
    data = {"transaction_receipt": maximum,
            "planned_after_sha256": captured["before_snapshot"]["plan"]["full_after_sha256"], "capacity_proof": proof}
    sealed = certificate(plan, "grant", predecessors, state=GRANTED, effect=diagnostics.VERIFIED, data=data)
    require(len(canonical(sealed).encode()) <= capacity.MAX_SAFE_RAW_CERTIFICATE_BYTES,
            "COMPLETE_PROOF_CAPACITY_EXCEEDED")
    after = captured["before_snapshot"]["plan"]["after"]
    context = {"database": spec.DATABASE, "principal": spec.READER_ROLE, "session": spec.READER_ROLE,
               "in_recovery": False, "readonly": "on", "isolation": "repeatable read"}
    namespaces = [{"oid": n["oid"], "nspname": n["nspname"], "usage": True, "create_allowed": False}
                  for n in after["namespaces"] if n["nspname"] in plan["target"]["schemas"]]
    roots = [r for r in after["relations"] if r["relkind"] in ("r", "p", "v", "m")]
    relations = [{**{key: r[key] for key in ("oid", "nspname", "relname", "relkind")},
                  "select_allowed": True, **dict.fromkeys(("insert_allowed", "update_allowed",
                    "delete_allowed", "truncate_allowed", "references_allowed", "trigger_allowed"), False)}
                 for r in roots]
    root_oids = {r["oid"] for r in roots}
    columns = [{"oid": c["oid"], "attnum": c["attnum"], "attname": c["name"],
                "select_allowed": True, "insert_allowed": False, "update_allowed": False, "references_allowed": False}
               for c in after["columns"] if c["oid"] in root_oids and not c["dropped"]]
    read = {**reader.validate(context, namespaces, relations, columns, planned_after=after),
            "representative_select_completed": True, "reader_profile": profiles.BROAD,
            "installed_policy_verified": True}
    reader_predecessors = [*predecessors, sealed]
    maximum_reader = certificate(plan, "reader", reader_predecessors, state=READ,
                                effect=diagnostics.VERIFIED, data={"reader_result": read})
    reader_size = len(canonical(maximum_reader).encode())
    require(reader_size <= capacity.MAX_SAFE_RAW_CERTIFICATE_BYTES, "COMPLETE_PROOF_CAPACITY_EXCEEDED")
    # Validate the complete future reader request before owner I/O/COMMIT too.
    # An extra deterministic high-entropy reserve is used ONLY in this capacity
    # calculation, never accepted as a stage payload or dispatched.
    request_payload = contract.stage_payload(plan, "reader", reader_predecessors)
    import hashlib
    request_payload["capacity_only_reserve"] = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(512)]
    request_payload["create_only_intent_sha256"] = spec.intent_preimage("reader", contract.operation_root(plan),
        request_payload, operation_id=plan["operation_id"])[1]
    pins = {row["path"]: row["sha256"] for row in plan["code_binding"]["files"]}
    request = spec.request("reader", root=contract.operation_root(plan), final_git_sha=plan["git_sha"],
        image=plan["image_binding"]["app"], payload=request_payload,
        worker_sha256=pins["scripts/reader_execution_dev.py"],
        controller_sha256=pins["src/mfdataindia/review/reader_execution_controller.py"],
        spec_sha256=pins["src/mfdataindia/review/broad_reader_direct_delivery_spec.py"],
        source_sha256=fingerprint(plan["packaged_source"]), operation_id=plan["operation_id"])
    require(request["request_bytes"] <= spec.MAX_REQUEST_BYTES, "COMPLETE_PROOF_CAPACITY_EXCEEDED")
    # The shared receipt records the fixed request bound, not compressor-version
    # dependent byte counts. Both operator and worker independently prove it;
    # canonical raw-certificate lengths remain deterministic across platforms.
    return {**proof, "maximal_complete_raw_bytes": len(canonical(sealed).encode()),
            "maximal_reader_raw_bytes": reader_size}


def verify_grant(plan, predecessors, body, captured):
    require(body["state"] == GRANTED and body["grant_effect"] == diagnostics.VERIFIED
            and body["reason"] == "OK" and body["disposition"] == "ADVANCE", "UNVERIFIED_GRANT")
    data = body["data"]
    require(set(data) == {"transaction_receipt", "planned_after_sha256", "capacity_proof"}, "INCOMPLETE_GRANT_PROOF")
    before = captured["before_snapshot"]
    desired = before["plan"]
    receipt = data["transaction_receipt"]
    payload = contract.stage_payload(plan, "grant", predecessors)
    document = direct_document(plan, captured, intent_sha256=payload["create_only_intent_sha256"])
    require(type(receipt) is dict and set(receipt) == {
        "format", "stage", "request_sha256", "before_sha256", "after_sha256", "source_sha256",
        "lock_proof", "pregrant_equality", "postgrant_proof", "delta", "materialized_unlocked",
        "postcommit_permanence_guarantee", "authenticated_reader_verified", "ti_verified",
        "permission_enabled", "custody"}
            and receipt["format"] == "mf-broad-reader-direct-transaction-receipt-v2-no-freeze"
            and receipt["stage"] == "COMMITTED_STATE_VERIFIED"
            and receipt["request_sha256"] == fingerprint(document)
            and receipt["before_sha256"] == desired["full_before_sha256"]
            and receipt["after_sha256"] == desired["full_after_sha256"]
            and receipt["source_sha256"] == fingerprint(plan["packaged_source"])
            and receipt["delta"] == desired["delta"]
            and receipt["custody"] == direct.custody_marker(plan["window"])
            and receipt["materialized_unlocked"] == desired["relation_scope"]["materialized_unlocked"]
            and receipt["postcommit_permanence_guarantee"] is False
            and receipt["authenticated_reader_verified"] is receipt["ti_verified"] is receipt["permission_enabled"] is False
            and receipt["pregrant_equality"] == snapshot.compare(before, before)
            and receipt["postgrant_proof"]["planned_after_sha256"] == desired["full_after_sha256"]
            and data["planned_after_sha256"] == desired["full_after_sha256"]
            and data["capacity_proof"] == prove_capacity(plan, captured, predecessors), "INCOMPLETE_GRANT_PROOF")
    proof = receipt["postgrant_proof"]
    require(type(proof) is dict and set(proof) == {
        "format", "before_sha256", "after_sha256", "planned_after_sha256", "permission_enabled"}
        and proof["format"] == "mf-broad-reader-postgrant-full-state-v1"
        and proof["before_sha256"] == fingerprint(before["state"])
        and proof["permission_enabled"] is False, "INCOMPLETE_GRANT_PROOF")
    spec.sha(proof["after_sha256"])
    lock = receipt["lock_proof"]
    require(lock == witness.verify_phases(desired["relation_scope"], baseline=lock["baseline"],
            tables=lock["tables"], views=lock["views"], target_pid=lock["target_pid"],
            observer_pid=lock["observer_pid"], database_oid=lock["database_oid"]), "INCOMPLETE_GRANT_PROOF")


def verify_chain(plan, kind, predecessors):
    require(kind in contract.KINDS and type(predecessors) is list, "INCOMPLETE_PREDECESSOR_CHAIN")
    if kind == "capture":
        require(not predecessors, "INCOMPLETE_PREDECESSOR_CHAIN")
        return None
    require(len(predecessors) in (1, 2), "INCOMPLETE_PREDECESSOR_CHAIN")
    first = read_certificate(plan, "capture", [], predecessors[0])
    require(first["state"] in (CAPTURED, NOOP) and first["reason"] == "OK"
            and first["grant_effect"] == diagnostics.NO_GRANT and first["disposition"] == "ADVANCE"
            and set(first["data"]) == {"capture"}, "UNVERIFIED_CAPTURE")
    captured = restore_capture(first["data"]["capture"], plan)
    before = captured["before_snapshot"]
    empty = not before["plan"]["sql"] and not any(before["plan"]["delta"].values())
    require(first["state"] == (NOOP if empty else CAPTURED), "NOOP_POLICY_MISMATCH")
    effect = diagnostics.NO_GRANT
    if kind == "grant":
        require(not empty and len(predecessors) == 1, "GRANT_NOT_REQUIRED_OR_CHAIN_MISMATCH")
    elif not empty:
        require(len(predecessors) == 2, "INCOMPLETE_PREDECESSOR_CHAIN")
        grant_body = read_certificate(plan, "grant", predecessors[:1], predecessors[1])
        verify_grant(plan, predecessors[:1], grant_body, captured)
        effect = diagnostics.VERIFIED
    else:
        require(len(predecessors) == 1, "NOOP_MUST_NOT_MANUFACTURE_COMMIT")
    return {"capture": captured, "planned_after": before["plan"]["after"], "prior_effect": effect}


def verify_reader(plan, predecessors, certificate_value):
    prior = verify_chain(plan, "reader", predecessors)
    body = read_certificate(plan, "reader", predecessors, certificate_value)
    require(body["state"] == READ and body["reason"] == "OK" and body["disposition"] == "ADVANCE"
            and body["grant_effect"] == prior["prior_effect"]
            and set(body["data"]) == {"reader_result"}, "UNVERIFIED_READER")
    result = body["data"]["reader_result"]
    checked = reader.validate(result["context"], result["namespaces"], result["relations"], result["columns"],
                               planned_after=prior["planned_after"])
    require(result == {**checked, "representative_select_completed": True,
                       "reader_profile": profiles.BROAD, "installed_policy_verified": True},
            "UNVERIFIED_READER")
    return body
