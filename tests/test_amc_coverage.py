"""Census tests distinguish discovery, source evidence and real publication."""

import json
from copy import deepcopy
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from mfdataindia.enrichment import amc_coverage as coverage
from mfdataindia.enrichment.amc_register import (
    Register,
    load_register,
    parse_directory,
    url_reasons,
)
from mfdataindia.review.app import (
    create_review_app,
    create_fact_verification_app,
    create_amc_coverage_app,
)
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.store import Conflict
from test_fact_verification import prepared
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust, assertion
from test_verified_review_store import ROLES, source_state
from test_verified_review_store import verified_db as verified_db


def registry():
    return Register.model_validate(
        {
            "format": "official-amc-source-register-v1",
            "version": 1,
            "directory_url": "https://www.amfiindia.com/online-center/download-factsheets",
            "directory_sha256": "a" * 64,
            "observed_at": datetime(2026, 10, 16, tzinfo=timezone.utc),
            "capture_execution": "mf-fs06-directory-capture-test",
            "population_basis": "official_directory_entries_not_all_registered_or_active_AMCs",
            "members": [
                {
                    "member_id": "3",
                    "name": "Alpha Mutual Fund",
                    "listed_factsheet_url": "https://alpha.example/facts/",
                    "source_state": "listed",
                    "url_review_reasons": [],
                    "planned_wave": "FS-08",
                    "acquisition_enabled": False,
                },
                {
                    "member_id": "4",
                    "name": "Missing Mutual Fund",
                    "listed_factsheet_url": None,
                    "source_state": "missing",
                    "url_review_reasons": ["no_factsheet_link_listed"],
                    "planned_wave": "EXP-01",
                    "acquisition_enabled": False,
                },
            ],
        }
    )


def fund(code, amc_id=1, **changes):
    return {
        "amfi_scheme_code": code,
        "amc_id": amc_id,
        "scheme_name": "Test " + str(code),
        "plan_type": "REGULAR",
        "option_type": "GROWTH",
        "in_scope": True,
        "is_defunct": False,
        "is_active": False,
        **changes,
    }


def build(**overrides):
    args = {
        "register": registry(),
        "amcs": [
            {"amc_id": 1, "amfi_amc_name": "Alpha Mutual Fund"},
            {"amc_id": 2, "amfi_amc_name": "Historical Unresolved AMC"},
        ],
        "funds": [
            fund(1),
            fund(2, 2, in_scope=False, plan_type="DIRECT"),
            fund(3, is_defunct=True),
        ],
        "sources": [],
        "snapshots": [],
        "selected": [],
        "active": True,
        "dataset_version": 22,
        "revision": "b" * 64,
    }
    return coverage.build(**{**args, **overrides})


def frame(rows, headings):
    data = "c:" + json.dumps(["$", "$L19", None, {"data": rows}]) + "\n"
    script = "self.__next_f.push(" + json.dumps([1, data]) + ")"
    return (
        "<html>"
        + "".join("<h5>" + h + "</h5>" for h in headings)
        + "<script>"
        + script
        + "</script></html>"
    ).encode()


def test_next_directory_parser_retains_missing_records_and_does_not_execute_scripts():
    rows = [
        {
            "mf_id": "1",
            "mf_name": "Alpha Mutual Fund",
            "amc_monthly_mf_factsheets": "http://alpha.example/#facts",
        },
        {"mf_id": "2", "mf_name": "Missing Mutual Fund", "amc_monthly_mf_factsheets": ""},
    ]
    result = parse_directory(
        frame(rows, ["Alpha Mutual Fund"]) + b'<script>throw new Error("never execute")</script>'
    )
    assert len(result) == 2 and result[1]["listed_factsheet_url"] is None
    assert result[0]["listed_factsheet_url"] == "http://alpha.example/#facts"
    assert url_reasons(result[0]["listed_factsheet_url"]) == [
        "fragment_requires_source_adapter",
        "non_https_listing",
        "not_approved_transport_url",
    ]
    with pytest.raises(ValueError, match="disagree"):
        parse_directory(frame(rows, ["Alpha Mutual Fund", "Missing Mutual Fund"]))
    with pytest.raises(ValueError, match="required keys"):
        parse_directory(
            frame(
                [
                    {
                        "mf_id": "1",
                        "mf_name": "Alpha Mutual Fund",
                        "other": "amc_monthly_mf_factsheets",
                    }
                ],
                [],
            )
        )
    with pytest.raises(ValueError, match="duplicate"):
        parse_directory(frame(rows + rows, ["Alpha Mutual Fund"]))


