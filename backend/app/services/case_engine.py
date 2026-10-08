"""Phase 2a of the Business Case Store: turns the per-scan finding lists
scan_all()/build_inventory_findings()/scan_short_term() already produce into
persistent cases that survive across scan cycles.

Deliberately minimal, per the reviewed design: no scope-based case
splitting (a finding's confirmed segments are evidence ON a case, never a
second axis of case identity -- see _scope_from_checks), no fuzzy/overlap
matching, no rewrite of tracking.py/state_tracking.py/weekly_streaks.py
(they keep running unchanged; this is a layer above them). Case identity
is the finding's own real, already-emitted reporting scope --
(metric_id, domain, horizon) -- never an invented per-segment entity ID.

Concurrency: run_daily_scan() (brain.py) already holds a cross-process
FileLock for its entire duration, which is what actually prevents two
scans from racing here in normal operation. The partial UNIQUE index below
is a backstop for any caller that invokes sync_cases() outside that lock
(a direct/manual call, a future API trigger), not the primary defense.

Calendar-day logic (scan_run_id, the grace-period cutoff, and a
weekly/snapshot case's verification cadence) uses the same business
timezone the daily scheduler already runs on (APP_TIMEZONE, default
Asia/Colombo -- see main.py's BackgroundScheduler), not the server's
local/UTC day -- a scan running late at night UTC is already "tomorrow"
in Colombo, and using the wrong day here would misfire both the
same-day-observation dedupe and the verification-window count.
"""

import json
import math
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from filelock import Timeout
from sqlalchemy.exc import IntegrityError

from backend.app.db import appdb
from backend.app.services import related_signals
from mcp_server.tools import intelligence, measurement

GRACE_PERIOD_DAYS = 30  # how far back a closed case is still considered for recurrence/reopen matching
VERIFICATION_WINDOW = 2  # consecutive fresh, clean cadence units required before auto-resolving

_ACTIVE_STATUSES = ("open", "verification_pending", "reopened")
_ACTIVE_STATUSES_SQL = "(" + ",".join(f"'{s}'" for s in _ACTIVE_STATUSES) + ")"
_CLOSED_STATUSES_SQL = "('resolved','dismissed')"

# The small, fixed comparison vocabulary a target/objective may use --
# shared between case-level objectives and action-level targets
# (action_outcomes.py imports this) so both validate and compare the
# exact same way, never inventing a semantics per numeric target.
COMPARISONS = {">=", "<=", ">", "<", "=="}

# The two measurement cadences this codebase's trend data actually
# supports (see mcp_server.tools.intelligence.get_trend / get_trailing_
# trend) -- "weekly" is only valid for a metric that declares
# short_term_trend_sql; there is no daily cadence anywhere in this system,
# so a target must never silently claim one (Phase 2e Task 4).
MEASUREMENT_PERIODS = {"monthly", "weekly"}

# How long an interactive action-save is willing to wait for the daily
# scan to release the shared case-store lock before giving up and asking
# the caller to retry -- short on purpose, since this sits on a request a
# person is waiting on, unlike the scan itself (which can hold this same
# lock for its full 80-100s run -- see appdb.case_store_lock).
ACTION_SAVE_LOCK_TIMEOUT = 8

_SCHEMA_READY = False


class StaleEvidenceError(Exception):
    """Raised by verify_evidence_version when the finding's evidence has
    moved on since the version a manager last reviewed -- the server-side
    reassertion of the same check CaseDetail.jsx already performs
    client-side, closing the gap between the client's own pre-save refetch
    and the moment this write actually reaches the database (a scan can
    still land in that gap)."""

    def __init__(self, current_version: str, current_observed_at: str, current_snapshot: dict):
        self.current_version = current_version  # opaque token -- see _observation_version
        self.current_observed_at = current_observed_at  # display only, never compared -- see verify_evidence_version
        self.current_snapshot = current_snapshot
        super().__init__("Evidence has changed since it was last reviewed.")


