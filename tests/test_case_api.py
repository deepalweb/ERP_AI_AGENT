"""Integration tests for Phase 2b: the /cases API surface, the finding_actions
<-> business_cases link, and backward compatibility with /brief.

Calls the FastAPI endpoint functions directly (await main.list_cases(...),
etc.) against a real, isolated appdb (appdb_reset) -- the same pattern
test_scan_pipeline.py already uses for /investigations. Authorization
itself (can an unauthenticated request even reach these functions) is
covered separately in test_access_control.py, which already tests the
ASGI/middleware layer for every write route the same way.
"""

import datetime
import json

import pytest

from backend.app import main
from backend.app.services import case_engine, finding_actions
from tests.coverage_helpers import sync_covered_cases


def _monthly_finding(metric_id, domain, period, category="critical", checks=None, current=100, impact=50.0,
                      title=None, priority="high"):
    baseline = current * 1.2
    return {
        "id": f"{metric_id}-{period}",
        "metric_id": metric_id,
        "domain": domain,
        "category": category,
        "priority": priority,
        "business_impact_score": impact,
        "title": title or f"{metric_id} moved",
        "confirm": {
            "period": period, "current": current, "baseline": baseline,
            "pct_change": (current - baseline) / baseline * 100 if baseline else 0.0,
        },
        "root_cause": {"checks": checks or []},
    }


@pytest.fixture
def two_scans(appdb_reset, monkeypatch):
    """A case that lives across two scan cycles, matching what brain.py
    actually produces: sync_cases() mutates the finding list in place."""
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 15))
    f1 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f1], [], [])
    return f1["case_id"]


# ---------------------------------------------------------------------------
# GET /cases and GET /cases/{id}
# ---------------------------------------------------------------------------

async def test_list_cases_returns_the_synced_case(two_scans):
    result = await main.list_cases()
    assert result["status"] == "ok"
    assert result["total"] == 1
    assert result["items"][0]["case_id"] == two_scans


async def test_list_cases_surfaces_the_top_contributor_for_where_context(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 15))
    finding = _monthly_finding("negative_stock_control", "controls", "2026-09")
    finding["breakdowns"] = [{
        "dimension": "store", "label": "Affected products by outlet",
        "top_contributors": [{"name": "Ethul Kotte", "current": 28, "delta": 28}],
    }]
    case_engine.sync_cases([finding], [], [])

    result = await main.list_cases()
    assert result["items"][0]["latest"]["top_contributor"] == {"dimension": "store", "name": "Ethul Kotte", "value": 28}


async def test_list_cases_top_contributor_is_none_without_a_breakdown(two_scans):
    result = await main.list_cases()
    assert result["items"][0]["latest"]["top_contributor"] is None


async def test_list_cases_filters_by_status_and_metric(two_scans):
    by_status = await main.list_cases(status="open")
    assert by_status["total"] == 1
    by_wrong_status = await main.list_cases(status="resolved")
    assert by_wrong_status["total"] == 0
    by_metric = await main.list_cases(metric_id="order_volume")
    assert by_metric["total"] == 0


async def test_list_cases_filters_by_priority(two_scans):
    high = await main.list_cases(priority="high")
    assert high["total"] == 1
    critical = await main.list_cases(priority="critical")
    assert critical["total"] == 0


async def test_list_cases_sort_by_priority_ranks_critical_first(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 15))
    case_engine.sync_cases([_monthly_finding("gross_margin", "financial", "2026-09", priority="low")], [], [])
    case_engine.sync_cases([_monthly_finding("revenue", "financial", "2026-09", priority="critical")], [], [])
    case_engine.sync_cases([_monthly_finding("order_volume", "order_customer", "2026-09", priority="medium")], [], [])

    result = await main.list_cases(sort="priority")
    assert [c["metric_id"] for c in result["items"]] == ["revenue", "order_volume", "gross_margin"]


async def test_list_cases_sort_by_impact_ranks_highest_score_first(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 15))
    case_engine.sync_cases([_monthly_finding("gross_margin", "financial", "2026-09", impact=10.0)], [], [])
    case_engine.sync_cases([_monthly_finding("revenue", "financial", "2026-09", impact=90.0)], [], [])

    result = await main.list_cases(sort="impact")
    assert [c["metric_id"] for c in result["items"]] == ["revenue", "gross_margin"]


async def test_list_cases_pagination_is_stable_across_pages(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 15))
    metric_ids = ["revenue", "order_volume", "cancellation_rate", "gross_margin", "delayed_delivery_rate"]
    for i, metric_id in enumerate(metric_ids):
        domain = "financial" if i % 2 == 0 else "order_customer"
        case_engine.sync_cases([_monthly_finding(metric_id, domain, "2026-09")], [], [])

    page1 = await main.list_cases(limit=2, offset=0)
    page2 = await main.list_cases(limit=2, offset=2)
    page3 = await main.list_cases(limit=2, offset=4)
    assert page1["total"] == page2["total"] == page3["total"] == 5
    seen_ids = [c["case_id"] for c in page1["items"] + page2["items"] + page3["items"]]
    assert len(seen_ids) == len(set(seen_ids)) == 5  # no row repeated or skipped across pages


