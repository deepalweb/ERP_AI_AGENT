"""Monitoring half of the Business Brain (#12) for state findings (stock-
outs, dead/overstock value, top-seller stock, dark outlets) — the sibling
tracking.py can't cover, since tracking.py's check_outcomes() assumes a
"YYYY-MM" period key that a point-in-time snapshot doesn't have.

State findings recompute fresh from live data every scan and simply omit
a metric once it's no longer a problem (see state_findings.py's own
threshold checks) — so "did this resolve" is directly observable from
whether today's scan still produces that metric_id, no stored baseline
period needed the way a month is. What IS needed is a starting value to
compare against once enough time has passed, which is what this module
stores.

Nothing here touches the customer's Odoo database; app.db (or
APP_DATABASE_URL) is entirely separate app state, same as tracking.py.
"""

from datetime import date, datetime, timedelta

from backend.app.db import appdb
from backend.app.services import finding_actions
from backend.app.services.tracking import verdict_for_deviation

CHECK_AFTER_DAYS = 7  # same week-length cadence weekly_streaks/trailing windows already use elsewhere in this codebase

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS tracked_state_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_id TEXT NOT NULL,
            category TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            detected_value REAL,
            check_after_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            checked_at TEXT,
            outcome_value REAL,
            verdict TEXT,
            action_status_during_run TEXT
        )
    """)
    # Same one-time correction as tracking.py's identical fix: a row marked
    # 'resolved' with a non-improved verdict was mislabeled by the old
    # unconditional check_outcomes() logic below. Absence-based resolution
    # (verdict='improved', outcome_value=0) is unaffected -- only the
    # still-present, compared-by-value path could produce a bad verdict.
    appdb.run_query(
        "UPDATE tracked_state_findings SET status = 'checked_unresolved' "
        "WHERE status = 'resolved' AND verdict IS NOT NULL AND verdict != 'improved'"
    )
    _SCHEMA_READY = True


def record_new_findings(state_findings: list[dict]):
    """Persist any state finding not already being tracked. Only one open
    row per metric_id at a time — if the problem is already being tracked,
    leave the stored detected_value alone so the eventual comparison stays
    anchored to when it was first noticed, not a moving target."""
    _ensure_schema()
    open_rows = appdb.run_query("SELECT metric_id FROM tracked_state_findings WHERE status = 'open'")
    already_tracked = {r["metric_id"] for r in open_rows}

    today = date.today()
    check_after_date = (today + timedelta(days=CHECK_AFTER_DAYS)).isoformat()

    for f in state_findings:
        if f["metric_id"] in already_tracked:
            continue
        appdb.run_query(
            """
            INSERT INTO tracked_state_findings
                (metric_id, category, detected_at, detected_value, check_after_date, status)
            VALUES (:metric_id, :category, :detected_at, :detected_value, :check_after_date, 'open')
            """,
            {
                "metric_id": f["metric_id"],
                "category": f["category"],
                "detected_at": today.isoformat(),
                "detected_value": f["confirm"]["current"],
                "check_after_date": check_after_date,
            },
        )


def check_outcomes(state_findings: list[dict]):
    """For every open tracked state finding: if it's absent from today's
    freshly recomputed state_findings list, the problem is gone — resolve
    immediately with verdict 'improved', no need to wait for
    check_after_date. If still present and check_after_date has passed,
    resolve now comparing detected_value against today's value. Otherwise
    leave it open."""
    _ensure_schema()
    current_by_metric = {f["metric_id"]: f for f in state_findings}
    today_iso = date.today().isoformat()

    open_rows = appdb.run_query("SELECT * FROM tracked_state_findings WHERE status = 'open'")
    for row in open_rows:
        current = current_by_metric.get(row["metric_id"])

        if current is None:
            outcome_value = 0
            verdict = "improved"
        elif today_iso >= row["check_after_date"]:
            outcome_value = current["confirm"]["current"]
            verdict = verdict_for_deviation(row["detected_value"], outcome_value)
        else:
            continue

        # Same fix as tracking.py: only an 'improved' outcome is a real
        # resolution. A row that's still present and compared by value can
        # come back 'worsened'/'unchanged' -- that's a checked outcome, not
        # a resolved one. (The current is None / absence branch above is
        # always 'improved' by construction, so it's unaffected.)
        status = "resolved" if verdict == "improved" else "checked_unresolved"

        action_status = finding_actions.best_status_for_metric(row["metric_id"], row["detected_at"], today_iso)
        appdb.run_query(
            """
            UPDATE tracked_state_findings
            SET status = :status, checked_at = :checked_at, outcome_value = :outcome_value,
                verdict = :verdict, action_status_during_run = :action_status
            WHERE id = :id
            """,
            {
                "status": status,
                "checked_at": datetime.utcnow().isoformat(),
                "outcome_value": outcome_value,
                "verdict": verdict,
                "action_status": action_status,
                "id": row["id"],
            },
        )


def get_tracking_history(limit: int = 20) -> list[dict]:
    _ensure_schema()
    return appdb.run_query(
        "SELECT * FROM tracked_state_findings ORDER BY detected_at DESC LIMIT :limit",
        {"limit": limit},
    )
