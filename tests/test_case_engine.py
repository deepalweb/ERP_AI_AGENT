"""Tests for case_engine.py (Phase 2a of the Business Case Store).

Case identity is (metric_id, domain, horizon) -- the finding's own real,
already-emitted reporting scope, never a per-segment entity ID (see the
module docstring). These tests validate the five points the design was
corrected on: granularity/scope (segments are evidence, not a splitting
key), deterministic matching with no transitive merging, MVP resolution
(absence alone / improved-verdict alone are both rejected as sole closure
proof), recurrence vs. reopening, and idempotency/uniqueness.
"""

import json
from datetime import date, timedelta

import pytest

from backend.app.db import appdb
from backend.app.services import case_engine


def _fake_date_sequence(days):
    """A callable matching case_engine._business_today()'s signature,
    advancing through `days` once per call (holding on the last one if
    called more times than provided) -- lets a test drive multiple
    sync_cases() calls as if they ran on distinct, controlled business
    calendar days. Patched in directly (not via the stdlib `date` class)
    since _business_today() is the one seam case_engine actually calls."""
    calls = {"i": 0}

    def _next():
        i = min(calls["i"], len(days) - 1)
        calls["i"] += 1
        return days[i]

    return _next


def _monthly_finding(metric_id, domain, period, category="critical", checks=None, current=100,
                      priority="high", impact=50.0):
    return {
        "id": f"{metric_id}-{period}",
        "metric_id": metric_id,
        "domain": domain,
        "category": category,
        "priority": priority,
        "business_impact_score": impact,
        "confirm": {"period": period, "current": current},
        "root_cause": {"checks": checks or []},
    }


def _weekly_finding(metric_id, domain, as_of_date, category="critical", checks=None, current=10):
    return {
        "id": f"{metric_id}-trailing-{as_of_date}",
        "metric_id": metric_id,
        "domain": domain,
        "horizon": "weekly",
        "category": category,
        "confirm": {"current": current},
        "root_cause": {"checks": checks or []},
    }


# ---------------------------------------------------------------------------
# 1. Granularity and scope: segments are evidence, never a splitting key.
# ---------------------------------------------------------------------------

