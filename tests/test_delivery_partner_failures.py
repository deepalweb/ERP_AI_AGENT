from types import SimpleNamespace

from mcp_server.tools import delivery_partner_failures as dpf


def _fake_metric(**overrides):
    base = {"label": "Uber order failure rate", "unit": "percent", "direction": "higher_is_bad",
            "notes": None, "dimensions": []}
    return SimpleNamespace(**{**base, **overrides})


def _patch(monkeypatch, metric, trend, anomaly, persistence=None):
    monkeypatch.setitem(dpf.intelligence.METRICS, "uber_failure_rate", metric)
    monkeypatch.setattr(dpf.intelligence, "get_trend", lambda _m: trend)
    monkeypatch.setattr(dpf.intelligence, "detect_anomaly", lambda *_a, **_k: anomaly)
    monkeypatch.setattr(dpf.intelligence, "classify_persistence", lambda *_a, **_k: persistence or {})


def test_normal_category_produces_no_finding(monkeypatch):
    _patch(
        monkeypatch, _fake_metric(),
        trend=[{"month": "2026-09", "value": 2.0, "numerator": 100.0, "denominator": 5000.0, "is_partial": False}],
        anomaly={"category": "normal", "latest_month": "2026-09", "latest_value": 2.0,
                 "baseline_mean": 2.0, "pct_change_vs_baseline": 0.0, "moved_up": False},
    )
    assert dpf._uber_failure_finding() is None


def test_critical_rise_builds_a_high_priority_finding(monkeypatch):
    _patch(
        monkeypatch, _fake_metric(),
        trend=[{"month": "2026-09", "value": 12.5, "numerator": 625.0, "denominator": 5000.0, "is_partial": False}],
        anomaly={"category": "critical", "latest_month": "2026-09", "latest_value": 12.5,
                 "baseline_mean": 2.0, "pct_change_vs_baseline": 525.0, "moved_up": True},
    )
    finding = dpf._uber_failure_finding()
    assert finding["metric_id"] == "uber_failure_rate"
    assert finding["priority"] == "high"
    assert "risen" in finding["title"]
    assert finding["confirm"]["current"] == 12.5
    assert "625 of 5000 Uber orders failed" in finding["root_cause"]["summary"]


def test_opportunity_fall_builds_a_medium_priority_finding(monkeypatch):
    _patch(
        monkeypatch, _fake_metric(),
        trend=[{"month": "2026-09", "value": 1.5, "numerator": 75.0, "denominator": 5000.0, "is_partial": False}],
        anomaly={"category": "opportunity", "latest_month": "2026-09", "latest_value": 1.5,
                 "baseline_mean": 25.0, "pct_change_vs_baseline": -94.0, "moved_up": False},
    )
    finding = dpf._uber_failure_finding()
    assert finding["priority"] == "medium"
    assert "fallen" in finding["title"]