class ScanInProgressError(Exception):
    """Raised by save_action_with_verification when it can't acquire the
    shared case-store lock within ACTION_SAVE_LOCK_TIMEOUT -- almost
    always because the daily scan is actively holding it. Nothing has
    actually gone stale; the caller should simply retry shortly, distinct
    from StaleEvidenceError's "your decision is based on old evidence"."""


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS business_cases (
            case_id TEXT PRIMARY KEY,
            metric_id TEXT NOT NULL,
            domain TEXT NOT NULL,
            horizon TEXT NOT NULL,
            category TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            resolution_policy TEXT NOT NULL DEFAULT 'return_to_baseline',
            verification_progress INTEGER NOT NULL DEFAULT 0,
            last_verified_period TEXT,
            scope_status TEXT,
            scope_segments TEXT,
            opened_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            closed_at TEXT,
            dismissed_reason TEXT,
            priority TEXT,
            business_impact_score REAL
        )
    """)
    # At most one active case per (metric_id, domain, horizon) -- the real
    # backstop for the matching policy below. 'reopened' is deliberately
    # included: a reopened case is active and must block a second case
    # from opening for the same key just as much as 'open' does.
    appdb.run_query(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_business_cases_active
        ON business_cases(metric_id, domain, horizon)
        WHERE status IN {_ACTIVE_STATUSES_SQL}
    """)
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS case_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            scan_run_id TEXT NOT NULL,
            finding_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            snapshot TEXT NOT NULL
        )
    """)
    # scan_run_id (the calendar day sync_cases() actually ran), not
    # finding_id, is the real idempotency key: a monthly finding's id
    # ("revenue-2026-09") stays identical for every daily scan within that
    # month, so keying on finding_id alone would silently drop every
    # observation after the first day of the month.
    appdb.run_query("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_case_observations_run
        ON case_observations(case_id, scan_run_id)
    """)
    # revision: a monotonic counter incremented on every write to a given
    # (case_id, scan_run_id) row (see _record_observation) -- the actual
    # version token verify_evidence_version compares, NOT observed_at.
    # observed_at is wall-clock time (datetime.utcnow().isoformat()),
    # which is not guaranteed unique: two writes to the SAME row landing
    # within the same clock tick (a real, confirmed risk on Windows, whose
    # default timer resolution is coarser than a microsecond) would
    # otherwise produce identical observed_at strings for DIFFERENT
    # content, silently defeating the staleness check (post-Phase-2e
    # acceptance audit, Task 1 point 2). observed_at is kept purely for
    # human-readable display; it is never compared for staleness anymore.
    try:
        appdb.run_query("ALTER TABLE case_observations ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS case_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            related_case_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS case_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail TEXT
        )
    """)
    # Phase 2d additions -- all additive, best-effort ALTER (business_cases
    # is brand new this project, so these run against an empty table in
    # practice; the pattern still follows every other table here for when
    # that's no longer true). Deliberately NO backfill UPDATE: an already-
    # resolved case's resolution_verdict stays NULL rather than guessing a
    # retrospective "recovered"/"captured" for a case closed before this
    # distinction existed -- see Phase 2d's migration notes.
    for column in (
        "last_measurement TEXT",
        "resolution_verdict TEXT",
        "objective_metric_id TEXT",
        "objective_target_value REAL",
        "objective_comparison TEXT",
        "objective_unit TEXT",
        "objective_baseline_relative INTEGER",
        "objective_baseline_value REAL",
        "objective_source_finding_id TEXT",
        "objective_target_version INTEGER",
    ):
        try:
            appdb.run_query(f"ALTER TABLE business_cases ADD COLUMN {column}")
        except Exception:
            pass
    _SCHEMA_READY = True


def _compare(value: float, target: float, comparison: str) -> bool:
    if comparison == ">=":
        return value >= target
    if comparison == "<=":
        return value <= target
    if comparison == ">":
        return value > target
    if comparison == "<":
        return value < target
    if comparison == "==":
        return value == target
    raise ValueError(f"Unknown comparison: {comparison}")


def _observation_version(row: dict) -> str:
    """The actual, collision-proof version token for a case_observations
    row: "<row id>:<revision>". Neither half is safe alone -- id never
    changes across an update (so it can't detect a same-row rewrite), and
    revision resets to 1 for every NEW row (so a fresh day's row could, by
    sheer coincidence, land on the exact revision value a manager's stale
    cached token from a DIFFERENT day's row also happened to have -- a
    real risk here specifically because a monthly finding's id stays
    identical across many different rows, one per day of the month, all
    matched by the same WHERE finding_id=... in verify_evidence_version).
    The pair together is unique both across rows (distinct id) and across
    rewrites of the same row (increasing revision), so equality on this
    string is a reliable "has anything about this exact evidence changed"
    check -- opaque to callers, never parsed, only ever echoed back."""
    return f"{row['id']}:{row['revision']}"


@contextmanager
def _case_write_lock(timeout: float | None = None):
    """Shared by every case-mutating entry point that's reached directly
    from an API request (save_action_with_verification, dismiss_case,
    reopen_case, set_case_objective, designate_case_objective_from_action)
    -- all of them write to business_cases/case_observations/
    finding_actions, the exact same tables brain.run_daily_scan() mutates
    under this same lock for a scan's whole duration, so all of them need
    the same mutual exclusion against a scan landing mid-operation (Phase
    2e post-acceptance audit, Task 1 point 3 -- dismiss/reopen/objective
    endpoints took no lock at all before this).

    NEVER use this inside a function that might already be called from
    within a scan (e.g. correct_resolution, reached from action_outcomes.
    check_outcomes() -> brain._compute_daily_brief(), already inside
    run_daily_scan()'s own acquisition of the identical lock) -- a second
    acquisition from the same thread would not be reentrant (proven by
    test_phase2e.py's concurrency tests: two FileLock instances for the
    same path block each other even from the same thread) and would
    deadlock until this shorter timeout expired, aborting the scan.

    timeout defaults to None (resolved to ACTION_SAVE_LOCK_TIMEOUT at call
    time, not baked in as a default-argument value at import time) so
    monkeypatching ACTION_SAVE_LOCK_TIMEOUT in a test actually takes
    effect."""
    lock = appdb.case_store_lock()
    try:
        lock.acquire(timeout=timeout if timeout is not None else ACTION_SAVE_LOCK_TIMEOUT)
    except Timeout:
        raise ScanInProgressError("A scan is currently running; please retry in a moment.")
    try:
        yield
    finally:
        lock.release()


def verify_evidence_version(case_id: str, finding_id: str, expected_version: str | None) -> None:
    """expected_version is None for any caller that doesn't track evidence
    versions at all (Home.jsx/Actions.jsx's plain "Track this" form) --
    the check is skipped entirely rather than made mandatory, so those
    flows are completely unaffected. When it IS supplied (the Investigate
    case-detail flow), it must match the finding's current latest
    observation's version token (see _observation_version) exactly, or
    this raises StaleEvidenceError with the fresh evidence attached so the
    caller can show it without a second round trip.

    ORDER BY observed_at DESC (wall-clock time) is safe here for finding
    WHICH row is latest -- a given finding_id gets at most one new
    case_observations row per calendar day (see the scan_run_id
    idempotency key), so two rows for the same finding_id are always at
    least a day apart and can never collide at datetime.utcnow()'s
    resolution. The value being COMPARED, once that row is found, is its
    version token, not observed_at -- see _observation_version for why."""
    if expected_version is None:
        return
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT id, revision, observed_at, snapshot FROM case_observations WHERE case_id=:c AND finding_id=:f "
        "ORDER BY observed_at DESC LIMIT 1",
        {"c": case_id, "f": finding_id},
    )
    if not rows:
        return  # nothing recorded yet for this finding -- nothing to conflict with
    current_version = _observation_version(rows[0])
    if current_version != expected_version:
        raise StaleEvidenceError(current_version, rows[0]["observed_at"], json.loads(rows[0]["snapshot"]))


def save_action_with_verification(finding_id: str, action_kwargs: dict, expected_version: str | None) -> dict:
    """The atomic counterpart to calling verify_evidence_version() and
    finding_actions.set_action() as two separate steps: the version check
    happens under the same case-store lock as the write (via
    _case_write_lock, see its docstring) so a scan cannot land in the gap
    between them, AND the write itself -- finding_actions.set_action() and
    record_decision_snapshot() together -- now runs inside ONE real SQL
    transaction (appdb.get_engine().begin()), so the two either both
    commit or, on any exception, both roll back. Without that transaction,
    a failure in record_decision_snapshot AFTER set_action's write had
    already committed would leave the action row updated with no matching
    audit entry -- a genuine partial-failure risk under the old two-
    independent-writes design (Phase 2e post-acceptance audit, Task 1
    point 1), not merely a concurrency one.

    finding_id's case (if any) is looked up fresh inside the lock, not
    passed in, for the same reason the version check itself is. evidence_
    verified in the case_id-is-not-None branch records whether THIS save
    was actually checked against a specific version (True), or is a
    legacy save that skipped versioning entirely (False, expected_version
    was None) -- distinct from a finding with no case at all, where
    evidence versioning has no meaning (None). Raises StaleEvidenceError
    (a real conflict -- the caller should show the fresh evidence) or
    ScanInProgressError (couldn't get the lock in time -- the caller
    should ask to retry) instead of returning.

    Deliberately has no recommendation-acceptance awareness -- see
    accept_recommendation for that fully separate write path, which never
    calls finding_actions.set_action() at all (a recommendation
    acceptance must never create or touch an operational action row)."""
    _ensure_schema()
    _ensure_finding_actions_schema()
    with _case_write_lock():
        case_id = get_case_id_for_finding(finding_id)
        evidence_verified = None
        if case_id is not None:
            evidence_verified = expected_version is not None
            verify_evidence_version(case_id, finding_id, expected_version)

        from backend.app.services import finding_actions
        with appdb.get_engine().begin() as conn:
            finding_actions.set_action(
                finding_id, case_id=case_id, evidence_verified=evidence_verified,
                evidence_version=expected_version, conn=conn, **action_kwargs,
            )
            if case_id is not None:
                record_decision_snapshot(case_id, finding_id, action_kwargs.get("status"), conn=conn)

        return {"case_id": case_id, "evidence_verified": evidence_verified}


def _business_today() -> date:
    tz = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Colombo"))
    return datetime.now(tz).date()


def _scope_from_checks(checks: list[dict]) -> tuple[str, list[dict]]:
    """Segments are evidence ON a case, never a second axis of case
    identity (see module docstring) -- this only ever informs the case's
    scope_status/scope_segments columns, which are rolling/informational,
    not part of the matching key. Distinguishes an explicitly checked,
    genuinely company-wide movement ('no_evidence_found' on every
    concentration check) from missing/inconclusive evidence ('unknown',
    or no concentration check at all) -- collapsing the two would claim
    certainty ('company-wide') the data doesn't actually support."""
    concentration_checks = [c for c in (checks or []) if c.get("source", "").endswith("_concentration")]
    confirmed = [c for c in concentration_checks if c.get("verdict") == "confirmed"]
    if confirmed:
        segments = []
        for c in confirmed:
            dimension = c["source"][: -len("_concentration")]
            for name in c.get("segments", []):
                segments.append({"dimension": dimension, "segment_name": name})
        return "segment_evidenced", segments
    if any(c.get("verdict") == "no_evidence_found" for c in concentration_checks):
        return "company_wide", []
    return "unknown", []


def _find_active_case(metric_id: str, domain: str, horizon: str) -> dict | None:
    rows = appdb.run_query(
        f"SELECT * FROM business_cases WHERE metric_id=:m AND domain=:d AND horizon=:h "
        f"AND status IN {_ACTIVE_STATUSES_SQL} ORDER BY opened_at DESC LIMIT 1",
        {"m": metric_id, "d": domain, "h": horizon},
    )
    return rows[0] if rows else None


def _find_recent_closed_case(metric_id: str, domain: str, horizon: str, today: date) -> dict | None:
    """Most recent CLOSED case for this key within the grace period -- used
    only to decide whether a new finding might relate to a past incident.
    Never used as proof of resolution on its own (see _reopen_case /
    sync_cases: that decision comes from the prior case's own verified
    status, not from the grace period)."""
    cutoff = (today - timedelta(days=GRACE_PERIOD_DAYS)).isoformat()
    rows = appdb.run_query(
        f"SELECT * FROM business_cases WHERE metric_id=:m AND domain=:d AND horizon=:h "
        f"AND status IN {_CLOSED_STATUSES_SQL} AND closed_at >= :cutoff "
        f"ORDER BY closed_at DESC LIMIT 1",
        {"m": metric_id, "d": domain, "h": horizon, "cutoff": cutoff},
    )
    return rows[0] if rows else None


def _log_history(case_id: str, event_type: str, detail: dict, conn=None) -> None:
    appdb.run_query(
        "INSERT INTO case_history (case_id, at, event_type, detail) VALUES (:c, :at, :e, :d)",
        {"c": case_id, "at": datetime.utcnow().isoformat(), "e": event_type, "d": json.dumps(detail, default=str)},
        conn=conn,
    )


def _open_new_case(metric_id: str, domain: str, horizon: str, finding: dict,
                    scope_status: str, scope_segments: list[dict]) -> str:
    case_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    try:
        appdb.run_query(
            """
            INSERT INTO business_cases
                (case_id, metric_id, domain, horizon, category, status, resolution_policy,
                 verification_progress, scope_status, scope_segments, opened_at, last_seen_at,
                 priority, business_impact_score)
            VALUES (:case_id, :metric_id, :domain, :horizon, :category, 'open', :resolution_policy,
                    0, :scope_status, :scope_segments, :opened_at, :last_seen_at, :priority, :impact)
            """,
            {
                "case_id": case_id,
                "metric_id": metric_id,
                "domain": domain,
                "horizon": horizon,
                "category": finding.get("category"),
                "resolution_policy": _RESOLUTION_POLICY_NAME.get(finding.get("category"), "problem_recovery"),
                "scope_status": scope_status,
                "scope_segments": json.dumps(scope_segments),
                "opened_at": now,
                "last_seen_at": now,
                "priority": finding.get("priority"),
                "impact": finding.get("business_impact_score"),
            },
        )
    except IntegrityError:
        # Lost a race against another sync for this (metric_id, domain,
        # horizon)'s active slot -- attach to whichever case won instead
        # of erroring (see module docstring on concurrency).
        existing = _find_active_case(metric_id, domain, horizon)
        if existing is None:
            raise
        return existing["case_id"]
    _log_history(case_id, "opened", {"detected_value": finding.get("confirm", {}).get("current")})
    return case_id


def _touch_case(case: dict, finding: dict, scope_status: str, scope_segments: list[dict]) -> None:
    """Continuation: same case, another cycle's observation. Only
    'verification_pending' gets reset here -- the case was believed to be
    clearing, and a matching finding just reappeared, so the verification
    window restarts. 'reopened' is left alone (it's a durable marker on
    the case, not reset by ordinary continuation) until it resolves again
    through the same verification path as any other active case."""
    now = datetime.utcnow().isoformat()
    reverting = case["status"] == "verification_pending"
    changes = {key: {"before": case.get(key), "after": finding.get(key)}
               for key in ("category", "priority", "business_impact_score")
               if case.get(key) != finding.get(key)}
    if changes:
        _log_history(case["case_id"], "case_changed", changes)
    reset_sql = ", status='open', verification_progress=0, last_verified_period=NULL" if reverting else ""
    appdb.run_query(
        "UPDATE business_cases SET last_seen_at=:t, category=:cat, priority=:pr, resolution_policy=:policy, "
        f"business_impact_score=:bi, scope_status=:ss, scope_segments=:seg{reset_sql} WHERE case_id=:id",
        {
            "t": now,
            "cat": finding.get("category"),
            "policy": _RESOLUTION_POLICY_NAME.get(finding.get("category"), "problem_recovery"),
            "pr": finding.get("priority"),
            "bi": finding.get("business_impact_score"),
            "ss": scope_status,
            "seg": json.dumps(scope_segments),
            "id": case["case_id"],
        },
    )
    if reverting:
        _log_history(case["case_id"], "verification_reset", {"reason": "matching finding reappeared"})


def _reopen_case(case_id: str, finding: dict, scope_status: str, scope_segments: list[dict], reason: str) -> None:
    now = datetime.utcnow().isoformat()
    appdb.run_query(
        "UPDATE business_cases SET status='reopened', closed_at=NULL, dismissed_reason=NULL, "
        "verification_progress=0, last_verified_period=NULL, last_seen_at=:t, category=:cat, resolution_policy=:policy, "
        "priority=:pr, business_impact_score=:bi, scope_status=:ss, scope_segments=:seg WHERE case_id=:id",
        {
            "t": now,
            "cat": finding.get("category"),
            "policy": _RESOLUTION_POLICY_NAME.get(finding.get("category"), "problem_recovery"),
            "pr": finding.get("priority"),
            "bi": finding.get("business_impact_score"),
            "ss": scope_status,
            "seg": json.dumps(scope_segments),
            "id": case_id,
        },
    )
    _log_history(case_id, "reopened", {"reason": reason})


def _link_relationship(case_id: str, related_case_id: str, relationship_type: str) -> None:
    appdb.run_query(
        "INSERT INTO case_relationships (case_id, related_case_id, relationship_type, created_at) "
        "VALUES (:c, :r, :t, :at)",
        {"c": case_id, "r": related_case_id, "t": relationship_type, "at": datetime.utcnow().isoformat()},
    )


def _record_observation(case_id: str, scan_run_id: str, finding: dict) -> None:
    """Upsert, not INSERT OR IGNORE: (case_id, scan_run_id) is the
    idempotency key for a same-day RE-run with IDENTICAL evidence, but a
    later same-day scan can legitimately see newer evidence (a forced
    rescan, a retried source) -- that must overwrite the day's row, not be
    silently dropped in favour of whichever run happened to land first.

    revision starts at its column default (1) on INSERT and is
    incremented on every UPDATE -- a plain SQLite integer counter, so
    unlike observed_at (wall-clock time) it is guaranteed distinct
    between the morning's and an afternoon's same-day rescan even if both
    happen to land in the same clock tick (see verify_evidence_version)."""
    previous = appdb.run_query(
        "SELECT snapshot FROM case_observations WHERE case_id=:c ORDER BY observed_at DESC LIMIT 1",
        {"c": case_id},
    )
    if previous:
        old = json.loads(previous[0]["snapshot"])
        keys = ("confirm", "root_cause", "impact")
        changed = [key for key in keys if old.get(key) != finding.get(key)]
        if changed:
            _log_history(case_id, "evidence_changed", {
                "fields": changed, "before": {key: old.get(key) for key in changed},
                "after": {key: finding.get(key) for key in changed},
            })
    appdb.run_query(
        """
        INSERT INTO case_observations (case_id, scan_run_id, finding_id, observed_at, snapshot)
        VALUES (:c, :r, :f, :at, :snap)
        ON CONFLICT(case_id, scan_run_id) DO UPDATE SET
            finding_id = excluded.finding_id,
            observed_at = excluded.observed_at,
            snapshot = excluded.snapshot,
            revision = case_observations.revision + 1
        """,
        {
            "c": case_id,
            "r": scan_run_id,
            "f": finding.get("id", ""),
            "at": datetime.utcnow().isoformat(),
            "snap": json.dumps(finding, default=str),
        },
    )


def _current_cadence_unit(horizon: str, metric_id: str, scan_run_id: str, data_fresh: bool) -> str | None:
    """The 'fresh measurement' a resolution check may credit at most once
    per unit. Monthly cases only get a genuinely new data point once a
    new calendar month's COMPLETE trend row lands (is_partial=False --
    re-running the same still-open month daily must not count multiple
    times, and a month that just rolled over but hasn't fully landed yet
    doesn't count either); weekly/snapshot cases recompute live every
    scan, so a new calendar day is normally a fresh point, EXCEPT when
    data_fresh is False -- the underlying order data itself is stale (see
    brain._data_freshness), so an "absent" finding today might just be a
    re-observation of the same stale snapshot as yesterday, not a real
    new measurement. sync_cases requires complete, fresh, explicitly healthy
    coverage before this cadence gate is reached. Monthly verification uses
    the period in that receipt, not a second query that could see newer data."""
    if horizon == "monthly":
        metric = intelligence.METRICS.get(metric_id)
        if metric is None:
            return None
        trend = intelligence.get_trend(metric)
        non_partial = [m for m in trend if not m.get("is_partial")]
        return non_partial[-1]["month"] if non_partial else None
    if not data_fresh:
        return None
    return scan_run_id


# ---------------------------------------------------------------------------
# Resolution policies (Phase 2d). Problem recovery and opportunity capture
# are genuinely different claims -- "the anomaly stopped" is never enough
# on its own to say an opportunity's business objective was achieved (see
# module-level docstring addendum below) -- so they get separate, small
# policy objects rather than one shared branch. Both reuse the exact same
# verification-window/cadence/freshness mechanics in _advance_verification;
# only what counts as a valid CLOSURE VERDICT differs.
#
# .resolve(case) -> str | None:
#   a string verdict closes the case now; None means "hold" -- the metric
#   side of verification is satisfied, but something else (an approved
#   action's outcome measurement) hasn't landed yet, so the case stays in
#   verification_pending rather than closing on an incomplete picture.
# ---------------------------------------------------------------------------

_RESOLUTION_POLICY_NAME = {"critical": "problem_recovery", "opportunity": "opportunity_capture"}


class ProblemRecoveryPolicy:
    """A critical finding clearing its own anomaly threshold for
    VERIFICATION_WINDOW consecutive clean cycles IS the claim -- 'the
    problem recovered' needs nothing beyond continued absence, same as the
    original Phase 2a policy. Never holds."""

    def resolve(self, case: dict) -> str | None:
        return "recovered"


class OpportunityPolicy:
    """'The opportunity stopped being flagged' is NOT 'the opportunity was
    captured' -- it could just as easily mean it expired unclaimed. This
    only ever calls it "captured" against a verified, specific business
    objective: either the case's own directly-declared objective, or one
    explicitly designated from a validated action target. No objective at
    all -> "expired" once verification completes (the honest default, per
    review: an opportunity must not be marked captured from absence
    alone). An objective that exists but whose measurement isn't ready
    yet -> hold (None), never a premature "expired"."""

    def resolve(self, case: dict) -> str | None:
        if case.get("objective_source_finding_id"):
            action = appdb.run_query("SELECT * FROM finding_actions WHERE finding_id=:f",
                                     {"f": case["objective_source_finding_id"]})
            if not action or action[0].get("case_id") != case["case_id"] or not action[0].get("relevant"):
                return None
            if case.get("objective_target_version") != action[0].get("target_version"):
                return None  # target changed; a manager must designate the new version
            return _resolve_from_action_outcome(case["objective_source_finding_id"])
        if case.get("objective_metric_id"):
            return _resolve_case_direct_objective(case)
        return _resolve_from_relevant_actions(case["case_id"])


RESOLUTION_POLICIES: dict[str, object] = {
    "critical": ProblemRecoveryPolicy(),
    "opportunity": OpportunityPolicy(),
}


def _ensure_finding_actions_schema() -> None:
    # Lazy, local import -- case_engine.py and finding_actions.py each
    # need a small piece of the other (finding_actions imports COMPARISONS
    # from here; the two functions below need finding_actions' table to
    # exist), so a module-level import either direction would be
    # circular. Same pattern investigation.py already uses for
    # backend.app.services.operations, for the same reason.
    from backend.app.services import finding_actions
    finding_actions._ensure_schema()


def _resolve_from_action_outcome(finding_id: str) -> str | None:
    """outcome_status (set by action_outcomes.check_outcomes(), see Phase
    2e Task 3) carries more states than outcome_verdict alone: 'pending'
    and 'blocked' both still mean "hold, we don't know yet" -- the
    distinction between them is for a human reading the Case Detail page,
    not for this policy decision. 'inconclusive' means check_outcomes()
    gave up waiting for a clean measurement after a reasonable deadline --
    an honest terminal state, not a hold, and not a failure either."""
    _ensure_finding_actions_schema()
    rows = appdb.run_query(
        "SELECT outcome_status, outcome_confidence FROM finding_actions WHERE finding_id=:f", {"f": finding_id}
    )
    if not rows:
        return None
    status = rows[0]["outcome_status"]
    if status in (None, "pending", "blocked"):
        return None  # the designated action's outcome hasn't been established yet -- hold
    if status == "inconclusive":
        return "inconclusive"
    if status == "met":
        return "inconclusive" if rows[0]["outcome_confidence"] == "overlapping_actions" else "captured"
    return "expired"  # status == "not_met"


def _resolve_from_relevant_actions(case_id: str) -> str | None:
    """No case-level objective was ever declared -- fall back to any
    relevant, approved action's own (already-validated) target. 'Relevant,
    approved' = a manager actually engaged with it (relevant=1) and moved
    it past a bare proposal (in_progress or completed), with a declared,
    validated target -- a mere untouched recommendation never blocks
    closure. See _resolve_from_action_outcome for why outcome_status,
    not outcome_verdict, is the field consulted here."""
    _ensure_finding_actions_schema()
    actions = appdb.run_query(
        "SELECT outcome_status, outcome_confidence FROM finding_actions WHERE case_id=:c AND relevant=1 "
        "AND status IN ('in_progress','completed') AND target_metric_id IS NOT NULL",
        {"c": case_id},
    )
    if not actions:
        return "expired"
    if any(a["outcome_status"] in (None, "pending", "blocked") for a in actions):
        return None  # at least one relevant action's outcome is still awaiting measurement -- hold
    if any(a["outcome_status"] == "met" and a["outcome_confidence"] == "single_action" for a in actions):
        return "captured"
    if any(a["outcome_status"] == "met" and a["outcome_confidence"] == "overlapping_actions" for a in actions):
        return "inconclusive"
    if any(a["outcome_status"] == "inconclusive" for a in actions):
        return "inconclusive"
    return "expired"  # every relevant action's own outcome was measured "not_met"


def _resolve_case_direct_objective(case: dict) -> str | None:
    """A case-level objective set independently of any specific action.
    Branches on the CASE's own horizon/cadence (Phase 2e Task 4): a
    monthly case is measured against case['last_verified_period'] -- the
    exact month the absence-verification pass just confirmed fresh and
    complete, matched by key against intelligence.get_trend()'s monthly
    rows. A weekly case's last_verified_period is NOT a month key at all
    -- it's the calendar day (scan_run_id) _current_cadence_unit uses for
    weekly/snapshot cadence -- so matching it against get_trend()'s "YYYY-
    MM" rows would never find a row and silently hold forever; that
    combination is instead measured against the metric's own trailing-
    7-day trend (get_trailing_trend), using its freshest complete window
    directly. set_case_objective() already refuses to pair a weekly case
    with a metric that has no trailing-trend support, and refuses a
    snapshot-horizon case outright, so those combinations should never
    reach here -- the horizon check below is a second, defensive gate
    against invoking the wrong trend source, never a place to guess."""
    metric = intelligence.METRICS.get(case["objective_metric_id"])
    if metric is None:
        return None

    if case["horizon"] == "monthly":
        period = case.get("last_verified_period")
        if not period:
            return None
        trend = intelligence.get_trend(metric)
        row = next((r for r in trend if r["month"] == period and not r.get("is_partial")), None)
    elif case["horizon"] == "weekly":
        if metric.short_term_trend_sql is None:
            return None  # structurally unsupported; set_case_objective already prevents this combination
        trend, _as_of = intelligence.get_trailing_trend(metric)
        row = trend[-1] if trend else None  # the freshest complete trailing window
    else:
        return None  # snapshot horizon: a direct metric-trend objective isn't supported (see set_case_objective)

    if row is None:
        return None  # the required period isn't available/complete yet -- hold
    target = case["objective_target_value"]
    if case.get("objective_baseline_relative"):
        target = (case.get("objective_baseline_value") or 0) + target
    return "captured" if _compare(row["value"], target, case["objective_comparison"]) else "expired"


def set_case_objective(case_id: str, metric_id: str, target_value: float, comparison: str,
                        baseline_relative: bool = False) -> None:
    """Declares a case-level business objective directly (the alternative
    to designate_case_objective_from_action). Validated against the real
    metric definition -- an unknown metric or comparison operator raises,
    same posture as finding_actions.set_action's status validation --
    never inventing measurement semantics from a bare number. Also
    validated against the CASE's own horizon (Phase 2e Task 4): a
    snapshot-horizon case has no metric trend to measure a direct
    objective against at all, and a weekly-horizon case needs the
    metric's trailing-7-day trend specifically, which not every metric
    has -- both are rejected here, at declaration time, rather than
    silently producing an objective that can never resolve (see
    _resolve_case_direct_objective). Runs under _case_write_lock (Phase 2e
    post-acceptance audit, Task 1 point 3): this reads the case, then
    writes it, and without the lock a scan could mutate the same case's
    row in between -- the same class of race save_action_with_
    verification was already fixed against."""
    _ensure_schema()
    if not math.isfinite(target_value):
        raise ValueError("Target must be a finite number.")
    if comparison not in COMPARISONS:
        raise ValueError(f"Unknown comparison: {comparison}")
    metric = intelligence.METRICS.get(metric_id)
    if metric is None:
        raise ValueError(f"Unknown metric: {metric_id}")
    with _case_write_lock():
        case = get_case(case_id)
        if case is None:
            raise ValueError(f"No such case: {case_id}")
        if case["horizon"] == "snapshot":
            raise ValueError("Case objectives aren't supported for snapshot-horizon cases yet.")
        if case["horizon"] == "weekly" and metric.short_term_trend_sql is None:
            raise ValueError(f"{metric_id} doesn't support weekly measurement, but this case's cadence is weekly.")

        baseline_value = None
        if baseline_relative:
            if case["horizon"] == "weekly":
                weekly_trend, _as_of = intelligence.get_trailing_trend(metric)
                baseline_value = weekly_trend[-1]["value"] if weekly_trend else None
            else:
                non_partial = [r for r in intelligence.get_trend(metric) if not r.get("is_partial")]
                baseline_value = non_partial[-1]["value"] if non_partial else None
            if baseline_value is None:
                raise ValueError("A relative objective requires a measured baseline.")

        appdb.run_query(
            "UPDATE business_cases SET objective_metric_id=:m, objective_target_value=:t, objective_comparison=:c, "
            "objective_unit=:u, objective_baseline_relative=:br, objective_baseline_value=:bv, "
            "objective_source_finding_id=NULL, objective_target_version=NULL WHERE case_id=:id",
            {"m": metric_id, "t": target_value, "c": comparison, "u": metric.unit,
             "br": 1 if baseline_relative else 0, "bv": baseline_value, "id": case_id},
        )
        _log_history(case_id, "objective_set", {
            "metric_id": metric_id, "target_value": target_value, "comparison": comparison,
            "baseline_relative": baseline_relative,
        })


def designate_case_objective_from_action(case_id: str, finding_id: str) -> None:
    """The other way to declare a case objective: point at a specific
    action's already-validated target instead of declaring a new one.
    Opportunity capture then defers directly to that action's own
    (already period-correct, already overlap-checked) outcome -- see
    OpportunityPolicy / _resolve_from_action_outcome. Runs under
    _case_write_lock -- see set_case_objective's docstring for why."""
    _ensure_schema()
    _ensure_finding_actions_schema()
    with _case_write_lock():
        case = get_case(case_id)
        if case is None:
            raise ValueError(f"No such case: {case_id}")
        rows = appdb.run_query("SELECT * FROM finding_actions WHERE finding_id=:f", {"f": finding_id})
        if not rows:
            raise ValueError(f"No such action: {finding_id}")
        action = rows[0]
        if action.get("case_id") != case_id:
            raise ValueError("The objective action must belong to this case.")
        if not action.get("relevant"):
            raise ValueError("The objective action must be relevant to this case.")
        if case["category"] != "opportunity" or case["horizon"] == "snapshot":
            raise ValueError("Action objectives require a trend opportunity case.")
        if (action.get("target_measurement_period") or "monthly") != case["horizon"]:
            raise ValueError("The objective measurement cadence must match the case.")
        if not action.get("target_metric_id"):
            raise ValueError("This action has no validated target to designate as the case objective.")

        appdb.run_query(
            "UPDATE business_cases SET objective_metric_id=:m, objective_target_value=:t, objective_comparison=:c, "
            "objective_unit=:u, objective_baseline_relative=:br, objective_baseline_value=:bv, "
            "objective_source_finding_id=:f, objective_target_version=:version WHERE case_id=:id",
            {
                "m": action["target_metric_id"], "t": action["target_value"], "c": action["target_comparison"],
                "u": action["target_unit"], "br": action.get("target_baseline_relative"),
                "bv": action.get("target_baseline_value"), "f": finding_id, "id": case_id,
                "version": action["target_version"],
            },
        )
        _log_history(case_id, "objective_designated_from_action", {"finding_id": finding_id})


def correct_resolution(case_id: str, new_verdict: str, reason: str) -> None:
    """Audited correction for a closure that turned out wrong -- e.g. an
    approved action's outcome measurement resolves 'met' AFTER its case
    had already closed 'expired' (an action's own measurement window can
    outlast the case's verification window). Never a silent overwrite:
    the OLD verdict is preserved forever in case_history as a
    'resolution_corrected' event; only the CURRENT resolution_verdict
    column -- the one field meant to answer "what do we believe now" --
    is updated. A no-op if the verdict didn't actually change."""
    _ensure_schema()
    case = get_case(case_id)
    if case is None:
        raise ValueError(f"No such case: {case_id}")
    if case["status"] != "resolved":
        raise ValueError(f"Case is not resolved (status={case['status']}); nothing to correct.")
    old_verdict = case["resolution_verdict"]
    if old_verdict == new_verdict:
        return

    appdb.run_query(
        "UPDATE business_cases SET resolution_verdict=:v WHERE case_id=:id", {"v": new_verdict, "id": case_id}
    )
    _log_history(case_id, "resolution_corrected", {"old_verdict": old_verdict, "new_verdict": new_verdict, "reason": reason})


def _advance_verification(case: dict, scan_run_id: str, data_fresh: bool, measured_period: str | None = None) -> None:
    """Runs only for a case whose (metric_id, domain, horizon) was NOT
    matched by any finding this cycle -- i.e. it's no longer flagged.
    Rejects both of the auto-closure shortcuts this was reviewed against:
    a single absent cycle is not enough (VERIFICATION_WINDOW requires 2
    consecutive fresh, clean cadence units), and an 'improved' verdict is
    not the criterion at all -- only continued absence against the same
    anomaly threshold that opened the case counts. When no fresh cadence
    unit is available yet (still mid-month, data stale, or metric
    un-registered), the case is left exactly as it is -- active or
    verification-pending -- rather than guessed at.

    Once the metric-side window is satisfied, the category's resolution
    policy decides the actual verdict (see RESOLUTION_POLICIES) -- for an
    opportunity case with a relevant action still awaiting its outcome
    measurement, that can mean holding in verification_pending rather
    than closing immediately (see OpportunityPolicy). A held case is
    re-asked EVERY cycle, not just when a new cadence unit lands: the
    metric side is already satisfied at that point, so what's left to
    change is purely whether the action's outcome has resolved since --
    which can happen days before the next calendar month rolls over."""
    current_unit = (measured_period if case["horizon"] == "monthly" else
                    _current_cadence_unit(case["horizon"], case["metric_id"], scan_run_id, data_fresh))
    if current_unit is None:
        return

    held = (
        case["status"] == "verification_pending"
        and case["verification_progress"] >= VERIFICATION_WINDOW
        and current_unit == case["last_verified_period"]
    )
    if held:
        _resolve_or_hold(case, now=datetime.utcnow().isoformat())
        return
    if current_unit == case["last_verified_period"]:
        return

    progress = case["verification_progress"] + 1
    now = datetime.utcnow().isoformat()

    if progress >= VERIFICATION_WINDOW:
        appdb.run_query(
            "UPDATE business_cases SET status='verification_pending', verification_progress=:p, "
            "last_verified_period=:u, last_seen_at=:t WHERE case_id=:id",
            {"p": progress, "u": current_unit, "t": now, "id": case["case_id"]},
        )
        _resolve_or_hold(case, now)
    else:
        appdb.run_query(
            "UPDATE business_cases SET status='verification_pending', verification_progress=:p, "
            "last_verified_period=:u, last_seen_at=:t WHERE case_id=:id",
            {"p": progress, "u": current_unit, "t": now, "id": case["case_id"]},
        )
        _log_history(case["case_id"], "verification_progress", {"progress": progress, "unit": current_unit})


def _resolve_or_hold(case: dict, now: str) -> None:
    """Asks the category's resolution policy for a verdict against the
    CURRENT database state (not the possibly-stale `case` argument) and
    either closes the case or leaves it held in verification_pending.
    Shared by the two paths that can reach this point: the cycle where
    the metric-side window first completes, and every subsequent cycle a
    still-held case gets re-asked (see _advance_verification)."""
    refreshed = get_case(case["case_id"])
    policy = RESOLUTION_POLICIES.get(refreshed["category"], ProblemRecoveryPolicy())
    verdict = policy.resolve(refreshed)
    if verdict is not None:
        appdb.run_query(
            "UPDATE business_cases SET status='resolved', resolution_verdict=:v, closed_at=:t, "
            "last_seen_at=:t WHERE case_id=:id",
            {"v": verdict, "t": now, "id": case["case_id"]},
        )
        _log_history(case["case_id"], "resolved", {
            "policy": type(policy).__name__, "verification_window": VERIFICATION_WINDOW, "verdict": verdict,
        })
    else:
        appdb.run_query(
            "UPDATE business_cases SET last_seen_at=:t WHERE case_id=:id", {"t": now, "id": case["case_id"]}
        )
        _log_history(case["case_id"], "resolution_held", {
            "reason": "the metric cleared, but a relevant action's outcome measurement hasn't landed yet",
        })


def _ingest_finding(finding: dict, horizon: str, scan_run_id: str, today: date) -> str:
    """One finding -> one case, matched/created/observed. Extracted from
    sync_cases()'s own per-finding loop (Completed Stock-Count Variance &
    Shrinkage Investigation) so save_ad_hoc_case() below can reuse the
    EXACT same match/create/observe logic outside the daily scan, rather
    than a second, drifting copy of it. Pure extraction -- every call this
    makes (_find_active_case, _touch_case, _open_new_case, _reopen_case,
    _record_observation, _scope_from_checks) already existed and already
    ran in this exact sequence inside sync_cases()."""
    metric_id = finding["metric_id"]
    domain = finding.get("domain") or "unclassified"
    # root_cause is legitimately `None` (not just absent) for finding
    # types that don't run a concentration check by design -- e.g.
    # state_findings._top_seller_stock_finding, whose comment explains
    # why a concentration check there would be trivially, meaninglessly
    # "100%". A plain `.get("root_cause", {})` only substitutes the
    # default when the key is MISSING, not when it's present and None,
    # so it still crashed here on real data (found via the Controlled
    # Pilot real full-stack test, which is the only test that syncs
    # build_operational_findings() output through sync_cases).
    scope_status, scope_segments = _scope_from_checks((finding.get("root_cause") or {}).get("checks", []))

    case = _find_active_case(metric_id, domain, horizon)
    if case is not None:
        _touch_case(case, finding, scope_status, scope_segments)
        case_id = case["case_id"]
    else:
        prior = _find_recent_closed_case(metric_id, domain, horizon, today)
        if prior is not None and prior["status"] == "dismissed":
            # The dismissal never went through verification -- a
            # matching finding reappearing means it probably wasn't a
            # real dismissal. Reopen the SAME case rather than fork.
            _reopen_case(prior["case_id"], finding, scope_status, scope_segments,
                         "matching finding reappeared after a dismissal")
            case_id = prior["case_id"]
        else:
            case_id = _open_new_case(metric_id, domain, horizon, finding, scope_status, scope_segments)
            if prior is not None:
                # prior["status"] == "resolved": that case genuinely
                # passed verification, so this is a NEW incident, not
                # evidence the old closure was wrong. Link, don't merge.
                _link_relationship(case_id, prior["case_id"], "recurrence_of")

    _record_observation(case_id, scan_run_id, finding)
    finding["case_id"] = case_id
    return case_id


def save_ad_hoc_case(finding: dict) -> str:
    """Completed Stock-Count Variance & Shrinkage Investigation: lets an
    on-demand Ask Celeste investigation become a persistent, trackable
    case OUTSIDE the daily scan -- the one genuinely new case-creation path
    in this module (every other entry point is sync_cases(), called only
    from the scan pipeline). Reuses _ingest_finding exactly as sync_cases()
    does, so a manually-saved case gets identical match/dedup/audit-trail
    behavior to a scan-created one -- re-saving the same investigation
    scope reopens/updates that one case rather than forking a duplicate.

    horizon is deliberately 'snapshot', not 'monthly': this finding's
    metric_id is synthesized per investigation scope (see state_findings.py
    precedent in incident_engine.py) and is never registered in intelligence.
    METRICS, and only the monthly-cadence verification path looks a
    metric_id up in that registry -- 'snapshot' avoids it entirely, same
    horizon every other state_findings.py finding already uses.

    finding['metric_id'] is the only hard requirement (KeyError otherwise,
    same as sync_cases()) -- the caller is responsible for it being scoped
    to what the manager actually investigated (e.g. embedding store/period),
    never a single shared id for every ad-hoc save."""
    _ensure_schema()
    today = _business_today()
    scan_run_id = today.isoformat()
    horizon = finding.get("horizon") or "snapshot"
    with _case_write_lock():
        case_id = _ingest_finding(finding, horizon, scan_run_id, today)
        _record_measurement(case_id, measurement.observation(
            finding["metric_id"], horizon, "measured_actionable", scope=finding["metric_id"],
            period=scan_run_id, reason="Manually measured scope; requires explicit remeasurement.",
        ))
        return case_id


def _record_measurement(case_id: str, record: dict) -> None:
    prior = get_case(case_id).get("last_measurement")
    previous = json.loads(prior) if prior else {}
    keys = ("measurement_status", "coverage", "freshness", "reason")
    if any(previous.get(key) != record.get(key) for key in keys):
        _log_history(case_id, "measurement_changed", record)
    appdb.run_query("UPDATE business_cases SET last_measurement=:m WHERE case_id=:c",
                    {"m": json.dumps(record), "c": case_id})


def sync_cases(investigations: list[dict] = None, state_findings: list[dict] = None,
                short_term_findings: list[dict] = None, data_fresh: bool = True,
                measurements: list[dict] | None = None) -> list[str]:
    """Called once per daily scan (brain._compute_daily_brief, after every
    finding list is finalized). Mutates each finding in place with a
    'case_id' -- additive, so /brief's existing shape is unchanged for any
    consumer that doesn't look for it yet.

    data_fresh should be the same freshness signal brain._data_freshness()
    already computes (order data not stale) -- passed through so a
    weekly/snapshot case's verification window doesn't advance on a cycle
    where "absent" might just mean "stale data again," not a real
    measurement. Monthly cases don't need this: their cadence unit is
    already gated on a genuinely new, complete trend month landing."""
    _ensure_schema()
    today = _business_today()
    scan_run_id = today.isoformat()

    grouped = []
    for inv in (investigations or []):
        grouped.append((inv, inv.get("horizon") or "monthly"))
    for f in (state_findings or []):
        grouped.append((f, "snapshot"))
    for f in (short_term_findings or []):
        grouped.append((f, f.get("horizon") or "weekly"))

    seen_keys = set()
    synced_case_ids = []

    for finding, horizon in grouped:
        key = (finding["metric_id"], finding.get("domain") or "unclassified", horizon)
        seen_keys.add(key)
        case_id = _ingest_finding(finding, horizon, scan_run_id, today)
        record = finding.get("measurement") or measurement.observation(
            finding["metric_id"], horizon, "measured_actionable", period=scan_run_id,
            freshness="fresh" if data_fresh else "unknown",
        )
        _record_measurement(case_id, record)
        synced_case_ids.append(case_id)

    active_rows = appdb.run_query(f"SELECT * FROM business_cases WHERE status IN {_ACTIVE_STATUSES_SQL}")
    for case in active_rows:
        key = (case["metric_id"], case["domain"], case["horizon"])
        if key in seen_keys:
            continue  # already handled above as a continuation this cycle
        record = next((m for m in measurements or []
                       if m["metric_id"] == case["metric_id"] and m["horizon"] == case["horizon"]), None)
        record = dict(record) if record else measurement.observation(
            case["metric_id"], case["horizon"], "not_remeasured", coverage="unknown",
            freshness="unknown", reason="No successful measurement covers this case's scope.",
        )
        if not data_fresh and case["horizon"] != "monthly":
            record.update(freshness="unknown", reason="Source freshness is not established.")
        _record_measurement(case["case_id"], record)
        if measurement.healthy([record], case["metric_id"], case["horizon"]):
            _advance_verification(case, scan_run_id, data_fresh, record.get("period"))

    return synced_case_ids


def dismiss_case(case_id: str, reason: str) -> None:
    """Distinct from dismissing a single action (finding_actions.relevant =
    False, which never touches a case): this closes the whole case without
    any verification having run, so it's NOT a 'resolved' outcome -- see
    sync_cases' handling of a dismissed case reappearing (reopen, not a
    fresh unrelated case). A manual, API-triggered dismissal (unlike the
    internal sync_cases path) requires a real reason and a case that's
    actually open to dismiss -- caught here, not left to a bare SQL no-op,
    so the API layer gets a clear error to surface. Runs under
    _case_write_lock -- see set_case_objective's docstring for why: this
    reads the case's status then writes it, and a scan mutating the same
    case in between would otherwise be a real lost-update race."""
    _ensure_schema()
    if not reason or not reason.strip():
        raise ValueError("A reason is required to dismiss a case.")
    with _case_write_lock():
        case = get_case(case_id)
        if case is None:
            raise ValueError(f"No such case: {case_id}")
        if case["status"] in ("resolved", "dismissed"):
            raise ValueError(f"Case is already closed (status={case['status']}).")

        now = datetime.utcnow().isoformat()
        appdb.run_query(
            "UPDATE business_cases SET status='dismissed', closed_at=:t, dismissed_reason=:r, last_seen_at=:t "
            "WHERE case_id=:id",
            {"t": now, "r": reason, "id": case_id},
        )
        _log_history(case_id, "dismissed", {"reason": reason, "manual": True})


def reopen_case(case_id: str, reason: str) -> None:
    """The manual/API counterpart to sync_cases' internal dismissed-case
    reopen path -- for a manager explicitly correcting a case that was
    closed (resolved OR dismissed) when it shouldn't have been, with no
    fresh finding required to trigger it. Requires a real reason and a
    case that's actually closed, same posture as dismiss_case above,
    including running under the same _case_write_lock."""
    _ensure_schema()
    if not reason or not reason.strip():
        raise ValueError("A reason is required to reopen a case.")
    with _case_write_lock():
        case = get_case(case_id)
        if case is None:
            raise ValueError(f"No such case: {case_id}")
        if case["status"] not in ("resolved", "dismissed"):
            raise ValueError(f"Case is not closed (status={case['status']}); nothing to reopen.")

        now = datetime.utcnow().isoformat()
        appdb.run_query(
            "UPDATE business_cases SET status='reopened', closed_at=NULL, dismissed_reason=NULL, "
            "verification_progress=0, last_verified_period=NULL, last_seen_at=:t WHERE case_id=:id",
            {"t": now, "id": case_id},
        )
        _log_history(case_id, "reopened", {"reason": reason, "manual": True})


def get_case_id_for_finding(finding_id: str) -> str | None:
    """Looks up which case a given finding_id was synced into -- the join
    finding_actions needs to attach a manager-set action to its case
    without finding_actions itself knowing anything about case identity.

    Safe across a recurrence by construction, not by extra logic: every
    finding_id already embeds a period or as_of_date (see investigation.py/
    state_findings.py), so a recurrence's NEW case always gets NEW
    finding_ids -- the old case's finding_ids stay permanently resolvable
    to the old case_id, and there is no literal string a recurring case
    could share with its predecessor to collide on."""
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT case_id FROM case_observations WHERE finding_id=:f ORDER BY observed_at DESC LIMIT 1",
        {"f": finding_id},
    )
    return rows[0]["case_id"] if rows else None


def record_decision_snapshot(case_id: str, finding_id: str, action_status: str, conn=None) -> None:
    """Called whenever a manager actually saves a decision on a finding
    (finding_actions.set_action) -- captures the exact evidence that was
    current for THAT finding_id at that moment into the already
    append-only case_history table. Exists because case_observations is
    NOT that immutable record: it's upserted per (case_id, scan_run_id) so
    a later same-day rescan's newer evidence correctly overwrites the
    morning's snapshot (see the Phase 2a same-day-rescan fix) -- which is
    right for "what does the evidence say now," but would otherwise erase
    the basis for a decision made against the morning's numbers. A no-op
    (silently) when the finding_id has no matching observation yet, or no
    case at all -- e.g. an action saved before this finding was ever
    synced, which should not raise on an ordinary user action.

    conn threads through to _log_history so this participates in the
    caller's transaction when given one (see save_action_with_
    verification) -- its write and the finding_actions write it
    accompanies must commit or roll back together, never just one of
    them (Phase 2e post-acceptance audit, Task 1 point 1).

    Deliberately has NO recommendation-acceptance awareness (an earlier
    revision of this function briefly added one) -- see
    record_recommendation_acceptance below for why that's now a fully
    separate function on a fully separate write path, never called from
    save_action_with_verification at all. A recommendation acceptance is
    not an action-status change, so it doesn't belong in a function whose
    whole job is "capture the evidence behind THIS action-status save."""
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT * FROM case_observations WHERE case_id=:c AND finding_id=:f ORDER BY observed_at DESC LIMIT 1",
        {"c": case_id, "f": finding_id}, conn=conn,
    )
    if not rows:
        return
    snapshot = json.loads(rows[0]["snapshot"])
    _log_history(case_id, "decision_evidence", {
        "finding_id": finding_id,
        "action_status": action_status,
        "observed_at": rows[0]["observed_at"],
        "version": _observation_version(rows[0]),
        "snapshot": snapshot,
    }, conn=conn)


def record_recommendation_acceptance(case_id: str, finding_id: str, recommendation_action: str, conn=None) -> bool:
    """Celeste Investigate Phase 2, final correction ("Recommendation vs
    Action"): a manager accepting one of Celeste's recommendations is an
    auditable planning decision, NOT operational work -- it must never
    imply a finding_actions row exists (see accept_recommendation below,
    which calls this and, on purpose, never calls finding_actions.
    set_action at all -- the earlier revision that piggybacked this event
    onto record_decision_snapshot/save_action_with_verification planted a
    blank finding_actions row on every acceptance, which is exactly the
    "phantom action" this review flagged: it inflated Actions-taken
    counts, showed up in the Actions/Outcomes workspaces as empty,
    non-actionable rows, and flipped the case-evolution stepper's "Action
    recorded" stage to done with no real action behind it).

    recommendation_action is matched against THIS decision-time
    snapshot's own real recommendations (never a client-asserted object)
    -- a text that doesn't match anything currently in the snapshot (e.g.
    a stale click racing a fresh scan) is silently skipped (returns
    False) rather than fabricating a match; the caller's own evidence-
    version check is the actual defense against that race, this is a
    second, defensive check. On a match (True), the full recommendation
    (action/owner/priority/expected_benefit) AND the full decision-time
    snapshot are preserved together in ONE case_history event
    ("recommendation_approved") -- self-sufficient on its own, since
    nothing else is written alongside it here."""
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT * FROM case_observations WHERE case_id=:c AND finding_id=:f ORDER BY observed_at DESC LIMIT 1",
        {"c": case_id, "f": finding_id}, conn=conn,
    )
    if not rows:
        return False
    snapshot = json.loads(rows[0]["snapshot"])
    matched = next(
        (r for r in (snapshot.get("recommendations") or []) if r.get("action") == recommendation_action), None,
    )
    if matched is None:
        return False
    _log_history(case_id, "recommendation_approved", {
        "finding_id": finding_id,
        "recommendation": matched,
        "observed_at": rows[0]["observed_at"],
        "version": _observation_version(rows[0]),
        "snapshot": snapshot,
    }, conn=conn)
    return True


