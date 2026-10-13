"""Customer timing, lifecycle and next-basket heuristics.

All reporting queries share the configured Celeste outlet scope and convert
Odoo UTC timestamps to Asia/Colombo before constructing calendar dates.
Timing uses each qualifying customer's recent purchase gaps and variability;
these windows are heuristics, not calibrated statistical prediction intervals.
Customers need at least three orders and a lifetime average gap of one day.

The daily scan writes versioned application snapshots. Aggregate expected
orders cover only qualifying repeat customers, not all outlet orders. The
separate offline outlet forecasting pilot evaluates complete daily histories.
"""

import json
from datetime import date, datetime, timedelta

from sqlalchemy import text

from backend.app.db import appdb, reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from backend.app.services.order_history import customer_order_query
from mcp_server.tools import intelligence

MIN_ORDER_COUNT = 3
HIGH_CONFIDENCE_ORDER_COUNT = 10
MIN_AVG_GAP_DAYS = 1.0

RECENT_WINDOW_GAPS = 5
"""Tier 2 of the timing model: the expected window is centered on a
customer's RECENT cadence (their last RECENT_WINDOW_GAPS gaps), not their
all-time average — checked against the live data before building this:
of 17,383 customers with 6+ orders, 56.5% have a last-3-orders average gap
that differs from their all-time average by 30%+, and 31% differ by 50%+.
A customer who used to order every 5 days and has settled into every 9
was, under the all-time average, reading as badly overdue when they were
actually right on their new normal. Naturally degrades to "use everything
available" for customers with fewer than RECENT_WINDOW_GAPS gaps (a
FILTER on a window narrower than what exists just returns everything in
it), so this needs no separate branch for low-order customers. Kept
separate from the all-time avg/stddev, which is still used for the
MIN_AVG_GAP_DAYS bulk-account exclusion check below — that one should
stay lifetime-based, since a bulk account having one quiet week shouldn't
suddenly look like a normal customer."""

TIMING_BUCKETS = [
    ("due_today", "Due today", 0, 0),
    ("next_1_3", "Next 1-3 days", 1, 3),
    ("four_7", "4-7 days", 4, 7),
    ("eight_14", "8-14 days", 8, 14),
    ("over_14", "Over 14 days", 15, None),
]

OVERDUE_SEVERITY = [
    ("critical", "Critical", 1.0),
    ("moderate", "Moderate", 0.4),
    ("recent", "Recent", 0.0),
]

PATTERN_DISTRIBUTION_SQL = customer_order_query(f"""
    SELECT
      CASE
        WHEN order_count >= :high_threshold THEN 'ten_plus'
        WHEN order_count >= :min_order_count THEN 'three_to_nine'
        ELSE 'one_to_two'
      END AS bucket,
      COUNT(*) AS n
    FROM (
        SELECT partner_id, COUNT(*) AS order_count
        FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES}
        GROUP BY partner_id
    ) x
    GROUP BY bucket
""")

TOTAL_CUSTOMERS_SQL = customer_order_query(f"SELECT COUNT(DISTINCT partner_id) AS n FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES}")

AS_OF_DATE_SQL = customer_order_query(f"SELECT MAX(date_order) AS as_of_date FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES}")

PREDICTIONS_SQL = customer_order_query(f"""
    WITH ordered AS (
        SELECT so.partner_id, so.date_order,
               LAG(so.date_order) OVER (PARTITION BY so.partner_id ORDER BY so.date_order) AS prev_order,
               ROW_NUMBER() OVER (PARTITION BY so.partner_id ORDER BY so.date_order) AS rn,
               COUNT(*) OVER (PARTITION BY so.partner_id) AS n_orders
        FROM customer_orders so
        WHERE so.state IN {SALE_ORDER_LIVE_STATES}
    ),
    gaps AS (
        SELECT partner_id, rn, n_orders,
               EXTRACT(EPOCH FROM (date_order - prev_order)) / 86400.0 AS gap_days
        FROM ordered
        WHERE prev_order IS NOT NULL
    ),
    gap_stats AS (
        SELECT partner_id, COUNT(*) AS n_gaps,
               AVG(gap_days) AS avg_gap_all, STDDEV(gap_days) AS stddev_gap_all,
               -- Tier 2: the same two stats, but only over each customer's
               -- last RECENT_WINDOW_GAPS gaps (a FILTER against a window
               -- wider than what exists just returns everything, so this
               -- needs no separate branch for customers with few orders).
               AVG(gap_days) FILTER (WHERE rn > n_orders - :recent_window_gaps) AS avg_gap_recent,
               STDDEV(gap_days) FILTER (WHERE rn > n_orders - :recent_window_gaps) AS stddev_gap_recent,
               COUNT(*) FILTER (WHERE rn > n_orders - :recent_window_gaps) AS n_gaps_recent
        FROM gaps
        GROUP BY partner_id
    ),
    orders AS (
        SELECT so.partner_id, rp.name AS customer_name, COUNT(*) AS order_count,
               SUM(so.amount_total) AS revenue, AVG(so.amount_total) AS avg_order_value,
               STDDEV(so.amount_total) AS stddev_order_value, MAX(so.date_order) AS last_order
        FROM customer_orders so
        JOIN res_partner rp ON rp.id = so.partner_id
        WHERE so.state IN {SALE_ORDER_LIVE_STATES}
        GROUP BY so.partner_id, rp.name
    ),
    bounds AS (SELECT MAX(last_order) AS as_of_date FROM orders)
    SELECT o.*, g.avg_gap_all, g.stddev_gap_all, g.avg_gap_recent, g.stddev_gap_recent, g.n_gaps_recent, g.n_gaps,
           b.as_of_date
    FROM orders o
    JOIN gap_stats g ON g.partner_id = o.partner_id
    CROSS JOIN bounds b
    WHERE o.order_count >= :min_order_count AND g.avg_gap_all >= :min_avg_gap_days
""")

_SCHEMA_READY = False
# Disposable snapshots are versioned for the new outlet/timezone contract.
# Legacy snapshots remain intact but cannot be served under the new scope.
ANALYTICS_CONTRACT = "celeste-outlets-colombo-v2"


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS customer_predictions_v2 (
            partner_id INTEGER PRIMARY KEY,
            name TEXT,
            order_count INTEGER,
            confidence TEXT,
            last_order_date TEXT,
            avg_gap_days REAL,
            stddev_gap_days REAL,
            expected_window_start TEXT,
            expected_window_end TEXT,
            days_since_last_order INTEGER,
            status TEXT,
            days_overdue INTEGER,
            revenue REAL,
            avg_order_value REAL,
            stddev_order_value REAL,
            generated_at TEXT
        )
    """)
    # customer_predictions_v2 is a pure daily-replace snapshot (record_predictions
    # deletes and reinserts every row on every scan) — no historical value in
    # existing rows, so a best-effort ALTER is enough. SQLite has no ADD
    # COLUMN IF NOT EXISTS, so guard it the same way brain.py does.
    for column in ("days_until_due INTEGER", "timing_bucket TEXT", "overdue_severity TEXT", "value_tier TEXT"):
        try:
            appdb.run_query(f"ALTER TABLE customer_predictions_v2 ADD COLUMN {column}")
        except Exception:
            pass
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS customer_prediction_stats_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            as_of_date TEXT,
            total_customers INTEGER NOT NULL,
            ten_plus INTEGER NOT NULL,
            three_to_nine INTEGER NOT NULL,
            one_to_two INTEGER NOT NULL,
            excluded INTEGER NOT NULL
        )
    """)
    try:
        appdb.run_query("ALTER TABLE customer_prediction_stats_v2 ADD COLUMN as_of_date TEXT")
    except Exception:
        pass
    _SCHEMA_READY = True


def _confidence_for(order_count: int) -> str:
    return "high" if order_count >= HIGH_CONFIDENCE_ORDER_COUNT else "moderate"


def _window_half_width(avg_gap: float, stddev_gap: float | None) -> float:
    """How wide the expected window is on each side of avg_gap: the
    customer's own gap-to-gap variability when there's enough of it to
    trust (order_count >= 3 means at least 2 gaps, which STDDEV can
    compute but not robustly), else a flat 35% of their own average gap.
    Both are real numbers taken from this customer's own history — never
    an invented constant unrelated to them."""
    half_width = stddev_gap if stddev_gap and stddev_gap > 0 else avg_gap * 0.35
    return max(half_width, 1.0)


def _timing_bucket(days_until_due: int) -> str:
    for bucket_id, _label, low, high in TIMING_BUCKETS:
        if days_until_due >= low and (high is None or days_until_due <= high):
            return bucket_id
    return TIMING_BUCKETS[-1][0]


def _overdue_severity(days_overdue: int, window_half_width: float) -> str:
    """Same personal-to-the-customer posture as the window itself: being 5
    days overdue means something different for a customer whose own window
    was 2 days wide than one whose window was 10 — so severity is a ratio
    against THEIR OWN window width, not a flat day count for everyone."""
    ratio = days_overdue / window_half_width
    for severity, _label, threshold in OVERDUE_SEVERITY:
        if ratio >= threshold:
            return severity
    return OVERDUE_SEVERITY[-1][0]


