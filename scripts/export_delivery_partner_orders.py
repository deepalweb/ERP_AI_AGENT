"""Run from repo root: python -m scripts.export_delivery_partner_orders [output.json].

Daily Uber + PickMe order volume per Celeste outlet -- the delivery-partner
"workload" series (D. Logistics forecasting). Deliberately counts EVERY
order row regardless of state: a failed or cancelled delivery still consumed
dispatch/rider attention, so it belongs in a workload count the same as a
completed one (this is a different question from uber_failure_rate/
pickme_cancellation_rate in intelligence.py, which specifically need the
success/failure split -- this series wants total attempted volume).

uber_order.create_date and pickme_order.create_date are raw UTC timestamps,
confirmed live against sale_order.date_order on the same linked order
(2026-11-07) -- not yet localized the way order_history.py's customer_orders
CTE already localizes sale_order, so it's done explicitly here for correct
day-bucketing (a few hours' offset barely matters when intelligence.py's
existing uber/pickme queries group by MONTH, but matters a lot for a daily
series).

Same prepare_series/eligibility rules as export_outlet_orders.py.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

from analytics.outlet_orders import prepare_series
from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS

LOCALIZE = "create_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Colombo'"


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    with reporting.get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '90s'"))
        latest = conn.execute(
            text(f"""
                SELECT MAX(d) FROM (
                    SELECT MAX({LOCALIZE}) AS d FROM uber_order WHERE store_id IN {CELESTE_STORE_IDS}
                    UNION ALL
                    SELECT MAX({LOCALIZE}) AS d FROM pickme_order WHERE store_id IN {CELESTE_STORE_IDS}
                ) latest
            """)
        ).scalar_one()
        if latest is None:
            raise ValueError("No scoped delivery-partner order history")
        today = (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date()
        cutoff = min(latest.date(), today)
        rows = list(
            conn.execute(
                text(f"""
            SELECT store_id, day, SUM(orders) AS orders FROM (
                SELECT store_id, DATE({LOCALIZE}) AS day, COUNT(*) AS orders
                FROM uber_order WHERE store_id IN {CELESTE_STORE_IDS} AND {LOCALIZE} < :cutoff
                GROUP BY store_id, DATE({LOCALIZE})
                UNION ALL
                SELECT store_id, DATE({LOCALIZE}) AS day, COUNT(*) AS orders
                FROM pickme_order WHERE store_id IN {CELESTE_STORE_IDS} AND {LOCALIZE} < :cutoff
                GROUP BY store_id, DATE({LOCALIZE})
            ) both_platforms
            GROUP BY store_id, day ORDER BY store_id, day
        """),
                {"cutoff": cutoff},
            ).mappings()
        )
    series, quality = prepare_series(rows, cutoff, value_key="orders")
    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "timezone": "Asia/Colombo",
        "latest_source_local": latest.isoformat(),
        "end_exclusive": cutoff.isoformat(),
        "store_ids": list(CELESTE_STORE_IDS),
        "quality": quality,
        "series": series,
        "caveats": [
            (
                "Combines Uber + PickMe order counts; does not distinguish platform or state "
                "(failed/cancelled attempts count the same as completed ones -- this is a workload "
                "volume, not a success-rate metric)"
            ),
            "Unknown days are excluded, not zero-filled",
            "Retrospective current-state orders; no historical ingestion snapshots",
        ],
    }
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/forecasting/delivery_partner_orders_source.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "series"}, indent=2))


if __name__ == "__main__":
    main()
