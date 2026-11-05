"""Outlet order-forecast outcome tracking: records each day's TimesFM
prediction (made in analytics/forecast_outlet_orders.py, read via
forecast.get_outlet_order_forecast()) against what actually happened, once
that forecast date is fully over. The one offline evaluation baked into
forecast.HOLDOUT_ACCURACY (18 Sep 2026) never updates -- this is the live,
growing counterpart docs/outlet-forecast-evaluation.md calls out as pending:
"app-store forecast/outcome history."

Same pattern as inventory_kpi_history.py / customer_predictions.py's
lifecycle snapshots: a plain appdb table, filled in incrementally, honestly
returning None rather than a number until real outcomes have accumulated.
"""

from datetime import date, datetime, timedelta

from backend.app.db import appdb, reporting
from backend.app.db.store_scope import SALE_ORDER_LIVE_STATES

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS outlet_forecast_outcomes (
            store_id INTEGER NOT NULL,
            forecast_date TEXT NOT NULL,
            predicted_orders REAL NOT NULL,
            q10 REAL,
            q90 REAL,
            predicted_at TEXT NOT NULL,
            actual_orders INTEGER,
            evaluated_at TEXT,
            PRIMARY KEY (store_id, forecast_date)
        )
    """)
    _SCHEMA_READY = True


def record_predictions(outlet_forecast: dict | None) -> None:
    """Called once per day (brain.py) right after reading the latest TimesFM
    artifact. A (store_id, forecast_date) pair already recorded is left
    alone -- the FIRST prediction made for a date is what gets graded, not
    whichever run most recently happened to predict that same date again."""
    if not outlet_forecast:
        return
    _ensure_schema()
    generated_at = outlet_forecast.get("generated_at") or datetime.utcnow().isoformat()
    for outlet in outlet_forecast.get("outlets", []):
        for i, forecast_date in enumerate(outlet["forecast_dates"]):
            existing = appdb.run_query(
                "SELECT 1 AS n FROM outlet_forecast_outcomes WHERE store_id = :s AND forecast_date = :d",
                {"s": outlet["store_id"], "d": forecast_date},
            )
            if existing:
                continue
            appdb.run_query(
                """
                    INSERT INTO outlet_forecast_outcomes
                        (store_id, forecast_date, predicted_orders, q10, q90, predicted_at)
                    VALUES (:store_id, :forecast_date, :predicted_orders, :q10, :q90, :predicted_at)
                """,
                {
                    "store_id": outlet["store_id"],
                    "forecast_date": forecast_date,
                    "predicted_orders": outlet["point"][i],
                    "q10": outlet["q10"][i],
                    "q90": outlet["q90"][i],
                    "predicted_at": generated_at,
                },
            )


def record_actuals() -> None:
    """Fills in actual_orders for any forecast_date that's fully in the past
    and not yet graded. Never grades today -- its order count is still
    accumulating, not a finished actual."""
    _ensure_schema()
    today = date.today().isoformat()
    pending = appdb.run_query(
        "SELECT store_id, forecast_date FROM outlet_forecast_outcomes "
        "WHERE actual_orders IS NULL AND forecast_date < :today",
        {"today": today},
    )
    for row in pending:
        start = row["forecast_date"]
        end = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
        actual = reporting.run_query(
            f"""
                SELECT COUNT(*) AS n FROM sale_order
                WHERE store_id = :store_id AND state IN {SALE_ORDER_LIVE_STATES}
                  AND date_order >= :start AND date_order < :end
            """,
            {"store_id": row["store_id"], "start": start, "end": end},
        )
        appdb.run_query(
            "UPDATE outlet_forecast_outcomes SET actual_orders = :n, evaluated_at = :now "
            "WHERE store_id = :s AND forecast_date = :d",
            {
                "n": actual[0]["n"] if actual else 0,
                "now": datetime.utcnow().isoformat(),
                "s": row["store_id"],
                "d": row["forecast_date"],
            },
        )


def get_track_record(window_days: int = 30) -> dict | None:
    """Live WAPE over the last `window_days` worth of GRADED predictions
    (actual known) -- None until at least one has been graded, same honesty
    convention as inventory_kpi_history.get_inventory_kpi_deltas. This is a
    live-running figure, distinct from forecast.HOLDOUT_ACCURACY's one frozen
    offline evaluation."""
    _ensure_schema()
    cutoff = (date.today() - timedelta(days=window_days)).isoformat()
    rows = appdb.run_query(
        "SELECT forecast_date, predicted_orders, actual_orders FROM outlet_forecast_outcomes "
        "WHERE actual_orders IS NOT NULL AND forecast_date >= :cutoff",
        {"cutoff": cutoff},
    )
    if not rows:
        return None
    abs_error = sum(abs(r["predicted_orders"] - r["actual_orders"]) for r in rows)
    actual_total = sum(r["actual_orders"] for r in rows)
    return {
        "graded_predictions": len(rows),
        "earliest_date": min(r["forecast_date"] for r in rows),
        "latest_date": max(r["forecast_date"] for r in rows),
        "wape_pct": round(abs_error / actual_total * 100, 2) if actual_total else None,
    }
