"""Sales mix by brand and by department — resolves the earlier private-label
naming discrepancy: the marketing names ("Island Tide", "Sterilex", "Puracel")
don't appear verbatim, but the real brand hierarchy does exist via
product_template.celeste_brand_id -> celeste_brand (50% of products linked,
real private-label-shaped brands present: PELICAN ISLAND, ISLAND CASHEW,
ISLAND COFFEE, ISLAND COCOA, CEYLON SPICES, NATURE SPICES, alongside CELESTE/
CELESTE DAILY and third-party brands). Verified clean: top-12 brand revenue
and department revenue for a real month both came back sensible, with only
a small NULL/unlinked remainder.

celeste_brand_name (a plain text field on product_template, not this table)
was checked previously and found to hold only "CELESTE"/"CELESTE DAILY" — use
celeste_brand_id/celeste_department_id (the FK hierarchy) here instead, not
that text field.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from mcp_server.tools.sales import _period_bounds, _period_label

TOP_N = 12

TOP_BRANDS_SQL = f"""
SELECT cb.name AS brand, SUM(sol.price_subtotal) AS revenue, SUM(sol.product_uom_qty) AS qty
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
JOIN product_product pp ON pp.id = sol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
JOIN celeste_brand cb ON cb.id = pt.celeste_brand_id
WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order <= :end
GROUP BY cb.name
ORDER BY revenue DESC
LIMIT {TOP_N}
"""

DEPARTMENT_MIX_SQL = f"""
SELECT COALESCE(cd.name, 'Unlinked') AS department, SUM(sol.price_subtotal) AS revenue
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
JOIN product_product pp ON pp.id = sol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
LEFT JOIN celeste_department cd ON cd.id = pt.celeste_department_id
WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order <= :end
GROUP BY cd.name
ORDER BY revenue DESC
"""


def get_brand_and_department_mix(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    start, end = _period_bounds(period, start_date, end_date)
    params = {"start": start, "end": end}

    brand_rows = reporting.run_query(TOP_BRANDS_SQL, params)
    dept_rows = reporting.run_query(DEPARTMENT_MIX_SQL, params)

    total_revenue = sum(float(r["revenue"] or 0) for r in dept_rows)

    return {
        "period": _period_label(period, start, end, start_date, end_date),
        "total_revenue": total_revenue,
        "top_brands": [
            {
                "brand": r["brand"],
                "revenue": float(r["revenue"] or 0),
                "qty": float(r["qty"] or 0),
            }
            for r in brand_rows
        ],
        "departments": [
            {
                "department": r["department"],
                "revenue": float(r["revenue"] or 0),
                "revenue_share_pct": round(float(r["revenue"] or 0) / total_revenue * 100, 1) if total_revenue else None,
            }
            for r in dept_rows
        ],
    }
