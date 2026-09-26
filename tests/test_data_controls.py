"""Tests for backend/app/services/data_controls.py -- Business Data
Controls, Wave 1 Step 1 + the Frontend Intelligence Integration outlet
breakdown. Pure unit tests; reporting.run_query is monkeypatched,
dispatching on a distinguishing keyword since each query is its own call.
"""

from backend.app.services import data_controls as dc


def _fake_run_query(negative_stock_rows, cost_coverage_rows, no_cost_products_rows, by_outlet_rows=None):
    def run_query(sql, params=None):
        if "per_store" in sql:
            return by_outlet_rows or []
        if "on_hand" in sql:
            return negative_stock_rows
        if "FILTER" in sql and "no_cost_at_all" in sql:
            return cost_coverage_rows
        if "product_template" in sql:
            return no_cost_products_rows
        raise AssertionError(f"unexpected query: {sql[:60]}")
    return run_query


def test_get_negative_stock_computes_value_and_sorts_descending(monkeypatch):
    rows = [
        {"tmpl_id": 1, "name": "A", "sku": "A1", "qty": -2.0, "cost": 10.0, "list_price": 20.0},  # 20
        {"tmpl_id": 2, "name": "B", "sku": "B1", "qty": -5.0, "cost": 50.0, "list_price": 100.0},  # 250
    ]
    monkeypatch.setattr(dc.reporting, "run_query", _fake_run_query(rows, [], []))
    result = dc.get_negative_stock()
    assert result["count"] == 2
    assert result["total_value_rs"] == 270.0
    assert [p["sku"] for p in result["products"]] == ["B1", "A1"]  # highest value first


def test_get_negative_stock_handles_no_usable_cost(monkeypatch):
    rows = [{"tmpl_id": 1, "name": "A", "sku": "A1", "qty": -2.0, "cost": None, "list_price": None}]
    monkeypatch.setattr(dc.reporting, "run_query", _fake_run_query(rows, [], []))
    result = dc.get_negative_stock()
    assert result["count"] == 1
    assert result["products"][0]["value_rs"] is None
    assert result["total_value_rs"] == 0.0  # excluded, not treated as zero-cost


def test_get_negative_stock_returns_zero_state_for_no_rows(monkeypatch):
    monkeypatch.setattr(dc.reporting, "run_query", _fake_run_query([], [], []))
    result = dc.get_negative_stock()
    assert result["count"] == 0
    assert result["products"] == []
    assert result["total_value_rs"] == 0.0
    assert result["by_outlet"] == []


def test_get_negative_stock_includes_a_per_outlet_breakdown(monkeypatch):
    """The outlet count is a genuinely separate aggregation (per-store
    subtotal, not derived from the company-wide product list) -- verified
    here by feeding it independently of negative_stock_rows."""
    rows = [{"tmpl_id": 1, "name": "A", "sku": "A1", "qty": -2.0, "cost": 10.0, "list_price": 20.0}]
    by_outlet_rows = [{"outlet": "Ethul Kotte", "product_count": 28}, {"outlet": "Outlet B", "product_count": 14}]
    monkeypatch.setattr(dc.reporting, "run_query", _fake_run_query(rows, [], [], by_outlet_rows))
    result = dc.get_negative_stock()
    assert result["by_outlet"] == [
        {"outlet": "Ethul Kotte", "product_count": 28},
        {"outlet": "Outlet B", "product_count": 14},
    ]


def test_get_cost_coverage_computes_percentage_and_fetches_uncosted_products(monkeypatch):
    coverage_rows = [{"total": 100, "has_direct_cost": 90, "catalog_fallback_only": 5, "no_cost_at_all": 5}]
    no_cost_rows = [{"name": "X", "sku": "X1"}, {"name": "Y", "sku": "Y1"}]
    monkeypatch.setattr(dc.reporting, "run_query", _fake_run_query([], coverage_rows, no_cost_rows))
    result = dc.get_cost_coverage()
    assert result["total_products"] == 100
    assert result["no_cost_at_all"] == 5
    assert result["coverage_pct"] == 95.0  # (90+5)/100
    assert len(result["uncosted_products"]) == 2


def test_get_cost_coverage_skips_uncosted_query_when_coverage_is_complete(monkeypatch):
    coverage_rows = [{"total": 100, "has_direct_cost": 100, "catalog_fallback_only": 0, "no_cost_at_all": 0}]
    calls = []

    def run_query(sql, params=None):
        calls.append(sql)
        if "on_hand" in sql:
            return []
        return coverage_rows

    monkeypatch.setattr(dc.reporting, "run_query", run_query)
    result = dc.get_cost_coverage()
    assert result["no_cost_at_all"] == 0
    assert result["coverage_pct"] == 100.0
    assert result["uncosted_products"] == []
    assert len(calls) == 1  # never queried the uncosted-products list when there's nothing to list


def test_get_data_controls_bundles_both_signals(monkeypatch):
    monkeypatch.setattr(dc, "get_negative_stock", lambda: {"count": 1})
    monkeypatch.setattr(dc, "get_cost_coverage", lambda: {"no_cost_at_all": 2})
    result = dc.get_data_controls()
    assert result == {"negative_stock": {"count": 1}, "cost_coverage": {"no_cost_at_all": 2}}
