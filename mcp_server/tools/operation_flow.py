"""Order-stage data quality and recurring fulfillment-flow signals.

These checks use the stage timestamps already stored on sale_order. They
describe process flow at an outlet; they do not identify picker/packer
headcount or attribute delays to a person. The reporting feed has no sync
watermark, so measurements remain freshness-unknown.
"""

from collections import defaultdict
from datetime import date

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from mcp_server.tools import evidence_engine, impact_engine, measurement

FLOW_SQL = f"""
WITH bounds AS (
    SELECT MAX(date_order) AS as_of FROM sale_order
    WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
), stage_rows AS (
    SELECT so.id AS order_id, so.store_id, rs.name AS outlet,
           so.date_order::date AS order_day, s.stage,
           EXTRACT(EPOCH FROM (s.finished_at - s.started_at)) / 60.0 AS minutes
    FROM sale_order so
    JOIN res_store rs ON rs.id = so.store_id
    CROSS JOIN bounds b
    CROSS JOIN LATERAL (VALUES
        ('Picking', so.picking_start_datetime, so.picking_datetime),
        ('Handoff to packing', so.picking_datetime, so.packing_start_datetime),
        ('Packing', so.packing_start_datetime, so.packing_datetime),
        ('Waiting for dispatch', so.packing_datetime, so.dispatch_datetime)
    ) AS s(stage, started_at, finished_at)
    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.date_order >= b.as_of - INTERVAL '34 days' AND so.date_order <= b.as_of
      AND s.started_at IS NOT NULL AND s.finished_at IS NOT NULL
      AND s.finished_at >= s.started_at
      AND s.finished_at < s.started_at + INTERVAL '24 hours'
), baseline AS (
    SELECT store_id, outlet, stage, COUNT(*) AS sample,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY minutes) AS p95_minutes
    FROM stage_rows, bounds
    WHERE order_day >= (as_of - INTERVAL '34 days')::date
      AND order_day < (as_of - INTERVAL '6 days')::date
    GROUP BY store_id, outlet, stage
), recent_daily AS (
    SELECT store_id, outlet, stage, order_day, COUNT(*) AS sample,
           PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY minutes) AS p90_minutes
    FROM stage_rows, bounds
    WHERE order_day >= (as_of - INTERVAL '6 days')::date
    GROUP BY store_id, outlet, stage, order_day
)
SELECT d.store_id, d.outlet, d.stage, d.order_day, d.sample AS daily_sample,
       d.p90_minutes AS daily_p90_minutes, b.sample AS baseline_sample,
       b.p95_minutes AS baseline_p95_minutes, bounds.as_of
FROM recent_daily d
JOIN baseline b USING (store_id, outlet, stage)
CROSS JOIN bounds
ORDER BY d.outlet, d.stage, d.order_day
"""

STAGE_QUALITY_SQL = f"""
WITH bounds AS (
    SELECT MAX(date_order) AS as_of FROM sale_order
    WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
)
SELECT b.as_of, COUNT(*) AS order_count,
       COUNT(*) FILTER (WHERE
          (picking_start_datetime IS NOT NULL AND picking_datetime IS NOT NULL
             AND picking_datetime < picking_start_datetime)
          OR (picking_datetime IS NOT NULL AND packing_start_datetime IS NOT NULL
             AND packing_start_datetime < picking_datetime)
          OR (packing_start_datetime IS NOT NULL AND packing_datetime IS NOT NULL
             AND packing_datetime < packing_start_datetime)
          OR (packing_datetime IS NOT NULL AND dispatch_datetime IS NOT NULL
             AND dispatch_datetime < packing_datetime)
       ) AS out_of_order_orders,
       COUNT(*) FILTER (WHERE state IN ('done', 'received') AND (
          (picking_datetime IS NOT NULL AND picking_start_datetime IS NULL)
          OR (packing_start_datetime IS NOT NULL AND picking_datetime IS NULL)
          OR (packing_datetime IS NOT NULL AND packing_start_datetime IS NULL)
          OR (dispatch_datetime IS NOT NULL AND packing_datetime IS NULL)
       )) AS unmatched_stage_orders,
       COUNT(*) FILTER (WHERE
          (picking_start_datetime IS NOT NULL AND picking_datetime IS NOT NULL
             AND picking_datetime < picking_start_datetime)
          OR (picking_datetime IS NOT NULL AND packing_start_datetime IS NOT NULL
             AND packing_start_datetime < picking_datetime)
          OR (packing_start_datetime IS NOT NULL AND packing_datetime IS NOT NULL
             AND packing_datetime < packing_start_datetime)
          OR (packing_datetime IS NOT NULL AND dispatch_datetime IS NOT NULL
             AND dispatch_datetime < packing_datetime)
          OR (state IN ('done', 'received') AND (
             (picking_datetime IS NOT NULL AND picking_start_datetime IS NULL)
             OR (packing_start_datetime IS NOT NULL AND picking_datetime IS NULL)
             OR (packing_datetime IS NOT NULL AND packing_start_datetime IS NULL)
             OR (dispatch_datetime IS NOT NULL AND packing_datetime IS NULL)
          ))
       ) AS orders_with_any_issue
FROM sale_order so
CROSS JOIN bounds b
WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= b.as_of - INTERVAL '30 days' AND so.date_order <= b.as_of
GROUP BY b.as_of
"""

