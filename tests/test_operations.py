"""User asked (2026-10-21) for the Operations page's branch/funnel/picker
detail to cover a trailing window of recent days instead of being dropped
to the last complete calendar month -- _trailing_window_bounds() is the new
date math, and the detail_* fields are a new company-wide aggregation over
per-branch rows, both previously untested (no test_operations.py existed
at all before this)."""

from datetime import date, datetime

from backend.app.services import operations


def test_trailing_window_bounds_anchors_to_latest_order_not_wall_clock(monkeypatch):
    monkeypatch.setattr(
        operations.reporting, "run_query",
        lambda query, params=None: [{"as_of": datetime(2026, 10, 20, 8, 54, 57)}],
    )

    start, end, as_of = operations._trailing_window_bounds(30)

    assert as_of == date(2026, 10, 20)
    assert start == "2026-09-21"  # 29 days before as_of -> 30-day inclusive window
    assert end == "2026-10-21"  # exclusive upper bound


def test_trailing_window_bounds_returns_none_when_no_orders(monkeypatch):
    monkeypatch.setattr(operations.reporting, "run_query", lambda query, params=None: [{"as_of": None}])

    start, end, as_of = operations._trailing_window_bounds(30)

    assert (start, end, as_of) == (None, None, None)


# ---------------------------------------------------------------------------
# _merge_picker_pace -- no real item-to-item scan time exists in this
# schema (verified live, 2026-10-21: stock_move_line.date is identical
# across every line of an order); this merges order volume with the derived
# duration/item-count estimate, hiding it below MIN_PACE_ORDERS.

def test_merge_picker_pace_hides_estimate_below_min_orders():
    picker_rows = [{"outlet": "Attidiya", "picker_name": "Sangeetha", "orders": 50}]
    pace_rows = [{
        "outlet": "Attidiya", "picker_name": "Sangeetha",
        "total_duration_s": 900, "total_items": 45, "order_count": operations.MIN_PACE_ORDERS - 1,
    }]

    result = operations._merge_picker_pace(picker_rows, pace_rows)

    assert result[0]["pickers"][0]["avg_seconds_per_item"] is None


def test_merge_picker_pace_computes_aggregate_ratio_not_average_of_ratios():
    picker_rows = [{"outlet": "Attidiya", "picker_name": "Sangeetha", "orders": 50}]
    # Aggregate ratio: 1000s total / 50 items total = 20s/item -- NOT the
    # mean of each order's own seconds-per-item (same reasoning as the
    # stock-count variance rate: sum first, divide once).
    pace_rows = [{
        "outlet": "Attidiya", "picker_name": "Sangeetha",
        "total_duration_s": 1000, "total_items": 50, "order_count": operations.MIN_PACE_ORDERS,
    }]

    result = operations._merge_picker_pace(picker_rows, pace_rows)

    assert result[0]["pickers"][0]["avg_seconds_per_item"] == 20.0


def test_merge_picker_pace_handles_picker_with_no_pace_rows():
    picker_rows = [{"outlet": "Attidiya", "picker_name": "NewHire", "orders": 3}]

    result = operations._merge_picker_pace(picker_rows, pace_rows=[])

    assert result[0]["pickers"][0]["avg_seconds_per_item"] is None
    assert result[0]["pickers"][0]["pace_order_count"] == 0


# ---------------------------------------------------------------------------
# _add_performance_scores -- 2026-11-03: user asked for a single relative
# performance percentage per picker (volume + pace scored against the
# outlet's own best picker that month, not a fixed target).

def _picker(name, orders, avg_seconds_per_item):
    return {"name": name, "orders": orders, "avg_seconds_per_item": avg_seconds_per_item, "pace_order_count": 30}


def test_performance_scores_best_picker_gets_100_on_both_axes():
    groups = [{"outlet": "Attidiya", "pickers": [
        _picker("Fastest", orders=200, avg_seconds_per_item=5.0),
        _picker("Slower", orders=100, avg_seconds_per_item=10.0),
    ]}]

    result = operations._add_performance_scores(groups)

    best = result[0]["pickers"][0]
    assert best["volume_score_pct"] == 100.0
    assert best["pace_score_pct"] == 100.0
    assert best["combined_score_pct"] == 100.0
    assert best["combined_score_basis"] == "volume_and_pace"


