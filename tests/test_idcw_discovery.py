"""Hermetic source-gate tests. Synthetic PDFs are NOT issuer/DEV certification."""
import io
import json
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from mfdataindia.enrichment import idcw_discovery as engine
from mfdataindia.enrichment import pdf_evidence

IDENTITY = {"code": 101763, "amc": "HDFC Mutual Fund", "scheme_name": engine.NAME,
            "plan": "REGULAR", "option": "IDCW", "isins": ["INF179K01582", "INF179K01590"]}
REGISTRY = """1 Fund Name HDFC Flexi Cap Fund 2 Option Names fixture
28 ISINs HDFC FLEXI CAP FUND - DIVIDEND PAYOUT OPTION- INF179K01582
HDFC FLEXI CAP FUND - DIVIDEND REINVESTMENT OPTION- INF179K01590
29 AMFI Codes (To be phased out) 101763 -HDFC Flexi Cap Fund - IDCW Plan
118954 -HDFC Flexi Cap Fund - IDCW Option - Direct Plan
101762 -HDFC Flexi Cap Fund- Growth Plan
118955 -HDFC Flexi Cap Fund - Growth Option - Direct Plan
30 SEBI Codes fixture
"""
HISTORY = """121 | April 2026
HDFC FLEXI CAP FUND (Past 3 years) IDCW Option
Record Date Regular Plan Direct Plan
Cum IDCW NAV(₹) IDCW per Unit(₹) For Cum IDCW NAV(₹) IDCW per Unit(₹) For
Individual & HUF Others Individual & HUF Others
Mar 14, 24 76.341 7.000 7.000 88.436 7.000 7.000
Mar 13, 25 76.3730 7.0000 7.0000 90.2910 7.0000 7.0000
Mar 05, 26 79.8080 7.0000 7.0000 96.4400 7.0000 7.0000
HDFC LARGE CAP FUND (Past 3 years) IDCW Option
Mar 05, 26 59.6610 5.5000 5.5000 70.5040 5.5000 5.5000
"""
NOTICE = """fixed Thursday, March 05, 2026 (or the immediately following Business Day,
if that day is not a Business Day) as the Record Date
Amount of Distribution (` per unit)#
February 27, 2026 (` per unit) Face Value (` per unit)
HDFC Flexi Cap Fund - Regular Plan - IDCW Option (Payout and Reinvestment) 7.00 81.663
10.00 HDFC Flexi Cap Fund - Direct Plan - fixture
lower of that mentioned above or the available distributable surplus
NAV of the IDCW Option(s) fixture statutory levy, if any
net of applicable TDS fixture applicable NAV per Unit (adjusted for applicable stamp duty)
Date : March 01, 2026 Authorized Signatory
"""


def request(**changes):
    sources = {role: {"file": role + ".pdf", "source_url": "https://files.hdfcfund.com/s3fs-public/" + role + ".pdf",
                      "content_hash": c * 64, "observed_at": "2026-10-15T10:00:00Z",
                      "pages": [1, 121] if role == "history" else [1]}
               for role, c in (("history", "a"), ("notice", "b"), ("registry", "c"))}
    return engine.IdcwDiscoveryRequest.model_validate({
        "format": "idcw-source-discovery-request-v1", "identity": IDENTITY,
        "proposed_start": "2025-04-01", "proposed_end": "2026-03-31", "history_period": "2026-04",
        "use_basis": "operator_reported_authorization", "authority_reference": "synthetic fixture only",
        **sources, **changes,
    })


def pages():
    return {"history": [{"page": 1, "text": "April 30, 2026"}, {"page": 121, "text": HISTORY}],
            "notice": [{"page": 1, "text": NOTICE}], "registry": [{"page": 1, "text": REGISTRY}]}