# A finding requires a substantial sample and repeated deviation, not one
# slow order. The 10-minute floor and 5-minute rise protect against tiny
# percentage swings on stages whose normal duration is near zero.
MIN_BASELINE_ORDERS = 100
MIN_RECENT_ORDERS = 50
MIN_DAILY_ORDERS = 10
MIN_REPEATED_DAYS = 3
MIN_BOTTLENECK_P90_MINUTES = 10.0
MIN_BOTTLENECK_INCREASE_MINUTES = 5.0
BOTTLENECK_MULTIPLE = 2.0
MIN_QUALITY_ISSUE_SHARE = 0.005
MIN_QUALITY_ISSUE_ORDERS = 5


def _number(value):
    return float(value) if value is not None else 0.0


def _iso_date(value):
    return value.date().isoformat() if hasattr(value, "date") else str(value)[:10]


def _flow_rows():
    rows = reporting.run_query(FLOW_SQL)
    if rows:
        measurement.source_as_of(
            _iso_date(rows[0]["as_of"]), verifies_source=False,
            unknown_reason="Order-stage event dates do not confirm when the reporting mirror was refreshed.",
        )
    else:
        measurement.source_unavailable("No order-stage records are available for the analysis window.")
    return rows


def _repeated_bottlenecks(flow_rows, stages):
    grouped = defaultdict(list)
    for row in flow_rows:
        if row["stage"] in stages:
            grouped[(row["store_id"], row["outlet"], row["stage"])].append(row)

    flagged = []
    for (_, outlet, stage), rows in grouped.items():
        baseline_n = int(rows[0]["baseline_sample"] or 0)
        baseline_p95 = _number(rows[0]["baseline_p95_minutes"])
        recent_n = sum(int(row["daily_sample"] or 0) for row in rows)
        if baseline_n < MIN_BASELINE_ORDERS or recent_n < MIN_RECENT_ORDERS:
            continue
        threshold = max(
            MIN_BOTTLENECK_P90_MINUTES,
            baseline_p95 * BOTTLENECK_MULTIPLE,
            baseline_p95 + MIN_BOTTLENECK_INCREASE_MINUTES,
        )
        slow_days = [
            row for row in rows
            if int(row["daily_sample"] or 0) >= MIN_DAILY_ORDERS
            and _number(row["daily_p90_minutes"]) >= threshold
        ]
        if len(slow_days) < MIN_REPEATED_DAYS:
            continue
        flagged.append({
            "outlet": outlet,
            "stage": stage,
            "recent_orders": recent_n,
            "baseline_orders": baseline_n,
            "baseline_p95_minutes": round(baseline_p95, 1),
            "recent_p90_minutes": round(max(_number(row["daily_p90_minutes"]) for row in slow_days), 1),
            "threshold_minutes": round(threshold, 1),
            "slow_days": len(slow_days),
            "days_observed": len({str(row["order_day"]) for row in rows}),
        })
    return flagged


def _flow_finding(metric_id, title, rows, as_of, detail, unknown_detail, *, current=None):
    current = len(rows) if current is None else current
    outlet_totals = defaultdict(float)
    stage_totals = defaultdict(float)
    issue_totals = defaultdict(float)
    for row in rows:
        if "count" in row:
            issue_totals[row["issue"]] += row["count"]
        else:
            outlet_totals[row["outlet"]] += row["recent_p90_minutes"]
            stage_totals[row["stage"]] += row["recent_p90_minutes"]
    checks = [
        {"source": "observed_flow", "verdict": "confirmed", "detail": detail},
        {"source": "cause", "verdict": "unknown", "detail": unknown_detail},
    ]
    if issue_totals:
        breakdowns = [{
            "dimension": "data_quality_check", "label": "Timestamp checks", "mode": "contribution",
            "baseline_month": None,
            "top_contributors": sorted(
                ({"name": name, "current": value} for name, value in issue_totals.items()),
                key=lambda item: -item["current"],
            ),
        }]
    else:
        breakdowns = [
            {"dimension": "outlet", "label": "Affected Celeste outlet", "mode": "contribution",
             "baseline_month": None,
             "top_contributors": sorted(
                 ({"name": name, "current": value} for name, value in outlet_totals.items()),
                 key=lambda item: -item["current"],
             )},
            {"dimension": "stage", "label": "Affected fulfillment stage", "mode": "contribution",
             "baseline_month": None,
             "top_contributors": sorted(
                 ({"name": name, "current": value} for name, value in stage_totals.items()),
                 key=lambda item: -item["current"],
             )},
        ]
    return {
        "id": f"{metric_id}-{str(as_of)[:10]}",
        "metric_id": metric_id,
        "domain": "delivery",
        "title": title,
        "category": "critical",
        "severity": "medium",
        "priority": "medium",
        "business_impact_score": impact_engine.business_impact_score(
            "medium", {"consecutive_periods": 1}, {"count": current}, None,
        ),
        "detected": date.today().isoformat(),
        "confirm": {"period": str(as_of)[:10], "current": current, "baseline": None,
                    "pct_change": None, "unit": "affected orders" if issue_totals else "outlet-stage pairs"},
        "breakdowns": breakdowns,
        "impact": {"kind": "operation_flow", "financial_impact_rs": None, "count": current,
                   "detail": detail, "confidence": "measured", "month": str(as_of)[:10]},
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "recommendations": [{
            "action": "Review the listed outlet-stage evidence and confirm it against the local operating process.",
            "owner": "Operations team", "priority": "medium",
            "expected_benefit": "Identifies which fulfillment stage needs a process review.",
        }],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "operation_flow_rows": rows,
    }


