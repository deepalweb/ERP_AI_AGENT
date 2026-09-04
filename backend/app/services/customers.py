"""Customer Intelligence: repeat-purchase segmentation, at-risk customers, and
the retention trend (the trend itself lives in intelligence.py's METRICS
registry — customer_retention_rate — so it gets anomaly detection, narrated
investigation, and AI Impact tracking for free; this module covers what that
registry entry doesn't: per-customer segmentation and the actionable at-risk list.

"Recency" is anchored to the data's own MAX(last_order), not date.today() —
the DB snapshot lags the system clock by weeks, so anchoring to the wall clock
would make every customer look artificially more idle than they are.
"""

import anyio

from backend.app.db import reporting
from mcp_server.tools import intelligence

AT_RISK_DAYS = 28   # p90 gap between orders for repeat customers
CHURNED_DAYS = 60   # ~2x that — clearly disengaged

TOP_AT_RISK = 8

CUSTOMER_STATS_SQL = """
    WITH customer_orders AS (
        SELECT so.partner_id, rp.name AS customer_name, COUNT(*) AS order_count,
               SUM(so.amount_total) AS revenue,
               MIN(so.date_order) AS first_order, MAX(so.date_order) AS last_order
        FROM sale_order so
        JOIN res_partner rp ON rp.id = so.partner_id
        WHERE so.state = 'sale'
        GROUP BY so.partner_id, rp.name
    ),
    bounds AS (SELECT MAX(last_order) AS as_of_date FROM customer_orders)
    SELECT co.*, b.as_of_date
    FROM customer_orders co CROSS JOIN bounds b
"""

SEGMENT_LABELS = {
    "active": "Active",
    "at_risk": "At risk",
    "churned": "Churned",
    "one_time": "One-time",
}


def _classify(order_count: int, days_since_last: float) -> str:
    if order_count == 1:
        return "one_time"
    if days_since_last <= AT_RISK_DAYS:
        return "active"
    if days_since_last <= CHURNED_DAYS:
        return "at_risk"
    return "churned"


async def get_customer_intelligence():
    rows = await reporting.fetch_report(CUSTOMER_STATS_SQL)

    if not rows:
        return {
            "as_of_date": None,
            "repeat_purchase_rate_pct": 0.0,
            "segments": [],
            "at_risk_customers": [],
            "retention_trend": [],
        }

    as_of_date = rows[0]["as_of_date"]

    segment_counts = {key: 0 for key in SEGMENT_LABELS}
    segment_revenue = {key: 0.0 for key in SEGMENT_LABELS}
    at_risk_rows = []

    for r in rows:
        days_since_last = (as_of_date - r["last_order"]).total_seconds() / 86400.0
        segment = _classify(int(r["order_count"]), days_since_last)
        segment_counts[segment] += 1
        segment_revenue[segment] += float(r["revenue"] or 0)

        if segment == "at_risk":
            at_risk_rows.append({
                "name": r["customer_name"] or "Unknown",
                "revenue": float(r["revenue"] or 0),
                "days_since_last_order": round(days_since_last),
            })

    total_customers = len(rows)
    repeat_customers = segment_counts["active"] + segment_counts["at_risk"] + segment_counts["churned"]

    at_risk_rows.sort(key=lambda c: c["revenue"], reverse=True)

    metric = intelligence.METRICS["customer_retention_rate"]
    trend = await anyio.to_thread.run_sync(intelligence.get_trend, metric)
    retention_trend = [
        {"month": row["month"], "retention_pct": row["value"], "is_partial": row["is_partial"]}
        for row in trend
    ]

    return {
        "as_of_date": as_of_date.date().isoformat(),
        "repeat_purchase_rate_pct": round(repeat_customers / total_customers * 100, 2) if total_customers else 0.0,
        "segments": [
            {
                "id": key,
                "label": SEGMENT_LABELS[key],
                "count": segment_counts[key],
                "revenue": round(segment_revenue[key], 2),
            }
            for key in ("active", "at_risk", "churned", "one_time")
        ],
        "at_risk_customers": at_risk_rows[:TOP_AT_RISK],
        "retention_trend": retention_trend,
    }
