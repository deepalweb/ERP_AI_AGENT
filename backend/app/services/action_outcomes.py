"""Phase 2d: measures whether a completed action's declared, validated
target was actually reached -- separate from and never proof of whether
that action CAUSED anything (see module docstring in case_engine.py's
OpportunityPolicy). Sibling to tracking.py/state_tracking.py: those watch
whether a METRIC recovered on its own; this watches whether a specific
ACTION's stated objective was met, on its own measurement schedule
(anchored to when the action finished, not the case's own cadence).

Phase 2e additions: a target now declares its own measurement cadence
(target_measurement_period, "monthly" or "weekly" -- see finding_actions.
set_action) and is evaluated against the matching trend source, never a
monthly trend substituted for a weekly request or vice versa (Task 4).
And a target whose required period never arrives doesn't wait forever in
silence: outcome_status distinguishes "pending" (still within a normal
wait), "blocked" (overdue -- worth a manager's attention), and
"inconclusive" (given up after a longer deadline -- an honest terminal
state, never presented as "failed") (Task 3).

Nothing here touches the customer's Odoo database directly; it reads
through intelligence.get_trend/get_trailing_trend (the same deterministic,
already-used metric evaluation every other check in this codebase reuses)
and writes only to finding_actions/business_cases in app.db.
"""

from datetime import date, datetime, timedelta

from backend.app.db import appdb
from backend.app.services import case_engine, finding_actions
from mcp_server.tools import intelligence

# How many days past a required period's earliest possible end a still-
# unmeasured target is allowed to sit as ordinary "pending" before it's
# surfaced as "blocked" (worth a manager's attention), and how many days
# past that before it's given up on entirely as "inconclusive" -- a
# monthly metric's pipeline naturally has more slack than a weekly one's.
# Deliberately generous, configurable constants, not a computed forecast:
# see module docstring, Task 3 asks for "a reasonable configurable
# deadline based on metric cadence," not a general SLA/timeout framework.
_GRACE_DAYS = {"monthly": 45, "weekly": 10}
_INCONCLUSIVE_AFTER_DAYS = {"monthly": 120, "weekly": 30}


def _today() -> date:
    return date.today()


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "")).date()


def _first_complete_period_after(completed_at: str) -> str:
    """The first COMPLETE calendar month strictly after the month an
    action finished in -- e.g. completed 2026-07-15 -> required period
    '2026-08'. The completion month itself is never used: it's a mix of
    pre- and post-intervention data, not a clean post-intervention read."""
    completed_date = _parse_date(completed_at)
    idx = completed_date.year * 12 + (completed_date.month - 1) + 1
    y, m = divmod(idx, 12)
    return f"{y:04d}-{m + 1:02d}"


def _period_end_date(period: str) -> date:
    """The first calendar day on which the given 'YYYY-MM' period is
    definitely fully elapsed -- used as the deadline anchor for
    _pending_outcome_status, independent of whether the trend data has
    actually caught up to it yet."""
    y, m = (int(x) for x in period.split("-"))
    y2, m2 = (y, m + 1) if m < 12 else (y + 1, 1)
    return date(y2, m2, 1)


def _first_complete_weekly_window(metric, completed_at: str):
    """The trailing-trend counterpart to _first_complete_period_after +
    the monthly trend lookup, combined: finds the most recent trailing-
    7-day window that starts at or after the action's completion date
    (never a window straddling the intervention), among the windows
    get_trailing_trend() currently has data for. Returns (row_or_None,
    period_end_date, label) -- period_end_date is always computable from
    completed_at alone (the earliest a clean window could possibly end),
    independent of whether that window actually exists in the trend yet,
    so it can anchor the pending/blocked/inconclusive deadline even when
    row is None."""
    completed_date = _parse_date(completed_at)
    period_end_date = completed_date + timedelta(days=7)
    trend, as_of = intelligence.get_trailing_trend(metric)
    if not trend or as_of is None:
        return None, period_end_date, None
    as_of_date = as_of.date() if hasattr(as_of, "date") else as_of
    # trend is oldest-first, latest-last (see get_trailing_trend) -- the
    # last entry is periods_ago=0, so walking it in reverse recovers each
    # row's periods_ago by its position.
    for periods_ago, row in enumerate(reversed(trend)):
        window_end = as_of_date - timedelta(days=7 * periods_ago)
        window_start = window_end - timedelta(days=7)
        if window_start >= completed_date:
            return row, window_end, row["month"]
    return None, period_end_date, None


def _pending_outcome_status(period_end_date: date, cadence: str) -> str:
    days_overdue = (_today() - period_end_date).days
    if days_overdue <= _GRACE_DAYS.get(cadence, 45):
        return "pending"
    if days_overdue <= _INCONCLUSIVE_AFTER_DAYS.get(cadence, 120):
        return "blocked"
    return "inconclusive"


def _influence_window(row: dict) -> tuple[date, date]:
    """The [start, end) calendar-date range a completed action's outcome
    measurement draws on -- used only to detect whether two actions on the
    same case overlap (outcome_confidence), never to merge or exclude
    either action's own result. Computed from real dates regardless of
    cadence, so a monthly action and a weekly action on the same case
    compare on the same footing."""
    completed_date = _parse_date(row["completed_at"])
    if (row.get("target_measurement_period") or "monthly") == "weekly":
        return completed_date, completed_date + timedelta(days=7)
    return completed_date, _period_end_date(_first_complete_period_after(row["completed_at"]))


def _windows_overlap(a: dict, b: dict) -> bool:
    a_start, a_end = _influence_window(a)
    b_start, b_end = _influence_window(b)
    return a_start < b_end and b_start < a_end