def test_single_finding_multi_segment_scope_is_one_case_with_evidence_preserved(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    checks = [{
        "source": "store_concentration", "verdict": "confirmed",
        "segments": ["Celeste Daily Colombo 6", "Celeste Daily Ethul Kotte"],
    }]
    f = _monthly_finding("revenue", "financial", "2026-09", checks=checks)

    case_engine.sync_cases([f], [], [])

    # One case, not two -- a finding containing two implicated outlets does
    # not fork into per-outlet cases.
    assert case_engine.get_case(f["case_id"]) is not None
    case = case_engine.get_case(f["case_id"])
    assert case["scope_status"] == "segment_evidenced"
    segments = json.loads(case["scope_segments"])
    assert {s["segment_name"] for s in segments} == {"Celeste Daily Colombo 6", "Celeste Daily Ethul Kotte"}


def test_explicit_company_wide_scope_is_distinguished_from_unknown(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    company_wide_checks = [{"source": "store_concentration", "verdict": "no_evidence_found"}]
    f1 = _monthly_finding("revenue", "financial", "2026-09", checks=company_wide_checks)
    f2 = _monthly_finding("gross_margin", "financial", "2026-09", checks=[])

    case_engine.sync_cases([f1, f2], [], [])

    assert case_engine.get_case(f1["case_id"])["scope_status"] == "company_wide"
    assert case_engine.get_case(f2["case_id"])["scope_status"] == "unknown"


def test_a_finding_with_no_root_cause_concept_does_not_crash_sync(appdb_reset, monkeypatch):
    # Some finding types (e.g. state_findings._top_seller_stock_finding)
    # legitimately set root_cause to None, not {"checks": []} -- a
    # concentration check would be meaningless for them by construction.
    # Found via the Controlled Pilot real full-stack test: the live daily
    # scan crashed on exactly this shape (_scope_from_checks called
    # finding.get("root_cause", {}).get("checks", []), which only
    # substitutes {} when the key is ABSENT, not when it's present as
    # None) the moment a real one of these findings actually fired.
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    f = _monthly_finding("revenue", "financial", "2026-09")
    f["root_cause"] = None

    case_engine.sync_cases([f], [], [])

    case = case_engine.get_case(f["case_id"])
    assert case is not None
    assert case["scope_status"] == "unknown"


def test_different_metrics_sharing_a_segment_are_never_merged(appdb_reset, monkeypatch):
    # Two DIFFERENT metrics whose concentration checks cite the exact same
    # outlet must still be two distinct cases -- matching is never based
    # on shared segments, only on (metric_id, domain, horizon).
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    checks = [{"source": "store_concentration", "verdict": "confirmed", "segments": ["Celeste Daily Ethul Kotte"]}]
    revenue = _monthly_finding("revenue", "financial", "2026-09", checks=checks)
    order_volume = _monthly_finding("order_volume", "order_customer", "2026-09", checks=checks)

    case_engine.sync_cases([revenue, order_volume], [], [])

    assert revenue["case_id"] != order_volume["case_id"]


def test_monthly_and_weekly_findings_for_the_same_metric_are_separate_cases(appdb_reset, monkeypatch):
    # Different reporting grain (horizon) is real, structural, and already
    # emitted by investigation.py -- treated as part of case identity.
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    monthly = _monthly_finding("cancellation_rate", "order_customer", "2026-09")
    weekly = _weekly_finding("cancellation_rate", "order_customer", "2026-09-15")

    case_engine.sync_cases([monthly], [], [weekly])

    assert monthly["case_id"] != weekly["case_id"]


# ---------------------------------------------------------------------------
# 2. Matching: continuation vs. new case, no ambiguous auto-attach.
# ---------------------------------------------------------------------------

def test_continuation_across_two_scans_is_one_case(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15), date(2026, 9, 16)]))

    f1 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f1], [], [])

    f2 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f2], [], [])

    assert f1["case_id"] == f2["case_id"]
    assert len(case_engine.get_case_observations(f1["case_id"])) == 2


def test_active_case_uniqueness_blocks_a_second_open_row_including_reopened(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    f = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f], [], [])
    case_engine.dismiss_case(f["case_id"], "test")
    case_engine._reopen_case(f["case_id"], f, "unknown", [], "test reopen")

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        appdb.run_query(
            "INSERT INTO business_cases (case_id, metric_id, domain, horizon, status, opened_at, last_seen_at) "
            "VALUES ('some-other-case', 'revenue', 'financial', 'monthly', 'open', 'x', 'x')"
        )


# ---------------------------------------------------------------------------
# 3. Resolution: absence alone / improved-verdict alone are both rejected.
# ---------------------------------------------------------------------------

def test_still_flagged_case_stays_open_regardless_of_trend(appdb_reset, monkeypatch):
    # Two scans in a row where the metric is STILL flagged (even if the raw
    # number is trending better) must never enter verification/resolution --
    # "2 consecutive improved verdicts while still flagged" is rejected.
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1), date(2026, 9, 2)]))
    f1 = _monthly_finding("cancellation_rate", "order_customer", "2026-09", current=15)
    case_engine.sync_cases([f1], [], [])

    f2 = _monthly_finding("cancellation_rate", "order_customer", "2026-09", current=13)
    case_engine.sync_cases([f2], [], [])

    case = case_engine.get_case(f1["case_id"])
    assert case["status"] == "open"
    assert case["verification_progress"] == 0


def test_single_absent_cycle_does_not_resolve(appdb_reset, monkeypatch):
    # "Metric absent from the next scan" alone is rejected as automatic
    # closure proof -- one clean cadence unit only reaches verification_pending.
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1), date(2026, 9, 2)]))

    class _M:
        label = "Cancellation rate"

    monkeypatch.setattr(case_engine.intelligence, "METRICS", {"cancellation_rate": _M()})
    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-09", "value": 1, "is_partial": False}])

    f = _monthly_finding("cancellation_rate", "order_customer", "2026-09")
    case_engine.sync_cases([f], [], [])
    case_id = f["case_id"]

    case_engine.sync_cases([], [], [])  # no longer flagged -- one clean cadence unit

    case = case_engine.get_case(case_id)
    assert case["status"] == "verification_pending"
    assert case["verification_progress"] == 1


