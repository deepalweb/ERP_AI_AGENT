"""Lifecycle regressions through the case/action/detection service boundaries."""
from datetime import date

import pytest

from backend.app.db import appdb
from backend.app.services import (
    action_outcomes,
    case_engine,
    finding_actions,
    state_tracking,
    weekly_streaks,
)
from mcp_server.tools import intelligence, measurement


def test_manual_case_absence_is_not_recovery(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", lambda: date(2026, 9, 1))
    case_id = case_engine.save_ad_hoc_case({
        "id": "manual-count", "metric_id": "completed_stock_count_variance-all-custom",
        "domain": "inventory", "category": "critical", "confirm": {"current": 100},
    })
    for day in (2, 3):
        monkeypatch.setattr(case_engine, "_business_today", lambda day=day: date(2026, 9, day))
        case_engine.sync_cases([], [], [], data_fresh=True)
    case = case_engine.get_case(case_id)
    assert case["status"] == "open"
    assert case["verification_progress"] == 0


def test_tracking_requires_explicit_healthy_coverage(appdb_reset):
    state_tracking.record_new_findings([{
        "id": "stock-today", "metric_id": "stock_availability", "category": "critical",
        "confirm": {"current": 20},
    }])
    weekly_streaks.record_streaks([{
        "metric_id": "cancellation_rate", "category": "critical", "as_of_date": "2026-09-28",
        "confirm": {"current": 20},
    }])
    state_tracking.check_outcomes([])
    weekly_streaks.record_streaks([])
    assert state_tracking.get_tracking_history()[0]["status"] == "open"
    assert "cancellation_rate" in weekly_streaks.get_active_streaks()
    state_tracking.check_outcomes([], measurements=[measurement.observation(
        "stock_availability", "snapshot", "measured_healthy")])
    weekly_streaks.record_streaks([], measurements=[measurement.observation(
        "cancellation_rate", "weekly", "measured_healthy", value=3)])
    assert state_tracking.get_tracking_history()[0]["status"] == "resolved"
    assert not weekly_streaks.get_active_streaks()


def test_target_edit_preserves_old_measurement_but_starts_new_version(appdb_reset, monkeypatch):
    finding_actions.set_action("action", True, "completed", "Promotion",
        target_metric_id="revenue", target_value=100, target_comparison=">=")
    appdb.run_query("UPDATE finding_actions SET completed_at='2026-07-01' WHERE finding_id='action'")
    monkeypatch.setattr(intelligence, "get_trend", lambda _: [
        {"month": "2026-08", "value": 150, "is_partial": False},
    ])
    action_outcomes.check_outcomes()
    finding_actions.set_action("action", True, "completed", "Promotion",
        target_metric_id="revenue", target_value=200, target_comparison=">=")
    action = finding_actions.get_actions_for(["action"])["action"]
    assert action["outcome_verdict"] is None
    assert action["target_version"] == 2
    history = finding_actions.get_target_history("action")
    assert history[0]["target"]["target_value"] == 100
    assert history[0]["outcomes"][-1]["outcome_verdict"] == "met"
    assert history[1]["target"]["target_value"] == 200


def test_foreign_action_cannot_become_case_objective(appdb_reset):
    case_id = case_engine.save_ad_hoc_case({
        "id": "manual", "metric_id": "manual", "category": "opportunity", "confirm": {"current": 10},
    })
    finding_actions.set_action("foreign", True, "completed", "Promotion",
        target_metric_id="revenue", target_value=100, target_comparison=">=")
    with pytest.raises(ValueError, match="belong"):
        case_engine.designate_case_objective_from_action(case_id, "foreign")


def test_zero_baseline_requires_material_change_and_exposure():
    trend = [{"month": str(i), "value": value, "denominator": 100, "is_partial": False}
             for i, value in enumerate([0, 0, 10])]
    metric = intelligence.METRICS["cancellation_rate"]
    assert intelligence.detect_anomaly(trend, metric.direction, metric=metric)["category"] == "critical"
    trend[-1]["denominator"] = 2
    assert intelligence.detect_anomaly(trend, metric.direction, metric=metric)["category"] == "normal"


def test_recommendations_reject_malformed_items_individually(monkeypatch):
    import json
    from types import SimpleNamespace

    from mcp_server.tools import investigation
    good = {"action": "Check cancellations", "owner": "Operations", "priority": "high", "expected_benefit": "Reduce cancellations"}
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps([None, good, {**good, "priority": "urgent"}])) )])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response)))
    monkeypatch.setattr(investigation, "_get_client", lambda: client)
    result = investigation._generate_recommendations({}, {}, [], None)
    assert result == [good]
    assert result.generation_status == "generated"
    assert result.rejected_count == 2


