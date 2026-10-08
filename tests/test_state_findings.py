"""Tests for state_findings._stuck_stock_count_finding -- the finding built
from stock_counts.get_stuck_stock_counts (see test_stock_counts.py for that
function's own tests). Pure unit tests; get_stuck_stock_counts and the
snapshot-staleness check are monkeypatched.
"""

from mcp_server.tools import state_findings as sf


def _session(session_id, outlet, unscanned_value_rs, total_lines=100, scanned_lines=10, days_stuck=2.0, name=None, responsible="Someone", notes=None):
    return {
        "id": session_id,
        "name": name or f"SC/{session_id}",
        "state": "review",
        "review_started_at": None,
        "notes": notes,
        "outlet": outlet,
        "started_at": "2026-08-11T08:15:01",
        "responsible": responsible,
        "days_stuck": days_stuck,
        "total_lines": total_lines,
        "scanned_lines": scanned_lines,
        "unscanned_lines": total_lines - scanned_lines,
        "unscanned_value_rs": unscanned_value_rs,
        "last_scan_at": "2026-08-11T10:04:24" if scanned_lines else None,
        "has_scan_activity": scanned_lines > 0,
    }


def _scan_result(sessions, total_value_at_risk_rs, as_of="2026-09-17T00:00:00", coverage=None, top_products=None):
    # valuation_coverage is now a required key on the real get_stuck_stock_
    # counts() return (Final Stock-Count Financial Disclosure item 1) -- a
    # placeholder default here since most tests in this file aren't
    # exercising coverage math itself (see test_stock_counts.py for that).
    return {
        "as_of": as_of,
        "sessions": sessions,
        "total_value_at_risk_rs": total_value_at_risk_rs,
        "valuation_coverage": coverage or {
            "total_unscanned_lines": 0, "source_cost_lines": 0, "catalog_estimate_lines": 0, "no_cost_data_lines": 0,
        },
        "top_products": top_products or [],
    }


def _stub_snapshot_evidence(monkeypatch):
    # This finding's own logic (priority tiers, recommendations, breakdown)
    # is what's under test here, not evidence_engine's own concentration
    # math (already covered by test_evidence_engine.py) -- stub it to a
    # fixed, valid shape so this test focuses on _stuck_stock_count_finding.
    monkeypatch.setattr(
        sf.evidence_engine, "build_snapshot_evidence",
        lambda breakdowns, as_of: {
            "checks": [{"source": "store_concentration", "verdict": "confirmed", "segments": ["x"]}],
            "summary": {"confirmed": ["store_concentration"], "possible_contributor": [], "no_evidence_found": [], "unknown": []},
            "data_quality": {"source": "data_quality", "verdict": "sufficient"},
        },
    )


def test_no_stuck_sessions_returns_none(monkeypatch):
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result([], 0.0))
    assert sf._stuck_stock_count_finding() is None


def test_single_low_value_session_is_medium_priority(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    sessions = [_session(1, "Celeste Daily - Attidiya", 5_000)]
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, 5_000))
    finding = sf._stuck_stock_count_finding()
    assert finding is not None
    assert finding["priority"] == "medium"
    assert finding["category"] == "critical"
    assert finding["metric_id"] == "stuck_stock_count"
    assert finding["domain"] == "inventory"
    assert len(finding["recommendations"]) == 1  # no "N more stuck" line for a single session


def test_high_and_critical_value_thresholds(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    for value, expected in [(19_999, "medium"), (20_000, "high"), (99_999, "high"), (100_000, "critical")]:
        sessions = [_session(1, "Celeste Daily Colombo 6", value)]
        monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda sessions=sessions, value=value: _scan_result(sessions, value))
        finding = sf._stuck_stock_count_finding()
        assert finding["priority"] == expected, f"value={value} expected={expected} got={finding['priority']}"


