"""User report, 2026-10-22: Investigate showed only Oct 16-21 cases, no new
findings today. Root cause: BackgroundScheduler's 5am cron doesn't catch up
a fire it missed because the process wasn't running at that exact moment
(this dev backend was restarted repeatedly through the morning) -- the scan
logic itself was fine (verified live: existing cases DO get fresh
observations every day the scan actually runs). catch_up_missed_scan() is
the fix: run once at startup, trigger a scan only if today's calendar date
has no daily_brief row yet."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.app.services import brain, home_briefing

TZ = ZoneInfo("Asia/Colombo")


def test_skips_when_a_scan_already_ran_today(monkeypatch):
    today_iso = datetime.now(TZ).astimezone(ZoneInfo("UTC")).replace(tzinfo=None).isoformat()
    monkeypatch.setattr(brain.appdb, "run_query", lambda query, params=None: [{"generated_at": today_iso}])
    triggered = []
    monkeypatch.setattr(home_briefing, "request_refresh", lambda **kw: triggered.append(kw))

    brain.catch_up_missed_scan()

    assert triggered == []


def test_triggers_when_last_scan_was_yesterday(monkeypatch):
    yesterday_iso = (datetime.now(TZ) - timedelta(days=1)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None).isoformat()
    monkeypatch.setattr(brain.appdb, "run_query", lambda query, params=None: [{"generated_at": yesterday_iso}])
    triggered = []
    monkeypatch.setattr(home_briefing, "request_refresh", lambda **kw: triggered.append(kw))

    brain.catch_up_missed_scan()

    assert triggered == [{"retry": True}]


def test_triggers_when_no_scan_has_ever_run(monkeypatch):
    monkeypatch.setattr(brain.appdb, "run_query", lambda query, params=None: [])
    triggered = []
    monkeypatch.setattr(home_briefing, "request_refresh", lambda **kw: triggered.append(kw))

    brain.catch_up_missed_scan()

    assert triggered == [{"retry": True}]
