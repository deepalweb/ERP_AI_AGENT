"""Operations Control Center: the fulfillment & delivery timing trend (reusing
delayed_delivery_rate from intelligence.py, so it stays in sync with what
investigation.py already investigates), a company-wide operational KPI rollup,
a unified per-branch scorecard, and a fulfillment-stage bottleneck breakdown.
"""

from datetime import date, timedelta

import anyio

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from mcp_server.tools import intelligence

BRANCH_SCORECARD_SQL = f"""
WITH sales AS (
    SELECT COALESCE(rs.name, 'Unknown store') AS name,
           COUNT(*) FILTER (WHERE so.state IN {SALE_ORDER_LIVE_STATES}) AS orders,
           SUM(so.amount_total) FILTER (WHERE so.state IN {SALE_ORDER_LIVE_STATES}) AS revenue,
           COUNT(*) FILTER (WHERE so.state = 'cancel') AS cancelled,
           COUNT(*) AS total_orders
    FROM sale_order so
    LEFT JOIN res_store rs ON rs.id = so.store_id
    WHERE so.state IN {SALE_ORDER_LIVE_STATES + ("cancel",)} AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.date_order >= :start AND so.date_order < :end
    GROUP BY rs.name
),
fulfillment AS (
    SELECT COALESCE(rs.name, 'Unknown store') AS name,
           COUNT(*) AS deliveries,
           COUNT(*) FILTER (WHERE sp.date_done - sp.scheduled_date > INTERVAL '24 hours') AS delayed,
           PERCENTILE_CONT(0.5) WITHIN GROUP (
             ORDER BY EXTRACT(EPOCH FROM (sp.date_done - sp.scheduled_date)) / 60
           ) AS median_minutes
    FROM stock_picking sp
    JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
    JOIN sale_order so ON so.id = sp.sale_id
    LEFT JOIN res_store rs ON rs.id = so.store_id
    WHERE spt.code = 'outgoing' AND sp.state = 'done' AND sp.date_done IS NOT NULL
      AND so.store_id IN {CELESTE_STORE_IDS}
      AND sp.date_done >= :start AND sp.date_done < :end
    GROUP BY rs.name
)
SELECT s.name, s.orders, s.revenue, s.cancelled, s.total_orders,
       f.deliveries, f.delayed, f.median_minutes
FROM sales s
LEFT JOIN fulfillment f ON f.name = s.name
ORDER BY s.revenue DESC NULLS LAST
"""

FULFILLMENT_FUNNEL_SQL = f"""
SELECT
  COUNT(*) AS total_orders,
  COUNT(picking_start_datetime) AS picking_started,
  COUNT(picking_datetime) AS picking_done,
  COUNT(packing_start_datetime) AS packing_started,
  COUNT(packing_datetime) AS packing_done,
  COUNT(dispatch_datetime) AS dispatched,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (picking_datetime - picking_start_datetime)) / 60
  ) FILTER (WHERE picking_datetime >= picking_start_datetime) AS median_picking_min,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (packing_start_datetime - picking_datetime)) / 60
  ) FILTER (WHERE packing_start_datetime >= picking_datetime) AS median_handoff_min,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (packing_datetime - packing_start_datetime)) / 60
  ) FILTER (WHERE packing_datetime >= packing_start_datetime) AS median_packing_min,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (dispatch_datetime - packing_datetime)) / 60
  ) FILTER (WHERE dispatch_datetime >= packing_datetime) AS median_dispatch_wait_min
FROM sale_order
WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
  AND date_order >= :start AND date_order < :end
"""

OVERALL_MEDIAN_MINUTES_SQL = f"""
SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (
  ORDER BY EXTRACT(EPOCH FROM (sp.date_done - sp.scheduled_date)) / 60
) AS median_minutes
FROM stock_picking sp
JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
JOIN sale_order so ON so.id = sp.sale_id
WHERE spt.code = 'outgoing' AND sp.state = 'done' AND sp.date_done IS NOT NULL
  AND so.store_id IN {CELESTE_STORE_IDS}
  AND sp.date_done >= :start AND sp.date_done < :end
"""

