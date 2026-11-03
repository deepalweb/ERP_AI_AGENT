"""Active sellers with zero on-hand stock at BOTH the main warehouse and every outlet.

User report, 2026-11-03: "MWH inventory mismatch. Products not in the MWH
primary stock or outlet primary stock." Investigated directly against the
live DB before building anything.

"Primary stock" is Odoo's own canonical concept, not a name guess:
`stock_warehouse.lot_stock_id` is the warehouse's designated main stock
location (distinct from its Input/Output/QC/Packing Zone/Transit locations).
MWH (warehouse 30, "DC MWH") is the single central warehouse that supplies
every outlet -- verified live it has no res_store mapping of its own, unlike
each outlet's 1:1 warehouse. A location's `parent_path` is used (not a name
prefix) so a warehouse whose primary stock is subdivided into storage bins
(e.g. Pannipitiya's "CPNP/PNPStock/SR/A2/01...") still has its bin-level
quantities correctly rolled up into the primary-stock total.

Verified live (2026-11-03): 285 active retail products (celeste_product_type
= 'INVENTORY', sale_ok) sold at a Celeste outlet in the last 30 days while
showing zero (or negative) quantity in BOTH MWH's primary stock AND every
outlet's own primary stock -- i.e. completely unaccounted for anywhere in the
primary supply chain despite real, continuing demand. 26 of those 285 are
fresh-production items (linked to `mrp_production.fresh_delivery_round`,
same system fresh_production.py already covers) which are produced and sold
same-day by design and never meant to carry on-hand primary-stock -- those
are excluded here as a different, already-covered flow, not a defect.

The remaining 259 are genuine shelf-stable retail SKUs (yoghurt, cheese,
ice cubes, soft drinks, pregnancy tests, etc.) that should carry a normal
on-hand balance. A floor of ORDERS_30D >= 5 (same floor stock_reconciliation.
py uses to separate a real pattern from a one-off) narrows this to 189
products, 12,478 units sold, Rs 3,315,102 at catalog cost -- a real, large
gap between recorded demand and recorded stock, not noise.

What this data cannot tell us: whether the gap is a genuine stockout (sold
past the point where stock was ever replenished), a posting/receiving step
that never landed in the primary stock location, or a stock-count adjustment
that zeroed these out without reflecting reality. Flagged as "verify the
physical count", never as a loss -- same honesty convention as
extreme_event.py.
"""

from collections import defaultdict
from datetime import date

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from mcp_server.tools import evidence_engine, impact_engine, measurement

MWH_WAREHOUSE_ID = 30  # "DC MWH" -- verified live via stock_warehouse; the one central warehouse, mapped to no res_store
WINDOW_DAYS = 30
MIN_ORDERS_30D = 5  # same floor as stock_reconciliation.py's MIN_CLUSTER_ORDERS: separates a real recurring seller from a one-off
ISSUE_METRIC_ID = "mwh_inventory_mismatch"

