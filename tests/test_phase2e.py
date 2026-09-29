"""Phase 2e: Workflow Completion and Validation. Covers the 6 review tasks
against the actual Phase 2d code -- atomic evidence-version enforcement
(Task 1), the target/objective cadence validation the frontend form relies
on (Task 2's server side), pending/blocked/inconclusive outcome states
(Task 3), weekly vs. monthly measurement correctness (Task 4), and the
acceptance scenarios explicitly requested for this phase: evidence
changing during a save, multiple actions on one case, a completed action
with no measurable outcome, missing/overdue observations, daily-vs-
monthly (here: weekly-vs-monthly) measurement, and legacy compatibility.
"""

import datetime
import json
import threading
import time

import pytest

from backend.app.db import appdb
from backend.app.services import action_outcomes, case_engine, finding_actions
from mcp_server.tools import intelligence
from tests.coverage_helpers import sync_covered_cases


def _monthly_finding(metric_id, domain, period, category="critical", current=100, impact=50.0):
    baseline = current * 1.2
    return {
        "id": f"{metric_id}-{period}",
        "metric_id": metric_id,
        "domain": domain,
        "category": category,
        "priority": "high",
        "business_impact_score": impact,
        "title": f"{metric_id} moved",
        "confirm": {
            "period": period, "current": current, "baseline": baseline,
            "pct_change": (current - baseline) / baseline * 100 if baseline else 0.0,
        },
        "root_cause": {"checks": []},
    }


def _state_finding(metric_id, domain, as_of, category="critical"):
    return {
        "id": f"{metric_id}-{as_of}",
        "metric_id": metric_id,
        "domain": domain,
        "category": category,
        "priority": "high",
        "business_impact_score": 10.0,
        "title": f"{metric_id} snapshot",
        "confirm": {"period": as_of, "current": 1, "baseline": 2, "pct_change": -50.0},
        "root_cause": {"checks": []},
    }


def _fake_today_sequence(days):
    calls = {"i": 0}

    def _next():
        i = min(calls["i"], len(days) - 1)
        calls["i"] += 1
        return days[i]

    return _next


class _FakeMetric:
    def __init__(self, unit="percent", short_term_trend_sql=None):
        self.label = "Fake metric"
        self.unit = unit
        self.short_term_trend_sql = short_term_trend_sql


def _set_trend(monkeypatch, metric_id, rows):
    # Never mutate a real, shared MetricDef in place (these are the exact
    # same module-level singleton objects the whole app registry uses) --
    # setdefault only ever inserts a fake for a metric_id that doesn't
    # already exist, exactly as the pre-existing test_resolution_policy.py
    # helper of the same name does.
    metrics = dict(getattr(case_engine.intelligence, "METRICS", {}))
    metrics.setdefault(metric_id, _FakeMetric())
    monkeypatch.setattr(case_engine.intelligence, "METRICS", metrics)
    monkeypatch.setattr(case_engine.intelligence, "get_trend", lambda metric: rows)


def _set_trailing_trend(monkeypatch, metric_id, rows, as_of):
    """Like _set_trend, but for get_trailing_trend -- and since these
    tests need short_term_trend_sql set (weekly support), a metric_id
    that already exists in the real registry is replaced with a fresh
    _FakeMetric copy (same unit) rather than mutated in place."""
    metrics = dict(getattr(case_engine.intelligence, "METRICS", {}))
    existing = metrics.get(metric_id)
    metrics[metric_id] = _FakeMetric(unit=existing.unit if existing else "percent",
                                      short_term_trend_sql="SELECT 1 -- fake, only presence is checked")
    monkeypatch.setattr(case_engine.intelligence, "METRICS", metrics)
    monkeypatch.setattr(case_engine.intelligence, "get_trailing_trend", lambda metric: (rows, as_of))


_DEFAULT_ACTION_KWARGS = dict(
    relevant=True, status="not_started", action_taken=None, owner=None, deadline=None, action_kind=None,
    intended_result=None, target_metric_id=None, target_value=None, target_comparison=None,
    target_baseline_relative=False, target_measurement_period=None,
)


