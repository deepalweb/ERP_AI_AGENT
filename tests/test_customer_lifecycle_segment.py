"""Tests for the per-segment customer list (list_customers_by_segment), the
revenue_by_tier addition to compute_lifecycle_summary (both back the
Customer growth heatmap's click-to-filter), and second-order conversion
(get_second_order_intelligence). Pure unit tests; reporting.run_query is
monkeypatched, dispatching on a distinguishing keyword in each query string
(same pattern as test_stock_counts.py)."""

from datetime import datetime, timedelta

from sqlalchemy import text

from backend.app.db import appdb
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


# ---------------------------------------------------------------------------
# get_segment_behavior -- behavior CHANGE for a lifecycle x value-tier cell
# (recent-vs-prior order frequency/basket, reusing get_deterioration_window's
# existing measurement; cancellation rate; channel mix), the "Segment
# Intelligence" counterpart to list_customers_by_segment's customer roster.

def _fake_segment_behavior_run_query(lifecycle_rows, deterioration_rows, cancel_row, baseline_row=None):
    def run_query(sql, params=None):
        if "gap_stats" in sql:
            return lifecycle_rows
        if "PERCENTILE_CONT" in sql:
            return [{"p80": 100_000, "p50": 10_000}]
        if "windowed" in sql:
            return deterioration_rows
        if "state = 'cancel'" in sql:
            # Both the segment and company-wide cancellation queries share
            # this substring -- only the segment one filters by partner_id.
            return [cancel_row] if "partner_id = ANY" in sql else [baseline_row or {"total": 0, "cancelled": 0}]
        raise AssertionError(f"unexpected query: {sql[:80]}")
    return run_query


def test_get_segment_behavior_scopes_to_the_cell_and_computes_deltas(monkeypatch):
    deterioration_rows = [
        {"partner_id": 1, "recent_orders": 5, "recent_revenue": 5000.0, "prior_orders": 4, "prior_revenue": 4400.0},
        # partner 3 is mid-tier -- must NOT leak into a "high" tier query.
        {"partner_id": 3, "recent_orders": 100, "recent_revenue": 999_999.0, "prior_orders": 1, "prior_revenue": 1.0},
    ]
    monkeypatch.setattr(
        cp.reporting, "run_query",
        _fake_segment_behavior_run_query(
            [HIGH_LAPSING, MID_LAPSING], deterioration_rows,
            {"total": 50, "cancelled": 2}, {"total": 1000, "cancelled": 20},
        ),
    )
    result = cp.get_segment_behavior("lapsing", "high")
    assert result["status"] == "ok"
    assert result["customers"] == 1
    assert result["historical_revenue"] == 600_000.0
    assert result["recent_revenue"] == 5000.0
    assert result["order_frequency"] == {"recent_per_customer": 5.0, "prior_per_customer": 4.0}
    assert result["basket"] == {"recent_aov": 1000.0, "prior_aov": 1100.0}
    # Segment rate 4% vs. baseline 2% -> lift 2.0 ("exposure is 2x higher").
    assert result["cancellation_rate"] == {
        "orders": 50, "cancelled": 2, "rate_pct": 4.0, "baseline_rate_pct": 2.0, "lift": 2.0,
    }


def test_get_segment_behavior_rejects_unknown_category(monkeypatch):
    assert cp.get_segment_behavior("not_a_real_category")["status"] == "error"


def test_get_segment_behavior_handles_an_empty_segment(monkeypatch):
    monkeypatch.setattr(
        cp.reporting, "run_query",
        _fake_segment_behavior_run_query([LOW_ACTIVE], [], {"total": 0, "cancelled": 0}),
    )
    result = cp.get_segment_behavior("lapsing", "high")  # LOW_ACTIVE is active, not lapsing
    assert result["status"] == "ok"
    assert result["customers"] == 0


# ---------------------------------------------------------------------------
# get_second_order_intelligence -- second-order conversion headline + cohort
# table. A RETROSPECTIVE calculation (see SECOND_ORDER_SQL's own docstring):
# every customer's first/second order is already fully recorded, so this
# doesn't need fresh daily data the way lifecycle deterioration does.

def _order_row(partner_id, first_order, second_order=None, as_of=AS_OF, store_id=None):
    return {
        "partner_id": partner_id,
        "first_order": first_order,
        "first_order_store_id": store_id,
        "second_order": second_order,
        "as_of_date": as_of,
    }


def _fake_second_order_run_query(rows):
    def run_query(sql, params=None):
        if "o2.rn = 2" in sql:
            return rows
        raise AssertionError(f"unexpected query: {sql[:80]}")
    return run_query