def compute_predictions() -> list[dict]:
    rows = reporting.run_query(PREDICTIONS_SQL, {
        "min_order_count": MIN_ORDER_COUNT,
        "min_avg_gap_days": MIN_AVG_GAP_DAYS,
        "recent_window_gaps": RECENT_WINDOW_GAPS,
    })
    if not rows:
        return []

    as_of_date = rows[0]["as_of_date"]
    # Value tier needs the whole population's revenue percentile, computed
    # once here and reused per row -- same shared _value_tier() helper
    # compute_lifecycle_summary() uses, so "High value" means the same
    # threshold on this table as it does on the heatmap, never two
    # independently-tuned definitions.
    percentile_rows = reporting.run_query(VALUE_PERCENTILE_SQL)
    value_p80 = float(percentile_rows[0]["p80"] or 0) if percentile_rows else 0.0
    value_p50 = float(percentile_rows[0]["p50"] or 0) if percentile_rows else 0.0
    predictions = []
    for r in rows:
        # Tier 2: window is centered on RECENT cadence, not all-time average
        # (avg_gap_all is still used above, in the SQL WHERE clause, for the
        # bulk-account exclusion check — that one should stay lifetime-based).
        avg_gap = float(r["avg_gap_recent"])
        stddev_gap = float(r["stddev_gap_recent"]) if r["stddev_gap_recent"] is not None else None
        half_width = _window_half_width(avg_gap, stddev_gap)
        low_days = max(1.0, avg_gap - half_width)
        high_days = avg_gap + half_width

        last_order = r["last_order"]
        days_since_last = (as_of_date - last_order).total_seconds() / 86400.0

        days_until_due = None
        timing_bucket = None
        overdue_severity = None

        if days_since_last < low_days:
            status, days_overdue = "on_track", 0
            days_until_due = round(low_days - days_since_last)
            timing_bucket = _timing_bucket(days_until_due)
        elif days_since_last <= high_days:
            status, days_overdue = "due", 0
            days_until_due = 0
            timing_bucket = _timing_bucket(0)
        else:
            status, days_overdue = "overdue", round(days_since_last - high_days)
            overdue_severity = _overdue_severity(days_overdue, half_width)

        predictions.append({
            "partner_id": int(r["partner_id"]),
            "name": (r["customer_name"] or "").strip() or "Unknown",
            "order_count": int(r["order_count"]),
            "confidence": _confidence_for(int(r["order_count"])),
            "last_order_date": last_order.date().isoformat(),
            "avg_gap_days": round(avg_gap, 1),
            "stddev_gap_days": round(stddev_gap, 1) if stddev_gap is not None else None,
            "expected_window_start": (last_order + timedelta(days=low_days)).date().isoformat(),
            "expected_window_end": (last_order + timedelta(days=high_days)).date().isoformat(),
            "days_since_last_order": round(days_since_last),
            "status": status,
            "days_overdue": days_overdue,
            "days_until_due": days_until_due,
            "timing_bucket": timing_bucket,
            "overdue_severity": overdue_severity,
            "revenue": round(float(r["revenue"] or 0), 2),
            "avg_order_value": round(float(r["avg_order_value"] or 0), 2),
            "stddev_order_value": round(float(r["stddev_order_value"]), 2) if r["stddev_order_value"] is not None else None,
            "value_tier": _value_tier(float(r["revenue"] or 0), value_p80, value_p50),
        })
    return predictions


def record_predictions(predictions: list[dict]) -> None:
    """Replaces the whole table on every daily scan — this is a snapshot
    of today's predictions, not a history, so nothing here needs a row's
    prior value. Bulk-inserted in one transaction (a plain loop of
    individual appdb.run_query calls would open ~33,000 separate
    connections for a table this size)."""
    _ensure_schema()
    generated_at = datetime.utcnow().isoformat()
    engine = appdb.get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_predictions_v2"))
        if predictions:
            conn.execute(
                text("""
                    INSERT INTO customer_predictions_v2
                        (partner_id, name, order_count, confidence, last_order_date, avg_gap_days,
                         stddev_gap_days, expected_window_start, expected_window_end,
                         days_since_last_order, status, days_overdue, days_until_due, timing_bucket,
                         overdue_severity, revenue, avg_order_value, stddev_order_value, value_tier, generated_at)
                    VALUES
                        (:partner_id, :name, :order_count, :confidence, :last_order_date, :avg_gap_days,
                         :stddev_gap_days, :expected_window_start, :expected_window_end,
                         :days_since_last_order, :status, :days_overdue, :days_until_due, :timing_bucket,
                         :overdue_severity, :revenue, :avg_order_value, :stddev_order_value, :value_tier, :generated_at)
                """),
                [{**p, "generated_at": generated_at} for p in predictions],
            )


def compute_pattern_stats(predictable_count: int) -> dict:
    """How much of the customer base Celeste can currently form an
    individual prediction for — checked against the live DB before this
    was ever a UI claim (see module docstring). `predictable_count` is
    len(compute_predictions()): customers with order_count >= 3 whose
    average gap also cleared MIN_AVG_GAP_DAYS. `excluded` is the gap
    between "has enough orders" and "actually got a prediction" — customers
    whose order-count would qualify them but whose gap pattern didn't
    (the handful of high-frequency non-human accounts the module docstring
    describes), not a second, silent filter."""
    dist_rows = reporting.run_query(
        PATTERN_DISTRIBUTION_SQL, {"high_threshold": HIGH_CONFIDENCE_ORDER_COUNT, "min_order_count": MIN_ORDER_COUNT}
    )
    dist = {r["bucket"]: r["n"] for r in dist_rows}
    ten_plus = dist.get("ten_plus", 0)
    three_to_nine = dist.get("three_to_nine", 0)
    one_to_two = dist.get("one_to_two", 0)
    total_customers = reporting.run_query(TOTAL_CUSTOMERS_SQL)[0]["n"]
    as_of_date = reporting.run_query(AS_OF_DATE_SQL)[0]["as_of_date"]

    return {
        "as_of_date": as_of_date.date().isoformat() if as_of_date else None,
        "total_customers": total_customers,
        "ten_plus": ten_plus,
        "three_to_nine": three_to_nine,
        "one_to_two": one_to_two,
        "excluded": max(0, (ten_plus + three_to_nine) - predictable_count),
    }


def record_pattern_stats(stats: dict) -> None:
    """Snapshot, not history — same reasoning as record_predictions()."""
    _ensure_schema()
    appdb.run_query("DELETE FROM customer_prediction_stats_v2")
    appdb.run_query(
        """INSERT INTO customer_prediction_stats_v2
               (generated_at, as_of_date, total_customers, ten_plus, three_to_nine, one_to_two, excluded)
           VALUES (:generated_at, :as_of_date, :total_customers, :ten_plus, :three_to_nine, :one_to_two, :excluded)""",
        {**stats, "generated_at": datetime.utcnow().isoformat()},
    )


SORT_COLUMNS = {
    "revenue": "revenue DESC",
    "days_overdue": "days_overdue DESC, revenue DESC",
    "days_since_last_order": "days_since_last_order DESC",
    "soonest": "CASE WHEN days_until_due IS NULL THEN 1 ELSE 0 END ASC, days_until_due ASC",
    "confidence": "CASE confidence WHEN 'high' THEN 0 ELSE 1 END ASC, revenue DESC",
    "order_count": "order_count DESC",
    "name": "name ASC",
}


TIMING_BUCKET_LABEL = {b[0]: b[1] for b in TIMING_BUCKETS}
OVERDUE_SEVERITY_LABEL = {s[0]: s[1] for s in OVERDUE_SEVERITY}


def get_summary() -> dict:
    """Executive-summary numbers for the Customer Purchase Intelligence
    page. Deliberately keeps "customers analyzed" (the whole customer
    base) and "customers with a usable pattern" (the ones a prediction
    could actually be formed for) as two different numbers — collapsing
    them into one was flagged as a real, confusing bug: most of the
    customer base has only 1-2 orders and has no individual pattern to
    predict from yet."""
    _ensure_schema()
    status_rows = appdb.run_query(
        "SELECT status, COUNT(*) AS count, SUM(revenue) AS revenue, SUM(avg_order_value) AS expected_value "
        "FROM customer_predictions_v2 GROUP BY status"
    )
    confidence_rows = appdb.run_query(
        "SELECT confidence, COUNT(*) AS count FROM customer_predictions_v2 GROUP BY confidence"
    )
    timing_rows = appdb.run_query(
        "SELECT timing_bucket, COUNT(*) AS count, SUM(avg_order_value) AS expected_value "
        "FROM customer_predictions_v2 WHERE timing_bucket IS NOT NULL GROUP BY timing_bucket"
    )
    overdue_rows = appdb.run_query(
        "SELECT overdue_severity, COUNT(*) AS count, SUM(revenue) AS historical_value "
        "FROM customer_predictions_v2 WHERE overdue_severity IS NOT NULL GROUP BY overdue_severity"
    )
    high_conf_due_rows = appdb.run_query(
        "SELECT COUNT(*) AS n, SUM(avg_order_value) AS v FROM customer_predictions_v2 "
        "WHERE status = 'due' AND confidence = 'high'"
    )
    generated_rows = appdb.run_query("SELECT generated_at FROM customer_predictions_v2 LIMIT 1")
    pattern_rows = appdb.run_query("SELECT * FROM customer_prediction_stats_v2 ORDER BY generated_at DESC LIMIT 1")

    by_status = {
        r["status"]: {"count": r["count"], "revenue": round(r["revenue"] or 0, 2)} for r in status_rows
    }
    by_confidence = {r["confidence"]: r["count"] for r in confidence_rows}
    predictable_customers = sum(v["count"] for v in by_status.values())

    timing_by_bucket = {r["timing_bucket"]: r for r in timing_rows}
    timing_distribution = [
        {
            "id": bucket_id,
            "label": label,
            "count": timing_by_bucket.get(bucket_id, {}).get("count", 0),
            "expected_value": round(timing_by_bucket.get(bucket_id, {}).get("expected_value") or 0, 2),
        }
        for bucket_id, label, _low, _high in TIMING_BUCKETS
    ]

    overdue_by_severity = {r["overdue_severity"]: r for r in overdue_rows}
    overdue_breakdown = [
        {
            "id": severity,
            "label": label,
            "count": overdue_by_severity.get(severity, {}).get("count", 0),
            "historical_value": round(overdue_by_severity.get(severity, {}).get("historical_value") or 0, 2),
        }
        for severity, label, _threshold in OVERDUE_SEVERITY
    ]

    pattern = pattern_rows[0] if pattern_rows else None

    return {
        "generated_at": generated_rows[0]["generated_at"] if generated_rows else None,
        "as_of_date": pattern["as_of_date"] if pattern else None,
        "total_customers": pattern["total_customers"] if pattern else None,
        "predictable_customers": predictable_customers,
        "pattern_distribution": {
            "ten_plus": pattern["ten_plus"] if pattern else None,
            "three_to_nine": pattern["three_to_nine"] if pattern else None,
            "one_to_two": pattern["one_to_two"] if pattern else None,
            "excluded": pattern["excluded"] if pattern else None,
        },
        "on_track": by_status.get("on_track", {"count": 0, "revenue": 0}),
        "due": by_status.get("due", {"count": 0, "revenue": 0}),
        "overdue": by_status.get("overdue", {"count": 0, "revenue": 0}),
        "high_confidence_count": by_confidence.get("high", 0),
        "moderate_confidence_count": by_confidence.get("moderate", 0),
        "timing_distribution": timing_distribution,
        "overdue_breakdown": overdue_breakdown,
        "expected_orders": by_status.get("due", {"count": 0}).get("count", 0),
        "expected_revenue": round(
            next((r["expected_value"] for r in status_rows if r["status"] == "due"), 0) or 0, 2
        ),
        "high_confidence_expected_revenue": round(high_conf_due_rows[0]["v"] or 0, 2) if high_conf_due_rows else 0,
        "high_confidence_due_count": high_conf_due_rows[0]["n"] if high_conf_due_rows else 0,
    }


