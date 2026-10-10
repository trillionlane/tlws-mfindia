import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

V1 = ROOT / "contracts" / "mfdataindia-openapi-v1.json"
V2 = ROOT / "contracts" / "mfdataindia-openapi-v2.json"


def _v1() -> dict:
    return json.loads(V1.read_text())


def _v2() -> dict:
    return json.loads(V2.read_text())


def test_committed_openapi_contract_is_current_and_complete() -> None:
    subprocess.run([sys.executable, "scripts/export_openapi.py", "--check"], cwd=ROOT, check=True)
    contract = _v2()
    assert contract["info"]["x-contract-id"] == "mfdataindia-json-read-v1".replace("v1", "v2")
    assert contract["info"]["x-contract-version"] == 2
    assert contract["x-mfdata-read-only"] is True
    assert contract["x-mfdata-api-path-count"] == 19
    api_paths = {path for path in contract["paths"] if path.startswith("/api/")}
    assert len(api_paths) == 19
    assert {
        method for path in api_paths for method in contract["paths"][path] if method != "parameters"
    } == {"get"}


def test_v1_inventory_and_read_only_are_unchanged_in_v2() -> None:
    v1, v2 = _v1(), _v2()
    v1_paths = {p for p in v1["paths"] if p.startswith("/api/")}
    v2_paths = {p for p in v2["paths"] if p.startswith("/api/")}
    assert v1_paths == v2_paths  # no endpoint added or removed
    for path in v2_paths:
        methods = {m for m in v2["paths"][path] if m != "parameters"}
        assert methods == {"get"}, f"{path} is no longer GET-only"


def test_no_public_or_authentication_surface_added() -> None:
    v1, v2 = _v1(), _v2()
    assert "securitySchemes" not in v2.get("components", {})
    assert "securitySchemes" not in v1.get("components", {})
    assert not v2.get("security")
    for path, item in v2["paths"].items():
        op = item.get("get", {})
        assert not op.get("security"), f"{path} added operation-level security"


def test_signal_schemas_exist_with_closed_enums() -> None:
    schemas = _v2()["components"]["schemas"]
    for model in ("FundLifecycle", "FundNavFreshness", "FundNavQuality", "FundMethodology"):
        assert model in schemas, f"missing response model {model}"

    def enum_values(schema: dict) -> set[str]:
        """Collect the literal values of a Literal-typed property.

        Multi-value Literals render as enum lists; single-value Literals
        render as ``const``; nullable variants as anyOf of the above.
        """
        s = schema
        while "$ref" in s:
            s = schemas[s["$ref"].split("/")[-1]]
        if "anyOf" in s:
            values = set()
            for sub in s["anyOf"]:
                if "enum" in sub:
                    values |= set(sub["enum"])
                if "const" in sub:
                    values.add(sub["const"])
            return values
        values = set(s.get("enum", []))
        if "const" in s:
            values.add(s["const"])
        return values

    lc = schemas["FundLifecycle"]["properties"]
    assert enum_values(lc["state"]) == {"active", "redeemed", "defunct", "unknown"}
    assert enum_values(lc["evidence"]) == {
        "amfi_current_feed",
        "amfi_redeemed_marker",
        "amfi_defunct_marker",
        "not_seen_in_latest_feed",
        "insufficient_evidence",
    }
    fr = schemas["FundNavFreshness"]["properties"]["status"]
    assert enum_values(fr) == {"current", "delayed", "stale", "very_stale", "missing"}
    q = schemas["FundNavQuality"]["properties"]
    assert enum_values(q["assessment_status"]) == {"current", "stale", "not_assessed"}
    sig = q["signals"]
    if "anyOf" in sig:  # nullable list: pick the array variant
        sig = next(sub for sub in sig["anyOf"] if sub.get("type") == "array")
    sig_items = sig["items"]
    while "$ref" in sig_items:
        sig_items = schemas[sig_items["$ref"].split("/")[-1]]
    assert set(sig_items["enum"]) == {
        "constant_nav_series",
        "duplicate_variant_series",
        "terminal_face_value_reset_candidate",
    }
    m = schemas["FundMethodology"]["properties"]
    assert enum_values(m["basis"]) == {"nav_change"}
    assert enum_values(m["distribution_adjustment"]) == {"not_applicable", "unavailable"}
    lim_items = m["limitations"]["items"]
    assert set(lim_items["enum"]) == {
        "distribution_history_unavailable",
        "lifecycle_not_active",
        "nav_freshness_lag",
        "nav_quality_signal",
        "nav_series_unavailable",
    }