async def test_case_detail_returns_observations_history_and_actions(two_scans):
    finding_actions.set_action("revenue-2026-09", True, "in_progress", "checking with ops", case_id=two_scans)

    result = await main.get_case_detail(two_scans)
    assert result["status"] == "ok"
    assert result["case"]["case_id"] == two_scans
    assert len(result["observations"]) == 1
    assert len(result["performance_history"]) == 1
    assert result["performance_history"][0]["current"] == 100
    assert any(h["event_type"] == "opened" for h in result["history"])
    assert result["relationships"] == []
    assert len(result["actions"]) == 1
    assert result["actions"][0]["status"] == "in_progress"


async def test_case_detail_for_unknown_case_is_an_error(appdb_reset):
    result = await main.get_case_detail("does-not-exist")
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# finding_actions <-> case linkage
# ---------------------------------------------------------------------------

async def test_patch_finding_action_resolves_and_stores_case_id(two_scans):
    request = main.FindingActionRequest(relevant=True, status="in_progress", action_taken="looking into it")
    result = await main.update_finding_action("revenue-2026-09", request)
    assert result["status"] == "ok"

    actions = finding_actions.get_actions_for(["revenue-2026-09"])
    assert actions["revenue-2026-09"]["case_id"] == two_scans


async def test_action_kind_is_preserved_across_an_edit_that_omits_it(two_scans):
    finding_actions.set_action("revenue-2026-09", True, "not_started", None,
                                case_id=two_scans, action_kind="investigation")
    # A later edit through the existing form, which has no action_kind field.
    request = main.FindingActionRequest(relevant=True, status="in_progress", action_taken="update")
    await main.update_finding_action("revenue-2026-09", request)

    actions = finding_actions.get_actions_for(["revenue-2026-09"])
    assert actions["revenue-2026-09"]["action_kind"] == "investigation"  # not wiped
    assert actions["revenue-2026-09"]["status"] == "in_progress"


async def test_set_action_rejects_an_unknown_action_kind(appdb_reset):
    with pytest.raises(ValueError):
        finding_actions.set_action("revenue-2026-09", True, "not_started", None, action_kind="nonsense")


async def test_case_supports_several_investigation_and_corrective_actions(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today",
                         lambda seq=[datetime.date(2026, 7, 1), datetime.date(2026, 8, 1)]: seq.pop(0))

    f1 = _monthly_finding("revenue", "financial", "2026-07")
    case_engine.sync_cases([f1], [], [])
    case_id = f1["case_id"]
    f2 = _monthly_finding("revenue", "financial", "2026-08")
    case_engine.sync_cases([f2], [], [])
    assert f2["case_id"] == case_id  # continuation, same case

    finding_actions.set_action("revenue-2026-07", True, "completed", "checked the data pipeline",
                                case_id=case_id, action_kind="investigation")
    finding_actions.set_action("revenue-2026-08", True, "in_progress", "renegotiating supplier terms",
                                case_id=case_id, action_kind="corrective")

    actions = finding_actions.get_actions_for_case(case_id)
    assert len(actions) == 2
    kinds = {a["finding_id"]: a["action_kind"] for a in actions}
    assert kinds == {"revenue-2026-07": "investigation", "revenue-2026-08": "corrective"}


async def test_dismissing_one_action_leaves_the_case_open(two_scans):
    finding_actions.set_action("revenue-2026-09", False, "not_started", None, case_id=two_scans)
    case = case_engine.get_case(two_scans)
    assert case["status"] == "open"


# ---------------------------------------------------------------------------
# Case-level dismiss / reopen via the API
# ---------------------------------------------------------------------------

async def test_dismiss_case_endpoint_requires_a_reason_and_logs_history(two_scans):
    empty = await main.dismiss_case_endpoint(two_scans, main.CaseReasonRequest(reason="   "))
    assert empty["status"] == "error"
    assert case_engine.get_case(two_scans)["status"] == "open"

    ok = await main.dismiss_case_endpoint(two_scans, main.CaseReasonRequest(reason="known seasonal blip"))
    assert ok["status"] == "ok"
    case = case_engine.get_case(two_scans)
    assert case["status"] == "dismissed"
    assert case["dismissed_reason"] == "known seasonal blip"
    history = case_engine.get_case_history(two_scans)
    assert any(h["event_type"] == "dismissed" for h in history)


async def test_reopen_case_endpoint_requires_a_closed_case(two_scans):
    still_open = await main.reopen_case_endpoint(two_scans, main.CaseReasonRequest(reason="test"))
    assert still_open["status"] == "error"

    await main.dismiss_case_endpoint(two_scans, main.CaseReasonRequest(reason="test dismissal"))
    reopened = await main.reopen_case_endpoint(two_scans, main.CaseReasonRequest(reason="turned out to be real"))
    assert reopened["status"] == "ok"
    case = case_engine.get_case(two_scans)
    assert case["status"] == "reopened"
    assert case["dismissed_reason"] is None


# ---------------------------------------------------------------------------
# Duplicate scans and backward compatibility.
# ---------------------------------------------------------------------------

async def test_duplicate_same_day_scan_does_not_duplicate_observations_or_actions(two_scans):
    finding_actions.set_action("revenue-2026-09", True, "in_progress", "x", case_id=two_scans)

    f_again = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f_again], [], [])  # same business day as the fixture's scan

    assert len(case_engine.get_case_observations(two_scans)) == 1
    # sync_cases never touches finding_actions -- re-running it can't
    # create, duplicate, or alter an action row.
    from backend.app.db import appdb
    rows = appdb.run_query("SELECT COUNT(*) AS n FROM finding_actions")
    assert rows[0]["n"] == 1