HOME_FINDING_MIN_DUE_COUNT = 20
"""Floor for surfacing a Customer Purchase Opportunity card on the Business
Briefing (Home) — below this, skip the card entirely rather than show
something as thin as "12 customers are expected to order tomorrow." Unlike
investigation.py's anomaly-based findings, this signal is always present
(there are always customers approaching their own cadence), so the
question isn't "is this unusual," it's "is this big enough to be worth a
manager's attention today." This is a floor to avoid a trivial-looking
card, not a tuned business threshold — there's no historical baseline yet
to tune one against."""


def build_home_finding(summary: dict) -> dict | None:
    """Decides whether today's Customer Purchase Intelligence summary is
    significant enough to surface on the Business Briefing as its own
    finding — separate from, and structurally different than, the
    anomaly-triggered findings in investigation.py/state_findings.py (no
    trend, no baseline, no breakdowns: just today's aggregate demand
    picture). The intelligence engine (get_summary) calculates everything
    regardless; this is the one place that decides what's worth a
    manager's attention, per [[project_celeste_intelligence_status]]."""
    due = summary.get("due") or {"count": 0}
    if due["count"] < HOME_FINDING_MIN_DUE_COUNT:
        return None

    return {
        "id": f"customer-purchase-opportunity-{summary.get('as_of_date') or 'unknown'}",
        "analytics_contract": ANALYTICS_CONTRACT,
        "title": "Customer Purchase Opportunity",
        "as_of_date": summary.get("as_of_date"),
        "approaching_count": due["count"],
        "expected_revenue": summary.get("expected_revenue", 0),
        "high_confidence_count": summary.get("high_confidence_due_count", 0),
        "overdue_count": (summary.get("overdue") or {"count": 0})["count"],
    }


def list_predictions(status: str | None, sort: str, limit: int, offset: int) -> tuple[list[dict], int]:
    _ensure_schema()
    where = "WHERE status = :status" if status else ""
    order = SORT_COLUMNS.get(sort, SORT_COLUMNS["revenue"])
    params = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status

    rows = appdb.run_query(
        f"SELECT * FROM customer_predictions_v2 {where} ORDER BY {order} LIMIT :limit OFFSET :offset", params
    )
    total_rows = appdb.run_query(f"SELECT COUNT(*) AS n FROM customer_predictions_v2 {where}", params)
    return rows, total_rows[0]["n"]


# All customer and outlet analytics share the configured Celeste outlet scope.
# repeat_counts is a second pass over the same scoped orders, grouped by
# (store, customer) first -- "did this customer order here more than once"
# is a different question than "how many orders did this outlet have," and
# collapsing them into one GROUP BY would double-count multi-order customers.
OUTLET_BEHAVIOR_SQL = customer_order_query(f"""
    WITH per_customer AS (
        SELECT store_id, partner_id, COUNT(*) AS orders_at_outlet
        FROM customer_orders
        WHERE state IN {SALE_ORDER_LIVE_STATES}
        GROUP BY store_id, partner_id
    ),
    repeat_counts AS (
        SELECT store_id, COUNT(*) AS repeat_customers
        FROM per_customer
        WHERE orders_at_outlet >= 2
        GROUP BY store_id
    )
    SELECT COALESCE(rs.name, 'Unknown store') AS outlet,
           so.store_id,
           COUNT(*) FILTER (WHERE so.state IN {SALE_ORDER_LIVE_STATES}) AS orders,
           COUNT(*) FILTER (WHERE so.state = 'cancel') AS cancelled,
           COUNT(DISTINCT so.partner_id) FILTER (WHERE so.state IN {SALE_ORDER_LIVE_STATES}) AS unique_customers,
           SUM(so.amount_total) FILTER (WHERE so.state IN {SALE_ORDER_LIVE_STATES}) AS revenue,
           AVG(so.amount_total) FILTER (WHERE so.state IN {SALE_ORDER_LIVE_STATES}) AS avg_order_value,
           MAX(rc.repeat_customers) AS repeat_customers
    FROM customer_orders so
    LEFT JOIN res_store rs ON rs.id = so.store_id
    LEFT JOIN repeat_counts rc ON rc.store_id = so.store_id
    WHERE so.state IN {SALE_ORDER_LIVE_STATES + ("cancel",)} AND so.store_id IN {CELESTE_STORE_IDS}
    GROUP BY rs.name, so.store_id
    ORDER BY orders DESC
""")


DAILY_FORECAST_SQL = """
    SELECT
      SUM(1.0 / (julianday(expected_window_end) - julianday(expected_window_start) + 1)) AS expected_orders,
      SUM(avg_order_value / (julianday(expected_window_end) - julianday(expected_window_start) + 1)) AS expected_value,
      COUNT(*) AS customers_in_window
    FROM customer_predictions_v2
    WHERE expected_window_start <= :d AND expected_window_end >= :d
"""


def _forecast_for_day(day: str) -> dict:
    """One day's worth of the bottom-up order-count forecast — built from
    each predictable customer's own expected window rather than a
    company-wide trend line (that's forecast.py's job for revenue/order_volume
    — this is the "per customer order pattern" version).

    The first version of this counted every customer whose window happens
    to COVER the target day, full stop — checked against this business's
    real historical average (~1,700-2,200 orders/day) before shipping, and
    it came out to 20,125 for a single day, over ten times too high. The
    bug: a customer's window is often several days wide, and counting them
    once per day the window spans effectively multiplies them by their own
    window width instead of predicting a single order. The fix: assume
    each customer's one order in their window is uniformly likely to land
    on any single day of it, so their contribution to any one day is
    1/width_days, not 1. Verified this normalized version lands at ~1,098
    for the same date — the right order of magnitude for a subset of the
    customer base (see get_daily_forecast), not a guess that happened to
    look plausible.

    Only covers the ~33,600 "predictable" customers (order_count >= 3)
    this whole module is scoped to — the ~51,300 with 1-2 orders and any
    brand-new customers aren't included, so every day here is a
    repeat-customer floor, not a full company forecast."""
    rows = appdb.run_query(DAILY_FORECAST_SQL, {"d": day})
    row = rows[0] if rows else None
    if not row or row["expected_orders"] is None:
        return {"date": day, "expected_orders": 0, "expected_revenue": 0.0, "customers_in_window": 0}

    return {
        "date": day,
        "expected_orders": round(row["expected_orders"]),
        "expected_revenue": round(row["expected_value"] or 0, 2),
        "customers_in_window": row["customers_in_window"],
    }


def _latest_as_of_date() -> date | None:
    _ensure_schema()
    rows = appdb.run_query("SELECT as_of_date FROM customer_prediction_stats_v2 ORDER BY generated_at DESC LIMIT 1")
    if not rows or not rows[0]["as_of_date"]:
        return None
    return date.fromisoformat(rows[0]["as_of_date"])


def get_tomorrow_forecast() -> dict | None:
    """See _forecast_for_day — this is just day 1 of get_daily_forecast().
    "Tomorrow" means the day after this data's own as_of_date (the latest
    order date in the DB), not wall-clock tomorrow — the DB snapshot lags
    real time by weeks, same anchor customers.py's at-risk logic uses."""
    as_of_date = _latest_as_of_date()
    if as_of_date is None:
        return None
    return _forecast_for_day((as_of_date + timedelta(days=1)).isoformat())


def get_daily_forecast(days: int = 14) -> list[dict]:
    """Day-by-day version of the tomorrow forecast — same method, one row
    per day for the next `days` days after this data's own as_of_date.
    Replaces trying to read a single "Due today: 18,788" bucket (a
    headcount of everyone currently inside their window, however wide)
    as if it meant "18,788 orders today" — it doesn't; this does the
    actual per-day math instead of leaving that gap for a reader to
    misinterpret."""
    as_of_date = _latest_as_of_date()
    if as_of_date is None:
        return []
    return [_forecast_for_day((as_of_date + timedelta(days=i)).isoformat()) for i in range(1, days + 1)]


def get_outlet_second_order_rates() -> dict[int, dict]:
    """Second-order conversion grouped by the outlet of each customer's
    FIRST order -- same 30-day window, same as_of_date, and same
    SECOND_ORDER_MIN_ELIGIBLE noisy-sample guard as get_second_order_intelligence(),
    just sliced by acquisition outlet instead of acquisition month. Answers
    "which outlet brings in customers who actually come back," the
    per-outlet counterpart to the company-wide headline -- reuses the same
    SECOND_ORDER_SQL rows rather than a second query."""
    rows = reporting.run_query(SECOND_ORDER_SQL)
    if not rows:
        return {}
    as_of_date = rows[0]["as_of_date"]
    by_store: dict[int, dict] = {}
    for r in rows:
        store_id = r["first_order_store_id"]
        if store_id is None:
            continue
        first_order = r["first_order"]
        days_since_first = (as_of_date - first_order).total_seconds() / 86400.0
        if days_since_first < SECOND_ORDER_HEADLINE_WINDOW_DAYS:
            continue
        bucket = by_store.setdefault(store_id, {"eligible": 0, "converted": 0})
        bucket["eligible"] += 1
        second_order = r["second_order"]
        gap = (second_order - first_order).total_seconds() / 86400.0 if second_order is not None else None
        if gap is not None and gap <= SECOND_ORDER_HEADLINE_WINDOW_DAYS:
            bucket["converted"] += 1
    return {
        store_id: {
            "eligible": b["eligible"],
            "converted": b["converted"],
            "rate_pct": round(b["converted"] / b["eligible"] * 100, 1) if b["eligible"] >= SECOND_ORDER_MIN_ELIGIBLE else None,
        }
        for store_id, b in by_store.items()
    }


