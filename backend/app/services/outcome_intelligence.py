"""Phase 5 (Outcome Intelligence / Learning) v1: a cross-metric "is this
working" scorecard — one honest, aggregate view of every tracked
finding's real outcome, across all three finding shapes.

Deliberately NOT a per-metric "which recommendations work" engine yet:
checked live before building this and no single metric has more than 1
resolved outcome in tracked_findings, while tracked_state_findings and
weekly_streaks currently have ZERO resolved verdicts at all. A per-metric
pattern claim from N<=1 would be exactly the fabricated-confidence
problem this app avoids everywhere else (evidence_engine's
LOW_CONFIDENCE_MONTH_FLOOR, snapshot staleness gates, impact_engine's
honest None for unpriceable metrics). This module instead answers the
one question that's honestly answerable today — in aggregate, across
every finding Celeste has ever tracked, did things tend to improve? —
and is the foundation a real per-metric engine builds on once enough
resolved outcomes exist.

Reads only; never touches the customer's Odoo database, same as every
other tracking module.
"""

from backend.app.db import appdb
from backend.app.services import state_tracking, tracking, weekly_streaks

# Below this many RESOLVED findings (across all 3 shapes combined), the
# improved/worsened/unchanged split is more likely to be sampling noise
# than a real signal -- same "state the floor, don't hide it" posture as
# evidence_engine.LOW_CONFIDENCE_MONTH_FLOOR. 20 is a judgment call, not a
# statistically derived number: enough that one metric's single resolved
# outcome can't look like a company-wide trend.
MIN_RESOLVED_FOR_CONFIDENCE = 20

ENGAGED_STATUSES = {"in_progress", "completed"}


def _tracked_findings_rows() -> list[dict]:
    tracking._ensure_schema()
    return appdb.run_query(
        "SELECT detected_at, verdict, action_status_at_check AS action_status FROM tracked_findings"
    )


def _tracked_state_findings_rows() -> list[dict]:
    state_tracking._ensure_schema()
    return appdb.run_query(
        "SELECT detected_at, verdict, action_status_during_run AS action_status FROM tracked_state_findings"
    )


def _weekly_streaks_rows() -> list[dict]:
    weekly_streaks._ensure_schema()
    return appdb.run_query(
        "SELECT started_date AS detected_at, verdict, action_status_during_run AS action_status FROM weekly_streaks"
    )


def get_summary() -> dict:
    """Combines all 3 tracking tables into one scorecard. A row counts as
    "resolved" when verdict IS NOT NULL -- the one consistent, comparable
    definition across all three (their `status` columns use different
    vocabularies: open/resolved, open/resolved, ongoing/ended). "Engaged"
    means the row's action-status column — already populated by Phase 4's
    finding_actions.best_status_for_metric — is in_progress or completed,
    not null/not_started."""
    rows = _tracked_findings_rows() + _tracked_state_findings_rows() + _weekly_streaks_rows()

    total_tracked = len(rows)
    resolved_rows = [r for r in rows if r["verdict"] is not None]
    resolved = len(resolved_rows)

    verdicts = {"improved": 0, "worsened": 0, "unchanged": 0}
    for r in resolved_rows:
        if r["verdict"] in verdicts:
            verdicts[r["verdict"]] += 1

    engaged_resolved = sum(1 for r in resolved_rows if r["action_status"] in ENGAGED_STATUSES)

    detected_dates = [r["detected_at"] for r in rows if r["detected_at"]]
    oldest_detected_at = min(detected_dates) if detected_dates else None

    return {
        "total_tracked": total_tracked,
        "resolved": resolved,
        "pending": total_tracked - resolved,
        "verdicts": verdicts,
        "engaged_resolved": engaged_resolved,
        "oldest_detected_at": oldest_detected_at,
        "confidence": "sufficient" if resolved >= MIN_RESOLVED_FOR_CONFIDENCE else "low_confidence",
    }