async def test_brief_response_gains_case_id_without_losing_existing_fields(appdb_reset, monkeypatch):
    monkeypatch.setattr(main.brain, "_SCHEMA_READY", False)
    monkeypatch.setattr(main.brain.investigation, "scan_all", lambda: [_monthly_finding("revenue", "financial", "2026-09")])
    monkeypatch.setattr(main.brain.investigation, "scan_short_term", list)
    monkeypatch.setattr(main.brain.investigation, "generate_daily_synthesis", lambda _: None)
    monkeypatch.setattr(main.brain.state_findings, "build_inventory_findings", list)
    monkeypatch.setattr(main.brain.state_findings, "build_operational_findings", list)
    monkeypatch.setattr(main.brain.incident_engine, "build_incident_findings", list)
    monkeypatch.setattr(main.brain, "_snapshot_health", dict)
    monkeypatch.setattr(main.brain, "_data_freshness", dict)
    predictions = main.brain.customer_predictions
    monkeypatch.setattr(predictions, "compute_predictions", list)
    monkeypatch.setattr(predictions, "record_predictions", lambda _: None)
    monkeypatch.setattr(predictions, "compute_pattern_stats", lambda _: {})
    monkeypatch.setattr(predictions, "record_pattern_stats", lambda _: None)
    monkeypatch.setattr(predictions, "get_summary", dict)
    monkeypatch.setattr(predictions, "build_home_finding", lambda _: None)
    monkeypatch.setattr(predictions, "compute_lifecycle_summary", dict)
    monkeypatch.setattr(predictions, "record_lifecycle_summary", lambda _: None)
    monkeypatch.setattr(predictions, "compute_lifecycle_snapshot_rows", list)
    monkeypatch.setattr(predictions, "record_lifecycle_snapshot", lambda _: None)
    monkeypatch.setattr(main.brain, "_record_inventory_kpi_snapshot", lambda: None)

    # Home now returns 202 on a cold cache. This compatibility test exercises
    # the completed snapshot; cold/background transitions have their own tests.
    main.brain.run_daily_scan()
    response = await main.daily_brief()

    assert response["status"] == "ok"
    # Every pre-existing top-level key /brief consumers already read.
    for key in ("investigations", "tracking", "state_findings", "short_term_findings", "health",
                "short_term_resolved", "customer_finding", "new_critical_findings", "state_tracking",
                "streak_outcomes", "data_freshness", "synthesis"):
        assert key in response
    assert response["investigations"][0]["metric_id"] == "revenue"
    assert "case_id" in response["investigations"][0]  # the additive field, present


async def test_existing_historical_action_without_case_id_is_still_readable(appdb_reset):
    # Simulates a row written before case_id/action_kind existed.
    finding_actions.set_action("revenue-2026-01", True, "completed", "old action from before Case Store")
    actions = finding_actions.get_actions_for(["revenue-2026-01"])
    assert actions["revenue-2026-01"]["status"] == "completed"
    assert actions["revenue-2026-01"]["case_id"] is None
    assert actions["revenue-2026-01"]["action_kind"] is None


# ---------------------------------------------------------------------------
# Phase 2c Step 1, point 1: same-day upserts must not destroy the evidence
# behind an already-made management decision.
# ---------------------------------------------------------------------------

async def test_saving_an_action_preserves_that_days_evidence_even_after_a_later_rescan(two_scans, monkeypatch):
    # Morning: manager reviews the case as it stands and records a decision.
    request = main.FindingActionRequest(relevant=True, status="in_progress", action_taken="escalated to ops")
    await main.update_finding_action("revenue-2026-09", request)

    # Afternoon, same business day: a rescan produces materially different
    # numbers for the SAME finding_id -- overwrites case_observations by
    # design (the "newer evidence must not be silently discarded" fix).
    f_afternoon = _monthly_finding("revenue", "financial", "2026-09", current=6_000_000)
    case_engine.sync_cases([f_afternoon], [], [])

    # The rolling observation now shows the afternoon's number...
    detail = await main.get_case_detail(two_scans)
    assert detail["performance_history"][-1]["current"] == 6_000_000

    # ...but the decision_evidence history entry still shows EXACTLY what
    # was true when the manager actually acted, unchanged by the rescan.
    decision_events = [h for h in detail["history"] if h["event_type"] == "decision_evidence"]
    assert len(decision_events) == 1
    preserved = json.loads(decision_events[0]["detail"])
    assert preserved["snapshot"]["confirm"]["current"] == 100  # the morning's value, from the fixture
    assert preserved["action_status"] == "in_progress"


async def test_no_decision_snapshot_is_recorded_when_the_finding_has_no_case_yet(appdb_reset):
    # An action saved for a finding_id that was never synced (edge case,
    # e.g. a stale card) must not raise or fabricate a snapshot.
    request = main.FindingActionRequest(relevant=True, status="in_progress", action_taken="test")
    result = await main.update_finding_action("unknown-metric-2026-09", request)
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Phase 2c Step 1, point 2: recurring cases must not inherit unrelated
# actions from a previous, distinct incident.
# ---------------------------------------------------------------------------

