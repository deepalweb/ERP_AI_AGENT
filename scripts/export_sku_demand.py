"""Run from repo root: python -m scripts.export_sku_demand [output.json].

Company-wide (all 4 outlets combined) daily demand per product (A. Product
demand forecasting -- SKU-level). Deliberately a third different prep
approach from the other export scripts, 2026-11-07:

- export_outlet_orders.py/export_outlet_revenue.py treat a missing DAY as
  an unknown ingestion gap to exclude (store-level aggregate -- a day with
  truly zero company-wide orders would be extraordinary for this business).
- export_outlet_hourly_orders.py zero-fills every HOUR, since a 24/7 store
  genuinely has slow, real zero-order hours.
- Here, a missing day for one SKU is completely normal (nobody bought that
  product that day) and must be zero-filled -- but only across a window
  where the STORES THEMSELVES are confirmed clean, otherwise a day missing
  from the order feed entirely would be miscounted as "zero demand" for
  every product at once. So this first derives the safe window as the
  INTERSECTION of all 4 outlets' own cleaned date ranges (reusing
  prepare_series's per-outlet order-count quality check, the same one
  export_outlet_orders.py already relies on) -- only inside that window is
  a day's absence from a product's own rows trustworthy as real zero
  demand, not a feed gap.

Eligibility: at least MIN_NONZERO_DAYS distinct days with a sale in the
window (not necessarily consecutive -- zero-sale days are expected and
kept), and at least one sale within RECENT_SALE_DAYS of the window's end
(excludes discontinued products whose own history looks dense but is
stale). Verified live, 2026-11-07: of SKUs active in the last 60 days,
~700 have 50+ days of the last 60 with a sale; this export's window is
wider (the full safe intersection, ~7 months) with a lower per-SKU bar
(30 nonzero days total, not 50 of 60), so more of the real long-tail
qualifies while the genuinely one-off/dead SKUs (lifetime history under
the bar) still don't.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

from analytics.outlet_orders import prepare_series
from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from backend.app.services.order_history import customer_order_query

MIN_NONZERO_DAYS = 30
RECENT_SALE_DAYS = 14


def _safe_window(conn, cutoff):
    """The latest date every outlet's own order-count series is jointly
    clean from -- see module docstring."""
    rows = list(
        conn.execute(
            text(
                customer_order_query(f"""
            SELECT store_id, DATE(date_order) AS day, COUNT(*) AS orders
            FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES} AND date_order < :cutoff
            GROUP BY store_id, DATE(date_order) ORDER BY store_id, day
        """)
            ),
            {"cutoff": cutoff},
        ).mappings()
    )
    series, _ = prepare_series(rows, cutoff)
    if set(series) != {str(s) for s in CELESTE_STORE_IDS}:
        raise ValueError("At least one configured outlet has no clean order-count history")
    return max(s["dates"][0] for s in series.values())


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    with reporting.get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '120s'"))
        latest = conn.execute(
            text(
                customer_order_query(
                    f"SELECT MAX(date_order) FROM customer_orders WHERE state IN {SALE_ORDER_LIVE_STATES}"
                )
            )
        ).scalar_one()
        if latest is None:
            raise ValueError("No scoped sales history")
        today = (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date()
        cutoff = min(latest.date(), today)
        window_start = datetime.fromisoformat(_safe_window(conn, cutoff)).date()

        rows = list(
            conn.execute(
                text(f"""
            SELECT sol.product_id, pt.name->>'en_US' AS product, pt.default_code AS sku,
                   DATE(so.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Colombo') AS day,
                   SUM(sol.product_uom_qty) AS qty
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            JOIN product_product pp ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
              AND so.date_order >= :window_start AND so.date_order < :cutoff
            GROUP BY sol.product_id, pt.name, pt.default_code,
                     DATE(so.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Colombo')
        """),
                {"window_start": window_start, "cutoff": cutoff},
            ).mappings()
        )

    all_days = [window_start + timedelta(days=i) for i in range((cutoff - window_start).days)]
    recent_cutoff = all_days[-RECENT_SALE_DAYS] if len(all_days) >= RECENT_SALE_DAYS else all_days[0]

    by_product = {}
    names = {}
    for row in rows:
        pid = int(row["product_id"])
        names[pid] = (row["product"], row["sku"])
        by_product.setdefault(pid, {})[row["day"]] = float(row["qty"])

    series, skipped_inactive, skipped_sparse = {}, 0, 0
    for pid, values_by_day in by_product.items():
        nonzero_days = len(values_by_day)
        if nonzero_days < MIN_NONZERO_DAYS:
            skipped_sparse += 1
            continue
        if max(values_by_day) < recent_cutoff:
            skipped_inactive += 1
            continue
        product, sku = names[pid]
        series[str(pid)] = {
            "product": product,
            "sku": sku,
            "dates": [d.isoformat() for d in all_days],
            "values": [values_by_day.get(d, 0.0) for d in all_days],
        }

    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "timezone": "Asia/Colombo",
        "latest_source_local": latest.isoformat(),
        "end_exclusive": cutoff.isoformat(),
        "window_start": window_start.isoformat(),
        "window_days": len(all_days),
        "eligible_products": len(series),
        "skipped_sparse_products": skipped_sparse,
        "skipped_inactive_products": skipped_inactive,
        "store_ids": list(CELESTE_STORE_IDS),
        "series": series,
        "caveats": [
            "Company-wide (all 4 outlets combined), not per-outlet demand",
            (
                f"Window is the intersection of every outlet's own clean order-count history "
                f"({window_start.isoformat()} to {cutoff.isoformat()}); days inside it with no "
                f"rows for a product are zero-filled as real zero demand, not excluded"
            ),
            "Retrospective current-state orders; no historical ingestion snapshots",
        ],
    }
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/forecasting/sku_demand_source.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "series"}, indent=2))


if __name__ == "__main__":
    main()
