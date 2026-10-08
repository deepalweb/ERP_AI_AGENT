"""Tests for the deterministic evidence layer (mcp_server/tools/evidence_engine.py):
own-breakdown concentration (trend + snapshot variants), cross-metric checks
(stock availability, dark outlet), data quality, memory, and the two
orchestrators. Pure unit tests against synthetic breakdown/trend data — no
live DB needed; get_stock_alerts / get_dark_outlets / tracking /
finding_actions are monkeypatched where used.
"""

from mcp_server.tools import evidence_engine as ee


def _breakdown(dimension, label, baseline_month, contributors):
    return {
        "dimension": dimension,
        "label": label,
        "mode": "contribution",
        "baseline_month": baseline_month,
        "top_contributors": contributors,
    }


def _contributor(name, current, baseline):
    return {"name": name, "current": current, "baseline": baseline, "delta": round(current - baseline, 2)}


# ---------------------------------------------------------------------------
# segment_concentration_checks

def test_strong_single_segment_concentration_is_confirmed():
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("store", "Store", "2026-07", [
            _contributor("Store A", 100_000, 250_000),  # delta -150,000 -> 75% of the -200,000 decline
            _contributor("Store B", 300_000, 330_000),  # delta -30,000
            _contributor("Store C", 400_000, 420_000),  # delta -20,000
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    assert len(checks) == 1
    c = checks[0]
    assert c["source"] == "store_concentration"
    assert c["verdict"] == "confirmed"
    assert c["segments"] == ["Store A"]
    assert c["concentration_pct"] == 75.0


def test_multiple_segments_combine_to_confirmed():
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("store", "Store", "2026-07", [
            _contributor("Store A", 265_000, 335_000),  # delta -70,000 -> 35%
            _contributor("Store B", 270_000, 330_000),  # delta -60,000 -> 30% (cumulative 65%)
            _contributor("Store C", 465_000, 485_000),  # delta -20,000, not needed
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    c = checks[0]
    assert c["verdict"] == "confirmed"
    assert c["segments"] == ["Store A", "Store B"]
    assert c["concentration_pct"] == 65.0


def test_fragmented_move_has_no_significant_concentration():
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("product", "Product", "2026-07", [
            _contributor("Onions", 396_000, 400_000),  # delta -4,000 -> 2%
            _contributor("Milk", 397_000, 400_000),    # delta -3,000 -> 1.5%
            _contributor("Bread", 398_000, 400_000),   # delta -2,000 -> 1%
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    c = checks[0]
    assert c["verdict"] == "no_evidence_found"
    assert "segments" not in c


def test_segment_moving_opposite_direction_is_not_counted_as_a_driver():
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("store", "Store", "2026-07", [
            # This store GREW while the total declined -- must not be treated as a driver of the decline.
            _contributor("Growing Store", 150_000, 100_000),
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    assert checks[0]["verdict"] == "no_evidence_found"


def test_rate_mode_breakdown_is_unknown_not_forced_through_count_math():
    # cancellation_rate's "store" dimension: each store's OWN rate (percentage
    # points), not additive across stores the way a raw count is -- must not
    # be run through the same concentration ratio as a contribution breakdown.
    trend = [
        {"month": "2026-06", "value": 3.5, "numerator": 3000, "denominator": 85000},
        {"month": "2026-07", "value": 2.8, "numerator": 2512, "denominator": 88398},
    ]
    anomaly = {"latest_month": "2026-07"}
    breakdown = _breakdown("store", "Store", "2026-06", [
        {"name": "Ethul Kotte", "current": 2.98, "baseline": 4.3, "delta": -1.32, "current_volume": 22011},
    ])
    breakdown["mode"] = "rate"
    checks = ee.segment_concentration_checks(anomaly, [breakdown], trend)
    assert checks[0]["verdict"] == "unknown"
    assert "rate" in checks[0]["detail"]


def test_rate_mode_breakdown_still_names_segments_moving_the_opposite_way():
    # Real case (stockout_cancellation_rate, 2026-09): a 41% company-wide
    # improvement was carried entirely by Colombo 6; the other three
    # outlets all got WORSE that month. Verdict stays "unknown" (this
    # still isn't a concentration question), but the detail must say so.
    trend = [
        {"month": "2026-08", "value": 0.55, "numerator": 400, "denominator": 72000},
        {"month": "2026-09", "value": 0.32, "numerator": 300, "denominator": 93000},
    ]
    anomaly = {"latest_month": "2026-09"}
    breakdown = _breakdown("store", "Store", "2026-08", [
        {"name": "Colombo 6", "current": 0.67, "baseline": 1.11, "delta": -0.44, "current_volume": 28504},
        {"name": "Attidiya", "current": 0.28, "baseline": 0.21, "delta": 0.07, "current_volume": 27367},
        {"name": "Pannipitiya", "current": 0.27, "baseline": 0.17, "delta": 0.11, "current_volume": 13912},
    ])
    breakdown["mode"] = "rate"
    checks = ee.segment_concentration_checks(anomaly, [breakdown], trend)
    assert checks[0]["verdict"] == "unknown"
    assert "Colombo 6" in checks[0]["detail"]
    assert "Attidiya" in checks[0]["detail"] and "Pannipitiya" in checks[0]["detail"]


def test_rate_mode_breakdown_says_nothing_extra_when_all_segments_agree():
    trend = [
        {"month": "2026-06", "value": 3.5, "numerator": 3000, "denominator": 85000},
        {"month": "2026-07", "value": 2.8, "numerator": 2512, "denominator": 88398},
    ]
    anomaly = {"latest_month": "2026-07"}
    breakdown = _breakdown("store", "Store", "2026-06", [
        {"name": "Ethul Kotte", "current": 2.98, "baseline": 4.3, "delta": -1.32, "current_volume": 22011},
        {"name": "Colombo 6", "current": 2.1, "baseline": 3.0, "delta": -0.9, "current_volume": 18000},
    ])
    breakdown["mode"] = "rate"
    checks = ee.segment_concentration_checks(anomaly, [breakdown], trend)
    assert checks[0]["verdict"] == "unknown"
    assert "Segment-level check" not in checks[0]["detail"]


def test_composition_mode_breakdown_is_unknown_not_forced_through_share_math():
    # cancellation_rate's "reason" dimension: each reason's SHARE of the mix
    # (a compositional question), not a driver of the metric's own movement.
    trend = [
        {"month": "2026-06", "value": 3.5, "numerator": 3000, "denominator": 85000},
        {"month": "2026-07", "value": 2.8, "numerator": 2512, "denominator": 88398},
    ]
    anomaly = {"latest_month": "2026-07"}
    breakdown = _breakdown("reason", "Cancellation reason", "2026-06", [
        {"name": "ITEM_ISSUE", "current": 967.0, "baseline": 653.0, "delta": 16.4,
         "current_share_pct": 38.5, "baseline_share_pct": 22.1},
    ])
    breakdown["mode"] = "composition"
    checks = ee.segment_concentration_checks(anomaly, [breakdown], trend)
    assert checks[0]["verdict"] == "unknown"
    assert "mix" in checks[0]["detail"]


def test_contribution_mode_on_a_rate_metric_uses_numerator_not_the_rate_value():
    # cancellation_rate's "product" dimension: raw cancellation COUNTS per
    # product, comparable against the total cancellation COUNT change
    # (numerator delta: 2512 - 3000 = -488), not the rate-percentage delta
    # (2.8 - 3.5 = -0.7), which is a completely different, much smaller number
    # that would make an identical raw count swing look artificially huge.
    trend = [
        {"month": "2026-06", "value": 3.5, "numerator": 3000, "denominator": 85000},
        {"month": "2026-07", "value": 2.8, "numerator": 2512, "denominator": 88398},
    ]
    anomaly = {"latest_month": "2026-07"}
    breakdown = _breakdown("product", "Product", "2026-06", [
        _contributor("Araliya Bags M", 223.0, 391.0),  # delta -168, out of a -488 total -> 34.4%
        _contributor("Araliya Bags S", 142.0, 309.0),  # delta -167, cumulative -> 68.6%
    ])
    checks = ee.segment_concentration_checks(anomaly, [breakdown], trend)
    c = checks[0]
    assert c["verdict"] == "confirmed"
    assert c["concentration_pct"] == 68.6


def test_offsetting_segments_dont_produce_a_percent_over_100():
    # Caught live on pickme_cancellation_rate: one store improved by MORE
    # than the entire net change because two smaller stores got slightly
    # worse over the same period -- the raw ratio is >100%, which is real
    # but reads like a bug as "156% of the decline came from one segment."
    trend = [{"month": "2026-06", "value": 4.3}, {"month": "2026-07", "value": 2.9}]
    anomaly = {"latest_month": "2026-07"}
    breakdowns = [
        _breakdown("store", "Store", "2026-06", [
            _contributor("Ethul Kotte", 211, 283),  # delta -72
            _contributor("Attidiya", 172, 176),      # delta -4
            _contributor("Colombo 6", 256, 244),     # delta +12 -- opposite direction
        ]),
    ]
    # net total delta = -72 - 4 + 12 = -64 (numerator/denominator not set on
    # these rows, so total falls back to the plain `value` delta: 2.9 - 4.3)
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    c = checks[0]
    assert c["verdict"] == "confirmed"
    assert "%" not in c["detail"]
    assert "more than the entire net" in c["detail"]
    assert "Ethul Kotte" in c["detail"]
    # the raw ratio is still exposed for anything that wants the real number
    assert c["concentration_pct"] > 100


def test_confirmed_detail_names_the_exact_two_months_compared():
    # Celeste Investigate Phase 3 revenue audit: this check's own
    # "increase"/"decline" is a single-prior-month move, a different
    # (usually much smaller) quantity than the case headline's own
    # baseline_mean-based percentage -- the detail text must name the
    # compared months explicitly so it can't be misread as explaining that
    # headline figure. See segment_concentration_checks' own docstring.
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("store", "Store", "2026-07", [
            _contributor("Store A", 100_000, 250_000),
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    assert "2026-07" in checks[0]["detail"]
    assert "2026-08" in checks[0]["detail"]


def test_no_evidence_found_detail_also_names_the_exact_two_months_compared():
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("product", "Product", "2026-07", [
            _contributor("Onions", 396_000, 400_000),
            _contributor("Milk", 397_000, 400_000),
            _contributor("Bread", 398_000, 400_000),
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    assert checks[0]["verdict"] == "no_evidence_found"
    assert "2026-07" in checks[0]["detail"]
    assert "2026-08" in checks[0]["detail"]


def test_offsetting_segments_detail_also_names_the_exact_two_months_compared():
    trend = [{"month": "2026-06", "value": 4.3}, {"month": "2026-07", "value": 2.9}]
    anomaly = {"latest_month": "2026-07"}
    breakdowns = [
        _breakdown("store", "Store", "2026-06", [
            _contributor("Ethul Kotte", 211, 283),
            _contributor("Attidiya", 172, 176),
            _contributor("Colombo 6", 256, 244),
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    assert "2026-06" in checks[0]["detail"]
    assert "2026-07" in checks[0]["detail"]


def test_confirmed_check_exposes_the_absolute_total_delta_and_its_unit():
    # Phase 3 final analytical validation, Check 2: "display each comparison's
    # start period, end period, absolute amount, and percentage together" --
    # total_delta/total_delta_unit let the frontend show the absolute Rs
    # amount alongside the already-present period+percentage, without any
    # new computation (the caller resolves the unit; this function only
    # forwards the already-computed delta).
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("store", "Store", "2026-07", [
            _contributor("Store A", 100_000, 250_000),  # delta -150,000
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend, total_delta_unit="currency")
    c = checks[0]
    assert c["verdict"] == "confirmed"
    assert c["total_delta"] == -200_000
    assert c["total_delta_unit"] == "currency"


def test_no_evidence_found_check_also_exposes_the_absolute_total_delta():
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("product", "Product", "2026-07", [
            _contributor("Onions", 396_000, 400_000),
            _contributor("Milk", 397_000, 400_000),
            _contributor("Bread", 398_000, 400_000),
        ]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend, total_delta_unit="currency")
    assert checks[0]["verdict"] == "no_evidence_found"
    assert checks[0]["total_delta"] == -200_000
    assert checks[0]["total_delta_unit"] == "currency"


def test_total_delta_unit_defaults_to_none_when_the_caller_does_not_resolve_one():
    # Backward-compatible default for the many existing call sites in this
    # file that don't pass total_delta_unit -- never crashes, just omits
    # a confident unit label rather than guessing one.
    trend = [{"month": "2026-07", "value": 1_000_000}, {"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [_breakdown("store", "Store", "2026-07", [_contributor("Store A", 100_000, 250_000)])]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    assert checks[0]["total_delta"] == -200_000
    assert checks[0]["total_delta_unit"] is None


def test_build_trend_evidence_resolves_count_unit_for_a_rate_metric_not_its_own_percent_unit():
    # A rate metric's total_delta is read from numerator (a raw count, e.g.
    # cancelled orders) -- never the rate's own "percent" unit, or the
    # frontend would render a raw order count as if it were a percentage.
    trend = [
        {"month": "2026-06", "value": 3.5, "numerator": 3000, "denominator": 85000, "is_partial": False},
        {"month": "2026-07", "value": 2.8, "numerator": 2512, "denominator": 88398, "is_partial": False},
    ]
    anomaly = {"latest_month": "2026-07", "category": "normal"}
    breakdowns = [
        _breakdown("store", "Store", "2026-06", [_contributor("Store A", 1200, 1500)]),
    ]
    evidence = ee.build_trend_evidence(
        "cancellation_rate", anomaly, breakdowns, trend, persistence={}, category="normal", include_memory=False
    )
    c = evidence["checks"][0]
    assert c["total_delta_unit"] == "count"


def test_build_trend_evidence_resolves_currency_unit_for_revenue():
    trend = [
        {"month": "2026-07", "value": 1_000_000, "is_partial": False},
        {"month": "2026-08", "value": 800_000, "is_partial": False},
    ]
    anomaly = {"latest_month": "2026-08", "category": "normal"}
    breakdowns = [_breakdown("store", "Store", "2026-07", [_contributor("Store A", 100_000, 250_000)])]
    evidence = ee.build_trend_evidence(
        "revenue", anomaly, breakdowns, trend, persistence={}, category="normal", include_memory=False
    )
    c = evidence["checks"][0]
    assert c["total_delta_unit"] == "currency"


def test_missing_trend_data_is_unknown_not_fabricated():
    # baseline_month "2026-07" has no matching trend entry.
    trend = [{"month": "2026-08", "value": 800_000}]
    anomaly = {"latest_month": "2026-08"}
    breakdowns = [
        _breakdown("store", "Store", "2026-07", [_contributor("Store A", 100_000, 250_000)]),
        _breakdown("product", "Product", "2026-07", [_contributor("Onions", 396_000, 400_000)]),
    ]
    checks = ee.segment_concentration_checks(anomaly, breakdowns, trend)
    assert len(checks) == 2
    assert all(c["verdict"] == "unknown" for c in checks)


# ---------------------------------------------------------------------------
# current_total_concentration_checks (state findings — a snapshot's "which
# segment holds most of the total" instead of a trend's "which segment
# drove the move")

def _snapshot_breakdown(dimension, label, contributors, total=None):
    b = {
        "dimension": dimension,
        "label": label,
        "mode": "contribution",
        "baseline_month": None,
        "top_contributors": contributors,
    }
    if total is not None:
        b["total"] = total
    return b


def _snapshot_contributor(name, current):
    return {"name": name, "current": current, "delta": current}


def test_current_total_strong_single_segment_is_confirmed():
    breakdowns = [
        _snapshot_breakdown("product", "Dead stock by product", [
            _snapshot_contributor("Product A", 82_000),
            _snapshot_contributor("Product B", 10_000),
            _snapshot_contributor("Product C", 8_000),
        ], total=100_000),
    ]
    checks = ee.current_total_concentration_checks(breakdowns)
    assert len(checks) == 1
    c = checks[0]
    assert c["source"] == "product_concentration"
    assert c["verdict"] == "confirmed"
    assert c["segments"] == ["Product A"]
    assert c["concentration_pct"] == 82.0


def test_current_total_multiple_segments_combine_to_confirmed():
    breakdowns = [
        _snapshot_breakdown("store", "Store", [
            _snapshot_contributor("Store A", 35),
            _snapshot_contributor("Store B", 30),
            _snapshot_contributor("Store C", 20),
        ], total=100),
    ]
    checks = ee.current_total_concentration_checks(breakdowns)
    c = checks[0]
    assert c["verdict"] == "confirmed"
    assert c["segments"] == ["Store A", "Store B"]
    assert c["concentration_pct"] == 65.0


def test_current_total_fragmented_has_no_significant_concentration():
    breakdowns = [
        _snapshot_breakdown("product", "Product", [
            _snapshot_contributor("Onions", 5),
            _snapshot_contributor("Milk", 4),
            _snapshot_contributor("Bread", 3),
        ], total=100),
    ]
    checks = ee.current_total_concentration_checks(breakdowns)
    c = checks[0]
    assert c["verdict"] == "no_evidence_found"
    assert "segments" not in c


def test_current_total_rate_mode_is_unknown_not_forced_through_count_math():
    breakdown = _snapshot_breakdown("store", "Store", [_snapshot_contributor("Store A", 4.3)])
    breakdown["mode"] = "rate"
    checks = ee.current_total_concentration_checks([breakdown])
    assert checks[0]["verdict"] == "unknown"
    assert "rate" in checks[0]["detail"]


def test_current_total_falls_back_to_summing_contributors_when_total_not_declared():
    # No explicit "total" -- e.g. _stock_availability_finding's by-outlet
    # breakdown, whose top_contributors already IS the complete list, so the
    # sum of what's shown legitimately equals the true total.
    breakdowns = [
        _snapshot_breakdown("store", "Store", [
            _snapshot_contributor("Colombo 6", 18),
            _snapshot_contributor("Attidiya", 2),
        ]),  # total omitted
    ]
    checks = ee.current_total_concentration_checks(breakdowns)
    c = checks[0]
    assert c["verdict"] == "confirmed"
    assert c["segments"] == ["Colombo 6"]
    assert c["concentration_pct"] == 90.0


def test_current_total_no_evidence_when_no_contributors():
    checks = ee.current_total_concentration_checks([_snapshot_breakdown("product", "Product", [], total=0)])
    assert checks[0]["verdict"] == "no_evidence_found"


# ---------------------------------------------------------------------------
# stock_availability_check

def test_stock_check_is_possible_contributor_when_declining_product_is_low_stock(monkeypatch):
    breakdowns = [
        _breakdown("product", "Product", "2026-07", [
            _contributor("Red Big Onion 250G", 100_000, 150_000),  # declined
            _contributor("Milk 1L", 200_000, 190_000),  # grew, should be ignored
        ]),
    ]

    def fake_get_stock_alerts():
        return {
            "as_of_date": "2026-08-02",
            "top_out_of_stock": [{"name": "Red Big Onion 250G"}],
            "top_low_stock": [],
        }

    monkeypatch.setattr("backend.app.services.stock_alerts.get_stock_alerts", fake_get_stock_alerts)
    # moved_up=False -- a revenue-like metric, where the culprit product's
    # OWN contribution declined (its sales fell).
    result = ee.stock_availability_check(breakdowns, moved_up=False)
    assert result["verdict"] == "possible_contributor"
    assert result["segments"] == ["Red Big Onion 250G"]


def test_stock_check_is_possible_contributor_when_growing_product_is_low_stock(monkeypatch):
    """moved_up=True -- a cancellation_rate-like metric, where the culprit
    product's OWN contribution grew (more cancellations attributed to it,
    not fewer). Regression test for the bug this check used to have: it
    hardcoded delta < 0 (declining), which only ever matched a
    revenue/order_volume-shaped metric and could never confirm a stockout
    as the cause of a rising cancellation count — the exact scenario that
    was traced live this session."""
    breakdowns = [
        _breakdown("product", "Product", "2026-07", [
            _contributor("Ambewela Fresh Milk 1L", 190_000, 100_000),  # grew -- more cancellations
            _contributor("Milk 1L", 100_000, 200_000),  # declined, should be ignored
        ]),
    ]

    def fake_get_stock_alerts():
        return {
            "as_of_date": "2026-08-02",
            "top_out_of_stock": [{"name": "Ambewela Fresh Milk 1L"}],
            "top_low_stock": [],
        }

    monkeypatch.setattr("backend.app.services.stock_alerts.get_stock_alerts", fake_get_stock_alerts)
    result = ee.stock_availability_check(breakdowns, moved_up=True)
    assert result["verdict"] == "possible_contributor"
    assert result["segments"] == ["Ambewela Fresh Milk 1L"]


def test_stock_check_is_no_evidence_found_when_no_overlap(monkeypatch):
    breakdowns = [
        _breakdown("product", "Product", "2026-07", [_contributor("Red Big Onion 250G", 100_000, 150_000)]),
    ]

    def fake_get_stock_alerts():
        return {"as_of_date": "2026-08-02", "top_out_of_stock": [], "top_low_stock": []}

    monkeypatch.setattr("backend.app.services.stock_alerts.get_stock_alerts", fake_get_stock_alerts)
    result = ee.stock_availability_check(breakdowns, moved_up=False)
    assert result["verdict"] == "no_evidence_found"


def test_stock_check_is_unknown_when_no_product_breakdown():
    result = ee.stock_availability_check([_breakdown("store", "Store", "2026-07", [])], moved_up=False)
    assert result["verdict"] == "unknown"


# ---------------------------------------------------------------------------
# dark_outlet_check

def test_dark_outlet_check_is_confirmed_when_flagged_store_has_zero_orders(monkeypatch):
    breakdowns = [_breakdown("store", "Store", "2026-07", [_contributor("Ja-Ela", 0, 50_000)])]
    monkeypatch.setattr("backend.app.services.operations.get_dark_outlets", lambda: ["Ja-Ela"])
    result = ee.dark_outlet_check(breakdowns)
    assert result["verdict"] == "confirmed"
    assert result["segments"] == ["Ja-Ela"]


def test_dark_outlet_check_is_no_evidence_found_when_flagged_store_still_trading(monkeypatch):
    breakdowns = [_breakdown("store", "Store", "2026-07", [_contributor("Colombo 6", 100_000, 150_000)])]
    monkeypatch.setattr("backend.app.services.operations.get_dark_outlets", lambda: ["Ja-Ela"])
    result = ee.dark_outlet_check(breakdowns)
    assert result["verdict"] == "no_evidence_found"


def test_dark_outlet_check_is_no_evidence_found_when_no_outlet_is_dark(monkeypatch):
    breakdowns = [_breakdown("store", "Store", "2026-07", [_contributor("Colombo 6", 100_000, 150_000)])]
    monkeypatch.setattr("backend.app.services.operations.get_dark_outlets", lambda: [])
    result = ee.dark_outlet_check(breakdowns)
    assert result["verdict"] == "no_evidence_found"


def test_dark_outlet_check_is_unknown_when_no_store_breakdown():
    result = ee.dark_outlet_check([_breakdown("product", "Product", "2026-07", [])])
    assert result["verdict"] == "unknown"


# ---------------------------------------------------------------------------
# shrinkage_loss_damage_check (Completed Stock-Count Variance & Shrinkage
# Investigation) -- same cross-source-overlap posture as stock_availability_
# check, capped at "possible_contributor" (never "confirmed", unlike
# dark_outlet_check -- overlap with loss/damage data has no guaranteed
# mechanical link the way zero-orders-means-zero-revenue does).

def _loss_and_damage(shortage_stores=(), damage_stores=()):
    return {
        "shortage_by_store": [{"outlet": name, "qty": 10} for name in shortage_stores],
        "damaged_by_store": [{"outlet": name, "qty": 5} for name in damage_stores],
    }


def test_shrinkage_loss_damage_check_is_possible_contributor_on_overlap(monkeypatch):
    breakdowns = [_breakdown("store", "Store", None, [_contributor("Celeste Daily Colombo 6", 100_000, 0)])]
    monkeypatch.setattr(
        "backend.app.services.loss_and_damage.get_loss_and_damage",
        lambda start_date=None, end_date=None, period=None: _loss_and_damage(shortage_stores=["Celeste Daily Colombo 6"]),
    )
    result = ee.shrinkage_loss_damage_check(breakdowns, "2026-06-01", "2026-09-01")
    assert result["verdict"] == "possible_contributor"
    assert result["segments"] == ["Celeste Daily Colombo 6"]
    assert "not proof" in result["detail"]


def test_shrinkage_loss_damage_check_is_no_evidence_found_when_no_overlap(monkeypatch):
    breakdowns = [_breakdown("store", "Store", None, [_contributor("Celeste Daily Colombo 6", 100_000, 0)])]
    monkeypatch.setattr(
        "backend.app.services.loss_and_damage.get_loss_and_damage",
        lambda start_date=None, end_date=None, period=None: _loss_and_damage(shortage_stores=["Celeste Daily - Attidiya"]),
    )
    result = ee.shrinkage_loss_damage_check(breakdowns, "2026-06-01", "2026-09-01")
    assert result["verdict"] == "no_evidence_found"


def test_shrinkage_loss_damage_check_is_no_evidence_found_when_nothing_flagged(monkeypatch):
    breakdowns = [_breakdown("store", "Store", None, [_contributor("Celeste Daily Colombo 6", 100_000, 0)])]
    monkeypatch.setattr(
        "backend.app.services.loss_and_damage.get_loss_and_damage",
        lambda start_date=None, end_date=None, period=None: _loss_and_damage(),
    )
    result = ee.shrinkage_loss_damage_check(breakdowns, "2026-06-01", "2026-09-01")
    assert result["verdict"] == "no_evidence_found"


def test_shrinkage_loss_damage_check_is_unknown_when_no_store_breakdown():
    result = ee.shrinkage_loss_damage_check([_breakdown("product_type", "Product type", None, [])], "2026-06-01", "2026-09-01")
    assert result["verdict"] == "unknown"


def test_shrinkage_loss_damage_check_never_confirms(monkeypatch):
    """Unlike dark_outlet_check, this must never emit 'confirmed' -- overlap
    with loss/damage data has no definitional cause-effect link."""
    breakdowns = [_breakdown("store", "Store", None, [_contributor("Celeste Daily Colombo 6", 100_000, 0)])]
    monkeypatch.setattr(
        "backend.app.services.loss_and_damage.get_loss_and_damage",
        lambda start_date=None, end_date=None, period=None: _loss_and_damage(
            shortage_stores=["Celeste Daily Colombo 6"], damage_stores=["Celeste Daily Colombo 6"],
        ),
    )
    result = ee.shrinkage_loss_damage_check(breakdowns, "2026-06-01", "2026-09-01")
    assert result["verdict"] != "confirmed"


# ---------------------------------------------------------------------------
# data_quality_check / snapshot_data_quality_check

def test_data_quality_is_sufficient_with_enough_complete_months():
    trend = [{"month": f"2026-0{i}", "value": 1, "is_partial": False} for i in range(1, 6)]
    result = ee.data_quality_check(trend)
    assert result["verdict"] == "sufficient"
    assert result["months_available"] == 5


def test_data_quality_is_low_confidence_under_the_floor():
    trend = [
        {"month": "2026-07", "value": 1, "is_partial": False},
        {"month": "2026-08", "value": 1, "is_partial": False},
        {"month": "2026-09", "value": 1, "is_partial": True},
    ]
    result = ee.data_quality_check(trend)
    assert result["verdict"] == "low_confidence"
    assert result["months_available"] == 2


def test_snapshot_data_quality_accepts_iso_string():
    result = ee.snapshot_data_quality_check("2026-09-11")
    assert result["source"] == "data_quality"
    assert result["verdict"] in ("sufficient", "low_confidence")
    assert isinstance(result["age_days"], int)


def test_snapshot_data_quality_flags_stale_snapshot():
    result = ee.snapshot_data_quality_check("2020-01-01")
    assert result["verdict"] == "low_confidence"


def test_snapshot_data_quality_unknown_when_no_date():
    result = ee.snapshot_data_quality_check(None)
    assert result["verdict"] == "unknown"


# ---------------------------------------------------------------------------
# cross_metric_corroboration_checks
#
# Uses REAL metric_ids from intelligence.METRICS throughout (not synthetic
# ones) so _independence_group's lookup exercises the actual, inspected
# groupings this fix is about -- revenue/order_volume/average_order_value
# genuinely share "order_funnel" (see intelligence.py), while
# customer_retention_rate/uber_failure_rate/pickme_cancellation_rate/
# gross_margin default to their own id (independent) because they were
# checked and found to come from disjoint tables/populations.

def _investigation(metric_id, label, checks, period="2026-08", category="critical", data_quality_verdict="sufficient"):
    return {
        "metric_id": metric_id,
        "metric": {"id": metric_id, "label": label},
        "category": category,
        "confirm": {"period": period},
        "data_quality": {"verdict": data_quality_verdict},
        "root_cause": {"checks": list(checks), "summary": ee.summarize(checks)},
    }


def _concentration_check(dimension, segments, verdict="confirmed"):
    check = {"source": f"{dimension}_concentration", "verdict": verdict}
    if verdict == "confirmed":
        check["segments"] = segments
    return check


def _sourced(checks, source):
    return [c for c in checks if c["source"] == source]


def test_same_independence_group_is_related_not_independent_not_confirmed():
    # revenue and order_volume share "order_funnel" -- agreeing is
    # guaranteed by their shared formula, not new evidence.
    revenue = _investigation("revenue", "Revenue", [_concentration_check("store", ["Colombo 6"])])
    volume = _investigation("order_volume", "Confirmed order volume", [_concentration_check("store", ["Colombo 6"])])
    investigations = [revenue, volume]
    ee.cross_metric_corroboration_checks(investigations)

    for inv, other_label in ((revenue, "Confirmed order volume"), (volume, "Revenue")):
        assert _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration") == []
        related = _sourced(inv["root_cause"]["checks"], "related_metric_agreement")
        assert len(related) == 1
        assert related[0]["verdict"] == "related_not_independent"
        assert other_label in related[0]["detail"]
        assert "related_not_independent" not in inv["root_cause"]["summary"]["possible_contributor"]
        assert "related_metric_agreement" in inv["root_cause"]["summary"]["related_not_independent"]


def test_repeated_dependent_observations_dont_escalate_the_verdict():
    # revenue + order_volume + average_order_value all agree -- three
    # dependent metrics piling on must still never become "confirmed",
    # and must still be exactly ONE related_metric_agreement check.
    revenue = _investigation("revenue", "Revenue", [_concentration_check("store", ["Colombo 6"])])
    volume = _investigation("order_volume", "Confirmed order volume", [_concentration_check("store", ["Colombo 6"])])
    aov = _investigation("average_order_value", "Average order value", [_concentration_check("store", ["Colombo 6"])])
    investigations = [revenue, volume, aov]
    ee.cross_metric_corroboration_checks(investigations)

    related = _sourced(revenue["root_cause"]["checks"], "related_metric_agreement")
    assert len(related) == 1
    assert related[0]["verdict"] == "related_not_independent"
    assert "Confirmed order volume" in related[0]["detail"]
    assert "Average order value" in related[0]["detail"]
    assert all(c["verdict"] != "confirmed" for c in revenue["root_cause"]["checks"] if c["source"] != "store_concentration")


def test_independent_metrics_without_a_declared_relationship_make_no_claim():
    # revenue (order_funnel) and uber_failure_rate (its own, unrelated
    # group -- a different courier company's own table) both naming the
    # same segment is a real OBSERVATION (co-location), but independence
    # alone is not enough for an explanatory claim -- no relationship
    # between these two is actually declared, so neither a corroboration
    # nor a conflict check is manufactured. Each metric's own confirmed
    # concentration evidence is preserved untouched either way.
    revenue = _investigation("revenue", "Revenue", [_concentration_check("store", ["Colombo 6"])], category="critical")
    uber = _investigation("uber_failure_rate", "Uber order failure rate", [_concentration_check("store", ["Colombo 6"])], category="critical")
    investigations = [revenue, uber]
    ee.cross_metric_corroboration_checks(investigations)

    for inv in (revenue, uber):
        assert _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration") == []
        assert _sourced(inv["root_cause"]["checks"], "conflicting_signal") == []
        assert _sourced(inv["root_cause"]["checks"], "related_metric_agreement") == []
        # The metric's own affected-segment evidence is untouched.
        own_check = _sourced(inv["root_cause"]["checks"], "store_concentration")
        assert own_check[0]["verdict"] == "confirmed"
        assert own_check[0]["segments"] == ["Colombo 6"]


def test_declared_same_direction_relationship_honored_as_support(monkeypatch):
    # Symmetric to the opposite_direction case below: a declared
    # same_direction pair actually moving together IS real, reviewed
    # explanatory support.
    fake_a = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {"metric_b": "same_direction"}})()
    fake_b = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {}})()
    monkeypatch.setattr(ee.intelligence, "METRICS", {"metric_a": fake_a, "metric_b": fake_b})

    a = _investigation("metric_a", "Metric A", [_concentration_check("store", ["Colombo 6"])], category="critical")
    b = _investigation("metric_b", "Metric B", [_concentration_check("store", ["Colombo 6"])], category="critical")
    ee.cross_metric_corroboration_checks([a, b])

    for inv in (a, b):
        cross = _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration")
        assert len(cross) == 1
        assert cross[0]["verdict"] == "possible_contributor"
        assert _sourced(inv["root_cause"]["checks"], "conflicting_signal") == []


def test_never_emits_a_confirmed_verdict_no_matter_how_many_metrics_agree():
    # Five metrics (some dependent via independence_group, some
    # independent with no declared relationship) all naming the same
    # segment -- root_cause must contain zero "confirmed" verdicts from
    # ANY of the three cross-metric sources, regardless of count. The
    # order_funnel trio (revenue/order_volume/average_order_value) still
    # produces related_metric_agreement (dependence isn't gated on a
    # declared relationship); uber/pickme, undeclared against everything
    # here, correctly produce no cross-metric check at all -- both
    # outcomes are checked explicitly rather than assumed.
    metrics = [
        ("revenue", "Revenue", "critical"),
        ("order_volume", "Confirmed order volume", "critical"),
        ("average_order_value", "Average order value", "critical"),
        ("uber_failure_rate", "Uber order failure rate", "critical"),
        ("pickme_cancellation_rate", "PickMe order cancellation rate", "critical"),
    ]
    investigations = [_investigation(mid, label, [_concentration_check("store", ["Colombo 6"])], category=cat) for mid, label, cat in metrics]
    by_id = dict(zip((m[0] for m in metrics), investigations))
    ee.cross_metric_corroboration_checks(investigations)

    for inv in investigations:
        cross_sourced = [c for c in inv["root_cause"]["checks"] if c["source"] in
                          ("cross_metric_corroboration", "related_metric_agreement", "conflicting_signal")]
        assert all(c["verdict"] != "confirmed" for c in cross_sourced)

    for dependent_id in ("revenue", "order_volume", "average_order_value"):
        assert _sourced(by_id[dependent_id]["root_cause"]["checks"], "related_metric_agreement") != []
    for undeclared_id in ("uber_failure_rate", "pickme_cancellation_rate"):
        for source in ("cross_metric_corroboration", "related_metric_agreement", "conflicting_signal"):
            assert _sourced(by_id[undeclared_id]["root_cause"]["checks"], source) == []


def test_uber_and_pickme_are_independent_but_make_no_unsupported_claim():
    # Different courier companies, different tables (uber_order vs
    # pickme_order), disjoint order populations -- inspected, not assumed,
    # per the audit requirement, so neither is grouped with the other and
    # both are independence_reviewed. That establishes INDEPENDENCE, a
    # separate question from explanatory relevance: no relationship
    # between them is actually declared, so agreeing on a segment does not
    # by itself produce a corroboration (or conflict) claim.
    assert ee._independence_group("uber_failure_rate") != ee._independence_group("pickme_cancellation_rate")
    assert ee.intelligence.METRICS["uber_failure_rate"].independence_reviewed is True
    assert ee.intelligence.METRICS["pickme_cancellation_rate"].independence_reviewed is True

    uber = _investigation("uber_failure_rate", "Uber order failure rate", [_concentration_check("store", ["Ethul Kotte"])])
    pickme = _investigation("pickme_cancellation_rate", "PickMe order cancellation rate", [_concentration_check("store", ["Ethul Kotte"])])
    ee.cross_metric_corroboration_checks([uber, pickme])
    for inv in (uber, pickme):
        assert _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration") == []
        assert _sourced(inv["root_cause"]["checks"], "conflicting_signal") == []


def test_undeclared_category_mismatch_produces_no_claim_either_way():
    # Same segment, same period, opposite category (critical vs
    # opportunity), but NO declared expected_relationship between these two
    # metrics -- an undeclared pair must not be reported as "conflicting"
    # (that would be an unsupported contradiction claim) OR as supporting.
    # It says nothing, which is the honest answer when no relationship was
    # ever declared.
    revenue = _investigation("revenue", "Revenue", [_concentration_check("store", ["Colombo 6"])], category="critical")
    uber = _investigation("uber_failure_rate", "Uber order failure rate", [_concentration_check("store", ["Colombo 6"])], category="opportunity")
    investigations = [revenue, uber]
    ee.cross_metric_corroboration_checks(investigations)

    for inv in (revenue, uber):
        assert _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration") == []
        assert _sourced(inv["root_cause"]["checks"], "conflicting_signal") == []
        assert _sourced(inv["root_cause"]["checks"], "related_metric_agreement") == []


def test_declared_same_direction_relationship_violated_is_surfaced_as_conflicting(monkeypatch):
    # Two metrics with a DECLARED same_direction expectation (fabricated
    # for this test -- no real pair in METRICS declares one today) that
    # actually moved in different categories: this IS a real, stated
    # contradiction, and must surface as conflicting_signal.
    fake_a = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {"metric_b": "same_direction"}})()
    fake_b = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {}})()
    monkeypatch.setattr(ee.intelligence, "METRICS", {"metric_a": fake_a, "metric_b": fake_b})

    a = _investigation("metric_a", "Metric A", [_concentration_check("store", ["Colombo 6"])], category="critical")
    b = _investigation("metric_b", "Metric B", [_concentration_check("store", ["Colombo 6"])], category="opportunity")
    ee.cross_metric_corroboration_checks([a, b])

    for inv in (a, b):
        assert _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration") == []
        conflict = _sourced(inv["root_cause"]["checks"], "conflicting_signal")
        assert len(conflict) == 1
        assert conflict[0]["verdict"] == "no_evidence_found"


def test_declared_opposite_direction_relationship_honored_as_support(monkeypatch):
    # A declared opposite_direction pair moving in opposite categories is
    # the EXPECTED, consistent pattern -- counts as support, not conflict.
    fake_a = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {"metric_b": "opposite_direction"}})()
    fake_b = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {}})()
    monkeypatch.setattr(ee.intelligence, "METRICS", {"metric_a": fake_a, "metric_b": fake_b})

    a = _investigation("metric_a", "Metric A", [_concentration_check("store", ["Colombo 6"])], category="critical")
    b = _investigation("metric_b", "Metric B", [_concentration_check("store", ["Colombo 6"])], category="opportunity")
    ee.cross_metric_corroboration_checks([a, b])

    for inv in (a, b):
        cross = _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration")
        assert len(cross) == 1
        assert cross[0]["verdict"] == "possible_contributor"
        assert _sourced(inv["root_cause"]["checks"], "conflicting_signal") == []


def test_unreviewed_metric_does_not_strengthen_or_conflict(monkeypatch):
    # A never-inspected metric (independence_reviewed defaults to False)
    # must not participate in either direction, even when it names the
    # same segment in the same period.
    reviewed = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {}})()
    unreviewed = type("M", (), {"independence_group": "", "independence_reviewed": False, "expected_relationship": {}})()
    monkeypatch.setattr(ee.intelligence, "METRICS", {"metric_a": reviewed, "metric_b": unreviewed})

    a = _investigation("metric_a", "Metric A", [_concentration_check("store", ["Colombo 6"])], category="critical")
    b = _investigation("metric_b", "Metric B", [_concentration_check("store", ["Colombo 6"])], category="critical")
    ee.cross_metric_corroboration_checks([a, b])

    for inv in (a, b):
        assert _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration") == []
        assert _sourced(inv["root_cause"]["checks"], "related_metric_agreement") == []
        assert _sourced(inv["root_cause"]["checks"], "conflicting_signal") == []


def test_revenue_and_uber_failure_rate_are_both_reviewed_and_undeclared():
    # The real, live-verified example this whole mechanism was built
    # around: both are marked independence_reviewed=True (see
    # intelligence.py's backfill), and neither declares an
    # expected_relationship with the other -- so same-category agreement
    # still corroborates exactly as it did before this change (this
    # mechanism must not have altered that real, already-verified case).
    revenue = ee.intelligence.METRICS["revenue"]
    uber = ee.intelligence.METRICS["uber_failure_rate"]
    assert revenue.independence_reviewed is True
    assert uber.independence_reviewed is True
    assert "uber_failure_rate" not in (revenue.expected_relationship or {})
    assert "revenue" not in (uber.expected_relationship or {})


def test_misaligned_time_periods_dont_corroborate():
    revenue = _investigation("revenue", "Revenue", [_concentration_check("store", ["Colombo 6"])], period="2026-08")
    uber = _investigation("uber_failure_rate", "Uber order failure rate", [_concentration_check("store", ["Colombo 6"])], period="2026-07")
    investigations = [revenue, uber]
    ee.cross_metric_corroboration_checks(investigations)
    for inv in investigations:
        assert _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration") == []
        assert _sourced(inv["root_cause"]["checks"], "related_metric_agreement") == []
        assert _sourced(inv["root_cause"]["checks"], "conflicting_signal") == []


def test_low_confidence_finding_does_not_participate_either_side():
    # A thin-history finding must not corroborate others, and must not be
    # corroborated by others -- weak evidence shouldn't strengthen, or be
    # strengthened.
    revenue = _investigation("revenue", "Revenue", [_concentration_check("store", ["Colombo 6"])], data_quality_verdict="sufficient")
    uber = _investigation("uber_failure_rate", "Uber order failure rate", [_concentration_check("store", ["Colombo 6"])], data_quality_verdict="low_confidence")
    investigations = [revenue, uber]
    ee.cross_metric_corroboration_checks(investigations)
    assert _sourced(revenue["root_cause"]["checks"], "cross_metric_corroboration") == []
    # the low-confidence side is skipped entirely -- it gets no cross-metric
    # check of its own either, since it never participates as a comparer.
    assert _sourced(uber["root_cause"]["checks"], "cross_metric_corroboration") == []


def test_different_entities_are_not_combined_across_dimensions():
    # Same segment NAME, but one is a store-concentration and the other a
    # customer-concentration -- different entity types, not a real match.
    revenue = _investigation("revenue", "Revenue", [_concentration_check("store", ["Ethul Kotte"])])
    other = _investigation("uber_failure_rate", "Uber order failure rate", [_concentration_check("customer", ["Ethul Kotte"])])
    investigations = [revenue, other]
    ee.cross_metric_corroboration_checks(investigations)
    for inv in investigations:
        assert _sourced(inv["root_cause"]["checks"], "cross_metric_corroboration") == []
        assert _sourced(inv["root_cause"]["checks"], "related_metric_agreement") == []


def test_no_cross_metric_checks_when_only_one_metric_confirms():
    revenue = _investigation("revenue", "Revenue", [_concentration_check("store", ["Attidiya"])])
    other = _investigation("order_volume", "Confirmed order volume", [_concentration_check("store", ["Attidiya"], verdict="no_evidence_found")])
    investigations = [revenue, other]
    ee.cross_metric_corroboration_checks(investigations)
    for inv in investigations:
        for source in ("cross_metric_corroboration", "related_metric_agreement", "conflicting_signal"):
            assert _sourced(inv["root_cause"]["checks"], source) == []


def test_does_not_self_match_a_single_finding():
    solo = _investigation("revenue", "Revenue", [_concentration_check("store", ["Pannipitiya"])])
    ee.cross_metric_corroboration_checks([solo])
    for source in ("cross_metric_corroboration", "related_metric_agreement", "conflicting_signal"):
        assert _sourced(solo["root_cause"]["checks"], source) == []


def test_names_every_independently_corroborating_metric(monkeypatch):
    # Fabricated metrics with a genuinely declared relationship against
    # BOTH others -- this test's own purpose is the detail-text building
    # (every corroborating metric's label actually gets listed), which
    # only fires at all once a real declaration exists.
    fake_a = type("M", (), {"independence_group": "", "independence_reviewed": True,
                             "expected_relationship": {"metric_b": "same_direction", "metric_c": "same_direction"}})()
    fake_b = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {}})()
    fake_c = type("M", (), {"independence_group": "", "independence_reviewed": True, "expected_relationship": {}})()
    monkeypatch.setattr(ee.intelligence, "METRICS", {"metric_a": fake_a, "metric_b": fake_b, "metric_c": fake_c})

    a = _investigation("metric_a", "Metric A", [_concentration_check("store", ["Colombo 6"])])
    b = _investigation("metric_b", "Metric B", [_concentration_check("store", ["Colombo 6"])])
    c = _investigation("metric_c", "Metric C", [_concentration_check("store", ["Colombo 6"])])
    investigations = [a, b, c]
    ee.cross_metric_corroboration_checks(investigations)
    cross = _sourced(a["root_cause"]["checks"], "cross_metric_corroboration")[0]
    assert "Metric B" in cross["detail"]
    assert "Metric C" in cross["detail"]


def test_unrelated_existing_verdicts_are_untouched_by_this_fix():
    # segment_concentration_checks' own "confirmed" verdict (a same-metric,
    # within-breakdown result) must survive completely unchanged -- this
    # fix only ever appends NEW checks, never edits existing ones.
    own_check = _concentration_check("store", ["Colombo 6"])
    revenue = _investigation("revenue", "Revenue", [own_check])
    ee.cross_metric_corroboration_checks([revenue])
    assert revenue["root_cause"]["checks"][0] == own_check
    assert revenue["root_cause"]["summary"]["confirmed"] == ["store_concentration"]


# ---------------------------------------------------------------------------
# summarize

def test_summarize_buckets_checks_by_verdict():
    checks = [
        {"source": "store_concentration", "verdict": "confirmed"},
        {"source": "stock_availability", "verdict": "possible_contributor"},
        {"source": "dark_outlet", "verdict": "no_evidence_found"},
        {"source": "reason_concentration", "verdict": "unknown"},
    ]
    summary = ee.summarize(checks)
    assert summary["confirmed"] == ["store_concentration"]
    assert summary["possible_contributor"] == ["stock_availability"]
    assert summary["no_evidence_found"] == ["dark_outlet"]
    assert summary["unknown"] == ["reason_concentration"]


# ---------------------------------------------------------------------------
# get_finding_memory (Context+Memory)

def test_finding_memory_is_none_when_no_prior_period(monkeypatch):
    monkeypatch.setattr("backend.app.services.tracking.get_most_recent_prior", lambda metric_id, before_period: None)
    assert ee.get_finding_memory("revenue", "2026-09") is None


def test_finding_memory_resolved_prior_includes_verdict(monkeypatch):
    prior = {
        "period": "2026-07", "status": "resolved", "verdict": "unchanged",
        "detected_pct_change": 106.43, "outcome_pct_change": -88.6, "outcome_period": "2026-08",
        "recommendations": [{"action": "Investigate the spike"}],
    }
    monkeypatch.setattr("backend.app.services.tracking.get_most_recent_prior", lambda metric_id, before_period: prior)
    monkeypatch.setattr("backend.app.services.finding_actions.get_actions_for", lambda ids: {})
    memory = ee.get_finding_memory("revenue", "2026-09")
    assert memory["prior_period"] == "2026-07"
    assert memory["outcome_verdict"] == "unchanged"
    assert "106.43%" in memory["outcome_detail"] and "-88.6%" in memory["outcome_detail"]
    assert "action_taken_by_team" not in memory


def test_finding_memory_open_prior_is_pending_not_fabricated(monkeypatch):
    prior = {
        "period": "2026-08", "status": "open", "verdict": None,
        "detected_pct_change": 40.0, "outcome_pct_change": None, "outcome_period": None,
        "recommendations": [],
    }
    monkeypatch.setattr("backend.app.services.tracking.get_most_recent_prior", lambda metric_id, before_period: prior)
    monkeypatch.setattr("backend.app.services.finding_actions.get_actions_for", lambda ids: {})
    memory = ee.get_finding_memory("revenue", "2026-09")
    assert memory["outcome_verdict"] == "pending"


def test_finding_memory_includes_action_taken_when_a_manager_logged_one(monkeypatch):
    prior = {
        "period": "2026-07", "status": "resolved", "verdict": "worsened",
        "detected_pct_change": 20.0, "outcome_pct_change": 30.0, "outcome_period": "2026-08",
        "recommendations": [],
    }
    monkeypatch.setattr("backend.app.services.tracking.get_most_recent_prior", lambda metric_id, before_period: prior)
    monkeypatch.setattr(
        "backend.app.services.finding_actions.get_actions_for",
        lambda ids: {"revenue-2026-07": {"action_taken": "Ran a promo at Colombo 6", "status": "completed"}},
    )
    memory = ee.get_finding_memory("revenue", "2026-09")
    assert memory["action_taken_by_team"] == "Ran a promo at Colombo 6"


# ---------------------------------------------------------------------------
# build_trend_evidence / build_snapshot_evidence — orchestration

def test_build_trend_evidence_skips_stock_and_dark_outlet_for_an_opportunity(monkeypatch):
    calls = []
    monkeypatch.setattr(ee, "stock_availability_check", lambda breakdowns, moved_up: calls.append("stock"))
    monkeypatch.setattr(ee, "dark_outlet_check", lambda breakdowns: calls.append("dark"))
    monkeypatch.setattr(ee, "get_finding_memory", lambda metric_id, period: None)
    trend = [
        {"month": "2026-07", "value": 800_000, "is_partial": False},
        {"month": "2026-08", "value": 1_000_000, "is_partial": False},
    ]
    anomaly = {"latest_month": "2026-08"}
    # total increase is +200,000; Store A alone accounts for +180,000 (90%) of it.
    breakdowns = [_breakdown("store", "Store", "2026-07", [_contributor("Store A", 280_000, 100_000)])]
    persistence = {"pattern": "single_period_move", "consecutive_periods": 1}
    evidence = ee.build_trend_evidence("revenue", anomaly, breakdowns, trend, persistence, "opportunity")
    assert calls == []  # cross-metric checks must not run for a non-critical finding
    assert evidence["checks"][0]["verdict"] == "confirmed"
    assert evidence["historical_context"] == persistence


def test_build_trend_evidence_runs_cross_metric_checks_for_a_critical_finding(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.stock_alerts.get_stock_alerts",
        lambda: {"as_of_date": "2026-08-02", "top_out_of_stock": [], "top_low_stock": []},
    )
    monkeypatch.setattr("backend.app.services.operations.get_dark_outlets", lambda: [])
    monkeypatch.setattr(ee, "get_finding_memory", lambda metric_id, period: None)
    trend = [
        {"month": "2026-07", "value": 1_000_000, "is_partial": False},
        {"month": "2026-08", "value": 800_000, "is_partial": False},
    ]
    anomaly = {"latest_month": "2026-08", "moved_up": False}
    breakdowns = [_breakdown("store", "Store", "2026-07", [_contributor("Store A", 100_000, 250_000)])]
    persistence = {"pattern": "single_period_move", "consecutive_periods": 1}
    evidence = ee.build_trend_evidence("revenue", anomaly, breakdowns, trend, persistence, "critical")
    sources = {c["source"] for c in evidence["checks"]}
    assert "stock_availability" in sources
    assert "dark_outlet" in sources
    assert evidence["data_quality"]["verdict"] == "low_confidence"  # only 2 complete months in this trend


def test_build_trend_evidence_omits_memory_when_include_memory_is_false(monkeypatch):
    calls = []
    monkeypatch.setattr(ee, "get_finding_memory", lambda metric_id, period: calls.append(1))
    trend = [
        {"month": "2026-07", "value": 1_000_000, "is_partial": False},
        {"month": "2026-08", "value": 800_000, "is_partial": False},
    ]
    anomaly = {"latest_month": "2026-08", "moved_up": True}
    evidence = ee.build_trend_evidence(
        "cancellation_rate", anomaly, [], trend, {"pattern": "flat", "consecutive_periods": 0}, "critical",
        include_memory=False,
    )
    assert evidence["memory"] is None
    assert calls == []


def test_build_snapshot_evidence_has_no_memory_or_historical_context():
    breakdowns = [_snapshot_breakdown("product", "Product", [_snapshot_contributor("Product A", 100)], total=100)]
    evidence = ee.build_snapshot_evidence(breakdowns, "2026-09-11")
    assert "memory" not in evidence
    assert "historical_context" not in evidence
    assert evidence["checks"][0]["verdict"] == "confirmed"
    assert evidence["data_quality"]["source"] == "data_quality"