# Same 4 fulfillment-stage transitions as FULFILLMENT_FUNNEL_SQL, split into
# "flagged" (the stores an investigation's WHY breakdown pointed at) vs.
# everyone else — used by investigation.py's HOW stage to show which stage of
# the funnel is actually slow at the stores driving an anomaly, instead of
# guessing at a mechanism.
STAGE_TIMING_BY_STORE_SQL = f"""
SELECT
  (rs.name = ANY(:flagged_stores)) AS is_flagged,
  COUNT(*) AS n,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (so.picking_datetime - so.picking_start_datetime)) / 60
  ) FILTER (WHERE so.picking_datetime >= so.picking_start_datetime) AS median_picking_min,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (so.packing_start_datetime - so.picking_datetime)) / 60
  ) FILTER (WHERE so.packing_start_datetime >= so.picking_datetime) AS median_handoff_min,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (so.packing_datetime - so.packing_start_datetime)) / 60
  ) FILTER (WHERE so.packing_datetime >= so.packing_start_datetime) AS median_packing_min,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (so.dispatch_datetime - so.packing_datetime)) / 60
  ) FILTER (WHERE so.dispatch_datetime >= so.packing_datetime) AS median_dispatch_wait_min
FROM sale_order so
JOIN res_store rs ON rs.id = so.store_id
WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order < :end
GROUP BY 1
"""

# picker_id is a real FK to hr_employee, populated on ~99.9% of orders
# (verified live, 2026-10-21: 519,519/520,075 all-time). packer_id is the
# same kind of column but is 0% populated company-wide -- never recorded --
# so there is no packer-side equivalent of this query.
PICKER_THROUGHPUT_SQL = f"""
SELECT COALESCE(rs.name, 'Unknown store') AS outlet, he.name AS picker_name, COUNT(*) AS orders
FROM sale_order so
LEFT JOIN res_store rs ON rs.id = so.store_id
LEFT JOIN hr_employee he ON he.id = so.picker_id
WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.picker_id IS NOT NULL
  AND so.date_order >= :start AND so.date_order < :end
GROUP BY rs.name, he.name
"""

# No per-item scan timestamp exists anywhere in this schema -- verified
# live, 2026-10-21: stock_move_line.date is identical across every line of
# an order (confirmed on 10 sampled orders, 0 exceptions), it's the bulk
# picking-completion time copied onto every line, not an incremental
# per-item scan. fresh_scan_log has real per-item timestamps but is a
# different workflow entirely (produce weighing/packing tied to
# manufacturing orders, not customer-order picking).
#
# So this is a derived estimate, not a measurement: SUM(order duration) /
# SUM(item count) across MANY orders for one picker -- aggregate ratio, not
# an average of per-order ratios (same reasoning as the stock-count
# variance rate: a single order's duration/item-count is noisy -- sampled
# orders ranged 0.7s/item to 35s/item -- so this is only shown pooled over
# a picker's whole recent history, never per order.
#
# "item" = one order line (one distinct product on the order), not one
# physical unit -- grabbing 3 units of the same SKU in one motion is
# modeled as one pick, not three.
#
# Two sanity filters, both sized against the real distribution, not a round
# number (verified live, 2026-10-21, 30-day window, 88,665 qualifying orders):
#   - Ceiling 30 min: real p99 is 865s (~14.4min); a flat 2h ceiling let 778
#     orders (0.9%) through -- a picker who leaves one order open for a
#     phone call/break without logging out would otherwise get that whole
#     gap added to their total, silently doubling their pace on a handful
#     of "ghost pause" orders. 30 min is just above 2x the real p99.
#   - Floor 10s AND >=2s/item: 10,501 orders (11.8%!) complete in under
#     10s, and 5,330 compute under 2s/item -- almost certainly cancelled-
#     mid-pick or already-staged orders rushed through, not genuine picks;
#     large enough a share that leaving them in would materially drag every
#     picker's average down.
PICKER_PACE_MAX_DURATION_S = 1800
PICKER_PACE_MIN_DURATION_S = 10
PICKER_PACE_MIN_SECONDS_PER_ITEM = 2.0