async def test_a_recurring_case_does_not_inherit_the_old_cases_actions(appdb_reset, monkeypatch):
    days = [datetime.date(2026, 7, 1), datetime.date(2026, 8, 1), datetime.date(2026, 9, 1), datetime.date(2026, 9, 10)]
    monkeypatch.setattr(case_engine, "_business_today", lambda seq=list(days): seq.pop(0))

    class _M:
        label = "Revenue"

    monkeypatch.setattr(case_engine.intelligence, "METRICS", {"revenue": _M()})

    f = _monthly_finding("revenue", "financial", "2026-07")
    case_engine.sync_cases([f], [], [])
    old_case_id = f["case_id"]
    finding_actions.set_action("revenue-2026-07", True, "completed", "fixed the July issue",
                                case_id=old_case_id, action_kind="corrective")

    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-08", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])
    monkeypatch.setattr(case_engine.intelligence, "get_trend",
                         lambda metric: [{"month": "2026-09", "value": 1, "is_partial": False}])
    sync_covered_cases([], [], [])
    assert case_engine.get_case(old_case_id)["status"] == "resolved"

    f2 = _monthly_finding("revenue", "financial", "2026-09")
    case_engine.sync_cases([f2], [], [])
    new_case_id = f2["case_id"]
    assert new_case_id != old_case_id

    # The new (recurring) case has zero actions -- the July action stays
    # attached only to the case it actually belongs to.
    assert finding_actions.get_actions_for_case(new_case_id) == []
    old_actions = finding_actions.get_actions_for_case(old_case_id)
    assert len(old_actions) == 1
    assert old_actions[0]["finding_id"] == "revenue-2026-07"

    # And an action later saved against the NEW case's finding_id resolves
    # to the new case, not the old one.
    assert case_engine.get_case_id_for_finding("revenue-2026-09") == new_case_id
    assert case_engine.get_case_id_for_finding("revenue-2026-07") == old_case_id


# ---------------------------------------------------------------------------
# Phase 2c Step 1, point 3: the API must not represent rolling daily
# snapshots as a complete immutable evidence log.
# ---------------------------------------------------------------------------

async def test_case_detail_honestly_labels_observations_as_not_a_complete_log(two_scans):
    detail = await main.get_case_detail(two_scans)
    assert detail["observations_note"] == case_engine.OBSERVATIONS_NOTE
    assert "not a complete" in detail["observations_note"] or "not every scan" in detail["observations_note"]


# ---------------------------------------------------------------------------
# Phase 2c Step 1, point 4: the API contract must give the UI what it needs
# without the frontend re-deriving business logic (title, current metric
# reading) from raw finding JSON itself.
# ---------------------------------------------------------------------------

async def test_list_cases_exposes_a_latest_summary_per_case(two_scans):
    result = await main.list_cases()
    item = result["items"][0]
    assert item["latest"]["title"] == "revenue moved"
    assert item["latest"]["confirm"]["current"] == 100
    assert item["latest"]["category"] == "critical"


async def test_list_cases_action_is_none_before_any_save(two_scans):
    result = await main.list_cases()
    assert result["items"][0]["action"] is None


async def test_list_cases_exposes_the_latest_action_for_the_actions_and_outcomes_workspaces(two_scans):
    # Frontend Experience v2, Stage 1: the Actions/Outcomes list views need
    # a case's action state without an N+1 fetch per case (main.py's
    # list_cases route composes case_engine + finding_actions.
    # get_latest_action_for_cases, mirroring how /cases/{id} already
    # composes get_actions_for_case).
    detail = await main.get_case_detail(two_scans)
    finding_id = detail["latest"]["finding_id"]
    case_engine.save_action_with_verification(
        finding_id,
        dict(relevant=True, status="in_progress", action_taken="Reviewing.", owner="Ops", deadline=None,
             action_kind="investigation", intended_result=None, target_metric_id=None, target_value=None,
             target_comparison=None, target_baseline_relative=False, target_measurement_period=None),
        None,
    )

    result = await main.list_cases()
    action = result["items"][0]["action"]
    assert action is not None
    assert action["status"] == "in_progress"
    assert action["owner"] == "Ops"
    assert action["case_id"] == two_scans
    assert action["finding_id"] == finding_id


async def test_list_cases_flags_outstanding_work_even_when_the_latest_action_is_completed(two_scans):
    # Reproduces the exact scenario "action" alone can't answer: an OLDER
    # action still open, and a NEWER action on the same case already
    # completed. get_latest_action_for_cases (MAX(updated_at)) would show
    # only the completed one -- has_outstanding_action must still be true.
    from backend.app.db import appdb

    finding_actions.set_action("older-finding", True, "in_progress", "Investigating.", case_id=two_scans)
    finding_actions.set_action("newer-finding", True, "completed", "Fixed a different issue.", case_id=two_scans)
    # Force a deterministic ordering by updated_at regardless of how close
    # together the two calls above actually landed in wall-clock time.
    appdb.run_query("UPDATE finding_actions SET updated_at = '2026-09-10T00:00:00' WHERE finding_id = 'older-finding'")
    appdb.run_query("UPDATE finding_actions SET updated_at = '2026-09-15T00:00:00' WHERE finding_id = 'newer-finding'")

    result = await main.list_cases()
    item = result["items"][0]
    # The naive "latest action" signal reads "completed" here...
    assert item["action"]["finding_id"] == "newer-finding"
    assert item["action"]["status"] == "completed"
    # ...but the case still has real outstanding work, and the dedicated
    # aggregate must say so rather than hiding it.
    assert item["has_outstanding_action"] is True


