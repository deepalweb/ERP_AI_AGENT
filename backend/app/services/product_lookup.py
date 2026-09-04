"""Product lookup: item-wise sales, margin, stock, and channel mix for a single
product, resolved by SKU (product_template.default_code) or a name substring.

Every other "item" tool in this codebase (get_top_products, get_inventory_depth,
get_gross_margin) collapses to a fixed Top-N ranking or a company-wide total —
none of them can answer "how is product X doing". This reruns the same
per-product joins those tools already do internally, but keyed to one product
instead of aggregated away.
"""

from mcp_server.tools.sales import _period_bounds, get_sales_summary
from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

MAX_MATCHES = 10

MATCH_SQL = """
    SELECT id, name->>'en_US' AS name, default_code
    FROM product_template
    WHERE default_code = :exact OR name->>'en_US' ILIKE :pattern
    ORDER BY (default_code = :exact) DESC, name
    LIMIT :limit
"""

SALES_MARGIN_SQL = f"""
    SELECT
        SUM(sol.price_subtotal) AS revenue,
        SUM(sol.product_uom_qty) AS qty,
        COUNT(DISTINCT sol.order_id) AS orders,
        SUM(sol.price_subtotal) FILTER (WHERE pt.celeste_cost_price_ex_vat > 0) AS revenue_with_cost,
        SUM(sol.product_uom_qty * pt.celeste_cost_price_ex_vat)
            FILTER (WHERE pt.celeste_cost_price_ex_vat > 0) AS cogs,
        COUNT(*) FILTER (WHERE sol.ue_item_removed = true) AS uber_removals
    FROM sale_order_line sol
    JOIN sale_order so ON so.id = sol.order_id
    JOIN product_product pp ON pp.id = sol.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    WHERE pt.id = :tmpl_id AND so.state IN ('sale', 'done') AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.date_order >= :start AND so.date_order <= :end
"""

CHANNEL_SQL = f"""
    SELECT
        CASE
            WHEN so.pickme_order_id IS NOT NULL THEN 'PickMe'
            WHEN so.uber_order_id IS NOT NULL THEN 'Uber'
            ELSE 'Direct'
        END AS channel,
        SUM(sol.price_subtotal) AS revenue,
        SUM(sol.product_uom_qty) AS qty
    FROM sale_order_line sol
    JOIN sale_order so ON so.id = sol.order_id
    JOIN product_product pp ON pp.id = sol.product_id
    WHERE pp.product_tmpl_id = :tmpl_id AND so.state IN ('sale', 'done') AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.date_order >= :start AND so.date_order <= :end
    GROUP BY 1 ORDER BY revenue DESC
"""

# Same warehouse-based join as inventory.py's INVENTORY_BY_OUTLET_SQL — a
# warehouse can serve co-located non-Celeste storefronts, so this avoids
# fanning stock out across them.
STOCK_BY_OUTLET_SQL = f"""
    SELECT rs.name AS outlet, SUM(sq.quantity) AS qty
    FROM stock_quant sq
    JOIN stock_location sl ON sl.id = sq.location_id
    JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
    JOIN product_product pp ON pp.id = sq.product_id
    WHERE pp.product_tmpl_id = :tmpl_id AND sl.usage = 'internal' AND rs.id IN {CELESTE_STORE_IDS}
    GROUP BY rs.name ORDER BY qty DESC
"""


def _resolve(query: str):
    query = query.strip()
    rows = reporting.run_query(MATCH_SQL, {"exact": query, "pattern": f"%{query}%", "limit": MAX_MATCHES})
    exact = [r for r in rows if r["default_code"] == query]
    if len(exact) == 1:
        return exact[0], rows
    if len(rows) == 1:
        return rows[0], rows
    return None, rows


def get_product_lookup(
    query: str, period: str | None = None, start_date: str | None = None, end_date: str | None = None
):
    chosen, candidates = _resolve(query)

    if chosen is None:
        if not candidates:
            return {"query": query, "status": "not_found"}
        return {
            "query": query,
            "status": "ambiguous",
            "matches": [{"name": r["name"], "sku": r["default_code"]} for r in candidates],
        }

    start, end = _period_bounds(period, start_date, end_date)
    summary = get_sales_summary(period, start_date, end_date)  # reused only for period label + partial-period detection
    params = {"tmpl_id": chosen["id"], "start": start, "end": end}

    row = reporting.run_query(SALES_MARGIN_SQL, params)[0]
    revenue = float(row.get("revenue") or 0)
    revenue_with_cost = float(row.get("revenue_with_cost") or 0)
    cogs = float(row.get("cogs") or 0)
    margin_pct = (
        round((revenue_with_cost - cogs) / revenue_with_cost * 100, 2) if revenue_with_cost else None
    )

    channels = reporting.run_query(CHANNEL_SQL, params)
    stock = reporting.run_query(STOCK_BY_OUTLET_SQL, {"tmpl_id": chosen["id"]})

    return {
        "query": query,
        "status": "found",
        "product": {"name": chosen["name"], "sku": chosen["default_code"]},
        "period": summary["period"],
        "is_partial_period": summary["is_partial_period"],
        "sales": {
            "revenue": round(revenue, 2),
            "qty_sold": round(float(row.get("qty") or 0), 2),
            "orders": int(row.get("orders") or 0),
        },
        "margin": {
            "gross_margin_pct": margin_pct,
            "cost_data_coverage_pct": round(revenue_with_cost / revenue * 100, 1) if revenue else None,
        },
        "uber_removals": int(row.get("uber_removals") or 0),
        "channel_mix": [
            {"name": r["channel"], "revenue": float(r["revenue"] or 0), "qty": float(r["qty"] or 0)}
            for r in channels
        ],
        "stock_by_outlet": [{"name": r["outlet"], "qty": float(r["qty"] or 0)} for r in stock],
    }
