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
    result = ee.stock_availability_check(breakdowns)
    assert result["verdict"] == "possible_contributor"
    assert result["segments"] == ["Red Big Onion 250G"]


def test_stock_check_is_no_evidence_found_when_no_overlap(monkeypatch):
    breakdowns = [
        _breakdown("product", "Product", "2026-07", [_contributor("Red Big Onion 250G", 100_000, 150_000)]),
    ]

    def fake_get_stock_alerts():
        return {"as_of_date": "2026-08-02", "top_out_of_stock": [], "top_low_stock": []}

    monkeypatch.setattr("backend.app.services.stock_alerts.get_stock_alerts", fake_get_stock_alerts)
    result = ee.stock_availability_check(breakdowns)
    assert result["verdict"] == "no_evidence_found"


def test_stock_check_is_unknown_when_no_product_breakdown():
    result = ee.stock_availability_check([_breakdown("store", "Store", "2026-07", [])])
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
    monkeypatch.setattr(ee, "stock_availability_check", lambda breakdowns: calls.append("stock"))
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
    anomaly = {"latest_month": "2026-08"}
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
    anomaly = {"latest_month": "2026-08"}
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
