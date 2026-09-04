from datetime import date

import anyio

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

LOW_STOCK_THRESHOLD = 10

SALES_BY_MONTH_SQL = f"""
SELECT date_trunc('month', date_order) AS month, state, COUNT(*) AS c, SUM(amount_total) AS rev
FROM sale_order
WHERE state IN ('sale', 'cancel') AND store_id IN {CELESTE_STORE_IDS}
GROUP BY 1, 2
ORDER BY 1
"""

TOP_PRODUCTS_SQL = f"""
SELECT pt.name->>'en_US' AS name, SUM(sol.price_subtotal) AS revenue, SUM(sol.product_uom_qty) AS qty
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
JOIN product_product pp ON pp.id = sol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
GROUP BY pt.name
ORDER BY revenue DESC
LIMIT 8
"""

TOP_CUSTOMERS_SQL = f"""
SELECT rp.name AS name, SUM(so.amount_total) AS revenue, COUNT(*) AS orders
FROM sale_order so
JOIN res_partner rp ON rp.id = so.partner_id
WHERE so.state = 'sale' AND so.store_id IN {CELESTE_STORE_IDS}
GROUP BY rp.name
ORDER BY revenue DESC
LIMIT 8
"""

INVENTORY_SQL = f"""
SELECT
  COUNT(*) FILTER (WHERE total_qty <= {LOW_STOCK_THRESHOLD} AND total_qty > 0) AS low_stock,
  COUNT(*) FILTER (WHERE total_qty <= 0) AS out_of_stock,
  COUNT(*) AS tracked_products
FROM (
  SELECT sq.product_id, SUM(sq.quantity) AS total_qty
  FROM stock_quant sq
  JOIN stock_location sl ON sl.id = sq.location_id
  JOIN product_product pp ON pp.id = sq.product_id
  JOIN product_template pt ON pt.id = pp.product_tmpl_id
  WHERE sl.usage = 'internal' AND pt.celeste_product_type = 'INVENTORY'
  GROUP BY sq.product_id
) t
"""

PO_SPEND_SQL = """
SELECT date_trunc('month', date_order) AS month, SUM(amount_total) AS spend, COUNT(*) AS orders
FROM purchase_order
WHERE state = 'purchase'
GROUP BY 1
ORDER BY 1
"""


def _month_key(dt) -> str:
    return dt.strftime("%Y-%m")


def _mark_partial(months: list[dict]) -> None:
    """Only the most recent month in a sorted list can possibly be incomplete —
    a later month existing at all proves every earlier one already elapsed.
    Anchored to the data itself, not date.today() (see intelligence.get_trend
    for the full reasoning); applied per-list since different tables can have
    different sync freshness."""
    for i, m in enumerate(months):
        m["is_partial"] = i == len(months) - 1


def _pct_change(current: float, previous: float):
    if not previous:
        return None
    return round((current - previous) / previous * 100, 2)


async def get_sales_and_orders():
    """Monthly revenue/order-count and cancellation health, from one grouped query."""
    rows = await reporting.fetch_report(SALES_BY_MONTH_SQL)

    by_month: dict[str, dict] = {}
    for row in rows:
        key = _month_key(row["month"])
        entry = by_month.setdefault(key, {"month": key, "sale_orders": 0, "revenue": 0.0, "cancelled_orders": 0})
        if row["state"] == "sale":
            entry["sale_orders"] = int(row["c"])
            entry["revenue"] = float(row["rev"] or 0)
        elif row["state"] == "cancel":
            entry["cancelled_orders"] = int(row["c"])

    months = [by_month[k] for k in sorted(by_month)]
    _mark_partial(months)
    for m in months:
        total = m["sale_orders"] + m["cancelled_orders"]
        m["cancellation_rate_pct"] = round(m["cancelled_orders"] / total * 100, 2) if total else 0.0

    # Headline figures come from the latest *complete* month, since a partial
    # in-progress month isn't comparable to a full prior one.
    complete_months = [m for m in months if not m["is_partial"]]
    headline = complete_months[-1] if complete_months else (months[-1] if months else None)
    previous = None
    if headline:
        idx = months.index(headline)
        previous = months[idx - 1] if idx > 0 else None

    total_sale = sum(m["sale_orders"] for m in months)
    total_cancel = sum(m["cancelled_orders"] for m in months)
    total_orders = total_sale + total_cancel

    return {
        "months": months,
        "headline_month": headline["month"] if headline else None,
        "headline_revenue": headline["revenue"] if headline else 0.0,
        "headline_orders": headline["sale_orders"] if headline else 0,
        "revenue_growth_pct": _pct_change(headline["revenue"], previous["revenue"]) if headline and previous else None,
        "orders_growth_pct": _pct_change(headline["sale_orders"], previous["sale_orders"]) if headline and previous else None,
        "overall_cancellation_rate_pct": round(total_cancel / total_orders * 100, 2) if total_orders else 0.0,
        "total_orders": total_orders,
        "total_cancelled": total_cancel,
    }


async def get_top_products():
    rows = await reporting.fetch_report(TOP_PRODUCTS_SQL)
    return [
        {"name": r["name"] or "Unknown", "revenue": float(r["revenue"] or 0), "qty": float(r["qty"] or 0)}
        for r in rows
    ]


async def get_top_customers():
    rows = await reporting.fetch_report(TOP_CUSTOMERS_SQL)
    return [
        {"name": r["name"] or "Unknown", "revenue": float(r["revenue"] or 0), "orders": int(r["orders"] or 0)}
        for r in rows
    ]


async def get_inventory():
    inv_rows = await reporting.fetch_report(INVENTORY_SQL)
    po_rows = await reporting.fetch_report(PO_SPEND_SQL)
    inv = inv_rows[0] if inv_rows else {}

    months = [
        {
            "month": _month_key(r["month"]),
            "spend": float(r["spend"] or 0),
            "orders": int(r["orders"] or 0),
        }
        for r in po_rows
    ]
    _mark_partial(months)

    return {
        "low_stock_count": int(inv.get("low_stock") or 0),
        "out_of_stock_count": int(inv.get("out_of_stock") or 0),
        "tracked_products": int(inv.get("tracked_products") or 0),
        "low_stock_threshold": LOW_STOCK_THRESHOLD,
        "po_spend_by_month": months,
    }


async def get_summary():
    """Fetch all dashboard sections concurrently and assemble the response."""
    results: dict = {}

    async def run(key, coro_fn):
        results[key] = await coro_fn()

    async with anyio.create_task_group() as tg:
        tg.start_soon(run, "sales", get_sales_and_orders)
        tg.start_soon(run, "top_products", get_top_products)
        tg.start_soon(run, "top_customers", get_top_customers)
        tg.start_soon(run, "inventory", get_inventory)

    return {
        "generated_at": date.today().isoformat(),
        "sales": results["sales"],
        "top_products": results["top_products"],
        "top_customers": results["top_customers"],
        "inventory": results["inventory"],
    }
