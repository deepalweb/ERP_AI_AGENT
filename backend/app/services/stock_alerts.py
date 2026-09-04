"""Stock alerts: which actively-selling products are out of stock or critically
low at each outlet right now — the shortage counterpart to get_inventory_depth's
dead-stock/overstock segmentation, which only looks at excess, never shortage.

Scoped to products with real recent sales velocity (qty_sold_30d > 0) — a
product with zero recent sales that's also at zero stock isn't a "stockout",
it's just not moving, which is dead-stock territory and already covered by
get_inventory_depth.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

LOW_STOCK_DAYS = 3  # quick-commerce grocery — a few days' cover is already tight
TOP_N = 10

STOCK_ALERTS_SQL = f"""
    WITH bounds AS (
        SELECT MAX(date_order) AS as_of FROM sale_order
        WHERE state = 'sale' AND store_id IN {CELESTE_STORE_IDS}
    ),
    recent_sales AS (
        SELECT so.store_id, sol.product_id, SUM(sol.product_uom_qty) AS qty_sold_30d
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        CROSS JOIN bounds b
        WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
          AND so.date_order >= b.as_of - INTERVAL '30 days'
        GROUP BY so.store_id, sol.product_id
    ),
    on_hand AS (
        SELECT rs.id AS store_id, sq.product_id, SUM(sq.quantity) AS qty
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
        WHERE sl.usage = 'internal' AND rs.id IN {CELESTE_STORE_IDS}
        GROUP BY rs.id, sq.product_id
    )
    SELECT rs.name AS outlet, pt.name->>'en_US' AS product_name, pt.default_code AS sku,
           COALESCE(oh.qty, 0) AS qty, r.qty_sold_30d, b.as_of
    FROM recent_sales r
    JOIN res_store rs ON rs.id = r.store_id
    JOIN product_product pp ON pp.id = r.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN on_hand oh ON oh.store_id = r.store_id AND oh.product_id = r.product_id
    CROSS JOIN bounds b
    WHERE pt.celeste_product_type = 'INVENTORY' AND r.qty_sold_30d > 0
"""


def get_stock_alerts():
    rows = reporting.run_query(STOCK_ALERTS_SQL)
    if not rows:
        return {
            "as_of_date": None,
            "segments_by_outlet": [],
            "top_out_of_stock": [],
            "top_low_stock": [],
            "low_stock_threshold_days": LOW_STOCK_DAYS,
        }

    as_of_date = rows[0]["as_of"]
    counts: dict[str, dict[str, int]] = {}
    out_of_stock_items = []
    low_stock_items = []

    for r in rows:
        outlet = r["outlet"]
        qty = float(r["qty"] or 0)
        sold_30d = float(r["qty_sold_30d"] or 0)
        counts.setdefault(outlet, {"out_of_stock": 0, "low_stock": 0})

        item = {
            "outlet": outlet,
            "name": r["product_name"] or "Unknown",
            "sku": r["sku"],
            "qty": round(qty, 2),
            "sold_30d": round(sold_30d, 2),
        }

        if qty <= 0:
            counts[outlet]["out_of_stock"] += 1
            out_of_stock_items.append(item)
        else:
            days_of_stock = qty / (sold_30d / 30.0)
            if days_of_stock < LOW_STOCK_DAYS:
                item["days_of_stock"] = round(days_of_stock, 1)
                counts[outlet]["low_stock"] += 1
                low_stock_items.append(item)

    out_of_stock_items.sort(key=lambda x: -x["sold_30d"])
    low_stock_items.sort(key=lambda x: -x["sold_30d"])

    return {
        "as_of_date": as_of_date.date().isoformat(),
        "segments_by_outlet": [{"outlet": outlet, **c} for outlet, c in sorted(counts.items())],
        "top_out_of_stock": out_of_stock_items[:TOP_N],
        "top_low_stock": low_stock_items[:TOP_N],
        "low_stock_threshold_days": LOW_STOCK_DAYS,
    }
