"""Tests for state_findings._negative_stock_control_finding,
_cost_coverage_control_finding, and build_controls_findings --
Business Data Controls, Wave 1 Step 1. Both source their raw data from
backend.app.services.data_controls (monkeypatched here), never query
reporting directly -- that's what test_data_controls.py already covers.
"""

from mcp_server.tools import state_findings as sf


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


def _negative_stock_result(count, total_value, products=None):
    return {"as_of_date": "2026-09-22", "count": count, "total_value_rs": total_value, "products": products or []}


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


def test_build_controls_findings_returns_only_firing_findings(monkeypatch):
    monkeypatch.setattr(sf.data_controls, "get_negative_stock", lambda: _negative_stock_result(0, 0.0))
    monkeypatch.setattr(sf.data_controls, "get_cost_coverage", lambda: _cost_coverage_result(100, 0, has_direct=100, coverage_pct=100.0))
    assert sf.build_controls_findings() == []