def _stage_bottleneck_finding():
    flow_rows = _flow_rows()
    rows = _repeated_bottlenecks(flow_rows, {"Picking", "Handoff to packing", "Packing"})
    if not rows:
        return None
    as_of = _iso_date(flow_rows[0]["as_of"])
    detail = (
        f"{len(rows)} outlet/stage pair(s) had a daily p90 duration of at least "
        f"{MIN_BOTTLENECK_P90_MINUTES:g} minutes and twice the outlet's prior 28-day p95, "
        f"on at least {MIN_REPEATED_DAYS} of the latest 7 days."
    )
    finding = _flow_finding(
        "operation_flow_stage_bottleneck", "Repeated delay in picking, handoff, or packing",
        rows, as_of, detail,
        "Not established: whether the delay reflects workload, staffing, equipment, process, or timestamp capture. "
        "Packer identity is never recorded. Picker identity is recorded per order but is not joined into this "
        "stage-timing measure, so it does not identify which picker handled a slow order.",
    )
    return finding


def _dispatch_wait_finding():
    flow_rows = _flow_rows()
    rows = _repeated_bottlenecks(flow_rows, {"Waiting for dispatch"})
    if not rows:
        return None
    as_of = _iso_date(flow_rows[0]["as_of"])
    detail = (
        f"{len(rows)} outlet/stage pair(s) had repeated long waits between packing completion and dispatch: "
        f"at least {MIN_BOTTLENECK_P90_MINUTES:g} minutes and twice the prior 28-day p95 on "
        f"at least {MIN_REPEATED_DAYS} of the latest 7 days."
    )
    return _flow_finding(
        "operation_flow_dispatch_wait", "Repeated wait after packing before dispatch",
        rows, as_of, detail,
        "Not established: whether the wait is caused by dispatch capacity, courier availability, batching, or timestamp capture.",
    )


def _stage_data_quality_finding():
    rows = reporting.run_query(STAGE_QUALITY_SQL)
    row = rows[0] if rows else None
    if not row or row["as_of"] is None:
        measurement.source_unavailable("Order-stage records have no observation date.")
        return None
    as_of = row["as_of"]
    period = _iso_date(as_of)
    measurement.source_as_of(
        period, verifies_source=False,
        unknown_reason="Order-stage event dates do not confirm when the reporting mirror was refreshed.",
    )
    count = int(row["order_count"] or 0)
    out_of_order = int(row["out_of_order_orders"] or 0)
    unmatched = int(row["unmatched_stage_orders"] or 0)
    issue_count = int(row["orders_with_any_issue"] or 0)
    if count < 200 or issue_count < MIN_QUALITY_ISSUE_ORDERS or issue_count / count < MIN_QUALITY_ISSUE_SHARE:
        return None
    issue_rows = [
        {"issue": "Timestamps out of sequence", "count": out_of_order},
        {"issue": "Completed orders with unmatched stages", "count": unmatched},
    ]
    detail = (
        f"{issue_count} of {count:,} Celeste orders in the latest 30-day window have stage timestamps "
        f"that run backwards or completed orders with a later stage but no recorded preceding stage "
        f"({issue_count / count:.1%})."
    )
    finding = _flow_finding(
        "operation_flow_stage_data_quality", "Order fulfillment stage records need review",
        issue_rows, period, detail,
        "Not established: whether these are process exceptions or missing/inconsistent timestamp capture. They are not evidence of employee performance.",
        current=issue_count,
    )
    finding["recommendations"] = [{
        "action": "Review the affected order-stage records and confirm how the picking, packing, and dispatch timestamps are captured.",
        "owner": "Operations and systems teams", "priority": "medium",
        "expected_benefit": "Separates real process exceptions from incomplete or inconsistent event data.",
    }]
    return finding


def build_operation_flow_findings():
    """Measure timestamp quality, repeated stage bottlenecks, and dispatch waits."""
    return measurement.combine([
        measurement.run_snapshot(
            "operation_flow_stage_data_quality", _stage_data_quality_finding,
            unknown_freshness_is_measured=True,
        ),
        measurement.run_snapshot(
            "operation_flow_stage_bottleneck", _stage_bottleneck_finding,
            unknown_freshness_is_measured=True,
        ),
        measurement.run_snapshot(
            "operation_flow_dispatch_wait", _dispatch_wait_finding,
            unknown_freshness_is_measured=True,
        ),
    ])