def accept_recommendation(finding_id: str, recommendation_action: str, expected_version: str | None) -> dict:
    """The pure "accept this recommendation" write path -- deliberately
    parallel to, but never routed through, save_action_with_verification.
    Same lock (_case_write_lock, so a scan can't land mid-write), same
    evidence-version check (verify_evidence_version -- raises
    StaleEvidenceError/ScanInProgressError exactly like the action-save
    path), same one-real-SQL-transaction posture -- but the transaction
    here contains only record_recommendation_acceptance's single
    case_history write, never finding_actions.set_action(). That absence
    is the entire point: accepting a recommendation must never create or
    touch an operational action row (see record_recommendation_
    acceptance's own docstring for the "phantom action" bug this fixes).

    Raises ValueError if finding_id resolves to no case at all -- unlike
    save_action_with_verification's case_id-is-None branch (which still
    writes the action row, just skips the case-aware history), there is
    nothing at all for this function to record without a case, so it's a
    real error here rather than a silent no-op."""
    _ensure_schema()
    with _case_write_lock():
        case_id = get_case_id_for_finding(finding_id)
        if case_id is None:
            raise ValueError(f"No case found for finding_id: {finding_id}")
        evidence_verified = expected_version is not None
        verify_evidence_version(case_id, finding_id, expected_version)

        with appdb.get_engine().begin() as conn:
            recorded = record_recommendation_acceptance(case_id, finding_id, recommendation_action, conn=conn)

        return {"case_id": case_id, "evidence_verified": evidence_verified, "recorded": recorded}


