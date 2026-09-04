"""Uber lost-sale tracking: items Uber removed from an order after placement
(typically out-of-stock at pick time).

ue_item_removed is the real, Uber-specific signal — verified non-trivial and
consistent (6,766 of 588,003 July lines). product_uom_qty is zeroed out on a
removed line, so exact lost revenue can't be reconstructed; price_unit survives,
so the revenue estimate here assumes 1 unit per removed line and is reported
as an estimate, not an exact figure.
"""

from mcp_server.tools.sales import _period_bounds, get_sales_summary
from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

TOP_N = 8

SUMMARY_SQL = f"""
SELECT
    COUNT(*) FILTER (WHERE sol.ue_item_removed = true) AS removed_lines,
    COUNT(*) AS total_lines,
    SUM(sol.price_unit) FILTER (WHERE sol.ue_item_removed = true) AS estimated_lost_revenue
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
WHERE so.store_id IN {CELESTE_STORE_IDS} AND so.date_order >= :start AND so.date_order <= :end
"""

REASONS_SQL = f"""
SELECT COALESCE(sol.ue_removal_reason, 'Not recorded') AS reason, COUNT(*) AS n
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
WHERE sol.ue_item_removed = true AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order <= :end
GROUP BY 1 ORDER BY n DESC LIMIT {TOP_N}
"""

TOP_PRODUCTS_SQL = f"""
SELECT pt.name->>'en_US' AS name, COUNT(*) AS times_removed
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
JOIN product_product pp ON pp.id = sol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE sol.ue_item_removed = true AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order <= :end
GROUP BY pt.name ORDER BY times_removed DESC LIMIT {TOP_N}
"""


def get_uber_lost_sales(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    start, end = _period_bounds(period, start_date, end_date)
    params = {"start": start, "end": end}
    summary = get_sales_summary(period, start_date, end_date)  # reused only for its partial-period detection

    summary_row = reporting.run_query(SUMMARY_SQL, params)[0]
    removed_lines = int(summary_row.get("removed_lines") or 0)
    total_lines = int(summary_row.get("total_lines") or 0)

    return {
        "period": summary["period"],
        "is_partial_period": summary["is_partial_period"],
        "days_elapsed": summary["days_elapsed"],
        "days_in_period": summary["days_in_period"],
        "removed_lines": removed_lines,
        "total_lines": total_lines,
        "removed_rate_pct": round(removed_lines / total_lines * 100, 2) if total_lines else 0.0,
        "estimated_lost_revenue": round(float(summary_row.get("estimated_lost_revenue") or 0), 2),
        "estimate_note": (
            "Assumes 1 unit per removed line — the original quantity is zeroed out "
            "when Uber removes an item, so this is a lower-bound estimate, not exact."
        ),
        "top_reasons": [
            {"reason": r["reason"], "count": int(r["n"] or 0)}
            for r in reporting.run_query(REASONS_SQL, params)
        ],
        "top_products": [
            {"name": r["name"] or "Unknown", "times_removed": int(r["times_removed"] or 0)}
            for r in reporting.run_query(TOP_PRODUCTS_SQL, params)
        ],
    }
