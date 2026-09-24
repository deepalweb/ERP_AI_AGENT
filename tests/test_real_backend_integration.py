"""Controlled Pilot Preparation, Task 2: a SEPARATE integration test that
exercises the real backend service layer against the real reporting
database (the customer's live, read-only Odoo data) -- not mocked
fixtures. Validates actual API response shapes, evidence verdicts, case
associations, and action persistence against genuine business data.

Deliberately NOT part of the fast, always-run suite: it needs live network
access to REPORTING_DATABASE_URL and takes 1-2 minutes (the real anomaly
scan queries a 500k+ row sale_order table across every registered metric,
concurrently). Skips cleanly when that database isn't reachable, so it
never breaks the fast suite or CI environments without DB access. Run it
explicitly: `pytest tests/test_real_backend_integration.py -v -s`.

Safety: reporting.run_query is READ-ONLY by architecture (see reporting.py
and every module built on it) -- this file never issues a write against
it. All writes (cases, actions) go through the SAME appdb_reset fixture
every other test uses, an isolated temp SQLite file -- the real app.db
that backs actual daily operations is never touched.
"""

import os

import pytest
from dotenv import load_dotenv

from backend.app.db import appdb, reporting
from backend.app.services import case_engine, finding_actions
from mcp_server.tools import intelligence, investigation, state_findings

load_dotenv()


def _reporting_db_reachable() -> bool:
    try:
        reporting.get_engine.cache_clear()
        reporting.run_query("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.real_backend,
    pytest.mark.skipif(
        not _reporting_db_reachable(),
        reason="REPORTING_DATABASE_URL not set or not reachable -- real-backend integration skipped",
    ),
]


@pytest.fixture(scope="module")
def real_scan():
    """One real scan, reused by every test in this module -- the actual
    cost (~1-2 minutes) is paid once, not once per assertion group."""
    investigations = investigation.scan_all()
    inventory = state_findings.build_inventory_findings()
    return {"investigations": investigations, "inventory": inventory}


# ---------------------------------------------------------------------------
# Metric formatting and trend shape, against real data.
# ---------------------------------------------------------------------------

def test_every_registered_metrics_trend_has_the_correct_shape_against_real_data():
    for metric_id, metric in intelligence.METRICS.items():
        trend = intelligence.get_trend(metric)
        assert isinstance(trend, list)
        for row in trend:
            assert set(["month", "value", "is_partial"]).issubset(row.keys())
            assert isinstance(row["month"], str) and len(row["month"]) == 7  # "YYYY-MM"
            assert isinstance(row["value"], (int, float))
            assert isinstance(row["is_partial"], bool)
        # Only the LAST row may be partial -- get_trend's own documented invariant.
        partial_flags = [r["is_partial"] for r in trend]
        assert sum(partial_flags) <= 1
        if any(partial_flags):
            assert partial_flags[-1] is True


def test_real_findings_have_well_formed_confirm_blocks_matching_their_metric_unit(real_scan):
    for inv in real_scan["investigations"]:
        metric = intelligence.METRICS[inv["metric_id"]]
        confirm = inv["confirm"]
        assert confirm["unit"] == metric.unit
        assert isinstance(confirm["current"], (int, float))
        assert isinstance(confirm["pct_change"], (int, float))
        assert inv["category"] in ("critical", "opportunity")
        assert inv["priority"] in ("critical", "high", "medium", "worth_pursuing")


# ---------------------------------------------------------------------------
# Evidence verdicts, against real data -- the actual vocabulary this whole
# project's honesty guarantees rest on.
# ---------------------------------------------------------------------------

_VALID_VERDICTS = {"confirmed", "possible_contributor", "no_evidence_found", "unknown", "related_not_independent"}


def test_real_root_cause_checks_only_use_the_declared_verdict_vocabulary(real_scan):
    saw_at_least_one_check = False
    for inv in real_scan["investigations"]:
        for check in inv.get("root_cause", {}).get("checks", []):
            saw_at_least_one_check = True
            assert check["verdict"] in _VALID_VERDICTS
            assert check.get("source")
            assert check.get("detail")  # never an empty explanation
    assert saw_at_least_one_check  # sanity: real data actually produced evidence to check


def test_a_confirmed_root_cause_check_has_a_specific_segment_not_a_vague_claim(real_scan):
    # Every "confirmed" verdict in this run must point at a real, named
    # segment (e.g. a specific store) -- never assert "confirmed" as a bare
    # label with nothing concrete backing it.
    confirmed_checks = [
        c for inv in real_scan["investigations"] for c in inv.get("root_cause", {}).get("checks", [])
        if c["verdict"] == "confirmed"
    ]
    assert confirmed_checks  # today's real data has at least one
    for c in confirmed_checks:
        assert c.get("segments") and len(c["segments"]) >= 1
        assert isinstance(c["segments"][0], str) and c["segments"][0].strip()


def test_inventory_findings_have_the_same_evidence_shape_as_trend_findings(real_scan):
    for inv in real_scan["inventory"]:
        assert inv["domain"] == "inventory"
        assert inv["category"] in ("critical", "opportunity")
        for check in inv.get("root_cause", {}).get("checks", []):
            assert check["verdict"] in _VALID_VERDICTS


# ---------------------------------------------------------------------------
# Case associations and action persistence, against real findings --
# writes go ONLY to the isolated temp appdb (appdb_reset), never real
# app.db.
# ---------------------------------------------------------------------------

def test_real_findings_sync_into_cases_with_correct_associations(appdb_reset, real_scan):
    investigations = [dict(inv) for inv in real_scan["investigations"]]  # sync_cases mutates in place
    inventory = [dict(inv) for inv in real_scan["inventory"]]
    case_engine.sync_cases(investigations, inventory, [])

    for finding in investigations + inventory:
        assert finding.get("case_id")  # every real finding got a case
        case = case_engine.get_case(finding["case_id"])
        assert case is not None
        assert case["metric_id"] == finding["metric_id"]
        assert case["category"] == finding["category"]
        # get_case_id_for_finding must resolve the SAME case back, the
        # real association PATCH /findings/{id}/action relies on.
        assert case_engine.get_case_id_for_finding(finding["id"]) == finding["case_id"]

    detail = case_engine.get_case_detail(investigations[0]["case_id"])
    assert detail is not None
    assert detail["latest"]["finding_id"] == investigations[0]["id"]
    assert detail["case"]["resolution_policy"] in ("problem_recovery", "opportunity_capture")


def test_action_persistence_against_a_real_finding_id(appdb_reset, real_scan):
    real_finding = real_scan["investigations"][0]
    investigations = [dict(real_finding)]
    case_engine.sync_cases(investigations, [], [])
    case_id = investigations[0]["case_id"]
    finding_id = investigations[0]["id"]

    result = case_engine.save_action_with_verification(
        finding_id,
        dict(relevant=True, status="in_progress", action_taken="Reviewing the real driver behind this.",
             owner="Ops", deadline=None, action_kind="investigation", intended_result=None,
             target_metric_id=None, target_value=None, target_comparison=None,
             target_baseline_relative=False, target_measurement_period=None),
        None,
    )
    assert result["case_id"] == case_id

    action = finding_actions.get_actions_for([finding_id])[finding_id]
    assert action["relevant"] is True
    assert action["status"] == "in_progress"
    assert action["case_id"] == case_id

    actions_for_case = finding_actions.get_actions_for_case(case_id)
    assert any(a["finding_id"] == finding_id for a in actions_for_case)