def get_outlet_behavior() -> list[dict]:
    """Per-outlet customer behavior for the 5 real Celeste stores — order
    count, cancellations, unique customers, how often each customer orders
    there on average, the share of customers who came back a 2nd time at
    that same outlet, and the share of customers ACQUIRED there who placed
    a second order anywhere within 30 days. Computed live (a ~2s aggregate
    over 5 stores, not the 33,600-customer computation the rest of this
    module caches daily) rather than added to the daily scan."""
    rows = reporting.run_query(OUTLET_BEHAVIOR_SQL)
    second_order_by_store = get_outlet_second_order_rates()
    result = []
    for r in rows:
        orders = int(r["orders"] or 0)
        cancelled = int(r["cancelled"] or 0)
        total = orders + cancelled
        unique_customers = int(r["unique_customers"] or 0)
        repeat_customers = int(r["repeat_customers"] or 0)
        second_order = second_order_by_store.get(r["store_id"])
        result.append({
            "outlet": r["outlet"],
            "orders": orders,
            "cancelled": cancelled,
            "cancellation_rate_pct": round(cancelled / total * 100, 2) if total else 0.0,
            "unique_customers": unique_customers,
            "orders_per_customer": round(orders / unique_customers, 2) if unique_customers else 0.0,
            "repeat_rate_pct": round(repeat_customers / unique_customers * 100, 1) if unique_customers else None,
            "second_order_rate_pct": second_order["rate_pct"] if second_order else None,
            "revenue": round(float(r["revenue"] or 0), 2),
            "avg_order_value": round(float(r["avg_order_value"] or 0), 2),
        })
    return result


# ---------------------------------------------------------------------------
# Customer Lifecycle — a second, independent categorization covering EVERY
# customer (not just the ~33,600 "predictable" ones the rest of this module
# is scoped to), plus an orthogonal value tier. Deliberately additive and
# parallel to compute_predictions()/get_summary() above rather than a
# rewrite of them — same reasoning as OUTLET_BEHAVIOR_SQL and the daily
# forecast: a new lens on the same order data, not a replacement for an
# already-working, already-reviewed page.
#
# Checked against the live DB before building this (see the counts this
# produces): the 6 lifecycle states the business asked for (New, Active,
# Due/At Risk, Lapsing, Churned, Win-back) don't cover everyone — of the
# ~51,300 customers with only 1-2 orders, ~38,300 are neither recent (New)
# nor scoreable against a personal window. Rather than silently drop 45% of
# the customer base from the categorization, they land in a 7th bucket,
# Dormant / One-time. A customer whose average order gap is too fast to be
# human (the same MIN_AVG_GAP_DAYS filter used above) is Excluded, same
# reasoning as elsewhere in this module.
LIFECYCLE_NEW_WINDOW_DAYS = 30
LIFECYCLE_CHURNED_MULTIPLIER = 2.0
"""How many multiples of a customer's own window width they have to be
overdue before "lapsing" becomes "churned" — checked against the live
data at 1.5x/2x/3x before picking 2x: at 2x, overdue customers split
roughly 4,100 lapsing / 5,700 churned, a real, meaningfully-sized split
in both directions rather than one bucket swallowing the other."""

LIFECYCLE_WIN_BACK_MULTIPLIER = 2.0
"""A customer only counts as a win-back if the gap immediately before
their most recent order was at least this many times their own PRIOR
average gap — i.e. they clearly went quiet and then came back, not just
ordered a little later than usual."""

LIFECYCLE_MIN_WIN_BACK_PRIOR_GAPS = 2
"""Win-back needs a "prior average gap" to compare the last gap against —
require at least 2 prior gaps (order_count >= 4) so that average isn't
just one single number pretending to be a pattern."""

LIFECYCLE_CATEGORIES = [
    ("new", "New", "🌱"),
    ("active", "Active / On Track", "✅"),
    ("due", "Due / At Risk", "🟡"),
    ("lapsing", "Lapsing", "🟠"),
    ("churned", "Churned / Lost", "🔴"),
    ("win_back", "Win-back", "🔵"),
    ("dormant", "Dormant / One-time", "💤"),
    ("excluded", "Excluded", "⚪"),
]

VALUE_TIERS = [
    ("high", "High value", "🥇"),
    ("mid", "Mid value", "🥈"),
    ("low", "Low value / occasional", "🥉"),
]

# Only these categories have a real churn signal to measure (New/Dormant/
# Excluded customers were never scored against a personal window in the
# first place) — channel churn-rate below is a share of THIS population,
# not of every customer tagged with that channel, or a channel dominated
# by brand-new customers would look falsely "healthy."
SCOREABLE_CATEGORIES = {"active", "due", "lapsing", "churned", "win_back"}

CHANNELS = [
    ("uber", "Uber", "🚗"),
    ("pickme", "PickMe", "🛵"),
    ("direct", "Direct", "🏪"),
]

LIFECYCLE_SQL = customer_order_query(f"""
    WITH ordered AS (
        SELECT so.partner_id, so.date_order,
               LAG(so.date_order) OVER (PARTITION BY so.partner_id ORDER BY so.date_order) AS prev_order,
               ROW_NUMBER() OVER (PARTITION BY so.partner_id ORDER BY so.date_order) AS rn,
               COUNT(*) OVER (PARTITION BY so.partner_id) AS n_orders
        FROM customer_orders so
        WHERE so.state IN {SALE_ORDER_LIVE_STATES}
    ),
    gaps AS (
        SELECT partner_id, rn, n_orders,
               EXTRACT(EPOCH FROM (date_order - prev_order)) / 86400.0 AS gap_days
        FROM ordered
        WHERE prev_order IS NOT NULL
    ),
    gap_stats AS (
        SELECT partner_id,
               AVG(gap_days) AS avg_gap_all,
               STDDEV(gap_days) AS stddev_gap_all,
               -- Tier 2, same as PREDICTIONS_SQL: window math uses only the
               -- last RECENT_WINDOW_GAPS gaps, not the all-time average —
               -- must match PREDICTIONS_SQL's rule exactly, or a customer
               -- could read one status on the Customers table and a
               -- different one in Customer Lifecycle again (see the
               -- Overdue-customers-section removal earlier for why that's
               -- a real problem, not a cosmetic one).
               AVG(gap_days) FILTER (WHERE rn > n_orders - :recent_window_gaps) AS avg_gap_recent,
               STDDEV(gap_days) FILTER (WHERE rn > n_orders - :recent_window_gaps) AS stddev_gap_recent,
               -- The gap ending at the most recent order (rn = n_orders) vs the
               -- average of every gap before it — used to detect a win-back
               -- (an unusually long gap right before the customer's latest
               -- order, not just "they ordered a bit late this time").
               MAX(gap_days) FILTER (WHERE rn = n_orders) AS last_gap,
               AVG(gap_days) FILTER (WHERE rn < n_orders) AS prior_avg_gap,
               COUNT(*) FILTER (WHERE rn < n_orders) AS n_prior_gaps
        FROM gaps
        GROUP BY partner_id
    ),
    orders AS (
        SELECT so.partner_id, COUNT(*) AS order_count,
               MIN(so.date_order) AS first_order, MAX(so.date_order) AS last_order,
               SUM(so.amount_total) AS revenue,
               -- Confirmed via channels.py: pickme_order_id/uber_order_id are
               -- the real per-order channel signals (is_pickme_order is a
               -- dead field, true on ~8 of 410K orders). A customer's
               -- "primary channel" below is whichever of these three they
               -- have the most orders through — checked against the live
               -- data before building this: 99.9% of customers only ever
               -- use one channel, so "primary" is barely an approximation.
               COUNT(*) FILTER (WHERE so.pickme_order_id IS NOT NULL) AS pickme_orders,
               COUNT(*) FILTER (WHERE so.uber_order_id IS NOT NULL) AS uber_orders,
               COUNT(*) FILTER (WHERE so.pickme_order_id IS NULL AND so.uber_order_id IS NULL) AS direct_orders
        FROM customer_orders so
        WHERE so.state IN {SALE_ORDER_LIVE_STATES}
        GROUP BY so.partner_id
    ),
    bounds AS (SELECT MAX(last_order) AS as_of_date FROM orders)
    SELECT o.*, g.avg_gap_all, g.stddev_gap_all, g.avg_gap_recent, g.stddev_gap_recent,
           g.last_gap, g.prior_avg_gap, g.n_prior_gaps, b.as_of_date
    FROM orders o
    LEFT JOIN gap_stats g ON g.partner_id = o.partner_id
    CROSS JOIN bounds b
    -- Filtered here, after bounds is computed from the whole population --
    -- never inside the orders CTE, or a single-customer call would silently
    -- recompute as_of_date from just that one customer's last order instead
    -- of the real company-wide "data through" date. NULL (the default)
    -- returns every customer, unchanged from before this filter existed --
    -- so compute_lifecycle_summary()'s own call needs no behavior change.
    WHERE (CAST(:partner_id AS integer) IS NULL OR o.partner_id = :partner_id)
""")

VALUE_PERCENTILE_SQL = customer_order_query(f"""
    SELECT PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY revenue) AS p80,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY revenue) AS p50
    FROM (
        SELECT partner_id, SUM(amount_total) AS revenue
        FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES} GROUP BY partner_id
    ) x
""")

# Customer Intelligence review, 2026-10-08: first and second order per
# customer, plus the data's own as_of_date -- the one raw fetch both
# get_second_order_conversion() (a single window, e.g. "42% within 30d")
# and get_second_order_cohorts() (grouped by acquisition month, several
# windows) are built from, so the two never risk computing "second order"
# two different ways. A RETROSPECTIVE calculation deliberately: every
# customer's first/second order is already fully recorded history, so
# unlike lifecycle deterioration this needs no fresh daily data to be
# honest -- only elapsed time since each customer's own first order,
# which has already happened for most of the population regardless of
# whether new orders are currently landing.
SECOND_ORDER_SQL = customer_order_query(f"""
    WITH ordered AS (
        SELECT partner_id, date_order, store_id,
               ROW_NUMBER() OVER (PARTITION BY partner_id ORDER BY date_order) AS rn
        FROM customer_orders
        WHERE state IN {SALE_ORDER_LIVE_STATES}
    ),
    bounds AS (SELECT MAX(date_order) AS as_of_date FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES})
    SELECT o1.partner_id, o1.date_order AS first_order, o1.store_id AS first_order_store_id,
           o2.date_order AS second_order, b.as_of_date
    FROM ordered o1
    LEFT JOIN ordered o2 ON o2.partner_id = o1.partner_id AND o2.rn = 2
    CROSS JOIN bounds b
    WHERE o1.rn = 1
""")

DETERIORATION_WINDOW_DAYS_DEFAULT = 60

