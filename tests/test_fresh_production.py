"""Fresh-round production: missed rounds, output shortfall, and cycle time.
All three finding builders are pure functions over already-fetched rows
(same pattern as test_stock_reconciliation.py's _cluster_episodes) --
tested directly, no live DB."""

from datetime import datetime

import pytest

from backend.app.db import appdb
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


def _router(average_rows, compliance_rows):
    def run_query(sql):
        if "MAX(scan_time)" in sql:
            return [{"as_of": datetime(2026, 11, 4, 12, 0, 0)}]
        return compliance_rows if "windowtbl" in sql else average_rows
    return run_query


def test_sync_weight_bands_stores_parsed_bands_and_skips_unparseable_names(appdb_reset, monkeypatch):
    monkeypatch.setattr(fp, "_SCHEMA_READY", False)
    monkeypatch.setattr(fp.reporting, "run_query", lambda sql: [
        {"product_tmpl_id": 1, "sku": "004917", "product": "Common Beans 200G - 250G"},
        {"product_tmpl_id": 2, "sku": "002032", "product": "Rambutan (10 Fruits)"},
    ])

    fp.sync_weight_bands()

    bands = fp.get_weight_bands()
    assert bands[1]["min_g"] == 200.0 and bands[1]["max_g"] == 250.0 and bands[1]["mid_g"] == 225.0
    assert 2 not in bands  # no parsable weight -- never an invented band


def test_sync_weight_bands_updates_an_existing_row_on_a_changed_name(appdb_reset, monkeypatch):
    monkeypatch.setattr(fp, "_SCHEMA_READY", False)
    monkeypatch.setattr(fp.reporting, "run_query", lambda sql: [
        {"product_tmpl_id": 1, "sku": "004917", "product": "Common Beans 200G - 250G"},
    ])
    fp.sync_weight_bands()

    monkeypatch.setattr(fp.reporting, "run_query", lambda sql: [
        {"product_tmpl_id": 1, "sku": "004917", "product": "Common Beans 220G - 260G"},
    ])
    fp.sync_weight_bands()

    bands = fp.get_weight_bands()
    assert len(bands) == 1
    assert bands[1]["min_g"] == 220.0 and bands[1]["max_g"] == 260.0


def test_get_average_weight_by_product_rounds_and_shapes_the_result(appdb_reset, monkeypatch):
    monkeypatch.setattr(fp, "_SCHEMA_READY", False)
    fp._ensure_weight_band_schema()
    appdb.run_query(
        "INSERT INTO fresh_product_weight_bands (product_tmpl_id, sku, product, min_g, max_g, mid_g, updated_at) "
        "VALUES (1, '004917', 'Common Beans 200G - 250G', 200.0, 250.0, 225.0, 'x')"
    )
    monkeypatch.setattr(fp.reporting, "run_query", _router(
        average_rows=[{"product_tmpl_id": 1, "product": "Common Beans 200G - 250G", "sku": "004917",
                        "scan_count": 10, "avg_weight_g": 231.456, "min_weight_g": 198.0, "max_weight_g": 261.0}],
        compliance_rows=[{"product_tmpl_id": 1, "below": 1, "above": 1, "correct": 8}],
    ))

    result = fp.get_average_weight_by_product()

    assert result["window_days"] == fp.WINDOW_DAYS
    assert result["products"] == [
        {"product": "Common Beans 200G - 250G", "sku": "004917", "scan_count": 10,
         "avg_weight_g": 231.5, "min_weight_g": 198.0, "max_weight_g": 261.0,
         "band_g": (200.0, 250.0), "band_mid_g": 225.0, "correct_pct": 80.0, "below_pct": 10.0, "above_pct": 10.0},
    ]


def test_get_average_weight_by_product_centers_the_window_on_actual_average_not_target(appdb_reset, monkeypatch):
    # Band 200-250g -> half-width 25g; CORRECT_WINDOW_FRACTION_OF_BAND=0.75 ->
    # window half-width 18.75g. Target (band midpoint) is 225g, but the
    # ACTUAL average is 231.5g -- the window must be centered on 231.5, not
    # 225, giving [212.8, 250.2] (float-rounding artifact, see the other
    # test's comment), not [206.2, 243.8] (Target-centered, the old/rejected
    # behavior).
    fp._ensure_weight_band_schema()
    appdb.run_query(
        "INSERT INTO fresh_product_weight_bands (product_tmpl_id, sku, product, min_g, max_g, mid_g, updated_at) "
        "VALUES (1, '004917', 'Common Beans 200G - 250G', 200.0, 250.0, 225.0, 'x')"
    )
    captured_sql = {}

    def run_query(sql):
        if "MAX(scan_time)" in sql:
            return [{"as_of": datetime(2026, 11, 4, 12, 0, 0)}]
        if "windowtbl" in sql:
            captured_sql["sql"] = sql
            return [{"product_tmpl_id": 1, "below": 0, "above": 0, "correct": 1}]
        return [{"product_tmpl_id": 1, "product": "Common Beans 200G - 250G", "sku": "004917",
                  "scan_count": 1, "avg_weight_g": 231.5, "min_weight_g": 225.0, "max_weight_g": 240.0}]

    monkeypatch.setattr(fp.reporting, "run_query", run_query)

    fp.get_average_weight_by_product()

    assert "(1, 212.8, 250.2)" in captured_sql["sql"]
    assert "(1, 206.2" not in captured_sql["sql"]  # not centered on Target


