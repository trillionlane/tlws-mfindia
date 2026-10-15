"""Policy-bound observed-ISIN overlap, never synthetic economic look-through."""

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
from pathlib import Path

from mfdataindia.enrichment import evidence, portfolios
from mfdataindia.review import policy as canonical_policy
from mfdataindia.enrichment.evidence import valid_isin
from mfdataindia.enrichment.portfolios import decimal
from mfdataindia.review.policy import fingerprint

_POLICY = {
    "id": "reported-isin-weighted-overlap",
    "version": 1,
    "scope": "observed identified nonnegative reported security weights; not full economic overlap, diversification advice or derivative look-through",
    "inputs": "two accepted complete immutable portfolio snapshots, exact same disclosure date, valid current identities; no top-ten or fuzzy name matching",
    "identity": "checksum-valid exact ISIN in equity/debt/government only; duplicate ISIN rows or cross-class matches rejected; issuer names informational",
    "weights": "reported percent of total fund AUM; never normalize, infer from rounded market value, forward fill or force totals to 100",
    "excluded": "cash, margin, current assets/liabilities, unknown/local identifiers and censored weights retained in coverage; never treat censored values as zero",
    "derivatives": "excluded from observed-security calculation, never equate margin with notional; full economic overlap always unavailable in this policy",
    "overlap": "sum over shared eligible ISINs of min(left reported weight, right reported weight), percentage points of original AUM; zero is scoped only",
    "concentration": "per fund sum of largest 5 and 10 eligible ISIN weights; HHI contribution=sum((weight/100)^2); no normalization, partial observed universe only; null metrics if no eligible security",
    "arithmetic": "Decimal precision 80; percentage outputs 8 decimal places HALF_EVEN, HHI 12; sum unrounded contributions before final rounding; no float math",
    "coverage": "eligible reported weight sum is not completeness percentage; negative liabilities and published rounding can produce totals above 100",
    "publication": "immutable policy/input pair/engine hash results, replay exact; old results retained; current reads withhold changed/conflicting/newer inputs, explicit pins permit historical as-of only if current identity still matches",
    "bounds": {"holdings_per_fund": 1000, "pairs_per_operation": 1, "shared_page": 100},
    "source_writes": "none; existing Jaccard and source portfolios unchanged",
}


def policy():
    return deepcopy(_POLICY)


def engine_hash():
    # The decimal validator, ISIN checksum and canonical serializer are part
    # of the executable derivation too. Changes cannot reuse a prior result.
    digest = sha256()
    for path in (
        Path(__file__),
        Path(portfolios.__file__),
        Path(evidence.__file__),
        Path(canonical_policy.__file__),
    ):
        content = path.read_bytes()
        digest.update(path.name.encode() + b"\0" + len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def percent(value):
    return format(value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN), "f")


def project(snapshot):
    if snapshot.get("statement_complete") is not True or not 1 <= len(snapshot["holdings"]) <= 1000:
        raise ValueError("accepted complete bounded portfolio required")
    selected, excluded = {}, []
    for item in snapshot["holdings"]:
        weight = item["weight_percent"]
        value = decimal(weight["value"]) if weight["value"] is not None else None
        reason = None
        if item["asset_class"] not in ("equity", "debt", "government"):
            reason = "non_security_balance"
        elif item["identifier_kind"] != "isin" or not valid_isin(item["identifier"] or ""):
            reason = "unresolved_security_identifier"
        elif weight["basis"] != "reported" or value is None:
            reason = "censored_or_missing_weight"
        elif not 0 <= value <= 100:
            raise ValueError("unsupported negative or leveraged security weight")
        if reason:
            excluded.append(
                {
                    "row": item["row"],
                    "reason": reason,
                    "reported_weight_percent": weight["value"],
                    "upper_bound_exclusive": weight["upper_bound_exclusive"],
                }
            )
            continue
        key = item["identifier"]
        if key in selected:
            raise ValueError("duplicate security ISIN requires a separate policy")
        selected[key] = {"weight": value, "name": item["name"], "asset_class": item["asset_class"]}
    weights = sorted((item["weight"] for item in selected.values()), reverse=True)
    return selected, {
        "code": snapshot["code"],
        "snapshot_id": snapshot["snapshot_id"],
        "holdings_count": len(snapshot["holdings"]),
        "eligible_security_count": len(selected),
        "eligible_reported_weight_percent": percent(sum(weights, Decimal(0))),
        "excluded_rows": excluded,
        "derivative_positions_excluded": len(snapshot["derivative_positions"]),
        "source_unrestricted_overlap_eligible": snapshot["overlap_eligible"],
        "concentration_state": "available" if weights else "unavailable",
        "top5_observed_weight_percent": percent(sum(weights[:5], Decimal(0))) if weights else None,
        "top10_observed_weight_percent": percent(sum(weights[:10], Decimal(0)))
        if weights
        else None,
        "observed_hhi_contribution": format(
            sum(((w / 100) ** 2 for w in weights), Decimal(0)).quantize(
                Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN
            ),
            "f",
        )
        if weights
        else None,
    }


def derive(left, right):
    """Pure deterministic engine; publication validates immutable source seals."""
    if left["disclosure_as_of"] != right["disclosure_as_of"]:
        raise ValueError("exact same-date portfolios required")
    if left["code"] > right["code"]:
        left, right = right, left
    with localcontext() as context:
        context.prec = 80
        context.rounding = ROUND_HALF_EVEN
        a, coverage_a = project(left)
        b, coverage_b = project(right)
        shared, total = [], Decimal(0)
        for key in sorted(a.keys() & b.keys()):
            if a[key]["asset_class"] != b[key]["asset_class"]:
                raise ValueError("shared ISIN asset classes conflict")
            contribution = min(a[key]["weight"], b[key]["weight"])
            total += contribution
            shared.append(
                {
                    "isin": key,
                    "left_name": a[key]["name"],
                    "right_name": b[key]["name"],
                    "asset_class": a[key]["asset_class"],
                    "left_weight_percent": format(a[key]["weight"], "f"),
                    "right_weight_percent": format(b[key]["weight"], "f"),
                    "overlap_contribution_percent": percent(contribution),
                }
            )
        body = {
            "format": "reported-isin-overlap-v1",
            "policy_hash": fingerprint(policy()),
            "engine_code_hash": engine_hash(),
            "left_code": left["code"],
            "right_code": right["code"],
            "left_snapshot_id": left["snapshot_id"],
            "right_snapshot_id": right["snapshot_id"],
            "disclosure_as_of": left["disclosure_as_of"],
            "observed_state": "available" if a and b else "unavailable",
            "observed_reason": None
            if a and b
            else "no_eligible_reported_securities_in_one_or_both_inputs",
            "observed_overlap_percent": percent(total) if a and b else None,
            "full_economic_overlap_percent": None,
            "full_economic_state": "unavailable",
            "full_economic_reason": "policy_excludes_unresolved_censored_non_security_and_derivative_exposures",
            "scope": "observed_reported_isin_securities_only",
            "not_normalized": True,
            "coverage": [coverage_a, coverage_b],
            "shared_security_count": len(shared),
            "shared_holdings": shared,
            "freshness_certified": False,
            "originals_replaced": False,
        }
        return {**body, "result_id": fingerprint(body)}
