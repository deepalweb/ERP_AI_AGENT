"""Continuity tracking for short-term (trailing-window) findings — the
counterpart to tracking.py, but for daily streaks instead of monthly
outcomes. investigation.py's trailing findings get a brand-new id every
day (metric-trailing-{as_of_date}, since as_of_date advances nightly) —
which meant a cancellation spike that stayed elevated for 5 straight days
looked like 5 unrelated one-day findings with zero memory of each other.

tracking.py's UNIQUE(metric_id, period) + open/resolved/verdict model
doesn't fit here: a trailing 7-day window has no disjoint "next period" to
compare against the way a calendar month does. Instead this tracks a
simple, honest streak — how many consecutive *scanned* days has this
metric stayed flagged — and when a streak ends (the metric stops being
flagged), that ending is itself the "did it get better" signal, with no
baseline/future-period math needed.

Nothing here touches the customer's Odoo database; app.db (or
APP_DATABASE_URL) is entirely separate app state, same as tracking.py.
"""

from datetime import date, timedelta

from backend.app.db import appdb
from mcp_server.tools import intelligence

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS weekly_streaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_id TEXT NOT NULL,
            category TEXT NOT NULL,
            started_date TEXT NOT NULL,
            last_flagged_date TEXT NOT NULL,
            consecutive_days INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'ongoing'
        )
    """)
    _SCHEMA_READY = True


def _prior_day(date_str: str) -> str:
    y, m, d = map(int, date_str.split("-"))
    return (date(y, m, d) - timedelta(days=1)).isoformat()


def record_streaks(short_term_findings: list[dict]):
    """Called once per daily scan (brain.run_daily_scan, right after
    investigation.scan_short_term()) — updates the streak table from
    today's flagged short-term findings and closes out any streak whose
    metric is no longer flagged. Idempotent for a given as_of_date:
    re-running the scan for the same day (e.g. a forced recompute during
    verification) doesn't double-count that day."""
    _ensure_schema()

    flagged_by_metric = {f["metric_id"]: f for f in short_term_findings if f.get("as_of_date")}

    ongoing_rows = appdb.run_query("SELECT * FROM weekly_streaks WHERE status = 'ongoing'")
    ongoing_by_metric = {r["metric_id"]: r for r in ongoing_rows}

    for metric_id, finding in flagged_by_metric.items():
        as_of_date = finding["as_of_date"]
        row = ongoing_by_metric.get(metric_id)

        if row and row["last_flagged_date"] == as_of_date:
            continue  # already recorded today's flag for this metric

        if row and row["last_flagged_date"] == _prior_day(as_of_date):
            appdb.run_query(
                """UPDATE weekly_streaks SET last_flagged_date = :as_of_date,
                   consecutive_days = consecutive_days + 1, category = :category
                   WHERE id = :id""",
                {"as_of_date": as_of_date, "category": finding["category"], "id": row["id"]},
            )
        else:
            if row:
                # A gap in flagging that the "close out" step below didn't
                # already catch (e.g. a missed scan day) — the old streak
                # is stale, close it before starting a fresh one.
                appdb.run_query("UPDATE weekly_streaks SET status = 'ended' WHERE id = :id", {"id": row["id"]})
            appdb.run_query(
                """INSERT INTO weekly_streaks (metric_id, category, started_date, last_flagged_date, consecutive_days, status)
                   VALUES (:metric_id, :category, :as_of_date, :as_of_date, 1, 'ongoing')""",
                {"metric_id": metric_id, "category": finding["category"], "as_of_date": as_of_date},
            )

    for metric_id, row in ongoing_by_metric.items():
        if metric_id not in flagged_by_metric:
            appdb.run_query("UPDATE weekly_streaks SET status = 'ended' WHERE id = :id", {"id": row["id"]})


def get_active_streaks() -> dict:
    """metric_id -> {started_date, consecutive_days} for every metric
    currently flagged on a trailing-window scan — merged onto each short-
    term finding so a card can say "day 4 of this issue" instead of
    restarting the count every day."""
    _ensure_schema()
    rows = appdb.run_query("SELECT * FROM weekly_streaks WHERE status = 'ongoing'")
    return {r["metric_id"]: {"started_date": r["started_date"], "consecutive_days": r["consecutive_days"]} for r in rows}


def get_recently_ended_streaks(limit: int = 10) -> list[dict]:
    """Streaks that stopped being flagged — the honest 'this got better'
    signal for short-term findings, surfaced without waiting for a future
    calendar period the way tracking.py's monthly outcome check does."""
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT * FROM weekly_streaks WHERE status = 'ended' ORDER BY id DESC LIMIT :limit",
        {"limit": limit},
    )
    return [
        {
            "metric_id": r["metric_id"],
            "metric_label": intelligence.METRICS[r["metric_id"]].label if r["metric_id"] in intelligence.METRICS else r["metric_id"],
            "category": r["category"],
            "started_date": r["started_date"],
            "last_flagged_date": r["last_flagged_date"],
            "consecutive_days": r["consecutive_days"],
        }
        for r in rows
    ]