@pytest.mark.parametrize("status,freshness,coverage", [
    ("not_remeasured", "fresh", "complete"),
    ("measurement_unavailable", "fresh", "complete"),
    ("measurement_not_applicable", "fresh", "complete"),
    ("measured_healthy", "stale", "complete"),
    ("measured_healthy", "unknown", "complete"),
    ("measured_healthy", "fresh", "incomplete"),
])
def test_only_complete_fresh_healthy_measurement_advances(appdb_reset, monkeypatch, status, freshness, coverage):
    case_id = case_engine.save_ad_hoc_case({"id": "scoped", "metric_id": "scoped", "category": "critical", "confirm": {"current": 1}})
    for day in (1, 2):
        monkeypatch.setattr(case_engine, "_business_today", lambda day=day: date(2026, 10, day))
        case_engine.sync_cases(measurements=[measurement.observation(
            "scoped", "snapshot", status, freshness=freshness, coverage=coverage)])
    assert case_engine.get_case(case_id)["verification_progress"] == 0
    case_engine.sync_cases(measurements=[measurement.observation("scoped", "snapshot", "measured_healthy")])
    assert case_engine.get_case(case_id)["verification_progress"] == 1
    # Same-day successful remeasurement cannot provide a second clean unit.
    case_engine.sync_cases(measurements=[measurement.observation("scoped", "snapshot", "measured_healthy")])
    assert case_engine.get_case(case_id)["status"] == "verification_pending"


def test_reopening_action_keeps_history_and_resets_completion(appdb_reset):
    finding_actions.set_action("reopen", True, "completed", "Work",
        target_metric_id="revenue", target_value=100, target_comparison=">=")
    before = finding_actions.get_actions_for(["reopen"])["reopen"]
    finding_actions.set_action("reopen", True, "in_progress", "More work")
    reopened = finding_actions.get_actions_for(["reopen"])["reopen"]
    assert reopened["completed_at"] is None
    assert reopened["target_version"] == 2
    assert finding_actions.get_target_history("reopen")[0]["completed_at"] == before["completed_at"]
    finding_actions.set_action("reopen", True, "completed", "More work")
    completed = finding_actions.get_actions_for(["reopen"])["reopen"]
    assert completed["completed_at"] >= before["completed_at"]
    assert completed["target_version"] == 2


def test_unchanged_target_preserves_version_and_measured_outcome(appdb_reset, monkeypatch):
    kwargs = {"target_metric_id": "revenue", "target_value": 100, "target_comparison": ">="}
    finding_actions.set_action("stable", True, "completed", "Work", **kwargs)
    appdb.run_query("UPDATE finding_actions SET completed_at='2026-07-01' WHERE finding_id='stable'")
    monkeypatch.setattr(intelligence, "get_trend", lambda _: [{"month": "2026-08", "value": 150, "is_partial": False}])
    action_outcomes.check_outcomes()
    finding_actions.set_action("stable", True, "completed", "Clarified work", **kwargs)
    result = finding_actions.get_actions_for(["stable"])["stable"]
    assert result["target_version"] == result["outcome_target_version"] == 1
    assert result["outcome_verdict"] == "met"


