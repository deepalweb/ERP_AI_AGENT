"""Sales channel breakdown: PickMe vs. Uber vs. direct.

`is_pickme_order` is a dead field (true on only 8 of 410,890 orders total) —
`pickme_order_id` (a real FK, not a flag) is the actual signal. Verified no
overlap between pickme_order_id and uber_order_id.
"""

from mcp_server.tools.sales import _period_bounds, get_sales_summary
from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

CHANNEL_SQL = f"""
SELECT
    CASE
        WHEN pickme_order_id IS NOT NULL THEN 'PickMe'
        WHEN uber_order_id IS NOT NULL THEN 'Uber'
        ELSE 'Direct'
    END AS channel,
    COUNT(*) AS orders,
    SUM(amount_total) AS revenue
FROM sale_order
WHERE state IN ('sale', 'done') AND store_id IN {CELESTE_STORE_IDS}
  AND date_order >= :start AND date_order <= :end
GROUP BY 1
ORDER BY revenue DESC
"""


def get_channel_breakdown(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    start, end = _period_bounds(period, start_date, end_date)
    summary = get_sales_summary(period, start_date, end_date)  # reused only for its partial-period detection
    rows = reporting.run_query(CHANNEL_SQL, {"start": start, "end": end})
    total_revenue = sum(float(r["revenue"] or 0) for r in rows)
    return {
        "period": summary["period"],
        "is_partial_period": summary["is_partial_period"],
        "days_elapsed": summary["days_elapsed"],
        "days_in_period": summary["days_in_period"],
        "channels": [
            {
                "name": r["channel"],
                "orders": int(r["orders"] or 0),
                "revenue": float(r["revenue"] or 0),
                "revenue_share_pct": round(float(r["revenue"] or 0) / total_revenue * 100, 1)
                if total_revenue
                else 0.0,
            }
            for r in rows
        ],
    }