def _latest_observations_for(case_ids: list[str]) -> dict[str, dict]:
    """One batch query for the most recent observation per case_id, not
    one query per case -- used by both list_cases and get_case_detail so
    neither the case list nor the detail view has to guess which
    observation is 'current' from raw rows itself."""
    if not case_ids:
        return {}
    placeholders = ", ".join(f":id{i}" for i in range(len(case_ids)))
    params = {f"id{i}": cid for i, cid in enumerate(case_ids)}
    rows = appdb.run_query(
        f"""
        SELECT co.* FROM case_observations co
        INNER JOIN (
            SELECT case_id, MAX(observed_at) AS max_observed_at
            FROM case_observations WHERE case_id IN ({placeholders})
            GROUP BY case_id
        ) latest ON co.case_id = latest.case_id AND co.observed_at = latest.max_observed_at
        """,
        params,
    )
    return {row["case_id"]: row for row in rows}


def _summarize_snapshot(snapshot: dict) -> dict:
    """The subset of a finding snapshot the UI actually needs to answer
    "what happened" and "what's the latest number" without reaching into
    raw JSON itself -- computed here once so the frontend never re-derives
    a headline or re-decides which nested field means what (that
    duplication is exactly what produced the Home.jsx sign/color bugs
    fixed earlier in this project).

    finding_id (the snapshot's own "id") is included so get_case_detail's
    "latest" can back a manager's FIRST action on a case that has no
    finding_actions row at all yet -- see CaseDetail.jsx's "Actions taken"
    section, found missing during the Phase 2e post-acceptance browser
    test (a brand-new case had no way to record any action at all, since
    the page only ever rendered an ActionTracker per EXISTING row)."""
    # Frontend Intelligence Integration, Phase 10: the case list needs a
    # "where" answer (e.g. "Ethul Kotte -- 28 affected products") without
    # opening the case -- the top entry of the primary breakdown already
    # carries exactly that, just never surfaced past this summarizer
    # before. Not every finding has one (a plain trend metric with no
    # breakdown), so this is None rather than a fabricated placeholder.
    # sku carried through when the raw contributor has one (state_findings.
    # py's top_seller_stock puts a real SKU on each contributor) -- Investigate
    # V3's card needs it to anchor a card in a real product, not just a name.
    breakdowns = snapshot.get("breakdowns") or []
    top_contributor = None
    if breakdowns and breakdowns[0].get("top_contributors"):
        c = breakdowns[0]["top_contributors"][0]
        top_contributor = {"dimension": breakdowns[0].get("dimension"), "name": c.get("name"), "value": c.get("current")}
        if c.get("sku"):
            top_contributor["sku"] = c["sku"]
    # A second breakdown, when one exists, is usually a different real-world
    # axis on the same finding (state_findings.py's negative_stock_control
    # breaks down by product AND by outlet) -- same derivation as
    # top_contributor, just the next breakdown in the list, so a card can
    # show a second concentration fact ("Largest concentration: Ethul
    # Kotte") without a metric_id-specific branch here.
    secondary_contributor = None
    if len(breakdowns) > 1 and breakdowns[1].get("top_contributors"):
        c = breakdowns[1]["top_contributors"][0]
        secondary_contributor = {"dimension": breakdowns[1].get("dimension"), "name": c.get("name"), "value": c.get("current")}
    # Investigate V3: "what we know" / "what we don't know" for the case
    # list, without shipping every check's full detail -- just the first
    # established (confirmed/possible_contributor) and first still-open
    # (unknown/no_evidence_found) check's own real sentence, generic across
    # every finding shape since it reads root_cause.checks, not a
    # metric_id-specific field.
    root_cause = snapshot.get("root_cause") or {}
    checks = root_cause.get("checks") or []
    established = next((c["detail"] for c in checks if c.get("verdict") in ("confirmed", "possible_contributor") and c.get("detail")), None)
    unknown = next((c["detail"] for c in checks if c.get("verdict") in ("unknown", "no_evidence_found") and c.get("detail")), None)
    evidence_summary = {"established": established, "unknown": unknown} if (established or unknown) else None
    impact = snapshot.get("impact") or {}
    impact_summary = (
        {"financial_impact_rs": impact.get("financial_impact_rs"), "detail": impact.get("detail"),
         "units_affected": impact.get("units_affected"), "valued_units_pct": impact.get("valued_units_pct"),
         "plan_units": impact.get("plan_units"), "sent_units": impact.get("sent_units"), "line_received_units": impact.get("line_received_units"),
         "planning_gap_units": impact.get("planning_gap_units"), "transit_gap_units": impact.get("transit_gap_units"), "total_gap_units": impact.get("total_gap_units"),
         "kind": impact.get("kind"),
         # Small, curated numbers the case-list title builder needs (Investigate.jsx's
         # standardTitleFor) -- the list's "latest" only ever gets this summary, never
         # the full snapshot, so anything a list-view title needs has to live here.
         "count": impact.get("count"), "distinct_products": impact.get("distinct_products"),
         "total_top_sellers": impact.get("total_top_sellers"),
         "outlet_count": len(snapshot.get("outlet_rows") or []) or (
             len(breakdowns[0]["top_contributors"]) if breakdowns and breakdowns[0].get("dimension") in ("store", "outlet") and breakdowns[0].get("top_contributors") else None
         )}
        if impact.get("financial_impact_rs") is not None or impact.get("detail")
        else None
    )
    return {
        "title": snapshot.get("title"),
        # Real, pre-existing bug found while rebuilding Case Detail (2026-09-26):
        # ControlsInvestigationPanel.jsx and plainLanguage.js's controlsExecutiveText/
        # controlsResolutionText all branch on `latest.metric_id`, but this whitelist
        # never included it -- every controls case silently fell through to the
        # generic panel-less/brief-less fallback (an empty "Control investigation"
        # card, generic brief text) even though the purpose-built copy for it already
        # existed and was correct. `metric_id` is a real, stable field on every
        # finding snapshot (state_findings.py/investigation.py both set it) -- not
        # new data, just never surfaced past this summarizer.
        "metric_id": snapshot.get("metric_id"),
        "category": snapshot.get("category"),
        "severity": snapshot.get("severity"),
        "priority": snapshot.get("priority"),
        "confirm": snapshot.get("confirm"),
        "metric": snapshot.get("metric"),
        "persistence": snapshot.get("persistence"),
        "horizon": snapshot.get("horizon"),
        "streak": snapshot.get("streak"),
        "finding_id": snapshot.get("id"),
        "top_contributor": top_contributor,
        "secondary_contributor": secondary_contributor,
        "evidence_summary": evidence_summary,
        "impact_summary": impact_summary,
        # Metric-specific, but a single well-justified passthrough (Investigate
        # V3's cost-coverage card wants "99.9% cost coverage" alongside the
        # affected count) rather than a generic field every finding would need.
        "coverage_pct": snapshot.get("coverage_pct"),
    }


