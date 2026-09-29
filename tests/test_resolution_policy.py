"""Phase 2d: resolution policies (problem recovery vs. opportunity capture),
the action-outcome model, server-side evidence-freshness enforcement, and
the corroboration-relationship tightening. Scenarios A-Q are the exact set
from the Phase 2d review.
"""

import datetime
import json

import pytest

from backend.app.db import appdb
from backend.app.services import action_outcomes, case_engine, finding_actions
from mcp_server.tools import evidence_engine as ee
from tests.coverage_helpers import sync_covered_cases


def _monthly_finding(metric_id, domain, period, category="critical", checks=None, current=100, impact=50.0,
                      title=None):
    baseline = current * 1.2
    return {
        "id": f"{metric_id}-{period}",
        "metric_id": metric_id,
        "domain": domain,
        "category": category,
        "priority": "high",
        "business_impact_score": impact,
        "title": title or f"{metric_id} moved",
        "confirm": {
            "period": period, "current": current, "baseline": baseline,
            "pct_change": (current - baseline) / baseline * 100 if baseline else 0.0,
        },
        "root_cause": {"checks": checks or []},
    }


def _fake_today_sequence(days):
    calls = {"i": 0}

    def _next():
        i = min(calls["i"], len(days) - 1)
        calls["i"] += 1
        return days[i]

    return _next


class _FakeMetric:
    def __init__(self, unit="percent"):
        self.label = "Fake metric"
        self.unit = unit


def _set_trend(monkeypatch, metric_id, rows):
    metrics = dict(getattr(case_engine.intelligence, "METRICS", {}))
    metrics.setdefault(metric_id, _FakeMetric())
    monkeypatch.setattr(case_engine.intelligence, "METRICS", metrics)
    monkeypatch.setattr(case_engine.intelligence, "get_trend", lambda metric: rows)


# ---------------------------------------------------------------------------
# A, B: problem recovery.
# ---------------------------------------------------------------------------

def test_A_delivery_delays_improve_but_stay_open(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1), datetime.date(2026, 9, 2)]))
    f1 = _monthly_finding("delayed_delivery_rate", "delivery", "2026-09", current=15)
    sync_covered_cases([f1], [], [])
    f2 = _monthly_finding("delayed_delivery_rate", "delivery", "2026-09", current=12)  # improved, still flagged
    sync_covered_cases([f2], [], [])
    case = case_engine.get_case(f1["case_id"])
    assert case["status"] == "open"
    assert case["resolution_verdict"] is None


def test_B_delivery_returns_to_normal_for_the_verification_period(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_today_sequence([datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("delayed_delivery_rate", "delivery", "2026-07", category="critical")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    _set_trend(monkeypatch, "delayed_delivery_rate", [{"month": "2026-08", "value": 3, "is_partial": False}])
    sync_covered_cases([], [], [])
    _set_trend(monkeypatch, "delayed_delivery_rate", [{"month": "2026-09", "value": 3, "is_partial": False}])
    sync_covered_cases([], [], [])

    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "recovered"


# ---------------------------------------------------------------------------
# C, N: action completed / worsens / doesn't meet objective.
# ---------------------------------------------------------------------------

def test_C_action_completed_but_performance_worsens(appdb_reset, monkeypatch):
    finding_actions.set_action(
        "revenue-2026-07", True, "completed", "ran a promo",
        target_metric_id="revenue", target_value=12_000_000, target_comparison=">=",
    )
    appdb.run_query(
        "UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
        {"t": "2026-07-15T00:00:00"},
    )
    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 9_000_000, "is_partial": False}])
    action_outcomes.check_outcomes()

    action = finding_actions.get_actions_for(["revenue-2026-07"])["revenue-2026-07"]
    assert action["outcome_verdict"] == "not_met"
    assert action["outcome_value"] == 9_000_000


def test_N_investigation_action_completes_without_achieving_case_objective(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_today_sequence([datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    finding_actions.set_action(
        "revenue-2026-07", True, "completed", "investigated the driver", action_kind="investigation",
        case_id=case_id, target_metric_id="revenue", target_value=12_000_000, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
                     {"t": "2026-07-15T00:00:00"})

    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 9_000_000, "is_partial": False}])
    action_outcomes.check_outcomes()
    sync_covered_cases([], [], [])  # metric absent this cycle
    _set_trend(monkeypatch, "revenue", [{"month": "2026-09", "value": 9_000_000, "is_partial": False}])
    sync_covered_cases([], [], [])

    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "expired"  # not_met -> never "captured"