def test_two_consecutive_clean_cadence_units_resolves(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "date",
                         _fake_date_sequence([date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]))

    class _M:
        label = "Revenue"

    monkeypatch.setattr(case_engine.intelligence, "METRICS", {"revenue": _M()})

    f = _monthly_finding("revenue", "financial", "2026-07")
    case_engine.sync_cases([f], [], [])
    case_id = f["case_id"]

    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-08", "value": 1, "is_partial": False}])
    case_engine.sync_cases([], [], [])
    assert case_engine.get_case(case_id)["status"] == "verification_pending"

    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-09", "value": 1, "is_partial": False}])
    case_engine.sync_cases([], [], [])
    case = case_engine.get_case(case_id)
    assert case["status"] == "resolved"
    assert case["closed_at"] is not None


def test_reappearing_before_verification_completes_resets_progress(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "date",
                         _fake_date_sequence([date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]))

    class _M:
        label = "Revenue"

    monkeypatch.setattr(case_engine.intelligence, "METRICS", {"revenue": _M()})

    f = _monthly_finding("revenue", "financial", "2026-07")
    case_engine.sync_cases([f], [], [])
    case_id = f["case_id"]

    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-08", "value": 1, "is_partial": False}])
    case_engine.sync_cases([], [], [])
    assert case_engine.get_case(case_id)["status"] == "verification_pending"

    # Flagged again before the window completed.
    f2 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f2], [], [])
    case = case_engine.get_case(case_id)
    assert case["status"] == "open"
    assert case["verification_progress"] == 0
    assert f2["case_id"] == case_id


# ---------------------------------------------------------------------------
# 4. Recurrence vs. reopening.
# ---------------------------------------------------------------------------

def test_recurrence_after_verified_resolution_creates_new_linked_case(appdb_reset, monkeypatch):
    days = [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1), date(2026, 9, 20)]
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence(days))

    class _M:
        label = "Revenue"

    monkeypatch.setattr(case_engine.intelligence, "METRICS", {"revenue": _M()})

    f = _monthly_finding("revenue", "financial", "2026-07")
    case_engine.sync_cases([f], [], [])
    original_case_id = f["case_id"]

    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-08", "value": 1, "is_partial": False}])
    case_engine.sync_cases([], [], [])
    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-09", "value": 1, "is_partial": False}])
    case_engine.sync_cases([], [], [])
    assert case_engine.get_case(original_case_id)["status"] == "resolved"

    # Recurs within the grace window, after a GENUINE (verified) resolution.
    f2 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f2], [], [])

    assert f2["case_id"] != original_case_id
    relationships = case_engine.get_case_relationships(f2["case_id"])
    assert any(
        r["related_case_id"] == original_case_id and r["relationship_type"] == "recurrence_of"
        for r in relationships
    )
    # The original case's own record is untouched by the new incident.
    original = case_engine.get_case(original_case_id)
    assert original["status"] == "resolved"


def test_dismissed_case_reopens_on_recurrence_instead_of_forking(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1), date(2026, 9, 10)]))

    f = _monthly_finding("purchase_price_variance", "supplier", "2026-09")
    case_engine.sync_cases([f], [], [])
    case_id = f["case_id"]
    case_engine.dismiss_case(case_id, "known one-off pricing correction")

    f2 = _monthly_finding("purchase_price_variance", "supplier", "2026-09")
    case_engine.sync_cases([f2], [], [])

    assert f2["case_id"] == case_id  # reopened, not forked
    case = case_engine.get_case(case_id)
    assert case["status"] == "reopened"
    history = case_engine.get_case_history(case_id)
    assert any(h["event_type"] == "reopened" for h in history)
    # No recurrence_of relationship for a reopen -- it's the same incident.
    assert case_engine.get_case_relationships(case_id) == []


