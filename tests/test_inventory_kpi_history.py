"""Inventory KPI strip history -- record_inventory_kpi_snapshot() and
get_inventory_kpi_deltas(). Pure unit tests against an isolated appdb
(appdb_reset), no reporting DB involved -- the function takes already-fetched
payload dicts, same shape the /inventory/* routes return."""

from datetime import date, timedelta

from backend.app.db import appdb
from backend.app.services import inventory_kpi_history as kh

STOCK_ALERTS = {"distinct_out_of_stock_products": 1113, "distinct_low_stock_products": 690}
DEPTH = {"segments": [
    {"id": "dead_stock", "value": 882433.28},
    {"id": "overstock", "value": 12876792.26},
    {"id": "healthy", "value": 69183708.8},
]}
STUCK_COUNTS = {"sessions": [{"id": 1}, {"id": 2}], "total_value_at_risk_rs": 303113.0}
LOSS_AND_DAMAGE = {"period": "2026-09", "shortage_by_reason": [
    {"reason": "Missing Item", "lines": 902, "qty": 9669.0},
    {"reason": "Extra Quantity", "lines": 26, "qty": 232.0},
]}


def test_record_inventory_kpi_snapshot_writes_one_row(appdb_reset, monkeypatch):
    monkeypatch.setattr(kh, "_SCHEMA_READY", False)
    kh.record_inventory_kpi_snapshot(
        stock_alerts=STOCK_ALERTS, depth=DEPTH, stuck_counts=STUCK_COUNTS, loss_and_damage=LOSS_AND_DAMAGE
    )
    rows = appdb.run_query("SELECT * FROM inventory_kpi_snapshots")
    assert len(rows) == 1
    row = rows[0]
    assert row["out_of_stock_products"] == 1113
    assert row["low_stock_products"] == 690
    assert row["dead_stock_value"] == 882433.28
    assert row["overstock_value"] == 12876792.26
    assert row["stuck_count_sessions"] == 2
    assert row["stuck_count_value_at_risk"] == 303113.0
    assert row["shortage_qty"] == 9901.0
    assert row["shortage_period"] == "2026-09"


def test_record_inventory_kpi_snapshot_is_a_no_op_for_the_same_day(appdb_reset, monkeypatch):
    monkeypatch.setattr(kh, "_SCHEMA_READY", False)
    kh.record_inventory_kpi_snapshot(
        stock_alerts=STOCK_ALERTS, depth=DEPTH, stuck_counts=STUCK_COUNTS, loss_and_damage=LOSS_AND_DAMAGE
    )
    # A second scan later the same day must not duplicate the row.
    kh.record_inventory_kpi_snapshot(
        stock_alerts={"distinct_out_of_stock_products": 9999, "distinct_low_stock_products": 1},
        depth=DEPTH, stuck_counts=STUCK_COUNTS, loss_and_damage=LOSS_AND_DAMAGE,
    )
    rows = appdb.run_query("SELECT * FROM inventory_kpi_snapshots")
    assert len(rows) == 1
    assert rows[0]["out_of_stock_products"] == 1113  # the first scan's value, unchanged


def test_get_inventory_kpi_deltas_returns_none_with_no_history(appdb_reset, monkeypatch):
    monkeypatch.setattr(kh, "_SCHEMA_READY", False)
    assert kh.get_inventory_kpi_deltas() is None


def test_get_inventory_kpi_deltas_has_no_delta_on_the_first_snapshot(appdb_reset, monkeypatch):
    monkeypatch.setattr(kh, "_SCHEMA_READY", False)
    kh.record_inventory_kpi_snapshot(
        stock_alerts=STOCK_ALERTS, depth=DEPTH, stuck_counts=STUCK_COUNTS, loss_and_damage=LOSS_AND_DAMAGE
    )
    result = kh.get_inventory_kpi_deltas()
    assert result["out_of_stock_products"]["value"] == 1113
    assert result["out_of_stock_products"]["delta"] is None
    assert result["prior_snapshot_date"] is None


def test_get_inventory_kpi_deltas_compares_against_the_prior_snapshot(appdb_reset, monkeypatch):
    monkeypatch.setattr(kh, "_SCHEMA_READY", False)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    kh._ensure_schema()
    appdb.run_query(
        """INSERT INTO inventory_kpi_snapshots
               (snapshot_date, generated_at, out_of_stock_products, low_stock_products,
                dead_stock_value, overstock_value, stuck_count_sessions, stuck_count_value_at_risk,
                shortage_qty, shortage_period)
           VALUES (:d, :d, 1027, 650, 900000.0, 12000000.0, 3, 320000.0, 9500.0, '2026-09')""",
        {"d": yesterday},
    )
    kh.record_inventory_kpi_snapshot(
        stock_alerts=STOCK_ALERTS, depth=DEPTH, stuck_counts=STUCK_COUNTS, loss_and_damage=LOSS_AND_DAMAGE
    )
    result = kh.get_inventory_kpi_deltas()
    assert result["out_of_stock_products"]["value"] == 1113
    assert result["out_of_stock_products"]["delta"] == 86  # 1113 - 1027
    assert result["stuck_count_sessions"]["delta"] == -1  # 2 - 3
    assert result["prior_snapshot_date"] == yesterday