def test_second_order_headline_counts_eligible_and_converted_within_window(monkeypatch):
    rows = [
        # Eligible (40 days since first order) and converted within 30 days.
        _order_row(1, datetime(2026, 8, 26), datetime(2026, 9, 10)),
        # Eligible but converted LATE (35 days > 30-day window) -- not counted as converted.
        _order_row(2, datetime(2026, 8, 26), datetime(2026, 9, 30)),
        # Eligible, never reordered.
        _order_row(3, datetime(2026, 8, 26), None),
        # NOT eligible yet -- first order only 10 days ago.
        _order_row(4, datetime(2026, 9, 25), None),
    ]
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    # Below SECOND_ORDER_MIN_ELIGIBLE, so rate_pct is correctly None here --
    # the counts themselves are still checked directly.
    result = cp.get_second_order_intelligence()
    assert result["headline"]["eligible"] == 3
    assert result["headline"]["converted"] == 1
    assert result["headline"]["rate_pct"] is None  # below SECOND_ORDER_MIN_ELIGIBLE


def test_second_order_headline_rate_appears_above_minimum_sample(monkeypatch):
    rows = [_order_row(i, datetime(2026, 8, 1), datetime(2026, 8, 10)) for i in range(20)]
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    result = cp.get_second_order_intelligence()
    assert result["headline"]["eligible"] == 20
    assert result["headline"]["converted"] == 20
    assert result["headline"]["rate_pct"] == 100.0


def test_second_order_cohorts_group_by_acquisition_month_and_hide_immature_windows(monkeypatch):
    # 20 customers acquired in August, all converted within 10 days --
    # eligible/converted for 30d and 60d (enough time has passed by AS_OF =
    # Oct 5), but NOT for 90d (only ~70 days have passed).
    rows = [_order_row(i, datetime(2026, 8, 1), datetime(2026, 8, 11)) for i in range(20)]
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    result = cp.get_second_order_intelligence()
    august = next(c for c in result["cohorts"] if c["month"] == "2026-08")
    assert august["customers"] == 20
    assert august["windows"]["30"]["rate_pct"] == 100.0
    assert august["windows"]["60"]["rate_pct"] == 100.0
    assert august["windows"]["90"]["eligible"] == 0
    assert august["windows"]["90"]["rate_pct"] is None


