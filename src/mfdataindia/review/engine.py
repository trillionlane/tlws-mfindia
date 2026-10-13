"""Seven deterministic metrics; results are new versions, not legacy replacements."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from mfdataindia.review.policy import decimal_text, validate_policy

METRICS = ("cumulative_return_pct", "cagr_pct", "annual_volatility_pct", "sharpe",
           "sortino", "max_drawdown_pct", "calmar")


def calculate(snapshot: dict, document: dict) -> dict:
    validate_policy(document)
    metrics = {name: None for name in METRICS}
    reasons = {name: "insufficient_observations" for name in METRICS}
    observations = snapshot["observations"]
    if snapshot["identity"]["option_type"] != "GROWTH":
        return {"metrics": metrics, "unavailable": dict.fromkeys(METRICS, "unsupported_option")}
    if len(observations) < 2:
        return {"metrics": metrics, "unavailable": reasons}
    days = [date.fromisoformat(row["date"]) for row in observations]
    # Non-finite strings are handled as unavailable; floats are not source truth.
    if any(isinstance(row["nav"], (float, bool)) for row in observations):
        raise ValueError("exact NAV text required")
    navs = [Decimal(row["nav"]) for row in observations]
    if any(not n.is_finite() or n <= 0 for n in navs):
        return {"metrics": metrics, "unavailable": dict.fromkeys(METRICS, "invalid_nav")}
    for nav in navs:
        decimal_text(nav)
    if any(b <= a for a, b in zip(days, days[1:])):
        raise ValueError("observations must be unique and strictly date ordered")
    with localcontext() as context:
        context.prec = document["precision"]
        context.rounding = ROUND_HALF_EVEN
        quantum = Decimal("0.00000001")

        def put(name: str, value: Decimal) -> None:
            if not value.is_finite():
                raise ValueError("non-finite calculated result")
            rounded = value.quantize(quantum)
            metrics[name] = format(abs(rounded) if rounded == 0 else rounded, "f")
            reasons.pop(name, None)

        put("cumulative_return_pct", (navs[-1] / navs[0] - 1) * 100)
        peak, drawdown = navs[0], Decimal(0)
        for nav in navs:
            peak = max(peak, nav)
            drawdown = min(drawdown, nav / peak - 1)
        put("max_drawdown_pct", drawdown * 100)
        cagr = None
        elapsed = (days[-1] - days[0]).days
        if elapsed >= document["minimum_cagr_elapsed_days"]:
            cagr = ((navs[-1] / navs[0]).ln() * Decimal("365.25") / elapsed).exp() - 1
            put("cagr_pct", cagr * 100)
            if drawdown < 0:
                put("calmar", cagr / abs(drawdown))
            else:
                reasons["calmar"] = "zero_drawdown"
        else:
            reasons["cagr_pct"] = reasons["calmar"] = "less_than_one_calendar_year"
        gap = any((b - a).days > document["max_observation_gap_calendar_days"]
                  for a, b in zip(days, days[1:]))
        weekend = any(day.weekday() >= 5 for day in days)
        if gap or weekend or len(navs) < document["minimum_risk_observations"]:
            reason = "large_observation_gap" if gap else "non_business_observation" if weekend else "insufficient_risk_observations"
            for name in ("annual_volatility_pct", "sharpe", "sortino"):
                reasons[name] = reason
        else:
            returns = [b / a - 1 for a, b in zip(navs, navs[1:])]
            periods = Decimal(document["annualization_periods"])
            rf = ((1 + Decimal(document["risk_free_annual_effective"])).ln() / periods).exp() - 1
            excess = [r - rf for r in returns]
            mean = sum(excess) / len(excess)
            variance = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
            stdev = variance.sqrt()
            put("annual_volatility_pct", stdev * periods.sqrt() * 100)
            if stdev:
                put("sharpe", mean / stdev * periods.sqrt())
            else:
                reasons["sharpe"] = "zero_volatility"
            downside = (sum(min(r, Decimal(0)) ** 2 for r in excess) / len(excess)).sqrt()
            if downside:
                put("sortino", mean / downside * periods.sqrt())
            else:
                reasons["sortino"] = "zero_downside_deviation"
    return {"metrics": metrics, "unavailable": reasons}