async def test_list_cases_has_outstanding_action_is_false_once_every_action_is_completed(two_scans):
    finding_actions.set_action("only-finding", True, "completed", "Done.", case_id=two_scans)

    result = await main.list_cases()
    assert result["items"][0]["has_outstanding_action"] is False


async def test_list_cases_has_outstanding_action_is_false_for_a_case_with_no_actions(two_scans):
    result = await main.list_cases()
    item = result["items"][0]
    assert item["action"] is None
    assert item["has_outstanding_action"] is False


async def test_case_detail_exposes_a_richer_latest_summary_including_evidence(two_scans):
    detail = await main.get_case_detail(two_scans)
    assert detail["latest"]["title"] == "revenue moved"
    assert detail["latest"]["root_cause"] == {"checks": []}
    assert detail["latest"]["recommendations"] is None or detail["latest"]["recommendations"] == []


async def test_transfer_case_detail_preserves_rate_basis_and_excluded_receipt_evidence(appdb_reset):
    finding = _monthly_finding("transfer_discrepancy_missing_item", "inventory", "2026-10-15")
    finding["horizon"] = "snapshot"
    finding["impact"] = {
        "kind": "transfer_discrepancy", "financial_impact_rs": 10.0,
        "unit_rate": 0.29, "unit_rate_numerator_units": 1039.0,
        "unit_rate_denominator_units": 364529.0,
        "unit_rate_denominator_label": "all Celeste outlet transfer receipts in the 14-day window",
        "window_days": 14, "excluded_lines": 1, "excluded_units": 500.0,
    }
    finding["excluded_rows"] = [{"outlet": "Attidiya", "lines": 1, "units": 500.0, "states": ["cancel"]}]
    finding["excluded_trace_rows"] = [{
        "id": 99, "receipt": "CLSAD/IN/09999", "receipt_state": "cancel",
        "outlet": "Attidiya", "product": "Example product", "missing_qty": 500.0,
    }]
    case_engine.sync_cases([], [finding], [])

    detail = await main.get_case_detail(finding["case_id"])
    assert detail["latest"]["impact"]["unit_rate_denominator_units"] == 364529.0
    assert detail["latest"]["impact"]["unit_rate_denominator_label"].startswith("all Celeste outlet")
    assert detail["latest"]["excluded_rows"][0]["units"] == 500.0
    assert detail["latest"]["excluded_trace_rows"][0]["receipt_state"] == "cancel"


async def test_case_detail_exposes_the_metrics_trend_series(appdb_reset, monkeypatch):
    # Celeste Investigate Phase 3 (revenue investigation): the trend series
    # is already computed and stored in every monthly finding's snapshot,
    # but was previously dropped before reaching /cases/{id} -- there was
    # no way to render a real multi-month trend chart on Case Detail.
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 15))
    f = _monthly_finding("revenue", "financial", "2026-09")
    f["trend"] = [
        {"month": "2026-07", "is_partial": False, "value": 100.0},
        {"month": "2026-08", "is_partial": False, "value": 120.0},
        {"month": "2026-09", "is_partial": True, "value": 40.0},
    ]
    case_engine.sync_cases([f], [], [])

    detail = await main.get_case_detail(f["case_id"])
    assert detail["latest"]["trend"] == f["trend"]

    # A case list row (list_cases) must NOT carry the full trend -- that
    # would bloat every row in the library for a field only the detail
    # page renders.
    result = await main.list_cases()
    item = next(i for i in result["items"] if i["case_id"] == f["case_id"])
    assert "trend" not in item["latest"]


async def test_case_detail_trend_is_absent_for_a_finding_shape_with_no_trend(appdb_reset):
    # State findings (state_findings.py, e.g. a stock-out) have no baseline/
    # trend concept at all -- must read as honestly absent, not crash or
    # fabricate an empty series.
    from mcp_server.tools import state_findings as sf

    products = [{
        "name": "Set Yogurt 80G", "sku": "000638", "qty_sold_30d": 100.0, "current_stock": 0.0,
        "projected_demand": None, "days_of_cover": None, "short": False, "negative_stock": False,
    }]
    from unittest.mock import patch
    with patch.object(sf, "get_top_product_stock_coverage", lambda: {
        "as_of_date": "2026-09-15", "horizon_days": 10, "low_stock_threshold_days": 3, "products": products,
    }):
        finding = sf._top_seller_stock_finding()

    case_engine.sync_cases([], [finding], [])
    case_id = case_engine.get_case_id_for_finding(finding["id"])
    detail = await main.get_case_detail(case_id)
    assert detail["latest"]["trend"] is None


async def test_case_detail_latest_carries_the_finding_id_for_a_first_ever_action(two_scans):
    # Phase 2e post-acceptance browser test: a case with no finding_actions
    # row at all has nothing in its "actions" list to render an
    # ActionTracker against -- CaseDetail.jsx falls back to latest.
    # finding_id to let a manager record a FIRST action. Confirms the id
    # this exposes actually resolves back to the SAME case.
    detail = await main.get_case_detail(two_scans)
    finding_id = detail["latest"]["finding_id"]
    assert finding_id
    assert case_engine.get_case_id_for_finding(finding_id) == two_scans


