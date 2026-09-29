"""Read-only performance comparisons with one storefront/calendar contract."""
from datetime import datetime

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, CELESTE_STORE_NAMES, SALE_ORDER_LIVE_STATES
from backend.app.services.order_history import customer_order_query


def validate_filters(month: str | None, store_id: int | None):
    if store_id is not None and store_id not in CELESTE_STORE_IDS:
        raise ValueError("Choose a configured Celeste outlet.")
    if month is not None:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
            if parsed.strftime("%Y-%m") != month:
                raise ValueError()
        except ValueError:
            raise ValueError("Month must be YYYY-MM.") from None


async def get_performance(month: str | None = None, store_id: int | None = None):
    validate_filters(month, store_id)
    metadata = await reporting.fetch_report(customer_order_query(f"""
        SELECT to_char(date_order, 'YYYY-MM') AS month, MIN(date_order) AS first_order,
               MAX(date_order) AS last_order
        FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES + ('cancel',)}
        GROUP BY 1 ORDER BY 1
    """))
    params = {"month": month, "store_id": store_id}
    base = customer_order_query(f"""WITH selected AS (
        SELECT * FROM customer_orders
        WHERE state IN {SALE_ORDER_LIVE_STATES + ('cancel',)}
          AND (CAST(:month AS text) IS NULL OR to_char(date_order, 'YYYY-MM') = :month)
          AND (CAST(:store_id AS integer) IS NULL OR store_id = :store_id)
    ) """)
    outlets = await reporting.fetch_report(base + f"""
        SELECT s.store_id, rs.name, COUNT(*) FILTER (WHERE s.state IN {SALE_ORDER_LIVE_STATES}) AS orders,
            COALESCE(SUM(amount_total) FILTER (WHERE s.state IN {SALE_ORDER_LIVE_STATES}), 0) AS revenue,
            COUNT(*) FILTER (WHERE s.state = 'cancel') AS cancelled, COUNT(*) AS total,
            MIN(date_order) AS first_order, MAX(date_order) AS last_order
        FROM selected s LEFT JOIN res_store rs ON rs.id = s.store_id
        GROUP BY s.store_id, rs.name ORDER BY revenue DESC
    """, params)
    lines = f""" FROM selected s JOIN sale_order_line sol ON sol.order_id = s.id
        JOIN product_product pp ON pp.id = sol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN celeste_department cd ON cd.id = pt.celeste_department_id
        WHERE s.state IN {SALE_ORDER_LIVE_STATES} """
    products = await reporting.fetch_report(base + "SELECT pp.id, pt.name->>'en_US' AS name, SUM(sol.price_subtotal) AS revenue" + lines + " GROUP BY pp.id, pt.name ORDER BY revenue DESC LIMIT 10", params)
    departments = await reporting.fetch_report(base + "SELECT COALESCE(cd.name, 'Unlinked') AS name, SUM(sol.price_subtotal) AS revenue" + lines + " GROUP BY cd.name ORDER BY revenue DESC", params)
    hours = await reporting.fetch_report(base + f"SELECT EXTRACT(HOUR FROM date_order)::int AS hour, COUNT(*) AS orders FROM selected WHERE state IN {SALE_ORDER_LIVE_STATES} GROUP BY 1 ORDER BY 1", params)
    first = min((r['first_order'] for r in outlets), default=None)
    last = max((r['last_order'] for r in outlets), default=None)
    return {
        "stores": [{"id": i, "name": n} for i, n in zip(CELESTE_STORE_IDS, CELESTE_STORE_NAMES)],
        "months": [r['month'] for r in metadata],
        "source_through": metadata[-1]['last_order'].isoformat() if metadata else None,
        "start": first.isoformat() if first else None, "end": last.isoformat() if last else None,
        "latest_month_included": bool(metadata) and (month is None or month == metadata[-1]['month']),
        "timezone": "Asia/Colombo", "month": month, "store_id": store_id,
        "outlets": [{"id": r['store_id'], "name": r['name'], "orders": r['orders'], "revenue": float(r['revenue']), "cancellation_rate_pct": round(r['cancelled'] / r['total'] * 100, 2) if r['total'] else None} for r in outlets],
        "products": [{"id": r['id'], "name": r['name'] or 'Unknown', "revenue": float(r['revenue'] or 0)} for r in products],
        "departments": [{"name": r['name'], "revenue": float(r['revenue'] or 0)} for r in departments],
        "hours": [{"hour": h, "orders": next((r['orders'] for r in hours if r['hour'] == h), 0)} for h in range(24)],
    }
