"""Tests for the Warning Engine's in-app diff logic (backend/app/services/
notifications.py). Pure unit tests against synthetic finding lists — no
appdb needed for diff_new_critical_findings itself.
"""

from backend.app.services import notifications as notif

# Safely above WARNING_IMPACT_THRESHOLD (3.0) by default, so existing tests
# that aren't about the threshold itself keep testing what they were
# testing, rather than silently failing once the threshold filter shipped.
_ABOVE_THRESHOLD = 3.5


def _trend(finding_id, category="critical", business_impact_score=_ABOVE_THRESHOLD):
    return {"id": finding_id, "category": category, "business_impact_score": business_impact_score}


def _state(finding_id, metric_id, category="critical", business_impact_score=_ABOVE_THRESHOLD):
    return {"id": finding_id, "metric_id": metric_id, "category": category,
            "business_impact_score": business_impact_score}


def test_first_ever_scan_reports_nothing_new():
    # prev_* is None on the very first-ever scan -- nothing to diff against,
    # so nothing should be fabricated as "new."
    result = notif.diff_new_critical_findings(
        [_trend("revenue-2026-09")], [_state("stock_availability-2026-09-11", "stock_availability")], [],
        None, None, None,
    )
    assert result == []


def test_trend_finding_with_a_new_period_id_is_new():
    prev = [_trend("revenue-2026-08")]
    current = [_trend("revenue-2026-08"), _trend("revenue-2026-09")]
    result = notif.diff_new_critical_findings(current, [], [], prev, [], [])
    assert [f["id"] for f in result] == ["revenue-2026-09"]


def test_state_finding_with_a_daily_changing_id_is_not_new_if_metric_already_flagged():
    # Same underlying problem (stock_availability), but the id embeds
    # today's date and so differs day over day -- must NOT be flagged new
    # every single scan just because the id changed.
    prev = [_state("stock_availability-2026-09-10", "stock_availability")]
    current = [_state("stock_availability-2026-09-11", "stock_availability")]
    result = notif.diff_new_critical_findings([], current, [], [], prev, [])
    assert result == []


def test_state_finding_is_new_when_its_metric_was_not_flagged_yesterday():
    prev = [_state("outlet_no_orders-2026-09-10", "outlet_no_orders")]
    current = [
        _state("outlet_no_orders-2026-09-11", "outlet_no_orders"),
        _state("top_seller_stock-2026-09-11", "top_seller_stock"),
    ]
    result = notif.diff_new_critical_findings([], current, [], [], prev, [])
    assert [f["metric_id"] for f in result] == ["top_seller_stock"]


def test_opportunity_findings_are_never_flagged_as_warnings():
    prev = []
    current = [_trend("revenue-2026-09", category="opportunity")]
    result = notif.diff_new_critical_findings(current, [], [], prev, [], [])
    assert result == []


def test_short_term_findings_use_metric_diff_like_state_findings():
    # Regression for a real bug: a short-term finding's id embeds a single
    # as_of date that advances every day the scan runs (same shape as a
    # state finding's id, NOT a stable period like "revenue-2026-08") --
    # diffing by id would re-fire a "new" notification every day the same
    # underlying spike stays flagged. Must be diffed by metric_id instead.
    prev = [_state("cancellation_rate-trailing-2026-09-10", "cancellation_rate")]
    current = [_state("cancellation_rate-trailing-2026-09-11", "cancellation_rate")]
    result = notif.diff_new_critical_findings([], [], current, [], [], prev)
    assert result == []


def test_short_term_finding_is_new_when_its_metric_was_not_flagged_yesterday():
    prev = []
    current = [_state("cancellation_rate-trailing-2026-09-11", "cancellation_rate")]
    result = notif.diff_new_critical_findings([], [], current, [], [], prev)
    assert [f["metric_id"] for f in result] == ["cancellation_rate"]


# ---------------------------------------------------------------------------
# business_impact_score threshold — "is this worth interrupting someone for"

def test_finding_at_the_score_floor_does_not_warn():
    # 3.0 is the score of a bare critical finding with nothing else behind
    # it (single period, no impact, full confidence) -- the floor, not
    # above it, so it must NOT warn.
    prev = []
    current = [_trend("revenue-2026-09", business_impact_score=3.0)]
    result = notif.diff_new_critical_findings(current, [], [], prev, [], [])
    assert result == []


def test_finding_just_above_the_score_floor_warns():
    prev = []
    current = [_trend("revenue-2026-09", business_impact_score=3.1)]
    result = notif.diff_new_critical_findings(current, [], [], prev, [], [])
    assert [f["id"] for f in result] == ["revenue-2026-09"]


def test_finding_missing_business_impact_score_does_not_warn():
    # Defensive: every real finding carries this field post-Impact-Engine,
    # but a diff shouldn't crash or fabricate a pass for one that doesn't.
    prev = []
    current = [{"id": "revenue-2026-09", "category": "critical"}]
    result = notif.diff_new_critical_findings(current, [], [], prev, [], [])
    assert result == []
