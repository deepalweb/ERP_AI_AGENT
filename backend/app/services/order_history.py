"""Shared read-only order contract for customer and outlet forecasting.

Odoo date_order is a UTC timestamp without time zone. Convert once, before
grouping dates or constructing customer windows. Scope is storefront ID,
never warehouse ID. The source's latest day may be incomplete.
"""

from backend.app.db.store_scope import CELESTE_STORE_IDS

BUSINESS_TIMEZONE = "Asia/Colombo"
SCOPED_ORDERS_CTE = f"""
customer_orders AS (
    SELECT id, partner_id, store_id, state, amount_total,
           pickme_order_id, uber_order_id,
           date_order AT TIME ZONE 'UTC' AT TIME ZONE '{BUSINESS_TIMEZONE}' AS date_order
    FROM sale_order
    WHERE store_id IN {CELESTE_STORE_IDS}
)
"""


def customer_order_query(sql: str) -> str:
    """Compose a query written against the explicit customer_orders relation."""
    sql = sql.strip()
    if sql.upper().startswith("WITH "):
        return "WITH " + SCOPED_ORDERS_CTE + ", " + sql[5:]
    return "WITH " + SCOPED_ORDERS_CTE + " " + sql
