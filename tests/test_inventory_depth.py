"""Inventory efficiency review, 2026-10-16: the 6-way classification
(potential_dead_stock/slow_moving/overstock/healthy/uncertain/never_sold)
and aging bands get_inventory_depth() now computes. No prior tests existed
for this module at all -- _classify()/_aging_band() are pure functions,
tested directly; get_inventory_depth() is tested end-to-end with
reporting.fetch_report monkeypatched (same pattern test_performance.py
already uses for an async reporting-backed function)."""

from datetime import datetime

from backend.app.services import inventory

AS_OF = datetime(2026, 10, 16)


# ---------------------------------------------------------------------------
# _classify -- pure function, one check per branch, in priority order.

def test_classify_never_sold_when_no_sale_in_history():
    assert inventory._classify(0, 0, False, None, 10.0) == "never_sold"


def test_classify_potential_dead_stock_when_no_sales_in_90d_and_priceable():
    assert inventory._classify(0, 0, True, None, 10.0) == "potential_dead_stock"


def test_classify_uncertain_when_no_sales_in_90d_and_unpriceable():
    # Sold before, nothing in 90d, but no usable cost -- can't confidently
    # call this dead stock without a value to back it.
    assert inventory._classify(0, 0, True, None, None) == "uncertain"


def test_classify_uncertain_when_90d_volume_too_low_to_trust():
    assert inventory._classify(0, inventory.MIN_RELIABLE_UNITS_90D - 1, True, None, 10.0) == "uncertain"


def test_classify_slow_moving_when_reliable_90d_sales_but_none_in_30d():
    assert inventory._classify(0, inventory.MIN_RELIABLE_UNITS_90D, True, None, 10.0) == "slow_moving"


def test_classify_overstock_when_cover_exceeds_threshold():
    days_of_stock = inventory.OVERSTOCK_DAYS + 1
    assert inventory._classify(10, 30, True, days_of_stock, 10.0) == "overstock"


def test_classify_healthy_otherwise():
    assert inventory._classify(10, 30, True, 15, 10.0) == "healthy"


# ---------------------------------------------------------------------------
# _aging_band

def test_aging_band_never_sold_for_no_days_since_last_sale():
    assert inventory._aging_band(None) == "never_sold"


def test_aging_band_boundaries():
    assert inventory._aging_band(0) == "0_30"
    assert inventory._aging_band(30) == "0_30"
    assert inventory._aging_band(31) == "31_60"
    assert inventory._aging_band(90) == "61_90"
    assert inventory._aging_band(91) == "91_180"
    assert inventory._aging_band(180) == "91_180"
    assert inventory._aging_band(181) == "180_plus"
    assert inventory._aging_band(400) == "180_plus"


# ---------------------------------------------------------------------------
# get_inventory_depth -- end to end, reporting.fetch_report monkeypatched
# (same pattern test_performance.py uses for an async reporting function).

def _row(product_id, name, total_qty, sold_30d, sold_90d, last_sale_date, cost=10.0, list_price=20.0):
    return {
        "product_id": product_id, "product_name": name, "total_qty": total_qty,
        "sold_30d": sold_30d, "sold_90d": sold_90d, "last_sale_date": last_sale_date,
        "cost": cost, "list_price": list_price, "as_of": AS_OF,
    }


async def test_get_inventory_depth_classifies_every_row_into_one_segment(monkeypatch):
    inventory.get_inventory_depth.cache_clear()
    rows = [
        _row(1, "Never sold item", 50, 0, 0, None),
        _row(2, "Dead item", 50, 0, 0, datetime(2026, 7, 1)),  # 90d+ since last sale
        _row(3, "Low-volume item", 50, 0, 2, datetime(2026, 10, 1)),  # below MIN_RELIABLE_UNITS_90D
        _row(4, "Slow mover", 50, 0, 20, datetime(2026, 9, 1)),  # sold in 90d, not 30d
        _row(5, "Overstocked item", 9000, 10, 300, AS_OF),  # huge qty, modest 30d sales -> high cover
        _row(6, "Healthy item", 100, 50, 150, AS_OF),
    ]

    async def fake_fetch_report(sql, params=None):
        return rows

    monkeypatch.setattr(inventory.reporting, "fetch_report", fake_fetch_report)
    result = await inventory.get_inventory_depth()

    by_id = {s["id"]: s for s in result["segments"]}
    assert by_id["never_sold"]["count"] == 1
    assert by_id["potential_dead_stock"]["count"] == 1
    assert by_id["uncertain"]["count"] == 1
    assert by_id["slow_moving"]["count"] == 1
    assert by_id["overstock"]["count"] == 1
    assert by_id["healthy"]["count"] == 1
    # Every row landed in exactly one segment -- no row double-counted or dropped.
    assert sum(s["count"] for s in result["segments"]) == len(rows)

    assert [i["name"] for i in result["potential_dead_stock_items"]] == ["Dead item"]
    assert [i["name"] for i in result["slow_moving_items"]] == ["Slow mover"]
    assert [i["name"] for i in result["overstock_items"]] == ["Overstocked item"]


