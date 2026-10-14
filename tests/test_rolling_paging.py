import base64
import json
from copy import deepcopy

import pytest

from mfdataindia.analytics import rolling, rolling_store as store
from mfdataindia.review import store as review
from test_calendar_analytics import sample
from test_rolling_analytics import series


def visible():
    result = rolling.derive(sample(series()), rolling.policy())
    return {"code": 1, "artifact": {"result_id": result["result_id"], "rolling": result["rolling"]}}


def encoded(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def test_paging_traverses_exact_retained_order_without_mutating_full_artifact():
    source = visible()
    before = deepcopy(source)
    cursor = None
    collected = []
    while True:
        value = store.page(source, limit=1, cursor=cursor)
        collected.extend(value["windows"])
        assert all("windows" not in t for t in value["artifact"]["rolling"]["terms"])
        cursor = value["pagination"]["next_cursor"]
        if not cursor:
            break
    assert collected == source["artifact"]["rolling"]["terms"][0]["windows"]
    assert source == before


def test_cursor_is_not_authorization_to_another_result_fund_term_or_withheld_series():
    source = visible()
    cursor = store.page(source, limit=1)["pagination"]["next_cursor"]
    with pytest.raises(ValueError, match="fund/term"):
        store.page(source, years=3, cursor=cursor)
    changed = deepcopy(source)
    changed["code"] = 2
    with pytest.raises(ValueError, match="fund/term"):
        store.page(changed, cursor=cursor)
    changed = deepcopy(source)
    changed["artifact"]["result_id"] = "a" * 64
    with pytest.raises(review.Conflict, match="restart paging"):
        store.page(changed, cursor=cursor)
    changed = deepcopy(source)
    changed["artifact"]["rolling"] = None
    with pytest.raises(review.Conflict, match="restart paging"):
        store.page(changed, cursor=cursor)


@pytest.mark.parametrize(
    "bad",
    [
        "!",
        "a" * 513,
        encoded([]),
        encoded({"v": 1}),
        encoded({"v": True, "code": 1, "result_id": "a" * 64, "years": 1, "after": "2023-01-31"}),
    ],
)
def test_malformed_oversized_and_ambiguous_cursors_are_rejected(bad):
    with pytest.raises(ValueError):
        store.page(visible(), cursor=bad)


def test_cursor_boundary_must_exist_in_this_artifact():
    source = visible()
    cursor = encoded(
        {
            "v": 1,
            "code": 1,
            "result_id": source["artifact"]["result_id"],
            "years": 1,
            "after": "1900-01-01",
        }
    )
    with pytest.raises(ValueError, match="boundary absent"):
        store.page(source, cursor=cursor)


@pytest.mark.parametrize(
    "arguments", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"years": 2}, {"years": True}]
)
def test_page_budgets_and_terms_are_explicit(arguments):
    with pytest.raises(ValueError, match="term/page bound"):
        store.page(visible(), **arguments)


def test_uncertified_or_ineligible_never_has_an_inferred_window_page():
    value = store.page({"code": 1, "artifact": None}, years=5)
    assert value["windows"] == [] and value["pagination"]["total_windows"] == 0
    assert value["pagination"]["next_cursor"] is None


def test_requested_unavailable_term_never_inherits_another_terms_eligibility():
    source = visible()
    source["current_comparison_eligible"] = True
    assert store.page(source, years=1)["current_comparison_eligible"]
    assert not store.page(source, years=3)["current_comparison_eligible"]
    assert not store.page(source, years=5)["current_comparison_eligible"]
    source["current_comparison_eligible"] = False
    assert not store.page(source, years=1)["current_comparison_eligible"]
