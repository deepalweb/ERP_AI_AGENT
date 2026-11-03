"""Per-product purchase price deviation. _price_deviations() is pure/sync,
tested directly (no live DB) -- same pattern as test_operation_flow.py's
_repeated_bottlenecks() tests. The key property under test: a large percent
change alone is never enough to flag a product -- it must also be
statistically unusual against that SAME product's own purchase history, so
a naturally volatile product (like fresh produce) doesn't get flagged for
normal variation while a quiet step-change on a stable product does."""

from datetime import date

from mcp_server.tools import price_intelligence as pi


def _row(product_id, price_unit, day, name="Test Product", supplier="Test Supplier"):
    return {
        "product_id": product_id, "product_name": name, "price_unit": price_unit,
        "date_order": date(2026, 10, day), "partner_id": 1, "supplier_name": supplier,
        "as_of": date(2026, 10, 22),
    }


def test_flags_a_genuine_outlier_against_its_own_stable_history():
    # 10 purchases with tiny natural noise around 100 (a real stdev to
    # baseline against -- a perfectly flat series has stdev=0 and is
    # correctly skipped, see test below), then a sudden jump to 130.
    stable_prices = [100.0, 101.0, 99.0, 100.5, 99.5, 100.0, 101.5, 99.0, 100.0, 100.5]
    rows = [_row(1, p, i + 1) for i, p in enumerate(stable_prices)] + [_row(1, 130.0, 11)]

    flagged = pi._price_deviations(rows)

    assert len(flagged) == 1
    assert flagged[0]["product_id"] == 1
    assert flagged[0]["pct_change"] == 29.4


def test_zero_variance_baseline_is_skipped_not_divided_by_zero():
    # A perfectly flat baseline has stdev=0 -- no z-score can be computed,
    # so this must not crash and must not flag.
    rows = [_row(5, 100.0, d) for d in range(1, 11)] + [_row(5, 130.0, 11)]

    assert pi._price_deviations(rows) == []


def test_ignores_product_with_too_few_purchases():
    rows = [_row(2, 100.0, d) for d in range(1, 4)] + [_row(2, 200.0, 4)]  # only 4 purchases total

    assert pi._price_deviations(rows) == []


def test_ignores_statistically_unusual_but_trivially_small_change():
    # Z-score will cross threshold on an ultra-stable series, but a 2% move
    # is below MIN_ABS_PCT_CHANGE and should never qualify alone.
    rows = [_row(3, 100.0, d) for d in range(1, 11)] + [_row(3, 102.0, 11)]

    assert pi._price_deviations(rows) == []


def test_does_not_flag_a_naturally_volatile_product_for_normal_variation():
    # A produce-like series that swings +/-40% purchase to purchase -- the
    # latest swing is well within ITS OWN normal range, so z stays low even
    # though the raw percent change is large. This is the whole point of a
    # per-product baseline over a flat threshold.
    prices = [100.0, 140.0, 90.0, 130.0, 95.0, 145.0, 85.0, 135.0, 100.0, 140.0, 90.0]
    rows = [_row(4, p, i + 1) for i, p in enumerate(prices)]

    assert pi._price_deviations(rows) == []


def test_attach_grn_corroboration_handles_empty_input_without_a_query(monkeypatch):
    called = []
    monkeypatch.setattr(pi.reporting, "run_query", lambda *a, **k: called.append(1) or [])

    result = pi._attach_grn_corroboration([])

    assert result == []
    assert called == []
