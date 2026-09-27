"""Cross-domain investigation v1 (related_signals.py) -- pure logic tests,
no DB/live data needed: intelligence.investigate, evidence_engine.
build_trend_evidence, and the case_id lookup are all monkeypatched so
these test the connection/tier rules in isolation."""

from backend.app.services import related_signals as rs


def _fake_investigate(period="2026-08"):
    def investigate(metric_id):
        return {
            "metric": {"id": metric_id, "label": "Order cancellation rate", "unit": "percent"},
            "trend": [],
            "anomaly": {"latest_month": period, "category": "critical"},
            "persistence": {},
            "breakdowns": [
                {"dimension": "store", "label": "Store", "mode": "rate",
                 "top_contributors": [{"name": "Celeste Daily Ethul Kotte", "current": 1.97}]},
            ],
        }
    return investigate


def test_corroborated_when_both_metrics_independently_confirm_the_same_segment(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate())
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]})
    monkeypatch.setattr(rs.appdb, "run_query", lambda *a, **k: [{"case_id": "related-case-1"}])

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)

    assert len(result) == 1
    assert result[0]["tier"] == "corroborated"
    assert result[0]["scope"] == "Celeste Daily Ethul Kotte"
    assert result[0]["case_id"] == "related-case-1"
    # Never the word "confirmed" in a cross-domain detail sentence -- that
    # verdict is reserved for same-metric internal evidence.
    assert "confirmed" not in result[0]["detail"].lower()


def test_point_in_time_when_related_metrics_own_concentration_check_cannot_run(monkeypatch):
    # The real, live case this was built against: cancellation_rate's store
    # breakdown is rate-mode, so its own concentration check always comes
    # back "unknown" -- this must still surface as a weaker signal via the
    # breakdown's top contributor, not disappear entirely.
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate())
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "unknown", "detail": "rate mode, not supported"},
    ]})
    monkeypatch.setattr(rs.appdb, "run_query", lambda *a, **k: [{"case_id": "related-case-1"}])

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)

    assert len(result) == 1
    assert result[0]["tier"] == "point_in_time"
    assert "confirmed" not in result[0]["detail"].lower().split("isn't a ")[0]
    assert "isn't a confirmed concentration" in result[0]["detail"]


def test_no_signal_when_periods_dont_match(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate(period="2026-07"))
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": []})

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)
    assert result == []


def test_no_signal_when_primary_has_no_confirmed_concentration():
    primary_checks = [
        {"source": "store_concentration", "verdict": "no_evidence_found"},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)
    assert result == []


def test_no_signal_for_a_metric_with_no_related_candidates_configured():
    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("cancellation_rate", "2026-08", primary_checks)
    assert result == []


def test_no_overlap_when_segments_differ(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate())
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Colombo 6"]},
    ]})

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)
    assert result == []
