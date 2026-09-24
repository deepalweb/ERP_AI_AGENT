"""Exercise request and scheduled scans together without Odoo or model calls."""

import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from backend.app import main
from backend.app.services import brain, state_tracking


def _stub_sources(monkeypatch):
    monkeypatch.setattr(brain, "_SCHEMA_READY", False)
    monkeypatch.setattr(brain.investigation, "scan_all", list)
    monkeypatch.setattr(brain.investigation, "scan_short_term", list)
    monkeypatch.setattr(brain.investigation, "generate_daily_synthesis", lambda _: None)
    sources = {
        "inventory": [{"metric_id": "stock_availability", "category": "critical", "confirm": {"current": 12}}],
        "operations": [{"metric_id": "dark_outlets", "category": "critical", "confirm": {"current": 1}}],
        "incidents": [{"metric_id": "incident_test", "category": "critical", "confirm": {"current": 3}}],
        # Business Data Controls, Wave 1 Step 1 -- empty by default so this
        # shared fixture stays deterministic; without this, build_controls_
        # findings() falls through to the real, unmocked function and hits
        # the live reporting DB from inside these tests.
        "controls": [],
    }
    monkeypatch.setattr(brain.state_findings, "build_inventory_findings", lambda: sources["inventory"])
    monkeypatch.setattr(brain.state_findings, "build_operational_findings", lambda: sources["operations"])
    monkeypatch.setattr(brain.state_findings, "build_controls_findings", lambda: sources["controls"])
    monkeypatch.setattr(brain.incident_engine, "build_incident_findings", lambda: sources["incidents"])
    monkeypatch.setattr(brain, "_snapshot_health", dict)
    monkeypatch.setattr(brain, "_data_freshness", dict)
    predictions = brain.customer_predictions
    monkeypatch.setattr(predictions, "compute_predictions", list)
    monkeypatch.setattr(predictions, "record_predictions", lambda _: None)
    monkeypatch.setattr(predictions, "compute_pattern_stats", lambda _: {})
    monkeypatch.setattr(predictions, "record_pattern_stats", lambda _: None)
    monkeypatch.setattr(predictions, "get_summary", dict)
    monkeypatch.setattr(predictions, "build_home_finding", lambda _: None)
    monkeypatch.setattr(predictions, "compute_lifecycle_summary", dict)
    monkeypatch.setattr(predictions, "record_lifecycle_summary", lambda _: None)
    return sources


@pytest.fixture
def scan_sources(appdb_reset, monkeypatch):
    return _stub_sources(monkeypatch)


async def test_investigations_does_not_falsely_resolve_operational_or_incident_findings(scan_sources):
    existing = scan_sources["operations"] + scan_sources["incidents"]
    state_tracking.record_new_findings(existing)

    response = await main.investigations()

    assert response["status"] == "ok"
    rows = {row["metric_id"]: row for row in state_tracking.get_tracking_history()}
    for finding in existing:
        assert rows[finding["metric_id"]]["status"] == "open"
        assert rows[finding["metric_id"]]["verdict"] is None
    assert {f["metric_id"] for f in response["state_findings"]} == set(rows)


async def test_failed_state_source_does_not_resolve_existing_findings(scan_sources, monkeypatch):
    state_tracking.record_new_findings(scan_sources["incidents"])

    def fail():
        raise RuntimeError("Source unavailable")

    monkeypatch.setattr(brain.incident_engine, "build_incident_findings", fail)
    response = await main.investigations()

    assert response["status"] == "error"
    assert state_tracking.get_tracking_history()[0]["status"] == "open"


def test_scheduled_and_stale_cache_requests_share_one_scan(scan_sources, monkeypatch):
    # Initialize schemas before threading so this tests scan coordination,
    # rather than racing SQLite CREATE TABLE calls.
    brain._ensure_schema()
    entered = Event()
    duplicate = Event()
    calls = []

    def scan():
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            duplicate.wait(timeout=0.5)
        else:
            duplicate.set()
        return []

    monkeypatch.setattr(brain.investigation, "scan_all", scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scheduled = pool.submit(brain.run_daily_scan)
        assert entered.wait(timeout=5)
        request = pool.submit(brain.get_latest_brief)
        scheduled.result(timeout=10)
        request.result(timeout=10)

    assert len(calls) == 1


def _scan_in_worker(database_url, barrier, calls):
    """Spawn-safe target using the real app DB and scan orchestration."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("APP_DATABASE_URL", database_url)
        patch.delenv("APP_SCAN_LOCK_PATH", raising=False)
        brain.appdb.get_engine.cache_clear()
        _stub_sources(patch)
        lock = brain.appdb.case_store_lock

        def synchronized_lock(*args, **kwargs):
            # Both requests have captured requested_at before either scans.
            barrier.wait(timeout=15)
            return lock(*args, **kwargs)

        def scan():
            with calls.get_lock():
                calls.value += 1
            return []

        patch.setattr(brain.appdb, "case_store_lock", synchronized_lock)
        patch.setattr(brain.investigation, "scan_all", scan)
        brain.run_daily_scan()


def test_scheduled_scans_in_separate_workers_share_one_result(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    calls = ctx.Value("i", 0)
    database_url = f"sqlite:///{tmp_path / 'worker_app.db'}"
    workers = [ctx.Process(target=_scan_in_worker, args=(database_url, barrier, calls)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=25)
            assert worker.exitcode == 0
        assert calls.value == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)


def test_failed_scan_releases_lock_and_can_be_retried(scan_sources, monkeypatch):
    def fail():
        raise RuntimeError("Source unavailable")

    monkeypatch.setattr(brain.investigation, "scan_all", fail)
    with pytest.raises(RuntimeError, match="Source unavailable"):
        brain.run_daily_scan()
    monkeypatch.setattr(brain.investigation, "scan_all", list)
    result = brain.get_latest_brief()
    assert len(result["state_findings"]) == 3
