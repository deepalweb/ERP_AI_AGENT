"""Phase 5A regression coverage: sales.period_completeness() was extracted
out of sales.get_sales_summary() (the partial-period-detection logic itself
was NOT changed) so channels.get_channel_breakdown() could stop calling the
whole summary just to reach it. No test protected this branching logic
before -- these lock in the exact behavior for the 3 real cases
(fully-elapsed month, still-partial month, month not yet started) plus the
two zero-query paths (rolling window, custom range), and confirm
get_sales_summary() and get_channel_breakdown() still agree with it.
"""
from datetime import date
from unittest.mock import patch

from mcp_server.tools import sales
from backend.app.services import channels


def _fake_latest_order_date(d: date):
    return patch.object(sales, "_latest_order_date", return_value=d)


def test_a_fully_elapsed_month_is_not_partial():
    with _fake_latest_order_date(date(2026, 9, 30)):
        is_partial, elapsed, in_period = sales.period_completeness("2026-08", "2026-08-01")
    assert (is_partial, elapsed, in_period) == (False, 31, 31)


def test_a_still_in_progress_month_is_partial_with_the_real_elapsed_day_count():
    with _fake_latest_order_date(date(2026, 9, 15)):
        is_partial, elapsed, in_period = sales.period_completeness("2026-09", "2026-09-01")
    assert (is_partial, elapsed, in_period) == (True, 15, 30)


def test_a_month_with_no_data_yet_is_partial_with_zero_days_elapsed():
    with _fake_latest_order_date(date(2026, 8, 1)):
        is_partial, elapsed, in_period = sales.period_completeness("2026-09", "2026-09-01")
    assert (is_partial, elapsed, in_period) == (True, 0, 30)


def test_a_rolling_window_is_never_partial_and_needs_no_query():
    with patch.object(sales, "_latest_order_date") as mocked:
        is_partial, elapsed, in_period = sales.period_completeness("7d", "2026-09-01")
        mocked.assert_not_called()
    assert (is_partial, elapsed, in_period) == (False, 7, 7)


def test_a_custom_range_is_never_partial_and_needs_no_query():
    with patch.object(sales, "_latest_order_date") as mocked:
        is_partial, elapsed, in_period = sales.period_completeness(
            None, "2026-09-01", start_date="2026-09-01", end_date="2026-09-10"
        )
        mocked.assert_not_called()
    assert (is_partial, elapsed, in_period) == (False, 10, 10)


def test_get_sales_summary_reports_the_same_completeness_as_period_completeness(monkeypatch):
    monkeypatch.setattr(sales.reporting, "run_query", lambda *a, **k: [{"revenue": 100.0, "orders": 5, "average_order_value": 20.0}])
    with _fake_latest_order_date(date(2026, 9, 15)):
        summary = sales.get_sales_summary("2026-09")
    assert (summary["is_partial_period"], summary["days_elapsed"], summary["days_in_period"]) == (True, 15, 30)


def test_channel_breakdown_reaches_the_same_completeness_without_the_wasted_summary_queries(monkeypatch):
    channels.get_channel_breakdown.cache_clear()  # this function is ttl_cache'd -- start from a clean slate
    calls = []

    def fake_run_query(query, params=None):
        calls.append(query)
        return [{"channel": "Direct", "orders": 10, "revenue": 500.0}]

    monkeypatch.setattr(channels.reporting, "run_query", fake_run_query)
    with _fake_latest_order_date(date(2026, 9, 15)):
        result = channels.get_channel_breakdown("2026-09")

    assert (result["is_partial_period"], result["days_elapsed"], result["days_in_period"]) == (True, 15, 30)
    # Only CHANNEL_SQL itself -- no revenue/orders/AOV/growth query wasted.
    assert len(calls) == 1
