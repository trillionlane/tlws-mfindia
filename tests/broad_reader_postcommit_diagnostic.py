"""Disposable-test-only fixed labels; no exception or catalogue values escape."""

from copy import deepcopy

from mfdataindia.review import broad_reader_live_snapshot as snapshot


LABELS = frozenset({
    "POSTCOMMIT_READ_FAILURE", "PLANNED_STATE", "CANDIDATES",
    "SYSTEM_LEAVES", "LEDGER_PROOF", "VIEW_PROFILE", "FUNCTION_ROWS",
    "IDEMPOTENT_AFTER_PLAN", "UNCLASSIFIED_COMPARISON",
    "READ_STATE", "READ_CANDIDATES", "READ_LEAVES", "READ_FUNCTIONS",
    "READ_LEDGER", "VERIFY_LEDGER", "VERIFY_FUNCTIONS", "VERIFY_PROFILE",
    "PREPARE_PLAN", "READ_FINAL",
    "PLAN_HELPER", "PLAN_CALLEE", "PLAN_DEPENDENCY", "PLAN_NORMALIZE",
    "PLAN_HISTORICAL", "PLAN_INVARIANTS", "PLAN_REVERSAL",
})
READ_STAGES = frozenset({
    "READ_STATE", "READ_CANDIDATES", "READ_LEAVES", "READ_FUNCTIONS",
    "READ_LEDGER", "VERIFY_LEDGER", "VERIFY_FUNCTIONS", "VERIFY_PROFILE",
    "PREPARE_PLAN", "READ_FINAL",
    "PLAN_HELPER", "PLAN_CALLEE", "PLAN_DEPENDENCY", "PLAN_NORMALIZE",
    "PLAN_HISTORICAL", "PLAN_INVARIANTS", "PLAN_REVERSAL",
})


def read_failure_label(_error, *, stage=None):
    """Never include an exception type, message, SQL, or private value."""
    return stage if stage in READ_STAGES else "POSTCOMMIT_READ_FAILURE"


def stage_wrapper(diagnostic, stage, operation, *, completed=None):
    """Test-only, one-call forwarding with a fixed post-read stage label."""
    assert stage in READ_STAGES
    assert completed is None or completed in READ_STAGES

    def forward(*args, **kwargs):
        if diagnostic["post_read_active"]:
            diagnostic["label"] = stage
        result = operation(*args, **kwargs)
        if completed is not None and diagnostic["post_read_active"]:
            diagnostic["label"] = completed
        return result

    return forward


def install_plan_stage_labels(monkeypatch, diagnostic):
    """Observe the existing pure planner without changing its calls or result."""
    planner = snapshot.direct_plan
    for module, name, label in (
        (planner.functions, "require_existing_helper", "PLAN_HELPER"),
        (planner.functions, "require_existing_callee", "PLAN_CALLEE"),
        (planner.dependencies, "classify", "PLAN_DEPENDENCY"),
        (planner, "reversal_template", "PLAN_REVERSAL"),
    ):
        original = getattr(module, name)
        monkeypatch.setattr(module, name, stage_wrapper(diagnostic, label, original))

    original_catalogue = planner.policy.catalogue

    def observed_catalogue(*args, **kwargs):
        if (diagnostic["post_read_active"]
                and not diagnostic["historical_prepare_active"]):
            diagnostic["label"] = "PLAN_NORMALIZE"
        return original_catalogue(*args, **kwargs)

    monkeypatch.setattr(planner.policy, "catalogue", observed_catalogue)
    original_historical = planner.policy.prepare

    def observed_historical(*args, **kwargs):
        active = diagnostic["post_read_active"]
        if active:
            diagnostic["label"] = "PLAN_HISTORICAL"
            diagnostic["historical_prepare_active"] = True
        try:
            result = original_historical(*args, **kwargs)
        finally:
            if active:
                diagnostic["historical_prepare_active"] = False
        if active:
            diagnostic["label"] = "PLAN_INVARIANTS"
        return result

    monkeypatch.setattr(planner.policy, "prepare", observed_historical)


def install_read_stage_labels(monkeypatch, diagnostic):
    """Observe existing second-read call sites without extra SQL or values."""
    statement_labels = {
        snapshot.policy.STATE_SQL: "READ_STATE",
        snapshot.inventory.CANDIDATE_SQL: "READ_CANDIDATES",
        snapshot.dependencies.SYSTEM_LEAVES_SQL: "READ_LEAVES",
        snapshot.functions.FUNCTION_SQL: "READ_FUNCTIONS",
        snapshot.source.LEDGER_SQL: "READ_LEDGER",
    }
    assert len(statement_labels) == 5
    original_rows = snapshot._rows

    def observed_rows(conn, statement, columns):
        if diagnostic["post_read_active"]:
            diagnostic["label"] = statement_labels.get(
                statement, "POSTCOMMIT_READ_FAILURE")
        return original_rows(conn, statement, columns)

    monkeypatch.setattr(snapshot, "_rows", observed_rows)
    install_plan_stage_labels(monkeypatch, diagnostic)
    for module, name, label, completed in (
        (snapshot.source, "verify_live_ledger", "VERIFY_LEDGER", None),
        (snapshot.functions, "verify", "VERIFY_FUNCTIONS", None),
        (snapshot.views, "select", "VERIFY_PROFILE", None),
        (snapshot.direct_plan, "prepare", "PREPARE_PLAN", "READ_FINAL"),
    ):
        original = getattr(module, name)
        monkeypatch.setattr(
            module, name, stage_wrapper(diagnostic, label, original, completed=completed))


def compare_failure_label(before, after):
    """Classify only the first existing postcheck mismatch, without SQL."""
    try:
        plan = before.get("plan")
        if (type(plan) is not dict
                or snapshot.canonical_state(after.get("state")) != plan.get("after")):
            return "PLANNED_STATE"
        if before.get("candidates") != after.get("candidates"):
            return "CANDIDATES"
        if before.get("system_leaves") != after.get("system_leaves"):
            return "SYSTEM_LEAVES"
        if (before.get("ledger") != after.get("ledger")
                or before.get("migration_proof") != after.get("migration_proof")):
            return "LEDGER_PROOF"
        if before.get("view_profile") != after.get("view_profile"):
            return "VIEW_PROFILE"
        old_rows = deepcopy(before.get("function_rows"))
        new_rows = deepcopy(after.get("function_rows"))
        if (type(old_rows) is not list or type(new_rows) is not list
                or len(old_rows) != 2 or len(new_rows) != 2):
            return "FUNCTION_ROWS"
        for old, new in zip(old_rows, new_rows, strict=True):
            if type(old) is not dict or type(new) is not dict:
                return "FUNCTION_ROWS"
            old.pop("reader_schema_usage", None)
            new.pop("reader_schema_usage", None)
            if old != new:
                return "FUNCTION_ROWS"
        if (after["plan"]["after"] != snapshot.canonical_state(after["state"])
                or any(after["plan"]["delta"].values())):
            return "IDEMPOTENT_AFTER_PLAN"
    except BaseException:
        return "UNCLASSIFIED_COMPARISON"
    return "UNCLASSIFIED_COMPARISON"