def test_second_order_cohorts_separate_different_acquisition_months(monkeypatch):
    rows = (
        [_order_row(i, datetime(2026, 6, 1), datetime(2026, 6, 15)) for i in range(20)]
        + [_order_row(100 + i, datetime(2026, 7, 1), None) for i in range(20)]
    )
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    result = cp.get_second_order_intelligence()
    june = next(c for c in result["cohorts"] if c["month"] == "2026-06")
    july = next(c for c in result["cohorts"] if c["month"] == "2026-07")
    assert june["windows"]["30"]["rate_pct"] == 100.0
    assert july["windows"]["30"]["rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# build_second_order_decline_finding -- runs the cohort table through
# intelligence.detect_anomaly(), the SAME anomaly rule every registered
# trend metric uses, to turn a real conversion decline into a case-eligible
# finding (see customer_predictions.py's own docstring on why this is
# reused rather than a new threshold).

def _second_order_cohort_rows(month_start, converted_n, total_n):
    rows = []
    for i in range(total_n):
        second_order = month_start + timedelta(days=10) if i < converted_n else None
        rows.append(_order_row(month_start.toordinal() * 1000 + i, month_start, second_order))
    return rows


def test_build_second_order_decline_finding_flags_a_real_decline(monkeypatch):
    rows = (
        _second_order_cohort_rows(datetime(2026, 6, 1), 16, 20)
        + _second_order_cohort_rows(datetime(2026, 7, 1), 16, 20)
        + _second_order_cohort_rows(datetime(2026, 8, 1), 16, 20)
        + _second_order_cohort_rows(datetime(2026, 9, 1), 8, 20)  # the decline: 80% -> 40%
    )
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    findings = cp.build_second_order_decline_finding()
    assert len(findings) == 1
    finding = findings[0]
    assert finding["category"] == "critical"
    assert finding["metric_id"] == "second_order_conversion_rate_30d"
    assert finding["domain"] == "order_customer"
    assert finding["confirm"]["period"] == "2026-09"
    assert finding["confirm"]["current"] == 40.0
    assert finding["confirm"]["baseline"] == 80.0


def test_build_second_order_decline_finding_returns_nothing_when_rate_is_stable(monkeypatch):
    rows = (
        _second_order_cohort_rows(datetime(2026, 6, 1), 16, 20)
        + _second_order_cohort_rows(datetime(2026, 7, 1), 16, 20)
        + _second_order_cohort_rows(datetime(2026, 8, 1), 16, 20)
        + _second_order_cohort_rows(datetime(2026, 9, 1), 16, 20)  # same rate, no decline
    )
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    assert cp.build_second_order_decline_finding() == []


# ---------------------------------------------------------------------------
# get_outlet_second_order_rates -- same 30-day conversion math as the
# company-wide headline, sliced by the outlet of each customer's FIRST
# order instead of acquisition month.

def test_outlet_second_order_rates_groups_by_acquisition_outlet(monkeypatch):
    rows = (
        [_order_row(i, datetime(2026, 8, 1), datetime(2026, 8, 10), store_id=1) for i in range(20)]
        + [_order_row(100 + i, datetime(2026, 8, 1), None, store_id=2) for i in range(20)]
    )
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    result = cp.get_outlet_second_order_rates()
    assert result[1]["eligible"] == 20
    assert result[1]["converted"] == 20
    assert result[1]["rate_pct"] == 100.0
    assert result[2]["eligible"] == 20
    assert result[2]["converted"] == 0
    assert result[2]["rate_pct"] == 0.0


def test_outlet_second_order_rates_hides_small_samples(monkeypatch):
    rows = [_order_row(i, datetime(2026, 8, 1), datetime(2026, 8, 10), store_id=1) for i in range(5)]
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    result = cp.get_outlet_second_order_rates()
    assert result[1]["eligible"] == 5
    assert result[1]["rate_pct"] is None  # below SECOND_ORDER_MIN_ELIGIBLE


def test_outlet_second_order_rates_ignores_immature_first_orders(monkeypatch):
    rows = [_order_row(i, datetime(2026, 9, 25), None, store_id=1) for i in range(20)]
    monkeypatch.setattr(cp.reporting, "run_query", _fake_second_order_run_query(rows))
    result = cp.get_outlet_second_order_rates()
    assert result == {}


def test_outlet_behavior_merges_repeat_rate_and_second_order_rate(monkeypatch):
    outlet_rows = [
        {
            "outlet": "Colombo 6", "store_id": 1, "orders": 100, "cancelled": 5,
            "unique_customers": 40, "revenue": 50_000.0, "avg_order_value": 500.0,
            "repeat_customers": 20,
        },
    ]
    second_order_rows = [_order_row(i, datetime(2026, 8, 1), datetime(2026, 8, 10), store_id=1) for i in range(20)]

    def run_query(sql, params=None):
        if "repeat_counts" in sql:
            return outlet_rows
        if "o2.rn = 2" in sql:
            return second_order_rows
        raise AssertionError(f"unexpected query: {sql[:80]}")

    monkeypatch.setattr(cp.reporting, "run_query", run_query)
    result = cp.get_outlet_behavior()
    assert len(result) == 1
    assert result[0]["repeat_rate_pct"] == 50.0  # 20 of 40 unique customers ordered there twice+
    assert result[0]["second_order_rate_pct"] == 100.0


# ---------------------------------------------------------------------------
# compute_predictions -- value_tier is attached per row from the SAME
# _value_tier() helper and percentile thresholds compute_lifecycle_summary()
# uses, so "High value" can't drift into meaning two different things
# between the Customers table and the heatmap.

def _prediction_row(partner_id, revenue, avg_gap=10.0, order_count=10,
                     last_order=datetime(2026, 10, 1), as_of=datetime(2026, 10, 5)):
    return {
        "partner_id": partner_id, "customer_name": f"Customer {partner_id}", "order_count": order_count,
        "revenue": revenue, "avg_order_value": revenue / order_count if order_count else 0,
        "stddev_order_value": None, "last_order": last_order,
        "avg_gap_all": avg_gap, "stddev_gap_all": 2.0, "avg_gap_recent": avg_gap, "stddev_gap_recent": 2.0,
        "n_gaps_recent": order_count - 1, "n_gaps": order_count - 1, "as_of_date": as_of,
    }


def test_compute_predictions_attaches_value_tier_from_population_percentiles(monkeypatch):
    rows = [
        _prediction_row(1, revenue=600_000),  # high (>= p80)
        _prediction_row(2, revenue=50_000),  # mid (>= p50, < p80)
        _prediction_row(3, revenue=1_000),  # low
    ]

    def run_query(sql, params=None):
        if "gap_stats" in sql:
            return rows
        if "PERCENTILE_CONT" in sql:
            return [{"p80": 100_000, "p50": 10_000}]
        raise AssertionError(f"unexpected query: {sql[:80]}")

    monkeypatch.setattr(cp.reporting, "run_query", run_query)
    predictions = cp.compute_predictions()
    by_id = {p["partner_id"]: p for p in predictions}
    assert by_id[1]["value_tier"] == "high"
    assert by_id[2]["value_tier"] == "mid"
    assert by_id[3]["value_tier"] == "low"


# ---------------------------------------------------------------------------
# compute_lifecycle_snapshot_rows / record_lifecycle_snapshot -- the raw
# per-customer history lifecycle TRANSITION rates will eventually be built
# from. No transition math here yet; just the accumulation starting point.

def test_record_lifecycle_snapshot_writes_one_row_per_customer(appdb_reset, monkeypatch):
    monkeypatch.setattr(cp, "_LIFECYCLE_SNAPSHOT_SCHEMA_READY", False)
    monkeypatch.setattr(cp.reporting, "run_query", _fake_run_query([HIGH_LAPSING, LOW_ACTIVE, MID_LAPSING]))
    rows = cp.compute_lifecycle_snapshot_rows()
    assert len(rows) == 3
    cp.record_lifecycle_snapshot(rows)
    stored = appdb.run_query("SELECT * FROM customer_lifecycle_snapshots ORDER BY partner_id")
    assert [r["partner_id"] for r in stored] == [1, 2, 3]
    assert [r["category"] for r in stored] == ["lapsing", "active", "lapsing"]
    assert [r["value_tier"] for r in stored] == ["high", "low", "mid"]
    assert [r["as_of_date"] for r in stored] == ["2026-10-05"] * 3


def test_record_lifecycle_snapshot_is_a_no_op_for_an_already_recorded_as_of_date(appdb_reset, monkeypatch):
    monkeypatch.setattr(cp, "_LIFECYCLE_SNAPSHOT_SCHEMA_READY", False)
    monkeypatch.setattr(cp.reporting, "run_query", _fake_run_query([HIGH_LAPSING]))
    rows = cp.compute_lifecycle_snapshot_rows()
    cp.record_lifecycle_snapshot(rows)
    cp.record_lifecycle_snapshot(rows)  # same frozen as_of_date -- must not fabricate a second "day"
    stored = appdb.run_query("SELECT COUNT(*) AS n FROM customer_lifecycle_snapshots")
    assert stored[0]["n"] == 1


# ---------------------------------------------------------------------------
# compute_lifecycle_transitions -- real customer-level movement between the
# two most recent snapshot dates, distinct from compute_lifecycle_summary's
# day-over-day CATEGORY COUNT delta (which can't say WHO moved where).

def test_compute_lifecycle_transitions_reports_insufficient_history_with_fewer_than_2_dates(appdb_reset, monkeypatch):
    monkeypatch.setattr(cp, "_LIFECYCLE_SNAPSHOT_SCHEMA_READY", False)
    monkeypatch.setattr(cp.reporting, "run_query", _fake_run_query([HIGH_LAPSING]))
    cp.record_lifecycle_snapshot(cp.compute_lifecycle_snapshot_rows())
    result = cp.compute_lifecycle_transitions()
    assert result == {"status": "insufficient_history", "snapshots_available": 1}


def test_compute_lifecycle_transitions_builds_a_real_per_customer_matrix(appdb_reset, monkeypatch):
    monkeypatch.setattr(cp, "_LIFECYCLE_SNAPSHOT_SCHEMA_READY", False)
    cp._ensure_lifecycle_snapshot_schema()
    engine = appdb.get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO customer_lifecycle_snapshots (partner_id, as_of_date, category, value_tier, revenue) "
                "VALUES (:partner_id, :as_of_date, :category, :value_tier, :revenue)"
            ),
            [
                {"partner_id": 1, "as_of_date": "2026-10-01", "category": "active", "value_tier": "high", "revenue": 100.0},
                {"partner_id": 2, "as_of_date": "2026-10-01", "category": "due", "value_tier": "low", "revenue": 10.0},
                {"partner_id": 1, "as_of_date": "2026-10-02", "category": "due", "value_tier": "high", "revenue": 100.0},
                {"partner_id": 2, "as_of_date": "2026-10-02", "category": "lapsing", "value_tier": "low", "revenue": 10.0},
                # Not present on 2026-10-01 -- acquired in between, a new
                # customer, not a transition from any prior state.
                {"partner_id": 3, "as_of_date": "2026-10-02", "category": "new", "value_tier": "low", "revenue": 5.0},
            ],
        )

    result = cp.compute_lifecycle_transitions()
    assert result["status"] == "ok"
    assert result["prev_date"] == "2026-10-01"
    assert result["curr_date"] == "2026-10-02"
    assert result["new_customers"] == 1
    active_row = next(t for t in result["transitions"] if t["from"] == "active")
    assert active_row["to"]["due"] == 1
    due_row = next(t for t in result["transitions"] if t["from"] == "due")
    assert due_row["to"]["lapsing"] == 1
    assert due_row["to"]["active"] == 0