# Customer deterioration, measurement layer only -- Wave 1 Customer Phase B,
# first pass. Deliberately raw: two equal-length day windows (recent vs
# prior) ending at the live as_of_date, order count and revenue in each,
# nothing blended into a score and no "deteriorating" threshold applied
# here -- that's a separate, later decision once real distributions are
# inspected. as_of_date is computed from the WHOLE scoped population
# before the optional partner_id filter narrows the rows, same discipline
# LIFECYCLE_SQL already uses, so a single-customer call and a population
# scan always agree on "data through" for the same run.
DETERIORATION_SQL = customer_order_query(f"""
    WITH ordered AS (
        SELECT so.partner_id, so.date_order, so.amount_total
        FROM customer_orders so
        WHERE so.state IN {SALE_ORDER_LIVE_STATES}
    ),
    bounds AS (SELECT MAX(date_order) AS as_of_date FROM ordered),
    aged AS (
        SELECT o.partner_id, o.amount_total,
               EXTRACT(EPOCH FROM (b.as_of_date - o.date_order)) / 86400.0 AS days_ago
        FROM ordered o CROSS JOIN bounds b
    ),
    windowed AS (
        SELECT partner_id,
               COUNT(*) FILTER (WHERE days_ago <= :window_days) AS recent_orders,
               COALESCE(SUM(amount_total) FILTER (WHERE days_ago <= :window_days), 0) AS recent_revenue,
               COUNT(*) FILTER (WHERE days_ago > :window_days AND days_ago <= 2 * :window_days) AS prior_orders,
               COALESCE(SUM(amount_total) FILTER (WHERE days_ago > :window_days AND days_ago <= 2 * :window_days), 0) AS prior_revenue
        FROM aged
        GROUP BY partner_id
    )
    SELECT w.*, b.as_of_date
    FROM windowed w
    CROSS JOIN bounds b
    WHERE (CAST(:partner_id AS integer) IS NULL OR w.partner_id = :partner_id)
""")


def get_deterioration_window(partner_id: int | None, window_days: int = DETERIORATION_WINDOW_DAYS_DEFAULT) -> list[dict]:
    """One row per customer (or one row, for a single partner_id): recent
    vs. prior order count/revenue over two window_days-long windows. Pass
    partner_id=None to scan the whole scoped population -- e.g. to inspect
    the real distribution of these signals before deciding what "material
    deterioration" should mean, which is a separate, later decision this
    function deliberately does not make."""
    return reporting.run_query(DETERIORATION_SQL, {"window_days": window_days, "partner_id": partner_id})


def _lifecycle_category(row: dict, as_of_date) -> str:
    order_count = row["order_count"]
    days_since_first = (as_of_date - row["first_order"]).days
    if days_since_first <= LIFECYCLE_NEW_WINDOW_DAYS:
        return "new"
    if order_count < MIN_ORDER_COUNT:
        return "dormant"

    avg_gap_all = float(row["avg_gap_all"]) if row["avg_gap_all"] is not None else None
    if avg_gap_all is None or avg_gap_all < MIN_AVG_GAP_DAYS:
        return "excluded"

    # Tier 2: window math uses recent cadence, not the all-time average
    # used just above for the bulk-account exclusion check — same split as
    # compute_predictions(), and it has to match exactly (see the SQL
    # comment on gap_stats above).
    avg_gap = float(row["avg_gap_recent"])
    stddev_gap = float(row["stddev_gap_recent"]) if row["stddev_gap_recent"] is not None else None
    half_width = _window_half_width(avg_gap, stddev_gap)
    low_days = max(1.0, avg_gap - half_width)
    high_days = avg_gap + half_width
    days_since_last = (as_of_date - row["last_order"]).total_seconds() / 86400.0

    if days_since_last < low_days:
        base = "active"
    elif days_since_last <= high_days:
        base = "due"
    else:
        ratio = (days_since_last - high_days) / half_width
        base = "churned" if ratio >= LIFECYCLE_CHURNED_MULTIPLIER else "lapsing"

    if base in ("active", "due") and (row["n_prior_gaps"] or 0) >= LIFECYCLE_MIN_WIN_BACK_PRIOR_GAPS:
        last_gap = row["last_gap"]
        prior_avg_gap = row["prior_avg_gap"]
        if prior_avg_gap and last_gap and float(last_gap) >= LIFECYCLE_WIN_BACK_MULTIPLIER * float(prior_avg_gap):
            return "win_back"
    return base


def _primary_channel(row: dict) -> str:
    counts = {"uber": row["uber_orders"], "pickme": row["pickme_orders"], "direct": row["direct_orders"]}
    return max(counts, key=counts.get)


def _value_tier(revenue: float, p80: float, p50: float) -> str:
    """p80/p50 must come from VALUE_PERCENTILE_SQL run over the WHOLE
    customer population -- classifying a single customer against their own
    revenue as both the value and the threshold is meaningless. Shared here
    so compute_lifecycle_summary() and a single-customer lookup can never
    drift onto two different definitions of "high value" (same reasoning
    as _lifecycle_category being shared, not reimplemented, per caller)."""
    return "high" if revenue >= p80 else "mid" if revenue >= p50 else "low"


def get_lifecycle_rows() -> tuple[list[dict], object]:
    """Raw per-customer rows, each annotated with its lifecycle_category --
    the one live query + classification rule compute_lifecycle_summary()
    and list_customers_by_segment() already use internally, now exposed so
    a DIFFERENT module (customers.get_customer_intelligence(), the Ask
    Celeste chat card's coarser 4-bucket vocabulary) can derive its
    segments from this SAME per-customer classification, rather than a
    second, independently-tuned rule that can silently disagree with it --
    the exact cross-module drift the 2026-10-13 Customer module review
    flagged (customers.py used to classify "at risk" by a flat 28/60-day
    threshold, unrelated to a customer's own order cadence)."""
    rows = reporting.run_query(LIFECYCLE_SQL, {"recent_window_gaps": RECENT_WINDOW_GAPS, "partner_id": None})
    if not rows:
        return [], None
    as_of_date = rows[0]["as_of_date"]
    for row in rows:
        row["lifecycle_category"] = _lifecycle_category(row, as_of_date)
    return rows, as_of_date


def compute_lifecycle_summary() -> dict | None:
    """One pass over every customer's order history (~3s for ~84,900
    customers), classifying each into exactly one of the 8
    LIFECYCLE_CATEGORIES, exactly one of the 3 VALUE_TIERS, and (checked
    against the live data: 99.9% of customers only ever order through one
    platform, so this is barely an approximation) a primary CHANNEL —
    three independent axes, e.g. a customer can be "Lapsing", "High
    value", AND "PickMe" all at once. Returns only aggregated
    counts/revenue, plus a category x value-tier cross-tab and a
    per-channel churn-rate breakdown — not a per-customer table, since
    nothing on the page drills into individual customers by lifecycle
    stage yet; that's a well-scoped follow-up if it turns out to be
    wanted, not something to build speculatively now."""
    rows = reporting.run_query(LIFECYCLE_SQL, {"recent_window_gaps": RECENT_WINDOW_GAPS, "partner_id": None})
    if not rows:
        return None

    as_of_date = rows[0]["as_of_date"]
    percentile_rows = reporting.run_query(VALUE_PERCENTILE_SQL)
    p80 = float(percentile_rows[0]["p80"] or 0) if percentile_rows else 0.0
    p50 = float(percentile_rows[0]["p50"] or 0) if percentile_rows else 0.0

    cat_counts = {cid: 0 for cid, _, _ in LIFECYCLE_CATEGORIES}
    cat_revenue = {cid: 0.0 for cid, _, _ in LIFECYCLE_CATEGORIES}
    value_counts = {vid: 0 for vid, _, _ in VALUE_TIERS}
    value_revenue = {vid: 0.0 for vid, _, _ in VALUE_TIERS}
    cross = {cid: {vid: 0 for vid, _, _ in VALUE_TIERS} for cid, _, _ in LIFECYCLE_CATEGORIES}
    cross_revenue = {cid: {vid: 0.0 for vid, _, _ in VALUE_TIERS} for cid, _, _ in LIFECYCLE_CATEGORIES}
    channel_stats = {
        chid: {"total": 0, "scoreable": 0, "churned": 0, "lapsing": 0, "revenue": 0.0} for chid, _, _ in CHANNELS
    }

    for row in rows:
        category = _lifecycle_category(row, as_of_date)
        revenue = float(row["revenue"] or 0)
        value_tier = _value_tier(revenue, p80, p50)
        channel = _primary_channel(row)

        cat_counts[category] += 1
        cat_revenue[category] += revenue
        value_counts[value_tier] += 1
        value_revenue[value_tier] += revenue
        cross[category][value_tier] += 1
        cross_revenue[category][value_tier] += revenue

        cs = channel_stats[channel]
        cs["total"] += 1
        cs["revenue"] += revenue
        if category in SCOREABLE_CATEGORIES:
            cs["scoreable"] += 1
            if category == "churned":
                cs["churned"] += 1
            elif category == "lapsing":
                cs["lapsing"] += 1

    return {
        "as_of_date": as_of_date.date().isoformat(),
        "total_customers": len(rows),
        "categories": [
            {"id": cid, "label": label, "icon": icon, "count": cat_counts[cid], "revenue": round(cat_revenue[cid], 2)}
            for cid, label, icon in LIFECYCLE_CATEGORIES
        ],
        "value_tiers": [
            {"id": vid, "label": label, "icon": icon, "count": value_counts[vid], "revenue": round(value_revenue[vid], 2)}
            for vid, label, icon in VALUE_TIERS
        ],
        "value_by_category": [
            {"id": cid, "label": label, **cross[cid],
             "revenue_by_tier": {vid: round(cross_revenue[cid][vid], 2) for vid in cross_revenue[cid]}}
            for cid, label, _icon in LIFECYCLE_CATEGORIES
        ],
        "channel_breakdown": [
            {
                "id": chid,
                "label": label,
                "icon": icon,
                "total_customers": channel_stats[chid]["total"],
                "scoreable_customers": channel_stats[chid]["scoreable"],
                "revenue": round(channel_stats[chid]["revenue"], 2),
                "churn_rate_pct": round(channel_stats[chid]["churned"] / channel_stats[chid]["scoreable"] * 100, 1)
                if channel_stats[chid]["scoreable"]
                else None,
                "lapsing_rate_pct": round(channel_stats[chid]["lapsing"] / channel_stats[chid]["scoreable"] * 100, 1)
                if channel_stats[chid]["scoreable"]
                else None,
            }
            for chid, label, icon in CHANNELS
        ],
    }


def _mask_phone(phone: str | None) -> str | None:
    # Duplicated from customer_lookup.py (not imported) -- that module
    # imports FROM this one, so importing it back here would be circular.
    # A 5-line pure function; not worth a shared module for this alone.
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) < 4:
        return None
    return f"•••• {digits[-4:]}"


