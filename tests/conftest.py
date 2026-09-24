"""Shared fixtures for tests that hit appdb (app.db) — an isolated,
per-test SQLite file so tests never touch the real dev app.db and never
leak state between tests."""

import pytest

from backend.app.db import appdb
from backend.app.services import case_engine, finding_actions, state_tracking, tracking, weekly_streaks

_SCHEMA_FLAG_MODULES = [finding_actions, tracking, state_tracking, weekly_streaks, case_engine]


@pytest.fixture
def appdb_reset(tmp_path, monkeypatch):
    db_path = tmp_path / "test_app.db"
    monkeypatch.setenv("APP_DATABASE_URL", f"sqlite:///{db_path}")
    appdb.get_engine.cache_clear()
    for module in _SCHEMA_FLAG_MODULES:
        module._SCHEMA_READY = False
    yield
    appdb.get_engine.cache_clear()
    for module in _SCHEMA_FLAG_MODULES:
        module._SCHEMA_READY = False