def check_outcomes() -> None:
    """For every completed action with a validated target whose outcome
    hasn't reached a terminal state yet: find the required post-
    intervention period in the metric's own trend -- monthly
    (intelligence.get_trend) or weekly (intelligence.get_trailing_trend),
    per the target's own declared target_measurement_period, never one
    substituted for the other (Task 4) -- and only measure once that
    EXACT period exists and is complete. Reads only; never invents a
    value for a period that isn't there yet.

    A target that stays unmeasurable updates its outcome_status every
    call (pending -> blocked -> inconclusive as real time passes, see
    _pending_outcome_status) without ever setting outcome_verdict until a
    real measurement lands. outcome_checked_at is set (making the row
    terminal and dropping it from future runs) once a real met/not_met
    verdict is reached -- but deliberately NOT when a row merely reaches
    "inconclusive": that state means "no clean measurement has arrived
    within a reasonable window," not "we're done looking." If the
    pipeline was down and later recovers, or a delayed data source
    finally lands the required period, an inconclusive action's outcome
    can still resolve for real (Phase 2e post-acceptance audit, Task 1
    point 6 / Task 4) -- the WHERE clause below re-includes any row still
    at outcome_status='inconclusive' on every call, alongside the
    ordinary not-yet-checked ones, so it keeps getting a chance to
    measure. If a genuine measurement lands, this overwrites the
    inconclusive placeholder with a real verdict and audits the case-
    level correction the same way a late-but-conclusive result always has
    (_maybe_correct_case_resolution, via case_engine.correct_resolution's
    own append-only history). If nothing has changed, _pending_outcome_
    status recomputes "inconclusive" again -- a harmless, idempotent
    re-write (days-overdue only grows with real time, so it can't drift
    back to "pending"/"blocked" once genuinely past the deadline)."""
    finding_actions._ensure_schema()
    case_engine._ensure_schema()
    rows = appdb.run_query(
        "SELECT * FROM finding_actions WHERE status='completed' AND completed_at IS NOT NULL "
        "AND target_metric_id IS NOT NULL AND (outcome_checked_at IS NULL OR outcome_status='inconclusive')"
    )
    if not rows:
        return

    case_ids = {r["case_id"] for r in rows if r["case_id"]}
    actions_by_case = {
        cid: appdb.run_query(
            "SELECT finding_id, completed_at, target_measurement_period FROM finding_actions "
            "WHERE case_id=:c AND target_metric_id IS NOT NULL AND completed_at IS NOT NULL",
            {"c": cid},
        )
        for cid in case_ids
    }

    for row in rows:
        metric = intelligence.METRICS.get(row["target_metric_id"])
        if metric is None:
            continue  # defensive only -- target_metric_id is validated at write time (set_action)

        cadence = row.get("target_measurement_period") or "monthly"
        if cadence == "weekly":
            period_row, period_end_date, period_label = _first_complete_weekly_window(metric, row["completed_at"])
        else:
            required_period = _first_complete_period_after(row["completed_at"])
            period_end_date = _period_end_date(required_period)
            trend = intelligence.get_trend(metric)
            period_row = next((r for r in trend if r["month"] == required_period and not r.get("is_partial")), None)
            period_label = required_period

        if period_row is None:
            status = _pending_outcome_status(period_end_date, cadence)
            updates = {"outcome_status": status}
            if status == "inconclusive":
                updates["outcome_checked_at"] = datetime.utcnow().isoformat()
            finding_actions.store_outcome(row, updates)
            _maybe_correct_case_resolution(row["case_id"], row["finding_id"])
            continue  # scenario L/M (too early / still partial), or overdue but not yet given up on

        target = row["target_value"]
        if row.get("target_baseline_relative"):
            target = (row.get("target_baseline_value") or 0) + target
        verdict = "met" if case_engine._compare(period_row["value"], target, row["target_comparison"]) else "not_met"

        confidence = "single_action"
        if row["case_id"]:
            siblings = [a for a in actions_by_case.get(row["case_id"], []) if a["finding_id"] != row["finding_id"]]
            if any(_windows_overlap(row, s) for s in siblings):
                confidence = "overlapping_actions"

        finding_actions.store_outcome(row, {
            "outcome_checked_at": datetime.utcnow().isoformat(), "outcome_value": period_row["value"],
            "outcome_period": period_label, "outcome_verdict": verdict, "outcome_status": verdict,
            "outcome_confidence": confidence,
        })
        _maybe_correct_case_resolution(row["case_id"], row["finding_id"])


def _maybe_correct_case_resolution(case_id: str | None, finding_id: str) -> None:
    """An action's outcome can resolve (or reach a final 'inconclusive')
    AFTER its case already closed -- scoped narrowly and traceably: only
    when the closed case's objective was EXPLICITLY designated from this
    exact action (see case_engine.designate_case_objective_from_action),
    never for the implicit "any relevant action" fallback path, where
    re-deriving a verdict after the fact from several actions risks
    flip-flopping. Re-derives the verdict via case_engine.
    _resolve_from_action_outcome (the exact same function the live
    resolution policy uses) rather than recomputing it here, so the two
    can never disagree. correct_resolution itself never overwrites
    silently -- the prior verdict is preserved in case_history."""
    if not case_id:
        return
    case = case_engine.get_case(case_id)
    if case is None or case["status"] != "resolved" or case["category"] != "opportunity":
        return
    if case.get("objective_source_finding_id") != finding_id:
        return
    new_verdict = case_engine.OpportunityPolicy().resolve(case)
    if new_verdict is None:
        return  # still pending/blocked -- nothing to correct yet
    if case["resolution_verdict"] != new_verdict:
        case_engine.correct_resolution(case_id, new_verdict, "action outcome resolved after case closure")