SEGMENT_LIST_CAP = 200
"""LIFECYCLE_SQL's window functions over the full order history take
8-10s to run (measured live, ~89k customers) -- real, unavoidable for a
live per-customer classification pass, not a query to optimize away. The
cost must be paid once per segment selection, not once per page: the
frontend fetches this one capped list per cell click and paginates
through it client-side, rather than re-running the whole query for every
Previous/Next click. A segment can have tens of thousands of matches
(e.g. dormant/low-value); capping at the top 200 by revenue keeps the
response bounded without hiding the real total (returned separately)."""


def list_customers_by_segment(category: str, value_tier: str | None = None) -> dict:
    """The named customers behind one lifecycle x value-tier cell on the
    Customer growth heatmap -- compute_lifecycle_summary() deliberately
    only returns aggregate counts (see its own docstring), so a cell click
    has nothing to filter against without this: the same full pass,
    classifying every customer exactly the same way, kept here as a
    second entry point rather than returning a per-customer list from
    compute_lifecycle_summary() itself (which /customers/lifecycle and the
    scan path both call and would pay for a per-customer payload neither
    of them needs). Masked contact info, same discipline as
    customer_lookup.py's search -- this is a list of real customers, not
    an internal id dump. Capped at SEGMENT_LIST_CAP, top by revenue --
    see that constant for why, and why there's no offset param."""
    if category not in {cid for cid, _, _ in LIFECYCLE_CATEGORIES}:
        return {"status": "error", "detail": f"Unknown lifecycle category: {category}"}
    if value_tier is not None and value_tier not in {vid for vid, _, _ in VALUE_TIERS}:
        return {"status": "error", "detail": f"Unknown value tier: {value_tier}"}

    rows = reporting.run_query(LIFECYCLE_SQL, {"recent_window_gaps": RECENT_WINDOW_GAPS, "partner_id": None})
    if not rows:
        return {"status": "ok", "as_of_date": None, "total": 0, "customers": []}

    as_of_date = rows[0]["as_of_date"]
    percentile_rows = reporting.run_query(VALUE_PERCENTILE_SQL)
    p80 = float(percentile_rows[0]["p80"] or 0) if percentile_rows else 0.0
    p50 = float(percentile_rows[0]["p50"] or 0) if percentile_rows else 0.0

    matched = []
    for row in rows:
        if _lifecycle_category(row, as_of_date) != category:
            continue
        revenue = float(row["revenue"] or 0)
        if value_tier is not None and _value_tier(revenue, p80, p50) != value_tier:
            continue
        matched.append({"partner_id": row["partner_id"], "order_count": row["order_count"],
                         "revenue": round(revenue, 2), "last_order": row["last_order"]})

    matched.sort(key=lambda c: c["revenue"], reverse=True)
    total = len(matched)
    page = matched[:SEGMENT_LIST_CAP]

    partner_ids = [c["partner_id"] for c in page]
    names = {}
    if partner_ids:
        partner_rows = reporting.run_query(
            "SELECT id, name, phone FROM res_partner WHERE id = ANY(:ids)", {"ids": partner_ids}
        )
        names = {r["id"]: r for r in partner_rows}

    customers = [{
        "partner_id": c["partner_id"],
        "name": (names.get(c["partner_id"]) or {}).get("name") or "Unknown",
        "phone_masked": _mask_phone((names.get(c["partner_id"]) or {}).get("phone")),
        "order_count": c["order_count"],
        "revenue": c["revenue"],
        "last_order": c["last_order"].isoformat() if c["last_order"] else None,
    } for c in page]

    return {
        "status": "ok",
        "as_of_date": as_of_date.date().isoformat() if hasattr(as_of_date, "date") else as_of_date,
        "total": total,
        "customers": customers,
    }


_LIFECYCLE_SNAPSHOT_SCHEMA_READY = False


def _ensure_lifecycle_snapshot_schema():
    global _LIFECYCLE_SNAPSHOT_SCHEMA_READY
    if _LIFECYCLE_SNAPSHOT_SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS customer_lifecycle_snapshots (
            partner_id INTEGER NOT NULL,
            as_of_date TEXT NOT NULL,
            category TEXT NOT NULL,
            value_tier TEXT NOT NULL,
            revenue REAL NOT NULL,
            PRIMARY KEY (partner_id, as_of_date)
        )
    """)
    _LIFECYCLE_SNAPSHOT_SCHEMA_READY = True


def compute_lifecycle_snapshot_rows() -> list[dict]:
    """Per-customer lifecycle category + value tier, one row per customer —
    the historical record compute_lifecycle_summary() deliberately doesn't
    keep (its own docstring: aggregates only; list_customers_by_segment()
    above already established the pattern of a second LIFECYCLE_SQL pass
    rather than reshaping that contract). This is the raw material lifecycle
    TRANSITION rates (Active -> Due, Due -> Lapsing, Lapsing -> Churned,
    etc.) will eventually be computed from — accumulating here starting
    now, before any transition math is built, since transitions need real
    day-over-day history that doesn't exist yet. See
    record_lifecycle_snapshot() for why a frozen as_of_date doesn't
    fabricate a new day of history."""
    rows = reporting.run_query(LIFECYCLE_SQL, {"recent_window_gaps": RECENT_WINDOW_GAPS, "partner_id": None})
    if not rows:
        return []

    as_of_date = rows[0]["as_of_date"]
    percentile_rows = reporting.run_query(VALUE_PERCENTILE_SQL)
    p80 = float(percentile_rows[0]["p80"] or 0) if percentile_rows else 0.0
    p50 = float(percentile_rows[0]["p50"] or 0) if percentile_rows else 0.0

    as_of_iso = as_of_date.date().isoformat()
    return [
        {
            "partner_id": int(row["partner_id"]),
            "as_of_date": as_of_iso,
            "category": _lifecycle_category(row, as_of_date),
            "value_tier": _value_tier(float(row["revenue"] or 0), p80, p50),
            "revenue": round(float(row["revenue"] or 0), 2),
        }
        for row in rows
    ]


def record_lifecycle_snapshot(rows: list[dict]) -> None:
    """Appends one row per customer for today's as_of_date. Deliberately a
    no-op if this as_of_date already has a snapshot — the daily scan can
    run more than once on the same day, and while Odoo's feed is frozen
    (see [[project_celeste_intelligence_status]]) every run shares the
    same as_of_date. A snapshot answers "what did the customer base look
    like on this real data date"; recording it twice, or recording a day
    where the underlying data never actually moved, would fabricate a
    transition that never happened once transition rates get built on top
    of this table."""
    if not rows:
        return
    _ensure_lifecycle_snapshot_schema()
    as_of_date = rows[0]["as_of_date"]
    existing = appdb.run_query(
        "SELECT COUNT(*) AS n FROM customer_lifecycle_snapshots WHERE as_of_date = :d", {"d": as_of_date}
    )
    if existing[0]["n"]:
        return
    engine = appdb.get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO customer_lifecycle_snapshots (partner_id, as_of_date, category, value_tier, revenue)
                VALUES (:partner_id, :as_of_date, :category, :value_tier, :revenue)
            """),
            rows,
        )


def compute_lifecycle_transitions() -> dict:
    """Real customer-level movement between the two most recent lifecycle
    snapshots -- distinct from compute_lifecycle_summary()'s day-over-day
    CATEGORY COUNT delta (e.g. "Lapsing +12 since yesterday"), which can't
    say whether that +12 is 12 people moving in or 12 moving in and 24
    moving out netting to +12. This is the transition math
    compute_lifecycle_snapshot_rows()'s own docstring said would eventually
    get built on top of its accumulating history.

    Needs at least 2 DISTINCT as_of_dates in customer_lifecycle_snapshots
    to show anything -- returns status="insufficient_history" honestly
    rather than fabricating a transition from a single snapshot (see
    record_lifecycle_snapshot's own docstring on why a frozen as_of_date,
    the reporting mirror's own lag, doesn't add a second day on its own).
    Checked live, 2026-10-13: exactly 1 distinct as_of_date exists so far,
    so this returns insufficient_history today -- correct behavior, not a
    bug, until the mirror's order data actually advances."""
    _ensure_lifecycle_snapshot_schema()
    dates = appdb.run_query(
        "SELECT DISTINCT as_of_date FROM customer_lifecycle_snapshots ORDER BY as_of_date DESC LIMIT 2"
    )
    if len(dates) < 2:
        return {"status": "insufficient_history", "snapshots_available": len(dates)}

    curr_date, prev_date = dates[0]["as_of_date"], dates[1]["as_of_date"]
    curr_rows = appdb.run_query(
        "SELECT partner_id, category FROM customer_lifecycle_snapshots WHERE as_of_date = :d", {"d": curr_date}
    )
    prev_by_partner = {
        r["partner_id"]: r["category"]
        for r in appdb.run_query(
            "SELECT partner_id, category FROM customer_lifecycle_snapshots WHERE as_of_date = :d", {"d": prev_date}
        )
    }

    matrix = {cid: {cid2: 0 for cid2, _, _ in LIFECYCLE_CATEGORIES} for cid, _, _ in LIFECYCLE_CATEGORIES}
    new_customers = 0
    for row in curr_rows:
        prev_category = prev_by_partner.get(row["partner_id"])
        if prev_category is None:
            # Acquired between the two snapshot dates -- a real new
            # customer, not a transition from any prior state, so counted
            # separately rather than invented as a fake "from" category.
            new_customers += 1
            continue
        matrix[prev_category][row["category"]] += 1

    return {
        "status": "ok",
        "prev_date": prev_date,
        "curr_date": curr_date,
        "new_customers": new_customers,
        "transitions": [
            {"from": cid, "label": label, "to": matrix[cid]}
            for cid, label, _icon in LIFECYCLE_CATEGORIES
        ],
    }


SECOND_ORDER_HEADLINE_WINDOW_DAYS = 30
SECOND_ORDER_COHORT_WINDOWS = (30, 60, 90)
SECOND_ORDER_COHORT_MONTHS = 12
# Same posture as intelligence.py's MIN_SEGMENT_VOLUME: a rate computed
# from a handful of customers isn't a rate, it's noise -- shown as
# unavailable (null) rather than a real-looking percentage. The most
# recent 1-2 cohort months are the common case this guards (too few
# customers have even reached day 30 yet), not an edge case.
SECOND_ORDER_MIN_ELIGIBLE = 15


