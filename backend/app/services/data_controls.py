"""Business Data Controls: signals about the RELIABILITY of the data other
findings and tools already depend on, not a business outcome in itself.

Two signals, both previously only existing as caveats embedded inside other
findings, never their own company-wide check:
- Negative recorded stock (stock_alerts.py already flags this per top-
  selling product; here it's the full, active INVENTORY-scoped count) --
  a data-integrity condition (sold past what the system thought was on
  hand), never confirmed loss. The cause (a late goods receipt, a POS/sync
  timing issue, a genuine unrecorded loss) is not established by this
  signal alone.
- Catalog cost coverage (the same source-cost/catalog-fallback/no-cost
  classification stock_counts.py already uses for count-line valuation,
  applied here to the whole catalog) -- verified live that coverage is
  actually near-complete; the point of this signal is the small residual
  gap, never a claim that valuations elsewhere are unreliable.

Same usage='internal' location scope and celeste_product_type='INVENTORY'
filter every other real inventory query in this codebase already uses, so
this never disagrees with what stock_alerts.py/inventory.py report for the
same products.
"""

from datetime import date

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from backend.app.services.inventory import _usable_cost

NEGATIVE_STOCK_SQL = f"""
    WITH on_hand AS (
        SELECT sq.product_id, SUM(sq.quantity) AS qty
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        JOIN res_store rs ON rs.warehouse_id = sl.warehouse_id
        WHERE sl.usage = 'internal' AND rs.id IN {CELESTE_STORE_IDS}
        GROUP BY sq.product_id
    )
    SELECT pt.id AS tmpl_id, pt.name->>'en_US' AS name, pt.default_code AS sku, oh.qty,
           pt.celeste_cost_price_ex_vat AS cost, pt.list_price
    FROM on_hand oh
    JOIN product_product pp ON pp.id = oh.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    WHERE pt.celeste_product_type = 'INVENTORY' AND pt.active = true AND oh.qty < 0
    ORDER BY oh.qty ASC
"""

COST_COVERAGE_SQL = """
    SELECT
        count(*) AS total,
        count(*) FILTER (WHERE celeste_cost_price_ex_vat > 0) AS has_direct_cost,
        count(*) FILTER (
            WHERE (celeste_cost_price_ex_vat IS NULL OR celeste_cost_price_ex_vat = 0) AND list_price > 0
        ) AS catalog_fallback_only,
        count(*) FILTER (
            WHERE (celeste_cost_price_ex_vat IS NULL OR celeste_cost_price_ex_vat = 0)
              AND (list_price IS NULL OR list_price = 0)
        ) AS no_cost_at_all
    FROM product_template WHERE celeste_product_type = 'INVENTORY' AND active = true
"""

NO_COST_PRODUCTS_SQL = """
    SELECT name->>'en_US' AS name, default_code AS sku
    FROM product_template
    WHERE celeste_product_type = 'INVENTORY' AND active = true
      AND (celeste_cost_price_ex_vat IS NULL OR celeste_cost_price_ex_vat = 0)
      AND (list_price IS NULL OR list_price = 0)
    ORDER BY name
"""


def get_negative_stock():
    rows = reporting.run_query(NEGATIVE_STOCK_SQL)
    products = []
    total_value = 0.0
    for r in rows:
        qty = float(r["qty"])
        usable_cost = _usable_cost(r["cost"], r["list_price"])
        value = abs(qty) * usable_cost if usable_cost is not None else None
        if value is not None:
            total_value += value
        products.append({
            "name": r["name"] or "Unknown", "sku": r["sku"], "qty": qty,
            "value_rs": round(value, 2) if value is not None else None,
        })
    products.sort(key=lambda p: p["value_rs"] or 0, reverse=True)
    return {
        "as_of_date": date.today().isoformat(),
        "count": len(products),
        "total_value_rs": round(total_value, 2),
        "products": products,
    }


def get_cost_coverage():
    rows = reporting.run_query(COST_COVERAGE_SQL)
    r = rows[0] if rows else {}
    total = int(r.get("total") or 0)
    has_direct = int(r.get("has_direct_cost") or 0)
    fallback_only = int(r.get("catalog_fallback_only") or 0)
    no_cost = int(r.get("no_cost_at_all") or 0)
    products = reporting.run_query(NO_COST_PRODUCTS_SQL) if no_cost else []
    return {
        "as_of_date": date.today().isoformat(),
        "total_products": total,
        "has_direct_cost": has_direct,
        "catalog_fallback_only": fallback_only,
        "no_cost_at_all": no_cost,
        "coverage_pct": round((has_direct + fallback_only) / total * 100, 1) if total else None,
        "uncosted_products": [{"name": p["name"] or "Unknown", "sku": p["sku"]} for p in products],
    }


def get_data_controls():
    """Combined for Ask Celeste -- both signals in one call, same bundling
    precedent as loss_and_damage.get_loss_and_damage (one thematic tool,
    not one call per sub-signal)."""
    return {"negative_stock": get_negative_stock(), "cost_coverage": get_cost_coverage()}
