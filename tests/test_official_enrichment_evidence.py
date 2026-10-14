"""Synthetic independent parser/mapping/security checks; not live source certification."""

import hashlib
from copy import deepcopy

import pytest
from pydantic import ValidationError

from mfdataindia.enrichment import acquisition, evidence, schemas
from mfdataindia.review.policy import fingerprint

ISIN = "INF179K01574"


def document(code="1", isin=ISIN, plan="REGULAR", option="GROWTH", name="Fund 1"):
    return (
        "<table><tr><th>AMFI scheme code</th><th>Scheme name</th><th>ISIN</th><th>Plan</th><th>Option</th></tr>"
        f"<tr><td>{code}</td><td>{name}</td><td>{isin}</td><td>{plan}</td><td>{option}</td></tr></table>"
    ).encode()


def identity():
    return {
        "code": 1,
        "amc": "Verified fixture",
        "scheme_name": "Fund 1",
        "plan": "REGULAR",
        "option": "GROWTH",
        "isins": [ISIN],
    }


def intake(content=None, **changes):
    content = content or document()
    return schemas.Intake.model_validate(
        {
            "source_url": "https://official.example/facts.html",
            "final_url": "https://official.example/facts.html",
            "official_hosts": ["official.example"],
            "amc": "Verified fixture",
            "observed_at": "2026-10-14T10:00:00Z",
            "document_as_of": "2026-09-30",
            "media_type": "text/html",
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "codes": [1],
            "use_basis": "operator_reported_authorization",
            "authority_reference": "SYNTHETIC FIXTURE NOT PROVIDER PERMISSION",
            "source_conflict": False,
            **changes,
        }
    )


def test_policy_copy_and_exact_candidate_deterministic_replay():
    policy = evidence.policy()
    policy["headers"].clear()
    assert len(evidence.policy()["headers"]) == 5
    parsed = evidence.classify(document(), "text/html")
    mapping = evidence.mappings(parsed, [identity()], "Verified fixture")[0]
    assert mapping["state"] == "matched_candidate" and mapping["reason"] is None
    assert mapping["identity_hash"] == fingerprint(identity())
    assert mapping == evidence.mappings(parsed, [identity()], "Verified fixture")[0]
    assert evidence.valid_isin(ISIN)
    assert not evidence.valid_isin(ISIN[:-1] + "5")


@pytest.mark.parametrize(
    "change,reason",
    [
        (dict(plan="DIRECT"), "plan_mismatch"),
        (dict(option="IDCW"), "option_mismatch"),
        (dict(isin="INF179K01575"), "invalid_isin_checksum"),
        (dict(name="Fund 2"), "scheme_name_mismatch"),
        (dict(code="2"), "code_absent"),
    ],
)
def test_never_infer_siblings_or_repair_identifiers(change, reason):
    parsed = evidence.classify(document(**change), "text/html")
    item = evidence.mappings(parsed, [identity()], "Verified fixture")[0]
    assert item["state"] == "unmapped" and item["reason"] == reason


def test_duplicate_rows_are_ambiguous_even_if_identical_and_amc_scope_exact():
    parsed = evidence.classify(document() + document(), "text/html")
    assert (
        evidence.mappings(parsed, [identity()], "Verified fixture")[0]["reason"]
        == "ambiguous_duplicate_code"
    )
    parsed = evidence.classify(document(), "text/html")
    assert evidence.mappings(parsed, [identity()], "Other AMC")[0]["reason"] == "amc_mismatch"
    alternate = deepcopy(identity())
    alternate["isins"] = ["INF179K01582"]
    assert (
        evidence.mappings(parsed, [alternate], "Verified fixture")[0]["reason"] == "isin_mismatch"
    )


def test_retained_pdf_is_not_claimed_as_parsed_or_full_holdings():
    content = b"%PDF-1.7\nsynthetic unparsed bytes"
    parsed = evidence.classify(content, "application/pdf")
    assert parsed == {"state": "unsupported", "reason": "pdf_parser_not_implemented", "rows": []}
    assert evidence.mappings(parsed, [identity()], "Verified fixture")[0]["state"] == "unmapped"
    with pytest.raises(ValueError):
        evidence.classify(document(), "application/pdf")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\xff<table></table>",
        b"<table><tr><td>x",
        b"<table><tr><td colspan='2'>x</td></tr></table>",
        b"<table><tr><td>x<tr><td>y</td></tr></table>",
    ],
)
def test_malformed_and_unsupported_markups_fail_closed(payload):
    with pytest.raises((ValueError, UnicodeError)):
        evidence.classify(payload, "text/html")


def test_headers_cannot_leak_between_tables_or_scripts():
    header_only = document().split(b"<tr><td>", 1)[0] + b"</table>"
    unrelated = b"<table><tr><td>1</td><td>Fund 1</td><td>INF179K01574</td><td>REGULAR</td><td>GROWTH</td></tr></table>"
    assert evidence.classify(header_only + unrelated, "text/html")["state"] == "unsupported"
    assert evidence.classify(b"<script>" + document() + b"</script>", "text/html")["rows"] == []


@pytest.mark.parametrize(
    "url",
    [
        "http://official.example/x",
        "https://official.example.evil/x",
        "https://user@official.example/x",
        "https://official.example:443/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://official.example/x?token=secret",
        "https://official.example/x#fragment",
        "https://official.example/x\n",
        "https://official.example/x,y=1",
        "https://official.example/\\evil",
    ],
)
def test_url_boundary_rejects_unapproved_or_secret_bearing_urls(url):
    with pytest.raises(ValueError):
        evidence.public_url(url, {"official.example"})


@pytest.mark.parametrize(
    "changes",
    [
        {"codes": [True]},
        {"codes": [1, 1]},
        {"codes": [0]},
        {"codes": [2147483648]},
        {"observed_at": "2026-10-14T10:00:00"},
        {"document_as_of": "2026-10-15"},
        {"source_conflict": True},
        {"amc": "AMC\nInjected"},
        {"official_hosts": ["official.example", "official.example"]},
        {"unknown": "field"},
    ],
)
def test_intake_bounds_and_reported_permission_are_not_blanket_clearance(changes):
    with pytest.raises(ValidationError):
        intake(**changes)
    with pytest.raises(ValidationError):
        intake(
            source_url=evidence.DIRECTORY,
            final_url=evidence.DIRECTORY,
            official_hosts=["www.amfiindia.com"],
        )


def test_directory_recognizes_dynamic_candidates_without_universe_claim_or_host_approval():
    html = b'<a href="https://official.example/factsheets"><img alt="Small Mutual Fund"></a><a href="https://official.example/factsheets">Small Mutual Fund</a><a href="http://bad.example/x">Bad Mutual Fund</a>'
    result = evidence.directory(html)
    assert result["complete"] is False
    assert result["sources"] == [
        {
            "amc_display_name": "Small Mutual Fund",
            "url": "https://official.example/factsheets",
            "state": "discovery_candidate",
        }
    ]
    assert result["content_hash"] == hashlib.sha256(html).hexdigest()


def test_dns_rebinding_and_internal_redirect_do_not_open_socket(monkeypatch):
    monkeypatch.setattr(
        acquisition.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))],
    )
    monkeypatch.setattr(acquisition, "PinnedHTTPS", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(ValueError, match="non-public"):
        acquisition.fetch("https://official.example/x", ["official.example"])
