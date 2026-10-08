"""Tests for loss_and_damage.get_damage_writeoff -- confirmed-state damage
write-off valuation (the catalog-cost fallback, the state filter, and the
by-outlet/by-product aggregation). Pure unit tests; reporting.run_query is
monkeypatched. See test_state_findings.py for the finding built on top of
this (_damage_writeoff_finding)."""

from backend.app.services import loss_and_damage


def _line(order_id=1, order_name="DMG/1", outlet="Celeste Daily Colombo 6", product="Carrot", sku="000083",
          qty=10.0, cost=15.0, list_price=20.0, state="scrapped"):
    return {
        "order_id": order_id, "order_name": order_name, "order_date": "2026-10-01", "state": state,
        "outlet": outlet, "product": product, "sku": sku, "qty": qty, "cost": cost, "list_price": list_price,
    }


def _fake_run_query(rows):
    def run_query(sql, params=None):
        return rows
    return run_query


def test_no_rows_returns_an_empty_zeroed_result(monkeypatch):
    monkeypatch.setattr(loss_and_damage.reporting, "run_query", _fake_run_query([]))
    result = loss_and_damage.get_damage_writeoff()
    assert result["line_count"] == 0
    assert result["order_count"] == 0
    assert result["total_value_rs"] == 0
    assert result["by_outlet"] == []
    assert result["top_products"] == []


def test_valuation_uses_catalog_cost_fallback_and_tracks_coverage(monkeypatch):
    rows = [
        _line(order_id=1, qty=10.0, cost=15.0, list_price=20.0),  # usable cost -> 150
        _line(order_id=2, qty=5.0, cost=0.0, list_price=0.0),  # no usable cost
    ]
    monkeypatch.setattr(loss_and_damage.reporting, "run_query", _fake_run_query(rows))
    result = loss_and_damage.get_damage_writeoff()
    assert result["total_value_rs"] == 150.0
    assert result["total_qty"] == 15.0
    assert result["valuation_coverage"] == {"total_lines": 2, "catalog_estimate_lines": 1, "no_cost_data_lines": 1}


def test_aggregates_by_outlet_and_by_product_sorted_by_value(monkeypatch):
    rows = [
        _line(order_id=1, outlet="Celeste Daily Colombo 6", product="Carrot", sku="A", qty=10.0, cost=10.0, list_price=20.0),
        _line(order_id=2, outlet="Celeste Daily Colombo 6", product="Carrot", sku="A", qty=5.0, cost=10.0, list_price=20.0),
        _line(order_id=3, outlet="Celeste Daily - Attidiya", product="Tomato", sku="B", qty=1.0, cost=5.0, list_price=10.0),
    ]
    monkeypatch.setattr(loss_and_damage.reporting, "run_query", _fake_run_query(rows))
    result = loss_and_damage.get_damage_writeoff()

    assert result["order_count"] == 3
    assert result["line_count"] == 3
    # Colombo 6: 2 orders, (10+5)*10 = 150; Attidiya: 1 order, 1*5 = 5
    assert result["by_outlet"][0] == {"outlet": "Celeste Daily Colombo 6", "orders": 2, "qty": 15.0, "value_rs": 150.0}
    assert result["by_outlet"][1] == {"outlet": "Celeste Daily - Attidiya", "orders": 1, "qty": 1.0, "value_rs": 5.0}
    assert result["top_products"][0] == {"name": "Carrot", "sku": "A", "lines": 2, "qty": 15.0, "value_rs": 150.0}


def test_window_days_controls_the_query_range(monkeypatch):
    captured = {}

    def run_query(sql, params=None):
        captured.update(params or {})
        return []

    monkeypatch.setattr(loss_and_damage.reporting, "run_query", run_query)
    loss_and_damage.get_damage_writeoff(window_days=7)
    from datetime import date, timedelta
    expected_start = (date.today() - timedelta(days=6)).isoformat()
    assert captured["start"] == expected_start
    assert captured["end"] == date.today().isoformat()