def get_second_order_intelligence() -> dict | None:
    """Second-order conversion: of customers whose first order is old
    enough to judge, what share placed a second order within the window --
    the headline rate at SECOND_ORDER_HEADLINE_WINDOW_DAYS, plus the same
    calculation broken out by acquisition month and by 30/60/90-day window
    (the cohort table). One fetch (SECOND_ORDER_SQL) for both, since
    they're the same per-customer (first_order, second_order) data grouped
    two different ways -- see that query's own docstring for why this is
    safe to treat as historical fact even while Odoo's live feed is stale."""
    rows = reporting.run_query(SECOND_ORDER_SQL)
    if not rows:
        return None
    as_of_date = rows[0]["as_of_date"]

    headline_eligible = 0
    headline_converted = 0
    cohorts: dict[str, dict] = {}
    for r in rows:
        first_order = r["first_order"]
        second_order = r["second_order"]
        days_since_first = (as_of_date - first_order).total_seconds() / 86400.0
        gap = (second_order - first_order).total_seconds() / 86400.0 if second_order is not None else None

        if days_since_first >= SECOND_ORDER_HEADLINE_WINDOW_DAYS:
            headline_eligible += 1
            if gap is not None and gap <= SECOND_ORDER_HEADLINE_WINDOW_DAYS:
                headline_converted += 1

        month_key = first_order.strftime("%Y-%m")
        cohort = cohorts.setdefault(month_key, {
            "customers": 0,
            "eligible": {w: 0 for w in SECOND_ORDER_COHORT_WINDOWS},
            "converted": {w: 0 for w in SECOND_ORDER_COHORT_WINDOWS},
        })
        cohort["customers"] += 1
        for w in SECOND_ORDER_COHORT_WINDOWS:
            if days_since_first < w:
                continue
            cohort["eligible"][w] += 1
            if gap is not None and gap <= w:
                cohort["converted"][w] += 1

    cohort_rows = []
    for month_key in sorted(cohorts)[-SECOND_ORDER_COHORT_MONTHS:]:
        c = cohorts[month_key]
        windows = {}
        for w in SECOND_ORDER_COHORT_WINDOWS:
            eligible = c["eligible"][w]
            windows[str(w)] = {
                "eligible": eligible,
                "converted": c["converted"][w],
                "rate_pct": round(c["converted"][w] / eligible * 100, 1) if eligible >= SECOND_ORDER_MIN_ELIGIBLE else None,
            }
        cohort_rows.append({"month": month_key, "customers": c["customers"], "windows": windows})

    return {
        "as_of_date": as_of_date.date().isoformat() if hasattr(as_of_date, "date") else as_of_date,
        "headline": {
            "window_days": SECOND_ORDER_HEADLINE_WINDOW_DAYS,
            "eligible": headline_eligible,
            "converted": headline_converted,
            "rate_pct": round(headline_converted / headline_eligible * 100, 1) if headline_eligible >= SECOND_ORDER_MIN_ELIGIBLE else None,
        },
        "cohorts": cohort_rows,
    }


def build_second_order_decline_finding() -> list[dict]:
    """Second-order conversion by acquisition cohort, run through the SAME
    anomaly rule intelligence.detect_anomaly() applies to every registered
    trend metric -- reused directly rather than inventing a new threshold,
    since a cohort's 30-day conversion rate is itself just one value per
    month, same shape as any other monthly trend once mature cohorts are
    lined up chronologically. Not registered in intelligence.METRICS
    outright: that registry's get_trend() assumes a metric computed by its
    own SQL path, and this one is retrospective cohort data this module
    already owns (see SECOND_ORDER_SQL's own docstring on why that's safe
    to treat as historical fact even while the order feed is stale).
    Checked live, 2026-10-13: 8 mature cohorts, a real, substantial decline
    from 69.1% (2026-03) to 28.6% (2026-09) -- this is not a theoretical
    detector, it has something to catch today. Returns a plain list (empty,
    or one finding) for brain.py's measurement.combine([...]) -- the same
    shape every other state_findings.py builder returns."""
    result = get_second_order_intelligence()
    if result is None:
        return []

    trend = [
        {
            "month": c["month"],
            "value": c["windows"]["30"]["rate_pct"],
            "is_partial": c["windows"]["30"]["rate_pct"] is None,
        }
        for c in result["cohorts"]
    ]
    anomaly = intelligence.detect_anomaly(trend, "higher_is_good")
    if anomaly["category"] not in ("critical", "opportunity"):
        return []

    cohort = next(c for c in result["cohorts"] if c["month"] == anomaly["latest_month"])
    window = cohort["windows"]["30"]
    moved = "fallen" if anomaly["category"] == "critical" else "risen"

    return [{
        "id": f"second_order_conversion_decline-{anomaly['latest_month']}",
        "metric_id": "second_order_conversion_rate_30d",
        "domain": "order_customer",
        "category": anomaly["category"],
        "priority": "high" if anomaly["category"] == "critical" else "medium",
        "title": f"Second-order conversion has {moved} to {anomaly['latest_value']}% "
                 f"for customers acquired in {anomaly['latest_month']}",
        "detected": date.today().isoformat(),
        "confirm": {
            "period": anomaly["latest_month"], "current": anomaly["latest_value"],
            "baseline": anomaly["baseline_mean"], "pct_change": anomaly["pct_change_vs_baseline"],
            "unit": "pct",
        },
        "root_cause": {
            "checks": [],
            "summary": f"{window['converted']} of {window['eligible']} customers acquired in "
                       f"{anomaly['latest_month']} placed a second order within 30 days "
                       f"({anomaly['latest_value']}%), vs a {round(anomaly['baseline_mean'], 1)}% "
                       f"average across prior mature cohorts.",
        },
        "impact": {
            "kind": "second_order_conversion_decline",
            "count": cohort["customers"],
            "confidence": "measured",
            "detail": f"{cohort['customers']} customers acquired in {anomaly['latest_month']}.",
        },
        "recommendations": [],
        "status": "completed",
    }]


_LIFECYCLE_SCHEMA_READY = False


def _ensure_lifecycle_schema():
    global _LIFECYCLE_SCHEMA_READY
    if _LIFECYCLE_SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS customer_lifecycle_stats_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            summary TEXT NOT NULL
        )
    """)
    _LIFECYCLE_SCHEMA_READY = True


def record_lifecycle_summary(summary: dict | None) -> None:
    """History, not a snapshot (reversed from the original design) — kept
    so category counts and channel churn/lapsing rates can carry a real
    day-over-day delta (e.g. "Lapsing +12 since yesterday"), the same
    reasoning brain.py's _snapshot_health() already uses for Business
    Health. Deltas are computed here, at write time, against the most
    recent PRIOR row, and stored embedded in the new row — so
    get_lifecycle_summary() needs no extra read-time logic to return them,
    same pattern health_history uses. A category or channel with no prior
    row (the very first run, or a brand-new id) correctly gets delta: None
    rather than a fabricated 0."""
    if summary is None:
        return
    _ensure_lifecycle_schema()

    prior_rows = appdb.run_query(
        "SELECT summary FROM customer_lifecycle_stats_v2 ORDER BY generated_at DESC LIMIT 1"
    )
    prior = json.loads(prior_rows[0]["summary"]) if prior_rows else None
    prior_cat_count = {c["id"]: c["count"] for c in prior["categories"]} if prior else {}
    prior_channel = {c["id"]: c for c in prior.get("channel_breakdown", [])} if prior else {}

    def _rate_delta(current, prior_val):
        if current is None or prior_val is None:
            return None
        return round(current - prior_val, 1)

    summary_with_deltas = {
        **summary,
        "categories": [
            {**c, "delta": (c["count"] - prior_cat_count[c["id"]]) if c["id"] in prior_cat_count else None}
            for c in summary["categories"]
        ],
        "channel_breakdown": [
            {
                **c,
                "churn_rate_delta": _rate_delta(c["churn_rate_pct"], (prior_channel.get(c["id"]) or {}).get("churn_rate_pct")),
                "lapsing_rate_delta": _rate_delta(c["lapsing_rate_pct"], (prior_channel.get(c["id"]) or {}).get("lapsing_rate_pct")),
            }
            for c in summary.get("channel_breakdown", [])
        ],
    }

    appdb.run_query(
        "INSERT INTO customer_lifecycle_stats_v2 (generated_at, summary) VALUES (:generated_at, :summary)",
        {"generated_at": datetime.utcnow().isoformat(), "summary": json.dumps(summary_with_deltas)},
    )


def get_lifecycle_summary() -> dict | None:
    _ensure_lifecycle_schema()
    rows = appdb.run_query("SELECT summary FROM customer_lifecycle_stats_v2 ORDER BY generated_at DESC LIMIT 1")
    if not rows:
        return None
    return json.loads(rows[0]["summary"])


# ---------------------------------------------------------------------------
# Order forecast chart — actual order counts transitioning into the Tier-2
# forecast. Originally also carried a stock-coverage overlay (current +
# required stock, company-wide); dropped after checking it against the live
# data — a single combined stock total across every SKU can't answer "will
# we run out," since a shortage in one fast-moving product is invisible
# inside a warehouse full of slow-moving stock. Per-product stock coverage
# for the top sellers lives separately — see stock_alerts.py
# get_top_product_stock_coverage().
ACTUAL_DAILY_ORDERS_SQL = customer_order_query(f"""
    SELECT DATE(date_order) AS day, COUNT(*) AS orders
    FROM customer_orders
    WHERE state IN {SALE_ORDER_LIVE_STATES} AND date_order >= :start_date AND date_order < :end_date
    GROUP BY DATE(date_order)
    ORDER BY day
