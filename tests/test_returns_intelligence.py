from types import SimpleNamespace

from mcp_server.tools import returns_intelligence as ri


def _fake_metric(**overrides):
    base = {"label": "Internal return rate", "unit": "percent", "direction": "higher_is_bad",
            "notes": None, "dimensions": []}
    return SimpleNamespace(**{**base, **overrides})


def _patch(monkeypatch, metric, trend, anomaly, persistence=None):
    monkeypatch.setitem(ri.intelligence.METRICS, "return_rate", metric)
    monkeypatch.setattr(ri.intelligence, "get_trend", lambda _m: trend)
    monkeypatch.setattr(ri.intelligence, "detect_anomaly", lambda *_a, **_k: anomaly)
    monkeypatch.setattr(ri.intelligence, "classify_persistence", lambda *_a, **_k: persistence or {})


def test_normal_category_produces_no_finding(monkeypatch):
    _patch(
        monkeypatch, _fake_metric(),
        trend=[{"month": "2026-09", "value": 0.1, "numerator": 50.0, "denominator": 50000.0, "is_partial": False}],
        anomaly={"category": "normal", "latest_month": "2026-09", "latest_value": 0.1,
                 "baseline_mean": 0.1, "pct_change_vs_baseline": 0.0, "moved_up": False},
    )
    assert ri._return_rate_finding() is None


def test_critical_rise_builds_a_high_priority_finding(monkeypatch):
    _patch(
        monkeypatch, _fake_metric(),
        trend=[{"month": "2026-10", "value": 0.59, "numerator": 520.0, "denominator": 88337.0, "is_partial": False}],
        anomaly={"category": "critical", "latest_month": "2026-10", "latest_value": 0.59,
                 "baseline_mean": 0.018, "pct_change_vs_baseline": 3177.8, "moved_up": True},
    )
    finding = ri._return_rate_finding()
    assert finding["metric_id"] == "return_rate"
    assert finding["priority"] == "high"
    assert "risen" in finding["title"]
    assert finding["confirm"]["current"] == 0.59
    assert "520 of 88337 orders in 2026-10 were returned" in finding["root_cause"]["summary"]
