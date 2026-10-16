"""Inventory Depth: days-of-stock, overstock, and dead-stock segmentation.

Filtered to celeste_product_type = 'INVENTORY' (real retail SKUs) — without this,
the dead-stock bucket is dominated by packaging/raw-material rows (e.g. a single
thermal-label-roll SKU with 673,879 units on hand) that structurally never sell
through sale_order_line, which would make "dead stock" actively misleading.

"Recency" is anchored to the data's own MAX(date_order), not date.today() — same
reasoning as customers.py: the DB snapshot lags the system clock by weeks.

Inventory efficiency review, 2026-10-16: the original 3-bucket classification
(dead_stock = zero sales in 30d, overstock, healthy) conflated several
different conditions a manager needs told apart -- a product with ONE sale
in 30 days read as "healthy" (not dead), while a genuinely seasonal item
with zero 30-day sales read as "dead" alongside products that will never
sell again. Checked live before redesigning (2,475 in-stock retail SKUs):
71 have NEVER sold at all (can't be judged against their own history), 46
sold before but nothing in the last 90 days (a real "potential dead stock"
signal), and the vast majority of active sellers move well over 20 units
in 90 days -- so a floor of 5 units/90d (MIN_RELIABLE_UNITS_90D below)
comfortably separates "genuinely slow" from "too little data to trust a
rate at all" without being arbitrary.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES

OVERSTOCK_DAYS = 90  # p90 of the days-of-stock distribution across in-stock retail products
MIN_RELIABLE_UNITS_90D = 5
"""Below this many units sold in 90 days, a velocity-based rate is noise,
not a trend -- checked live: products that sold at all in the trailing 90
days moved at least 21 units at the 5th percentile, so 5 is a conservative
floor well under normal activity, not a number tuned to produce a
particular split."""
TOP_ITEMS = 8

AGING_BANDS = [
    ("0_30", "0-30 days", 0, 30),
    ("31_60", "31-60 days", 31, 60),
    ("61_90", "61-90 days", 61, 90),
    ("91_180", "91-180 days", 91, 180),
    ("180_plus", "180+ days", 181, None),
]
AGING_BAND_LABELS = {band_id: label for band_id, label, _lo, _hi in AGING_BANDS}
AGING_BAND_LABELS["never_sold"] = "Never sold"

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
    sales_30d AS (
        SELECT sol.product_id, SUM(sol.product_uom_qty) AS qty_sold_30d
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        CROSS JOIN bounds b
        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.date_order >= b.as_of - INTERVAL '30 days'
        GROUP BY sol.product_id
    ),
    sales_90d AS (
        SELECT sol.product_id, SUM(sol.product_uom_qty) AS qty_sold_90d
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        CROSS JOIN bounds b
        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.date_order >= b.as_of - INTERVAL '90 days'
        GROUP BY sol.product_id
    ),
    -- All-time, not windowed -- "has this product EVER sold" and "how long
    -- ago" need the real last sale, not whether it falls inside one window.
    -- product_uom_qty > 0 matters here specifically -- checked live,
    -- 2026-10-16: 18% of ALL order lines company-wide (758K of 4.18M) carry
    -- qty=0 (packaging/tracking lines riding along on a real order, not an
    -- actual unit sold). sold_30d/90d below are SUMs, so a 0-qty line
    -- already contributes nothing to them -- but MAX(date_order) has no
    -- such protection: a product whose only recent order activity is a
    -- 0-qty line would otherwise show a misleadingly recent "last sale."
    -- Caught live: "Thermopack Bag" read as sold 6 days ago (really just
    -- touched 0-qty on an order) while its real sold_30d was correctly 0.
    last_sale AS (
        SELECT sol.product_id, MAX(so.date_order) AS last_sale_date
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND sol.product_uom_qty > 0
        GROUP BY sol.product_id
    )
    SELECT i.product_id, pt.name->>'en_US' AS product_name, i.total_qty,
           COALESCE(s30.qty_sold_30d, 0) AS sold_30d,
           COALESCE(s90.qty_sold_90d, 0) AS sold_90d,
           ls.last_sale_date,
           pt.celeste_cost_price_ex_vat AS cost, pt.list_price,
           b.as_of
    FROM in_stock i
    JOIN product_product pp ON pp.id = i.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN sales_30d s30 ON s30.product_id = i.product_id
    LEFT JOIN sales_90d s90 ON s90.product_id = i.product_id
    LEFT JOIN last_sale ls ON ls.product_id = i.product_id
    CROSS JOIN bounds b
    WHERE pt.celeste_product_type = 'INVENTORY'
"""

SEGMENT_LABELS = {
    "potential_dead_stock": "Potential dead stock",
    "slow_moving": "Slow-moving",
    "overstock": "Overstock",
    "healthy": "Healthy",
    "uncertain": "Uncertain",
    "never_sold": "Never sold",
}


def _usable_cost(cost, list_price):
    cost = float(cost or 0)
    list_price = float(list_price or 0)
    if cost > 0 and (list_price == 0 or cost <= list_price):
        return cost
    return None