def test_multiple_sessions_add_a_recurring_process_gap_recommendation(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    sessions = [
        _session(786, "Celeste Daily Colombo 6", 150_000, name="SC/2026/00783", responsible="Ashan Kannangara", days_stuck=37.0, total_lines=7724, scanned_lines=30),
        _session(287, "Celeste Daily Colombo 6", 1_200, name="SC/2026/00287"),
    ]
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, 151_200))
    finding = sf._stuck_stock_count_finding()
    assert finding["confirm"]["current"] == 2
    assert len(finding["recommendations"]) == 2
    assert "1 more stock count" in finding["recommendations"][1]["action"]
    # the named, actionable recommendation targets the WORST (highest value) session, not just the first one found.
    assert "SC/2026/00783" in finding["recommendations"][0]["action"]
    assert "Ashan Kannangara" in finding["recommendations"][0]["action"]
    assert "37 day" in finding["recommendations"][0]["action"]


def test_stuck_stock_count_finding_adds_a_product_breakdown_when_available(monkeypatch):
    # Previously the "What is driving it" section had nothing to show for
    # this finding (get_stuck_stock_counts only returned per-session stats),
    # so InventoryCaseView.jsx rendered a dead-end "product list is not
    # stored on the case" message. Product identity now rides along on the
    # same query for free -- this is the fix.
    _stub_snapshot_evidence(monkeypatch)
    sessions = [_session(1, "Celeste Daily Colombo 6", 10_000)]
    top_products = [{"name": "Carrot", "sku": "000083", "lines": 3, "qty": 50.0, "value_rs": 7_500.0}]
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, 10_000, top_products=top_products))

    finding = sf._stuck_stock_count_finding()
    product_breakdown = next(b for b in finding["breakdowns"] if b["dimension"] == "product")
    assert product_breakdown["top_contributors"] == [{"name": "Carrot", "current": 7_500.0, "delta": 7_500.0}]
    assert product_breakdown["total"] == 7_500.0


def test_stuck_stock_count_finding_has_no_product_breakdown_when_none_available(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    sessions = [_session(1, "Celeste Daily Colombo 6", 10_000)]
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, 10_000))

    finding = sf._stuck_stock_count_finding()
    assert not any(b["dimension"] == "product" for b in finding["breakdowns"])


def test_leading_recommendation_does_not_prescribe_a_specific_disposition(monkeypatch):
    # Incident Investigation, Phase 6: this app has no basis to recommend
    # WHICH disposition (complete/void/recount) is correct -- that depends
    # on physical reality it can't observe. The recommendation must point at
    # evidence review + an authorized decision, never prescribe a fix, and
    # must be honest that this real session (SC/2026/00428) has zero
    # recorded scan activity rather than implying some activity happened.
    _stub_snapshot_evidence(monkeypatch)
    sessions = [_session(1, "Celeste Daily Ethul Kotte", 279_533.64, name="SC/2026/00428", responsible="Dilanka Ranasinghe", days_stuck=103.0, total_lines=306, scanned_lines=0)]
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, 279_533.64, as_of="2026-09-22T00:00:00"))
    finding = sf._stuck_stock_count_finding()
    leading = finding["recommendations"][0]
    combined = leading["action"] + " " + leading["expected_benefit"]
    assert "complete it" not in combined.lower()
    assert "void it" not in combined.lower()
    assert "solves" not in combined.lower()
    assert "authorized disposition decision" in leading["action"]
    assert "no counting activity has been recorded" in leading["action"]
    assert "Dilanka Ranasinghe" in leading["action"]


def test_leading_recommendation_names_notes_and_activity_when_present(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    sessions = [_session(
        1, "Celeste Daily Colombo 6", 10_000, name="SC/2026/00546", responsible="Administrator",
        scanned_lines=5, notes="Auto-created for negative stock correction",
    )]
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, 10_000, as_of="2026-09-22T00:00:00"))
    finding = sf._stuck_stock_count_finding()
    leading = finding["recommendations"][0]
    assert "Auto-created for negative stock correction" in leading["action"]
    assert "last scan activity" in leading["action"]