# Frontend Intelligence Integration, Phase 10: whitelisted sort clauses,
# never a raw user-supplied ORDER BY string. "priority" ranks by the same
# critical>high>medium>low severity order Badge/toneForPriority already
# use elsewhere -- a CASE WHEN, not alphabetical (which would put
# "critical" after "high"). case_id is always the final tiebreaker so
# pagination stays stable when two cases tie on the primary key.
SORT_CLAUSES = {
    "recent_change": "last_seen_at DESC, case_id ASC",
    "newest": "opened_at DESC, case_id ASC",
    "oldest_unresolved": "opened_at ASC, case_id ASC",
    # (x IS NULL) ASC puts NULLs last, portable to SQLite (appdb's engine)
    # without relying on NULLS LAST, which older SQLite builds don't support.
    "impact": "(business_impact_score IS NULL) ASC, business_impact_score DESC, case_id ASC",
    "priority": (
        "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 "
        "WHEN 'low' THEN 3 ELSE 4 END ASC, case_id ASC"
    ),
}


def list_cases(status: str | None = None, metric_id: str | None = None, domain: str | None = None,
                priority: str | None = None, sort: str | None = None,
                limit: int = 50, offset: int = 0) -> dict:
    _ensure_schema()
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    where, params = [], {}
    if status == "active":
        where.append("status IN ('open', 'reopened', 'verification_pending')")
    elif status:
        where.append("status = :status")
        params["status"] = status
    if metric_id:
        where.append("metric_id = :metric_id")
        params["metric_id"] = metric_id
    if domain:
        where.append("domain = :domain")
        params["domain"] = domain
    if priority:
        where.append("priority = :priority")
        params["priority"] = priority
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = SORT_CLAUSES.get(sort, SORT_CLAUSES["recent_change"])

    total_rows = appdb.run_query(f"SELECT COUNT(*) AS n FROM business_cases {where_sql}", params)
    total = total_rows[0]["n"] if total_rows else 0
    items = appdb.run_query(
        f"SELECT * FROM business_cases {where_sql} ORDER BY {order_sql} LIMIT :limit OFFSET :offset",
        {**params, "limit": limit, "offset": offset},
    )

    latest_by_case = _latest_observations_for([item["case_id"] for item in items])
    for item in items:
        latest_row = latest_by_case.get(item["case_id"])
        item["latest"] = _summarize_snapshot(json.loads(latest_row["snapshot"])) if latest_row else None

    return {"items": items, "total": total, "limit": limit, "offset": offset}


