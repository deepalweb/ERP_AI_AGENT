from datetime import datetime

from mcp_server.tools import dispatch_overage


def _row(**overrides):
    base = {
        "move_id": 1, "transfer_out": "WPM/WPM/EK/00029", "dispatched_at": datetime(2026, 9, 27, 10, 0),
        "receipt": "CLSEK/IN/02246", "receipt_done_at": datetime(2026, 9, 27, 13, 40),
        "outlet": "Celeste Daily Ethul Kotte", "store_id": 46,
        "product": "Sticker Lable 76Mm*50Mm", "sku": "008076",
        "plan_qty": 2.0, "sent_qty": 2000.0, "unit_cost": 1.1,
        "planned_by": "manjula@celeste.lk", "dispatched_by": "gayan@celeste.lk",
    }
    return {**base, **overrides}


def _build(monkeypatch, rows):
    monkeypatch.setattr(dispatch_overage.reporting, "run_query", lambda *_a, **_k: rows)
    return dispatch_overage.build_dispatch_overage_findings()


def test_no_flagged_lines_produces_no_case(monkeypatch):
    assert _build(monkeypatch, []) == []


def test_one_line_rolls_up_with_outlet_and_product_breakdowns(monkeypatch):
    [finding] = _build(monkeypatch, [_row()])
    assert finding["metric_id"] == "dispatch_sent_above_plan"
    assert finding["domain"] == "inventory"
    assert finding["confirm"]["current"] == 1
    assert finding["impact"]["units_affected"] == 1998
    assert finding["impact"]["financial_impact_rs"] == round(1998 * 1.1, 2)
    outlet, product = finding["breakdowns"]
    assert outlet["dimension"] == "outlet" and outlet["top_contributors"][0]["name"] == "Celeste Daily Ethul Kotte"
    assert product["dimension"] == "product" and product["top_contributors"][0]["name"] == "Sticker Lable 76Mm*50Mm"


def test_dispatch_rows_carry_plan_sent_over_and_who(monkeypatch):
    [finding] = _build(monkeypatch, [_row()])
    [row] = finding["dispatch_rows"]
    assert row["plan_qty"] == 2.0 and row["sent_qty"] == 2000.0 and row["over_qty"] == 1998
    assert row["ratio"] == 1000.0
    assert row["dispatched_by"] == "gayan@celeste.lk" and row["planned_by"] == "manjula@celeste.lk"


def test_priority_is_medium_just_above_the_floor(monkeypatch):
    # 60 over plan, ratio 6x: clears the detection floor (>=50 units, >=5x) but far
    # under the priority step-ups (10x/2x the floor), and no cost data.
    [finding] = _build(monkeypatch, [_row(plan_qty=10.0, sent_qty=70.0, unit_cost=None)])
    assert finding["priority"] == "medium"


def test_priority_is_high_when_value_crosses_15k_but_the_line_is_still_small(monkeypatch):
    [finding] = _build(monkeypatch, [_row(plan_qty=10.0, sent_qty=70.0, unit_cost=250.0)])  # 60 over x Rs 250 = Rs 15,000
    assert finding["priority"] == "high"


def test_priority_is_critical_for_the_real_sticker_label_event(monkeypatch):
    [finding] = _build(monkeypatch, [_row()])
    assert finding["priority"] == "critical"


def test_checks_flag_record_and_demand_uncertainty(monkeypatch):
    [finding] = _build(monkeypatch, [_row()])
    sources = {c["source"] for c in finding["root_cause"]["checks"]}
    assert {"dispatch_magnitude", "record_verification", "demand_basis"} <= sources