PICKER_PACE_SQL = f"""
WITH orders AS (
    SELECT so.id, so.store_id, so.picker_id,
           EXTRACT(EPOCH FROM (so.picking_datetime - so.picking_start_datetime)) AS duration_s,
           COUNT(sol.id) AS item_count
    FROM sale_order so
    JOIN sale_order_line sol ON sol.order_id = so.id AND sol.product_uom_qty > 0
    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.picker_id IS NOT NULL
      AND so.picking_start_datetime IS NOT NULL AND so.picking_datetime IS NOT NULL
      AND so.picking_datetime > so.picking_start_datetime
      AND so.date_order >= :start AND so.date_order < :end
    GROUP BY so.id, so.store_id, so.picker_id, so.picking_start_datetime, so.picking_datetime
    HAVING COUNT(sol.id) > 0
      AND EXTRACT(EPOCH FROM (so.picking_datetime - so.picking_start_datetime))
          BETWEEN {PICKER_PACE_MIN_DURATION_S} AND {PICKER_PACE_MAX_DURATION_S}
      AND EXTRACT(EPOCH FROM (so.picking_datetime - so.picking_start_datetime)) / COUNT(sol.id)
          >= {PICKER_PACE_MIN_SECONDS_PER_ITEM}
)
SELECT COALESCE(rs.name, 'Unknown store') AS outlet, he.name AS picker_name,
       SUM(o.duration_s) AS total_duration_s, SUM(o.item_count) AS total_items, COUNT(*) AS order_count
FROM orders o
LEFT JOIN res_store rs ON rs.id = o.store_id
LEFT JOIN hr_employee he ON he.id = o.picker_id
GROUP BY rs.name, he.name
"""

MIN_PACE_ORDERS = 20

STAGE_LABELS = [
    ("Picking", "median_picking_min"),
    ("Handoff to packing", "median_handoff_min"),
    ("Packing", "median_packing_min"),
    ("Waiting for dispatch", "median_dispatch_wait_min"),
]

# Same query state_findings._outlet_activity_finding() uses to build its
# finding — extracted here so evidence_engine.py's cross-metric dark-outlet
# check can call it without importing state_findings.py (which itself
# imports evidence_engine.py for its own-breakdown checks; importing each
# other back would be circular).
DARK_OUTLET_SQL = f"""
    WITH bounds AS (
        SELECT MAX(date_order) AS as_of FROM sale_order
        WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
    )
    SELECT rs.id, rs.name,
           COUNT(so.id) FILTER (WHERE so.date_order >= b.as_of - INTERVAL '30 days') AS orders_30d
    FROM res_store rs
    CROSS JOIN bounds b
    LEFT JOIN sale_order so ON so.store_id = rs.id AND so.state IN {SALE_ORDER_LIVE_STATES}
    WHERE rs.id IN {CELESTE_STORE_IDS}
    GROUP BY rs.id, rs.name
"""


def get_dark_outlets() -> list[str]:
    """Names of Celeste's own outlets with zero orders in the trailing 30
    days, right now. Sync (see get_stage_timing_by_store's docstring for
    why that's fine here too)."""
    rows = reporting.run_query(DARK_OUTLET_SQL)
    return [r["name"] for r in rows if int(r["orders_30d"] or 0) == 0]