# ---------------------------------------------------------------------------
# D, E, F: opportunity closure correctness.
# ---------------------------------------------------------------------------

def test_D_problem_resolves_without_any_recorded_intervention(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_today_sequence([datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("gross_margin", "financial", "2026-07", category="critical")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    _set_trend(monkeypatch, "gross_margin", [{"month": "2026-08", "value": 20, "is_partial": False}])
    sync_covered_cases([], [], [])
    _set_trend(monkeypatch, "gross_margin", [{"month": "2026-09", "value": 20, "is_partial": False}])
    sync_covered_cases([], [], [])

    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "recovered"
    assert finding_actions.get_actions_for_case(case_id) == []


def test_E_opportunity_disappears_without_objective_achieved_is_expired(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_today_sequence([datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])
    _set_trend(monkeypatch, "revenue", [{"month": "2026-09", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])

    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "expired"  # never "captured" from absence alone


def test_F_opportunitys_objective_achieved_is_captured(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_today_sequence([datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    finding_actions.set_action(
        "revenue-2026-07", True, "completed", "ran a promo", case_id=case_id,
        target_metric_id="revenue", target_value=8_000_000, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
                     {"t": "2026-07-15T00:00:00"})

    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 9_000_000, "is_partial": False}])
    action_outcomes.check_outcomes()  # target met, single action
    sync_covered_cases([], [], [])
    _set_trend(monkeypatch, "revenue", [{"month": "2026-09", "value": 9_000_000, "is_partial": False}])
    sync_covered_cases([], [], [])

    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "captured"


# ---------------------------------------------------------------------------
# I, J: hold instead of premature expiry; late-resolving outcome corrects a
# closure via the audited correction mechanism.
# ---------------------------------------------------------------------------

def test_I_opportunity_signal_ends_while_action_outcome_pending_holds_open(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_today_sequence([datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    finding_actions.set_action(
        "revenue-2026-07", True, "completed", "ran a promo", case_id=case_id,
        target_metric_id="revenue", target_value=8_000_000, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
                     {"t": "2026-07-15T00:00:00"})
    # Action outcome NOT measurable yet (no August row at all) -- still pending.

    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])
    _set_trend(monkeypatch, "revenue", [{"month": "2026-09", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])  # verification window satisfied, but action outcome is pending

    case = case_engine.get_case(case_id)
    assert case["status"] == "verification_pending"  # held, NOT "resolved"/"expired"
    assert case["resolution_verdict"] is None


def test_J_action_target_achieved_after_signal_ends_closes_captured_next_cycle(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_today_sequence([datetime.date(2026, 7, 1), datetime.date(2026, 8, 1),
                                                datetime.date(2026, 9, 1), datetime.date(2026, 9, 5)]))
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    finding_actions.set_action(
        "revenue-2026-07", True, "completed", "ran a promo", case_id=case_id,
        target_metric_id="revenue", target_value=8_000_000, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
                     {"t": "2026-07-15T00:00:00"})

    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])  # progress=1
    _set_trend(monkeypatch, "revenue", [{"month": "2026-09", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])  # progress=2, window satisfied, outcome still pending -> held
    assert case_engine.get_case(case_id)["status"] == "verification_pending"

    # The action's outcome resolves NOW (August's real trend row lands,
    # target met) -- September's row stays visible too, so the case's own
    # cadence unit doesn't regress.
    _set_trend(monkeypatch, "revenue", [
        {"month": "2026-08", "value": 9_000_000, "is_partial": False},
        {"month": "2026-09", "value": 1, "is_partial": False},
    ])
    action_outcomes.check_outcomes()
    sync_covered_cases([], [], [])  # next cycle re-checks the policy

    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "captured"


def test_correct_resolution_preserves_the_original_verdict_in_history(appdb_reset):
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]
    appdb.run_query(
        "UPDATE business_cases SET status='resolved', resolution_verdict='expired' WHERE case_id=:id",
        {"id": case_id},
    )
    case_engine.correct_resolution(case_id, "captured", "action outcome resolved after case closure")

    case = case_engine.get_case(case_id)
    assert case["resolution_verdict"] == "captured"
    history = case_engine.get_case_history(case_id)
    correction = [h for h in history if h["event_type"] == "resolution_corrected"][0]
    detail = json.loads(correction["detail"])
    assert detail["old_verdict"] == "expired"
    assert detail["new_verdict"] == "captured"


# ---------------------------------------------------------------------------
# K: target validation.
# ---------------------------------------------------------------------------

def test_K_wrong_comparison_operator_is_rejected(appdb_reset):
    with pytest.raises(ValueError):
        finding_actions.set_action(
            "revenue-2026-07", True, "completed", None,
            target_metric_id="revenue", target_value=100, target_comparison="~=",
        )


def test_K_unknown_target_metric_is_rejected(appdb_reset):
    with pytest.raises(ValueError):
        finding_actions.set_action(
            "revenue-2026-07", True, "completed", None,
            target_metric_id="not_a_real_metric", target_value=100, target_comparison=">=",
        )


def test_K_target_unit_is_always_server_derived_not_client_supplied(appdb_reset):
    # No unit parameter exists on set_action at all -- it's read from the
    # real metric definition, so a caller can't record a target against
    # the wrong unit even if it tried.
    finding_actions.set_action(
        "revenue-2026-07", True, "completed", None,
        target_metric_id="revenue", target_value=100, target_comparison=">=",
    )
    action = finding_actions.get_actions_for(["revenue-2026-07"])["revenue-2026-07"]
    assert action["target_unit"] == case_engine.intelligence.METRICS["revenue"].unit


# ---------------------------------------------------------------------------
# L, M: measurement window correctness.
# ---------------------------------------------------------------------------

def test_L_latest_available_observation_predates_the_intervention(appdb_reset, monkeypatch):
    finding_actions.set_action(
        "revenue-2026-07", True, "completed", None,
        target_metric_id="revenue", target_value=100, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
                     {"t": "2026-07-15T00:00:00"})
    # Trend's latest data is from BEFORE the intervention (July) -- the
    # required period (August) doesn't exist in the trend at all.
    _set_trend(monkeypatch, "revenue", [{"month": "2026-07", "value": 9_000_000, "is_partial": False}])
    action_outcomes.check_outcomes()

    action = finding_actions.get_actions_for(["revenue-2026-07"])["revenue-2026-07"]
    assert action["outcome_verdict"] is None
    assert action["outcome_checked_at"] is None


def test_M_monthly_metric_evaluated_before_required_period_complete(appdb_reset, monkeypatch):
    finding_actions.set_action(
        "revenue-2026-07", True, "completed", None,
        target_metric_id="revenue", target_value=100, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
                     {"t": "2026-07-15T00:00:00"})
    # The required period (August) exists but is still partial (mid-month).
    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 9_000_000, "is_partial": True}])
    action_outcomes.check_outcomes()

    action = finding_actions.get_actions_for(["revenue-2026-07"])["revenue-2026-07"]
    assert action["outcome_verdict"] is None


# ---------------------------------------------------------------------------
# G: overlapping actions.
# ---------------------------------------------------------------------------

def test_G_two_overlapping_actions_are_not_individually_attributed(appdb_reset, monkeypatch):
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    finding_actions.set_action(
        "revenue-2026-07", True, "completed", "promo A", case_id=case_id,
        target_metric_id="revenue", target_value=8_000_000, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
                     {"t": "2026-07-10T00:00:00"})

    # A second, distinct action on the SAME case, overlapping window.
    finding_actions.set_action(
        "revenue-2026-08", True, "completed", "promo B", case_id=case_id,
        target_metric_id="revenue", target_value=8_500_000, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-08'",
                     {"t": "2026-07-20T00:00:00"})

    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 9_000_000, "is_partial": False}])
    action_outcomes.check_outcomes()

    actions = {a["finding_id"]: a for a in finding_actions.get_actions_for_case(case_id)}
    assert actions["revenue-2026-07"]["outcome_confidence"] == "overlapping_actions"
    assert actions["revenue-2026-08"]["outcome_confidence"] == "overlapping_actions"
    # Observed outcome is still preserved for each, not discarded.
    assert actions["revenue-2026-07"]["outcome_verdict"] == "met"
    assert actions["revenue-2026-08"]["outcome_verdict"] == "met"