def test_breakdown_aggregates_value_by_outlet_across_sessions(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    sessions = [
        _session(1, "Celeste Daily Colombo 6", 10_000),
        _session(2, "Celeste Daily Colombo 6", 5_000),
        _session(3, "Celeste Daily - Attidiya", 2_000),
    ]
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, 17_000))
    finding = sf._stuck_stock_count_finding()
    by_outlet = {c["name"]: c["current"] for c in finding["breakdowns"][0]["top_contributors"]}
    assert by_outlet["Celeste Daily Colombo 6"] == 15_000
    assert by_outlet["Celeste Daily - Attidiya"] == 2_000
    assert finding["breakdowns"][0]["total"] == 17_000


def test_aggregate_impact_breakdown_and_session_sum_all_reconcile_exactly(monkeypatch):
    """Final Integrity Check item 1: the case-level Rs figure (impact.
    financial_impact_rs), the outlet-concentration breakdown's own total,
    and a fresh re-sum of the individual sessions' own unscanned_value_rs
    must all agree to the cent -- verified live against the real 8-session
    case (Rs 290,626.55 three ways) and locked in here with deliberately
    rounding-prone float values so a future change can't silently drift
    one of the three away from the other two."""
    _stub_snapshot_evidence(monkeypatch)
    sessions = [
        _session(1, "Celeste Daily Colombo 6", 10_000.33),
        _session(2, "Celeste Daily Colombo 6", 5_000.34),
        _session(3, "Celeste Daily - Attidiya", 2_000.33),
    ]
    total = round(sum(s["unscanned_value_rs"] for s in sessions), 2)
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, total))
    finding = sf._stuck_stock_count_finding()

    contributor_sum = round(sum(c["current"] for c in finding["breakdowns"][0]["top_contributors"]), 2)
    assert finding["impact"]["financial_impact_rs"] == total
    assert finding["breakdowns"][0]["total"] == total
    assert contributor_sum == total


def test_terminology_is_consistent_and_never_overclaims_or_downplays_coverage(monkeypatch):
    """Final Stock-Count Financial Disclosure, item 2: one consistent label
    ("estimated value of unscanned...") across title/breakdown label/impact
    detail -- never "phantom variance," never implying confirmed loss or a
    fully valued total. Item 1: the excluded-line count and "unknown" must
    appear beside the figure, and the word "small" must never characterize
    it -- there's no basis to assume that."""
    _stub_snapshot_evidence(monkeypatch)
    sessions = [_session(1, "Celeste Daily Colombo 6", 1_000)]
    coverage = {"total_unscanned_lines": 90, "source_cost_lines": 60, "catalog_estimate_lines": 20, "no_cost_data_lines": 10}
    monkeypatch.setattr(sf.stock_counts, "get_stuck_stock_counts", lambda: _scan_result(sessions, 1_000, coverage=coverage))
    finding = sf._stuck_stock_count_finding()

    for text in (finding["title"], finding["breakdowns"][0]["label"], finding["impact"]["detail"]):
        assert "phantom" not in text.lower()
        assert "confirmed" not in text.lower()
        assert "accounting adjustment" not in text.lower()
        assert "small" not in text.lower()
    assert "estimated value of unscanned" in finding["title"].lower()
    assert finding["impact"]["valuation_coverage"] == coverage
    assert "10" in finding["impact"]["detail"]  # no_cost_data_lines named explicitly
    assert "unknown" in finding["impact"]["detail"].lower()


# ---------------------------------------------------------------------------
# build_completed_stock_count_variance_finding / _completed_stock_count_
# variance_finding -- Completed Stock-Count Variance & Shrinkage
# Investigation. Distinct from _stuck_stock_count_finding above: this is
# about sessions that actually FINISHED, with real variance, not a phantom
# unscanned-lines estimate.

def _completed_variance_result(session_count, gross, net, sessions=None, store_breakdown=None, product_type_breakdown=None):
    return {
        "period": "Last 7 days (2026-09-09 to 2026-09-15)",
        "start_date": "2026-09-09", "end_date": "2026-09-15",
        "session_count": session_count,
        "sessions": sessions or [],
        "net_variance_value_rs": net,
        "gross_abs_variance_value_rs": gross,
        "breakdowns": [
            {"dimension": "store", "label": "x", "mode": "contribution", "baseline_month": None,
             "total": gross, "top_contributors": store_breakdown or []},
            {"dimension": "product_type", "label": "x", "mode": "contribution", "baseline_month": None,
             "total": gross, "top_contributors": product_type_breakdown or []},
        ],
    }


