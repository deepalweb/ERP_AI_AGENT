"""Customer Next-Order Intelligence — Phase 1: per-customer purchase
cadence, an expected next-order window, and overdue detection.

Phase 1 scope only: WHEN a customer is likely to order next, and whether
they're currently on track, due, or overdue. Next-basket prediction,
an order-value range, and a prediction -> actual-outcome tracking loop
(comparing what Celeste predicted against what really happened, the same
way tracking.py and weekly_streaks.py already do for findings) are real,
separate pieces of work and are deferred to a later phase rather than
half-built here.

Same "stated rule, not a judgment call" posture as investigation.py's
_priority_for and state_findings.py's _priority_for_share: the expected
window comes directly from EACH customer's own order-gap history (their
average gap +/- their own variability), not a guessed number and not a
machine-learning model. customers.py's existing at-risk segmentation uses
one fixed 28-day threshold for every customer; this replaces that with a
window personal to each customer, since a customer who reliably orders
every 3 days and one who reliably orders every 20 shouldn't be judged
against the same number.

Only customers with order_count >= 3 (at least 2 gaps to measure) get a
prediction — checked against the live DB before building this: of
~84,900 customers, ~51,300 have only 1-2 orders and genuinely have no
individual pattern to measure yet. They're left out entirely rather than
assigned a guessed window.

Also checked against the live DB: a small number of accounts (15 of the
~9,700 with 10+ orders) place many orders per day on average — clearly
not an individual shopper's cadence (an internal/staff/test account, not
a customer). MIN_AVG_GAP_DAYS excludes these rather than silently
producing a nonsensical "expected every few hours" prediction.

This is a per-customer computation over the whole order history (~3.6s
DB query alone for ~33,600 qualifying customers) — too slow to compute
live on every page load, so it runs once a day inside brain.py's existing
daily scan and is cached into its own appdb table, read directly by the
/customers/predictions routes.
"""

import json
from datetime import date, datetime, timedelta

from sqlalchemy import text

from backend.app.db import appdb, reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

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

PATTERN_DISTRIBUTION_SQL = """
    SELECT
      CASE
        WHEN order_count >= :high_threshold THEN 'ten_plus'
        WHEN order_count >= :min_order_count THEN 'three_to_nine'
        ELSE 'one_to_two'
      END AS bucket,
      COUNT(*) AS n
    FROM (
        SELECT partner_id, COUNT(*) AS order_count
        FROM sale_order WHERE state = 'sale'
        GROUP BY partner_id
    ) x
    GROUP BY bucket
"""

TOTAL_CUSTOMERS_SQL = "SELECT COUNT(DISTINCT partner_id) AS n FROM sale_order WHERE state = 'sale'"

AS_OF_DATE_SQL = "SELECT MAX(date_order) AS as_of_date FROM sale_order WHERE state = 'sale'"

