"""Execute production SQL against synthetic CTEs in a read-only Postgres transaction.

No customer data is read and no fixtures are written to the reporting database.
"""

from datetime import date, datetime

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

from backend.app.db import appdb, reporting
from backend.app.services import customer_predictions as cp

pytestmark = pytest.mark.real_backend

FIXTURES = """
sale_order(id, partner_id, store_id, state, date_order, amount_total,
           pickme_order_id, uber_order_id) AS (VALUES
 (1, 10, 1, 'sale', TIMESTAMP '2026-01-01 20:00:00', 100, NULL::int, NULL::int),
 (2, 10, 1, 'sale', TIMESTAMP '2026-01-04 20:00:00', 100, NULL::int, NULL::int),
 (3, 10, 1, 'sale', TIMESTAMP '2026-01-07 20:00:00', 100, NULL::int, NULL::int),
 (4, 10, 99, 'sale', TIMESTAMP '2026-02-01 20:00:00', 9000, NULL::int, NULL::int),
 (5, 20, 99, 'sale', TIMESTAMP '2026-03-01 20:00:00', 9000, NULL::int, NULL::int),
 (6, 30, 1, 'cancel', TIMESTAMP '2026-04-01 20:00:00', 9000, NULL::int, NULL::int)),
res_partner(id, name) AS (VALUES (10, 'Fixture'), (20, 'Partner'))
"""


@pytest.fixture
def query():
    load_dotenv()
    with reporting.get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '15s'"))

        def run(sql, params=None):
            sql = sql.strip()
            tail = ", " + sql[5:] if sql.upper().startswith("WITH ") else " " + sql
            return list(conn.execute(text("WITH " + FIXTURES + tail), params or {}).mappings())

        yield run


def test_scope_excludes_partner_customers_and_late_partner_orders(query):
    assert query(cp.TOTAL_CUSTOMERS_SQL)[0]["n"] == 1
    assert query(cp.AS_OF_DATE_SQL)[0]["as_of_date"] == datetime(2026, 1, 8, 1, 30)  # noqa: DTZ001 -- SQL local timestamp
    rows = query(
        cp.PREDICTIONS_SQL, {"min_order_count": 3, "min_avg_gap_days": 1, "recent_window_gaps": 5}
    )
    assert len(rows) == 1
    assert rows[0]["order_count"] == 3
    assert rows[0]["revenue"] == 300
    assert rows[0]["avg_gap_recent"] == 3


def test_daily_actuals_use_colombo_day_and_same_scope(query):
    rows = query(cp.ACTUAL_DAILY_ORDERS_SQL, {"start_date": "2026-01-08", "end_date": "2026-01-09"})
    assert [(r["day"], r["orders"]) for r in rows] == [(date(2026, 1, 8), 1)]


def test_lifecycle_and_pattern_stats_use_the_same_scoped_history(query):
    params = {"min_order_count": 3, "high_threshold": 10, "recent_window_gaps": 5}
    assert dict(query(cp.PATTERN_DISTRIBUTION_SQL, params)[0]) == {
        "bucket": "three_to_nine",
        "n": 1,
    }
    lifecycle = query(cp.LIFECYCLE_SQL, {**params, "partner_id": None})
    assert len(lifecycle) == 1
    assert lifecycle[0]["order_count"] == 3
    assert lifecycle[0]["last_order"].date() == date(2026, 1, 8)
    assert query(cp.VALUE_PERCENTILE_SQL)[0]["p80"] == 300

    # Customer Lookup, Phase A: filtering to one partner_id must not change
    # as_of_date (still computed from the whole scoped population, not just
    # the filtered customer) -- a single-customer lookup and the company-wide
    # Customers page must report the same "data through" date.
    one = query(cp.LIFECYCLE_SQL, {**params, "partner_id": 10})
    assert len(one) == 1
    assert one[0]["order_count"] == 3
    assert one[0]["as_of_date"] == lifecycle[0]["as_of_date"]
    assert query(cp.LIFECYCLE_SQL, {**params, "partner_id": 999999}) == []


def test_live_scoped_snapshot_roundtrip_uses_only_isolated_appdb(appdb_reset, monkeypatch):
    load_dotenv()
    monkeypatch.setattr(cp, "_SCHEMA_READY", False)
    monkeypatch.setattr(cp, "_LIFECYCLE_SCHEMA_READY", False)
    monkeypatch.setattr(cp, "_LIFECYCLE_SNAPSHOT_SCHEMA_READY", False)
    with reporting.get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '90s'"))

        def read(sql, params=None):
            return [dict(r) for r in conn.execute(text(sql), params or {}).mappings()]

        monkeypatch.setattr(reporting, "run_query", read)
        predictions = cp.compute_predictions()
        assert predictions
        cp.record_predictions(predictions)
        cp.record_pattern_stats(cp.compute_pattern_stats(len(predictions)))
        summary = cp.get_summary()
        assert summary["predictable_customers"] == len(predictions)
        assert summary["total_customers"] >= len(predictions)
        cp.record_lifecycle_summary(cp.compute_lifecycle_summary())
        snapshot_rows = cp.compute_lifecycle_snapshot_rows()
        assert snapshot_rows
        cp.record_lifecycle_snapshot(snapshot_rows)
        stored = appdb.run_query("SELECT COUNT(*) AS n FROM customer_lifecycle_snapshots")
        assert stored[0]["n"] == len(snapshot_rows)
        # Re-running on the same as_of_date must not duplicate -- see
        # record_lifecycle_snapshot()'s own docstring for why.
        cp.record_lifecycle_snapshot(snapshot_rows)
        stored_again = appdb.run_query("SELECT COUNT(*) AS n FROM customer_lifecycle_snapshots")
        assert stored_again[0]["n"] == len(snapshot_rows)
        assert cp.get_lifecycle_summary()["as_of_date"] == summary["as_of_date"]
        chart = cp.get_order_forecast_chart()
        assert chart["forecast_scope"] == "repeat_customers_only"
        assert chart["latest_actual_day_is_partial"]
        assert cp.get_outlet_order_forecast()["days"]
        assert cp.get_predicted_next_basket(predictions[0]["partner_id"]) is not None
