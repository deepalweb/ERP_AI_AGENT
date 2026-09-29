"""Controlled Pilot Preparation, Task 1: final integrity checks against the
actual Phase 2e code. A new file (not appended to test_phase2e.py) so this
turn's additions are independently countable -- see the readiness report's
test-count reconciliation.

Point 1 (transactional action + decision-snapshot writes) and points about
evidence-version correctness were already covered directly in
test_phase2e.py; this file covers the two specifically NEW verification
asks: repeated inconclusive-outcome checks must not generate duplicate
history or unsupported changes, and an audited correction must fire
exactly once when evidence genuinely changes after a decision -- never
repeatedly for the same, unchanged evidence.
"""

import datetime
import json

from backend.app.db import appdb
from backend.app.services import action_outcomes, case_engine, finding_actions
from tests.coverage_helpers import sync_covered_cases
from tests.test_phase2e import _fake_today_sequence, _monthly_finding, _set_trend


def _history_event_counts(case_id):
    events = case_engine.get_case_history(case_id)
    counts = {}
    for e in events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
    return counts


def test_repeated_inconclusive_checks_do_not_duplicate_history_or_change_resolution(appdb_reset, monkeypatch):
    # Point 2: an action's outcome that's already given up on ("inconclusive",
    # no data ever arrived) is deliberately still re-examined on every
    # check_outcomes() call (so late-arriving data can still resolve it --
    # see test_late_arriving_data_resolves_a_previously_inconclusive_
    # action_outcome). That re-examination must be a genuine no-op when
    # nothing has actually changed: no new case_history entries, no
    # resolution_verdict churn, no outcome_value/verdict fabricated.
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence(
        [datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    case_engine.sync_cases([f], [], [])
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
    assert case_engine.get_case(case_id)["status"] == "verification_pending"

    # The required August row never arrives; push well past the inconclusive
    # deadline and let the case close on it.
    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2027, 1, 1))
    action_outcomes.check_outcomes()
    sync_covered_cases([], [], [])
    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["resolution_verdict"] == "inconclusive"

    counts_after_first_close = _history_event_counts(case_id)

    # Re-run check_outcomes() several more times with NOTHING new -- real
    # daily-scan cadence, same absent data.
    for _ in range(3):
        action_outcomes.check_outcomes()

    action_after_repeats = finding_actions.get_actions_for(["revenue-2026-07"])["revenue-2026-07"]
    case_after_repeats = case_engine.get_case(case_id)

    assert action_after_repeats["outcome_status"] == "inconclusive"
    assert action_after_repeats["outcome_verdict"] is None  # never fabricated
    assert action_after_repeats["outcome_value"] is None  # never fabricated
    assert case_after_repeats["resolution_verdict"] == "inconclusive"  # unchanged

    counts_after_repeats = _history_event_counts(case_id)
    assert counts_after_repeats == counts_after_first_close  # not one new history entry
    assert counts_after_repeats.get("resolution_corrected", 0) == 0  # nothing to correct yet


def test_evidence_change_after_a_decision_produces_exactly_one_audited_correction(appdb_reset, monkeypatch):
    # Point 3: a manager's decision (the action + target) was recorded
    # against the evidence available at the time. The case closes on an
    # interim verdict before the full picture is in. When the evidence
    # DOES genuinely change (the real trend value finally lands), the
    # correction must be audited (old verdict preserved, not erased) --
    # and it must fire exactly once, not once per subsequent scan that
    # simply re-observes the same, already-corrected evidence.
    monkeypatch.setattr(case_engine, "_business_today", _fake_today_sequence(
        [datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)]))
    f = _monthly_finding("revenue", "financial", "2026-07", category="opportunity")
    case_engine.sync_cases([f], [], [])
    case_id = f["case_id"]

    # The manager's decision: this action, this target, recorded against
    # the evidence available at save time.
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

    # Evidence as it stood then: no August measurement at all -- the case
    # closes "inconclusive," the honest verdict for what was known then.
    monkeypatch.setattr(action_outcomes, "_today", lambda: datetime.date(2027, 1, 1))
    action_outcomes.check_outcomes()
    sync_covered_cases([], [], [])
    assert case_engine.get_case(case_id)["resolution_verdict"] == "inconclusive"

    # The evidence genuinely changes: the real August figure finally lands.
    _set_trend(monkeypatch, "revenue", [{"month": "2026-08", "value": 9_000_000, "is_partial": False}])
    action_outcomes.check_outcomes()

    case = case_engine.get_case(case_id)
    assert case["resolution_verdict"] == "captured"
    history = case_engine.get_case_history(case_id)
    corrections = [h for h in history if h["event_type"] == "resolution_corrected"]
    assert len(corrections) == 1
    detail = json.loads(corrections[0]["detail"])
    assert detail["old_verdict"] == "inconclusive"
    assert detail["new_verdict"] == "captured"

    # Re-running check_outcomes() again must not produce a second
    # correction, a second history entry, or any drift in the verdict --
    # a real "met" verdict is terminal (outcome_checked_at is set), so
    # the row drops out of check_outcomes()'s working set entirely, same
    # as any other resolved action; nothing re-examines already-settled
    # evidence just because a scan happened.
    action_outcomes.check_outcomes()
    action_outcomes.check_outcomes()

    case_again = case_engine.get_case(case_id)
    assert case_again["resolution_verdict"] == "captured"
    history_again = case_engine.get_case_history(case_id)
    corrections_again = [h for h in history_again if h["event_type"] == "resolution_corrected"]
    assert len(corrections_again) == 1  # still exactly one -- not re-triggered
    assert len(history_again) == len(history)  # no additional entries of any kind