# ---------------------------------------------------------------------------
# O: server-side evidence-freshness enforcement.
# ---------------------------------------------------------------------------

def test_O_server_rejects_a_stale_evidence_version(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09", current=100)
    sync_covered_cases([f1], [], [])
    case_id = f1["case_id"]
    morning_version = case_engine.get_case_observations(case_id)[0]["version"]

    # A scan lands (same day, later rescan) with different evidence --
    # same row (case_id, scan_run_id), so its revision bumps and its
    # version token changes even though observed_at alone might not
    # (see case_engine._observation_version).
    f2 = _monthly_finding("revenue", "financial", "2026-09", current=90)
    sync_covered_cases([f2], [], [])

    with pytest.raises(case_engine.StaleEvidenceError) as excinfo:
        case_engine.verify_evidence_version(case_id, "revenue-2026-09", morning_version)
    assert excinfo.value.current_snapshot["confirm"]["current"] == 90
    assert excinfo.value.current_version != morning_version


def test_O_server_accepts_a_matching_evidence_version(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09")
    sync_covered_cases([f1], [], [])
    case_id = f1["case_id"]
    version = case_engine.get_case_observations(case_id)[0]["version"]
    case_engine.verify_evidence_version(case_id, "revenue-2026-09", version)  # does not raise


def test_O_no_expected_version_skips_the_check_entirely(appdb_reset, monkeypatch):
    # Home.jsx/Actions.jsx's plain form never sends an evidence version --
    # must be a complete no-op, not an error.
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09")
    sync_covered_cases([f1], [], [])
    case_id = f1["case_id"]
    case_engine.verify_evidence_version(case_id, "revenue-2026-09", None)  # does not raise


# ---------------------------------------------------------------------------
# P: an unknown/undeclared metric relationship never produces unsupported
# corroboration -- the real revenue/Uber failure-rate case.
# ---------------------------------------------------------------------------

def test_P_undeclared_relationship_produces_no_unsupported_corroboration():
    revenue = {
        "metric_id": "revenue", "metric": {"id": "revenue", "label": "Revenue"}, "category": "opportunity",
        "confirm": {"period": "2026-09"}, "data_quality": {"verdict": "sufficient"},
        "root_cause": {"checks": [{"source": "store_concentration", "verdict": "confirmed", "segments": ["Ethul Kotte"]}],
                        "summary": {}},
    }
    revenue["root_cause"]["summary"] = ee.summarize(revenue["root_cause"]["checks"])
    uber = {
        "metric_id": "uber_failure_rate", "metric": {"id": "uber_failure_rate", "label": "Uber order failure rate"},
        "category": "opportunity", "confirm": {"period": "2026-09"}, "data_quality": {"verdict": "sufficient"},
        "root_cause": {"checks": [{"source": "store_concentration", "verdict": "confirmed", "segments": ["Ethul Kotte"]}],
                        "summary": {}},
    }
    uber["root_cause"]["summary"] = ee.summarize(uber["root_cause"]["checks"])

    ee.cross_metric_corroboration_checks([revenue, uber])

    for inv in (revenue, uber):
        sources = [c["source"] for c in inv["root_cause"]["checks"]]
        assert "cross_metric_corroboration" not in sources
        assert "conflicting_signal" not in sources
        assert "store_concentration" in sources  # each metric's own evidence is preserved


# ---------------------------------------------------------------------------
# Q: migration -- an existing resolved case survives without a fabricated
# retrospective verdict.
# ---------------------------------------------------------------------------

def test_Q_pre_existing_resolved_case_gets_no_backfilled_verdict(appdb_reset):
    case_engine._ensure_schema()
    appdb.run_query(
        "INSERT INTO business_cases (case_id, metric_id, domain, horizon, category, status, opened_at, last_seen_at, closed_at) "
        "VALUES ('legacy-case', 'revenue', 'financial', 'monthly', 'critical', 'resolved', 'x', 'x', 'x')"
    )
    # Re-running schema init (simulates a fresh process start against an
    # already-migrated database) must not touch this row's verdict.
    case_engine._SCHEMA_READY = False
    case_engine._ensure_schema()

    case = case_engine.get_case("legacy-case")
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] is None  # never guessed