""")


def get_order_forecast_chart(actual_days: int = 5, forecast_days: int = 5) -> dict | None:
    """actual_days of real order counts leading up to the data's own
    as_of_date, then forecast_days of the Tier-2 order forecast — one
    continuous order-count line, solid-then-dashed on the frontend."""
    as_of_date = _latest_as_of_date()
    if as_of_date is None:
        return None

    start_date = as_of_date - timedelta(days=actual_days - 1)
    actual_rows = reporting.run_query(ACTUAL_DAILY_ORDERS_SQL, {
        "start_date": start_date.isoformat(),
        "end_date": (as_of_date + timedelta(days=1)).isoformat(),
    })
    actual_by_day = {
        (r["day"] if isinstance(r["day"], str) else r["day"].isoformat()): int(r["orders"])
        for r in actual_rows
    }

    forecast_by_day = {f["date"]: f["expected_orders"] for f in get_daily_forecast(forecast_days)}

    points = []
    for i in range(actual_days):
        d_iso = (start_date + timedelta(days=i)).isoformat()
        points.append({
            "date": d_iso,
            "actual_orders": actual_by_day.get(d_iso),
            "is_partial": d_iso == as_of_date.isoformat(),
            "predicted_orders": None,
        })
    for i in range(1, forecast_days + 1):
        d_iso = (as_of_date + timedelta(days=i)).isoformat()
        points.append({
            "date": d_iso,
            "actual_orders": None,
            "predicted_orders": forecast_by_day.get(d_iso, 0),
        })

    return {
        "as_of_date": as_of_date.isoformat(),
        "points": points,
        "timezone": "Asia/Colombo",
        "forecast_scope": "repeat_customers_only",
        "latest_actual_day_is_partial": True,
    }


# ---------------------------------------------------------------------------
# Outlet allocation of the repeat-customer estimate, using trailing 30-day
# shares. This remains a heuristic, not an independently trained outlet forecast.
# Independently rounded counts need not sum exactly to the displayed total.
OUTLET_RECENT_VOLUME_SQL = customer_order_query(f"""
    WITH bounds AS (SELECT MAX(date_order) AS as_of FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES})
    SELECT rs.name AS outlet, COUNT(*) AS orders_30d
    FROM customer_orders so
    JOIN res_store rs ON rs.id = so.store_id
    CROSS JOIN bounds b
    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.date_order >= b.as_of - INTERVAL '30 days'
    GROUP BY rs.name
""")


def get_outlet_order_forecast(forecast_days: int = 5) -> dict | None:
    """Company-wide daily forecast, split across outlets by each outlet's
    real trailing-30-day share of order volume. Outlets with zero recent
    orders (e.g. Ja-Ela, verified live to have none in the trailing 30
    days) get no forecast rather than a divide-by-zero or a fabricated
    share."""
    as_of_date = _latest_as_of_date()
    if as_of_date is None:
        return None

    volume_rows = reporting.run_query(OUTLET_RECENT_VOLUME_SQL)
    total_volume = sum(int(r["orders_30d"] or 0) for r in volume_rows)
    if total_volume == 0:
        return {"as_of_date": as_of_date.isoformat(), "outlets": [], "days": []}

    shares = {
        r["outlet"]: int(r["orders_30d"]) / total_volume
        for r in volume_rows if int(r["orders_30d"] or 0) > 0
    }
    outlet_names = sorted(shares.keys())

    days = []
    for f in get_daily_forecast(forecast_days):
        days.append({
            "date": f["date"],
            "total_expected_orders": f["expected_orders"],
            "by_outlet": {name: round(f["expected_orders"] * shares[name]) for name in outlet_names},
        })

    return {
        "as_of_date": as_of_date.isoformat(),
        "outlets": [{"name": name, "share_pct": round(shares[name] * 100, 1)} for name in outlet_names],
        "days": days,
    }


# ---------------------------------------------------------------------------
# Next-basket prediction — WHICH products a specific customer is likely to
# order next, not just WHEN (that's the rest of this module). Same method
# as the whole-order window (_window_half_width reused directly), applied
# one level down: per (customer, product) instead of per customer.
#
# Checked against the live data before building this: of the ~31,200
# predictable customers, 15,735 (about half) have at least one product
# they've bought 3+ separate times — real, usable signal for that half.
# The other half's repeat purchases don't concentrate on any single
# product often enough to predict from, and get an honest empty result
# rather than a guessed one.
#
# Computed on demand for one customer at a time (not a bulk daily job like
# the rest of this module) — it only matters when someone drills into a
# specific customer, and a single-partner query is cheap, unlike the
# ~33,600-customer whole-order computation this module caches daily.
NEXT_BASKET_SQL = customer_order_query(f"""
    WITH order_products AS (
        SELECT so.id AS order_id, sol.product_id, so.date_order
        FROM customer_orders so
        JOIN sale_order_line sol ON sol.order_id = so.id
        JOIN product_product pp ON pp.id = sol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE so.partner_id = :partner_id AND so.state IN {SALE_ORDER_LIVE_STATES}
          AND pt.celeste_product_type = 'INVENTORY'
        GROUP BY so.id, sol.product_id, so.date_order
    ),
    lines AS (
        SELECT product_id, date_order,
               LAG(date_order) OVER (PARTITION BY product_id ORDER BY date_order) AS prev_date,
               COUNT(*) OVER (PARTITION BY product_id) AS times_bought
        FROM order_products
    ),
    gaps AS (
        SELECT product_id, times_bought,
               EXTRACT(EPOCH FROM (date_order - prev_date)) / 86400.0 AS gap_days
        FROM lines
        WHERE prev_date IS NOT NULL
    ),
    product_stats AS (
        SELECT product_id, MAX(times_bought) AS times_bought,
               AVG(gap_days) AS avg_gap, STDDEV(gap_days) AS stddev_gap
        FROM gaps
        GROUP BY product_id
        HAVING MAX(times_bought) >= :min_times_bought
    ),
    last_purchase AS (
        SELECT product_id, MAX(date_order) AS last_date
        FROM order_products
        GROUP BY product_id
    )
    SELECT ps.product_id, pt.name->>'en_US' AS product_name, pt.default_code AS sku,
           ps.times_bought, ps.avg_gap, ps.stddev_gap, lp.last_date
    FROM product_stats ps
    JOIN last_purchase lp ON lp.product_id = ps.product_id
    JOIN product_product pp ON pp.id = ps.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
""")

NEXT_BASKET_MIN_TIMES_BOUGHT = 3  # same MIN_ORDER_COUNT reasoning: at least 2 gaps to measure a pattern
NEXT_BASKET_STATUS_ORDER = {"overdue": 0, "due": 1, "on_track": 2}


def get_predicted_next_basket(partner_id: int, top_n: int = 8) -> dict | None:
    """Which of this customer's own repeat products are due for reorder,
    ranked most-overdue first. Empty products list (not None) means this
    customer has no product bought often enough to predict from — a real,
    disclosed limitation, not an error."""
    as_of_date = _latest_as_of_date()
    if as_of_date is None:
        return None

    rows = reporting.run_query(NEXT_BASKET_SQL, {
        "partner_id": partner_id,
        "min_times_bought": NEXT_BASKET_MIN_TIMES_BOUGHT,
    })

    products = []
    for r in rows:
        avg_gap = float(r["avg_gap"])
        stddev_gap = float(r["stddev_gap"]) if r["stddev_gap"] is not None else None
        half_width = _window_half_width(avg_gap, stddev_gap)
        low_days = max(1.0, avg_gap - half_width)
        high_days = avg_gap + half_width

        last_date = r["last_date"]
        days_since_last = (as_of_date - last_date.date()).days

        if days_since_last < low_days:
            status = "on_track"
        elif days_since_last <= high_days:
            status = "due"
        else:
            status = "overdue"

        products.append({
            "product_id": int(r["product_id"]),
            "name": r["product_name"] or "Unknown",
            "sku": r["sku"],
            "times_bought": int(r["times_bought"]),
            "avg_gap_days": round(avg_gap, 1),
            "last_purchased": last_date.date().isoformat(),
            "days_since_last": round(days_since_last),
            "status": status,
        })

    products.sort(key=lambda p: (NEXT_BASKET_STATUS_ORDER[p["status"]], -p["times_bought"]))

    return {
        "as_of_date": as_of_date.isoformat(),
        "partner_id": partner_id,
        "products": products[:top_n],
        "total_repeat_products": len(products),
    }


# ---------------------------------------------------------------------------
# Customer search — look a customer up by name/phone/email to jump straight
# to their next-basket prediction instead of paging through the full table.
#
# Checked against the live DB before building this: email is not a usable
# lookup field for this business — only 17 of 84,896 customers have one on
# file. Phone is the reliable identifier (99.6% populated, all but 71 of
# 33,609 predictable customers have a unique one), but formats vary in the
# raw data ("+94 77 300 0003", "0773000003", "773000003" all appear) — so
# phone matching compares the last 9 digits after stripping everything but
# digits, not an exact string, or the same real customer would fail to
# match their own number typed in a different format. Name matching is a
# plain partial match (ILIKE) since names aren't unique here (27,679
# distinct names across 33,609 predictable customers) — a name search can
# and often will return more than one person; the frontend shows a picker
# rather than guessing which one was meant.
CUSTOMER_SEARCH_SQL = f"""
    SELECT rp.id AS partner_id, rp.name, rp.phone, rp.email,
           (SELECT COUNT(*) FROM sale_order so WHERE so.partner_id = rp.id AND so.state IN {SALE_ORDER_LIVE_STATES}
                AND so.store_id IN {CELESTE_STORE_IDS}) AS order_count
    FROM res_partner rp
    WHERE rp.id IN (SELECT DISTINCT partner_id FROM sale_order WHERE state IN {SALE_ORDER_LIVE_STATES}
                     AND store_id IN {CELESTE_STORE_IDS})
      AND (CAST(:name_pattern AS text) IS NULL OR rp.name ILIKE :name_pattern)
      AND (CAST(:phone_last9 AS text) IS NULL OR RIGHT(regexp_replace(COALESCE(rp.phone, ''), '[^0-9]', '', 'g'), 9) = :phone_last9)
      AND (CAST(:email AS text) IS NULL OR rp.email ILIKE :email)
    ORDER BY order_count DESC
    LIMIT :limit
"""


def search_customers(name: str | None = None, phone: str | None = None, email: str | None = None, limit: int = 10) -> list[dict]:
    """Find customers by any combination of name/phone/email — only the
    fields actually given are matched, so searching by phone alone doesn't
    require a name too. Returns an empty list (not an error) when nothing
    is given or nothing matches."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip()
    if not name and not phone and not email:
        return []

    phone_digits = "".join(c for c in phone if c.isdigit())
    rows = reporting.run_query(CUSTOMER_SEARCH_SQL, {
        "name_pattern": f"%{name}%" if name else None,
        "phone_last9": phone_digits[-9:] if len(phone_digits) >= 9 else (phone_digits or None),
        "email": email if email else None,
        "limit": limit,
    })
    return [
        {
            "partner_id": int(r["partner_id"]),
            "name": (r["name"] or "").strip() or "Unknown",
            "phone": r["phone"],
            "email": r["email"],
            "order_count": int(r["order_count"] or 0),
        }
        for r in rows
    ]