def test_report_is_blocked_not_product_completion_or_adjusted_analytics():
    value = engine.reconcile(request(), pages())
    assert value == engine.reconcile(request(), pages())
    assert value["state"] == "blocked" and value["coverage_state"] == "unknown"
    assert value["mf_e09_complete"] is False and value["analytics_eligible"] is False
    assert value["complete_event_window_proven"] is False
    assert value["accepted_event_count"] == value["source_rows_changed"] == 0
    assert value["universe_complete"] is value["freshness_certified"] is False
    assert value["proposed_window_reported_event_count"] == 1
    assert len(value["reported_history"]) == 3
    event = value["reported_history"][-1]
    assert event["amount_inr_per_unit"] == "7.0000"
    assert event["cum_idcw_nav_inr"] == "79.8080"
    assert event["reinvestment_nav_inr"] is event["payment_date"] is event["ex_date"] is None
    assert event["scheme_levy_inr_per_unit"] is None
    notice = value["conditional_announcement"]
    assert notice["pre_notice_nav_inr"] == "81.663"
    assert notice["amount_is_conditional"] and notice["reinvestment_nav_inr"] is None
    assert value["identity_evidence"]["current_database_identity_verified"] is False


def test_empty_proposed_window_is_unknown_not_no_payout():
    value = engine.reconcile(request(proposed_start="2026-04-01", proposed_end="2026-04-30"), pages())
    assert value["proposed_window_reported_event_count"] == 0
    assert value["coverage_state"] == "unknown" and not value["analytics_eligible"]


@pytest.mark.parametrize("field,replacement", [
    ("code", 118954), ("option", "GROWTH"), ("plan", "DIRECT"), ("amc", "Other AMC"),
    ("scheme_name", engine.NAME + " II"), ("isins", ["INF179K01582"]),
    ("isins", ["INF179K01583", "INF179K01590"]),
    ("isins", ["INF179K01582", "INF179K01590", "INF179K01582"]),
])
def test_wrong_identity_never_maps(field, replacement):
    with pytest.raises(ValueError):
        value = request(identity={**IDENTITY, field: replacement})
        engine.reconcile(value, pages())


@pytest.mark.parametrize("old,new", [
    ("Regular Plan Direct Plan", "Direct Plan Regular Plan"),
    ("7.0000 7.0000 96.4400", "7.0000 6.0000 96.4400"),
    ("Mar 05, 26", "Feb 30, 26"), ("Mar 05, 26", "Mar 05, 27"),
    ("79.8080 7.0000", "79.8080 NaN"), ("79.8080 7.0000", "79.8080 -7.0000"),
    ("IDCW per Unit(₹)", "IDCW (%)"), ("76.341", "unreadable"),
    ("April 2026", "April 2025"),
])
def test_ambiguous_or_unparsed_history_rejected(old, new):
    with pytest.raises(ValueError):
        engine.history_rows(HISTORY.replace(old, new), period="2026-04")


def test_duplicate_events_and_duplicate_identity_anchors_rejected():
    with pytest.raises(ValueError):
        engine.history_rows(HISTORY.replace("Mar 13, 25", "Mar 14, 24"), period="2026-04")
    with pytest.raises(ValueError):
        engine.registry_identity(IDENTITY, REGISTRY.replace("101762", "101763"))
    with pytest.raises(ValueError):
        engine.registry_identity(IDENTITY, REGISTRY + REGISTRY)


def test_conditional_maximum_is_not_an_actual_amount():
    value = pages()
    value["notice"][0]["text"] = NOTICE.replace("7.00 81.663", "8.00 81.663")
    report = engine.reconcile(request(), value)
    assert report["announcement_consistent_with_history"] is True
    assert report["reported_history"][-1]["amount_inr_per_unit"] == "7.0000"
    value["notice"][0]["text"] = NOTICE.replace("7.00 81.663", "6.00 81.663")
    report = engine.reconcile(request(), value)
    assert report["announcement_consistent_with_history"] is False
    assert report["blocking_reasons"][0] == "history_exceeds_conditional_announcement"
    assert report["conditional_announcement"]["announced_maximum_inr_per_unit"] == "6.00"


@pytest.mark.parametrize("change", [
    {"proposed_start": "2027-01-01"}, {"proposed_end": "2027-01-01"},
    {"analytics_eligible": True}, {"complete_event_window_proven": True},
    {"authority_reference": "bad\nreference"},
])
def test_manifest_cannot_assert_completeness_or_acceptance(change):
    with pytest.raises(ValueError):
        request(**change)


