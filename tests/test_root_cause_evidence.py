"""Tests for investigation.py's use of the evidence engine: prompt content
(_generate_recommendations receiving the evidence package), the
now-unconditional evidence attachment (no more per-metric allowlist — see
evidence_engine.py's module docstring), and the insufficient_data crash
regression. Pure unit tests — the OpenAI client and DB-backed lookups
(tracking, stock_alerts, get_dark_outlets) are monkeypatched where used.
"""

from mcp_server.tools import investigation as inv


class _FakeMessage:
    content = "[]"


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    choices = [_FakeChoice()]


class _FakeCompletions:
    def __init__(self, captured):
        self._captured = captured

    def create(self, model, messages, temperature):
        self._captured["prompt"] = messages[0]["content"]
        return _FakeResponse()


class _FakeChat:
    def __init__(self, captured):
        self.completions = _FakeCompletions(captured)


class _FakeClient:
    def __init__(self, captured):
        self.chat = _FakeChat(captured)


# ---------------------------------------------------------------------------
# _generate_recommendations receives the evidence package

def test_recommendations_prompt_includes_memory_instructions(monkeypatch):
    captured = {}
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient(captured))

    evidence = {
        "checks": [],
        "historical_context": {"pattern": "single_period_move", "consecutive_periods": 1},
        "data_quality": {"source": "data_quality", "verdict": "sufficient", "months_available": 6},
        "memory": {
            "prior_period": "2026-07",
            "prior_recommendations": [{"action": "Investigate the spike"}],
            "outcome_verdict": "unchanged",
            "outcome_detail": "Deviation from baseline went from 106.43% to -88.6% by 2026-08.",
        },
    }
    inv._generate_recommendations(
        metric={"id": "revenue", "label": "Revenue", "unit": "currency"},
        anomaly={"latest_month": "2026-09", "category": "critical"},
        breakdowns=[],
        impact=None,
        evidence_pkg=evidence,
    )

    assert "prompt" in captured
    assert "outcome_verdict" in captured["prompt"]
    assert "already failed to work" in captured["prompt"]
    assert "2026-07" in captured["prompt"]


def test_recommendations_prompt_includes_root_cause_evidence(monkeypatch):
    captured = {}
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient(captured))

    evidence = {
        "checks": [
            {"source": "store_concentration", "verdict": "confirmed", "detail": "75% from Store A.", "segments": ["Store A"]},
            {"source": "stock_availability", "verdict": "possible_contributor", "detail": "Overlaps with low stock."},
        ],
        "historical_context": {"pattern": "single_period_move", "consecutive_periods": 1},
        "data_quality": {"source": "data_quality", "verdict": "sufficient", "months_available": 6},
        "memory": None,
    }
    inv._generate_recommendations(
        metric={"id": "revenue", "label": "Revenue", "unit": "currency"},
        anomaly={"latest_month": "2026-08", "category": "critical"},
        breakdowns=[],
        impact=None,
        evidence_pkg=evidence,
    )

    assert "prompt" in captured
    assert "possible_contributor" in captured["prompt"]
    assert "Store A" in captured["prompt"]
    # the instruction to the model on how to treat each verdict must be present,
    # not just the raw evidence -- this is the actual "don't invent causes" guard.
    assert "confirmed" in captured["prompt"]
    assert "never state it as the cause" in captured["prompt"]


def test_recommendations_prompt_includes_historical_context_and_data_quality_instructions(monkeypatch):
    captured = {}
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient(captured))

    evidence = {
        "checks": [],
        "historical_context": {"pattern": "consistent_declining", "consecutive_periods": 3,
                                "direction": "declining", "period_range": "2026-06 to 2026-08"},
        "data_quality": {"source": "data_quality", "verdict": "low_confidence", "months_available": 2},
        "memory": None,
    }
    inv._generate_recommendations(
        metric={"id": "revenue", "label": "Revenue", "unit": "currency"},
        anomaly={"latest_month": "2026-08", "category": "critical"},
        breakdowns=[],
        impact=None,
        evidence_pkg=evidence,
    )

    assert "consecutive_periods" in captured["prompt"]
    assert "low_confidence" in captured["prompt"]
    assert "consistent_declining" in captured["prompt"]


