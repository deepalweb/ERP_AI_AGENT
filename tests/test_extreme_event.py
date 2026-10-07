from datetime import datetime

from mcp_server.tools import extreme_event


def _row(**overrides):
    base = {
        "shortage_id": 1, "product_id": 10, "store_id": 2, "outlet": "Celeste Daily Pannipitiya",
        "picking": "CPNP/IN/00001", "picking_state": "done",
        "source_location": "Transits/X", "dest_location": "CPNP/Stock",
        "scheduled_at": datetime(2026, 9, 27, 7, 0), "done_at": datetime(2026, 9, 27, 9, 0),
        "created_by": "a@celeste.lk", "recorded_by": "mobile",
        "qty": 1000.0, "expected_qty": 1000.0, "received_qty": 0.0,
        "created_at": datetime(2026, 9, 27, 7, 4),
        "product_name": "Araliya Bags", "sku": "004713", "unit_cost": None,
        "n_lines": 2, "median_qty": 525.0,
    }
    return {**base, **overrides}


def _build(monkeypatch, rows):
    monkeypatch.setattr(extreme_event.reporting, "run_query", lambda *_a, **_k: rows)
    return extreme_event.build_extreme_event_findings()


def test_no_flagged_lines_produces_no_case(monkeypatch):
    assert _build(monkeypatch, []) == []


def test_many_lines_roll_up_into_one_case_with_outlet_and_product_breakdowns(monkeypatch):
    rows = [
        _row(shortage_id=1, outlet="Celeste Daily Pannipitiya", qty=1000.0, expected_qty=1000.0),
        _row(shortage_id=2, outlet="Celeste Daily Colombo 6", product_name="Potato", sku="005732",
             qty=918.0, expected_qty=918.0, n_lines=12, median_qty=10.0),
    ]
    [finding] = _build(monkeypatch, rows)
    assert finding["metric_id"] == "extreme_event_missing_item"
    assert finding["confirm"]["current"] == 2
    assert finding["impact"]["units_affected"] == 1918.0
    outlet, product = finding["breakdowns"]
    assert outlet["dimension"] == "outlet" and len(outlet["top_contributors"]) == 2
    assert product["dimension"] == "product" and product["top_contributors"][0]["name"] == "Araliya Bags"
    assert len(finding["extreme_rows"]) == 2


def test_thin_history_row_says_no_usual_baseline(monkeypatch):
    [finding] = _build(monkeypatch, [_row()])
    assert finding["extreme_rows"][0]["vs_usual"] == "No usual baseline"
    assert "pooled distribution" not in str(finding["root_cause"]["checks"][0]["detail"])


def test_quantity_mismatch_is_flagged_on_the_case(monkeypatch):
    [finding] = _build(monkeypatch, [_row(qty=20.0, expected_qty=24.0, received_qty=0.0)])
    sources = [c["source"] for c in finding["root_cause"]["checks"]]
    assert "record_consistency" in sources
    assert "Quantity does not match expected minus received" in finding["extreme_rows"][0]["flags"]


def test_clean_done_receipt_adds_no_flag_checks(monkeypatch):
    [finding] = _build(monkeypatch, [_row()])
    sources = [c["source"] for c in finding["root_cause"]["checks"]]
    assert "receipt_status" not in sources
    assert "record_consistency" not in sources
    assert finding["extreme_rows"][0]["recorded_by"] == "mobile"


def test_priority_is_medium_for_a_small_single_event(monkeypatch):
    rows = [_row(qty=25.0, expected_qty=25.0, unit_cost=None)]
    [finding] = _build(monkeypatch, rows)
    assert finding["priority"] == "medium"


def test_priority_is_high_when_the_batch_value_crosses_15k_but_the_line_is_still_small(monkeypatch):
    rows = [_row(qty=25.0, expected_qty=25.0, unit_cost=700.0)]  # Rs 17,500, well under the 50k cut
    [finding] = _build(monkeypatch, rows)
    assert finding["priority"] == "high"


def test_priority_is_critical_for_a_1000_unit_single_line(monkeypatch):
    rows = [_row(qty=1000.0, expected_qty=1000.0)]
    [finding] = _build(monkeypatch, rows)
    assert finding["priority"] == "critical"
