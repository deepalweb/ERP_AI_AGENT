"""Tests for the new per-segment customer list (list_customers_by_segment)
and the revenue_by_tier addition to compute_lifecycle_summary -- both added
to back the Customer growth heatmap's click-to-filter. Pure unit tests;
reporting.run_query is monkeypatched, dispatching on a distinguishing
keyword in each query string (same pattern as test_stock_counts.py)."""

from datetime import datetime

from backend.app.services import customer_predictions as cp

AS_OF = datetime(2026, 10, 5)


def _lifecycle_row(partner_id, revenue, days_since_last, avg_gap=10.0, stddev_gap=2.0,
                    order_count=10, first_order=datetime(2025, 1, 1)):
    last_order = AS_OF.replace(hour=0) - __import__("datetime").timedelta(days=days_since_last)
    return {
        "partner_id": partner_id, "order_count": order_count, "first_order": first_order,
        "last_order": last_order, "revenue": revenue,
        "pickme_orders": 0, "uber_orders": 0, "direct_orders": order_count,
        "avg_gap_all": avg_gap, "stddev_gap_all": stddev_gap,
        "avg_gap_recent": avg_gap, "stddev_gap_recent": stddev_gap,
        "last_gap": None, "prior_avg_gap": None, "n_prior_gaps": 0,
        "as_of_date": AS_OF,
    }


# High value (p80=100_000, p50=10_000): avg_gap=10, stddev=2 -> half_width=2,
# window 8-12 days. 15 days since last -> ratio (15-12)/2=1.5 -> lapsing (< 2.0 churned cutoff).
HIGH_LAPSING = _lifecycle_row(1, revenue=600_000, days_since_last=15)
# Low value, 5 days since last (< 8 low_days) -> active.
LOW_ACTIVE = _lifecycle_row(2, revenue=1_000, days_since_last=5)
# Mid value, same timing as HIGH_LAPSING -> also lapsing, different tier.
MID_LAPSING = _lifecycle_row(3, revenue=50_000, days_since_last=15)


def _fake_run_query(lifecycle_rows, partner_rows=None):
    def run_query(sql, params=None):
        if "ordered AS" in sql:
            return lifecycle_rows
        if "PERCENTILE_CONT" in sql:
            return [{"p80": 100_000, "p50": 10_000}]
        if "FROM res_partner" in sql:
            ids = set(params["ids"])
            return [r for r in (partner_rows or []) if r["id"] in ids]
        raise AssertionError(f"unexpected query: {sql[:80]}")
    return run_query


def test_list_customers_by_segment_filters_by_category_and_tier(monkeypatch):
    monkeypatch.setattr(
        cp.reporting, "run_query",
        _fake_run_query([HIGH_LAPSING, LOW_ACTIVE, MID_LAPSING], [
            {"id": 1, "name": "Alice", "phone": "0771234567"},
        ]),
    )
    result = cp.list_customers_by_segment("lapsing", "high")
    assert result["status"] == "ok"
    assert result["total"] == 1
    assert result["customers"][0]["partner_id"] == 1
    assert result["customers"][0]["name"] == "Alice"
    assert result["customers"][0]["phone_masked"] == "•••• 4567"
    assert result["customers"][0]["revenue"] == 600_000


def test_list_customers_by_segment_without_tier_includes_all_tiers(monkeypatch):
    monkeypatch.setattr(
        cp.reporting, "run_query",
        _fake_run_query([HIGH_LAPSING, LOW_ACTIVE, MID_LAPSING], [
            {"id": 1, "name": "Alice", "phone": "0771234567"},
            {"id": 3, "name": "Charlie", "phone": "0779876543"},
        ]),
    )
    result = cp.list_customers_by_segment("lapsing", None)
    assert result["total"] == 2  # HIGH_LAPSING + MID_LAPSING, LOW_ACTIVE excluded (active, not lapsing)
    # Sorted by revenue descending.
    assert [c["partner_id"] for c in result["customers"]] == [1, 3]


def test_list_customers_by_segment_rejects_unknown_category(monkeypatch):
    result = cp.list_customers_by_segment("not_a_real_category")
    assert result["status"] == "error"


def test_list_customers_by_segment_caps_the_returned_list_but_not_the_total(monkeypatch):
    many = [_lifecycle_row(100 + i, revenue=1000.0 + i, days_since_last=15) for i in range(cp.SEGMENT_LIST_CAP + 5)]
    monkeypatch.setattr(cp.reporting, "run_query", _fake_run_query(many, []))
    result = cp.list_customers_by_segment("lapsing")
    assert result["total"] == cp.SEGMENT_LIST_CAP + 5
    assert len(result["customers"]) == cp.SEGMENT_LIST_CAP


def test_compute_lifecycle_summary_tracks_revenue_by_tier(monkeypatch):
    monkeypatch.setattr(cp.reporting, "run_query", _fake_run_query([HIGH_LAPSING, LOW_ACTIVE, MID_LAPSING]))
    summary = cp.compute_lifecycle_summary()
    lapsing = next(c for c in summary["value_by_category"] if c["id"] == "lapsing")
    assert lapsing["revenue_by_tier"]["high"] == 600_000.0
    assert lapsing["revenue_by_tier"]["mid"] == 50_000.0
    assert lapsing["revenue_by_tier"]["low"] == 0.0
    active = next(c for c in summary["value_by_category"] if c["id"] == "active")
    assert active["revenue_by_tier"]["low"] == 1_000.0