async def test_list_cases_latest_is_none_for_a_case_with_no_observations(appdb_reset):
    # Defensive: get_case_detail/list_cases must not crash on a case row
    # that somehow has no observations yet.
    from backend.app.db import appdb
    case_engine._ensure_schema()
    appdb.run_query(
        "INSERT INTO business_cases (case_id, metric_id, domain, horizon, status, opened_at, last_seen_at) "
        "VALUES ('orphan-case', 'revenue', 'financial', 'monthly', 'open', 'x', 'x')"
    )
    result = await main.list_cases()
    item = next(i for i in result["items"] if i["case_id"] == "orphan-case")
    assert item["latest"] is None


# ---------------------------------------------------------------------------
# Phase 2, final correction ("Recommendation vs Action"): three distinct
# concepts, three distinct representations in the existing model --
#
#   1. Celeste recommendation -- a proposed course of action. Pure data,
#      part of a finding snapshot (latest.recommendations[]); no
#      persistence of its own.
#   2. Management acceptance -- an auditable planning decision. A
#      case_history row (event_type="recommendation_approved", written by
#      case_engine.record_recommendation_acceptance via accept_
#      recommendation / POST /findings/{id}/recommendation-acceptance).
#      NEVER touches finding_actions. Its case_history.id is its own
#      identity -- explicitly NOT an action id.
#   3. Recorded action -- real operational work: a finding_actions row
#      (status/owner/deadline/action_taken/target_*), written only by
#      PATCH /findings/{id}/action (case_engine.save_action_with_
#      verification), the pre-existing "Record action" tracker flow,
#      untouched by this correction.
#
# finding_actions still has exactly one row per finding_id (its own
# PRIMARY KEY) -- a case CAN have several action rows, but only across
# DIFFERENT finding_ids (several days' scans of the same case, each
# re-minting a new finding_id -- see get_case_id_for_finding's own
# docstring). These tests exercise the real stock-out case shape end to
# end through the actual API routes, against the isolated test app
# database (appdb_reset) -- never the reporting mirror or production app.db.
# ---------------------------------------------------------------------------

def _stock_out_finding(period="2026-09-15", extra_recommendations=None):
    recommendations = [
        {
            "action": "Urgently verify and restock top sellers recorded at zero or negative stock: Set Yogurt 80G - Ambewela - SKU 000638",
            "owner": "Inventory team", "priority": "critical",
            "expected_benefit": "Restores recorded stock for 1 of your 15 best-selling products by volume.",
        },
        {
            "action": "Reconcile recorded stock for Set Yogurt 80G - Ambewela - SKU 000638 -- negative on-hand quantity requires investigation",
            "owner": "Inventory team", "priority": "high",
            "expected_benefit": "Determines the actual cause before it distorts reorder decisions.",
        },
    ]
    if extra_recommendations:
        recommendations.extend(extra_recommendations)
    return {
        "id": f"top_seller_stock-{period}",
        "metric_id": "top_seller_stock",
        "domain": "inventory",
        "category": "critical",
        "priority": "critical",
        "business_impact_score": 3.75,
        "title": "1 of your top 15 best-selling products are recorded at zero or negative stock, company-wide",
        "confirm": {"period": period, "current": 1, "baseline": None, "pct_change": None, "unit": "count"},
        "root_cause": None,
        "impact": {
            "kind": "top_sellers_out_of_stock", "financial_impact_rs": None, "count": 1, "total_top_sellers": 15,
            "avg_order_value_used": None, "confidence": "measured", "month": period,
            "detail": "1 of your 15 best-selling products are recorded at zero or negative stock across monitored locations, company-wide.",
        },
        "breakdowns": [{
            "dimension": "product", "label": "Current stock (units)", "mode": "contribution", "baseline_month": None,
            "top_contributors": [{
                "name": "Set Yogurt 80G - Ambewela - SKU 000638", "sku": "000638", "current": -1.0, "delta": -1.0,
                "negative_stock": True, "qty_sold_30d": 7083.0,
            }],
        }],
        "recommendations": recommendations,
        "status": "completed",
    }


@pytest.fixture
def stock_out_case(appdb_reset, monkeypatch):
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 20))
    f = _stock_out_finding()
    case_engine.sync_cases([], [f], [])
    return f["case_id"]


async def test_stock_out_case_starts_with_zero_actions(stock_out_case):
    detail = await main.get_case_detail(stock_out_case)
    assert detail["actions"] == []
    assert len(detail["latest"]["recommendations"]) == 2


async def test_accepting_one_recommendation_writes_no_action_row(stock_out_case):
    # The "phantom action" bug this correction fixes: accepting a
    # recommendation must never create or touch a finding_actions row.
    detail = await main.get_case_detail(stock_out_case)
    finding_id = detail["latest"]["finding_id"]
    verify_action = detail["latest"]["recommendations"][1]["action"]

    req = main.RecommendationAcceptanceRequest(recommendation_action=verify_action)
    result = await main.accept_finding_recommendation(finding_id, req)
    assert result["status"] == "ok"
    assert result["recorded"] is True

    detail = await main.get_case_detail(stock_out_case)
    # Zero action rows -- accepting is not the same as recording an action.
    assert detail["actions"] == []
    approved = [h for h in detail["history"] if h["event_type"] == "recommendation_approved"]
    assert len(approved) == 1
    saved = json.loads(approved[0]["detail"])
    assert saved["recommendation"]["action"] == verify_action
    assert saved["recommendation"]["owner"] == "Inventory team"
    # The decision-time evidence snapshot is preserved on the acceptance
    # event itself -- self-sufficient, no sibling decision_evidence event
    # is written (there's no action-status change to attach one to).
    assert saved["snapshot"]["confirm"]["current"] == 1
    assert [h for h in detail["history"] if h["event_type"] == "decision_evidence"] == []


