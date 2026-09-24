"""Run from repo root: python -m scripts.export_outlet_orders [output.json]."""

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


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    with reporting.get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '90s'"))
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
    series, quality = prepare_series(rows, cutoff)
    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "timezone": "Asia/Colombo",
        "latest_source_local": latest.isoformat(),
        "end_exclusive": cutoff.isoformat(),
        "store_ids": list(CELESTE_STORE_IDS),
        "quality": quality,
        "series": series,
        "caveats": [
            "Order rows cannot prove ingestion completeness or opening days",
            "Unknown days are excluded, not zero-filled",
            "Retrospective current-state orders; no historical ingestion snapshots",
        ],
    }
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/forecasting/orders.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "series"}, indent=2))


if __name__ == "__main__":
    main()
