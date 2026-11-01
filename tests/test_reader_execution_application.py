"""Actual ASGI transport plumbing; native fixture separately proves DB checks."""
import asyncio
from copy import deepcopy

import pytest

from mfdataindia.review.app import create_review_app
from mfdataindia.review import reader_execution_application as application
from mfdataindia.review.policy import canonical
from reader_execution_fixtures import plan, app_context


def test_actual_asgi_no_store_unauthenticated_dormant_app_never_connects():
    app = create_review_app("postgresql://fixture@127.0.0.1:1/fixture", monthly_facts=True)
    result = asyncio.run(application._request(app, "GET", "/api/enrichment/v4/funds/1/facts"))
    assert result["status"] == 503 and result["no_store"] is True
    assert set(result) == {"status", "no_store", "seconds"}


def test_application_wrong_identity_dsn_or_trust_rejects_before_startup():
    value, verifier, private = plan()
    context = app_context(value, verifier, private)
    wrong = application.ApplicationCheck(context.dsn.replace("mfdata_verified_reader", "mfdata_admin"), verifier, context.read_assertion)
    with pytest.raises(ValueError):
        wrong.validate(value)
    changed = deepcopy(value)
    changed["application_trust_sha256"] = "a" * 64
    with pytest.raises(ValueError):
        context.validate(changed)
    assert context.dsn not in canonical(application.public_trust(verifier))


def test_admin_claim_cannot_replace_the_required_read_assertion():
    value, verifier, private = plan()
    original = app_context(value, verifier, private)
    wrong = application.ApplicationCheck(original.dsn, verifier, original.admin_assertion)
    with pytest.raises(ValueError, match="APPLICATION_ASSERTION_SCOPE_MISMATCH"):
        wrong.validate(value)


def test_missing_admin_assertion_cannot_certify_profile_mutation_boundary():
    value, verifier, private = plan()
    context = app_context(value, verifier, private)
    missing = application.ApplicationCheck(context.dsn, verifier, context.read_assertion)
    with pytest.raises(ValueError, match="APPLICATION_ASSERTION_REQUIRED"):
        missing.validate(value)