async def test_accepting_two_recommendations_independently_writes_no_action_rows(stock_out_case):
    detail = await main.get_case_detail(stock_out_case)
    finding_id = detail["latest"]["finding_id"]
    restock_action = detail["latest"]["recommendations"][0]["action"]
    verify_action = detail["latest"]["recommendations"][1]["action"]

    result1 = await main.accept_finding_recommendation(
        finding_id, main.RecommendationAcceptanceRequest(recommendation_action=verify_action),
    )
    assert result1["status"] == "ok"
    result2 = await main.accept_finding_recommendation(
        finding_id, main.RecommendationAcceptanceRequest(recommendation_action=restock_action),
    )
    assert result2["status"] == "ok"

    # Reload persistence -- a second, independent fetch sees exactly the
    # same two distinct decision records, still zero action rows.
    detail_a = await main.get_case_detail(stock_out_case)
    detail_b = await main.get_case_detail(stock_out_case)
    for detail in (detail_a, detail_b):
        assert detail["actions"] == []
        approved = sorted(
            (h for h in detail["history"] if h["event_type"] == "recommendation_approved"), key=lambda h: h["id"],
        )
        assert len(approved) == 2
        assert approved[0]["id"] != approved[1]["id"]  # distinct, individually traceable identities
        assert json.loads(approved[0]["detail"])["recommendation"]["action"] == verify_action
        assert json.loads(approved[1]["detail"])["recommendation"]["action"] == restock_action


async def test_recording_a_real_action_after_accepting_creates_exactly_one_action_row(stock_out_case):
    detail = await main.get_case_detail(stock_out_case)
    finding_id = detail["latest"]["finding_id"]
    verify_action = detail["latest"]["recommendations"][1]["action"]

    await main.accept_finding_recommendation(
        finding_id, main.RecommendationAcceptanceRequest(recommendation_action=verify_action),
    )
    detail = await main.get_case_detail(stock_out_case)
    assert detail["actions"] == []  # accepting alone: still zero

    # Now the manager actually records the action -- a real operational
    # record with its own description, owner, and deadline.
    req = main.FindingActionRequest(
        relevant=True, status="in_progress", action_taken="Counted physical stock; discrepancy confirmed.",
        owner="Ops - Inventory", deadline="2026-10-01",
    )
    result = await main.update_finding_action(finding_id, req)
    assert result["status"] == "ok"

    detail = await main.get_case_detail(stock_out_case)
    assert len(detail["actions"]) == 1
    action = detail["actions"][0]
    assert action["action_taken"] == "Counted physical stock; discrepancy confirmed."
    assert action["owner"] == "Ops - Inventory"
    assert action["deadline"] == "2026-10-01"
    assert action["status"] == "in_progress"
    # The earlier acceptance is untouched by recording the action.
    approved = [h for h in detail["history"] if h["event_type"] == "recommendation_approved"]
    assert len(approved) == 1
    assert json.loads(approved[0]["detail"])["recommendation"]["action"] == verify_action


async def test_accepting_a_recommendation_never_overwrites_an_existing_recorded_action(stock_out_case):
    detail = await main.get_case_detail(stock_out_case)
    finding_id = detail["latest"]["finding_id"]
    restock_action = detail["latest"]["recommendations"][0]["action"]

    # A real action already exists, with its own description/owner/deadline.
    await main.update_finding_action(finding_id, main.FindingActionRequest(
        relevant=True, status="in_progress", action_taken="Restocking in progress.",
        owner="Ops - Inventory", deadline="2026-10-05",
    ))

    # Accepting a (different) recommendation afterwards must not touch it.
    result = await main.accept_finding_recommendation(
        finding_id, main.RecommendationAcceptanceRequest(recommendation_action=restock_action),
    )
    assert result["status"] == "ok"

    detail = await main.get_case_detail(stock_out_case)
    assert len(detail["actions"]) == 1
    action = detail["actions"][0]
    assert action["action_taken"] == "Restocking in progress."
    assert action["owner"] == "Ops - Inventory"
    assert action["deadline"] == "2026-10-05"
    assert action["status"] == "in_progress"
    approved = [h for h in detail["history"] if h["event_type"] == "recommendation_approved"]
    assert len(approved) == 1


async def test_a_stale_evidence_version_during_acceptance_is_rejected_and_writes_nothing(appdb_reset, monkeypatch):
    # A fresh rescan changes the evidence for the SAME finding_id (the
    # reporting mirror can go a whole business day without its as_of_date
    # advancing -- see state_findings._top_seller_stock_finding -- so this
    # is a real, not hypothetical, scenario) between when a manager opened
    # the case and when they click "Accept recommendation".
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 20))
    f = _stock_out_finding()
    case_engine.sync_cases([], [f], [])
    case_id = f["case_id"]
    finding_id = f["id"]
    stale_version = case_engine.get_case_observations(case_id)[-1]["version"]

    # A second same-day rescan with a materially different count -- e.g. a
    # second product now also out of stock -- overwrites the observation
    # (the "newer evidence must not be silently discarded" rule).
    f2 = _stock_out_finding()
    f2["confirm"]["current"] = 2
    case_engine.sync_cases([], [f2], [])

    verify_action = f["recommendations"][1]["action"]
    with pytest.raises(case_engine.StaleEvidenceError) as excinfo:
        case_engine.accept_recommendation(finding_id, verify_action, stale_version)
    assert excinfo.value.current_snapshot["confirm"]["current"] == 2
    # Nothing was written -- no action row (never expected here) and no
    # decision record either.
    assert finding_actions.get_actions_for([finding_id]) == {}
    detail = await main.get_case_detail(case_id)
    assert [h for h in detail["history"] if h["event_type"] == "recommendation_approved"] == []


