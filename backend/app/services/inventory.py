"""Inventory Depth: days-of-stock, overstock, and dead-stock segmentation.

Filtered to celeste_product_type = 'INVENTORY' (real retail SKUs) — without this,
the dead-stock bucket is dominated by packaging/raw-material rows (e.g. a single
thermal-label-roll SKU with 673,879 units on hand) that structurally never sell
through sale_order_line, which would make "dead stock" actively misleading.

"Recency" is anchored to the data's own MAX(date_order), not date.today() — same
reasoning as customers.py: the DB snapshot lags the system clock by weeks.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES

OVERSTOCK_DAYS = 90  # p90 of the days-of-stock distribution across in-stock retail products
TOP_ITEMS = 8

# Joins via stock_location.warehouse_id -> res_store.warehouse_id, not
# res_store.name pattern-matching — a warehouse can serve co-located
# non-Celeste storefronts (SPAR, Budget Kade, etc.), so grouping by store name
# would fan out one warehouse's stock across multiple label rows. Restricting
# to CELESTE_STORE_IDS keeps this a clean 1:1 warehouse-to-outlet mapping
# (verified: each of the 5 real outlets has a distinct warehouse_id).
INVENTORY_BY_OUTLET_SQL = f"""
    SELECT rs.name AS outlet, SUM(sq.quantity) AS total_qty,
           SUM(sq.quantity * COALESCE(pt.celeste_cost_price_ex_vat, 0)) AS estimated_value
    FROM stock_quant sq
    JOIN stock_location sl ON sl.id = sq.location_id
    JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
    JOIN product_product pp ON pp.id = sq.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    WHERE sl.usage = 'internal' AND rs.id IN {CELESTE_STORE_IDS}
      AND sq.quantity > 0 AND pt.celeste_product_type = 'INVENTORY'
    GROUP BY rs.name
    ORDER BY estimated_value DESC
"""

INVENTORY_DEPTH_SQL = f"""
    WITH bounds AS (SELECT MAX(date_order) AS as_of FROM sale_order WHERE state IN {SALE_ORDER_LIVE_STATES}),
    in_stock AS (
        SELECT sq.product_id, SUM(sq.quantity) AS total_qty
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        WHERE sl.usage = 'internal'
        GROUP BY sq.product_id
        HAVING SUM(sq.quantity) > 0
    ),
    recent_sales AS (
        SELECT sol.product_id, SUM(sol.product_uom_qty) AS qty_sold_30d
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        CROSS JOIN bounds b
        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.date_order >= b.as_of - INTERVAL '30 days'
        GROUP BY sol.product_id
    )
    SELECT i.product_id, pt.name->>'en_US' AS product_name, i.total_qty,
           COALESCE(rs.qty_sold_30d, 0) AS sold_30d,
           pt.celeste_cost_price_ex_vat AS cost, pt.list_price,
           b.as_of
    FROM in_stock i
    JOIN product_product pp ON pp.id = i.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN recent_sales rs ON rs.product_id = i.product_id
    CROSS JOIN bounds b
    WHERE pt.celeste_product_type = 'INVENTORY'
"""

SEGMENT_LABELS = {
    "dead_stock": "Dead stock",
    "overstock": "Overstock",
    "healthy": "Healthy",
}


def _usable_cost(cost, list_price):
    cost = float(cost or 0)
    list_price = float(list_price or 0)
    if cost > 0 and (list_price == 0 or cost <= list_price):
        return cost
    return None


def _classify(sold_30d: float, days_of_stock):
    if sold_30d == 0:
        return "dead_stock"
    if days_of_stock is not None and days_of_stock > OVERSTOCK_DAYS:
        return "overstock"
    return "healthy"


async def get_inventory_depth():
    rows = await reporting.fetch_report(INVENTORY_DEPTH_SQL)

    if not rows:
        return {"as_of_date": None, "segments": [], "dead_stock_items": [], "overstock_items": []}

    as_of_date = rows[0]["as_of"]

    segment_counts = {key: 0 for key in SEGMENT_LABELS}
    segment_units = {key: 0.0 for key in SEGMENT_LABELS}
    segment_value = {key: 0.0 for key in SEGMENT_LABELS}
    segment_valued_count = {key: 0 for key in SEGMENT_LABELS}

    dead_stock_candidates = []
    overstock_candidates = []

    for r in rows:
        total_qty = float(r["total_qty"] or 0)
        sold_30d = float(r["sold_30d"] or 0)
        days_of_stock = (total_qty / (sold_30d / 30.0)) if sold_30d > 0 else None
        segment = _classify(sold_30d, days_of_stock)
        usable_cost = _usable_cost(r["cost"], r["list_price"])
        estimated_value = total_qty * usable_cost if usable_cost is not None else None

        segment_counts[segment] += 1
        segment_units[segment] += total_qty
        if estimated_value is not None:
            segment_value[segment] += estimated_value
            segment_valued_count[segment] += 1

        item = {
            "name": r["product_name"] or "Unknown",
            "units": round(total_qty, 2),
            "estimated_value": round(estimated_value, 2) if estimated_value is not None else None,
            "days_of_stock": round(days_of_stock) if days_of_stock is not None else None,
        }
        if segment == "dead_stock":
            dead_stock_candidates.append(item)
        elif segment == "overstock":
            overstock_candidates.append(item)

    def _sort_key(item):
        return item["estimated_value"] if item["estimated_value"] is not None else -1

    dead_stock_candidates.sort(key=_sort_key, reverse=True)
    overstock_candidates.sort(key=_sort_key, reverse=True)

    return {
        "as_of_date": as_of_date.date().isoformat(),
        "segments": [
            {
                "id": key,
                "label": SEGMENT_LABELS[key],
                "count": segment_counts[key],
                "units": round(segment_units[key], 2),
                "value": round(segment_value[key], 2),
                "value_coverage": f"{segment_valued_count[key]}/{segment_counts[key]}",
            }
            for key in ("dead_stock", "overstock", "healthy")
        ],
        "dead_stock_items": dead_stock_candidates[:TOP_ITEMS],
        "overstock_items": overstock_candidates[:TOP_ITEMS],
    }


async def get_inventory_by_outlet():
    rows = await reporting.fetch_report(INVENTORY_BY_OUTLET_SQL)
    return [
        {
            "name": r["outlet"],
            "units": round(float(r["total_qty"] or 0), 2),
            "estimated_value": round(float(r["estimated_value"] or 0), 2),
        }
        for r in rows
    ]
