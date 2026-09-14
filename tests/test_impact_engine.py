"""Tests for the Business Impact Engine (mcp_server/tools/impact_engine.py):
compute_impact()'s per-metric-shape translation into Rs/count figures, and
business_impact_score()'s deterministic ranking factors. Pure unit tests —
intelligence.get_trend (used for revenue/order_volume price lookups) is
monkeypatched, no live DB needed.
"""

from mcp_server.tools import impact_engine as ie
from mcp_server.tools import intelligence


def _metric(metric_id, impact_kind=None):
    return intelligence.METRICS[metric_id] if impact_kind is None else intelligence.METRICS[metric_id]


# ---------------------------------------------------------------------------
# compute_impact

def test_revenue_impact_is_direct_not_estimated():
    anomaly = {"latest_month": "2026-08", "latest_value": 800_000, "baseline_mean": 1_000_000}
    impact = ie.compute_impact("revenue", intelligence.METRICS["revenue"], anomaly, [], horizon="monthly")
    assert impact["kind"] == "revenue_lost"
    assert impact["financial_impact_rs"] == 200_000
    assert impact["confidence"] == "measured"
    assert impact["count"] is None
    assert "200,000" in impact["detail"] or "200000" in impact["detail"].replace(",", "")


def test_revenue_gain_impact():
    anomaly = {"latest_month": "2026-08", "latest_value": 1_200_000, "baseline_mean": 1_000_000}
    impact = ie.compute_impact("revenue", intelligence.METRICS["revenue"], anomaly, [], horizon="monthly")
    assert impact["kind"] == "revenue_gained"
    assert impact["financial_impact_rs"] == 200_000


def test_revenue_impact_none_when_no_deviation():
    anomaly = {"latest_month": "2026-08", "latest_value": 1_000_000, "baseline_mean": 1_000_000}
    assert ie.compute_impact("revenue", intelligence.METRICS["revenue"], anomaly, [], horizon="monthly") is None


def test_order_volume_impact_is_estimated_via_avg_order_value(monkeypatch):
    def fake_get_trend(metric):
        if metric.id == "revenue":
            return [{"month": "2026-08", "value": 1_000_000, "is_partial": False}]
        raise AssertionError(f"unexpected metric {metric.id}")

    monkeypatch.setattr(intelligence, "get_trend", fake_get_trend)
    anomaly = {"latest_month": "2026-08", "latest_value": 8_000, "baseline_mean": 10_000}
    impact = ie.compute_impact("order_volume", intelligence.METRICS["order_volume"], anomaly, [], horizon="monthly")
    assert impact["kind"] == "orders_lost"
    assert impact["count"] == 2000
    assert impact["avg_order_value_used"] == 125.0  # 1,000,000 / 8,000
    assert impact["financial_impact_rs"] == 250_000.0  # 2000 * 125
    assert impact["confidence"] == "estimated"


def test_order_volume_impact_none_when_volume_is_zero(monkeypatch):
    anomaly = {"latest_month": "2026-08", "latest_value": 0, "baseline_mean": 10_000}
    impact = ie.compute_impact("order_volume", intelligence.METRICS["order_volume"], anomaly, [], horizon="monthly")
    assert impact is None


def test_lost_orders_rate_metric_unchanged_math(monkeypatch):
    def fake_get_trend(metric):
        if metric.id == "revenue":
            return [{"month": "2026-08", "value": 1_000_000, "is_partial": False}]
        if metric.id == "order_volume":
            return [{"month": "2026-08", "value": 10_000, "is_partial": False}]
        raise AssertionError(f"unexpected metric {metric.id}")

    monkeypatch.setattr(intelligence, "get_trend", fake_get_trend)
    trend = [{"month": "2026-08", "value": 5.0, "numerator": 500, "denominator": 10_000, "is_partial": False}]
    anomaly = {"latest_month": "2026-08", "latest_value": 5.0, "baseline_mean": 3.0}
    impact = ie.compute_impact(
        "cancellation_rate", intelligence.METRICS["cancellation_rate"], anomaly, trend, horizon="monthly"
    )
    # excess_rate_pts = 2.0 -> excess_orders = 0.02 * 10,000 = 200
    assert impact["kind"] == "lost_orders"
    assert impact["count"] == 200
    assert impact["avg_order_value_used"] == 100.0
    assert impact["financial_impact_rs"] == 20_000.0
    assert impact["confidence"] == "estimated"


def test_lost_orders_recovered_when_rate_improves(monkeypatch):
    def fake_get_trend(metric):
        if metric.id == "revenue":
            return [{"month": "2026-08", "value": 1_000_000, "is_partial": False}]
        if metric.id == "order_volume":
            return [{"month": "2026-08", "value": 10_000, "is_partial": False}]
        raise AssertionError(f"unexpected metric {metric.id}")

    monkeypatch.setattr(intelligence, "get_trend", fake_get_trend)
    trend = [{"month": "2026-08", "value": 1.0, "numerator": 100, "denominator": 10_000, "is_partial": False}]
    anomaly = {"latest_month": "2026-08", "latest_value": 1.0, "baseline_mean": 3.0}
    impact = ie.compute_impact(
        "cancellation_rate", intelligence.METRICS["cancellation_rate"], anomaly, trend, horizon="monthly"
    )
    assert impact["kind"] == "orders_recovered"


def test_impact_none_for_horizon_weekly():
    anomaly = {"latest_month": "2026-08", "latest_value": 800_000, "baseline_mean": 1_000_000}
    assert ie.compute_impact("revenue", intelligence.METRICS["revenue"], anomaly, [], horizon="weekly") is None


def test_impact_none_for_unmeasurable_metric():
    anomaly = {"latest_month": "2026-08", "latest_value": 20.0, "baseline_mean": 10.0}
    impact = ie.compute_impact(
        "delayed_delivery_rate", intelligence.METRICS["delayed_delivery_rate"], anomaly, [], horizon="monthly"
    )
    assert impact is None


# ---------------------------------------------------------------------------
# business_impact_score

def test_score_scales_with_severity():
    critical = ie.business_impact_score("critical", None, None, None)
    medium = ie.business_impact_score("medium", None, None, None)
    assert critical > medium


def test_score_scales_with_persistence():
    one_period = ie.business_impact_score("critical", {"consecutive_periods": 1}, None, None)
    three_periods = ie.business_impact_score("critical", {"consecutive_periods": 3}, None, None)
    assert three_periods > one_period


def test_score_boosted_by_real_impact():
    no_impact = ie.business_impact_score("critical", None, None, None)
    with_impact = ie.business_impact_score("critical", None, {"kind": "revenue_lost"}, None)
    assert with_impact > no_impact


def test_score_dampened_by_low_confidence_data_quality():
    sufficient = ie.business_impact_score("critical", None, None, {"verdict": "sufficient"})
    low_confidence = ie.business_impact_score("critical", None, None, {"verdict": "low_confidence"})
    assert low_confidence < sufficient


def test_score_handles_all_none_gracefully():
    assert ie.business_impact_score("worth_pursuing", None, None, None) == 1.0