async def test_get_inventory_depth_aging_bands_are_independent_of_segment(monkeypatch):
    inventory.get_inventory_depth.cache_clear()
    rows = [
        _row(1, "Sold today", 10, 5, 5, AS_OF),
        _row(2, "Sold 45 days ago", 10, 0, 20, datetime(2026, 9, 1)),  # 45 days before AS_OF
        _row(3, "Never sold", 10, 0, 0, None),
    ]

    async def fake_fetch_report(sql, params=None):
        return rows

    monkeypatch.setattr(inventory.reporting, "fetch_report", fake_fetch_report)
    result = await inventory.get_inventory_depth()

    by_id = {b["id"]: b for b in result["aging_bands"]}
    assert by_id["0_30"]["count"] == 1
    assert by_id["31_60"]["count"] == 1
    assert by_id["never_sold"]["count"] == 1
    assert sum(b["count"] for b in result["aging_bands"]) == len(rows)


async def test_get_inventory_depth_handles_no_rows(monkeypatch):
    inventory.get_inventory_depth.cache_clear()

    async def fake_fetch_report(sql, params=None):
        return []

    monkeypatch.setattr(inventory.reporting, "fetch_report", fake_fetch_report)
    result = await inventory.get_inventory_depth()
    assert result["segments"] == []
    assert result["aging_bands"] == []
    assert result["potential_dead_stock_items"] == []


# ---------------------------------------------------------------------------
# get_inventory_turnover -- InventoryTurnover = COGS / current inventory
# value (annualized); DaysInventoryOutstanding = 365 / turnover. COGS
# reuses margin.get_gross_margin(); current inventory value reuses
# get_inventory_depth() -- both monkeypatched directly here rather than
# re-mocking their own SQL, which each already has its own test coverage.

def _fake_margin(cogs, coverage_pct=95.0, period="Last 90 days (2026-07-18 to 2026-10-16)"):
    return lambda period_arg=None, start_date=None, end_date=None: {
        "period": period, "cost_of_goods_sold": cogs, "cost_data_coverage_pct": coverage_pct,
    }


async def test_get_inventory_turnover_computes_annualized_turnover_and_dio(monkeypatch):
    monkeypatch.setattr(inventory.margin, "get_gross_margin", _fake_margin(5_000_000.0))
    monkeypatch.setattr(inventory, "_period_bounds", lambda *a, **k: ("2026-07-18", "2026-10-16"))

    async def fake_get_inventory_depth():
        return {"segments": [{"value": 1_000_000.0}, {"value": 500_000.0}]}

    monkeypatch.setattr(inventory, "get_inventory_depth", fake_get_inventory_depth)

    result = await inventory.get_inventory_turnover(period="90d")
    assert result["cost_of_goods_sold"] == 5_000_000.0
    assert result["current_inventory_value"] == 1_500_000.0
    assert result["days_in_window"] == 91  # Jul 18 to Oct 16, inclusive
    expected_turnover = round((5_000_000.0 / 1_500_000.0) * (365 / 91), 2)
    assert result["inventory_turnover_annualized"] == expected_turnover
    assert result["days_inventory_outstanding"] == round(365 / expected_turnover, 1)


async def test_get_inventory_turnover_is_none_without_cogs_or_inventory_value(monkeypatch):
    monkeypatch.setattr(inventory.margin, "get_gross_margin", _fake_margin(0.0, coverage_pct=None))
    monkeypatch.setattr(inventory, "_period_bounds", lambda *a, **k: ("2026-09-01", "2026-09-30"))

    async def fake_get_inventory_depth():
        return {"segments": []}

    monkeypatch.setattr(inventory, "get_inventory_depth", fake_get_inventory_depth)

    result = await inventory.get_inventory_turnover(period="2026-09")
    assert result["inventory_turnover_annualized"] is None
    assert result["days_inventory_outstanding"] is None


async def test_get_inventory_turnover_defaults_to_90d_window_when_nothing_specified(monkeypatch):
    captured = {}

    def fake_period_bounds(period, start_date, end_date):
        captured["period"] = period
        return ("2026-07-18", "2026-10-16")

    monkeypatch.setattr(inventory, "_period_bounds", fake_period_bounds)
    monkeypatch.setattr(inventory.margin, "get_gross_margin", _fake_margin(0.0, coverage_pct=None))

    async def fake_get_inventory_depth():
        return {"segments": []}

    monkeypatch.setattr(inventory, "get_inventory_depth", fake_get_inventory_depth)

    await inventory.get_inventory_turnover()
    assert captured["period"] == inventory.DEFAULT_TURNOVER_WINDOW