@pytest.mark.parametrize("url", [
    "https://evil.example/s3fs-public/notice.pdf", "https://files.hdfcfund.com:443/s3fs-public/a.pdf",
    "https://files.hdfcfund.com/s3fs-public/a.pdf?key=value",
    "https://files.hdfcfund.com/s3fs-public/a,WRITE=true.pdf",
    "https://files.hdfcfund.com/s3fs-public/%252e%252e/a.pdf",
    "https://files.hdfcfund.com/s3fs-public/%2e%2e/a.pdf",
    "https://files.hdfcfund.com/s3fs-public/a%0a.pdf",
])
def test_provenance_metadata_rejects_unsafe_urls(url):
    body = request().model_dump(mode="json")
    body["notice"]["source_url"] = url
    with pytest.raises(ValueError):
        engine.IdcwDiscoveryRequest.model_validate(body)


def test_literal_commas_in_issuer_filename_are_metadata_not_env_assignments():
    body = request().model_dump(mode="json")
    body["notice"]["source_url"] = "https://files.hdfcfund.com/s3fs-public/Flexi,%20Small.pdf"
    assert engine.IdcwDiscoveryRequest.model_validate(body).notice.source_url.endswith("Small.pdf")


def pdf(text="Synthetic source fixture", encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 12 Tf 10 10 Td (" + text + ") Tj ET").encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("fixture only")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_real_isolated_worker_and_separate_immutable_pdf_policy():
    before = deepcopy(pdf_evidence.policy())
    assert engine.decode(pdf(), [1])[0]["text"] == "Synthetic source fixture"
    assert pdf_evidence.policy() == before and before["maximum_source_bytes"] == 8 * 1024 * 1024
    assert engine.policy()["maximum_source_bytes"] == 16 * 1024 * 1024
    changed = engine.policy()
    changed["publication"] = "accepted"
    assert engine.policy()["publication"] == "discovery_only_not_accepted_events"
    assert len(engine.code_hash()) == 64


@pytest.mark.parametrize("data,numbers", [(b"not pdf", [1]), (pdf(encrypted=True), [1]),
    (pdf(), [2]), (pdf(), [True]), (pdf(), [1, 1]), (pdf(), [0]), (pdf(), [1, 2, 3])])
def test_decoder_rejects_invalid_or_unbounded_inputs(data, numbers):
    with pytest.raises(ValueError):
        engine.decode(data, numbers)


def test_timeout_and_source_substitution_fail_closed(monkeypatch):
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("fixture", 30)
    monkeypatch.setattr(engine.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="deadline"):
        engine.decode(pdf(), [1])
    with pytest.raises(ValueError, match="hash"):
        engine.discover(request(), {"history": pdf()})


def test_lineage_and_report_hash_include_exact_sources_request_and_code(monkeypatch):
    import hashlib
    body = request().model_dump(mode="json")
    contents = {role: pdf(role) for role in ("history", "notice", "registry")}
    for role, data in contents.items():
        body[role]["content_hash"] = hashlib.sha256(data).hexdigest()
    texts = pages()
    monkeypatch.setattr(engine, "decode", lambda data, numbers: texts[next(role for role, content in contents.items() if content == data)])
    value = engine.discover(engine.IdcwDiscoveryRequest.model_validate(body), contents)
    assert value["report_hash"] == engine.fingerprint({k: v for k, v in value.items() if k != "report_hash"})
    assert value["source_lineage"]["history"]["content_hash"] == body["history"]["content_hash"]
    assert value["engine_code_hash"] == engine.code_hash()


def test_cli_is_offline_does_not_overwrite_or_publish_and_rejects_links(monkeypatch, tmp_path):
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "scripts/discover_idcw_evidence.py"
    spec = importlib.util.spec_from_file_location("idcw_discovery_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    manifest = tmp_path / "request.json"
    manifest.write_text(request().model_dump_json())
    for role in ("history", "notice", "registry"):
        (tmp_path / (role + ".pdf")).write_bytes(pdf(role))
    monkeypatch.setattr(cli, "discover", lambda req, sources: {"state": "blocked"})
    output = tmp_path / "report.json"
    assert cli.run(manifest, tmp_path, output) == 2
    assert json.loads(output.read_bytes()) == {"state": "blocked"}
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        cli.run(manifest, tmp_path, output)
    (tmp_path / "history.pdf").unlink()
    (tmp_path / "history.pdf").symlink_to(tmp_path / "notice.pdf")
    with pytest.raises(ValueError, match="no links"):
        cli.run(manifest, tmp_path, tmp_path / "must-not-exist.json")
    assert not (tmp_path / "must-not-exist.json").exists()
