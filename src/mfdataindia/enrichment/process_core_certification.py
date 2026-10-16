"""Read-only full core-route contract/methodology witness; fixed private origin."""

from mfdataindia.api import schemas
from mfdataindia.enrichment.current_facts_certification import CORE_ORIGIN
from mfdataindia.review.certification import metadata_identity

MODELS = {
    "/api/funds/{code}": schemas.FundDetail,
    "/api/funds/{code}/returns": schemas.ReturnsResponse,
    "/api/funds/{code}/analytics": schemas.AnalyticsResponse,
    "/api/funds/{code}/peers": schemas.PeersResponse,
    "/api/funds/{code}/risk-reward": schemas.RiskRewardResponse,
    "/api/movers": schemas.MoversResponse,
    "/api/movers/categories": schemas.CategoryMoversResponse,
    "/api/compare": schemas.CompareResponse,
}


def cases():
    return [
        ("/api/health", "/api/health"),
        ("/api/stats", "/api/stats"),
        ("/api/funds", "/api/funds?per_page=2&live=false&in_scope=false"),
        ("/api/fund-families", "/api/fund-families?per_page=2&live=false"),
        ("/api/funds/batch", "/api/funds/batch?ids=103174,103173"),
        ("/api/funds/{code}", "/api/funds/103174"),
        ("/api/funds/{code}/nav", "/api/funds/103174/nav?years=1"),
        ("/api/funds/{code}/returns", "/api/funds/103174/returns"),
        ("/api/funds/{code}/analytics", "/api/funds/103174/analytics"),
        ("/api/funds/{code}/peers", "/api/funds/103174/peers"),
        ("/api/funds/{code}/risk-reward", "/api/funds/103174/risk-reward"),
        ("/api/holdings-overlap", "/api/holdings-overlap?codes=103174,103173"),
        ("/api/amcs", "/api/amcs"),
        ("/api/categories", "/api/categories"),
        ("/api/options", "/api/options"),
        ("/api/suggest", "/api/suggest?q=Large%20Cap&limit=2"),
        ("/api/movers", "/api/movers?limit=2"),
        ("/api/movers/categories", "/api/movers/categories?limit=1"),
        ("/api/compare", "/api/compare?codes=103174,103173&years=1"),
    ]


def signals(document):
    count = nulls = 0
    if isinstance(document, list):
        for value in document:
            c, n = signals(value)
            count += c
            nulls += n
    elif isinstance(document, dict):
        if "nav_quality" in document:
            quality = document["nav_quality"]
            if not all(key in document for key in ("lifecycle", "nav_freshness")):
                raise ValueError("explicit v2 lifecycle/freshness required")
            if not isinstance(quality, dict) or "signals" not in quality:
                raise ValueError("omitted signals is not observed null")
            schemas.FundNavQuality.model_validate(quality)
            schemas.FundLifecycle.model_validate(document["lifecycle"])
            schemas.FundNavFreshness.model_validate(document["nav_freshness"])
            if "methodology" in document:
                schemas.FundMethodology.model_validate(document["methodology"])
            if quality["signals"] is None:
                if quality["assessment_status"] != "not_assessed":
                    raise ValueError("null signals requires explicit not_assessed")
                nulls += 1
            elif quality["assessment_status"] == "not_assessed":
                raise ValueError("unassessed signals are unknown, not an empty clean array")
            count += 1
        for value in document.values():
            c, n = signals(value)
            count += c
            nulls += n
    return count, nulls