# ---------------------------------------------------------------------------
# Task 1: atomic evidence-version enforcement + legacy compatibility.
# ---------------------------------------------------------------------------

def test_scan_holding_the_lock_blocks_and_times_out_a_concurrent_action_save(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "ACTION_SAVE_LOCK_TIMEOUT", 0.2)
    scan_lock = appdb.case_store_lock()
    scan_lock.acquire()
    try:
        with pytest.raises(case_engine.ScanInProgressError):
            case_engine.save_action_with_verification("revenue-2026-07", _DEFAULT_ACTION_KWARGS, None)
    finally:
        scan_lock.release()

    # Once the "scan" releases the lock, the exact same save succeeds.
    case_engine.save_action_with_verification("revenue-2026-07", _DEFAULT_ACTION_KWARGS, None)
    action = finding_actions.get_actions_for(["revenue-2026-07"])["revenue-2026-07"]
    assert action["relevant"] is True


def test_action_save_and_a_concurrent_scan_never_interleave(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09", current=100)
    sync_covered_cases([f1], [], [])
    version = case_engine.get_case_observations(f1["case_id"])[0]["version"]

    order = []
    barrier = threading.Barrier(2)

    def scan_thread():
        barrier.wait(timeout=5)
        with appdb.case_store_lock():
            order.append("scan_start")
            time.sleep(0.15)
            order.append("scan_end")

    def save_thread():
        barrier.wait(timeout=5)
        case_engine.save_action_with_verification("revenue-2026-09", _DEFAULT_ACTION_KWARGS, version)
        order.append("save")

    t1, t2 = threading.Thread(target=scan_thread), threading.Thread(target=save_thread)
    t1.start(); t2.start()
    t1.join(timeout=5); t2.join(timeout=5)

    assert set(order) == {"scan_start", "scan_end", "save"}
    scan_start, scan_end, save = order.index("scan_start"), order.index("scan_end"), order.index("save")
    assert not (scan_start < save < scan_end)  # the save never lands inside the scan's held window


def test_legacy_save_on_a_case_aware_finding_is_marked_evidence_unverified(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09")
    sync_covered_cases([f1], [], [])

    # Home.jsx/Actions.jsx's plain form -- no evidence version at all.
    result = case_engine.save_action_with_verification("revenue-2026-09", _DEFAULT_ACTION_KWARGS, None)
    assert result["evidence_verified"] is False
    action = finding_actions.get_actions_for(["revenue-2026-09"])["revenue-2026-09"]
    assert action["evidence_verified"] is False


def test_verified_save_on_a_case_aware_finding_is_marked_evidence_verified(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09")
    sync_covered_cases([f1], [], [])
    version = case_engine.get_case_observations(f1["case_id"])[0]["version"]

    result = case_engine.save_action_with_verification("revenue-2026-09", _DEFAULT_ACTION_KWARGS, version)
    assert result["evidence_verified"] is True
    action = finding_actions.get_actions_for(["revenue-2026-09"])["revenue-2026-09"]
    assert action["evidence_verified"] is True
    assert action["evidence_version"] == version


def test_save_on_a_finding_with_no_case_leaves_evidence_verified_not_applicable(appdb_reset):
    result = case_engine.save_action_with_verification("unknown-metric-2026-09", _DEFAULT_ACTION_KWARGS, None)
    assert result["evidence_verified"] is None
    action = finding_actions.get_actions_for(["unknown-metric-2026-09"])["unknown-metric-2026-09"]
    assert action["evidence_verified"] is None


def test_a_stale_version_is_still_rejected_through_the_atomic_path(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09", current=100)
    sync_covered_cases([f1], [], [])
    stale_version = case_engine.get_case_observations(f1["case_id"])[0]["version"]

    f2 = _monthly_finding("revenue", "financial", "2026-09", current=90)  # same-day rescan, new evidence
    sync_covered_cases([f2], [], [])

    with pytest.raises(case_engine.StaleEvidenceError) as excinfo:
        case_engine.save_action_with_verification("revenue-2026-09", _DEFAULT_ACTION_KWARGS, stale_version)
    assert excinfo.value.current_snapshot["confirm"]["current"] == 90
    # Rejected -- nothing was written.
    assert finding_actions.get_actions_for(["revenue-2026-09"]) == {}


# ---------------------------------------------------------------------------
# Task 4: weekly vs. monthly measurement correctness.
# ---------------------------------------------------------------------------

def test_weekly_target_is_rejected_for_a_metric_with_no_weekly_support(appdb_reset):
    # The real "revenue" metric has no short_term_trend_sql.
    with pytest.raises(ValueError):
        finding_actions.set_action(
            "revenue-2026-07", True, "completed", None,
            target_metric_id="revenue", target_value=100, target_comparison=">=",
            target_measurement_period="weekly",
        )


def test_weekly_target_measures_against_the_trailing_trend_not_the_monthly_one(appdb_reset, monkeypatch):
    _set_trailing_trend(monkeypatch, "cancellation_rate", [
        {"month": "Aug 24 – Aug 30", "value": 15, "is_partial": False},
        {"month": "Aug 31 – Sep 06", "value": 4, "is_partial": False},
    ], as_of=datetime.date(2026, 9, 6))

    def _get_trend_should_not_be_called(metric):
        raise AssertionError("a weekly target must never fall back to the monthly trend")
    monkeypatch.setattr(case_engine.intelligence, "get_trend", _get_trend_should_not_be_called)

    finding_actions.set_action(
        "cancellation_rate-2026-08", True, "completed", None,
        target_metric_id="cancellation_rate", target_value=5, target_comparison="<=",
        target_measurement_period="weekly",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='cancellation_rate-2026-08'",
                     {"t": "2026-08-25T00:00:00"})

    action_outcomes.check_outcomes()

    action = finding_actions.get_actions_for(["cancellation_rate-2026-08"])["cancellation_rate-2026-08"]
    assert action["outcome_verdict"] == "met"  # 4 <= 5, the post-intervention window
    assert action["outcome_period"] == "Aug 31 – Sep 06"


def test_weekly_target_stays_pending_when_no_clean_post_intervention_window_exists_yet(appdb_reset, monkeypatch):
    _set_trailing_trend(monkeypatch, "cancellation_rate", [
        {"month": "Aug 24 – Aug 30", "value": 15, "is_partial": False},
    ], as_of=datetime.date(2026, 8, 30))

    finding_actions.set_action(
        "cancellation_rate-2026-08", True, "completed", None,
        target_metric_id="cancellation_rate", target_value=5, target_comparison="<=",
        target_measurement_period="weekly",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='cancellation_rate-2026-08'",
                     {"t": "2026-08-29T00:00:00"})  # the only window available still overlaps completion

    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2026, 9, 6))  # still within grace
    action_outcomes.check_outcomes()

    action = finding_actions.get_actions_for(["cancellation_rate-2026-08"])["cancellation_rate-2026-08"]
    assert action["outcome_verdict"] is None
    assert action["outcome_status"] == "pending"


def test_weekly_horizon_case_with_a_direct_objective_actually_resolves(appdb_reset, monkeypatch):
    # Regression: before Phase 2e, _resolve_case_direct_objective always
    # matched last_verified_period (a calendar DAY for a weekly case)
    # against get_trend()'s monthly "YYYY-MM" rows -- which could never
    # match, so a weekly-horizon case with a direct objective would hold
    # forever. Fixed to use the trailing trend for a weekly case instead.
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence(
        [datetime.date(2026, 9, 1), datetime.date(2026, 9, 2), datetime.date(2026, 9, 3)]))
    f = _state_finding("cancellation_rate", "operations", "2026-09-01", category="opportunity")
    sync_covered_cases([], [], [f])
    case_id = f["case_id"]

    _set_trailing_trend(monkeypatch, "cancellation_rate", [{"month": "recent week", "value": 3, "is_partial": False}],
                         as_of=datetime.date(2026, 9, 3))
    case_engine.set_case_objective(case_id, "cancellation_rate", 5, "<=")

    sync_covered_cases([], [], [])  # progress=1
    sync_covered_cases([], [], [])  # progress=2, window satisfied

    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "captured"


def test_case_objective_rejects_a_weekly_metric_without_weekly_support(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f = _state_finding("revenue", "financial", "2026-09-01", category="opportunity")
    sync_covered_cases([], [], [f])
    with pytest.raises(ValueError):
        case_engine.set_case_objective(f["case_id"], "revenue", 1_000_000, ">=")


def test_case_objective_rejects_snapshot_horizon(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f = {
        "id": "stockout_rate-2026-09-01", "metric_id": "stockout_rate", "domain": "inventory",
        "category": "opportunity", "priority": "high", "business_impact_score": 10.0, "title": "x",
        "confirm": {"period": "2026-09-01", "current": 1, "baseline": 2, "pct_change": -50.0},
        "root_cause": {"checks": []},
    }
    sync_covered_cases([], [f], [])  # state_findings -> horizon="snapshot"
    with pytest.raises(ValueError):
        case_engine.set_case_objective(f["case_id"], "cancellation_rate", 5, "<=")


# ---------------------------------------------------------------------------
# Task 3: pending / blocked / inconclusive verification states.
# ---------------------------------------------------------------------------

def test_a_stuck_monthly_target_moves_from_pending_to_blocked_to_inconclusive(appdb_reset, monkeypatch):
    _set_trend(monkeypatch, "revenue", [])  # required period never appears
    finding_actions.set_action(
        "revenue-2026-01", True, "completed", None,
        target_metric_id="revenue", target_value=100, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-01'",
                     {"t": "2026-01-15T00:00:00"})
    # required period end = 2026-02-01

    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2026, 2, 10))  # period not even due yet
    action_outcomes.check_outcomes()
    action = finding_actions.get_actions_for(["revenue-2026-01"])["revenue-2026-01"]
    assert action["outcome_status"] == "pending"
    assert action["outcome_checked_at"] is None

    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2026, 5, 1))  # 61 days overdue
    action_outcomes.check_outcomes()
    action = finding_actions.get_actions_for(["revenue-2026-01"])["revenue-2026-01"]
    assert action["outcome_status"] == "blocked"
    assert action["outcome_checked_at"] is None  # still not terminal -- kept in the working set

    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2026, 8, 1))  # 153 days overdue
    action_outcomes.check_outcomes()
    action = finding_actions.get_actions_for(["revenue-2026-01"])["revenue-2026-01"]
    assert action["outcome_status"] == "inconclusive"
    assert action["outcome_verdict"] is None  # never fabricated as "not_met"
    assert action["outcome_checked_at"] is not None

    # Re-running check_outcomes() again with no new data keeps recomputing
    # the SAME "inconclusive" verdict (harmless, idempotent -- see
    # check_outcomes' docstring) rather than freezing it forever: an
    # inconclusive outcome must stay eligible for a real answer if valid
    # data eventually arrives (post-Phase-2e acceptance audit, Task 1
    # point 6 / Task 4 -- see test_late_arriving_data_resolves_a_
    # previously_inconclusive_action_outcome for the case where it does).
    action_outcomes.check_outcomes()
    action = finding_actions.get_actions_for(["revenue-2026-01"])["revenue-2026-01"]
    assert action["outcome_status"] == "inconclusive"
    assert action["outcome_verdict"] is None