async def test_accepting_a_recommendation_for_a_finding_with_no_case_is_rejected(appdb_reset):
    req = main.RecommendationAcceptanceRequest(recommendation_action="Do something")
    result = await main.accept_finding_recommendation("unknown-metric-2026-09", req)
    assert result["status"] == "error"


async def test_completing_a_recorded_action_never_resolves_the_case_and_target_metrics_stay_unsupported(stock_out_case):
    detail = await main.get_case_detail(stock_out_case)
    finding_id = detail["latest"]["finding_id"]

    req = main.FindingActionRequest(
        relevant=True, status="completed", action_taken="Restocked from the central warehouse.",
    )
    await main.update_finding_action(finding_id, req)

    case = case_engine.get_case(stock_out_case)
    # Completing the tracked action never resolves the case by itself --
    # resolution is driven only by ProblemRecoveryPolicy clearing the
    # metric's own anomaly threshold on a later scan.
    assert case["status"] == "open"

    detail = await main.get_case_detail(stock_out_case)
    action = detail["actions"][0]
    assert action["status"] == "completed"
    # top_seller_stock is not in intelligence.METRICS -- confirms no
    # measurable outcome target was fabricated for a metric that can't
    # support one.
    assert action["target_metric_id"] is None
    assert action["outcome_status"] is None


async def test_actions_and_outcomes_workspaces_show_nothing_for_recommendation_only_acceptance(stock_out_case):
    # "Actions taken" and the Actions/Outcomes workspaces must count real
    # operational action records only -- never recommendation-acceptance
    # events or an empty placeholder row.
    detail = await main.get_case_detail(stock_out_case)
    finding_id = detail["latest"]["finding_id"]
    restock_action = detail["latest"]["recommendations"][0]["action"]
    verify_action = detail["latest"]["recommendations"][1]["action"]

    await main.accept_finding_recommendation(
        finding_id, main.RecommendationAcceptanceRequest(recommendation_action=restock_action),
    )
    await main.accept_finding_recommendation(
        finding_id, main.RecommendationAcceptanceRequest(recommendation_action=verify_action),
    )

    from backend.app.services import management
    all_actions = management.list_actions()
    assert finding_id not in {a["finding_id"] for a in all_actions["items"]}
    outstanding = management.list_actions(status="outstanding")
    assert finding_id not in {a["finding_id"] for a in outstanding["items"]}

    result = await main.list_cases()
    item = next(i for i in result["items"] if i["case_id"] == detail["case"]["case_id"])
    assert item["action"] is None
    assert item["has_outstanding_action"] is False


async def test_investigate_and_actions_workspaces_both_see_outstanding_work_across_two_scan_days(appdb_reset, monkeypatch):
    # The REAL "multiple actions on one case" mechanism (see the module
    # comment above): a case that persists across two scan days gets a
    # NEW finding_id each day the reporting mirror's as_of_date actually
    # advances, and each finding_id can carry its OWN action row. Day 1's
    # action is left in_progress; day 2's is completed -- distinct from,
    # and not to be confused with, several recommendations approved on the
    # SAME day's single finding_id (covered above).
    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 15))
    f1 = _stock_out_finding(period="2026-09-15")
    case_engine.sync_cases([], [f1], [])
    case_id = case_engine.get_case_id_for_finding(f1["id"])

    result1 = await main.update_finding_action(
        f1["id"], main.FindingActionRequest(relevant=True, status="in_progress", action_taken="Investigating stock discrepancy."),
    )
    assert result1["status"] == "ok"

    monkeypatch.setattr(case_engine, "_business_today", lambda: datetime.date(2026, 9, 16))
    f2 = _stock_out_finding(period="2026-09-16")
    case_engine.sync_cases([], [f2], [])
    assert case_engine.get_case_id_for_finding(f2["id"]) == case_id  # same case, new finding_id

    result2 = await main.update_finding_action(
        f2["id"], main.FindingActionRequest(relevant=True, status="completed", action_taken="Restocked."),
    )
    assert result2["status"] == "ok"

    # Investigate (the case list): the naive "latest action" signal alone
    # would read "completed" (day 2's row, most recently updated)...
    result = await main.list_cases()
    item = next(i for i in result["items"] if i["case_id"] == case_id)
    assert item["action"]["status"] == "completed"
    # ...but the dedicated aggregate still surfaces day 1's real outstanding work.
    assert item["has_outstanding_action"] is True

    # Actions workspace: the outstanding filter must surface day 1's row.
    from backend.app.services import management
    outstanding = management.list_actions(status="outstanding")
    outstanding_finding_ids = {a["finding_id"] for a in outstanding["items"]}
    assert f1["id"] in outstanding_finding_ids
    assert f2["id"] not in outstanding_finding_ids
