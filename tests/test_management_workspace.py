"""Management read models use every stored action and server-side pagination."""
import pytest

from backend.app import main
from backend.app.db import appdb
from backend.app.services import case_engine, finding_actions


@pytest.fixture
def workspace(appdb_reset):
    case_engine._ensure_schema()
    finding_actions._ensure_schema()
    with appdb.get_engine().begin() as conn:
        for i in range(205):
            appdb.run_query(
                "INSERT INTO business_cases (case_id, metric_id, domain, horizon, status, opened_at, last_seen_at) "
                "VALUES (:id, :metric, 'financial', 'monthly', 'open', :at, :at)",
                {"id": f"case-{i:03}", "metric": f"metric-{i}", "at": "2026-09-18T00:00:00"}, conn=conn,
            )
    finding_actions.set_action("older-finding", True, "in_progress", "Older active action", case_id="case-204")
    finding_actions.set_action("newer-finding", False, "not_started", "Dismissed newer action", case_id="case-204")
    finding_actions.set_action("completed-finding", True, "completed", "Completed work", case_id="case-204")
    appdb.run_query("UPDATE finding_actions SET target_metric_id='revenue', target_value=10, "
                    "target_comparison='>=', outcome_status='blocked' WHERE finding_id='older-finding'")


async def test_older_active_action_survives_newer_actions_and_case_cap(workspace):
    result = await main.management_actions(status="in_progress")
    assert result["status"] == "ok"
    assert result["total"] == 1
    action = result["items"][0]
    assert action["finding_id"] == "older-finding"
    assert action["case_id"] == "case-204"
    assert action["outcome_status"] == "blocked"
    assert action["target_value"] == 10
    all_actions = await main.management_actions()
    assert all_actions["total"] == 3  # dismissed work remains retrievable


async def test_exact_saved_observation_links_an_unassociated_action(workspace):
    finding_actions.set_action("historical-observed", True, "in_progress", "Review evidence")
    appdb.run_query(
        "INSERT INTO case_observations (case_id, scan_run_id, finding_id, observed_at, snapshot) "
        "VALUES ('case-204', 'test-run', 'historical-observed', '2026-09-18T00:00:00', '{}')"
    )
    result = await main.management_actions()
    action = next(a for a in result["items"] if a["finding_id"] == "historical-observed")
    assert action["case"]["case_id"] == "case-204"
    assert action["case_link_status"] == "observation_link"
    # Projection only: do not silently migrate historical records.
    assert action["case_id"] is None


async def test_cases_beyond_200_are_retrievable_and_totals_are_global(workspace):
    first = await main.list_cases(limit=200)
    last = await main.list_cases(limit=200, offset=200)
    assert first["total"] == last["total"] == 205
    assert len({c["case_id"] for c in first["items"] + last["items"]}) == 205
    summary = await main.management_summary()
    assert summary["cases"]["open"] == 205
    assert summary["actions"]["outstanding"] == 1
    assert summary["actions"]["blocked"] == 1
    assert summary["actions"]["completed"] == 1
    assert summary["cases"].get("recovered", 0) == 0  # completed action does not resolve a case


async def test_action_pagination_and_filters_apply_before_limit(workspace):
    pages = [await main.management_actions(limit=1, offset=i) for i in range(3)]
    assert all(p["total"] == 3 for p in pages)
    assert len({p["items"][0]["finding_id"] for p in pages}) == 3
    blocked = await main.management_actions(outcome_status="blocked", limit=1)
    assert blocked["total"] == 1
    assert blocked["items"][0]["finding_id"] == "older-finding"


async def test_legacy_finding_link_uses_stored_association(workspace):
    result = await main.finding_case("older-finding")
    assert result["case_id"] == "case-204"
    missing = await main.finding_case("metric-204-guessed-period")
    assert missing["case_id"] is None


async def test_unlinked_action_is_preserved_without_guessing_a_case(workspace):
    finding_actions.set_action("legacy-unlinked", True, "in_progress", "Legacy work")
    result = await main.management_actions(status="in_progress")
    legacy = next(a for a in result["items"] if a["finding_id"] == "legacy-unlinked")
    assert legacy["case_id"] is None
    assert legacy["case"] is None


async def test_action_carries_its_case_title_priority_and_domain(workspace):
    """Frontend Intelligence Integration: "Review session" alone doesn't
    say why -- the case's own title (from its latest finding snapshot) is
    the real business reason, batch-fetched the same way list_cases()
    already does."""
    appdb.run_query(
        "UPDATE business_cases SET priority='high' WHERE case_id='case-204'"
    )
    appdb.run_query(
        "INSERT INTO case_observations (case_id, scan_run_id, finding_id, observed_at, snapshot) "
        "VALUES ('case-204', 'test-run', 'older-finding', '2026-09-18T00:00:00', "
        "'{\"title\": \"8 stock counts started but never finished\"}')"
    )
    result = await main.management_actions(status="in_progress")
    action = next(a for a in result["items"] if a["finding_id"] == "older-finding")
    assert action["case"]["domain"] == "financial"
    assert action["case"]["priority"] == "high"
    assert action["case"]["title"] == "8 stock counts started but never finished"


async def test_domain_and_owner_filters_narrow_the_action_list(workspace):
    appdb.run_query(
        "INSERT INTO business_cases (case_id, metric_id, domain, horizon, status, opened_at, last_seen_at) "
        "VALUES ('case-controls', 'negative_stock_control', 'controls', 'snapshot', 'open', "
        "'2026-09-18T00:00:00', '2026-09-18T00:00:00')"
    )
    finding_actions.set_action("controls-finding", True, "in_progress", "Reconcile stock", case_id="case-controls")
    appdb.run_query("UPDATE finding_actions SET owner='Inventory team' WHERE finding_id='controls-finding'")
    appdb.run_query("UPDATE finding_actions SET owner='Ops team' WHERE finding_id='older-finding'")

    controls_only = await main.management_actions(domain="controls")
    assert {a["finding_id"] for a in controls_only["items"]} == {"controls-finding"}

    inventory_owner = await main.management_actions(owner="Inventory team")
    assert {a["finding_id"] for a in inventory_owner["items"]} == {"controls-finding"}

    # Partial, case-insensitive -- a manager typing "inventory" alone
    # should still find "Inventory team", not require an exact retype.
    partial = await main.management_actions(owner="inventory")
    assert {a["finding_id"] for a in partial["items"]} == {"controls-finding"}