MISMATCH_SQL = f"""
    WITH bounds AS (
        SELECT MAX(date_order) AS as_of FROM sale_order
        WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
    ),
    primary_locs AS (
        SELECT 'mwh' AS scope, sl.parent_path AS ppath
        FROM stock_warehouse sw JOIN stock_location sl ON sl.id = sw.lot_stock_id
        WHERE sw.id = {MWH_WAREHOUSE_ID}
        UNION ALL
        SELECT 'outlet', sl.parent_path
        FROM res_store rs
        JOIN stock_warehouse sw ON sw.id = rs.warehouse_id
        JOIN stock_location sl ON sl.id = sw.lot_stock_id
        WHERE rs.id IN {CELESTE_STORE_IDS}
    ),
    mwh_qty AS (
        SELECT sq.product_id, SUM(sq.quantity) AS qty
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        JOIN primary_locs pl ON pl.scope = 'mwh' AND sl.parent_path LIKE pl.ppath || '%'
        GROUP BY sq.product_id
    ),
    outlet_qty AS (
        SELECT sq.product_id, SUM(sq.quantity) AS qty
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        JOIN primary_locs pl ON pl.scope = 'outlet' AND sl.parent_path LIKE pl.ppath || '%'
        GROUP BY sq.product_id
    ),
    recent_sellers AS (
        SELECT sol.product_id, SUM(sol.product_uom_qty) AS qty_30d, COUNT(DISTINCT so.id) AS orders_30d
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        CROSS JOIN bounds
        WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
          AND so.date_order >= bounds.as_of - INTERVAL '{WINDOW_DAYS} days'
          AND sol.product_uom_qty > 0
        GROUP BY sol.product_id
    ),
    fresh_production_products AS (
        SELECT DISTINCT product_id FROM mrp_production WHERE fresh_delivery_round IS NOT NULL
    )
    SELECT rs.product_id, pt.name->>'en_US' AS product_name, pt.default_code AS sku,
           rs.qty_30d, rs.orders_30d, COALESCE(m.qty, 0) AS mwh_qty, COALESCE(o.qty, 0) AS outlet_qty,
           pt.celeste_cost_price_ex_vat AS unit_cost, bounds.as_of
    FROM recent_sellers rs
    CROSS JOIN bounds
    JOIN product_product pp ON pp.id = rs.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN mwh_qty m ON m.product_id = rs.product_id
    LEFT JOIN outlet_qty o ON o.product_id = rs.product_id
    LEFT JOIN fresh_production_products fp ON fp.product_id = rs.product_id
    WHERE pt.celeste_product_type = 'INVENTORY' AND pt.sale_ok = true
      AND fp.product_id IS NULL
      AND COALESCE(m.qty, 0) <= 0.001 AND COALESCE(o.qty, 0) <= 0.001
      AND rs.orders_30d >= {MIN_ORDERS_30D}
    ORDER BY rs.qty_30d DESC
"""

OUTLET_BREAKDOWN_SQL = f"""
    WITH bounds AS (
        SELECT MAX(date_order) AS as_of FROM sale_order
        WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
    )
    SELECT rs.name AS outlet, SUM(sol.product_uom_qty) AS qty, COUNT(DISTINCT so.id) AS orders
    FROM sale_order_line sol
    JOIN sale_order so ON so.id = sol.order_id
    JOIN res_store rs ON rs.id = so.store_id
    CROSS JOIN bounds
    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.date_order >= bounds.as_of - INTERVAL '{WINDOW_DAYS} days'
      AND sol.product_uom_qty > 0 AND sol.product_id = ANY(:product_ids)
    GROUP BY rs.name
"""


def _breakdown(rows, key_fn, label, dimension):
    totals = defaultdict(lambda: {"current": 0.0, "lines": 0})
    for row in rows:
        bucket = totals[key_fn(row)]
        bucket["current"] += float(row["qty"])
        bucket["lines"] += 1
    return {
        "dimension": dimension, "label": label, "mode": "contribution", "baseline_month": None,
        "top_contributors": sorted(
            [{"name": name, "current": v["current"], "lines": v["lines"]} for name, v in totals.items()],
            key=lambda c: -c["current"],
        ),
    }