# ---------------------------------------------------------------------------
# Evidence now attaches unconditionally — no more per-metric allowlist. A
# metric whose only dimension is rate-mode (delayed_delivery_rate) used to
# come back with root_cause: None entirely; now it gets a real package with
# an honest, explained "unknown" instead of being silently skipped.

def test_delayed_delivery_rate_now_gets_an_honest_evidence_package_instead_of_none(monkeypatch):
    fake_result = {
        "metric": {"id": "delayed_delivery_rate", "label": "Severely delayed delivery rate", "unit": "percent",
                   "direction": "higher_is_bad", "notes": None},
        "anomaly": {
            "category": "opportunity", "latest_month": "2026-08", "latest_value": 3.0,
            "baseline_mean": 6.0, "pct_change_vs_baseline": -50.0, "z_score": None, "moved_up": False,
        },
        "trend": [
            {"month": "2026-07", "value": 6.0, "is_partial": False},
            {"month": "2026-08", "value": 3.0, "is_partial": False},
        ],
        "breakdowns": [
            {"dimension": "store", "label": "Store", "mode": "rate", "baseline_month": "2026-07",
             "top_contributors": [
                 {"name": "Colombo 6", "current": 3.0, "baseline": 6.0, "delta": -3.0, "current_volume": 500},
             ]},
        ],
        "persistence": {"pattern": "single_period_move", "consecutive_periods": 1},
    }
    monkeypatch.setattr(inv.intelligence, "investigate", lambda metric_id: fake_result)
    monkeypatch.setattr("backend.app.services.tracking.get_most_recent_prior", lambda metric_id, before_period: None)
    monkeypatch.setattr(inv, "_get_client", lambda: _FakeClient({}))

    result = inv.build_investigation("delayed_delivery_rate")

    assert result["status"] == "completed"
    assert result["root_cause"] is not None
    checks = result["root_cause"]["checks"]
    assert any(c["source"] == "store_concentration" and c["verdict"] == "unknown" for c in checks)
    assert "store_concentration" in result["root_cause"]["summary"]["unknown"]


# ---------------------------------------------------------------------------
# insufficient_data handling — detect_anomaly() omits "category" entirely
# when a metric has fewer than 2 complete months (caught live: a real scan
# crashed here for supplier_fill_rate once its source view thinned to 1
# month of data)

def test_build_investigation_handles_insufficient_data_without_crashing(monkeypatch):
    fake_result = {
        "metric": {"id": "supplier_fill_rate", "label": "Supplier on-time fill rate", "unit": "percent"},
        "anomaly": {"is_anomalous": False, "verdict": "insufficient_data", "latest_month": None},
        "trend": [],
        "breakdowns": [],
        "persistence": {"pattern": "insufficient_data", "consecutive_periods": 0},
    }
    monkeypatch.setattr(inv.intelligence, "investigate", lambda metric_id: fake_result)
    result = inv.build_investigation("supplier_fill_rate")
    assert result["status"] == "no_finding"
    assert result["category"] is None


def test_build_trailing_investigation_handles_insufficient_data_without_crashing(monkeypatch):
    fake_result = {
        "metric": {"id": "cancellation_rate", "label": "Order cancellation rate", "unit": "percent"},
        "anomaly": {"is_anomalous": False, "verdict": "insufficient_data", "latest_month": None},
        "trend": [],
        "breakdowns": [],
        "persistence": {"pattern": "insufficient_data", "consecutive_periods": 0},
    }
    monkeypatch.setattr(inv.intelligence, "investigate_trailing", lambda metric_id: fake_result)
    result = inv.build_trailing_investigation("cancellation_rate")
    assert result["status"] == "no_finding"
    assert result["category"] is None
