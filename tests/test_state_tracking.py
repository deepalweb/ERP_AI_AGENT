"""Tests for state_tracking.py — the Monitoring-half sibling to tracking.py
for state findings (stock-outs, dead/overstock value, etc.), which recompute
fresh every scan instead of following a calendar period."""

from datetime import date, timedelta

from backend.app.services import finding_actions, state_tracking as st

TODAY = date.today()


def _finding(metric_id, current, category="critical"):
    return {"metric_id": metric_id, "category": category, "confirm": {"current": current}}


def test_record_new_findings_starts_one_open_row_per_metric(appdb_reset):
    st.record_new_findings([_finding("stock_availability", 12)])
    rows = st.get_tracking_history()
    assert len(rows) == 1
    assert rows[0]["metric_id"] == "stock_availability"
    assert rows[0]["detected_value"] == 12
    assert rows[0]["status"] == "open"


def test_record_new_findings_does_not_reopen_or_overwrite_existing_open_row(appdb_reset):
    st.record_new_findings([_finding("stock_availability", 12)])
    # Same metric flagged again on a later scan with a worse value -- the
    # detected_value must stay anchored to when it was FIRST noticed.
    st.record_new_findings([_finding("stock_availability", 20)])
    rows = st.get_tracking_history()
    assert len(rows) == 1
    assert rows[0]["detected_value"] == 12


def test_finding_absent_from_todays_scan_resolves_immediately_as_improved(appdb_reset):
    st.record_new_findings([_finding("stock_availability", 12)])
    # Resolved well before check_after_date (7 days out) -- absence itself
    # is the signal, no need to wait.
    st.check_outcomes([])  # today's scan no longer produces this metric at all
    rows = st.get_tracking_history()
    assert rows[0]["status"] == "resolved"
    assert rows[0]["verdict"] == "improved"
    assert rows[0]["outcome_value"] == 0


def test_still_present_before_check_after_date_stays_open(appdb_reset):
    st.record_new_findings([_finding("stock_availability", 12)])
    # Still flagged, and check_after_date (today + 7 days) hasn't passed yet.
    st.check_outcomes([_finding("stock_availability", 11)])
    rows = st.get_tracking_history()
    assert rows[0]["status"] == "open"
    assert rows[0]["verdict"] is None


def test_still_present_after_check_after_date_resolves_by_value_comparison(appdb_reset):
    st.record_new_findings([_finding("stock_availability", 20)])
    # Force the check_after_date into the past so this scan is due.
    from backend.app.db import appdb

    appdb.run_query(
        "UPDATE tracked_state_findings SET check_after_date = :d WHERE metric_id = 'stock_availability'",
        {"d": (TODAY - timedelta(days=1)).isoformat()},
    )
    st.check_outcomes([_finding("stock_availability", 5)])  # shrank to <=80% of 20
    rows = st.get_tracking_history()
    assert rows[0]["status"] == "resolved"
    assert rows[0]["verdict"] == "improved"
    assert rows[0]["outcome_value"] == 5


def test_worsened_outcome_is_not_marked_resolved(appdb_reset):
    # Same regression as tracking.py: check_outcomes() used to set
    # status='resolved' the instant ANY outcome was checked, regardless of
    # verdict. Only a genuinely improved comparison may resolve.
    st.record_new_findings([_finding("stock_availability", 10)])
    from backend.app.db import appdb

    appdb.run_query(
        "UPDATE tracked_state_findings SET check_after_date = :d WHERE metric_id = 'stock_availability'",
        {"d": (TODAY - timedelta(days=1)).isoformat()},
    )
    st.check_outcomes([_finding("stock_availability", 20)])  # got worse, still present
    rows = st.get_tracking_history()
    assert rows[0]["verdict"] == "worsened"
    assert rows[0]["status"] == "checked_unresolved"
    assert rows[0]["status"] != "resolved"


def test_action_status_during_run_is_captured_on_resolution(appdb_reset):
    st.record_new_findings([_finding("stock_availability", 12)])
    finding_actions.set_action("stock_availability-" + TODAY.isoformat(), True, "in_progress", None)
    st.check_outcomes([])
    rows = st.get_tracking_history()
    assert rows[0]["action_status_during_run"] == "in_progress"


def test_a_recurring_problem_after_full_resolution_opens_a_fresh_row(appdb_reset):
    st.record_new_findings([_finding("stock_availability", 12)])
    st.check_outcomes([])  # resolves it
    st.record_new_findings([_finding("stock_availability", 8)])  # flagged again later
    rows = st.get_tracking_history()
    assert len(rows) == 2
    open_rows = [r for r in rows if r["status"] == "open"]
    assert len(open_rows) == 1
    assert open_rows[0]["detected_value"] == 8