def test_register_has_actual_57_entries_51_links_and_no_activation_capability():
    result = load_register()
    assert (
        len(result.members) == 57 and sum(m.source_state == "listed" for m in result.members) == 51
    )
    assert (
        result.directory_sha256
        == "83515327db20693cc23aaf730f7fe7a510d65257652b50e07ab5a7d401531ec2"
    )
    assert all(not r.acquisition_enabled for r in result.members)
    assert len({r.member_id for r in result.members}) == 57
    rows = result.model_dump(mode="json")
    rows["members"][0]["acquisition_enabled"] = True
    with pytest.raises(ValueError, match="authority"):
        Register.model_validate(rows)


def test_register_rejects_duplicate_identity_undated_evidence_and_url_reason_drift():
    for change in ("duplicate", "date", "reason"):
        doc = registry().model_dump(mode="json")
        if change == "duplicate":
            doc["members"].append(deepcopy(doc["members"][0]))
        if change == "date":
            doc["observed_at"] = "2026-10-16T00:00:00"
        if change == "reason":
            doc["members"][0]["url_review_reasons"] = ["invented"]
        with pytest.raises(ValueError):
            Register.model_validate(doc)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/x",
        "https://user:pass@example.com/x",
        "https://[bad",
        "https://example.com:443/x",
        "https://example.com/x,OTHER=value",
    ],
)
def test_unsafe_discovery_urls_remain_flagged_not_transport_approved(url):
    assert "not_approved_transport_url" in url_reasons(url)


def test_census_accounts_for_every_retained_scheme_without_lifecycle_or_scope_rewrite():
    report, pages = build()
    assert report["directory_entries"] == 2 and report["database_amcs"] == 2
    assert report["database_only_amcs"] == 1 and report["retained_schemes"] == 3
    assert sum(r["counts"]["retained_schemes"] for r in report["amcs"]) == 3
    assert pages["amfi-4"] == [] and len(pages["db-2"]) == 1
    assert pages["amfi-3"][0]["operational_target"]  # is_active false is not silently excluded
    assert not pages["amfi-3"][1]["operational_target"]  # defunct retained but not a new target
    assert not report["all_amc_data_complete"] and not report["new_acquisition_authority"]
    assert pages["amfi-3"][0]["current_field_dates"] == {}
    assert pages["amfi-3"][0]["extraction_state"] == "no_monthly_snapshot_evidence"


def test_archival_extraction_and_active_projection_are_independent_counts():
    snapshots = [
        {"amfi_scheme_code": 1, "disclosure_as_of": "2026-09-30", "accepted_field_observations": 23}
    ]
    sources = [{"content_hash": "c" * 64, "intake": {"codes": [1]}}]
    selected = [{"code": 1, "field": "risk_level", "as_of": "2026-09-30", "value": "Very High"}]
    report, pages = build(sources=sources, snapshots=snapshots, selected=selected, active=False)
    counts = next(r["counts"] for r in report["amcs"] if r["amc_key"] == "amfi-3")
    assert counts["with_monthly_archived_book"] == 1 and counts["with_monthly_snapshot"] == 1
    assert counts["with_current_projection_fields"] == 0
    assert pages["amfi-3"][0]["accepted_field_observations"] == 23
    report, pages = build(sources=sources, snapshots=snapshots, selected=selected)
    assert pages["amfi-3"][0]["current_field_dates"] == {"risk_level": "2026-09-30"}
    assert not pages["amfi-3"][0]["entire_factsheet_coverage"]


def test_ambiguous_database_name_never_fuzzily_merges_or_drops_schemes():
    amcs = [
        {"amc_id": 1, "amfi_amc_name": "Alpha Mutual Fund"},
        {"amc_id": 2, "amfi_amc_name": " ALPHA  mutual fund "},
    ]
    result, pages = build(amcs=amcs)
    assert result["mapping_counts"]["ambiguous_database_name"] == 1
    assert len(pages["db-1"]) == 2 and len(pages["db-2"]) == 1
    assert result["retained_schemes"] == 3


