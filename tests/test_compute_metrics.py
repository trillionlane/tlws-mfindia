"""Unit tests for the computed-metrics math (jobs.compute_metrics).

The pure functions must reproduce the API's on-the-fly conventions
(api.queries: full-history log returns, *252 / *sqrt(252), rf 6.5%,
30.44-day months), so a stored value equals the on-the-fly value.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from mfdataindia.jobs.compute_metrics import (
    RISK_FREE_ANNUAL,
    annualize,
    compute_for_series,
    log_returns,
    one_day_return_pct,
    sharpe_of,
    since_launch_return_pct,
    trailing_return_pct,
)


def _series(days: int, start: float = 10.0, step: float = 0.01) -> tuple[list[date], list[float]]:
    d0 = date(2024, 1, 1)
    return [d0 + timedelta(days=i) for i in range(days)], \
           [start + i * step for i in range(days)]


class TestLogReturns:
    def test_basic(self):
        assert log_returns([2.0, 4.0, 8.0]) == [math.log(2.0), math.log(2.0)]

    def test_skips_non_positive(self):
        assert log_returns([1.0, 0.0, 2.0]) == []  # both pairs touch a 0


class TestAnnualize:
    def test_known_values(self):
        rets = [0.01, -0.01, 0.02, -0.005]
        ann_ret, ann_vol = annualize(rets)
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        assert ann_ret == mean * 252
        assert ann_vol == math.sqrt(var) * math.sqrt(252)

    def test_constant_returns_zero_vol(self):
        ann_ret, ann_vol = annualize([0.001] * 10)
        assert ann_ret == 0.001 * 252
        assert ann_vol == 0.0


class TestSharpe:
    def test_matches_api_formula(self):
        ann_ret, ann_vol = 0.12, 0.15
        assert sharpe_of(ann_ret, ann_vol) == (ann_ret - RISK_FREE_ANNUAL) / ann_vol

    def test_zero_vol_is_none(self):
        assert sharpe_of(0.1, 0.0) is None


class TestTrailingReturn:
    def test_base_is_at_or_before_cutoff(self):
        dates, navs = _series(400)
        got = trailing_return_pct(dates, navs, 1)  # 30 days back
        # Base: latest NAV <= last_date - 30d. In a daily series that is index len-31.
        base_i = len(dates) - 31
        expected = (navs[-1] / navs[base_i] - 1) * 100
        assert got == expected

    def test_young_series_is_none(self):
        dates, navs = _series(10)
        assert trailing_return_pct(dates, navs, 1) is None

    def test_uses_latest_nav_on_or_before_cutoff(self):
        # Gap in the series: base must be the latest available NAV <= cutoff.
        # Cutoff for 1M is 30d before day 60 -> day 30; latest NAV on/before it
        # is day 29 (the day-50 NAV is *after* the cutoff and must be skipped).
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in (0, 1, 2, 29, 50, 60)]
        navs = [10.0, 10.1, 10.2, 11.0, 11.2, 12.0]
        got = trailing_return_pct(dates, navs, 1)
        assert got == (12.0 / 11.0 - 1) * 100


class TestOneDayReturn:
    def test_basic(self):
        import pytest
        assert one_day_return_pct([10.0, 11.0]) == pytest.approx(10.0)

    def test_too_short(self):
        assert one_day_return_pct([10.0]) is None


class TestSinceLaunch:
    def test_inception_inside_data(self):
        dates, navs = _series(100)
        assert since_launch_return_pct(dates, navs, dates[0]) == (navs[-1] / navs[0] - 1) * 100

    def test_inception_before_data_is_none(self):
        dates, navs = _series(100)
        assert since_launch_return_pct(dates, navs, dates[0] - timedelta(days=1)) is None

    def test_unknown_inception_is_none(self):
        dates, navs = _series(100)
        assert since_launch_return_pct(dates, navs, None) is None


class TestComputeForSeries:
    def test_all_targets_present(self):
        from mfdataindia.jobs.compute_metrics import TARGET_FIELDS
        dates, navs = _series(400)
        out = compute_for_series(dates, navs, dates[0])
        assert set(out) == set(TARGET_FIELDS)

    def test_rounding_two_dp(self):
        dates, navs = _series(400)
        out = compute_for_series(dates, navs, dates[0])
        for f, v in out.items():
            if v is not None:
                assert round(v, 2) == v

    def test_young_fund_long_horizons_none(self):
        dates, navs = _series(200)  # < 5 months
        out = compute_for_series(dates, navs, dates[0])
        assert out["return_5year"] is None
        assert out["return_1month"] is not None