def get_stage_timing_by_store(flagged_stores: list[str], month_key: str):
    """Sync (not async) — called from investigation.py's build_investigation(),
    which already runs inside a worker thread via anyio.to_thread.run_sync at
    the main.py level, same as every other query intelligence.py issues."""
    if not flagged_stores:
        return None
    start, end = _month_bounds(month_key)
    rows = reporting.run_query(STAGE_TIMING_BY_STORE_SQL, {"start": start, "end": end, "flagged_stores": flagged_stores})
    flagged_row = next((r for r in rows if r["is_flagged"]), None)
    baseline_row = next((r for r in rows if not r["is_flagged"]), None)
    if not flagged_row or not baseline_row:
        return None
    return {
        "stages": [
            {
                "name": label,
                "flagged_minutes": round(float(flagged_row.get(key) or 0), 2),
                "baseline_minutes": round(float(baseline_row.get(key) or 0), 2),
            }
            for label, key in STAGE_LABELS
        ]
    }


AS_OF_SQL = f"""
SELECT MAX(date_order) AS as_of FROM sale_order
WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
"""


def _trailing_window_bounds(days: int):
    """Trailing N days anchored to the latest real order (not wall-clock
    "today"), same as_of principle as operation_flow.py's FLOW_SQL -- user
    asked (2026-10-21) for the Operations page's branch/funnel/picker
    detail to cover "all days in database" rather than be dropped to the
    last complete calendar month, which is what _month_bounds gave it."""
    rows = reporting.run_query(AS_OF_SQL)
    as_of = rows[0]["as_of"] if rows else None
    if as_of is None:
        return None, None, None
    as_of_date = as_of.date()
    start = as_of_date - timedelta(days=days - 1)
    end = as_of_date + timedelta(days=1)
    return start.isoformat(), end.isoformat(), as_of_date


def _month_bounds(month_key: str):
    year, month = map(int, month_key.split("-"))
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def _headline_from_trend(trend: list[dict]):
    """Last complete month's value + growth vs the month before it — the same
    'don't compare a partial month' rule used throughout the app."""
    complete = [row for row in trend if not row["is_partial"]]
    headline = complete[-1] if complete else (trend[-1] if trend else None)
    if not headline:
        return None, None, None

    idx = trend.index(headline)
    previous = trend[idx - 1] if idx > 0 else None
    growth_pct = None
    if previous and previous["value"]:
        growth_pct = round((headline["value"] - previous["value"]) / previous["value"] * 100, 2)
    return headline["value"], growth_pct, headline["month"]


def _merge_picker_pace(picker_rows: list[dict], pace_rows: list[dict]) -> list[dict]:
    """Pairs each picker's order count with their avg_seconds_per_item (None
    below MIN_PACE_ORDERS -- too few orders to trust an average, same
    reasoning as inventory.py's MIN_RELIABLE_UNITS_90D). Pure/sync so it's
    testable without mocking the reporting DB."""
    pace_by_key = {
        (r["outlet"] or "Unknown store", r["picker_name"] or "Unresolved picker"): r
        for r in pace_rows
    }
    by_outlet: dict[str, list] = {}
    for r in picker_rows:
        outlet = r["outlet"] or "Unknown store"
        name = r["picker_name"] or "Unresolved picker"
        pace = pace_by_key.get((outlet, name))
        avg_seconds_per_item = None
        pace_order_count = int(pace["order_count"] or 0) if pace else 0
        if pace and pace_order_count >= MIN_PACE_ORDERS:
            total_items = float(pace["total_items"] or 0)
            if total_items > 0:
                avg_seconds_per_item = round(float(pace["total_duration_s"]) / total_items, 1)
        by_outlet.setdefault(outlet, []).append({
            "name": name, "orders": int(r["orders"] or 0),
            "avg_seconds_per_item": avg_seconds_per_item,
            "pace_order_count": pace_order_count,
        })
    return [
        {"outlet": outlet, "pickers": sorted(rows, key=lambda row: -row["orders"])[:10]}
        for outlet, rows in sorted(by_outlet.items())
    ]


