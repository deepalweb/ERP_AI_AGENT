"""Operations Control Center: the fulfillment & delivery timing trend (reusing
delayed_delivery_rate from intelligence.py, so it stays in sync with what
investigation.py already investigates), a company-wide operational KPI rollup,
a unified per-branch scorecard, and a fulfillment-stage bottleneck breakdown.
"""

from datetime import date

import anyio

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from mcp_server.tools import intelligence

BRANCH_SCORECARD_SQL = f"""
WITH sales AS (
    SELECT COALESCE(rs.name, 'Unknown store') AS name,
           COUNT(*) FILTER (WHERE so.state = 'sale') AS orders,
           SUM(so.amount_total) FILTER (WHERE so.state = 'sale') AS revenue,
           COUNT(*) FILTER (WHERE so.state = 'cancel') AS cancelled,
           COUNT(*) AS total_orders
    FROM sale_order so
    LEFT JOIN res_store rs ON rs.id = so.store_id
    WHERE so.state IN ('sale', 'cancel') AND so.store_id IN {CELESTE_STORE_IDS}
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
WHERE state = 'sale' AND store_id IN {CELESTE_STORE_IDS}
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
WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order < :end
GROUP BY 1
"""

STAGE_LABELS = [
    ("Picking", "median_picking_min"),
    ("Handoff to packing", "median_handoff_min"),
    ("Packing", "median_packing_min"),
    ("Waiting for dispatch", "median_dispatch_wait_min"),
]


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


async def get_fulfillment_summary():
    metric = intelligence.METRICS["delayed_delivery_rate"]
    trend = await anyio.to_thread.run_sync(intelligence.get_trend, metric)

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

    # Operational Control Center rollup: the same already-registered metrics
    # Overview surfaces company-wide, reused here so an ops manager doesn't have
    # to cross-reference the other page to see the full operational picture.
    cancellation_trend = await anyio.to_thread.run_sync(
        intelligence.get_trend, intelligence.METRICS["cancellation_rate"]
    )
    revenue_trend = await anyio.to_thread.run_sync(intelligence.get_trend, intelligence.METRICS["revenue"])
    order_volume_trend = await anyio.to_thread.run_sync(intelligence.get_trend, intelligence.METRICS["order_volume"])

    headline_cancellation_rate_pct, cancellation_rate_growth_pct, _ = _headline_from_trend(cancellation_trend)
    headline_revenue, revenue_growth_pct, _ = _headline_from_trend(revenue_trend)
    headline_orders, orders_growth_pct, _ = _headline_from_trend(order_volume_trend)

    median_minutes = 0.0
    branches = []
    funnel_stages = []
    funnel_wait_times = []
    if headline_month:
        start, end = _month_bounds(headline_month)
        median_rows = await reporting.fetch_report(OVERALL_MEDIAN_MINUTES_SQL, {"start": start, "end": end})
        median_minutes = float((median_rows[0].get("median_minutes") if median_rows else 0) or 0)

        branch_rows = await reporting.fetch_report(BRANCH_SCORECARD_SQL, {"start": start, "end": end})
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

        funnel_rows = await reporting.fetch_report(FULFILLMENT_FUNNEL_SQL, {"start": start, "end": end})
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
    }