def _mwh_inventory_mismatch_finding():
    rows = reporting.run_query(MISMATCH_SQL)
    # Zero rows is the clean/healthy result here (the SQL already filters to
    # the threshold), not "can't tell" -- the stock_quant check is live, right
    # now, so today genuinely is this measurement's freshness.
    measurement.source_as_of(date.today().isoformat())
    if not rows:
        return None

    as_of = str(rows[0]["as_of"])[:10]
    product_ids = [r["product_id"] for r in rows]
    outlet_rows = reporting.run_query(OUTLET_BREAKDOWN_SQL, {"product_ids": product_ids})

    total_units = sum(float(r["qty_30d"]) for r in rows)
    total_value = round(sum(float(r["qty_30d"]) * float(r["unit_cost"] or 0) for r in rows), 2)
    products = [{
        "product": r["product_name"], "sku": r["sku"],
        "qty_sold_30d": float(r["qty_30d"]), "orders_30d": r["orders_30d"],
        "mwh_qty": float(r["mwh_qty"]), "outlet_qty": float(r["outlet_qty"]),
        "unit_cost": float(r["unit_cost"]) if r["unit_cost"] is not None else None,
    } for r in rows]

    checks = [
        {"source": "primary_stock_absence", "verdict": "confirmed",
         "detail": f"{len(rows)} product(s) sold at least {MIN_ORDERS_30D} times in the last {WINDOW_DAYS} days "
                   f"({total_units:g} units total) while showing zero (or negative) quantity in both MWH's "
                   "primary stock location and every outlet's own primary stock location."},
        {"source": "root_cause", "verdict": "unknown",
         "detail": "Not established: whether this is a genuine stockout, a receiving/posting step that never "
                   "landed in the primary stock location, or a stock-count adjustment that zeroed these out "
                   "without matching the physical count. Verify the physical count before treating this as a loss."},
        {"source": "fresh_production_scope", "verdict": "no_evidence_found",
         "detail": "Fresh-production items (produced and sold same-day via MRP, tracked separately by "
                   "fresh_production.py) are excluded here -- this finding is scoped to shelf-stable retail SKUs."},
    ]

    impact = {
        "kind": "mwh_inventory_mismatch",
        "financial_impact_rs": total_value,
        "count": len(rows),
        "units_affected": total_units,
        "window_days": WINDOW_DAYS,
        "confidence": impact_engine.CONFIDENCE_ESTIMATED,
        "month": as_of,
        "detail": f"{len(rows)} product(s), {total_units:g} units sold in the last {WINDOW_DAYS} days with no "
                  f"backing stock anywhere in the primary supply chain (~Rs. {total_value:,.0f} at catalog cost).",
    }

    # Cuts are fractions of this detector's own measured full-population numbers
    # (189 products / Rs 3.31M, verified live 2026-11-03), not round guesses --
    # revisit after a pilot, same convention as extreme_event.py's thresholds.
    if total_value >= 1_000_000 or len(rows) >= 100:
        priority = "critical"
    elif total_value >= 300_000 or len(rows) >= 30:
        priority = "high"
    else:
        priority = "medium"

    data_quality = evidence_engine.snapshot_data_quality_check(as_of)
    business_impact_score = impact_engine.business_impact_score(
        priority, {"consecutive_periods": 1}, impact, data_quality,
    )
    return {
        "id": f"{ISSUE_METRIC_ID}-{as_of}",
        "metric_id": ISSUE_METRIC_ID,
        "domain": "inventory",
        "title": f"{len(rows)} product(s) selling with zero recorded stock at MWH or any outlet, last {WINDOW_DAYS} days",
        "category": "critical",
        "severity": priority,
        "priority": priority,
        "business_impact_score": business_impact_score,
        "detected": date.today().isoformat(),
        "confirm": {"period": as_of, "current": len(rows), "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": [
            _breakdown(outlet_rows, lambda r: r["outlet"], "Outlet", "outlet"),
            _breakdown(
                [{"qty": p["qty_sold_30d"], "name": p["product"]} for p in products],
                lambda r: r["name"], "Product", "product",
            ),
        ],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "data_quality": data_quality,
        "recommendations": [{
            "action": "Physically verify the on-hand count for these SKUs at MWH and the affected outlets, and "
                      "check recent GRNs/stock counts for a posting gap around the same period.",
            "owner": "Inventory and warehouse teams", "priority": priority,
            "expected_benefit": "Confirms whether this is a real stockout (lost-sales risk) or a stock-location "
                                 "data error masking real on-hand stock.",
        }],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "store": None,
        "reason": "Zero primary stock despite active sales",
        "mismatch_rows": products,
    }


def build_mwh_inventory_mismatch_findings() -> measurement.FindingBatch:
    return measurement.combine([measurement.run_snapshot(ISSUE_METRIC_ID, _mwh_inventory_mismatch_finding)])