def _stub_shrinkage_check(monkeypatch, verdict="no_evidence_found"):
    monkeypatch.setattr(
        sf.evidence_engine, "shrinkage_loss_damage_check",
        lambda breakdowns, start_date, end_date: {"source": "shrinkage_loss_damage", "verdict": verdict, "detail": "stub"},
    )


def test_completed_variance_finding_returns_none_below_threshold(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    _stub_shrinkage_check(monkeypatch)
    session = {"id": 1, "name": "SC/X", "outlet": "Celeste Daily Colombo 6", "completed_at": "2026-09-10T00:00:00",
               "lines": 10, "net_variance_value_rs": 5_000, "gross_abs_variance_value_rs": 5_000}
    result = _completed_variance_result(1, 5_000, 5_000, sessions=[session])
    monkeypatch.setattr(sf.stock_counts, "get_completed_stock_count_variance", lambda period: result)
    assert sf._completed_stock_count_variance_finding() is None  # below COMPLETED_VARIANCE_HIGH_SESSION_RS


def test_completed_variance_finding_returns_none_with_no_sessions(monkeypatch):
    monkeypatch.setattr(sf.stock_counts, "get_completed_stock_count_variance", lambda period: _completed_variance_result(0, 0, 0))
    assert sf._completed_stock_count_variance_finding() is None


def test_completed_variance_finding_fires_above_threshold_with_real_priority_tiers(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    _stub_shrinkage_check(monkeypatch, verdict="possible_contributor")
    session = {"id": 866, "name": "SC/2026/00863", "outlet": "Celeste Daily Colombo 6",
               "completed_at": "2026-09-08T03:56:20", "lines": 7735,
               "net_variance_value_rs": 2_084_739.66, "gross_abs_variance_value_rs": 7_098_828.12}
    result = _completed_variance_result(
        1, 7_098_828.12, 2_084_739.66, sessions=[session],
        store_breakdown=[{"name": "Celeste Daily Colombo 6", "current": 7_098_828.12, "delta": 7_098_828.12}],
        product_type_breakdown=[{"name": "INVENTORY", "current": 7_098_828.12, "delta": 7_098_828.12}],
    )
    monkeypatch.setattr(sf.stock_counts, "get_completed_stock_count_variance", lambda period: result)

    finding = sf._completed_stock_count_variance_finding()
    assert finding is not None
    assert finding["priority"] == "critical"  # clears COMPLETED_VARIANCE_CRITICAL_SESSION_RS
    assert finding["metric_id"] == "completed_stock_count_variance"
    assert finding["domain"] == "inventory"
    assert "SC/2026/00863" in finding["recommendations"][0]["action"]
    assert any(c["source"] == "shrinkage_loss_damage" for c in finding["root_cause"]["checks"])
    assert finding["root_cause"]["summary"]["possible_contributor"] == ["shrinkage_loss_damage"]
    assert finding["sessions"] == [session]


# ---------------------------------------------------------------------------
# _damage_writeoff_finding -- confirmed damage write-offs (Odoo's
# damaged_order document), always-fires posture (see DAMAGE_WRITEOFF_*
# constants' docstring for why -- the condition is chronic, not anomalous).

def _damage_result(order_count, total_value, line_count=None, by_outlet=None, top_products=None, as_of="2026-10-08"):
    return {
        "as_of": as_of, "window_days": 14, "start_date": "2026-09-25", "end_date": as_of,
        "order_count": order_count, "line_count": line_count if line_count is not None else order_count,
        "total_qty": total_value / 10 if total_value else 0, "total_value_rs": total_value,
        "valuation_coverage": {"total_lines": order_count, "catalog_estimate_lines": order_count, "no_cost_data_lines": 0},
        "by_outlet": by_outlet or [],
        "top_products": top_products or [],
    }


def test_damage_writeoff_finding_returns_none_with_no_lines(monkeypatch):
    monkeypatch.setattr(sf.loss_and_damage, "get_damage_writeoff", lambda window_days: _damage_result(0, 0, line_count=0))
    assert sf._damage_writeoff_finding() is None


def test_damage_writeoff_finding_fires_even_at_a_typical_level(monkeypatch):
    # The whole point of this detector: it's always real, so it must fire
    # even well below the "critical" tier -- not gated behind a spike.
    _stub_snapshot_evidence(monkeypatch)
    by_outlet = [{"outlet": "Celeste Daily Ethul Kotte", "orders": 20, "qty": 500.0, "value_rs": 50_000.0}]
    monkeypatch.setattr(sf.loss_and_damage, "get_damage_writeoff", lambda window_days: _damage_result(20, 50_000.0, by_outlet=by_outlet))

    finding = sf._damage_writeoff_finding()
    assert finding is not None
    assert finding["metric_id"] == "damage_writeoff"
    assert finding["domain"] == "inventory"
    assert finding["priority"] == "medium"  # below DAMAGE_WRITEOFF_HIGH_VALUE_RS
    assert finding["impact"]["financial_impact_rs"] == 50_000.0
    assert "Ethul Kotte" in finding["recommendations"][0]["action"]


def test_damage_writeoff_finding_priority_scales_with_real_tiers(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    for value, expected in ((50_000, "medium"), (400_000, "high"), (600_000, "critical")):
        monkeypatch.setattr(sf.loss_and_damage, "get_damage_writeoff", lambda window_days, v=value: _damage_result(5, v))
        assert sf._damage_writeoff_finding()["priority"] == expected


def test_damage_writeoff_finding_discloses_valuation_coverage_gap(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    result = _damage_result(10, 100_000.0)
    result["valuation_coverage"] = {"total_lines": 10, "catalog_estimate_lines": 7, "no_cost_data_lines": 3}
    monkeypatch.setattr(sf.loss_and_damage, "get_damage_writeoff", lambda window_days: result)

    finding = sf._damage_writeoff_finding()
    assert "7 of 10 lines" in finding["impact"]["detail"]
    assert "lower bound" in finding["impact"]["detail"]


def test_build_completed_stock_count_variance_finding_returns_none_for_empty_result():
    assert sf.build_completed_stock_count_variance_finding(_completed_variance_result(0, 0, 0), "x") is None


def test_build_completed_stock_count_variance_finding_uses_the_given_metric_id_and_medium_priority_below_high(monkeypatch):
    """The ad-hoc save path always gets a finding regardless of size (the
    manager already decided it's worth saving) -- 'medium' is the floor,
    never a None like the automatic detector's threshold gate."""
    _stub_snapshot_evidence(monkeypatch)
    _stub_shrinkage_check(monkeypatch)
    session = {"id": 1, "name": "SC/X", "outlet": "Celeste Daily Colombo 6", "completed_at": "2026-09-10T00:00:00",
               "lines": 10, "net_variance_value_rs": 5_000, "gross_abs_variance_value_rs": 5_000}
    result = _completed_variance_result(1, 5_000, 5_000, sessions=[session])
    finding = sf.build_completed_stock_count_variance_finding(result, "completed_stock_count_variance-all-2026-06-01_2026-09-15")
    assert finding["metric_id"] == "completed_stock_count_variance-all-2026-06-01_2026-09-15"
    assert finding["priority"] == "medium"


# ---------------------------------------------------------------------------
# _top_seller_stock_finding -- Phase 2 (Celeste Investigate flagship
# workflow) carries sku/negative_stock/days_of_cover through onto each
# breakdown contributor instead of dropping them, and derives the "of N"
# denominator from the real product list instead of a hardcoded "15".
# get_top_product_stock_coverage is imported by name (not as a module), so
# it's patched directly on `sf`, not via an attribute path.


def _product(name, sku, current_stock, qty_sold_30d=100.0, days_of_cover=None, short=False):
    return {
        "name": name,
        "sku": sku,
        "qty_sold_30d": qty_sold_30d,
        "current_stock": current_stock,
        "projected_demand": None,
        "days_of_cover": days_of_cover,
        "short": short,
        "negative_stock": current_stock < 0,
    }


def _coverage(products, as_of="2026-09-15"):
    return {"as_of_date": as_of, "horizon_days": 10, "low_stock_threshold_days": 3, "products": products}


def test_no_out_of_stock_top_sellers_returns_none(monkeypatch):
    products = [_product("Milk 1L", "000123", 40.0, days_of_cover=5.0)]
    monkeypatch.setattr(sf, "get_top_product_stock_coverage", lambda: _coverage(products))
    assert sf._top_seller_stock_finding() is None


def test_out_of_stock_product_carries_real_sku_and_negative_flag_through(monkeypatch):
    # Regression: sku/negative_stock were computed by get_top_product_stock_
    # coverage but discarded before this function returned, leaving the
    # case-detail investigation panel with no real SKU or stock-status flag.
    products = [
        _product("Set Yogurt 80G - Ambewela - SKU 000638", "000638", -1.0),
        _product("Milk 1L", "000123", 40.0, days_of_cover=5.0),
    ]
    monkeypatch.setattr(sf, "get_top_product_stock_coverage", lambda: _coverage(products))
    finding = sf._top_seller_stock_finding()
    assert finding is not None
    contributors = finding["breakdowns"][0]["top_contributors"]
    assert contributors == [
        {
            "name": "Set Yogurt 80G - Ambewela - SKU 000638",
            "sku": "000638",
            "current": -1.0,
            "delta": -1.0,
            "negative_stock": True,
            "qty_sold_30d": 100.0,
        }
    ]
    assert finding["impact"]["total_top_sellers"] == 2
    assert "top 2 best-selling" in finding["title"]  # real count, not a hardcoded "15"
    assert finding["impact"]["detail"] == (
        "1 of your 2 best-selling products are recorded at zero or negative stock across monitored locations, company-wide."
    )


def test_other_short_products_become_a_second_structured_breakdown(monkeypatch):
    # Previously only mentioned in recommendation prose ("12 more top
    # sellers are under 3 days of cover...") -- now real structured data.
    products = [
        _product("Set Yogurt 80G", "000638", 0.0),
        _product("Onions 1kg", "000200", 30.0, days_of_cover=1.5, short=True),
        _product("Tomatoes 1kg", "000201", 20.0, days_of_cover=2.0, short=True),
        _product("Milk 1L", "000123", 400.0, days_of_cover=8.0),
    ]
    monkeypatch.setattr(sf, "get_top_product_stock_coverage", lambda: _coverage(products))
    finding = sf._top_seller_stock_finding()
    assert len(finding["breakdowns"]) == 2
    other = finding["breakdowns"][1]
    assert other["label"] == "Other top sellers under 3 days of cover"
    assert {c["sku"] for c in other["top_contributors"]} == {"000200", "000201"}
    assert all("days_of_cover" in c for c in other["top_contributors"])


def test_no_other_short_products_means_only_one_breakdown(monkeypatch):
    products = [_product("Set Yogurt 80G", "000638", 0.0)]
    monkeypatch.setattr(sf, "get_top_product_stock_coverage", lambda: _coverage(products))
    finding = sf._top_seller_stock_finding()
    assert len(finding["breakdowns"]) == 1


# ---------------------------------------------------------------------------
# Metric Truthfulness Correction: title/detail no longer assert verified
# unavailability (Task 1), and the negative-stock recommendation no longer
# states a specific cause as fact (Task 2). SKU 000638 (Set Yogurt 80G --
# Ambewela) is the exact real product from the live case this was audited
# against.

def test_title_and_impact_detail_describe_a_measurement_not_a_verified_unavailability_claim(monkeypatch):
    products = [_product("Set Yogurt 80G - Ambewela - SKU 000638", "000638", -1.0)]
    monkeypatch.setattr(sf, "get_top_product_stock_coverage", lambda: _coverage(products))
    finding = sf._top_seller_stock_finding()
    for text in (finding["title"], finding["impact"]["detail"]):
        assert "completely out of stock" not in text
        assert "recorded" in text and ("zero or negative" in text or "zero" in text)


def test_sku_000638_negative_stock_recommendation_does_not_state_the_cause_as_fact(monkeypatch):
    products = [_product("Set Yogurt 80G - Ambewela - SKU 000638", "000638", -1.0)]
    monkeypatch.setattr(sf, "get_top_product_stock_coverage", lambda: _coverage(products))
    finding = sf._top_seller_stock_finding()
    negative_rec = next(r for r in finding["recommendations"] if r["action"].startswith("Reconcile"))
    combined = negative_rec["action"] + " " + negative_rec["expected_benefit"]
    # Must not assert a specific cause as settled fact.
    assert "sold past what's recorded on hand" not in combined
    assert "sold beyond" not in combined
    # Must frame it as requiring investigation/reconciliation instead.
    assert "requires investigation" in negative_rec["action"] or "requires investigation" in combined
    assert "reconcil" in combined.lower()


def test_main_restock_recommendation_does_not_claim_confirmed_unavailability(monkeypatch):
    products = [_product("Set Yogurt 80G - Ambewela - SKU 000638", "000638", -1.0)]
    monkeypatch.setattr(sf, "get_top_product_stock_coverage", lambda: _coverage(products))
    finding = sf._top_seller_stock_finding()
    restock_rec = finding["recommendations"][0]
    assert "completely out" not in restock_rec["action"]
    assert "zero or negative stock" in restock_rec["action"]


# ---------------------------------------------------------------------------
# Business Data Controls, Wave 1 Step 1 -- _negative_stock_control_finding /
# _cost_coverage_control_finding. Both source their raw data from
# backend.app.services.data_controls (monkeypatched here), never query
# reporting directly -- that's what test_data_controls.py already covers.

def _negative_stock_result(count, total_value, products=None, by_outlet=None):
    return {
        "as_of_date": "2026-09-22", "count": count, "total_value_rs": total_value,
        "products": products or [], "by_outlet": by_outlet or [],
    }


def _cost_coverage_result(total, no_cost, has_direct=0, fallback=0, coverage_pct=None, uncosted=None):
    return {
        "as_of_date": "2026-09-22", "total_products": total, "has_direct_cost": has_direct,
        "catalog_fallback_only": fallback, "no_cost_at_all": no_cost, "coverage_pct": coverage_pct,
        "uncosted_products": uncosted or [],
    }


def test_negative_stock_finding_returns_none_when_nothing_negative(monkeypatch):
    monkeypatch.setattr(sf.data_controls, "get_negative_stock", lambda: _negative_stock_result(0, 0.0))
    assert sf._negative_stock_control_finding() is None


def test_negative_stock_finding_fires_with_real_priority_tiers_and_honest_wording(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    products = [{"name": "Canned Fish Mackeral", "sku": "005689", "qty": -7.0, "value_rs": 3915.31}] + [
        {"name": f"P{i}", "sku": str(i), "qty": -1.0, "value_rs": 100.0} for i in range(53)
    ]
    monkeypatch.setattr(sf.data_controls, "get_negative_stock", lambda: _negative_stock_result(54, 26_524.95, products))
    finding = sf._negative_stock_control_finding()
    assert finding is not None
    assert finding["priority"] == "high"  # 54 >= NEGATIVE_STOCK_HIGH_COUNT (20), < CRITICAL_COUNT (100)
    assert finding["domain"] == "controls"
    assert finding["metric_id"] == "negative_stock_control"
    # Must never claim confirmed loss/theft/shrinkage -- "not confirmed
    # loss" is the one permitted use of "loss", as an explicit negation.
    assert "theft" not in finding["title"].lower() and "theft" not in finding["impact"]["detail"].lower()
    assert "shrinkage" not in finding["title"].lower() and "shrinkage" not in finding["impact"]["detail"].lower()
    assert "not confirmed loss" in finding["impact"]["detail"]
    assert "data-integrity" in finding["impact"]["detail"]
    assert finding["products"] == products


def test_negative_stock_finding_carries_outlet_breakdown_as_a_second_evidence_breakdown(monkeypatch):
    """Frontend Intelligence Integration: by_outlet must reach the finding
    dict (it's what gets persisted as the case snapshot the Controls panel
    reads from) both as its own detail field and as a second breakdown fed
    into evidence_engine, alongside the existing product breakdown."""
    products = [{"name": "A", "sku": "A1", "qty": -1.0, "value_rs": 100.0}]
    by_outlet = [{"outlet": "Ethul Kotte", "product_count": 28}, {"outlet": "Outlet B", "product_count": 14}]
    monkeypatch.setattr(sf.data_controls, "get_negative_stock", lambda: _negative_stock_result(1, 100.0, products, by_outlet))
    finding = sf._negative_stock_control_finding()
    assert finding["by_outlet"] == by_outlet
    dims = [b["dimension"] for b in finding["breakdowns"]]
    assert dims == ["product", "store"]
    store_breakdown = finding["breakdowns"][1]
    assert store_breakdown["total"] == 42  # 28 + 14
    assert store_breakdown["top_contributors"][0] == {"name": "Ethul Kotte", "current": 28, "delta": 28}


def test_negative_stock_finding_has_one_breakdown_when_no_outlet_data(monkeypatch):
    products = [{"name": "A", "sku": "A1", "qty": -1.0, "value_rs": 100.0}]
    monkeypatch.setattr(sf.data_controls, "get_negative_stock", lambda: _negative_stock_result(1, 100.0, products))
    finding = sf._negative_stock_control_finding()
    assert finding["by_outlet"] == []
    assert [b["dimension"] for b in finding["breakdowns"]] == ["product"]


def test_negative_stock_finding_priority_scales_with_count(monkeypatch):
    _stub_snapshot_evidence(monkeypatch)
    for count, expected in [(5, "medium"), (20, "high"), (99, "high"), (100, "critical")]:
        products = [{"name": f"P{i}", "sku": str(i), "qty": -1.0, "value_rs": 10.0} for i in range(count)]
        monkeypatch.setattr(sf.data_controls, "get_negative_stock", lambda products=products, count=count: _negative_stock_result(count, count * 10.0, products))
        finding = sf._negative_stock_control_finding()
        assert finding["priority"] == expected, f"count={count} expected={expected} got={finding['priority']}"


def test_cost_coverage_finding_returns_none_when_fully_covered(monkeypatch):
    monkeypatch.setattr(sf.data_controls, "get_cost_coverage", lambda: _cost_coverage_result(100, 0, has_direct=100, coverage_pct=100.0))
    assert sf._cost_coverage_control_finding() is None


def test_cost_coverage_finding_never_implies_other_products_are_wrong(monkeypatch):
    """Final Stock-Count Financial Disclosure discipline, applied here too:
    a small residual gap must never read as a widespread valuation
    problem. Real data: 7 of 7,218 (99.9%) -- the wording must say so."""
    _stub_snapshot_evidence(monkeypatch)
    uncosted = [{"name": "Naturally Imperfect Banana", "sku": "003193"}]
    monkeypatch.setattr(
        sf.data_controls, "get_cost_coverage",
        lambda: _cost_coverage_result(7218, 7, has_direct=7065, fallback=146, coverage_pct=99.9, uncosted=uncosted),
    )
    finding = sf._cost_coverage_control_finding()
    assert finding is not None
    assert finding["domain"] == "controls"
    detail = finding["impact"]["detail"]
    assert "99.9%" in detail
    assert "does not affect" in detail.lower()
    assert "7211" in detail  # the untouched remainder, named explicitly
    assert finding["products"] == uncosted
    # Frontend Intelligence Integration: these must be real fields, not
    # only baked into impact.detail's prose -- a panel shouldn't have to
    # regex-parse a sentence to get the coverage percentage.
    assert finding["total_products"] == 7218
    assert finding["coverage_pct"] == 99.9


def test_build_controls_findings_returns_only_firing_findings(monkeypatch):
    monkeypatch.setattr(sf.data_controls, "get_negative_stock", lambda: _negative_stock_result(0, 0.0))
    monkeypatch.setattr(sf.data_controls, "get_cost_coverage", lambda: _cost_coverage_result(100, 0, has_direct=100, coverage_pct=100.0))
    assert sf.build_controls_findings() == []
