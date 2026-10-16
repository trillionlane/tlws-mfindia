"""Versioned human verification policy; never source publication or correction."""

from copy import deepcopy

_POLICY = {
    "id": "official-facts-human-verification",
    "version": 1,
    "scope": "selected current official scalar fields; immutable source history/change reads",
    "authority": "private backend signed review:admin assertion; caller must delegate a real human actor and record original-source review attestation",
    "verification": "explicit human_reviewed_original attestation, reason, exact selected source snapshot references and named fields; acquisition, parser, CLI, proposal or rollout success cannot create human verification",
    "dependencies": "exact current identity, current policy and all selected/head scalar values, dates and source snapshot IDs; a changed dependency requires revalidation even if a displayed value remains identical",
    "concurrency": "expected fund review version plus exact dependency hash; same publication lock as source refresh at the database boundary",
    "idempotency": "caller-scoped stable key; actor, fund and request bound; replay returns the original receipt without renewing verification",
    "status": "latest actual review per named field only; reviewing another field cannot clear an earlier stale review",
    "history": "retain every original source, source snapshot and human event; bounded stable cursors; differences compare retained same-fund source fields without applying edits",
    "publication": "routine source-validated publication continues independently of human review; conflicts/unusable evidence cannot be marked verified by this endpoint",
    "source_rows_changed": 0,
    "UI": False,
    "canonical_analytics_replaced": False,
    "limitations": "signed backend is responsible for authenticating the human; API does not independently prove physical identity; no live synthetic human review for certification",
}


def policy():
    return deepcopy(_POLICY)