# ---------------------------------------------------------------------------
# 5. Idempotency and observation-level uniqueness.
# ---------------------------------------------------------------------------

def test_rerunning_sync_for_the_same_scan_run_does_not_duplicate_observations(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f1], [], [])
    f2 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f2], [], [])  # same calendar day -- e.g. a retry

    assert len(case_engine.get_case_observations(f1["case_id"])) == 1


def test_distinct_daily_scans_append_observations_even_with_identical_finding_id(appdb_reset, monkeypatch):
    # A monthly finding's id ("revenue-2026-09") is identical for every
    # day within that month -- scan_run_id, not finding_id, is the real
    # idempotency key.
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1), date(2026, 9, 2)]))
    f1 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f1], [], [])
    f2 = _monthly_finding("revenue", "financial", "2026-09")
    assert f1["id"] == f2["id"]
    case_engine.sync_cases([f2], [], [])

    assert len(case_engine.get_case_observations(f1["case_id"])) == 2


def test_dismissing_a_finding_action_never_touches_a_case(appdb_reset, monkeypatch):
    # Dismissing one proposed action is not the same operation as
    # dismissing a case -- finding_actions has no case awareness at all
    # in Phase 2a, so setting one has zero effect on business_cases.
    from backend.app.services import finding_actions

    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1)]))
    f = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f], [], [])

    finding_actions.set_action(f["id"], False, "not_started", None)

    case = case_engine.get_case(f["case_id"])
    assert case["status"] == "open"


# ---------------------------------------------------------------------------
# Resolution freshness: a missing finding caused by stale/incomplete data
# must not count as recovery; monthly cadence must use distinct complete
# reporting months, not distinct daily scans of the same still-open month.
# ---------------------------------------------------------------------------

def test_stale_data_does_not_advance_a_weekly_cases_verification(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1), date(2026, 9, 2)]))

    f = _weekly_finding("cancellation_rate", "order_customer", "2026-09-01")
    case_engine.sync_cases([], [], [f])
    case_id = f["case_id"]

    # No longer flagged, but today's order data is stale -- an "absent"
    # finding here might just be re-observing yesterday's stale snapshot.
    case_engine.sync_cases([], [], [], data_fresh=False)

    case = case_engine.get_case(case_id)
    assert case["status"] == "open"
    assert case["verification_progress"] == 0


def test_fresh_data_still_advances_a_weekly_cases_verification(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1), date(2026, 9, 2)]))

    f = _weekly_finding("cancellation_rate", "order_customer", "2026-09-01")
    case_engine.sync_cases([], [], [f])
    case_id = f["case_id"]

    case_engine.sync_cases([], [], [], data_fresh=True)

    case = case_engine.get_case(case_id)
    assert case["status"] == "verification_pending"
    assert case["verification_progress"] == 1


def test_same_still_open_month_rerun_does_not_double_count_progress(appdb_reset, monkeypatch):
    # Two daily scans that both land on the SAME (still the latest,
    # not-yet-superseded) trend month must credit verification exactly
    # once, not once per scan -- only a genuinely new complete month
    # counts as a fresh cadence unit.
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_date_sequence([date(2026, 7, 1), date(2026, 8, 1), date(2026, 8, 2)]))

    class _M:
        label = "Revenue"

    monkeypatch.setattr(case_engine.intelligence, "METRICS", {"revenue": _M()})
    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-08", "value": 1, "is_partial": False}])

    f = _monthly_finding("revenue", "financial", "2026-07")
    case_engine.sync_cases([f], [], [])
    case_id = f["case_id"]

    case_engine.sync_cases([], [], [])  # day 1 of August: absent, fresh unit "2026-08"
    assert case_engine.get_case(case_id)["verification_progress"] == 1

    case_engine.sync_cases([], [], [])  # day 2 of August: same trend row, same month
    case = case_engine.get_case(case_id)
    assert case["verification_progress"] == 1  # NOT 2
    assert case["status"] == "verification_pending"  # not resolved yet


