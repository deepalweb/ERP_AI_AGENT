"""Wastage/loss signal: shortage reasons recorded during picking (missing
item, item declined, product damage, extra quantity), physically damaged
goods, and stock returns. None of this existed as a queryable signal before
this tool — the app previously had no visibility into shrinkage at all.

return_order here reads as internal stock returns, not customer or supplier
returns: 91% of scoped rows have is_supplier_return = false, and partner_id
is populated on under 10% of rows. Framed as "stock returns," not either of
those — this stays separate from purchasing/supplier spend, which is out of
scope per an earlier explicit hold.

Store scoping goes through the picking-type -> warehouse -> store path
(stock_picking_type.warehouse_id = res_store.warehouse_id), not the usual
sale_order.store_id, since these are warehouse-side records with no sale
order behind them. Several other res_store rows (SPAR co-located storefronts,
21+/Budget Kade/brand-fulfillment variants) share a warehouse with a real
Celeste outlet, so the CELESTE_STORE_IDS filter must apply directly in the
join, not after — confirmed no row fan-out with a distinct-count check.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from mcp_server.tools.sales import _period_bounds, _period_label

TOP_N = 10

SHORTAGE_BY_REASON_SQL = f"""
SELECT sr.name AS reason, COUNT(*) AS lines, SUM(sms.quantity) AS qty
FROM stock_move_shortage sms
JOIN stock_picking sp ON sp.id = sms.picking_id
JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
JOIN shortage_reason sr ON sr.id = sms.reason_id
WHERE rs.id IN {CELESTE_STORE_IDS} AND sms.create_date >= :start AND sms.create_date <= :end
GROUP BY sr.name
ORDER BY qty DESC
"""

SHORTAGE_TOP_PRODUCTS_SQL = f"""
SELECT pt.name->>'en_US' AS name, MIN(pp.id) AS product_id, pt.default_code AS sku, COUNT(*) AS lines, SUM(sms.quantity) AS qty
FROM stock_move_shortage sms
JOIN stock_picking sp ON sp.id = sms.picking_id
JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
JOIN product_product pp ON pp.id = sms.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE rs.id IN {CELESTE_STORE_IDS} AND sms.create_date >= :start AND sms.create_date <= :end
GROUP BY pt.name, pt.default_code
ORDER BY qty DESC
LIMIT {TOP_N}
"""

SHORTAGE_BY_STORE_SQL = f"""
SELECT rs.name AS outlet, COUNT(*) AS lines, SUM(sms.quantity) AS qty
FROM stock_move_shortage sms
JOIN stock_picking sp ON sp.id = sms.picking_id
JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
WHERE rs.id IN {CELESTE_STORE_IDS} AND sms.create_date >= :start AND sms.create_date <= :end
GROUP BY rs.name
ORDER BY qty DESC
"""

DAMAGED_TOP_PRODUCTS_SQL = f"""
SELECT pt.name->>'en_US' AS name, MIN(pp.id) AS product_id, pt.default_code AS sku, COUNT(*) AS lines, SUM(dol.initial_demand) AS qty
FROM damaged_order_line dol
JOIN damaged_order do_ ON do_.id = dol.order_id
JOIN stock_picking_type spt ON spt.id = do_.operation_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
JOIN product_product pp ON pp.id = dol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE rs.id IN {CELESTE_STORE_IDS} AND do_.date >= :start AND do_.date <= :end
GROUP BY pt.name, pt.default_code
ORDER BY qty DESC
LIMIT {TOP_N}
"""

DAMAGED_BY_STORE_SQL = f"""
SELECT rs.name AS outlet, COUNT(DISTINCT do_.id) AS damaged_orders, SUM(dol.initial_demand) AS qty
FROM damaged_order_line dol
JOIN damaged_order do_ ON do_.id = dol.order_id
JOIN stock_picking_type spt ON spt.id = do_.operation_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
WHERE rs.id IN {CELESTE_STORE_IDS} AND do_.date >= :start AND do_.date <= :end
GROUP BY rs.name
ORDER BY qty DESC
"""

RETURNS_SUMMARY_SQL = f"""
SELECT COUNT(DISTINCT ro.id) AS orders, SUM(rol.demand) AS qty,
       COUNT(DISTINCT ro.id) FILTER (WHERE ro.is_supplier_return) AS supplier_returns
FROM return_order ro
JOIN stock_picking_type spt ON spt.id = ro.operation_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
LEFT JOIN return_order_line rol ON rol.order_id = ro.id
WHERE rs.id IN {CELESTE_STORE_IDS} AND ro.date >= :start AND ro.date <= :end
"""


def get_loss_and_damage(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    start, end = _period_bounds(period, start_date, end_date)
    params = {"start": start, "end": end}

    shortage_by_reason = reporting.run_query(SHORTAGE_BY_REASON_SQL, params)
    shortage_top_products = reporting.run_query(SHORTAGE_TOP_PRODUCTS_SQL, params)
    shortage_by_store = reporting.run_query(SHORTAGE_BY_STORE_SQL, params)
    damaged_top_products = reporting.run_query(DAMAGED_TOP_PRODUCTS_SQL, params)
    damaged_by_store = reporting.run_query(DAMAGED_BY_STORE_SQL, params)
    returns_rows = reporting.run_query(RETURNS_SUMMARY_SQL, params)
    returns = returns_rows[0] if returns_rows else {}

    return {
        "period": _period_label(period, start, end, start_date, end_date),
        "shortage_by_reason": [
            {"reason": r["reason"], "lines": int(r["lines"]), "qty": float(r["qty"] or 0)}
            for r in shortage_by_reason
        ],
        "shortage_top_products": [
            {"name": r["name"] or "Unknown", "product_id": r["product_id"], "sku": r["sku"],
             "lines": int(r["lines"]), "qty": float(r["qty"] or 0)}
            for r in shortage_top_products
        ],
        "shortage_by_store": [
            {"outlet": r["outlet"] or "Unknown", "lines": int(r["lines"]), "qty": float(r["qty"] or 0)}
            for r in shortage_by_store
        ],
        "damaged_top_products": [
            {"name": r["name"] or "Unknown", "product_id": r["product_id"], "sku": r["sku"],
             "lines": int(r["lines"]), "qty": float(r["qty"] or 0)}
            for r in damaged_top_products
        ],
        "damaged_by_store": [
            {
                "outlet": r["outlet"] or "Unknown",
                "damaged_orders": int(r["damaged_orders"]),
                "qty": float(r["qty"] or 0),
            }
            for r in damaged_by_store
        ],
        "returns_orders": int(returns.get("orders") or 0),
        "returns_qty": float(returns.get("qty") or 0),
        "returns_supplier_count": int(returns.get("supplier_returns") or 0),
    }
