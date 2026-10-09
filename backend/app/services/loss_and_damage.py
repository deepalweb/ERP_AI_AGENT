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

from datetime import date, timedelta

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from backend.app.services.inventory import _usable_cost
from mcp_server.tools.sales import _period_bounds, _period_label

TOP_N = 10

# Confirmed Odoo damaged_order states only -- checked live (2026-10-08):
# 'draft'/'approval' are unconfirmed (not yet written off, 742 of 26,784
# all-time lines), 'rejected' means the damage claim was reviewed and
# denied (37 lines) -- including either would overstate real loss.
# 'scrapped'/'record_wastage'/'done' are the confirmed write-off states.
DAMAGE_CONFIRMED_STATES = ("scrapped", "record_wastage", "done")

DAMAGE_WRITEOFF_LINES_SQL = f"""
SELECT do_.id AS order_id, do_.name AS order_name, do_.date AS order_date, do_.state AS state,
       rs.name AS outlet, pt.name->>'en_US' AS product, pt.default_code AS sku,
       dol.initial_demand AS qty, pt.celeste_cost_price_ex_vat AS cost, pt.list_price AS list_price
FROM damaged_order_line dol
JOIN damaged_order do_ ON do_.id = dol.order_id
JOIN stock_picking_type spt ON spt.id = do_.operation_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
JOIN product_product pp ON pp.id = dol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE rs.id IN {CELESTE_STORE_IDS} AND do_.state IN {DAMAGE_CONFIRMED_STATES}
  AND do_.date >= :start AND do_.date <= :end
"""

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

# Reason x outlet, one row per combination -- answers "is one outlet
# dominated by missing items while another runs more overages/damage," which
# neither SHORTAGE_BY_REASON_SQL nor SHORTAGE_BY_STORE_SQL can answer alone.
SHORTAGE_BY_REASON_AND_STORE_SQL = f"""
SELECT rs.name AS outlet, sr.name AS reason, SUM(sms.quantity) AS qty
FROM stock_move_shortage sms
JOIN stock_picking sp ON sp.id = sms.picking_id
JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
JOIN shortage_reason sr ON sr.id = sms.reason_id
WHERE rs.id IN {CELESTE_STORE_IDS} AND sms.create_date >= :start AND sms.create_date <= :end
GROUP BY rs.name, sr.name
"""

# Day-by-day shortage qty across the period -- tells management whether the
# problem is an isolated spike, persistent, growing, or shrinking, which a
# single monthly total can't.
SHORTAGE_DAILY_TREND_SQL = f"""
SELECT DATE(sms.create_date) AS day, SUM(sms.quantity) AS qty
FROM stock_move_shortage sms
JOIN stock_picking sp ON sp.id = sms.picking_id
JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
JOIN res_store rs ON rs.warehouse_id = spt.warehouse_id
WHERE rs.id IN {CELESTE_STORE_IDS} AND sms.create_date >= :start AND sms.create_date <= :end
GROUP BY DATE(sms.create_date)
ORDER BY day
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
    shortage_by_reason_and_store = reporting.run_query(SHORTAGE_BY_REASON_AND_STORE_SQL, params)
    shortage_daily_trend = reporting.run_query(SHORTAGE_DAILY_TREND_SQL, params)
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
        "shortage_by_reason_and_store": [
            {"outlet": r["outlet"] or "Unknown", "reason": r["reason"], "qty": float(r["qty"] or 0)}
            for r in shortage_by_reason_and_store
        ],
        "shortage_daily_trend": [
            {"day": r["day"].isoformat() if hasattr(r["day"], "isoformat") else r["day"], "qty": float(r["qty"] or 0)}
            for r in shortage_daily_trend
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


def get_damage_writeoff(window_days: int = 14):
    """Confirmed damage write-offs (Odoo's damaged_order document -- a
    standalone scrap/wastage record) in the trailing window, valued at
    catalog cost (no per-line cost exists on this document, unlike a stock
    count's avg_cost -- same _usable_cost fallback as stock_counts.py, so
    coverage here is a simpler 2-tier "catalog_estimate" / "no_cost_data"
    rather than that module's 3-tier "source_cost" / "catalog_estimate" /
    "no_cost_data").

    Distinct from get_loss_and_damage's damaged_top_products/damaged_by_store
    above: those report ALL states for a fixed calendar month with no
    valuation; this is a trailing window, confirmed states only
    (DAMAGE_CONFIRMED_STATES), and priced -- built for state_findings.
    _damage_writeoff_finding, which needs a real Rs figure and a live
    as-of date to mark its measurement fresh."""
    end = date.today()
    start = end - timedelta(days=window_days - 1)
    rows = reporting.run_query(DAMAGE_WRITEOFF_LINES_SQL, {"start": start.isoformat(), "end": end.isoformat()})

    by_outlet: dict[str, dict] = {}
    by_product: dict[tuple, dict] = {}
    coverage = {"total_lines": 0, "catalog_estimate_lines": 0, "no_cost_data_lines": 0}
    total_qty = 0.0
    total_value = 0.0

    for r in rows:
        qty = float(r["qty"] or 0)
        usable_cost = _usable_cost(r["cost"], r["list_price"])
        value = qty * usable_cost if usable_cost is not None else 0.0
        total_qty += qty
        total_value += value
        coverage["total_lines"] += 1
        coverage["catalog_estimate_lines" if usable_cost is not None else "no_cost_data_lines"] += 1

        outlet = r["outlet"] or "Unknown"
        o = by_outlet.setdefault(outlet, {"outlet": outlet, "orders": set(), "qty": 0.0, "value_rs": 0.0})
        o["orders"].add(r["order_id"])
        o["qty"] += qty
        o["value_rs"] += value

        key = (r["sku"], r["product"] or "Unnamed product")
        p = by_product.setdefault(key, {"name": key[1], "sku": key[0], "lines": 0, "qty": 0.0, "value_rs": 0.0})
        p["lines"] += 1
        p["qty"] += qty
        p["value_rs"] += value

    outlet_rows = sorted(
        [{"outlet": o["outlet"], "orders": len(o["orders"]), "qty": round(o["qty"], 2), "value_rs": round(o["value_rs"], 2)}
         for o in by_outlet.values()],
        key=lambda r: r["value_rs"], reverse=True,
    )
    product_rows = sorted(
        [{"name": p["name"], "sku": p["sku"], "lines": p["lines"], "qty": round(p["qty"], 2), "value_rs": round(p["value_rs"], 2)}
         for p in by_product.values()],
        key=lambda r: r["value_rs"], reverse=True,
    )

    return {
        "as_of": end.isoformat(),
        "window_days": window_days,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "order_count": len({r["order_id"] for r in rows}),
        "line_count": len(rows),
        "total_qty": round(total_qty, 2),
        "total_value_rs": round(total_value, 2),
        "valuation_coverage": coverage,
        "by_outlet": outlet_rows,
        "top_products": product_rows[:TOP_N],
    }
