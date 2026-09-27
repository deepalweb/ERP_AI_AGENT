"""Cross-domain investigation v1 (related_signals.py) -- pure logic tests,
no DB/live data needed: intelligence.investigate, evidence_engine.
build_trend_evidence/build_snapshot_evidence, get_stock_alerts, and the
case_id lookup are all monkeypatched so these test the connection/tier
rules in isolation."""

from backend.app.services import related_signals as rs


def _fake_investigate(period="2026-08"):
    def investigate(metric_id):
        return {
            "metric": {"id": metric_id, "label": "Order cancellation rate", "unit": "percent"},
            "trend": [],
            "anomaly": {"latest_month": period, "category": "critical", "moved_up": True},
            "persistence": {},
            "breakdowns": [
                {"dimension": "store", "label": "Store", "mode": "rate",
                 "top_contributors": [{"name": "Celeste Daily Ethul Kotte", "current": 1.97}]},
            ],
        }
    return investigate


def _no_stock_alerts(monkeypatch):
    # Every test exercises the cancellation path only, unless it patches
    # get_stock_alerts itself -- this stub keeps the stock_availability
    # candidate a real, harmless no-op (no DB call) rather than a crash.
    monkeypatch.setattr(rs, "get_stock_alerts", lambda: {"as_of_date": None})


def test_corroborated_when_both_metrics_independently_confirm_the_same_segment(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate())
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]})
    monkeypatch.setattr(rs.appdb, "run_query", lambda *a, **k: [{"case_id": "related-case-1"}])
    _no_stock_alerts(monkeypatch)

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)

    assert len(result) == 1
    assert result[0]["tier"] == "corroborated"
    assert result[0]["scope"] == "Celeste Daily Ethul Kotte"
    assert result[0]["case_id"] == "related-case-1"
    assert result[0]["qualifier"] == "Independently corroborated · cause not established"
    # Never the word "confirmed" in a manager-facing sentence -- that
    # verdict is reserved for same-metric internal evidence.
    assert "confirmed" not in result[0]["headline"].lower()
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
    _no_stock_alerts(monkeypatch)

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)

    assert len(result) == 1
    assert result[0]["tier"] == "point_in_time"
    assert result[0]["qualifier"] == "Related signal observed · cause not established"
    assert "confirmed" not in result[0]["headline"].lower()


def test_no_signal_when_periods_dont_match(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate(period="2026-07"))
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": []})
    _no_stock_alerts(monkeypatch)

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)
    assert result == []


def test_no_signal_when_primary_has_no_confirmed_concentration(monkeypatch):
    _no_stock_alerts(monkeypatch)
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
    _no_stock_alerts(monkeypatch)

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)
    assert result == []


def test_stock_availability_surfaces_as_snapshot_tier_with_both_dates_shown(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", lambda m: {"error": "not used"})
    monkeypatch.setattr(rs, "get_stock_alerts", lambda: {
        "as_of_date": "2026-08-15",
        "segments_by_outlet": [
            {"outlet": "Celeste Daily Ethul Kotte", "out_of_stock": 12},
            {"outlet": "Celeste Daily - Attidiya", "out_of_stock": 40},
        ],
    })
    monkeypatch.setattr(rs.evidence_engine, "build_snapshot_evidence", lambda breakdowns, as_of: {"checks": [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily - Attidiya"]},
    ]})

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)

    assert len(result) == 1
    signal = result[0]
    assert signal["metric_id"] == "stock_availability"
    assert signal["tier"] == "snapshot"
    assert signal["scope"] == "Celeste Daily Ethul Kotte"
    # Both the snapshot's own date AND the primary's month must appear --
    # the whole point is showing the timing mismatch, not hiding it.
    assert "2026-08-15" in signal["headline"]
    assert "2026-08" in signal["headline"]
    assert "cause" in signal["detail"].lower()
    # Ethul Kotte wasn't the most-affected outlet (Attidiya was, 40 vs 12)
    # -- the concentrated-elsewhere note must not claim it was.
    assert "most-affected outlet" not in signal["detail"]


def test_stock_availability_absent_when_primary_outlet_has_no_stockouts(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", lambda m: {"error": "not used"})
    monkeypatch.setattr(rs, "get_stock_alerts", lambda: {
        "as_of_date": "2026-08-15",
        "segments_by_outlet": [{"outlet": "Celeste Daily - Attidiya", "out_of_stock": 40}],
    })
    monkeypatch.setattr(rs.evidence_engine, "build_snapshot_evidence", lambda breakdowns, as_of: {"checks": []})

    primary_checks = [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks)
    assert result == []
