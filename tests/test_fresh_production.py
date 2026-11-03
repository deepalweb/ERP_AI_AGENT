"""Fresh-round production: missed rounds, output shortfall, and cycle time.
All three finding builders are pure functions over already-fetched rows
(same pattern as test_stock_reconciliation.py's _cluster_episodes) --
tested directly, no live DB."""

from datetime import datetime

import pytest

from mcp_server.tools import fresh_production as fp


def _missed_row(id_, qty=10.0, round_="morning", product="Lettuce", unit_cost=None):
    return {"id": id_, "name": f"FWH/MO/{id_}", "origin": "FR/2026/10/0001", "round": round_,
            "date_start": datetime(2026, 10, 20), "qty": qty, "product": product, "sku": f"SKU{id_}",
            "unit_cost": unit_cost, "responsible": "manjula@celeste.lk"}


def _shortfall_row(id_, planned, produced, round_="morning", product="Lettuce", unit_cost=None):
    return {"id": id_, "name": f"FWH/MO/{id_}", "origin": "FR/2026/10/0001", "round": round_,
            "date_start": datetime(2026, 10, 20), "date_finished": datetime(2026, 10, 20, 18, 0),
            "planned_qty": planned, "produced_qty": produced, "product": product, "sku": f"SKU{id_}",
            "unit_cost": unit_cost, "responsible": "manjula@celeste.lk", "completed_by": "madusanka@celeste.lk"}


def _cycle_time_row(id_, cycle_hours, cutoff_hours, round_="morning", product="Lettuce"):
    return {"id": id_, "name": f"FWH/MO/{id_}", "origin": "FR/2026/10/0001", "round": round_,
            "date_start": datetime(2026, 10, 20), "date_finished": datetime(2026, 10, 20, 18, 0),
            "cycle_hours": cycle_hours, "cutoff_hours": cutoff_hours, "qty": 10.0,
            "product": product, "sku": f"SKU{id_}",
            "responsible": "manjula@celeste.lk", "completed_by": "madusanka@celeste.lk"}


def test_missed_rounds_finding_is_none_when_nothing_missed():
    assert fp._missed_rounds_finding([]) is None


def test_missed_rounds_finding_reports_count_and_units():
    rows = [_missed_row(1, qty=10), _missed_row(2, qty=5)]

    finding = fp._missed_rounds_finding(rows)

    assert finding["metric_id"] == "fresh_production_missed"
    assert finding["impact"]["count"] == 2
    assert finding["impact"]["units_affected"] == 15


def test_missed_rounds_finding_prices_only_what_has_catalog_cost():
    rows = [_missed_row(1, qty=10, unit_cost=5.0), _missed_row(2, qty=5, unit_cost=None)]

    finding = fp._missed_rounds_finding(rows)

    assert finding["impact"]["financial_impact_rs"] == 50.0
    assert finding["impact"]["valued_units_pct"] == pytest.approx(10 / 15 * 100, abs=0.1)


def test_missed_rounds_finding_escalates_priority_with_volume():
    small = fp._missed_rounds_finding([_missed_row(1, qty=1)])
    huge = fp._missed_rounds_finding([_missed_row(i, qty=1000) for i in range(2000)])

    assert small["priority"] == "medium"
    assert huge["priority"] == "critical"


def test_shortfall_finding_is_none_when_nothing_flagged():
    assert fp._shortfall_finding([]) is None


def test_shortfall_finding_separates_under_from_over_production():
    rows = [_shortfall_row(1, planned=100, produced=60), _shortfall_row(2, planned=50, produced=80)]

    finding = fp._shortfall_finding(rows)

    assert finding["impact"]["count"] == 1  # only the under-produced row counts toward shortfall impact
    assert finding["impact"]["units_affected"] == 40


def test_shortfall_finding_computes_deviation_percentage():
    rows = [_shortfall_row(1, planned=100, produced=70)]

    finding = fp._shortfall_finding(rows)

    row = finding["fresh_production_shortfall_rows"][0]
    assert row["pct_dev"] == 30.0
    assert row["shortfall_qty"] == 30.0


def test_missed_rounds_carry_document_and_responsible_evidence():
    finding = fp._missed_rounds_finding([_missed_row(1)])

    row = finding["fresh_production_missed_rows"][0]
    assert row["document"] == "FWH/MO/1"
    assert row["batch_reference"] == "FR/2026/10/0001"
    assert row["responsible"] == "manjula@celeste.lk"
    assert row["scheduled_at"] == "2026-10-20 00:00"


def test_shortfall_rows_carry_document_and_completion_evidence():
    finding = fp._shortfall_finding([_shortfall_row(1, planned=100, produced=70)])

    row = finding["fresh_production_shortfall_rows"][0]
    assert row["document"] == "FWH/MO/1"
    assert row["responsible"] == "manjula@celeste.lk"
    assert row["completed_by"] == "madusanka@celeste.lk"
    assert row["completed_at"] == "2026-10-20 18:00"


def test_cycle_time_finding_is_none_when_nothing_flagged():
    assert fp._cycle_time_finding([]) is None


def test_cycle_time_finding_reports_count_and_distinct_products():
    rows = [
        _cycle_time_row(1, cycle_hours=20.0, cutoff_hours=13.6, round_="evening", product="Lettuce"),
        _cycle_time_row(2, cycle_hours=90.0, cutoff_hours=84.3, round_="morning", product="Carrot"),
    ]

    finding = fp._cycle_time_finding(rows)

    assert finding["metric_id"] == "fresh_production_cycle_time"
    assert finding["impact"]["count"] == 2
    assert finding["impact"]["distinct_products"] == 2
    assert finding["impact"]["financial_impact_rs"] is None  # time-based, no rupee value


def test_cycle_time_finding_carries_each_rounds_own_cutoff():
    rows = [_cycle_time_row(1, cycle_hours=20.0, cutoff_hours=13.6, round_="evening")]

    finding = fp._cycle_time_finding(rows)

    row = finding["fresh_production_cycle_time_rows"][0]
    assert row["cycle_hours"] == 20.0
    assert row["round_cutoff_hours"] == 13.6
    assert row["document"] == "FWH/MO/1"