def _add_performance_scores(outlet_groups: list[dict]) -> list[dict]:
    """A single relative performance percentage per picker, scored against
    the best picker in the SAME outlet group that month (not a fixed
    target -- none exists in the data) -- user asked for this 2026-11-03.

    Volume score: this picker's orders / the group's best order count.
    Pace score: the group's best (lowest) avg_seconds_per_item / this
    picker's own -- inverted because lower is better -- scored ONLY
    against pickers who cleared MIN_PACE_ORDERS in _merge_picker_pace
    above. Comparing against a pace figure already too noisy to show
    (avg_seconds_per_item is None) would just move that same problem one
    step downstream into a false-precision percentage.

    Combined score averages whichever of the two are actually available
    for this picker. A picker below MIN_PACE_ORDERS has no pace score, so
    their combined score silently becomes volume-only -- combined_basis
    says which, so a caller never presents a partial number as if it were
    the full picture."""
    for group in outlet_groups:
        pickers = group["pickers"]
        best_orders = max((p["orders"] for p in pickers), default=0)
        reliable_paces = [p["avg_seconds_per_item"] for p in pickers if p["avg_seconds_per_item"] is not None]
        best_pace = min(reliable_paces) if reliable_paces else None
        for p in pickers:
            volume_pct = round(p["orders"] / best_orders * 100, 1) if best_orders else None
            pace_pct = (
                round(best_pace / p["avg_seconds_per_item"] * 100, 1)
                if best_pace is not None and p["avg_seconds_per_item"] is not None
                else None
            )
            p["volume_score_pct"] = volume_pct
            p["pace_score_pct"] = pace_pct
            if volume_pct is not None and pace_pct is not None:
                p["combined_score_pct"] = round((volume_pct + pace_pct) / 2, 1)
                p["combined_score_basis"] = "volume_and_pace"
            elif volume_pct is not None:
                p["combined_score_pct"] = volume_pct
                p["combined_score_basis"] = "volume_only"
            else:
                p["combined_score_pct"] = None
                p["combined_score_basis"] = None
    return outlet_groups


