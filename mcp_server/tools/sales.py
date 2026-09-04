from datetime import datetime, date
from calendar import monthrange
from typing import Optional

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS


def _period_bounds(period: Optional[str]):
    """Return (start_date, end_date) for a YYYY-MM period string or last month if None."""
    if period:
        year, month = map(int, period.split("-"))
        start = date(year, month, 1)
    else:
        today = date.today()
        first = today.replace(day=1)
        # last month
        if first.month == 1:
            start = first.replace(year=first.year - 1, month=12)
        else:
            start = first.replace(month=first.month - 1)
    last_day = monthrange(start.year, start.month)[1]
    end = date(start.year, start.month, last_day)  # inclusive
    # use ISO date strings
    return start.isoformat(), (end.replace(day=end.day) ) .isoformat()


def get_sales_summary(period: Optional[str] = None):
    """Return a structured sales summary for `period` (YYYY-MM). Defaults to last month.

    Tries `sale_order` first; falls back to `account_move` if necessary.
    """
    start, end = _period_bounds(period)

    # Try sale_order table (scoped to Celeste's own outlets — see store_scope.py)
    sql_so = f"""
    SELECT
      SUM(amount_total) AS revenue,
      COUNT(id) AS orders,
      AVG(amount_total) AS average_order_value
    FROM sale_order
    WHERE state IN ('sale','done')
      AND store_id IN {CELESTE_STORE_IDS}
      AND date_order >= :start
      AND date_order <= :end
    """
    rows = reporting.run_query(sql_so, {"start": start, "end": end})
    if not rows or rows[0].get("revenue") is None:
        # Fallback to account_move (posted customer invoices)
        sql_am = """
        SELECT
          SUM(amount_total) AS revenue,
          COUNT(id) AS orders,
          AVG(amount_total) AS average_order_value
        FROM account_move
        WHERE move_type = 'out_invoice'
          AND invoice_date >= :start
          AND invoice_date <= :end
        AND state = 'posted'
        """
        rows = reporting.run_query(sql_am, {"start": start, "end": end})

    row = rows[0] if rows else {}
    revenue = float(row.get("revenue") or 0)
    orders = int(row.get("orders") or 0)
    avg = float(row.get("average_order_value") or 0)

    # previous period
    # compute previous month bounds
    s_year, s_month = map(int, start.split("-")[:2]) if isinstance(start, str) else (start.year, start.month)
    if s_month == 1:
        ps, pm = s_year - 1, 12
    else:
        ps, pm = s_year, s_month - 1
    prev_period = f"{ps:04d}-{pm:02d}"
    pstart, pend = _period_bounds(prev_period)

    sql_prev = f"""
    SELECT SUM(amount_total) AS revenue FROM (
      SELECT amount_total FROM sale_order
        WHERE state IN ('sale','done') AND store_id IN {CELESTE_STORE_IDS}
          AND date_order >= :start AND date_order <= :end
      UNION ALL
      SELECT amount_total FROM account_move WHERE move_type='out_invoice' AND invoice_date >= :start AND invoice_date <= :end AND state='posted'
    ) t
    """
    prow = reporting.run_query(sql_prev, {"start": pstart, "end": pend})
    prev_row = prow[0] if prow else {}
    prev_revenue = float(prev_row.get("revenue") or 0)

    growth = None
    if prev_revenue:
        growth = (revenue - prev_revenue) / prev_revenue * 100

    # Flag when the requested period isn't fully covered by the data yet, since
    # a partial period's revenue isn't comparable to a full previous month.
    # Anchored to the data's own latest order date, not date.today() — the DB
    # snapshot can lag real time by weeks, which would otherwise make a fully
    # elapsed month look "partial" or (worse) a genuinely partial one look done.
    as_of_rows = reporting.run_query(
        f"SELECT MAX(date_order) AS max_d FROM sale_order WHERE state IN ('sale','done') AND store_id IN {CELESTE_STORE_IDS}"
    )
    as_of = as_of_rows[0].get("max_d") if as_of_rows else None
    days_in_period = monthrange(s_year, s_month)[1]
    period_start = date(s_year, s_month, 1)
    period_end = date(s_year, s_month, days_in_period)

    if as_of is None or as_of.date() >= period_end:
        is_partial_period = False
        days_elapsed = days_in_period
    elif as_of.date() < period_start:
        is_partial_period = True
        days_elapsed = 0
    else:
        is_partial_period = True
        days_elapsed = as_of.date().day

    return {
        "period": period or f"{start[:7]}",
        "revenue": revenue,
        "orders": orders,
        "average_order_value": avg,
        "growth_vs_previous_month_pct": round(growth, 2) if growth is not None else None,
        "is_partial_period": is_partial_period,
        "days_elapsed": days_elapsed,
        "days_in_period": days_in_period,
    }