def certify(session, *, unassessed_code, option_lookup):
    if type(unassessed_code) is not int or not 0 < unassessed_code <= 2147483647:
        raise ValueError("actual unassessed database sample required; never fabricate one")
    token = metadata_identity(session, CORE_ORIGIN)
    headers = {"Authorization": "Bearer " + token}
    documents = {}
    quality_objects = null_observations = 0

    def read(template, path):
        nonlocal quality_objects, null_observations
        response = session.get(
            CORE_ORIGIN + path, headers=headers, timeout=30, allow_redirects=False
        )
        if response.status_code != 200 or response.headers.get("Cache-Control") != "no-store":
            raise ValueError("private core route status/cache contract differs")
        body = response.json()
        if template in MODELS:
            MODELS[template].model_validate(body)
        elif template in ("/api/amcs", "/api/categories", "/api/options", "/api/suggest"):
            if not isinstance(body, list):
                raise ValueError("core list response shape differs")
        elif not isinstance(body, dict):
            raise ValueError("core object response shape differs")
        count, nulls = signals(body)
        quality_objects += count
        null_observations += nulls
        return body

    for template, path in cases():
        documents[template] = read(template, path)
    health = documents["/api/health"]
    if health.get("ok") is not True or not str(health.get("db", "")).startswith("PostgreSQL 18."):
        raise ValueError("actual provider database health unavailable")
    stats = documents["/api/stats"]
    if any(
        type(stats.get(key)) is not int or stats[key] <= 0
        for key in ("schemes_total", "nav_rows", "dataset_version")
    ):
        raise ValueError("actual positive source inventory required")
    for route in ("/api/funds", "/api/fund-families"):
        body = documents[route]
        if not isinstance(body.get("results"), list) or type(body.get("total")) is not int:
            raise ValueError("core discovery result shape differs")
    batch = documents["/api/funds/batch"]
    if batch.get("found") != 2 or [r.get("amfi_scheme_code") for r in batch.get("funds", [])] != [
        103174,
        103173,
    ]:
        raise ValueError("both exact batch identities required")
    nav = documents["/api/funds/{code}/nav"]
    if nav.get("code") != 103174 or not isinstance(nav.get("points"), list):
        raise ValueError("core NAV identity/shape differs")
    overlap = documents["/api/holdings-overlap"]
    if overlap.get("codes") != [103174, 103173] or not isinstance(overlap.get("pairs"), list):
        raise ValueError("legacy top-holdings overlap shape differs")
    growth = documents["/api/funds/{code}"]
    if growth.get("amfi_scheme_code") != 103174 or growth.get("option_type") != "GROWTH":
        raise ValueError("primary detail/returns/analytics sample must be Growth")
    idcw = read("/api/funds/{code}", "/api/funds/103173")
    idcw_returns = read("/api/funds/{code}/returns", "/api/funds/103173/returns")
    if (
        idcw.get("option_type") != "IDCW"
        or idcw_returns["code"] != 103173
        or idcw_returns["methodology"]["comparison_eligible"]
    ):
        raise ValueError("IDCW cannot silently acquire total-return comparison eligibility")
    if idcw_returns["methodology"]["distribution_adjustment"] != "unavailable":
        raise ValueError("unadjusted IDCW distribution evidence cannot be overclaimed")
    unassessed = read("/api/funds/{code}", f"/api/funds/{unassessed_code}")
    if (
        unassessed["nav_quality"].get("assessment_status") != "not_assessed"
        or "signals" not in unassessed["nav_quality"]
        or unassessed["nav_quality"]["signals"] is not None
    ):
        raise ValueError("actual explicitly unassessed/null sample not observed")
    compare = documents["/api/compare"]["funds"]
    if [row["fund"]["amfi_scheme_code"] for row in compare] != [103174, 103173] or compare[1][
        "methodology"
    ]["comparison_eligible"]:
        raise ValueError("comparison identity/order/IDCW eligibility differs")
    movers = documents["/api/movers"]["results"]
    categories = documents["/api/movers/categories"]["categories"]
    category_members = [
        row for group in categories for key in ("gainers", "losers") for row in group[key]
    ]
    if any(row.get("option_type") != "GROWTH" for row in [*movers, *category_members]):
        raise ValueError("IDCW/non-Growth entered a movers population")
    risk_ids = [
        row["amfi_scheme_code"] for row in documents["/api/funds/{code}/risk-reward"]["points"]
    ]
    if len(risk_ids) > 1000:
        raise ValueError("risk population exceeds bounded certification cohort")
    options = option_lookup(risk_ids) if risk_ids else {}
    if set(options) != set(risk_ids) or any(value != "GROWTH" for value in options.values()):
        raise ValueError("risk population includes non-Growth or unknown identity")
    return {
        "core_origin": CORE_ORIGIN,
        "read_route_templates_checked": sorted(documents),
        "routes_checked": len(documents),
        "responses_checked": len(cases()) + 3,
        "explicit_v2_signal_objects_checked": quality_objects,
        "explicit_null_observations": null_observations,
        "actual_unassessed_code": unassessed_code,
        "primary_growth_code": 103174,
        "idcw_comparison_eligible": False,
        "risk_population_members_checked": len(risk_ids),
        "movers_members_checked": len(movers) + len(category_members),
        "growth_comparison_eligible_observed": documents["/api/funds/{code}/returns"][
            "methodology"
        ]["comparison_eligible"],
        "eligibility_is_not_universe_completeness": True,
        "source_rows_changed": 0,
        "read_only": True,
    }
