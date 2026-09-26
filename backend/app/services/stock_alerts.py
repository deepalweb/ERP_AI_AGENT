"""Stock alerts: which actively-selling products are out of stock or critically
low at each outlet right now — the shortage counterpart to get_inventory_depth's
dead-stock/overstock segmentation, which only looks at excess, never shortage.

Scoped to products with real recent sales velocity (qty_sold_30d > 0) — a
product with zero recent sales that's also at zero stock isn't a "stockout",
it's just not moving, which is dead-stock territory and already covered by
get_inventory_depth.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES

LOW_STOCK_DAYS = 3  # quick-commerce grocery — a few days' cover is already tight
TOP_N = 10

STOCK_ALERTS_SQL = f"""
    WITH bounds AS (
        SELECT MAX(date_order) AS as_of FROM sale_order
        WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
    ),
    recent_sales AS (
        SELECT so.store_id, sol.product_id, SUM(sol.product_uom_qty) AS qty_sold_30d
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        CROSS JOIN bounds b
        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
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
    # Phase 5A: deliberately NOT cached here -- state_findings.py and
    # evidence_engine.py both call this directly as part of scan_all()'s
    # real detection pipeline, which must see fresh stock levels on every
    # rescan. Caching happens one layer up, in main.py's own
    # /inventory/stock-alerts route -- Home's Inventory panel is the only
    # caller that's a pure supplementary display.
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

    # segments_by_outlet's out_of_stock counts (and their sum) are per
    # (outlet, product) row -- the same product out of stock at 3 outlets
    # counts 3 times there, which is correct for the store-concentration
    # breakdown but NOT the same thing as "how many distinct products are
    # out of stock". Callers that want the latter (state_findings.py's
    # headline count) need this separately.
    distinct_out_of_stock_products = len({i["sku"] for i in out_of_stock_items})
    # Same reasoning as distinct_out_of_stock_products above -- top_low_stock
    # is truncated to TOP_N and segments_by_outlet's low_stock counts are
    # per (outlet, product), so neither answers "how many distinct products
    # are low on stock" on its own.
    distinct_low_stock_products = len({i["sku"] for i in low_stock_items})

    return {
        "as_of_date": as_of_date.date().isoformat(),
        "segments_by_outlet": [{"outlet": outlet, **c} for outlet, c in sorted(counts.items())],
        "top_out_of_stock": out_of_stock_items[:TOP_N],
        "top_low_stock": low_stock_items[:TOP_N],
        "low_stock_threshold_days": LOW_STOCK_DAYS,
        "distinct_out_of_stock_products": distinct_out_of_stock_products,
        "distinct_low_stock_products": distinct_low_stock_products,
    }


# ---------------------------------------------------------------------------
# Top-product stock coverage: does a company-wide stock total ever answer
# "will we run out"? No — it pools thousands of SKUs together, so a
# fast-mover running low is invisible inside a warehouse full of slow-moving
# stock (the same reason get_stock_alerts above and get_inventory_depth are
# both per-product, never a single combined number). This narrows to the
# products that actually matter for that question — the top sellers by
# volume — and checks each one's own stock against its own projected need.
TOP_PRODUCTS_TOP_N = 15
COVERAGE_HORIZON_DAYS = 10  # matches the Order forecast chart's window

TOP_PRODUCTS_STOCK_SQL = f"""
    WITH bounds AS (
        SELECT MAX(date_order) AS as_of FROM sale_order
        WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
    ),
    recent_sales AS (
        SELECT sol.product_id, SUM(sol.product_uom_qty) AS qty_sold_30d
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        CROSS JOIN bounds b
        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
          AND so.date_order >= b.as_of - INTERVAL '30 days'
        GROUP BY sol.product_id
    ),
    on_hand AS (
        SELECT sq.product_id, SUM(sq.quantity) AS qty
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
        WHERE sl.usage = 'internal' AND rs.id IN {CELESTE_STORE_IDS}
        GROUP BY sq.product_id
    )
    SELECT pt.name->>'en_US' AS product_name, pt.default_code AS sku,
           r.qty_sold_30d, COALESCE(oh.qty, 0) AS current_stock, b.as_of
    FROM recent_sales r
    JOIN product_product pp ON pp.id = r.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN on_hand oh ON oh.product_id = r.product_id
    CROSS JOIN bounds b
    WHERE pt.celeste_product_type = 'INVENTORY' AND r.qty_sold_30d > 0
    ORDER BY r.qty_sold_30d DESC
    LIMIT :top_n
"""


def get_top_product_stock_coverage(top_n: int = TOP_PRODUCTS_TOP_N, horizon_days: int = COVERAGE_HORIZON_DAYS):
    """Top-selling products (by units sold, trailing 30 days), each with its
    own stock checked against its own sales rate.

    "short" reuses LOW_STOCK_DAYS (the same 3-day threshold get_stock_alerts
    already uses), not a flat horizon_days requirement — checked against
    the live data before picking this: the top 15 sellers by volume here
    are entirely fresh produce/dairy (onions, tomatoes, eggs, milk...), and
    the #1 item is restocked nearly every day (400-800+ units/day, verified
    against stock_move). Requiring horizon_days (10) of cover on hand for a
    product replenished daily would flag every single one as "short" when
    that's normal, deliberately-thin stock for a quick-commerce grocer, not
    a shortage. projected_demand over horizon_days is still returned as
    context (how much will sell in that window), it just isn't what
    decides the flag.

    negative_stock is reported separately from short — current_stock < 0
    isn't a low-stock situation, it's a data-integrity issue (sold past
    what was recorded on hand), and conflating the two would bury a real
    data problem inside an ordinary restocking signal."""
    rows = reporting.run_query(TOP_PRODUCTS_STOCK_SQL, {"top_n": top_n})
    if not rows:
        return {"as_of_date": None, "horizon_days": horizon_days, "low_stock_threshold_days": LOW_STOCK_DAYS, "products": []}

    as_of_date = rows[0]["as_of"]
    products = []
    for r in rows:
        qty_sold_30d = float(r["qty_sold_30d"] or 0)
        current_stock = float(r["current_stock"] or 0)
        avg_daily_sales = qty_sold_30d / 30.0
        projected_demand = round(avg_daily_sales * horizon_days, 1)
        days_of_cover = round(current_stock / avg_daily_sales, 1) if avg_daily_sales > 0 else None
        products.append({
            "name": r["product_name"] or "Unknown",
            "sku": r["sku"],
            "qty_sold_30d": round(qty_sold_30d, 1),
            "current_stock": round(current_stock, 1),
            "projected_demand": projected_demand,
            "days_of_cover": days_of_cover,
            "short": days_of_cover is not None and days_of_cover < LOW_STOCK_DAYS,
            "negative_stock": current_stock < 0,
        })

    return {
        "as_of_date": as_of_date.date().isoformat(),
        "horizon_days": horizon_days,
        "low_stock_threshold_days": LOW_STOCK_DAYS,
        "products": products,
    }
