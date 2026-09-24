"""Tests for tracking.py's Phase 4 additions: the public verdict_for_deviation
(renamed from _verdict_for so state_tracking.py/weekly_streaks.py can reuse
it) and the action_status_at_check field check_outcomes() now populates."""

from datetime import date

from backend.app.services import finding_actions, tracking


def test_verdict_for_deviation_improved():
    assert tracking.verdict_for_deviation(20.0, 4.0) == "improved"  # 4 <= 20*0.8


def test_verdict_for_deviation_worsened():
    assert tracking.verdict_for_deviation(10.0, 15.0) == "worsened"  # 15 >= 10*1.2


def test_verdict_for_deviation_unchanged():
    assert tracking.verdict_for_deviation(10.0, 10.0) == "unchanged"


def test_verdict_for_deviation_zero_baseline_is_unchanged():
    assert tracking.verdict_for_deviation(0.0, 5.0) == "unchanged"


def test_verdict_for_deviation_works_with_raw_values_not_just_pct_change():
    # This is the whole point of the rename -- state_tracking.py passes a
    # raw Rs figure/count here, not a percentage, and gets the identical logic.
    assert tracking.verdict_for_deviation(500000, 100000) == "improved"


def _investigation(metric_id, period, current, baseline, pct_change, category="critical"):
    return {
        "metric_id": metric_id,
        "category": category,
        "confirm": {"period": period, "current": current, "baseline": baseline, "pct_change": pct_change},
        "recommendations": [],
    }


def test_check_outcomes_captures_action_status_when_present(appdb_reset, monkeypatch):
    tracking.record_new_findings([_investigation("revenue", "2026-08", 8_000_000, 10_000_000, -20.0)])
    finding_actions.set_action("revenue-2026-08", True, "in_progress", None)

    # Fake next month's trend so check_outcomes finds a complete outcome row.
    monkeypatch.setattr(
        "backend.app.services.tracking.intelligence.get_trend",
        lambda metric: [{"month": "2026-09", "value": 9_800_000, "is_partial": False}],
    )
    monkeypatch.setattr(
        "backend.app.services.tracking.intelligence.METRICS", {"revenue": type("M", (), {"label": "Revenue", "unit": "Rs"})()}
    )
    tracking.check_outcomes()

    history = tracking.get_tracking_history()
    assert history[0]["action_status_at_check"] == "in_progress"
    assert history[0]["verdict"] == "improved"


def test_worsened_outcome_is_not_marked_resolved(appdb_reset, monkeypatch):
    # Regression test for a real defect: check_outcomes() used to set
    # status='resolved' the instant ANY outcome was checked, regardless of
    # verdict -- so a finding that got WORSE was displayed as "resolved".
    # Only an 'improved' verdict may ever produce status='resolved'.
    tracking.record_new_findings([_investigation("revenue", "2026-08", 8_000_000, 10_000_000, -20.0)])

    monkeypatch.setattr(
        "backend.app.services.tracking.intelligence.get_trend",
        lambda metric: [{"month": "2026-09", "value": 7_000_000, "is_partial": False}],
    )
    monkeypatch.setattr(
        "backend.app.services.tracking.intelligence.METRICS", {"revenue": type("M", (), {"label": "Revenue", "unit": "Rs"})()}
    )
    tracking.check_outcomes()

    history = tracking.get_tracking_history()
    assert history[0]["verdict"] == "worsened"
    assert history[0]["status"] == "checked_unresolved"
    assert history[0]["status"] != "resolved"


def test_unchanged_outcome_is_not_marked_resolved(appdb_reset, monkeypatch):
    tracking.record_new_findings([_investigation("revenue", "2026-08", 9_500_000, 10_000_000, -5.0)])

    monkeypatch.setattr(
        "backend.app.services.tracking.intelligence.get_trend",
        lambda metric: [{"month": "2026-09", "value": 9_500_000, "is_partial": False}],
    )
    monkeypatch.setattr(
        "backend.app.services.tracking.intelligence.METRICS", {"revenue": type("M", (), {"label": "Revenue", "unit": "Rs"})()}
    )
    tracking.check_outcomes()

    history = tracking.get_tracking_history()
    assert history[0]["verdict"] == "unchanged"
    assert history[0]["status"] == "checked_unresolved"


def test_legacy_resolved_rows_with_a_bad_verdict_are_backfilled_on_schema_init(appdb_reset):
    # Simulate a row written by the OLD buggy code: status='resolved' with
    # a non-improved verdict. _ensure_schema()'s one-time backfill must
    # correct it the next time it runs, not just prevent new bad rows.
    from backend.app.db import appdb

    tracking._ensure_schema()
    appdb.run_query(
        """
        INSERT INTO tracked_findings
            (metric_id, period, category, detected_at, baseline_value, detected_value,
             detected_pct_change, status, verdict)
        VALUES ('revenue', '2026-07', 'critical', :now, 10000000, 12000000, 20.0, 'resolved', 'worsened')
        """,
        {"now": "2026-08-01T00:00:00"},
    )

    tracking._SCHEMA_READY = False
    tracking._ensure_schema()

    rows = appdb.run_query("SELECT status, verdict FROM tracked_findings WHERE metric_id = 'revenue'")
    assert rows[0]["status"] == "checked_unresolved"
    assert rows[0]["verdict"] == "worsened"


def test_check_outcomes_action_status_is_none_when_no_action_logged(appdb_reset, monkeypatch):
    tracking.record_new_findings([_investigation("revenue", "2026-08", 8_000_000, 10_000_000, -20.0)])

    monkeypatch.setattr(
        "backend.app.services.tracking.intelligence.get_trend",
        lambda metric: [{"month": "2026-09", "value": 9_800_000, "is_partial": False}],
    )
    monkeypatch.setattr(
        "backend.app.services.tracking.intelligence.METRICS", {"revenue": type("M", (), {"label": "Revenue", "unit": "Rs"})()}
    )
    tracking.check_outcomes()

    history = tracking.get_tracking_history()
    assert history[0]["action_status_at_check"] is None