# ---------------------------------------------------------------------------
# Same-day rescan: idempotent for identical evidence, but a later same-day
# scan with genuinely newer evidence must not be silently discarded.
# ---------------------------------------------------------------------------

def test_same_day_rescan_with_newer_evidence_overwrites_the_days_observation(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1)]))

    f1 = _monthly_finding("revenue", "financial", "2026-09", current=8_000_000)
    case_engine.sync_cases([f1], [], [])

    f2 = _monthly_finding("revenue", "financial", "2026-09", current=7_500_000)  # a later same-day rescan
    case_engine.sync_cases([f2], [], [])

    observations = case_engine.get_case_observations(f1["case_id"])
    assert len(observations) == 1  # still one row for the day, not two
    snapshot = json.loads(observations[0]["snapshot"])
    assert snapshot["confirm"]["current"] == 7_500_000  # the LATER value, not the first


def test_business_timezone_default_is_asia_colombo(appdb_reset, monkeypatch):
    captured = {}

    class _FakeDateTime:
        @staticmethod
        def now(tz):
            captured["tz"] = tz
            from datetime import datetime as _real_datetime
            return _real_datetime(2026, 9, 1, 12, 0, tzinfo=tz)

    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    monkeypatch.setattr(case_engine, "datetime", _FakeDateTime)

    result = case_engine._business_today()

    assert str(captured["tz"]) == "Asia/Colombo"
    assert result == date(2026, 9, 1)


def test_business_timezone_honours_app_timezone_env_var(appdb_reset, monkeypatch):
    captured = {}

    class _FakeDateTime:
        @staticmethod
        def now(tz):
            captured["tz"] = tz
            from datetime import datetime as _real_datetime
            return _real_datetime(2026, 9, 1, 12, 0, tzinfo=tz)

    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    monkeypatch.setattr(case_engine, "datetime", _FakeDateTime)

    case_engine._business_today()

    assert str(captured["tz"]) == "UTC"


# ---------------------------------------------------------------------------
# Legacy tracking (tracking.py/state_tracking.py) vs. the Case Store: the
# two must never be conflated -- business_cases.status is authoritative
# for case-level resolution regardless of what the legacy tables say.
# ---------------------------------------------------------------------------

def test_legacy_improved_status_does_not_resolve_the_case(appdb_reset, monkeypatch):
    from backend.app.services import tracking

    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 8, 1), date(2026, 9, 1)]))

    f = _monthly_finding("revenue", "financial", "2026-08", current=8_000_000)
    case_engine.sync_cases([f], [], [])
    case_id = f["case_id"]

    # The LEGACY tracker independently marks this finding as a verified
    # improvement -- must have zero effect on the Case Store's own status.
    tracking.record_new_findings([{
        "metric_id": "revenue", "category": "critical",
        "confirm": {"period": "2026-08", "current": 8_000_000, "baseline": 10_000_000, "pct_change": -20.0},
        "recommendations": [],
    }])
    monkeypatch.setattr(tracking.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-09", "value": 9_900_000, "is_partial": False}])
    monkeypatch.setattr(tracking.intelligence, "METRICS",
                         {"revenue": type("M", (), {"label": "Revenue", "unit": "Rs"})()})
    tracking.check_outcomes()
    legacy_row = tracking.get_tracking_history()[0]
    assert legacy_row["status"] == "resolved"
    assert legacy_row["verdict"] == "improved"

    # The case itself is still open -- it was never touched by tracking.py.
    case = case_engine.get_case(case_id)
    assert case["status"] == "open"


def test_case_resolved_status_does_not_imply_a_legacy_verdict_exists(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         _fake_date_sequence([date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]))

    class _M:
        label = "Revenue"

    monkeypatch.setattr(case_engine.intelligence, "METRICS", {"revenue": _M()})

    f = _monthly_finding("revenue", "financial", "2026-07")
    case_engine.sync_cases([f], [], [])
    case_id = f["case_id"]

    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-08", "value": 1, "is_partial": False}])
    case_engine.sync_cases([], [], [])
    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-09", "value": 1, "is_partial": False}])
    case_engine.sync_cases([], [], [])

    assert case_engine.get_case(case_id)["status"] == "resolved"

    # The legacy tracked_findings table was never written to by case_engine.
    from backend.app.services import tracking
    assert tracking.get_tracking_history() == []


# ---------------------------------------------------------------------------
# Notification diffing agrees with case identity: a continuing case is
# never re-flagged as "new"; a genuinely new (or reopened) case is.
# ---------------------------------------------------------------------------

def test_notifications_do_not_re_flag_a_continuing_case_as_new(appdb_reset, monkeypatch):
    from backend.app.services import notifications

    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1), date(2026, 9, 2)]))

    scan1 = [_monthly_finding("revenue", "financial", "2026-09", impact=10.0)]
    case_engine.sync_cases(scan1, [], [])
    case_id_1 = scan1[0]["case_id"]

    scan2 = [_monthly_finding("revenue", "financial", "2026-09", impact=10.0)]
    case_engine.sync_cases(scan2, [], [])

    assert scan2[0]["case_id"] == case_id_1  # same case, continuation
    new_findings = notifications.diff_new_critical_findings(scan2, [], [], scan1, [], [])
    assert new_findings == []  # not re-flagged as new