def test_an_inconclusive_action_lets_its_opportunity_case_close_inconclusive_not_hold_forever(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence(
        [datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
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
    sync_covered_cases([], [], [])
    _set_trend(monkeypatch, "revenue", [{"month": "2026-09", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])
    assert case_engine.get_case(case_id)["status"] == "verification_pending"  # held, outcome still pending

    # The trend already lacks an August row (only September's, from
    # above) -- the required post-intervention period for the action
    # (August) never arrives, and enough real time passes that its
    # outcome gives up. September stays visible so the CASE's own
    # (already-satisfied) monthly cadence unit doesn't regress, matching
    # the held-case re-check semantics _advance_verification relies on.
    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2027, 1, 1))
    action_outcomes.check_outcomes()
    sync_covered_cases([], [], [])  # re-checks the policy

    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "inconclusive"  # not "expired" -- never claims it disproved capture


# ---------------------------------------------------------------------------
# GET /intelligence/metrics exposes cadence support (Task 2's server side).
# ---------------------------------------------------------------------------

def test_list_metrics_exposes_which_metrics_support_weekly_measurement():
    metrics = {m["id"]: m for m in intelligence.list_metrics()}
    assert metrics["cancellation_rate"]["supports_weekly_measurement"] is True
    assert metrics["revenue"]["supports_weekly_measurement"] is False


# ---------------------------------------------------------------------------
# Post-Phase-2e acceptance audit, Task 1: technical integrity fixes.
# ---------------------------------------------------------------------------

def test_version_tokens_never_collide_across_different_days_of_a_monthly_finding(appdb_reset, monkeypatch):
    # Point 2: a monthly finding's id ("revenue-2026-09") stays identical
    # across every daily scan within the month, so many DIFFERENT
    # case_observations rows share it. Each fresh day's row starts back at
    # revision 1 -- if the version token were "revision" alone, day 2's
    # row could coincidentally repeat day 1's token, silently defeating
    # the staleness check. It doesn't, because the token also carries the
    # row's own unique id.
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence(
        [datetime.date(2026, 9, 1), datetime.date(2026, 9, 2)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09", current=100)
    sync_covered_cases([f1], [], [])
    case_id = f1["case_id"]
    day1_version = case_engine.get_case_observations(case_id)[0]["version"]

    f2 = _monthly_finding("revenue", "financial", "2026-09", current=110)  # next calendar day -- a fresh row
    sync_covered_cases([f2], [], [])
    day2_version = case_engine.get_case_observations(case_id)[-1]["version"]

    assert day1_version != day2_version
    with pytest.raises(case_engine.StaleEvidenceError):
        case_engine.verify_evidence_version(case_id, "revenue-2026-09", day1_version)


def test_a_failure_recording_the_decision_snapshot_rolls_back_the_action_write_too(appdb_reset, monkeypatch):
    # Point 1: the action write and its audit-trail entry must succeed or
    # fail together -- simulate the audit-trail write blowing up AFTER the
    # action write would otherwise have already committed, and confirm
    # nothing was left half-saved.
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09")
    sync_covered_cases([f1], [], [])

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure recording the decision snapshot")
    monkeypatch.setattr(case_engine, "record_decision_snapshot", _boom)

    with pytest.raises(RuntimeError):
        case_engine.save_action_with_verification("revenue-2026-09", _DEFAULT_ACTION_KWARGS, None)

    # Rolled back -- the action write did NOT survive on its own.
    assert finding_actions.get_actions_for(["revenue-2026-09"]) == {}


def test_dismiss_reopen_and_objective_endpoints_respect_the_same_case_write_lock(appdb_reset, monkeypatch):
    # Point 3: every case-mutating entry point reachable directly from an
    # API request must respect the same concurrency guarantee as action
    # saves -- dismiss_case/reopen_case/set_case_objective previously took
    # no lock at all.
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    monkeypatch.setattr(case_engine, "ACTION_SAVE_LOCK_TIMEOUT", 0.2)
    f1 = _monthly_finding("revenue", "financial", "2026-09", category="opportunity")
    sync_covered_cases([f1], [], [])
    case_id = f1["case_id"]

    scan_lock = appdb.case_store_lock()
    scan_lock.acquire()
    try:
        with pytest.raises(case_engine.ScanInProgressError):
            case_engine.set_case_objective(case_id, "revenue", 1_000_000, ">=")
        with pytest.raises(case_engine.ScanInProgressError):
            case_engine.dismiss_case(case_id, "not relevant")
    finally:
        scan_lock.release()

    # Once released, the identical calls succeed normally.
    case_engine.set_case_objective(case_id, "revenue", 1_000_000, ">=")
    case_engine.dismiss_case(case_id, "not relevant")
    case = case_engine.get_case(case_id)
    assert case["status"] == "dismissed"
    assert case["objective_metric_id"] == "revenue"

    scan_lock = appdb.case_store_lock()
    scan_lock.acquire()
    try:
        with pytest.raises(case_engine.ScanInProgressError):
            case_engine.reopen_case(case_id, "actually still relevant")
    finally:
        scan_lock.release()
    case_engine.reopen_case(case_id, "actually still relevant")
    assert case_engine.get_case(case_id)["status"] == "reopened"


def test_designate_objective_from_action_respects_the_case_write_lock(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence([datetime.date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09", category="opportunity")
    sync_covered_cases([f1], [], [])
    case_id = f1["case_id"]
    finding_actions.set_action(
        "revenue-2026-09", True, "completed", None, case_id=case_id,
        target_metric_id="revenue", target_value=1_000_000, target_comparison=">=",
    )

    monkeypatch.setattr(case_engine, "ACTION_SAVE_LOCK_TIMEOUT", 0.2)
    scan_lock = appdb.case_store_lock()
    scan_lock.acquire()
    try:
        with pytest.raises(case_engine.ScanInProgressError):
            case_engine.designate_case_objective_from_action(case_id, "revenue-2026-09")
    finally:
        scan_lock.release()

    case_engine.designate_case_objective_from_action(case_id, "revenue-2026-09")
    assert case_engine.get_case(case_id)["objective_source_finding_id"] == "revenue-2026-09"


# ---------------------------------------------------------------------------
# Post-Phase-2e acceptance audit, Tasks 1.6 & 4: late-arriving valid data.
# ---------------------------------------------------------------------------

def test_late_arriving_data_resolves_a_previously_inconclusive_action_outcome(appdb_reset, monkeypatch):
    finding_actions.set_action(
        "revenue-2026-01", True, "completed", None,
        target_metric_id="revenue", target_value=100, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-01'",
                     {"t": "2026-01-15T00:00:00"})
    _set_trend(monkeypatch, "revenue", [])  # the pipeline never produces the required period

    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2026, 7, 1))  # well past the deadline
    action_outcomes.check_outcomes()
    action = finding_actions.get_actions_for(["revenue-2026-01"])["revenue-2026-01"]
    assert action["outcome_status"] == "inconclusive"
    assert action["outcome_verdict"] is None

    # The pipeline recovers -- the real February row finally lands.
    _set_trend(monkeypatch, "revenue", [{"month": "2026-02", "value": 150, "is_partial": False}])
    action_outcomes.check_outcomes()
    action = finding_actions.get_actions_for(["revenue-2026-01"])["revenue-2026-01"]
    assert action["outcome_status"] == "met"
    assert action["outcome_verdict"] == "met"
    assert action["outcome_value"] == 150


def test_late_arriving_data_audits_a_correction_on_a_case_already_closed_inconclusive(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence(
        [datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    sync_covered_cases([f], [], [])
    case_id = f["case_id"]

    finding_actions.set_action(
        "revenue-2026-07", True, "completed", "ran a promo", case_id=case_id,
        target_metric_id="revenue", target_value=8_000_000, target_comparison=">=",
    )
    appdb.run_query("UPDATE finding_actions SET completed_at=:t WHERE finding_id='revenue-2026-07'",
                     {"t": "2026-07-15T00:00:00"})
    case_engine.designate_case_objective_from_action(case_id, "revenue-2026-07")

    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])
    _set_trend(monkeypatch, "revenue", [{"month": "2026-09", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])
    assert case_engine.get_case(case_id)["status"] == "verification_pending"  # held on the designated action

    # The required August row never lands; the deadline passes and the
    # action goes inconclusive. Because this case's objective was
    # designated from this exact action, the held case closes on that.
    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2027, 1, 1))
    action_outcomes.check_outcomes()
    sync_covered_cases([], [], [])
    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "inconclusive"

    # Real time keeps passing and the pipeline finally recovers with the
    # real August number, well after the case already closed.
    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 9_000_000, "is_partial": False}])
    action_outcomes.check_outcomes()

    case = case_engine.get_case(case_id)
    assert case["resolution_verdict"] == "captured"  # audited correction, not a silent overwrite
    history = case_engine.get_case_history(case_id)
    correction = [h for h in history if h["event_type"] == "resolution_corrected"][0]
    detail = json.loads(correction["detail"])
    assert detail["old_verdict"] == "inconclusive"
    assert detail["new_verdict"] == "captured"