@reporting.ttl_cache(seconds=300)
async def get_fulfillment_summary():
    """Phase 5A: profiling found this endpoint issuing 7 real queries fully
    SEQUENTIALLY (each awaited before the next started) despite every one
    of them being independent -- confirmed the dominant cost (17.9s/8.8s
    wall across two profiling runs, ~= the sum of all 7 query times, with
    zero overlap). Reworked below to run all 7 concurrently via the same
    anyio.create_task_group() pattern dashboard.get_summary() already
    uses, so wall time drops toward the SLOWEST single query instead of
    their sum. Also cached 5 minutes (reporting.ttl_cache) -- no params,
    same freshness reasoning as dashboard.get_summary."""
    metric = intelligence.METRICS["delayed_delivery_rate"]

    trends: dict = {}

    async def _trend(key, metric_def):
        trends[key] = await anyio.to_thread.run_sync(intelligence.get_trend, metric_def)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_trend, "delayed_delivery_rate", metric)
        # Operational Control Center rollup: the same already-registered
        # metrics Overview surfaces company-wide, reused here so an ops
        # manager doesn't have to cross-reference the other page to see
        # the full operational picture. Independent of the trend above --
        # no reason to wait for it first.
        tg.start_soon(_trend, "cancellation_rate", intelligence.METRICS["cancellation_rate"])
        tg.start_soon(_trend, "revenue", intelligence.METRICS["revenue"])
        tg.start_soon(_trend, "order_volume", intelligence.METRICS["order_volume"])

    trend = trends["delayed_delivery_rate"]
    cancellation_trend = trends["cancellation_rate"]
    revenue_trend = trends["revenue"]
    order_volume_trend = trends["order_volume"]

    months = [
        {
            "month": row["month"],
            "delayed_rate_pct": round(row["value"], 2),
            "delayed_count": int(row["numerator"]),
            "total": int(row["denominator"]),
            "is_partial": row["is_partial"],
        }
        for row in trend
    ]

    complete_months = [m for m, row in zip(months, trend) if not row["is_partial"]]
    headline = complete_months[-1] if complete_months else (months[-1] if months else None)
    previous = None
    if headline:
        idx = months.index(headline)
        previous = months[idx - 1] if idx > 0 else None

    headline_month = headline["month"] if headline else None
    delayed_rate_growth_pct = None
    if headline and previous and previous["delayed_rate_pct"]:
        delayed_rate_growth_pct = round(
            (headline["delayed_rate_pct"] - previous["delayed_rate_pct"]) / previous["delayed_rate_pct"] * 100, 2
        )

    headline_cancellation_rate_pct, cancellation_rate_growth_pct, _ = _headline_from_trend(cancellation_trend)
    headline_revenue, revenue_growth_pct, _ = _headline_from_trend(revenue_trend)
    headline_orders, orders_growth_pct, _ = _headline_from_trend(order_volume_trend)

    median_minutes = 0.0
    branches = []
    funnel_stages = []
    funnel_wait_times = []
    detail_window_start, detail_window_end, detail_window_as_of = _trailing_window_bounds(30)
    detail_window_label = None
    detail_orders = 0
    detail_revenue = 0.0
    detail_cancellation_rate_pct = 0.0
    detail_delayed_rate_pct = 0.0
    detail_delayed_count = 0
    if detail_window_as_of:
        start, end = detail_window_start, detail_window_end
        detail_window_label = f"{date.fromisoformat(start):%b %d} – {detail_window_as_of:%b %d, %Y}"
        sql_results: dict = {}

        async def _sql(key, query):
            rows = await reporting.fetch_report(query, {"start": start, "end": end})
            sql_results[key] = rows

        async with anyio.create_task_group() as tg:
            tg.start_soon(_sql, "median", OVERALL_MEDIAN_MINUTES_SQL)
            tg.start_soon(_sql, "branch", BRANCH_SCORECARD_SQL)
            tg.start_soon(_sql, "funnel", FULFILLMENT_FUNNEL_SQL)

        median_rows = sql_results["median"]
        median_minutes = float((median_rows[0].get("median_minutes") if median_rows else 0) or 0)

        branch_rows = sql_results["branch"]
        branches = [
            {
                "name": r["name"],
                "revenue": float(r["revenue"] or 0),
                "orders": int(r["orders"] or 0),
                "cancellation_rate_pct": round(r["cancelled"] / r["total_orders"] * 100, 2) if r["total_orders"] else 0.0,
                "delayed_rate_pct": round(r["delayed"] / r["deliveries"] * 100, 2) if r["deliveries"] else 0.0,
                "median_minutes": round(float(r["median_minutes"] or 0), 1),
            }
            for r in branch_rows
        ]
        detail_orders = sum(int(r["orders"] or 0) for r in branch_rows)
        detail_revenue = round(sum(float(r["revenue"] or 0) for r in branch_rows), 2)
        total_orders_sum = sum(int(r["total_orders"] or 0) for r in branch_rows)
        cancelled_sum = sum(int(r["cancelled"] or 0) for r in branch_rows)
        deliveries_sum = sum(int(r["deliveries"] or 0) for r in branch_rows)
        delayed_sum = sum(int(r["delayed"] or 0) for r in branch_rows)
        detail_cancellation_rate_pct = round(cancelled_sum / total_orders_sum * 100, 2) if total_orders_sum else 0.0
        detail_delayed_rate_pct = round(delayed_sum / deliveries_sum * 100, 2) if deliveries_sum else 0.0
        detail_delayed_count = delayed_sum

        funnel_rows = sql_results["funnel"]
        f = funnel_rows[0] if funnel_rows else {}
        total = int(f.get("total_orders") or 0)

        def _stage(label, key):
            count = int(f.get(key) or 0)
            return {"name": label, "count": count, "pct": round(count / total * 100, 1) if total else 0.0}

        funnel_stages = [
            _stage("Picking started", "picking_started"),
            _stage("Picking done", "picking_done"),
            _stage("Packing started", "packing_started"),
            _stage("Packing done", "packing_done"),
            _stage("Dispatched", "dispatched"),
        ]
        funnel_wait_times = [
            {"name": "Picking", "minutes": round(float(f.get("median_picking_min") or 0), 2)},
            {"name": "Handoff to packing", "minutes": round(float(f.get("median_handoff_min") or 0), 2)},
            {"name": "Packing", "minutes": round(float(f.get("median_packing_min") or 0), 2)},
            {"name": "Waiting for dispatch", "minutes": round(float(f.get("median_dispatch_wait_min") or 0), 2)},
        ]

    return {
        "headline_month": headline_month,
        "headline_delayed_rate_pct": headline["delayed_rate_pct"] if headline else 0.0,
        "headline_delayed_count": headline["delayed_count"] if headline else 0,
        "headline_total": headline["total"] if headline else 0,
        "delayed_rate_growth_pct": delayed_rate_growth_pct,
        "median_fulfillment_minutes": round(median_minutes, 1),
        "headline_revenue": headline_revenue or 0.0,
        "revenue_growth_pct": revenue_growth_pct,
        "headline_orders": headline_orders or 0,
        "orders_growth_pct": orders_growth_pct,
        "headline_cancellation_rate_pct": headline_cancellation_rate_pct or 0.0,
        "cancellation_rate_growth_pct": cancellation_rate_growth_pct,
        "months": months,
        "branches": branches,
        "funnel_stages": funnel_stages,
        "funnel_wait_times": funnel_wait_times,
        "detail_window_start": detail_window_start,
        "detail_window_end": detail_window_end,
        "detail_window_label": detail_window_label,
        "detail_orders": detail_orders,
        "detail_revenue": detail_revenue,
        "detail_cancellation_rate_pct": detail_cancellation_rate_pct,
        "detail_delayed_rate_pct": detail_delayed_rate_pct,
        "detail_delayed_count": detail_delayed_count,
    }