PREDICTIONS_SQL = """
    WITH ordered AS (
        SELECT so.partner_id, so.date_order,
               LAG(so.date_order) OVER (PARTITION BY so.partner_id ORDER BY so.date_order) AS prev_order,
               ROW_NUMBER() OVER (PARTITION BY so.partner_id ORDER BY so.date_order) AS rn,
               COUNT(*) OVER (PARTITION BY so.partner_id) AS n_orders
        FROM sale_order so
        WHERE so.state = 'sale'
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
        FROM sale_order so
        JOIN res_partner rp ON rp.id = so.partner_id
        WHERE so.state = 'sale'
        GROUP BY so.partner_id, rp.name
    ),
    bounds AS (SELECT MAX(last_order) AS as_of_date FROM orders)
    SELECT o.*, g.avg_gap_all, g.stddev_gap_all, g.avg_gap_recent, g.stddev_gap_recent, g.n_gaps_recent, g.n_gaps,
           b.as_of_date
    FROM orders o
    JOIN gap_stats g ON g.partner_id = o.partner_id
    CROSS JOIN bounds b
    WHERE o.order_count >= :min_order_count AND g.avg_gap_all >= :min_avg_gap_days
"""

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS customer_predictions (
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
    # customer_predictions is a pure daily-replace snapshot (record_predictions
    # deletes and reinserts every row on every scan) — no historical value in
    # existing rows, so a best-effort ALTER is enough. SQLite has no ADD
    # COLUMN IF NOT EXISTS, so guard it the same way brain.py does.
    for column in ("days_until_due INTEGER", "timing_bucket TEXT", "overdue_severity TEXT"):
        try:
            appdb.run_query(f"ALTER TABLE customer_predictions ADD COLUMN {column}")
        except Exception:
            pass
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS customer_prediction_stats (
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
        appdb.run_query("ALTER TABLE customer_prediction_stats ADD COLUMN as_of_date TEXT")
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
        conn.execute(text("DELETE FROM customer_predictions"))
        if predictions:
            conn.execute(
                text("""
                    INSERT INTO customer_predictions
                        (partner_id, name, order_count, confidence, last_order_date, avg_gap_days,
                         stddev_gap_days, expected_window_start, expected_window_end,
                         days_since_last_order, status, days_overdue, days_until_due, timing_bucket,
                         overdue_severity, revenue, avg_order_value, stddev_order_value, generated_at)
                    VALUES
                        (:partner_id, :name, :order_count, :confidence, :last_order_date, :avg_gap_days,
                         :stddev_gap_days, :expected_window_start, :expected_window_end,
                         :days_since_last_order, :status, :days_overdue, :days_until_due, :timing_bucket,
                         :overdue_severity, :revenue, :avg_order_value, :stddev_order_value, :generated_at)
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
    appdb.run_query("DELETE FROM customer_prediction_stats")
    appdb.run_query(
        """INSERT INTO customer_prediction_stats
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
        "FROM customer_predictions GROUP BY status"
    )
    confidence_rows = appdb.run_query(
        "SELECT confidence, COUNT(*) AS count FROM customer_predictions GROUP BY confidence"
    )
    timing_rows = appdb.run_query(
        "SELECT timing_bucket, COUNT(*) AS count, SUM(avg_order_value) AS expected_value "
        "FROM customer_predictions WHERE timing_bucket IS NOT NULL GROUP BY timing_bucket"
    )
    overdue_rows = appdb.run_query(
        "SELECT overdue_severity, COUNT(*) AS count, SUM(revenue) AS historical_value "
        "FROM customer_predictions WHERE overdue_severity IS NOT NULL GROUP BY overdue_severity"
    )
    high_conf_due_rows = appdb.run_query(
        "SELECT COUNT(*) AS n, SUM(avg_order_value) AS v FROM customer_predictions "
        "WHERE status = 'due' AND confidence = 'high'"
    )
    generated_rows = appdb.run_query("SELECT generated_at FROM customer_predictions LIMIT 1")
    pattern_rows = appdb.run_query("SELECT * FROM customer_prediction_stats ORDER BY generated_at DESC LIMIT 1")

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
to tune one against (same honest caveat as other untuned thresholds in
this codebase, e.g. customers.py's AT_RISK_DAYS)."""


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
        f"SELECT * FROM customer_predictions {where} ORDER BY {order} LIMIT :limit OFFSET :offset", params
    )
    total_rows = appdb.run_query(f"SELECT COUNT(*) AS n FROM customer_predictions {where}", params)
    return rows, total_rows[0]["n"]


# Scoped to Celeste's own 5 retail outlets (see store_scope.py) — unlike the
# rest of this module, which doesn't filter by store at all. Checked against
# the live DB before adding this: of 379,999 'sale' orders and 84,896
# customers system-wide, only 299,948 orders / 68,242 customers belong to
# these 5 outlets — the remaining ~20% are SPAR co-located locations and
# Budget Kade partner-fulfillment channels, explicitly called out in
# store_scope.py's own docstring as not Celeste's own retail business. This
# view is genuinely about "my 5 outlets," so it's scoped correctly; the rest
# of this module (predictions, pattern distribution, the Home finding) is
# NOT scoped this way today — a real, separate discrepancy worth fixing
# later, flagged rather than silently carried into new code.
OUTLET_BEHAVIOR_SQL = f"""
    SELECT COALESCE(rs.name, 'Unknown store') AS outlet,
           COUNT(*) FILTER (WHERE so.state = 'sale') AS orders,
           COUNT(*) FILTER (WHERE so.state = 'cancel') AS cancelled,
           COUNT(DISTINCT so.partner_id) FILTER (WHERE so.state = 'sale') AS unique_customers,
           SUM(so.amount_total) FILTER (WHERE so.state = 'sale') AS revenue,
           AVG(so.amount_total) FILTER (WHERE so.state = 'sale') AS avg_order_value
    FROM sale_order so
    LEFT JOIN res_store rs ON rs.id = so.store_id
    WHERE so.state IN ('sale', 'cancel') AND so.store_id IN {CELESTE_STORE_IDS}
    GROUP BY rs.name
    ORDER BY orders DESC
"""


DAILY_FORECAST_SQL = """
    SELECT
      SUM(1.0 / (julianday(expected_window_end) - julianday(expected_window_start) + 1)) AS expected_orders,
      SUM(avg_order_value / (julianday(expected_window_end) - julianday(expected_window_start) + 1)) AS expected_value,
      COUNT(*) AS customers_in_window
    FROM customer_predictions
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
    rows = appdb.run_query("SELECT as_of_date FROM customer_prediction_stats ORDER BY generated_at DESC LIMIT 1")
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


def get_outlet_behavior() -> list[dict]:
    """Per-outlet customer behavior for the 5 real Celeste stores — order
    count, cancellations, unique customers, and how often each customer
    orders there on average. Computed live (a ~2s aggregate over 5 stores,
    not the 33,600-customer computation the rest of this module caches
    daily) rather than added to the daily scan."""
    rows = reporting.run_query(OUTLET_BEHAVIOR_SQL)
    result = []
    for r in rows:
        orders = int(r["orders"] or 0)
        cancelled = int(r["cancelled"] or 0)
        total = orders + cancelled
        unique_customers = int(r["unique_customers"] or 0)
        result.append({
            "outlet": r["outlet"],
            "orders": orders,
            "cancelled": cancelled,
            "cancellation_rate_pct": round(cancelled / total * 100, 2) if total else 0.0,
            "unique_customers": unique_customers,
            "orders_per_customer": round(orders / unique_customers, 2) if unique_customers else 0.0,
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

LIFECYCLE_SQL = """
    WITH ordered AS (
        SELECT so.partner_id, so.date_order,
               LAG(so.date_order) OVER (PARTITION BY so.partner_id ORDER BY so.date_order) AS prev_order,
               ROW_NUMBER() OVER (PARTITION BY so.partner_id ORDER BY so.date_order) AS rn,
               COUNT(*) OVER (PARTITION BY so.partner_id) AS n_orders
        FROM sale_order so
        WHERE so.state = 'sale'
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
        FROM sale_order so
        WHERE so.state = 'sale'
        GROUP BY so.partner_id
    ),
    bounds AS (SELECT MAX(last_order) AS as_of_date FROM orders)
    SELECT o.*, g.avg_gap_all, g.stddev_gap_all, g.avg_gap_recent, g.stddev_gap_recent,
           g.last_gap, g.prior_avg_gap, g.n_prior_gaps, b.as_of_date
    FROM orders o
    LEFT JOIN gap_stats g ON g.partner_id = o.partner_id
    CROSS JOIN bounds b
"""

VALUE_PERCENTILE_SQL = """
    SELECT PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY revenue) AS p80,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY revenue) AS p50
    FROM (
        SELECT partner_id, SUM(amount_total) AS revenue
        FROM sale_order WHERE state = 'sale' GROUP BY partner_id
    ) x
"""


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
    rows = reporting.run_query(LIFECYCLE_SQL, {"recent_window_gaps": RECENT_WINDOW_GAPS})
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
    channel_stats = {
        chid: {"total": 0, "scoreable": 0, "churned": 0, "lapsing": 0, "revenue": 0.0} for chid, _, _ in CHANNELS
    }

    for row in rows:
        category = _lifecycle_category(row, as_of_date)
        revenue = float(row["revenue"] or 0)
        value_tier = "high" if revenue >= p80 else "mid" if revenue >= p50 else "low"
        channel = _primary_channel(row)

        cat_counts[category] += 1
        cat_revenue[category] += revenue
        value_counts[value_tier] += 1
        value_revenue[value_tier] += revenue
        cross[category][value_tier] += 1

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
            {"id": cid, "label": label, **cross[cid]} for cid, label, _icon in LIFECYCLE_CATEGORIES
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


_LIFECYCLE_SCHEMA_READY = False


def _ensure_lifecycle_schema():
    global _LIFECYCLE_SCHEMA_READY
    if _LIFECYCLE_SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS customer_lifecycle_stats (
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
        "SELECT summary FROM customer_lifecycle_stats ORDER BY generated_at DESC LIMIT 1"
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
        "INSERT INTO customer_lifecycle_stats (generated_at, summary) VALUES (:generated_at, :summary)",
        {"generated_at": datetime.utcnow().isoformat(), "summary": json.dumps(summary_with_deltas)},
    )


def get_lifecycle_summary() -> dict | None:
    _ensure_lifecycle_schema()
    rows = appdb.run_query("SELECT summary FROM customer_lifecycle_stats ORDER BY generated_at DESC LIMIT 1")
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
ACTUAL_DAILY_ORDERS_SQL = """
    SELECT DATE(date_order) AS day, COUNT(*) AS orders
    FROM sale_order
    WHERE state = 'sale' AND date_order >= :start_date AND date_order < :end_date
    GROUP BY DATE(date_order)
    ORDER BY day
"""


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
            "actual_orders": actual_by_day.get(d_iso, 0),
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
    }


# ---------------------------------------------------------------------------
# Outlet-wise order forecast — the company-wide daily forecast above split
# across Celeste's 5 outlets by each outlet's own real share of order
# volume over the trailing 30 days (store-scoped, unlike the forecast
# itself). Not customer-level attribution (which outlet each predictable
# customer orders from most) — that would require resolving this module's
# open store-scoping gap first (see the module docstring), a bigger piece
# of work. This is the simpler, honestly-approximate version: it assumes
# each outlet keeps its current share of volume, so it won't catch one
# outlet growing faster than another, but every number behind it (the
# shares) is real and store-scoped. Per-outlet counts are rounded
# independently for display, so they may not sum exactly to the total —
# expected, not a bug.
OUTLET_RECENT_VOLUME_SQL = f"""
    WITH bounds AS (SELECT MAX(date_order) AS as_of FROM sale_order WHERE state = 'sale')
    SELECT rs.name AS outlet, COUNT(*) AS orders_30d
    FROM sale_order so
    JOIN res_store rs ON rs.id = so.store_id
    CROSS JOIN bounds b
    WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.date_order >= b.as_of - INTERVAL '30 days'
    GROUP BY rs.name
"""


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
NEXT_BASKET_SQL = """
    WITH order_products AS (
        SELECT so.id AS order_id, sol.product_id, so.date_order
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        JOIN product_product pp ON pp.id = sol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE so.partner_id = :partner_id AND so.state = 'sale'
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
"""

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
CUSTOMER_SEARCH_SQL = """
    SELECT rp.id AS partner_id, rp.name, rp.phone, rp.email,
           (SELECT COUNT(*) FROM sale_order so WHERE so.partner_id = rp.id AND so.state = 'sale') AS order_count
    FROM res_partner rp
    WHERE rp.id IN (SELECT DISTINCT partner_id FROM sale_order WHERE state = 'sale')
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
