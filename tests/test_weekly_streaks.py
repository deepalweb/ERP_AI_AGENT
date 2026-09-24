"""Tests for weekly_streaks.py's Phase 4 verdict addition: a frozen
improved/worsened/unchanged verdict computed once a sound comparison point
exists (WINDOW_DAYS consecutive days, or the streak ending early)."""

from datetime import date, timedelta

from backend.app.services import finding_actions, weekly_streaks as ws

# Anchored to real "today" (not a fixed literal) so that finding_actions
# rows -- whose updated_at is always stamped as real wall-clock now -- fall
# inside the day-offset windows this file constructs.
START = date.today()


def _day(offset):
    return (START + timedelta(days=offset)).isoformat()


def _finding(metric_id, day_offset, current, category="critical"):
    return {
        "metric_id": metric_id,
        "category": category,
        "as_of_date": _day(day_offset),
        "confirm": {"current": current},
    }


def test_streak_under_window_days_has_no_verdict_yet(appdb_reset):
    for offset in range(3):  # 3 consecutive days, well under WINDOW_DAYS (7)
        ws.record_streaks([_finding("cancellation_rate", offset, 15.0)])
    active = ws.get_active_streaks()
    assert active["cancellation_rate"]["consecutive_days"] == 3
    assert ws.get_verdicted_streaks() == []


def test_streak_reaching_window_days_gets_a_verdict_while_still_ongoing(appdb_reset):
    # Detected at 15.0, still 15.0 a week later -> unchanged, but a genuine
    # problem still actively flagged (status must stay 'ongoing').
    for offset in range(ws.WINDOW_DAYS):
        ws.record_streaks([_finding("cancellation_rate", offset, 15.0)])
    verdicted = ws.get_verdicted_streaks()
    assert len(verdicted) == 1
    assert verdicted[0]["status"] == "ongoing"
    assert verdicted[0]["verdict"] == "unchanged"


def test_streak_ending_before_window_days_gets_a_verdict_from_last_value(appdb_reset):
    # Detected at 15.0 (day 0), improves to 4.0 by day 1, then stops being
    # flagged on day 2 -- verdict must come from the LAST known value (4.0),
    # not the original detected_value.
    ws.record_streaks([_finding("cancellation_rate", 0, 15.0)])
    ws.record_streaks([_finding("cancellation_rate", 1, 4.0)])
    ws.record_streaks([])  # day 2: no longer flagged at all -> streak ends
    verdicted = ws.get_verdicted_streaks()
    assert len(verdicted) == 1
    assert verdicted[0]["status"] == "ended"
    assert verdicted[0]["verdict"] == "improved"
    assert verdicted[0]["outcome_value"] == 4.0


def test_verdict_freezes_and_does_not_recompute_after_window_days(appdb_reset):
    for offset in range(ws.WINDOW_DAYS):
        ws.record_streaks([_finding("cancellation_rate", offset, 15.0)])
    first_verdict = ws.get_verdicted_streaks()[0]["verdict"]
    # Keeps getting worse afterward -- the frozen verdict must not flip.
    for offset in range(ws.WINDOW_DAYS, ws.WINDOW_DAYS + 3):
        ws.record_streaks([_finding("cancellation_rate", offset, 40.0)])
    assert ws.get_verdicted_streaks()[0]["verdict"] == first_verdict


def test_a_gap_in_flagging_closes_the_stale_streak_without_a_verdict(appdb_reset):
    # Day 0-1 flagged, day 2 scan missed entirely (not just "not flagged" --
    # simulated by jumping straight to day 3 as a fresh detection), so the
    # old streak's true day-2 value was never observed.
    ws.record_streaks([_finding("cancellation_rate", 0, 15.0)])
    ws.record_streaks([_finding("cancellation_rate", 1, 15.0)])
    ws.record_streaks([_finding("cancellation_rate", 3, 15.0)])  # gap at day 2
    verdicted = ws.get_verdicted_streaks()
    # The stale (gapped) streak closed with no verdict; only the fresh
    # streak starting at day 3 exists now, itself with no verdict yet.
    assert verdicted == []


def test_action_status_during_run_is_captured_when_streak_ends(appdb_reset):
    ws.record_streaks([_finding("cancellation_rate", 0, 15.0)])
    finding_actions.set_action("cancellation_rate-trailing-" + _day(0), True, "completed", "Fixed the courier issue.")
    ws.record_streaks([])  # ends the streak
    verdicted = ws.get_verdicted_streaks()
    assert verdicted[0]["action_status_during_run"] == "completed"


def test_detected_value_is_set_on_first_day(appdb_reset):
    ws.record_streaks([_finding("cancellation_rate", 0, 22.5)])
    rows = ws.get_verdicted_streaks()
    assert rows == []  # no verdict yet -- but confirm via a direct DB read
    from backend.app.db import appdb

    row = appdb.run_query("SELECT detected_value FROM weekly_streaks WHERE metric_id = 'cancellation_rate'")[0]
    assert row["detected_value"] == 22.5