async def get_picker_summary(month: str | None = None) -> dict:
    """Picker order volume & pace, one calendar month (1st through its last
    day) at a time -- decoupled from get_fulfillment_summary()'s own 30-day
    trailing window (branches/funnel/median) so switching months here never
    re-runs those other five queries.

    month defaults to the month containing the LATEST available order, not
    wall-clock "today" -- same as_of principle every other window in this
    file already uses, and the one genuinely confirmed live this session
    (2026-10-31): the reporting mirror can lag the real calendar by days,
    so a wall-clock default could silently default to a month with zero
    data in it yet."""
    as_of_rows = reporting.run_query(AS_OF_SQL)
    as_of = as_of_rows[0]["as_of"] if as_of_rows else None
    if as_of is None:
        return {"month": month, "window_label": None, "is_current_month": False, "pickers_by_outlet": []}

    as_of_date = as_of.date()
    month = month or as_of_date.strftime("%Y-%m")
    start, end = _month_bounds(month)
    month_date = date.fromisoformat(start)
    is_current_month = as_of_date < date.fromisoformat(end)
    last_included_day = min(as_of_date, date.fromisoformat(end) - timedelta(days=1))
    window_label = (
        f"{month_date:%b %Y} (through {last_included_day.day})" if is_current_month else f"{month_date:%b %Y}"
    )

    sql_results: dict = {}

    async def _sql(key, query):
        sql_results[key] = await reporting.fetch_report(query, {"start": start, "end": end})

    async with anyio.create_task_group() as tg:
        tg.start_soon(_sql, "pickers", PICKER_THROUGHPUT_SQL)
        tg.start_soon(_sql, "picker_pace", PICKER_PACE_SQL)

    return {
        "month": month,
        "window_label": window_label,
        "is_current_month": is_current_month,
        "pickers_by_outlet": _add_performance_scores(
            _merge_picker_pace(sql_results["pickers"], sql_results["picker_pace"])
        ),
    }