def test_fund_detail_schema_covers_signals_and_v1_fields() -> None:
    detail = _v2()["components"]["schemas"]["FundDetail"]["properties"]
    # v2 signal objects
    for key in ("lifecycle", "nav_freshness", "nav_quality"):
        assert key in detail
    # v1 fields must all survive (byte/semantic compatibility)
    v1_fields = {
        "amfi_scheme_code",
        "scheme_name",
        "plan_type",
        "plan_source",
        "option_type",
        "periodicity",
        "scheme_type",
        "scheme_category",
        "is_etf",
        "is_defunct",
        "is_active",
        "in_scope",
        "isin_growth_or_div_payout",
        "isin_div_reinvest",
        "isin_primary",
        "amfi_amc_name",
        "created_at",
        "updated_at",
        "latest_nav",
        "latest_nav_date",
        "facts",
        "facts_source_code",
        "family",
        "siblings",
        "holdings",
    }
    assert v1_fields <= set(detail), v1_fields - set(detail)
    # no top-level buyability translation
    assert not any("transaction_available" in k or "matured" in k or "buyable" in k for k in detail)


def test_siblings_carry_periodicity() -> None:
    sibling = _v2()["components"]["schemas"]["FundSibling"]["properties"]
    assert "periodicity" in sibling
    assert sibling["periodicity"].get("type") in ("string", None)  # nullable string


def test_ranking_responses_are_explicit_not_generic() -> None:
    schemas = _v2()["components"]["schemas"]
    for model in (
        "MoversResponse",
        "CategoryMoversResponse",
        "PeersResponse",
        "RiskRewardResponse",
        "ReturnsResponse",
        "AnalyticsResponse",
        "CompareResponse",
    ):
        assert model in schemas
    mover = schemas["MoverItem"]["properties"]
    for key in (
        "amfi_scheme_code",
        "scheme_name",
        "option_type",
        "amfi_amc_name",
        "latest_nav",
        "prev_nav",
        "latest_nav_date",
        "prev_nav_date",
        "pct_change",
    ):
        assert key in mover
    peer = schemas["PeerHorizon"]["properties"]
    for key in ("peer_count", "fund_return", "beats_pct", "rank"):
        assert key in peer
    rr = schemas["RiskRewardPoint"]["properties"]
    for key in ("amfi_scheme_code", "scheme_name", "vol", "return", "max_drawdown", "aum", "self"):
        assert key in rr
    comparison = schemas["ComparisonItem"]["properties"]
    for key in ("fund", "points", "returns", "lifecycle", "nav_freshness", "nav_quality", "methodology"):
        assert key in comparison


def test_plan_scope_is_documented_on_every_discovery_route() -> None:
    contract = _v2()
    # Routes that discover or navigate to funds must document the plan scope,
    # so a consumer can see that Direct plans are excluded by default rather
    # than inferring it from an empty result.
    for path in (
        "/api/funds",
        "/api/fund-families",
        "/api/funds/{code}",
        "/api/suggest",
        "/api/movers",
        "/api/movers/categories",
    ):
        parameters = contract["paths"][path]["get"]["parameters"]
        plan = next(item for item in parameters if item["name"] == "plan")
        assert plan["in"] == "query"
        assert plan["required"] is False
        assert plan["schema"]["default"] == "regular"
