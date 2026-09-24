"""Sales channel breakdown: PickMe vs. Uber vs. direct.

`is_pickme_order` is a dead field (true on only 8 of 410,890 orders total) —
`pickme_order_id` (a real FK, not a flag) is the actual signal. Verified no
overlap between pickme_order_id and uber_order_id.
"""

from mcp_server.tools.sales import _period_bounds, _period_label, period_completeness
from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES

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
WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
  AND date_order >= :start AND date_order <= :end
GROUP BY 1
ORDER BY revenue DESC
"""


@reporting.ttl_cache(seconds=300)
def get_channel_breakdown(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    """Phase 5A: previously called sales.get_sales_summary() "reused only for
    its partial-period detection" -- that function's own revenue/orders/AOV
    and previous-period growth figures were computed (2 real queries) and
    then discarded every time, confirmed via profiling (~65% of this
    endpoint's own query time was that waste). Calls sales.period_completeness()
    instead, which computes only the 3 fields this function actually uses.
    Cached 5 minutes (reporting.ttl_cache), keyed by (period, start_date, end_date)."""
    start, end = _period_bounds(period, start_date, end_date)
    is_partial_period, days_elapsed, days_in_period = period_completeness(period, start, start_date, end_date)
    rows = reporting.run_query(CHANNEL_SQL, {"start": start, "end": end})
    total_revenue = sum(float(r["revenue"] or 0) for r in rows)
    return {
        "period": _period_label(period, start, end, start_date, end_date),
        "is_partial_period": is_partial_period,
        "days_elapsed": days_elapsed,
        "days_in_period": days_in_period,
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
