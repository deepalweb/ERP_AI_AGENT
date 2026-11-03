from datetime import date

from mcp_server.tools import mwh_inventory_mismatch as m


def _row(**overrides):
    base = {
        "product_id": 10, "product_name": "Yoghurt 90G", "sku": "001740",
        "qty_30d": 100.0, "orders_30d": 20, "mwh_qty": 0.0, "outlet_qty": 0.0,
        "unit_cost": 60.0, "as_of": date(2026, 11, 1),
    }
    return {**base, **overrides}


def _build(monkeypatch, mismatch_rows, outlet_rows=()):
    def fake_run_query(query, params=None):
        if "GROUP BY rs.name" in query:
            return list(outlet_rows)
        return list(mismatch_rows)
    monkeypatch.setattr(m.reporting, "run_query", fake_run_query)
    return m.build_mwh_inventory_mismatch_findings()


def test_no_flagged_products_produces_no_case(monkeypatch):
    assert _build(monkeypatch, []) == []


def test_flagged_products_roll_up_into_one_case_with_breakdowns(monkeypatch):
    rows = [
        _row(product_id=10, product_name="Yoghurt 90G", qty_30d=100.0, unit_cost=60.0),
        _row(product_id=11, product_name="Cheese Wedges", sku="002207", qty_30d=50.0, unit_cost=400.0),
    ]
    outlet_rows = [{"outlet": "Celeste Daily Colombo 6", "qty": 90.0, "orders": 15},
                   {"outlet": "Celeste Daily Pannipitiya", "qty": 60.0, "orders": 10}]
    [finding] = _build(monkeypatch, rows, outlet_rows)
    assert finding["metric_id"] == "mwh_inventory_mismatch"
    assert finding["confirm"]["current"] == 2
    assert finding["impact"]["units_affected"] == 150.0
    assert finding["impact"]["financial_impact_rs"] == 100.0 * 60.0 + 50.0 * 400.0
    outlet, product = finding["breakdowns"]
    assert outlet["dimension"] == "outlet" and len(outlet["top_contributors"]) == 2
    assert product["top_contributors"][0]["name"] == "Yoghurt 90G"
    assert len(finding["mismatch_rows"]) == 2
