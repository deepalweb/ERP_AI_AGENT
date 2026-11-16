"""Run from repo root: python -m scripts.export_outlet_hourly_orders [output.json].

Hourly order-count series per outlet (C. Order volume forecasting -- hourly).
Deliberately a different prep approach from prepare_series() (outlet_orders.py):
that function treats a missing DAY as an unknown gap to exclude, since this
is a 24/7 quick-commerce operation and a whole missing day usually means an
ingestion problem, not zero demand. A missing HOUR is different -- checked
live, 2026-11-07: over the last 30 days, ~18% of hour-buckets per outlet
have zero orders (590-595 of 720 possible hours have at least one order),
which is a real overnight-slowdown pattern for an always-open store, not a
data gap. So every hour in the window is zero-filled here, not excluded.

Horizon for the hourly forecast is hours, not the daily protocol's 7 days --
see analytics/forecast_series.py's --horizon/--step-hours/--min-points.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES

WINDOW_DAYS = 45
LOCALIZE = "date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Colombo'"


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    with reporting.get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '90s'"))
        latest = conn.execute(
            text(f"""
                SELECT MAX({LOCALIZE}) FROM sale_order
                WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
            """)
        ).scalar_one()
        if latest is None:
            raise ValueError("No scoped sales history")
        today = (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date()
        cutoff = min(latest.date(), today)
        window_start = cutoff - timedelta(days=WINDOW_DAYS)
        rows = list(
            conn.execute(
                text(f"""
            SELECT store_id, date_trunc('hour', {LOCALIZE}) AS hr, COUNT(*) AS orders
            FROM sale_order
            WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
              AND {LOCALIZE} >= :window_start AND {LOCALIZE} < :cutoff
            GROUP BY store_id, date_trunc('hour', {LOCALIZE})
        """),
                {"window_start": window_start, "cutoff": cutoff},
            ).mappings()
        )

    by_outlet = {}
    for row in rows:
        by_outlet.setdefault(int(row["store_id"]), {})[row["hr"]] = int(row["orders"])

    cutoff_hour = datetime(cutoff.year, cutoff.month, cutoff.day)
    start_hour = datetime(window_start.year, window_start.month, window_start.day)
    all_hours = [start_hour + timedelta(hours=i) for i in range(int((cutoff_hour - start_hour).total_seconds() // 3600))]

    series = {}
    for outlet_id in CELESTE_STORE_IDS:
        values_by_hour = by_outlet.get(outlet_id, {})
        series[str(outlet_id)] = {
            "dates": [h.isoformat() for h in all_hours],
            "values": [values_by_hour.get(h, 0) for h in all_hours],  # zero-filled -- see module docstring
        }

    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "timezone": "Asia/Colombo",
        "latest_source_local": latest.isoformat(),
        "end_exclusive": cutoff.isoformat(),
        "store_ids": list(CELESTE_STORE_IDS),
        "series": series,
        "caveats": [
            (
                "Every hour in the window is zero-filled, not excluded -- a real pattern for a "
                "24/7 operation, not a data gap (see module docstring for the verified zero-hour rate)"
            ),
            "Retrospective current-state orders; no historical ingestion snapshots",
        ],
    }
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/forecasting/outlet_hourly_orders_source.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "series"}, indent=2))


if __name__ == "__main__":
    main()
