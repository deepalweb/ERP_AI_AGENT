"""Inventory KPI strip history -- a daily snapshot of the 6 headline numbers
on the Inventory page (out-of-stock/low-stock product counts, dead-stock and
overstock value, stuck stock-count sessions, and last month's shortage qty),
so the KPI strip can eventually show a real "vs previous scan" delta instead
of a bare number.

Same reasoning as customer_predictions.py's customer_lifecycle_snapshots:
nothing here is a prediction or a threshold-based alert, just a plain daily
measurement, recorded starting now so a real comparison exists once a few
days have accumulated. Keyed by the day the daily scan actually ran (not any
one source's own as_of_date, since stock_alerts/depth, stuck-stock-counts,
and loss_and_damage each anchor to a different notion of "as of") -- this is
"what did the KPI strip read on day X," a point of observation, not a data
period. One row per day; a second scan on the same day is a no-op, same
pattern as the customer snapshot table.
"""

from datetime import date, datetime

from backend.app.db import appdb

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS inventory_kpi_snapshots (
            snapshot_date TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            out_of_stock_products INTEGER NOT NULL,
            low_stock_products INTEGER NOT NULL,
            dead_stock_value REAL NOT NULL,
            overstock_value REAL NOT NULL,
            stuck_count_sessions INTEGER NOT NULL,
            stuck_count_value_at_risk REAL NOT NULL,
            shortage_qty REAL,
            shortage_period TEXT
        )
    """)
    _SCHEMA_READY = True


def record_inventory_kpi_snapshot(*, stock_alerts: dict | None, depth: dict | None,
                                   stuck_counts: dict | None, loss_and_damage: dict | None) -> None:
    """Takes the SAME payload shapes the /inventory/* routes already return
    (not a second set of queries) -- the KPI strip has no numbers of its own,
    it only ever displays what those four endpoints already computed."""
    _ensure_schema()
    today = date.today().isoformat()
    existing = appdb.run_query(
        "SELECT 1 AS n FROM inventory_kpi_snapshots WHERE snapshot_date = :d", {"d": today}
    )
    if existing:
        return

    segments = {s["id"]: s for s in (depth or {}).get("segments", [])}
    shortage_qty = None
    if loss_and_damage and loss_and_damage.get("shortage_by_reason"):
        shortage_qty = sum(r["qty"] for r in loss_and_damage["shortage_by_reason"])

    appdb.run_query(
        """
            INSERT INTO inventory_kpi_snapshots
                (snapshot_date, generated_at, out_of_stock_products, low_stock_products,
                 dead_stock_value, overstock_value, stuck_count_sessions, stuck_count_value_at_risk,
                 shortage_qty, shortage_period)
            VALUES
                (:snapshot_date, :generated_at, :out_of_stock_products, :low_stock_products,
                 :dead_stock_value, :overstock_value, :stuck_count_sessions, :stuck_count_value_at_risk,
                 :shortage_qty, :shortage_period)
        """,
        {
            "snapshot_date": today,
            "generated_at": datetime.utcnow().isoformat(),
            "out_of_stock_products": (stock_alerts or {}).get("distinct_out_of_stock_products", 0),
            "low_stock_products": (stock_alerts or {}).get("distinct_low_stock_products", 0),
            "dead_stock_value": (segments.get("dead_stock") or {}).get("value", 0),
            "overstock_value": (segments.get("overstock") or {}).get("value", 0),
            "stuck_count_sessions": len((stuck_counts or {}).get("sessions", [])),
            "stuck_count_value_at_risk": (stuck_counts or {}).get("total_value_at_risk_rs", 0),
            "shortage_qty": shortage_qty,
            "shortage_period": (loss_and_damage or {}).get("period"),
        },
    )


def get_inventory_kpi_deltas() -> dict | None:
    """The latest snapshot plus its delta against the one before it -- None
    for any field where there isn't yet a prior snapshot to compare against
    (the first day this ever ran), rather than a fabricated 0."""
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT * FROM inventory_kpi_snapshots ORDER BY snapshot_date DESC LIMIT 2"
    )
    if not rows:
        return None

    current = rows[0]
    prior = rows[1] if len(rows) > 1 else None

    def _delta(key):
        if prior is None or current[key] is None or prior[key] is None:
            return None
        return round(current[key] - prior[key], 2)

    return {
        "snapshot_date": current["snapshot_date"],
        "prior_snapshot_date": prior["snapshot_date"] if prior else None,
        "out_of_stock_products": {"value": current["out_of_stock_products"], "delta": _delta("out_of_stock_products")},
        "low_stock_products": {"value": current["low_stock_products"], "delta": _delta("low_stock_products")},
        "dead_stock_value": {"value": current["dead_stock_value"], "delta": _delta("dead_stock_value")},
        "overstock_value": {"value": current["overstock_value"], "delta": _delta("overstock_value")},
        "stuck_count_sessions": {"value": current["stuck_count_sessions"], "delta": _delta("stuck_count_sessions")},
        "stuck_count_value_at_risk": {"value": current["stuck_count_value_at_risk"], "delta": _delta("stuck_count_value_at_risk")},
        "shortage_qty": {"value": current["shortage_qty"], "delta": _delta("shortage_qty"), "period": current["shortage_period"]},
    }
