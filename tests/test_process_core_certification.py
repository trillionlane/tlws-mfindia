"""Full local HTTP core shape proof; real private DEV witness is separate."""

from copy import deepcopy
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from mfdataindia.api.app import create_app
from mfdataindia.enrichment import process_core_certification as certification
from test_official_fact_refresh import setup
from test_verified_review_store import verified_db as verified_db


def test_all_nineteen_real_core_read_templates_are_enumerated():
    specification = create_app("postgresql://fixture:fixture@127.0.0.1:1/fixture").openapi()
    paths = {
        route
        for route, methods in specification["paths"].items()
        if route.startswith("/api/") and "get" in methods
    }
    assert {template for template, _ in certification.cases()} == paths
    assert len(paths) == len(certification.cases()) == 19


@pytest.mark.parametrize("suffix", ["returns", "analytics", "peers", "risk-reward"])
def test_valid_shape_from_another_fund_cannot_certify_primary_growth_identity(suffix):
    documents = {"/api/funds/{code}": {"amfi_scheme_code": 103174, "option_type": "GROWTH"}}
    documents.update(
        {
            "/api/funds/{code}/" + part: {"code": 103174}
            for part in ("returns", "analytics", "peers", "risk-reward")
        }
    )
    certification.primary_identity(documents)
    documents["/api/funds/{code}/" + suffix]["code"] = 103173
    with pytest.raises(ValueError, match="identity differs"):
        certification.primary_identity(documents)


@pytest.mark.parametrize(
    "change", ["none", "omitted", "empty_unknown", "current_null", "missing_lifecycle"]
)
def test_explicit_unassessed_null_cannot_be_inferred_from_defaults(change):
    body = {
        "lifecycle": {"state": "unknown", "evidence": "insufficient_evidence"},
        "nav_freshness": {"status": "missing"},
        "nav_quality": {"assessment_status": "not_assessed", "signals": None},
        "methodology": {
            "basis": "nav_change",
            "distribution_adjustment": "unavailable",
            "comparison_eligible": False,
            "limitations": ["distribution_history_unavailable"],
        },
    }
    body = deepcopy(body)
    if change == "omitted":
        del body["nav_quality"]["signals"]
    elif change == "empty_unknown":
        body["nav_quality"]["signals"] = []
    elif change == "current_null":
        body["nav_quality"]["assessment_status"] = "current"
    elif change == "missing_lifecycle":
        del body["lifecycle"]
    if change == "none":
        assert certification.signals(body) == (1, 1)
    else:
        with pytest.raises(ValueError):
            certification.signals(body)


@pytest.mark.postgres
def test_actual_local_core_http_all_routes_and_growth_idcw_null_samples(verified_db, monkeypatch):
    setup(verified_db, monkeypatch)
    monkeypatch.setattr(
        certification, "metadata_identity", lambda session, audience: "synthetic-identity"
    )

    def lookup(codes):
        with psycopg.connect(verified_db, row_factory=dict_row) as conn:
            return {
                row["amfi_scheme_code"]: row["option_type"]
                for row in conn.execute(
                    "SELECT amfi_scheme_code,option_type FROM mf.funds WHERE amfi_scheme_code=ANY(%s)",
                    (codes,),
                )
            }

    with TestClient(create_app(verified_db)) as client:

        class Session:
            def get(self, url, **kwargs):
                kwargs["follow_redirects"] = kwargs.pop("allow_redirects")
                return client.get(url, **kwargs)

        result = certification.certify(Session(), unassessed_code=103174, option_lookup=lookup)
    assert result["routes_checked"] == 19 and result["responses_checked"] == 22
    assert result["explicit_null_observations"] > 0 and not result["idcw_comparison_eligible"]
