"""Gross margin: revenue minus cost of goods sold, for a given month.

Cost comes from product_template.celeste_cost_price_ex_vat — verified to cover
~99% of actual sold revenue (not just catalog count), so a small "no cost data"
slice is excluded and reported via cost_data_coverage_pct rather than silently
treated as zero-cost (which would inflate margin).

Reuses sales.get_sales_summary for revenue and the partial-period flag rather
than re-deriving that logic here.
"""

from mcp_server.tools.sales import _period_bounds, get_sales_summary
from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

GROSS_MARGIN_SQL = f"""
SELECT
    SUM(sol.price_subtotal) FILTER (WHERE pt.celeste_cost_price_ex_vat > 0) AS revenue_with_cost,
    SUM(sol.product_uom_qty * pt.celeste_cost_price_ex_vat)
        FILTER (WHERE pt.celeste_cost_price_ex_vat > 0) AS cogs
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
JOIN product_product pp ON pp.id = sol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE so.state IN ('sale', 'done') AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order <= :end
"""


def get_gross_margin(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    start, end = _period_bounds(period, start_date, end_date)
    summary = get_sales_summary(period, start_date, end_date)

    rows = reporting.run_query(GROSS_MARGIN_SQL, {"start": start, "end": end})
    row = rows[0] if rows else {}
    revenue_with_cost = float(row.get("revenue_with_cost") or 0)
    cogs = float(row.get("cogs") or 0)

    margin_amount = revenue_with_cost - cogs if revenue_with_cost else None
    margin_pct = round(margin_amount / revenue_with_cost * 100, 2) if revenue_with_cost else None
    coverage_pct = (
        round(revenue_with_cost / summary["revenue"] * 100, 1) if summary["revenue"] else None
    )

    return {
        "period": summary["period"],
        "revenue": summary["revenue"],
        "revenue_with_cost_data": revenue_with_cost,
        "cost_of_goods_sold": round(cogs, 2),
        "gross_margin_amount": round(margin_amount, 2) if margin_amount is not None else None,
        "gross_margin_pct": margin_pct,
        "cost_data_coverage_pct": coverage_pct,
        "is_partial_period": summary["is_partial_period"],
        "days_elapsed": summary["days_elapsed"],
        "days_in_period": summary["days_in_period"],
    }
