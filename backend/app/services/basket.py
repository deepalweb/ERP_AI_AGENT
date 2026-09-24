"""Market-basket / co-purchase analysis: which products get bought together
more than chance alone would predict, within a chosen period.

Scoped to real inventory items (celeste_product_type = 'INVENTORY') only —
packaging (carrier bags) and service lines (convenience fees) get excluded,
since they ride along on nearly every order and would otherwise dominate a
plain frequency count without being a genuine cross-sell signal.

Two rankings are returned from the same underlying pair set: by_frequency
(raw co-occurrence count — the "what's commonly bought together" read) and
by_affinity (lift — how much more often the pair occurs than chance, i.e.
"what's a surprisingly strong combination"), each floored at MIN_PAIR_ORDERS
so a rare coincidence can't surface as a top result.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from mcp_server.tools.sales import _period_bounds, get_sales_summary

TOP_N = 10
MIN_PAIR_ORDERS = 15  # ignore pairs too rare to draw a conclusion from

BASKET_SQL = f"""
WITH bounds_orders AS (
    SELECT so.id AS order_id
    FROM sale_order so
    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
      AND so.date_order >= :start AND so.date_order <= :end
),
scoped_lines AS (
    SELECT DISTINCT sol.order_id, sol.product_id
    FROM sale_order_line sol
    JOIN bounds_orders bo ON bo.order_id = sol.order_id
    JOIN product_product pp ON pp.id = sol.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    WHERE pt.celeste_product_type = 'INVENTORY'
),
product_counts AS (
    SELECT product_id, COUNT(*) AS orders_with_product
    FROM scoped_lines
    GROUP BY product_id
),
total AS (
    SELECT COUNT(*) AS total_orders FROM bounds_orders
),
pairs AS (
    SELECT a.product_id AS product_a, b.product_id AS product_b, COUNT(*) AS orders_both
    FROM scoped_lines a
    JOIN scoped_lines b ON a.order_id = b.order_id AND a.product_id < b.product_id
    GROUP BY a.product_id, b.product_id
    HAVING COUNT(*) >= :min_pair_orders
)
SELECT
    pa.name->>'en_US' AS product_a_name,
    pb.name->>'en_US' AS product_b_name,
    p.orders_both,
    ca.orders_with_product AS orders_a,
    cb.orders_with_product AS orders_b,
    t.total_orders,
    ROUND(
        (p.orders_both::numeric / ca.orders_with_product)
        / (cb.orders_with_product::numeric / t.total_orders),
        2
    ) AS lift
FROM pairs p
JOIN product_counts ca ON ca.product_id = p.product_a
JOIN product_counts cb ON cb.product_id = p.product_b
JOIN product_product ppa ON ppa.id = p.product_a
JOIN product_template pa ON pa.id = ppa.product_tmpl_id
JOIN product_product ppb ON ppb.id = p.product_b
JOIN product_template pb ON pb.id = ppb.product_tmpl_id
CROSS JOIN total t
"""


def _row_to_pair(r: dict) -> dict:
    orders_both = int(r["orders_both"])
    orders_a = int(r["orders_a"])
    orders_b = int(r["orders_b"])
    return {
        "product_a": r["product_a_name"] or "Unknown",
        "product_b": r["product_b_name"] or "Unknown",
        "orders_together": orders_both,
        "product_a_order_count": orders_a,
        "product_b_order_count": orders_b,
        "confidence_a_to_b_pct": round(orders_both / orders_a * 100, 1) if orders_a else None,
        "confidence_b_to_a_pct": round(orders_both / orders_b * 100, 1) if orders_b else None,
        "lift": float(r["lift"]) if r["lift"] is not None else None,
    }


def get_basket_analysis(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    start, end = _period_bounds(period, start_date, end_date)
    summary = get_sales_summary(period, start_date, end_date)

    rows = reporting.run_query(BASKET_SQL, {"start": start, "end": end, "min_pair_orders": MIN_PAIR_ORDERS})
    total_orders = int(rows[0]["total_orders"]) if rows else 0
    pairs = [_row_to_pair(r) for r in rows]

    by_frequency = sorted(pairs, key=lambda p: p["orders_together"], reverse=True)[:TOP_N]
    by_affinity = sorted(pairs, key=lambda p: p["lift"] or 0, reverse=True)[:TOP_N]

    return {
        "period": summary["period"],
        "is_partial_period": summary["is_partial_period"],
        "total_orders": total_orders,
        "min_pair_orders": MIN_PAIR_ORDERS,
        "pairs_considered": len(pairs),
        "top_pairs_by_frequency": by_frequency,
        "top_pairs_by_affinity": by_affinity,
    }
