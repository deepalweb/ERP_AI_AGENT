"""Customer Intelligence: repeat-purchase segmentation, at-risk customers, and
the retention trend (the trend itself lives in intelligence.py's METRICS
registry — customer_retention_rate — so it gets anomaly detection, narrated
investigation, and AI Impact tracking for free; this module covers what that
registry entry doesn't: per-customer segmentation and the actionable at-risk list.

Segmentation is DERIVED from customer_predictions.get_lifecycle_rows() -- the
same personalized-cadence classification the Customer page's lifecycle view
uses -- rather than this module's own independent day-threshold rule (the
original design, before the 2026-10-13 Customer module review found the two
could disagree: a customer could read "at risk" here, in Ask Celeste chat,
and "active" on the Customer page at the same moment, for the same person).
LIFECYCLE_TO_SEGMENT below is the only place that mapping lives.
"""

import anyio

from backend.app.db import reporting
from backend.app.services import customer_predictions
from mcp_server.tools import intelligence

TOP_AT_RISK = 8

SEGMENT_LABELS = {
    "active": "Active",
    "at_risk": "At risk",
    "churned": "Churned",
    "one_time": "One-time",
}

# win_back/excluded -> active: both are currently-transacting customers
# (win_back just placed an order after a long gap; excluded are atypically
# fast-ordering bulk accounts, never scored against a personal window in
# the first place). dormant -> one_time: the closest existing bucket for
# customers with too few lifetime orders (< customer_predictions.
# MIN_ORDER_COUNT) to ever be scored against a personal cadence.
LIFECYCLE_TO_SEGMENT = {
    "new": "active",
    "active": "active",
    "due": "at_risk",
    "lapsing": "at_risk",
    "churned": "churned",
    "win_back": "active",
    "dormant": "one_time",
    "excluded": "active",
}


async def get_customer_intelligence():
    rows, as_of_date = await anyio.to_thread.run_sync(customer_predictions.get_lifecycle_rows)

    if not rows:
        return {
            "as_of_date": None,
            "repeat_purchase_rate_pct": 0.0,
            "segments": [],
            "at_risk_customers": [],
            "retention_trend": [],
        }

    segment_counts = {key: 0 for key in SEGMENT_LABELS}
    segment_revenue = {key: 0.0 for key in SEGMENT_LABELS}
    at_risk_rows = []
    repeat_customers = 0

    for r in rows:
        segment = LIFECYCLE_TO_SEGMENT[r["lifecycle_category"]]
        revenue = float(r["revenue"] or 0)
        segment_counts[segment] += 1
        segment_revenue[segment] += revenue
        # Independent of segment membership on purpose -- "repeat customer"
        # means order_count > 1, full stop, same definition this metric has
        # always had. Deriving it from segment counts instead would quietly
        # change its meaning: a brand-new customer with exactly 1 order now
        # segments as "active" (see LIFECYCLE_TO_SEGMENT), which is correct
        # for segmentation but would wrongly count them as a repeat buyer.
        if int(r["order_count"]) > 1:
            repeat_customers += 1

        if segment == "at_risk":
            days_since_last = (as_of_date - r["last_order"]).total_seconds() / 86400.0
            at_risk_rows.append({
                "partner_id": r["partner_id"],
                "revenue": revenue,
                "days_since_last_order": round(days_since_last),
            })

    total_customers = len(rows)
    at_risk_rows.sort(key=lambda c: c["revenue"], reverse=True)
    at_risk_rows = at_risk_rows[:TOP_AT_RISK]

    names = {}
    partner_ids = [c["partner_id"] for c in at_risk_rows]
    if partner_ids:
        name_rows = await reporting.fetch_report(
            "SELECT id, name FROM res_partner WHERE id = ANY(:ids)", {"ids": partner_ids}
        )
        names = {row["id"]: row["name"] for row in name_rows}

    at_risk_customers = [
        {
            "name": names.get(c["partner_id"]) or "Unknown",
            "revenue": round(c["revenue"], 2),
            "days_since_last_order": c["days_since_last_order"],
        }
        for c in at_risk_rows
    ]

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
        "at_risk_customers": at_risk_customers,
        "retention_trend": retention_trend,
    }