# Deliberately explicit and shown to API consumers, not left implicit:
# case_observations is a rolling per-day snapshot (a later same-day rescan
# overwrites the morning's row -- see the Phase 2a same-day-rescan fix),
# never a complete append-only evidence log. The one place a specific
# piece of evidence IS preserved immutably is a "decision_evidence" event
# in case_history, captured at the moment a manager actually saved an
# action against a finding (see record_decision_snapshot).
OBSERVATIONS_NOTE = (
    "Each observation reflects the last scan of that calendar day, not every scan run that day -- "
    "it is not a complete evidence log. A decision_evidence entry in history is captured whenever a "
    "manager records an action, and preserves the exact evidence that was current at that moment."
)


def get_case_detail(case_id: str) -> dict | None:
    """Everything a case detail view needs in one call. 'Evidence timeline'
    and 'performance history' are both derived from case_observations'
    already-stored snapshots (Phase 2a deliberately didn't add a separate
    case_evidence_log / verdict-per-observation table -- see the Phase 2a
    report's limitations) rather than a new table: each observation's
    snapshot already carries its finding's full root_cause.checks, and the
    current/baseline pair on it is real, already-stored data, not a new
    computation. See OBSERVATIONS_NOTE for what this is NOT."""
    _ensure_schema()
    case = get_case(case_id)
    if case is None:
        return None

    observations = get_case_observations(case_id)
    performance_history = []
    latest_snapshot = None
    for obs in observations:
        snapshot = json.loads(obs["snapshot"])
        latest_snapshot = snapshot  # observations is ASC-ordered -- the last one wins
        confirm = snapshot.get("confirm") or {}
        performance_history.append({
            "observed_at": obs["observed_at"],
            "scan_run_id": obs["scan_run_id"],
            "current": confirm.get("current"),
            "baseline": confirm.get("baseline"),
        })

    latest = None
    if latest_snapshot is not None:
        latest = {
            **_summarize_snapshot(latest_snapshot),
            "root_cause": latest_snapshot.get("root_cause"),
            "impact": latest_snapshot.get("impact"),
            "how": latest_snapshot.get("how"),
            "recommendations": latest_snapshot.get("recommendations"),
            "recommendation_generation": latest_snapshot.get("recommendation_generation"),
            "breakdowns": latest_snapshot.get("breakdowns"),
            # Celeste Investigate Phase 3 (revenue investigation): the
            # metric's own multi-month trend series -- already computed and
            # stored in every monthly/weekly finding's snapshot (see
            # investigation.build_investigation/build_trailing_investigation,
            # which expose it for exactly this "render a real trend chart
            # without an extra live intelligence.investigate() call"
            # reason). Detail-only, like the fields above -- never added to
            # _summarize_snapshot's own whitelist, which list_cases() also
            # uses, since a multi-month array would bloat every case-list
            # row for a field only the detail page renders.
            "trend": latest_snapshot.get("trend"),
            # Visual Investigation V2, Phase 2 (affected-session table): the
            # stuck_stock_count finding's own real per-session records
            # (session id/outlet/age/completion status/variance) -- already
            # computed in full by stock_counts.get_stuck_stock_counts(), just
            # not previously exposed past the single "worst" session's prose
            # in one recommendation string. Detail-only, same reasoning as
            # trend above. None for every other finding shape.
            "sessions": latest_snapshot.get("sessions"),
            # Case Detail V3 bug fix: ControlsInvestigationPanel.jsx and
            # IncidentInvestigationPanel.jsx both read these directly off
            # `latest`, but they were never added to this whitelist -- so
            # every controls case (negative_stock_control/cost_coverage_
            # control) and every incident case silently rendered nothing in
            # its investigation panel (get() returned undefined, both
            # panels' own `if (products.length === 0) return null` guard
            # fired), regardless of the panel code being correct. Confirmed
            # live: /cases/{id} was missing `products` entirely even though
            # state_findings.py's finding dict always includes it. None for
            # every finding shape that doesn't set them.
            "products": latest_snapshot.get("products"),
            "by_outlet": latest_snapshot.get("by_outlet"),
            "total_products": latest_snapshot.get("total_products"),
            "coverage_pct": latest_snapshot.get("coverage_pct"),
            "store": latest_snapshot.get("store"),
            "product": latest_snapshot.get("product"),
            "hour": latest_snapshot.get("hour"),
            # transfer_discrepancy.py's own finding shape -- reuses `store`
            # above, adds `reason` (the stock_move_shortage reason code:
            # "Extra Quantity"/"Missing Item"/etc.) for its investigation panel.
            "reason": latest_snapshot.get("reason"),
            # incident_engine.py's own composite ranking number (hourly
            # z-score x product lift x sqrt(order count) x persistence
            # factor) -- computed and stored on every incident finding since
            # that module was built, but never whitelisted through, so
            # IncidentInvestigationPanel.jsx had no way to show it or which
            # score band (confirmed/escalate/auto-alert) it fell in.
            "incident_score": latest_snapshot.get("incident_score"),
            # transfer_discrepancy.py's richer investigation template
            # (2026-10-01, manager review): pattern_status is the plain-
            # language "Sustained pattern" / "Early recurring signal" label;
            # daily_breakdown is every day in the 14-day window (including
            # zero-discrepancy days) for the recurrence timeline and the
            # transfer-day breakdown table.
            "pattern_status": latest_snapshot.get("pattern_status"),
            "daily_breakdown": latest_snapshot.get("daily_breakdown"),
            # 2026-10-02: which OTHER outlets have this same reason code
            # recurring right now -- checked live, a real case that looked
            # outlet-specific in isolation was actually happening at all 4
            # outlets simultaneously, which the per-outlet case view had no
            # way to show before this.
            "company_wide_context": latest_snapshot.get("company_wide_context"),
            # 2026-10-02: per-line severity distribution (median/p75/p90/p95/
            # p99/max units and Rs) -- frequency and rate say nothing about
            # SHAPE (many small lines vs. a few severe ones read identically
            # otherwise). Input the extreme-event sibling detector will be
            # designed from, once these real numbers are inspected.
            "severity": latest_snapshot.get("severity"),
            "extreme_rows": latest_snapshot.get("extreme_rows"),
            "outlet_rows": latest_snapshot.get("outlet_rows"),
            "transfer_rows": latest_snapshot.get("transfer_rows"),
            "trace_rows": latest_snapshot.get("trace_rows"),
            "dispatch_rows": latest_snapshot.get("dispatch_rows"),
            "pattern_rows": latest_snapshot.get("pattern_rows"),
            # _damage_writeoff_finding's own per-product breakdown (reuses
            # "by_outlet" above, which is already whitelisted) -- a
            # different shape than "products" (negative_stock_control's
            # company-wide list), so its own key.
            "top_products": latest_snapshot.get("top_products"),
        }

    # Cross-domain investigation v1 (2026-09-27): computed fresh on every
    # fetch, never persisted -- see related_signals.py's own docstring for
    # the full design and why. Only ever non-empty for metric_ids listed in
    # RELATED_METRIC_CANDIDATES (today: revenue only); every other case
    # gets an early-exit empty list at negligible cost.
    related = []
    if latest and latest.get("root_cause"):
        confirm = latest.get("confirm") or {}
        related = related_signals.related_signals_for(
            case["metric_id"], confirm.get("period"), latest["root_cause"].get("checks") or [],
            category=latest.get("category"), pct_change=confirm.get("pct_change"),
        )

    return {
        "case": case,
        "resolution_policy": {"name": _RESOLUTION_POLICY_NAME.get(case["category"], "problem_recovery"), "verification_window": VERIFICATION_WINDOW},
        "measurement": json.loads(case["last_measurement"]) if case.get("last_measurement") else None,
        "verification_explanation": verification_explanation(case),
        "latest": latest,
        "observations": observations,
        "observations_note": OBSERVATIONS_NOTE,
        "performance_history": performance_history,
        "relationships": get_case_relationships(case_id),
        "related_signals": related,
        "history": get_case_history(case_id),
    }


