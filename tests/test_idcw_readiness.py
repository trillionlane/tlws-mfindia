"""Synthetic offline diagnostics, not issuer completeness or DEV certification."""

import csv
import io
import json
import runpy
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from mfdataindia.enrichment import idcw_catalog as catalog, idcw_readiness as engine
from mfdataindia.review.policy import fingerprint
from test_idcw_catalog import inputs

ROOT = Path(__file__).resolve().parents[1]
NAV_HEADER = "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date\n\nOpen Ended Schemes ( Equity Scheme - Flexi Cap Fund )\nHDFC Mutual Fund\n"
NAV_LINE = "101763;HDFC Flexi Cap Fund - IDCW Plan;Regular Plan;IDCW Option;INF179K01582;INF179K01590;79.808;05-Mar-2026\n"
SID = [
    {
        "page": 2,
        "text": "HDFC Flexi Cap Fund-SID This Scheme Information Document is dated November 21, 2025.",
    },
    {
        "page": 57,
        "text": "HDFC Flexi Cap Fund-SID IDCW Distribution Procedure The NAV will be adjusted for distributions at the close of business hours on record date.",
    },
]
SAI = [
    {"page": 1, "text": "HDFC Mutual Fund This SAI is dated June 30, 2025."},
    {
        "page": 167,
        "text": "HDFC MF SAI - dated June 30, 2025 STAMP DUTY ON ALLOTMENT/TRANSFER OF UNITS Dividend reinvestment under IDCW Option) would be subject to stamp duty @ 0.005%.",
    },
]


def listing(rows=None):
    if rows is None:
        rows = [
            ["Equity", "HDFC Flexi Cap Fund", engine.DIRECT, "05 Mar, 2026", "6.0000", "6.1000"],
            ["Equity", "HDFC Flexi Cap Fund", engine.REGULAR, "05 Mar, 2026", "7.0000", "7.0000"],
        ]
    s = io.StringIO(newline="")
    w = csv.writer(s)
    w.writerow(engine.HEADERS)
    w.writerows(rows)
    return s.getvalue().encode()


def build(monkeypatch):
    requests, raw, _ = inputs(monkeypatch)
    snap = catalog.derive(requests[0], raw[0])
    sources = {
        "listing": listing(),
        "observation": json.dumps(snap).encode(),
        "nav": (
            NAV_HEADER + NAV_LINE + NAV_LINE.replace("79.808", "71.772").replace("05-Mar", "06-Mar")
        ).encode(),
        "sid": b"%PDF-synthetic-sid",
        "sai": b"%PDF-synthetic-sai",
    }
    context = {
        "format": "hdfc-idcw-query-context-v1",
        "url": engine.LISTING_URL,
        "selected_tab": "IDCW",
        "category": "Equity",
        "scheme": "HDFC Flexi Cap Fund",
        "start": "2025-04-01",
        "end": "2026-03-31",
        "export_sha256": sha256(sources["listing"]).hexdigest(),
        "displayed_rows": [
            [r["scheme"], r["date"], r["individual_inr_per_unit"], r["others_inr_per_unit"]]
            for r in engine.csv_rows(sources["listing"])
        ],
        "pagination": "no_pagination_controls_observed",
        "context_kind": "operator_observed_browser_dom_not_server_response",
    }
    sources["query"] = json.dumps(context).encode()

    def decode(content, pages):
        expected, data = ([2, 57], SID) if content == sources["sid"] else ([1, 167], SAI)
        assert pages == expected
        return deepcopy(data)

    monkeypatch.setattr(engine.decoder, "decode", decode)
    request = {
        "format": "idcw-window-readiness-request-v1",
        "code": 101763,
        "start": "2025-04-01",
        "end": "2026-03-31",
        "expected_snapshot_id": snap["snapshot_id"],
        "observed_at": "2026-10-15T22:00:00Z",
        "listing_url": engine.LISTING_URL,
        "sid_url": engine.SID_URL,
        "sai_url": engine.SAI_URL,
        "nav_start": "2026-03-05",
        "nav_end": "2026-03-06",
    }
    for role in engine.ROLES:
        request[role] = {"file": role + ".source", "sha256": sha256(sources[role]).hexdigest()}
    return request, sources


def rebind(request, sources, role, raw):
    sources[role] = raw
    request[role]["sha256"] = sha256(raw).hexdigest()