def test_performance_scores_halves_the_slower_half_as_expected():
    groups = [{"outlet": "Attidiya", "pickers": [
        _picker("Fastest", orders=200, avg_seconds_per_item=5.0),
        _picker("Slower", orders=100, avg_seconds_per_item=10.0),
    ]}]

    result = operations._add_performance_scores(groups)

    slower = result[0]["pickers"][1]
    assert slower["volume_score_pct"] == 50.0  # 100/200 orders
    assert slower["pace_score_pct"] == 50.0  # best pace 5.0 / own pace 10.0
    assert slower["combined_score_pct"] == 50.0


def test_performance_scores_fall_back_to_volume_only_below_pace_reliability_threshold():
    groups = [{"outlet": "Attidiya", "pickers": [
        _picker("Established", orders=200, avg_seconds_per_item=5.0),
        {"name": "NewHire", "orders": 100, "avg_seconds_per_item": None, "pace_order_count": 5},
    ]}]

    result = operations._add_performance_scores(groups)

    new_hire = result[0]["pickers"][1]
    assert new_hire["pace_score_pct"] is None
    assert new_hire["combined_score_pct"] == 50.0  # volume alone: 100/200
    assert new_hire["combined_score_basis"] == "volume_only"


def test_performance_scores_dont_compare_pace_against_an_unreliable_best():
    # Every picker in the group is below the pace-reliability threshold --
    # there is no trustworthy "best pace" to compare against at all.
    groups = [{"outlet": "Attidiya", "pickers": [
        {"name": "A", "orders": 100, "avg_seconds_per_item": None, "pace_order_count": 5},
        {"name": "B", "orders": 50, "avg_seconds_per_item": None, "pace_order_count": 3},
    ]}]

    result = operations._add_performance_scores(groups)

    assert all(p["pace_score_pct"] is None for p in result[0]["pickers"])
    assert all(p["combined_score_basis"] == "volume_only" for p in result[0]["pickers"])


# ---------------------------------------------------------------------------
# get_picker_summary -- 2026-11-03: user asked for the Picker card to go
# month-wise (1st through its last day) with a month picker, defaulting to
# the latest available data's month, not wall-clock "today" (the reporting
# mirror can lag the real calendar by days -- confirmed live, 2026-10-31).

async def test_picker_summary_defaults_to_latest_orders_month(monkeypatch):
    monkeypatch.setattr(operations.reporting, "run_query", lambda query, params=None: [{"as_of": datetime(2026, 10, 26, 12, 4)}])

    async def fake_fetch_report(sql, params=None):
        return []

    monkeypatch.setattr(operations.reporting, "fetch_report", fake_fetch_report)

    result = await operations.get_picker_summary()

    assert result["month"] == "2026-10"
    assert result["is_current_month"] is True
    assert result["window_label"] == "Oct 2026 (through 26)"


async def test_picker_summary_labels_a_fully_closed_month_without_a_through_date(monkeypatch):
    monkeypatch.setattr(operations.reporting, "run_query", lambda query, params=None: [{"as_of": datetime(2026, 10, 26, 12, 4)}])

    async def fake_fetch_report(sql, params=None):
        return []

    monkeypatch.setattr(operations.reporting, "fetch_report", fake_fetch_report)

    result = await operations.get_picker_summary("2026-09")

    assert result["is_current_month"] is False
    assert result["window_label"] == "Sep 2026"


async def test_picker_summary_returns_empty_when_no_orders_exist(monkeypatch):
    monkeypatch.setattr(operations.reporting, "run_query", lambda query, params=None: [{"as_of": None}])

    result = await operations.get_picker_summary()

    assert result == {"month": None, "window_label": None, "is_current_month": False, "pickers_by_outlet": []}


async def test_picker_summary_wires_picker_rows_through_the_merge(monkeypatch):
    monkeypatch.setattr(operations.reporting, "run_query", lambda query, params=None: [{"as_of": datetime(2026, 10, 26, 12, 4)}])

    async def fake_fetch_report(sql, params=None):
        if "duration_s" in sql or "item_count" in sql:
            return []
        return [{"outlet": "Attidiya", "picker_name": "Sangeetha", "orders": 50}]

    monkeypatch.setattr(operations.reporting, "fetch_report", fake_fetch_report)

    result = await operations.get_picker_summary()

    assert result["pickers_by_outlet"][0]["outlet"] == "Attidiya"
    assert result["pickers_by_outlet"][0]["pickers"][0]["orders"] == 50