def test_correct_window_is_always_tighter_than_the_products_own_band():
    # The whole point of scaling by a fraction of each product's own band,
    # rather than a flat percentage of target -- verify the property holds
    # for a product whose band is unusually narrow relative to its target
    # (Carrot: 250-300g, target 275g, band half-width only +/-9.1%).
    min_g, max_g, mid_g = 250.0, 300.0, 275.0
    half_width = (max_g - min_g) / 2 * fp.CORRECT_WINDOW_FRACTION_OF_BAND
    lo, hi = mid_g - half_width, mid_g + half_width
    assert min_g < lo < mid_g < hi < max_g  # strictly inside the original band on both sides


def test_get_average_weight_by_product_handles_no_scans(appdb_reset, monkeypatch):
    monkeypatch.setattr(fp, "_SCHEMA_READY", False)
    monkeypatch.setattr(fp.reporting, "run_query", _router(average_rows=[], compliance_rows=[]))

    result = fp.get_average_weight_by_product()

    assert result["products"] == []


def test_get_average_weight_by_product_leaves_products_without_a_stored_band_alone(appdb_reset, monkeypatch):
    # No row in fresh_product_weight_bands for this product (e.g. its name never
    # parsed) -- never a compliance query, never an invented band.
    monkeypatch.setattr(fp, "_SCHEMA_READY", False)
    calls = []

    def run_query(sql):
        calls.append(sql)
        if "MAX(scan_time)" in sql:
            return [{"as_of": datetime(2026, 11, 4, 12, 0, 0)}]
        return [{"product_tmpl_id": 1, "product": "Rambutan (10 Fruits)", "sku": "002032",
                  "scan_count": 5, "avg_weight_g": 1200.0, "min_weight_g": 1100.0, "max_weight_g": 1300.0}]

    monkeypatch.setattr(fp.reporting, "run_query", run_query)

    result = fp.get_average_weight_by_product()

    assert result["products"][0]["band_g"] is None
    assert result["products"][0]["band_mid_g"] is None
    assert result["products"][0]["correct_pct"] is None
    # window-resolution + the average query -- no products had a band, so no compliance query ran
    assert len(calls) == 2


def test_parse_weight_band_g_handles_the_real_name_format_variations():
    assert fp._parse_weight_band_g("Common Beans 200G - 250G - Celeste Daily - SKU 004917") == (200.0, 250.0)
    assert fp._parse_weight_band_g("Jama Narang (Local Mandarin) 250 - 300G - SKU 000066") == (250.0, 300.0)
    assert fp._parse_weight_band_g("Tjc Mango 500G-700G - Nelna - SKU 002915") == (500.0, 700.0)
    assert fp._parse_weight_band_g("Ambun Banana 1 - 1.2Kg - Celeste Daily - SKU 002096") == (1000.0, 1200.0)
    assert fp._parse_weight_band_g("Tanin Papaya 600G - 1.1Kg - Celeste Daily - SKU 006502") == (600.0, 1100.0)
    assert fp._parse_weight_band_g("Green Grapes 250G - Celeste Daily - Sku 001507") is None  # single value, no range
    assert fp._parse_weight_band_g("Rambutan (10 Fruits) - Celeste Daily - SKU 002032") is None
    assert fp._parse_weight_band_g("") is None


def test_resolve_weight_window_for_an_explicit_month_uses_real_calendar_boundaries(monkeypatch):
    def fail(*_a, **_kw):
        raise AssertionError("an explicit month must not query the DB for the rolling window's anchor")
    monkeypatch.setattr(fp.reporting, "run_query", fail)

    start, end_exclusive, window_days = fp._resolve_weight_window("2026-09")

    assert (start, end_exclusive, window_days) == ("2026-09-01", "2026-10-01", None)


def test_resolve_weight_window_handles_december_rolling_into_next_year():
    start, end_exclusive, window_days = fp._resolve_weight_window("2026-12")
    assert (start, end_exclusive, window_days) == ("2026-12-01", "2027-01-01", None)


def test_resolve_weight_window_defaults_to_rolling_window_anchored_on_latest_scan(monkeypatch):
    monkeypatch.setattr(fp.reporting, "run_query", lambda sql: [{"as_of": datetime(2026, 10, 31, 17, 34, 52)}])

    start, end_exclusive, window_days = fp._resolve_weight_window(None)

    assert window_days == fp.WINDOW_DAYS
    assert start == "2026-10-01"
    assert end_exclusive == "2026-11-01"  # the day AFTER the latest scan's date, so that day is included


def test_get_average_weight_by_product_reports_the_month_and_period_requested(appdb_reset, monkeypatch):
    fp._ensure_weight_band_schema()
    monkeypatch.setattr(fp.reporting, "run_query", lambda sql: [])

    result = fp.get_average_weight_by_product(month="2026-09")

    assert result["month"] == "2026-09"
    assert result["period_start"] == "2026-09-01"
    assert result["period_end_exclusive"] == "2026-10-01"
    assert result["window_days"] is None
    assert result["products"] == []