def _classify(sold_30d: float, sold_90d: float, has_sold_ever: bool, days_of_stock, usable_cost) -> str:
    """Priority-ordered: each condition below is checked only once the
    ones above it have ruled themselves out, same evidence-ladder shape as
    the rest of this codebase's classification logic. See this module's
    own docstring for why these specific conditions/thresholds."""
    if not has_sold_ever:
        return "never_sold"
    if sold_90d == 0:
        # A real "no sales in the longer window" signal -- but only a
        # confident dead-stock READING if there's a cost to value it by;
        # otherwise there's genuinely not enough information to call it
        # dead stock rather than just untracked.
        return "potential_dead_stock" if usable_cost is not None else "uncertain"
    if sold_90d < MIN_RELIABLE_UNITS_90D:
        return "uncertain"
    if sold_30d == 0:
        # Sold reliably in the last 90 days, nothing in the last 30 --
        # cooling off, not dead; the old 3-bucket version would have
        # called this "dead stock" outright.
        return "slow_moving"
    if days_of_stock is not None and days_of_stock > OVERSTOCK_DAYS:
        return "overstock"
    return "healthy"


def _aging_band(days_since_last_sale) -> str:
    if days_since_last_sale is None:
        return "never_sold"
    for band_id, _label, lo, hi in AGING_BANDS:
        if days_since_last_sale >= lo and (hi is None or days_since_last_sale <= hi):
            return band_id
    return "180_plus"


@reporting.ttl_cache(seconds=300)
async def get_inventory_depth():
    rows = await reporting.fetch_report(INVENTORY_DEPTH_SQL)

    if not rows:
        return {
            "as_of_date": None, "segments": [], "aging_bands": [],
            "potential_dead_stock_items": [], "slow_moving_items": [], "overstock_items": [],
        }

    as_of_date = rows[0]["as_of"]

    segment_counts = {key: 0 for key in SEGMENT_LABELS}
    segment_units = {key: 0.0 for key in SEGMENT_LABELS}
    segment_value = {key: 0.0 for key in SEGMENT_LABELS}
    segment_valued_count = {key: 0 for key in SEGMENT_LABELS}

    aging_counts = {band_id: 0 for band_id, *_ in AGING_BANDS}
    aging_counts["never_sold"] = 0
    aging_value = {band_id: 0.0 for band_id in aging_counts}

    potential_dead_stock_candidates = []
    slow_moving_candidates = []
    overstock_candidates = []

    for r in rows:
        total_qty = float(r["total_qty"] or 0)
        sold_30d = float(r["sold_30d"] or 0)
        sold_90d = float(r["sold_90d"] or 0)
        last_sale_date = r["last_sale_date"]
        has_sold_ever = last_sale_date is not None
        days_since_last_sale = (as_of_date - last_sale_date).days if has_sold_ever else None
        days_of_stock = (total_qty / (sold_30d / 30.0)) if sold_30d > 0 else None
        usable_cost = _usable_cost(r["cost"], r["list_price"])
        segment = _classify(sold_30d, sold_90d, has_sold_ever, days_of_stock, usable_cost)
        estimated_value = total_qty * usable_cost if usable_cost is not None else None

        segment_counts[segment] += 1
        segment_units[segment] += total_qty
        if estimated_value is not None:
            segment_value[segment] += estimated_value
            segment_valued_count[segment] += 1

        band = _aging_band(days_since_last_sale)
        aging_counts[band] += 1
        if estimated_value is not None:
            aging_value[band] += estimated_value

        item = {
            "name": r["product_name"] or "Unknown",
            "units": round(total_qty, 2),
            "estimated_value": round(estimated_value, 2) if estimated_value is not None else None,
            "days_of_stock": round(days_of_stock) if days_of_stock is not None else None,
            "days_since_last_sale": days_since_last_sale,
            "last_sale_date": last_sale_date.date().isoformat() if has_sold_ever else None,
        }
        if segment == "potential_dead_stock":
            potential_dead_stock_candidates.append(item)
        elif segment == "slow_moving":
            slow_moving_candidates.append(item)
        elif segment == "overstock":
            overstock_candidates.append(item)

    def _sort_key(item):
        return item["estimated_value"] if item["estimated_value"] is not None else -1

    potential_dead_stock_candidates.sort(key=_sort_key, reverse=True)
    slow_moving_candidates.sort(key=_sort_key, reverse=True)
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
            for key in ("potential_dead_stock", "slow_moving", "overstock", "healthy", "uncertain", "never_sold")
        ],
        # Complementary to `segments` above -- "how long since this product
        # last sold" cuts across the activity classification (an overstock
        # item and a healthy item can both have sold yesterday; aging is
        # about recency alone, not velocity or cover).
        "aging_bands": [
            {"id": band_id, "label": AGING_BAND_LABELS[band_id], "count": aging_counts[band_id],
             "value": round(aging_value[band_id], 2)}
            for band_id in [*[b for b, *_ in AGING_BANDS], "never_sold"]
        ],
        "potential_dead_stock_items": potential_dead_stock_candidates[:TOP_ITEMS],
        "slow_moving_items": slow_moving_candidates[:TOP_ITEMS],
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
