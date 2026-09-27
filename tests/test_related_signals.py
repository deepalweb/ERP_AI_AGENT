"""Cross-domain investigation v1 (related_signals.py) -- pure logic tests,
no DB/live data needed: intelligence.investigate, evidence_engine.
build_trend_evidence/build_snapshot_evidence, get_stock_alerts, and the
case_id lookup are all monkeypatched so these test the connection/tier/
direction rules in isolation."""

from backend.app.services import related_signals as rs


def _fake_investigate(period="2026-08", category="opportunity", moved_up=False):
    def investigate(metric_id):
        return {
            "metric": {"id": metric_id, "label": "Order cancellation rate", "unit": "percent"},
            "trend": [],
            "anomaly": {"latest_month": period, "category": category, "moved_up": moved_up},
            "persistence": {},
            "breakdowns": [
                {"dimension": "store", "label": "Store", "mode": "rate",
                 "top_contributors": [{"name": "Celeste Daily Ethul Kotte", "current": 1.97}]},
            ],
        }
    return investigate


def _no_stock_alerts(monkeypatch):
    monkeypatch.setattr(rs, "get_stock_alerts", lambda: {"as_of_date": None})


PRIMARY_CHECKS = [
    {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
]


def test_consistent_movement_when_categories_agree(monkeypatch):
    # Real live case this was built against: revenue up 85% (opportunity),
    # cancellation down 53% (also opportunity -- lower cancellation is
    # good) -- both improving, genuinely consistent, not a counter-signal.
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate(category="opportunity", moved_up=False))
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "unknown"},
    ]})
    monkeypatch.setattr(rs.appdb, "run_query", lambda *a, **k: [{"case_id": "related-case-1"}])
    _no_stock_alerts(monkeypatch)

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="opportunity", pct_change=84.85)

    assert len(result) == 1
    assert result[0]["is_consistent"] is True
    assert "while revenue also" in result[0]["headline"]
    assert result[0]["qualifier"] == "Related signal observed · cause not established"
    assert "Counter-signal" not in result[0]["qualifier"]


def test_counter_signal_when_categories_disagree(monkeypatch):
    # A revenue DECLINE (critical) with cancellation also decreasing
    # (opportunity, i.e. improving) -- these move opposite good/bad
    # directions and must never be presented as supporting the decline.
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate(category="opportunity", moved_up=False))
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "unknown"},
    ]})
    monkeypatch.setattr(rs.appdb, "run_query", lambda *a, **k: [{"case_id": "related-case-1"}])
    _no_stock_alerts(monkeypatch)

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="critical", pct_change=-12.0)

    assert len(result) == 1
    assert result[0]["is_consistent"] is False
    assert "while revenue" in result[0]["headline"]
    assert "while revenue also" not in result[0]["headline"]  # "also" signals agreement -- must not appear here
    assert result[0]["qualifier"] == "Counter-signal observed · does not explain this revenue change"


def test_corroborated_when_both_metrics_independently_confirm_the_same_segment(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate(category="critical", moved_up=True))
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]},
    ]})
    monkeypatch.setattr(rs.appdb, "run_query", lambda *a, **k: [{"case_id": "related-case-1"}])
    _no_stock_alerts(monkeypatch)

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="critical", pct_change=-10.0)

    assert len(result) == 1
    assert result[0]["tier"] == "corroborated"
    assert result[0]["scope"] == "Celeste Daily Ethul Kotte"
    assert result[0]["case_id"] == "related-case-1"
    assert result[0]["is_consistent"] is True
    assert result[0]["qualifier"] == "Independently corroborated · cause not established"
    assert "confirmed" not in result[0]["headline"].lower()
    assert "confirmed" not in result[0]["detail"].lower()


def test_point_in_time_when_related_metrics_own_concentration_check_cannot_run(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate(category="critical", moved_up=True))
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "unknown", "detail": "rate mode, not supported"},
    ]})
    monkeypatch.setattr(rs.appdb, "run_query", lambda *a, **k: [{"case_id": "related-case-1"}])
    _no_stock_alerts(monkeypatch)

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="critical", pct_change=-10.0)

    assert len(result) == 1
    assert result[0]["tier"] == "point_in_time"
    assert result[0]["qualifier"] == "Related signal observed · cause not established"
    assert "confirmed" not in result[0]["headline"].lower()


def test_no_signal_when_periods_dont_match(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate(period="2026-07"))
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": []})
    _no_stock_alerts(monkeypatch)

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="opportunity", pct_change=84.85)
    assert result == []


def test_no_signal_when_primary_has_no_confirmed_concentration(monkeypatch):
    _no_stock_alerts(monkeypatch)
    primary_checks = [{"source": "store_concentration", "verdict": "no_evidence_found"}]
    result = rs.related_signals_for("revenue", "2026-08", primary_checks, category="opportunity", pct_change=84.85)
    assert result == []


def test_no_signal_for_a_metric_with_no_related_candidates_configured():
    result = rs.related_signals_for("cancellation_rate", "2026-08", PRIMARY_CHECKS, category="opportunity", pct_change=1.0)
    assert result == []


def test_no_overlap_when_segments_differ(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", _fake_investigate(category="opportunity"))
    monkeypatch.setattr(rs.evidence_engine, "build_trend_evidence", lambda *a, **k: {"checks": [
        {"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Colombo 6"]},
    ]})
    _no_stock_alerts(monkeypatch)

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="opportunity", pct_change=84.85)
    assert result == []


def test_stock_availability_surfaces_when_snapshot_date_is_inside_the_revenue_month(monkeypatch):
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

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="critical", pct_change=-10.0)

    assert len(result) == 1
    signal = result[0]
    assert signal["metric_id"] == "stock_availability"
    assert signal["tier"] == "snapshot"
    assert signal["scope"] == "Celeste Daily Ethul Kotte"
    assert "2026-08-15" in signal["headline"]
    assert "2026-08" in signal["headline"]
    assert "cause" in signal["detail"].lower()
    assert "most-affected outlet" not in signal["detail"]


def test_stock_availability_absent_when_snapshot_date_is_outside_the_revenue_month(monkeypatch):
    # The real bug caught live: a September 15 snapshot must never be
    # presented as evidence "during" an August revenue period. Strict
    # containment for v1 -- no signal at all, not a weaker "later" tier.
    monkeypatch.setattr(rs.intelligence, "investigate", lambda m: {"error": "not used"})
    monkeypatch.setattr(rs, "get_stock_alerts", lambda: {
        "as_of_date": "2026-09-15",
        "segments_by_outlet": [{"outlet": "Celeste Daily Ethul Kotte", "out_of_stock": 12}],
    })
    monkeypatch.setattr(rs.evidence_engine, "build_snapshot_evidence", lambda breakdowns, as_of: {"checks": []})

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="critical", pct_change=-10.0)
    assert result == []


def test_stock_availability_absent_when_primary_outlet_has_no_stockouts(monkeypatch):
    monkeypatch.setattr(rs.intelligence, "investigate", lambda m: {"error": "not used"})
    monkeypatch.setattr(rs, "get_stock_alerts", lambda: {
        "as_of_date": "2026-08-15",
        "segments_by_outlet": [{"outlet": "Celeste Daily - Attidiya", "out_of_stock": 40}],
    })
    monkeypatch.setattr(rs.evidence_engine, "build_snapshot_evidence", lambda breakdowns, as_of: {"checks": []})

    result = rs.related_signals_for("revenue", "2026-08", PRIMARY_CHECKS, category="critical", pct_change=-10.0)
    assert result == []