def test_notifications_flag_a_genuinely_new_case_as_new(appdb_reset, monkeypatch):
    from backend.app.services import notifications

    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 1)]))

    prev_scan = [_monthly_finding("cancellation_rate", "order_customer", "2026-08", impact=10.0)]
    new_scan = [_monthly_finding("revenue", "financial", "2026-09", impact=10.0)]
    case_engine.sync_cases(new_scan, [], [])

    new_findings = notifications.diff_new_critical_findings(new_scan, [], [], prev_scan, [], [])
    assert len(new_findings) == 1
    assert new_findings[0]["metric_id"] == "revenue"


# ---------------------------------------------------------------------------
# save_ad_hoc_case (Completed Stock-Count Variance & Shrinkage Investigation)
# -- the one genuinely new case-creation path in this module, letting an
# Ask Celeste "save as case" action create/update a case OUTSIDE the daily
# scan. Reuses _ingest_finding exactly as sync_cases() does.

def _adhoc_finding(metric_id, current=100, impact=50.0):
    return {
        "id": f"{metric_id}-2026-09-15",
        "metric_id": metric_id,
        "domain": "inventory",
        "category": "critical",
        "priority": "high",
        "business_impact_score": impact,
        "confirm": {"period": "2026-09-15", "current": current},
        "root_cause": {"checks": []},
    }


def test_save_ad_hoc_case_creates_a_new_open_snapshot_case(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    case_id = case_engine.save_ad_hoc_case(_adhoc_finding("completed_stock_count_variance-all-2026-06_2026-09"))

    case = case_engine.get_case(case_id)
    assert case["status"] == "open"
    assert case["horizon"] == "snapshot"
    assert case["metric_id"] == "completed_stock_count_variance-all-2026-06_2026-09"
    assert case["domain"] == "inventory"

    detail = case_engine.get_case_detail(case_id)
    assert detail["history"][0]["event_type"] == "opened"


def test_save_ad_hoc_case_reuses_the_same_case_for_the_same_scope(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    finding = _adhoc_finding("completed_stock_count_variance-all-2026-06_2026-09")

    case_id_1 = case_engine.save_ad_hoc_case(finding)
    case_id_2 = case_engine.save_ad_hoc_case(finding)
    assert case_id_1 == case_id_2


def test_save_ad_hoc_case_with_a_different_scope_opens_a_different_case(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    case_id_colombo = case_engine.save_ad_hoc_case(_adhoc_finding("completed_stock_count_variance-1-2026-06_2026-09"))
    case_id_attidiya = case_engine.save_ad_hoc_case(_adhoc_finding("completed_stock_count_variance-3-2026-06_2026-09"))
    assert case_id_colombo != case_id_attidiya


def test_save_ad_hoc_case_requires_a_metric_id(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", _fake_date_sequence([date(2026, 9, 15)]))
    with pytest.raises(KeyError):
        case_engine.save_ad_hoc_case({"domain": "inventory"})
