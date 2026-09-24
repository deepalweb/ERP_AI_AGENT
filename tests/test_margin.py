"""Metric Truthfulness Correction, Task 6: margin.get_gross_margin() had zero
test coverage before this -- Task 3 only changed labels/notes (never the
numerator/denominator math), and this locks in the exact numeric behavior
so any future accidental change to the calculation itself is caught.
"""
from unittest.mock import patch

from backend.app.services import margin
from mcp_server.tools import intelligence


def test_gross_margin_metric_terminology_corrected_but_id_unit_direction_and_sql_unchanged():
    metric = intelligence.METRICS["gross_margin"]
    # Machine-readable identity: untouched (Task 3 explicitly preserves this).
    assert metric.id == "gross_margin"
    assert metric.unit == "percent"
    assert metric.direction == "higher_is_good"
    assert metric.is_rate is True
    # The actual calculation: untouched -- still the same cost field applied
    # the same way, never a fabricated historical-cost source.
    assert "celeste_cost_price_ex_vat" in metric.trend_sql
    assert "sol.price_subtotal - sol.product_uom_qty * pt.celeste_cost_price_ex_vat" in metric.trend_sql
    # Terminology: corrected -- unmistakably an estimate, current-cost basis.
    assert "Gross margin" != metric.label  # the old, uncorrected label
    assert "estimat" in metric.label.lower() or "estimat" in metric.notes.lower()
    assert "current-cost" in metric.label.lower() or "current-cost" in metric.notes.lower()
    # Coverage and point-in-time accuracy are explicitly distinguished, not conflated.
    assert "coverage" in metric.notes.lower()
    assert "point-in-time" in metric.notes.lower() or "historical" in metric.notes.lower()


def test_margin_amount_and_pct_computed_correctly_from_revenue_and_cogs():
    with patch.object(margin, "get_sales_summary", return_value={
        "period": "2026-08", "revenue": 1000.0, "orders": 10, "average_order_value": 100.0,
        "growth_vs_previous_month_pct": None, "is_partial_period": False, "days_elapsed": 31, "days_in_period": 31,
    }):
        with patch.object(margin.reporting, "run_query", return_value=[{"revenue_with_cost": 800.0, "cogs": 600.0}]):
            result = margin.get_gross_margin("2026-08")

    assert result["cost_of_goods_sold"] == 600.0
    assert result["gross_margin_amount"] == 200.0  # 800 - 600
    assert result["gross_margin_pct"] == 25.0  # 200 / 800 * 100
    assert result["cost_data_coverage_pct"] == 80.0  # 800 / 1000 * 100
    assert result["revenue"] == 1000.0


def test_zero_revenue_with_cost_produces_no_margin_not_a_divide_by_zero():
    with patch.object(margin, "get_sales_summary", return_value={
        "period": "2026-08", "revenue": 0.0, "orders": 0, "average_order_value": 0.0,
        "growth_vs_previous_month_pct": None, "is_partial_period": False, "days_elapsed": 31, "days_in_period": 31,
    }):
        with patch.object(margin.reporting, "run_query", return_value=[{"revenue_with_cost": None, "cogs": None}]):
            result = margin.get_gross_margin("2026-08")

    assert result["gross_margin_amount"] is None
    assert result["gross_margin_pct"] is None
    assert result["cost_data_coverage_pct"] is None