def test_contract_is_strictly_additive_and_dormant_routes_never_connect(monkeypatch):
    dsn = "postgresql://fixture:fixture@127.0.0.1:1/fixture"
    old, new = create_fact_verification_app(dsn).openapi(), create_amc_coverage_app(dsn).openapi()
    assert len(new["paths"]) == 46
    assert all(new["paths"][k] == v for k, v in old["paths"].items())
    assert all(
        new["components"]["schemas"][k] == v for k, v in old["components"]["schemas"].items()
    )
    monkeypatch.delenv("MFDATAINDIA_REVIEW_TRUST_FILE", raising=False)
    with TestClient(create_amc_coverage_app(dsn)) as client:
        for path in (
            "/api/enrichment/v7/coverage",
            "/api/enrichment/v7/policy",
            "/api/enrichment/v7/amcs/amfi-3/schemes",
        ):
            response = client.get(path, headers={"X-Role": "admin"})
            assert response.status_code == 503 and response.headers["Cache-Control"] == "no-store"


def test_pages_require_revision_and_reject_changed_dependencies(monkeypatch):
    monkeypatch.setattr(coverage, "capture", lambda _: build())
    first = coverage.schemes(None, "amfi-3", limit=1)
    assert first["next_after_code"] == 1
    with pytest.raises(ValueError):
        coverage.schemes(None, "amfi-3", after_code=1)
    with pytest.raises(Conflict):
        coverage.schemes(None, "amfi-3", after_code=1, expected_revision="d" * 64)
    last = coverage.schemes(
        None, "amfi-3", after_code=1, expected_revision=first["coverage_revision"]
    )
    assert [r["amfi_scheme_code"] for r in last["items"]] == [3]


def test_independent_certificate_rejects_omitted_directory_member(monkeypatch):
    from mfdataindia.enrichment.amc_coverage_certification import independent_denominator
    from mfdataindia.enrichment import amc_register

    class Result:
        def fetchall(self):
            return [
                {
                    "amc_id": 1,
                    "retained_schemes": 2,
                    "in_scope": 2,
                    "in_scope_non_defunct": 1,
                    "out_of_scope": 0,
                },
                {
                    "amc_id": 2,
                    "retained_schemes": 1,
                    "in_scope": 0,
                    "in_scope_non_defunct": 0,
                    "out_of_scope": 1,
                },
            ]

    class Connection:
        def execute(self, _):
            return Result()

    monkeypatch.setattr(amc_register, "load_register", registry)
    report, _ = build()
    assert independent_denominator(Connection(), report)["retained_schemes"] == 3
    report["amcs"] = [g for g in report["amcs"] if g["amc_key"] != "amfi-4"]
    with pytest.raises(ValueError, match="directory members"):
        independent_denominator(Connection(), report)


@pytest.mark.postgres
def test_actual_restricted_reader_census_http_and_preservation(verified_db, monkeypatch):
    prepared(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        before = source_state(conn)
    reader = runtime_dsn(verified_db, ROLES[1])
    with psycopg.connect(reader, row_factory=dict_row) as conn:
        with pytest.raises(ValueError, match="read-only"):
            coverage.summary(conn)
    with psycopg.connect(reader, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        with pytest.raises(ValueError, match="repeatable-read"):
            coverage.summary(conn)
    with psycopg.connect(reader, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        result = coverage.summary(conn)
        from mfdataindia.enrichment.amc_coverage_certification import independent_denominator

        assert (
            independent_denominator(conn, result)["retained_schemes"] == result["retained_schemes"]
        )
    assert result["retained_schemes"] >= 2
    assert (
        sum(a["counts"]["retained_schemes"] for a in result["amcs"]) == result["retained_schemes"]
    )
    key, verifier = trust()
    app = create_review_app(reader, verifier=verifier, amc_coverage=True)
    headers = {"Authorization": "Bearer " + assertion(key, scope=["review:read"])}
    with TestClient(app) as client:
        assert client.get("/api/enrichment/v7/coverage").status_code == 401
        response = client.get("/api/enrichment/v7/coverage", headers=headers)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        doc = response.json()
        group = next(g for g in doc["amcs"] if g["counts"]["retained_schemes"] > 1)
        base = "/api/enrichment/v7/amcs/" + group["amc_key"] + "/schemes"
        first = client.get(base + "?limit=1", headers=headers).json()
        assert len(first["items"]) == 1 and first["next_after_code"]
        assert client.get(base + "?after_code=1", headers=headers).status_code == 422
        assert (
            client.get(base + "?expected_revision=" + ("d" * 64), headers=headers).status_code
            == 409
        )
        assert (
            client.get("/api/enrichment/v7/amcs/amfi-999999/schemes", headers=headers).status_code
            == 404
        )
        assert (
            client.post("/api/enrichment/v7/coverage", json={}, headers=headers).status_code == 405
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert source_state(conn) == before
    assert fingerprint(result) == fingerprint(doc)