def assess(request, sources):
    return engine.assess(engine.ReadinessRequest.model_validate(request), sources)


def test_replay_keeps_known_unknown_and_no_readiness_claim(monkeypatch):
    r, s = build(monkeypatch)
    first = assess(r, s)
    assert first == assess(r, s)
    assert first["report_hash"] == fingerprint(
        {k: v for k, v in first.items() if k != "report_hash"}
    )
    assert first["state"] == "blocked" and first["coverage_state"] == "unknown"
    assert first["listing_reconciliation"] == "corroborated_not_complete"
    assert first["listing_row_count"] == 2 and first["pilot_listing_event_count"] == 1
    for field in (
        "analytics_eligible",
        "readiness_certified",
        "complete_event_window_proven",
        "database_imported",
        "derived_returns_generated",
    ):
        assert first[field] is False
    assert first["source_rows_changed"] == 0
    assert first["record_day_cum_nav_matches"] == ["2026-03-05"]
    assert all(
        v["meaning"] == "reported_nav_not_reinvestment_price"
        for v in first["reported_nav_observations"]
    )
    assert (
        first["issuer_rule_observations"]["reinvestment_stamp_duty_rate_percent_as_reported"]
        == "0.005"
    )
    assert "unit_event_window_unverified" in first["remaining_gaps"]


@pytest.mark.parametrize(
    "change",
    [
        {"code": 118954},
        {"start": "2025-04-02"},
        {"end": "2026-04-01"},
        {"observed_at": "2026-10-15T22:00:00"},
        {"nav_end": "2026-03-20"},
        {"analytics_eligible": True},
        {"complete_event_window_proven": True},
        {"sid_url": engine.SID_URL + "?VersionId=abc,WRITER=true"},
        {"sid_url": engine.SID_URL + "?VersionId=abc&VersionId=def"},
        {"sid_url": engine.SID_URL + "?VersionId=abc%0A"},
        {"sid_url": engine.SID_URL + "#page=57"},
    ],
)
def test_caller_cannot_expand_scope_or_inject_proof(monkeypatch, change):
    r, _ = build(monkeypatch)
    with pytest.raises(ValueError):
        engine.ReadinessRequest.model_validate({**r, **change})


@pytest.mark.parametrize("role", engine.ROLES)
def test_each_original_hash_is_checked(monkeypatch, role):
    r, s = build(monkeypatch)
    s[role] += b"changed"
    with pytest.raises(ValueError, match="size/hash"):
        assess(r, s)


@pytest.mark.parametrize(
    "change",
    [
        {"selected_tab": "NAV"},
        {"start": "2025-03-01"},
        {"scheme": "HDFC Other Fund"},
        {"pagination": "complete"},
        {"context_kind": "verified_complete"},
        {"displayed_rows": []},
        {"export_sha256": "0" * 64},
    ],
)
def test_query_bounds_rows_and_export_binding(monkeypatch, change):
    r, s = build(monkeypatch)
    q = json.loads(s["query"])
    q.update(change)
    rebind(r, s, "query", json.dumps(q).encode())
    with pytest.raises(ValueError):
        assess(r, s)


@pytest.mark.parametrize(
    "old,new",
    [
        ("7.0000", "NaN"),
        ("7.0000", "0"),
        ("7.0000", "-7"),
        ("05 Mar, 2026", "05 März, 2026"),
        ("05 Mar, 2026", "30 Feb, 2026"),
        ("05 Mar, 2026", "05 Mar, 2025"),
        ("Scheme Name", "Scheme"),
        ("HDFC Flexi Cap Fund - Regular Plan", "HDFC Large Cap Fund - Regular Plan"),
    ],
)
def test_unsupported_csv_never_silently_skips_rows(old, new):
    with pytest.raises((ValueError, csv.Error)):
        engine.csv_rows(listing().decode().replace(old, new).encode())


def test_duplicate_csv_and_duplicate_json_keys_rejected():
    raw = listing()
    with pytest.raises(ValueError):
        engine.csv_rows(raw + raw.splitlines(keepends=True)[-1])
    for raw in (b'{"code":1,"code":2}', b'{"n":NaN}'):
        with pytest.raises(ValueError):
            engine.strict_json(raw)


