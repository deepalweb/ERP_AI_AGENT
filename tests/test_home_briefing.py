from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from backend.app import main
from backend.app.db import appdb
from backend.app.services import brain, finding_actions, home_briefing, management


@pytest.fixture(autouse=True)
def coordinator(appdb_reset, monkeypatch):
    monkeypatch.setattr(home_briefing, "_refresh", {"status": "idle", "error": None})
    monkeypatch.setattr(home_briefing, "_thread", None)
    yield
    if home_briefing._thread:
        home_briefing._thread.join(timeout=5)


async def test_expired_brief_is_returned_without_waiting_for_scan(appdb_reset, monkeypatch):
    monkeypatch.setattr(brain, "_SCHEMA_READY", False)
    brain._ensure_schema()
    generated = (datetime.now(UTC) - timedelta(days=2)).replace(tzinfo=None).isoformat()
    appdb.run_query(
        "INSERT INTO daily_brief (generated_at, investigations, tracking) VALUES (:d,'[]','[]')",
        {"d": generated},
    )

    def scan_must_not_block_read(*args, **kwargs):
        raise RuntimeError("scan unavailable")

    monkeypatch.setattr(brain, "run_daily_scan", scan_must_not_block_read)
    response = await main.daily_brief()
    assert response["status"] == "ok"
    assert response["generated_at"] == generated
    assert response["cache_status"] == "stale"


async def test_cold_start_has_loading_then_real_failure_and_retry(appdb_reset, monkeypatch):
    monkeypatch.setattr(brain, "_SCHEMA_READY", False)
    entered, release = Event(), Event()
    calls = []

    def scan(*args, **kwargs):
        calls.append(1)
        entered.set()
        release.wait(3)
        raise RuntimeError("reporting unavailable")

    monkeypatch.setattr(brain, "run_daily_scan", scan)
    first = await main.daily_brief()
    assert first.status_code == 202
    assert entered.wait(1)
    assert (await main.daily_brief()).status_code == 202
    assert len(calls) == 1
    release.set()
    home_briefing._thread.join(3)
    assert (await main.daily_brief()).status_code == 503
    assert len(calls) == 1  # failures do not cause a request/retry storm
    assert (await main.refresh_home_brief()).status_code == 202
    home_briefing._thread.join(3)
    assert len(calls) == 2


def test_priority_projection_preserves_horizons_and_existing_scores():
    a = {"id": "a", "business_impact_score": 1, "priority": "critical"}
    b = {"id": "b", "business_impact_score": 5, "category": "opportunity"}
    result = home_briefing.presentation(
        {"investigations": [a], "state_findings": [b], "short_term_findings": []}
    )
    assert result["long"]["priority_ids"] == ["b", "a"]
    assert result["long"]["severity_counts"]["positive"] == 1
    assert result["week"]["ranked_ids"] == []


def test_saved_action_text_missing_links_and_bad_date_are_not_invented(appdb_reset):
    finding_actions.set_action("legacy", True, "in_progress", None, deadline="2020-01-01")
    finding_actions.set_action("described", True, "in_progress", "Call the supplier")
    items = {a["finding_id"]: a for a in management.list_actions()["items"]}
    assert items["legacy"]["description"] is None
    assert items["legacy"]["case"] is None
    assert items["legacy"]["case_link_status"] == "unlinked"
    assert items["legacy"]["deadline"] == "2020-01-01"
    assert items["legacy"]["deadline_note"]
    assert items["described"]["description"] == "Call the supplier"
