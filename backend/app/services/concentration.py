"""Revenue concentration: what share of revenue comes from the top N products
and top N customers — a dependency-risk read ("are we too reliant on a
handful of SKUs or customers"), not just a ranking (get_top_products already
covers the ranking itself).
"""

from mcp_server.tools.sales import _period_bounds, get_sales_summary
from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES

TOP_N = 10

TOP_PRODUCTS_SQL = f"""
SELECT pt.name->>'en_US' AS name, SUM(sol.price_subtotal) AS revenue
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
JOIN product_product pp ON pp.id = sol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order <= :end
GROUP BY pt.name ORDER BY revenue DESC LIMIT {TOP_N}
"""

TOP_CUSTOMERS_SQL = f"""
SELECT rp.name AS name, SUM(so.amount_total) AS revenue
FROM sale_order so
JOIN res_partner rp ON rp.id = so.partner_id
WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
  AND so.date_order >= :start AND so.date_order <= :end
GROUP BY rp.name ORDER BY revenue DESC LIMIT {TOP_N}
"""


def _with_share(rows, total_revenue):
    return [
        {
            "name": r["name"] or "Unknown",
            "revenue": float(r["revenue"] or 0),
            "share_pct": round(float(r["revenue"] or 0) / total_revenue * 100, 1) if total_revenue else None,
        }
        for r in rows
    ]


def get_revenue_concentration(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    start, end = _period_bounds(period, start_date, end_date)
    summary = get_sales_summary(period, start_date, end_date)
    total_revenue = summary["revenue"]
    params = {"start": start, "end": end}

    top_products = _with_share(reporting.run_query(TOP_PRODUCTS_SQL, params), total_revenue)
    top_customers = _with_share(reporting.run_query(TOP_CUSTOMERS_SQL, params), total_revenue)

    products_share = round(sum(p["revenue"] for p in top_products) / total_revenue * 100, 1) if total_revenue else None
    customers_share = round(sum(c["revenue"] for c in top_customers) / total_revenue * 100, 1) if total_revenue else None

    return {
        "period": summary["period"],
        "is_partial_period": summary["is_partial_period"],
        "total_revenue": total_revenue,
        "top_n": TOP_N,
        "top_products_revenue_share_pct": products_share,
        "top_customers_revenue_share_pct": customers_share,
        "top_products": top_products,
        "top_customers": top_customers,
    }