def test_conflict_retains_both_amounts_instead_of_choosing_latest(monkeypatch):
    r, s = build(monkeypatch)
    raw = listing().replace(b"7.0000", b"8.0000")
    rebind(r, s, "listing", raw)
    q = json.loads(s["query"])
    q["export_sha256"] = r["listing"]["sha256"]
    q["displayed_rows"][-1][-2:] = ["8.0000", "8.0000"]
    rebind(r, s, "query", json.dumps(q).encode())
    result = assess(r, s)
    assert result["listing_reconciliation"] == "conflicting"
    assert result["differences"][0]["issuer_listing"]["individual_inr_per_unit"] == "8.0000"
    assert result["differences"][0]["retained_observation"]["amount_inr_per_unit"] == "7.0000"
    assert result["analytics_eligible"] is False


def test_empty_listing_is_unknown_and_conflicts_with_retained_event(monkeypatch):
    r, s = build(monkeypatch)
    rebind(r, s, "listing", listing([]))
    q = json.loads(s["query"])
    q.update(export_sha256=r["listing"]["sha256"], displayed_rows=[])
    rebind(r, s, "query", json.dumps(q).encode())
    result = assess(r, s)
    assert result["pilot_listing_event_count"] == 0 and result["coverage_state"] == "unknown"
    assert "empty_listing_does_not_prove_no_distributions" in result["remaining_gaps"]


@pytest.mark.parametrize(
    "old,new",
    [
        ("INF179K01590", "INF179K01VM3"),
        ("Regular Plan", "Direct Plan"),
        ("79.808", "0"),
        ("HDFC Mutual Fund", "Other Mutual Fund"),
        ("05-Mar-2026", "05-Mar-2025"),
        ("101763", "118954"),
    ],
)
def test_nav_identity_and_date_validation(monkeypatch, old, new):
    r, s = build(monkeypatch)
    rebind(r, s, "nav", s["nav"].decode().replace(old, new).encode())
    with pytest.raises(ValueError):
        assess(r, s)


def test_duplicate_nav_and_changed_snapshot_seal_are_rejected(monkeypatch):
    r, s = build(monkeypatch)
    rebind(r, s, "nav", s["nav"] + NAV_LINE.encode())
    with pytest.raises(ValueError):
        assess(r, s)
    r, s = build(monkeypatch)
    doc = json.loads(s["observation"])
    doc["events"][0]["amount_inr_per_unit"] = "9.0000"
    rebind(r, s, "observation", json.dumps(doc).encode())
    with pytest.raises(ValueError):
        assess(r, s)


def test_documents_require_actual_pages_and_rule_anchors():
    for sid, sai in (
        ([{**SID[0], "page": 1}, SID[1]], SAI),
        (SID, [SAI[0], {**SAI[1], "text": SAI[1]["text"].replace("0.005%", "0.05%")}]),
        ([SID[0], {**SID[1], "text": "another rule"}], SAI),
    ):
        with pytest.raises(ValueError):
            engine.document_rules(sid, sai)


def test_cli_blocked_exit_permissions_exclusive_output_and_no_source_changes(monkeypatch, tmp_path):
    r, s = build(monkeypatch)
    tmp_path.chmod(0o700)
    for role, raw in s.items():
        (tmp_path / r[role]["file"]).write_bytes(raw)
    manifest = tmp_path / "request.json"
    manifest.write_text(json.dumps(r))
    output = tmp_path / "report.json"
    run = runpy.run_path(str(ROOT / "scripts/check_idcw_window_readiness.py"))["run"]
    before = {p.name: sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    assert run(manifest, tmp_path, output) == 2
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        run(manifest, tmp_path, output)
    assert all(
        sha256((tmp_path / name).read_bytes()).hexdigest() == h for name, h in before.items()
    )
    linked = tmp_path / "linked.json"
    linked.symlink_to(manifest)
    with pytest.raises(ValueError):
        run(linked, tmp_path, tmp_path / "other.json")


def test_cli_rejects_source_links_and_oversized_files(tmp_path):
    reader = runpy.run_path(str(ROOT / "scripts/check_idcw_window_readiness.py"))["read_regular"]
    p = tmp_path / "data"
    p.write_bytes(b"original")
    q = tmp_path / "link"
    q.symlink_to(p)
    with pytest.raises(ValueError):
        reader(q, 65536)
    with pytest.raises(ValueError):
        reader(p, 3)
    assert reader(p, 8) == b"original"