def test_recommendations_see_completed_cross_metric_evidence(monkeypatch):
    from mcp_server.tools import investigation
    monkeypatch.setattr(intelligence, "METRICS", {"metric": object()})
    def build(metric_id, *, enrich=True):
        assert enrich is False
        return {"metric_id": metric_id, "metric": {}, "category": "critical", "status": "completed",
                "confirm": {"period": "2026-08", "current": 20, "baseline": 10, "pct_change": 100},
                "root_cause": {"checks": []}, "breakdowns": [], "impact": None}
    monkeypatch.setattr(investigation, "build_investigation", build)
    monkeypatch.setattr(investigation.evidence_engine, "cross_metric_corroboration_checks",
        lambda findings: findings[0]["root_cause"]["checks"].append({"source": "corroboration", "verdict": "possible_contributor"}))
    seen = []
    def recommend(metric, anomaly, breakdowns, impact, evidence):
        seen.extend(evidence["checks"])
        return []
    monkeypatch.setattr(investigation, "_generate_recommendations", recommend)
    investigation.scan_all()
    assert seen == [{"source": "corroboration", "verdict": "possible_contributor"}]


def test_inventory_sales_timestamp_cannot_prove_refresh():
    def detector():
        measurement.source_as_of(date(2026, 9, 28), verifies_source=False)
    batch = measurement.run_snapshot("inventory", detector)
    assert batch == []
    assert batch.measurements[0]["measurement_status"] == "measurement_unavailable"
    assert batch.measurements[0]["freshness"] == "unknown"


def test_failed_detector_reports_unknown_not_healthy():
    def fail():
        raise RuntimeError("source offline")
    batch = measurement.run_snapshot("inventory", fail)
    assert batch == []
    assert batch.measurements[0]["measurement_status"] == "measurement_unavailable"


def test_outcome_history_failure_rolls_back_current_verdict(appdb_reset, monkeypatch):
    finding_actions.set_action("atomic", True, "completed", "Work",
        target_metric_id="revenue", target_value=100, target_comparison=">=")
    appdb.run_query("UPDATE finding_actions SET completed_at='2026-07-01' WHERE finding_id='atomic'")
    monkeypatch.setattr(intelligence, "get_trend", lambda _: [{"month": "2026-08", "value": 150, "is_partial": False}])
    def fail(*args):
        raise RuntimeError("history unavailable")
    monkeypatch.setattr(finding_actions, "_append_outcome", fail)
    with pytest.raises(RuntimeError, match="history unavailable"):
        action_outcomes.check_outcomes()
    assert finding_actions.get_actions_for(["atomic"])["atomic"]["outcome_verdict"] is None


def test_migration_adopts_existing_target_without_new_completion(appdb_reset):
    finding_actions.set_action("legacy", True, "completed", "Work",
        target_metric_id="revenue", target_value=100, target_comparison=">=")
    appdb.run_query("UPDATE finding_actions SET target_version=0, completed_at='2026-07-01', outcome_verdict='met', outcome_status='met', outcome_checked_at='2026-09-01' WHERE finding_id='legacy'")
    appdb.run_query("DELETE FROM action_target_versions WHERE finding_id='legacy'")
    finding_actions._SCHEMA_READY = False
    result = finding_actions.get_actions_for(["legacy"])["legacy"]
    assert result["target_version"] == 1
    assert result["completed_at"] == "2026-07-01"
    assert result["outcome_verdict"] == "met"
    assert finding_actions.get_target_history("legacy")[0]["outcomes"][0]["outcome_verdict"] == "met"


def test_monthly_verification_uses_the_period_actually_measured(appdb_reset, monkeypatch):
    finding = {"id": "revenue-july", "metric_id": "revenue", "domain": "financial", "category": "critical", "confirm": {"current": 100}}
    case_engine.sync_cases([finding])
    def must_not_requery(metric):
        raise AssertionError("A later query may describe a different period")
    monkeypatch.setattr(intelligence, "get_trend", must_not_requery)
    for period in ("2026-08", "2026-09"):
        case_engine.sync_cases(measurements=[measurement.observation("revenue", "monthly", "measured_healthy", period=period)])
    assert case_engine.get_case(finding["case_id"])["resolution_verdict"] == "recovered"
