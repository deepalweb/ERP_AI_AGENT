"""Tests for finding_actions.py's Phase 4 additions: deadline/overdue and
best_status_for_metric (the action-linkage lookup used by tracking.py,
state_tracking.py, and weekly_streaks.py)."""

from datetime import date, timedelta

from backend.app.services import finding_actions as fa

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def test_no_deadline_is_never_overdue(appdb_reset):
    fa.set_action("revenue-2026-08", True, "in_progress", None, owner="Ops")
    action = fa.get_actions_for(["revenue-2026-08"])["revenue-2026-08"]
    assert action["deadline"] is None
    assert action["overdue"] is False


def test_future_deadline_is_not_overdue(appdb_reset):
    fa.set_action("revenue-2026-08", True, "in_progress", None, deadline=TOMORROW)
    action = fa.get_actions_for(["revenue-2026-08"])["revenue-2026-08"]
    assert action["overdue"] is False


def test_past_deadline_still_open_is_overdue(appdb_reset):
    fa.set_action("revenue-2026-08", True, "in_progress", None, deadline=YESTERDAY)
    action = fa.get_actions_for(["revenue-2026-08"])["revenue-2026-08"]
    assert action["overdue"] is True


def test_past_deadline_completed_is_not_overdue(appdb_reset):
    fa.set_action("revenue-2026-08", True, "completed", "Fixed it.", deadline=YESTERDAY)
    action = fa.get_actions_for(["revenue-2026-08"])["revenue-2026-08"]
    assert action["overdue"] is False


def test_finding_with_no_action_row_defaults_to_not_overdue(appdb_reset):
    action = fa.get_actions_for(["revenue-2026-08"])
    assert action == {}  # DEFAULT_ACTION is the caller's fallback, not a row


def test_best_status_for_metric_prefers_completed_over_in_progress(appdb_reset):
    # set_action stamps updated_at as real wall-clock "now", so the lookup
    # window has to bracket today's actual date, not a fixed literal.
    fa.set_action("stock_availability-2026-09-10", True, "in_progress", None)
    fa.set_action("stock_availability-2026-09-11", True, "completed", "Restocked.")
    status = fa.best_status_for_metric("stock_availability", TODAY, TODAY)
    assert status == "completed"


def test_best_status_for_metric_ignores_rows_outside_window(appdb_reset):
    fa.set_action("stock_availability-2026-09-05", True, "completed", "Old fix.")
    status = fa.best_status_for_metric("stock_availability", TOMORROW, TOMORROW)
    assert status is None


def test_best_status_for_metric_does_not_cross_prefix_boundary(appdb_reset):
    # "revenue-%" must not match a differently-named metric that merely
    # starts with the same letters (metric_ids only ever use '_'
    # internally, never '-', so this is a real boundary to guard).
    fa.set_action("revenue_growth-2026-09-10", True, "completed", "Unrelated metric.")
    status = fa.best_status_for_metric("revenue", TODAY, TODAY)
    assert status is None


def test_best_status_for_metric_with_no_rows_is_none(appdb_reset):
    assert fa.best_status_for_metric("outlet_no_orders", TODAY, TODAY) is None
