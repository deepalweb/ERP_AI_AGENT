"""Loyalty points activity and store promotions — neither existed as a
queryable signal before this tool.

loyalty_card.partner_id is only 1% populated — don't use it to identify the
customer. loyalty_card.order_id links to sale_order at 93%, and the customer
comes from there (sale_order.partner_id) instead; verified loyalty_history's
own order_model is 100% 'sale.order', so this join path is safe (no POS-order
mix-in to worry about).

store_promotion has no store dimension (company-wide by definition, scoped
only by applied_to: uber/pickme/both) — so it isn't filtered by
CELESTE_STORE_IDS the way order-based queries are.

store_promotion_line.celeste_contribution/supplier_contribution are NULL on
every row checked (not populated at all, despite an initial pass reporting
them as "100% populated" — that check apparently only confirmed the columns
exist, not that they hold values) — dropped. The real discount value is
price - promo_price, with promotion_type telling you whether the underlying
mechanic was a flat rate_amount (295 rows) or a rate_percentage (28,774 rows).

loyalty_history.issued/used are equal on 99% of rows (near-instant
redemption is the norm for this program, not a save-and-spend mechanic) —
the ~1% where they differ (used = 0, issued > 0) is the genuine unredeemed-
points tail. Report both, but don't frame "100% redemption" as a notable
finding — it's the baseline, not an achievement.
"""

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from mcp_server.tools.sales import _period_bounds, _period_label

TOP_N = 10

LOYALTY_SUMMARY_SQL = f"""
SELECT SUM(lh.issued) AS total_issued, SUM(lh.used) AS total_used,
       COUNT(DISTINCT lh.card_id) AS active_cards
FROM loyalty_history lh
JOIN loyalty_card lc ON lc.id = lh.card_id
JOIN sale_order so ON so.id = lc.order_id
WHERE so.store_id IN {CELESTE_STORE_IDS} AND lh.create_date >= :start AND lh.create_date <= :end
"""

LOYALTY_TOP_CUSTOMERS_SQL = f"""
SELECT rp.name AS customer, SUM(lh.issued) AS issued, SUM(lh.used) AS used
FROM loyalty_history lh
JOIN loyalty_card lc ON lc.id = lh.card_id
JOIN sale_order so ON so.id = lc.order_id
JOIN res_partner rp ON rp.id = so.partner_id
WHERE so.store_id IN {CELESTE_STORE_IDS} AND lh.create_date >= :start AND lh.create_date <= :end
GROUP BY rp.name
ORDER BY issued DESC
LIMIT {TOP_N}
"""

ACTIVE_PROMOTIONS_SQL = """
SELECT sp.name, sp.applied_to, sp.start_date, sp.end_date,
       COUNT(spl.id) AS product_count,
       SUM(spl.price - spl.promo_price) AS total_discount_value,
       AVG(spl.rate_percentage) FILTER (WHERE spl.rate_percentage > 0) AS avg_discount_pct
FROM store_promotion sp
LEFT JOIN store_promotion_line spl ON spl.promotion_id = sp.id
WHERE sp.state = 'approved' AND sp.start_date <= :end AND sp.end_date >= :start
GROUP BY sp.id, sp.name, sp.applied_to, sp.start_date, sp.end_date
ORDER BY total_discount_value DESC NULLS LAST
"""


def get_loyalty_and_promotions(period: str | None = None, start_date: str | None = None, end_date: str | None = None):
    start, end = _period_bounds(period, start_date, end_date)
    params = {"start": start, "end": end}

    summary_rows = reporting.run_query(LOYALTY_SUMMARY_SQL, params)
    summary = summary_rows[0] if summary_rows else {}
    top_customers = reporting.run_query(LOYALTY_TOP_CUSTOMERS_SQL, params)
    promotions = reporting.run_query(ACTIVE_PROMOTIONS_SQL, params)

    total_issued = float(summary.get("total_issued") or 0)
    total_used = float(summary.get("total_used") or 0)

    return {
        "period": _period_label(period, start, end, start_date, end_date),
        "points_issued": total_issued,
        "points_used": total_used,
        "redemption_rate_pct": round(total_used / total_issued * 100, 1) if total_issued else None,
        "active_cards": int(summary.get("active_cards") or 0),
        "top_customers_by_points": [
            {
                "customer": r["customer"] or "Unknown",
                "issued": float(r["issued"] or 0),
                "used": float(r["used"] or 0),
            }
            for r in top_customers
        ],
        "active_promotions": [
            {
                "name": r["name"],
                "applied_to": r["applied_to"],
                "start_date": r["start_date"].isoformat() if r["start_date"] else None,
                "end_date": r["end_date"].isoformat() if r["end_date"] else None,
                "product_count": int(r["product_count"] or 0),
                "total_discount_value": round(float(r["total_discount_value"] or 0), 2),
                "avg_discount_pct": round(float(r["avg_discount_pct"]), 1) if r["avg_discount_pct"] is not None else None,
            }
            for r in promotions
        ],
    }
