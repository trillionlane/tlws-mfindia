"""Disposable-test-only fixed labels; no exception or catalogue values escape."""

from copy import deepcopy

from mfdataindia.review import broad_reader_live_snapshot as snapshot


LABELS = frozenset({
    "POSTCOMMIT_READ_FAILURE", "PLANNED_STATE", "CANDIDATES",
    "SYSTEM_LEAVES", "LEDGER_PROOF", "VIEW_PROFILE", "FUNCTION_ROWS",
    "IDEMPOTENT_AFTER_PLAN", "UNCLASSIFIED_COMPARISON",
})


def read_failure_label(_error):
    """Never include an exception type, message, SQL, or private value."""
    return "POSTCOMMIT_READ_FAILURE"


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