def get_case(case_id: str) -> dict | None:
    _ensure_schema()
    rows = appdb.run_query("SELECT * FROM business_cases WHERE case_id=:id", {"id": case_id})
    if not rows:
        return None
    rows[0]["resolution_policy"] = _RESOLUTION_POLICY_NAME.get(rows[0]["category"], "problem_recovery")
    return rows[0]


def verification_explanation(case):
    if case["status"] == "resolved":
        if case.get("resolution_verdict") == "recovered":
            return "Condition recovered: the detector measured clean observations. This does not prove the underlying cause was resolved."
        return "Closed under the case's objective policy; see the recorded outcome."
    if case["status"] == "dismissed":
        return "Dismissed by a manager; recovery has not been verified."
    record = json.loads(case["last_measurement"]) if case.get("last_measurement") else {}
    if record.get("measurement_status") == "measured_actionable":
        return "Still open because the most recent measurement detected the condition."
    if not measurement.healthy([record] if record else [], case["metric_id"], case["horizon"]):
        return "Verification paused: " + (record.get("reason") or "this scope has not been successfully remeasured.")
    if case["verification_progress"] >= VERIFICATION_WINDOW:
        return "The condition cleared; awaiting the declared objective's measurement or designation of its current target version."
    unit = "complete monthly period" if case["horizon"] == "monthly" else "fresh daily measurement"
    return f"Awaiting the next {unit}; {case['verification_progress']} of {VERIFICATION_WINDOW} clean observations recorded."


def get_case_observations(case_id: str) -> list[dict]:
    """Each row gets a computed "version" key (see _observation_version)
    -- the opaque token CaseDetail.jsx echoes back as evidence_version on
    save, and the one verify_evidence_version actually compares. Computed
    here, once, server-side, rather than making the frontend reconstruct
    "<id>:<revision>" itself."""
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT * FROM case_observations WHERE case_id=:id ORDER BY observed_at ASC", {"id": case_id}
    )
    for row in rows:
        row["version"] = _observation_version(row)
    return rows


def get_case_history(case_id: str) -> list[dict]:
    _ensure_schema()
    return appdb.run_query("SELECT * FROM case_history WHERE case_id=:id ORDER BY at ASC", {"id": case_id})


def get_case_relationships(case_id: str) -> list[dict]:
    _ensure_schema()
    return appdb.run_query(
        "SELECT * FROM case_relationships WHERE case_id=:id OR related_case_id=:id", {"id": case_id}
    )
