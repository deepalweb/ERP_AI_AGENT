"""Manager-facing action tracking for findings — separate from tracking.py's
automatic, data-driven outcome verdicts. That system watches whether a
metric recovered on its own a month later; this one records whether a
person decided a finding was worth acting on, and what the team actually
did about it. Keyed by each finding's own `id`, which is already globally
unique across all three finding shapes (monthly "metric-YYYY-MM", weekly
"metric-trailing-{date}", state "metric-{date}") — so this applies
uniformly to every finding type without needing tracking.py's "YYYY-MM"
period assumption, which weekly/state findings don't satisfy.

Nothing here touches the customer's Odoo database; app.db (or
APP_DATABASE_URL) is entirely separate app state, same as tracking.py.
"""

import json
import math
from datetime import UTC, date, datetime

from backend.app.db import appdb
from backend.app.services.case_engine import COMPARISONS, MEASUREMENT_PERIODS
from mcp_server.tools import intelligence

STATUSES = {"not_started", "in_progress", "completed"}
# investigation: evidence points at a segment but the mechanism isn't known
# yet ("find out why"). corrective: the mechanism is known and this action
# changes something ("fix it"). Set explicitly by whoever proposes the
# action -- never inferred from a finding's evidence confidence (a
# 'confirmed' finding can still need an investigation action first).
ACTION_KINDS = {"investigation", "corrective"}
DEFAULT_ACTION = {
    "relevant": None,
    "status": "not_started",
    "action_taken": None,
    "owner": None,
    "deadline": None,
    "case_id": None,
    "action_kind": None,
    "completed_at": None,
    "intended_result": None,
    "target_metric_id": None,
    "target_value": None,
    "target_comparison": None,
    "target_unit": None,
    "target_baseline_relative": False,
    "target_measurement_period": None,
    "outcome_checked_at": None,
    "outcome_value": None,
    "outcome_period": None,
    "outcome_verdict": None,
    "outcome_status": None,
    "outcome_confidence": None,
    "evidence_verified": None,
    "evidence_version": None,
    "overdue": False,
    "updated_at": None,
}

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS finding_actions (
            finding_id TEXT PRIMARY KEY,
            relevant INTEGER,
            status TEXT NOT NULL DEFAULT 'not_started',
            action_taken TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    # finding_actions already has live rows in production — a fresh CREATE
    # above won't touch an existing table, so `owner`/`deadline` need the
    # same best-effort ALTER pattern brain.py uses for its cache columns.
    try:
        appdb.run_query("ALTER TABLE finding_actions ADD COLUMN owner TEXT")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE finding_actions ADD COLUMN deadline TEXT")
    except Exception:
        pass
    # case_id: which business_cases row this action belongs to, resolved by
    # the API layer from case_observations (see case_engine.
    # get_case_id_for_finding) since finding_actions itself has no case
    # awareness. REFERENCES is declarative only -- SQLite doesn't enforce
    # FK constraints without PRAGMA foreign_keys=ON, which nothing in this
    # app turns on (no table here has ever used real FK enforcement); the
    # column documents the relationship and is what the index below and
    # get_actions_for_case() query against.
    try:
        appdb.run_query("ALTER TABLE finding_actions ADD COLUMN case_id TEXT REFERENCES business_cases(case_id)")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE finding_actions ADD COLUMN action_kind TEXT")
    except Exception:
        pass
    appdb.run_query("CREATE INDEX IF NOT EXISTS ix_finding_actions_case_id ON finding_actions(case_id)")
    # Phase 2d: the action-outcome model. completed_at is set once, the
    # FIRST time status transitions to 'completed' (see set_action) --
    # distinct from updated_at, which changes on every edit. The
    # target_* group declares a measurable objective (validated against
    # intelligence.METRICS -- see set_action); the outcome_* group is
    # filled in later by action_outcomes.check_outcomes().
    for column in (
        "target_version INTEGER NOT NULL DEFAULT 0",
        "outcome_target_version INTEGER",
        "completed_at TEXT",
        "intended_result TEXT",
        "target_metric_id TEXT",
        "target_value REAL",
        "target_comparison TEXT",
        "target_unit TEXT",
        "target_baseline_relative INTEGER",
        "target_baseline_value REAL",
        "outcome_checked_at TEXT",
        "outcome_value REAL",
        "outcome_period TEXT",
        "outcome_verdict TEXT",
        "outcome_confidence TEXT",
        # Phase 2e additions. target_measurement_period: "monthly" (default)
        # or "weekly" -- validated at write time against the metric's own
        # trend support (see set_action), never left to the caller to
        # assume. outcome_status: the fuller pending/blocked/inconclusive/
        # met/not_met state action_outcomes.check_outcomes() now tracks
        # (see that module) -- outcome_verdict above stays exactly as it
        # was (None/"met"/"not_met") for the resolution-policy code that
        # already reads it, so this is purely additive. evidence_verified/
        # evidence_version records whether THIS row's most recent save was
        # actually checked against a specific evidence version, or is a
        # legacy save that skipped versioning entirely (see case_engine.
        # save_action_with_verification). It's an opaque string token
        # ("<observation row id>:<revision>"), not a timestamp -- see
        # case_engine._observation_version for why observed_at alone
        # isn't a safe version identifier.
        "target_measurement_period TEXT",
        "outcome_status TEXT",
        "evidence_verified INTEGER",
        "evidence_version TEXT",
    ):
        try:
            appdb.run_query(f"ALTER TABLE finding_actions ADD COLUMN {column}")
        except Exception:
            pass
    appdb.run_query("""CREATE TABLE IF NOT EXISTS action_target_versions (
        finding_id TEXT NOT NULL, version INTEGER NOT NULL, target TEXT NOT NULL,
        created_at TEXT NOT NULL, superseded_at TEXT, completed_at TEXT,
        PRIMARY KEY(finding_id, version))""")
    appdb.run_query("""CREATE TABLE IF NOT EXISTS action_outcome_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT NOT NULL,
        target_version INTEGER NOT NULL, recorded_at TEXT NOT NULL, outcome TEXT NOT NULL)""")
    # Adopt legacy targets without changing their measurement window or verdict.
    with appdb.get_engine().begin() as conn:
        for row in appdb.run_query("SELECT * FROM finding_actions WHERE target_metric_id IS NOT NULL AND target_version=0", conn=conn):
            appdb.run_query("UPDATE finding_actions SET target_version=1, outcome_target_version=CASE WHEN outcome_checked_at IS NOT NULL THEN 1 END WHERE finding_id=:f",
                            {"f": row["finding_id"]}, conn=conn)
            _insert_target_version(row, 1, row["updated_at"], conn)
            if row.get("outcome_status"):
                _append_outcome({**row, "target_version": 1}, conn)
    _SCHEMA_READY = True


_TARGET_FIELDS = ("target_metric_id", "target_value", "target_comparison", "target_unit",
                  "target_baseline_relative", "target_baseline_value", "target_measurement_period")
_OUTCOME_FIELDS = ("outcome_checked_at", "outcome_value", "outcome_period", "outcome_verdict",
                   "outcome_status", "outcome_confidence", "outcome_target_version")


def _insert_target_version(row, version, now, conn):
    appdb.run_query("INSERT OR IGNORE INTO action_target_versions (finding_id,version,target,created_at,completed_at) VALUES (:f,:v,:t,:at,:c)",
        {"f": row["finding_id"], "v": version, "t": json.dumps({key: row.get(key) for key in _TARGET_FIELDS}),
         "at": now, "c": row.get("completed_at")}, conn=conn)


def _append_outcome(row, conn):
    payload = {key: row.get(key) for key in _OUTCOME_FIELDS}
    previous = appdb.run_query("SELECT outcome FROM action_outcome_history WHERE finding_id=:f AND target_version=:v ORDER BY id DESC LIMIT 1",
                              {"f": row["finding_id"], "v": row["target_version"]}, conn=conn)
    # Pending/blocked status repeated in successive scans is not a new event.
    if previous and all(json.loads(previous[0]["outcome"]).get(k) == payload[k]
                        for k in _OUTCOME_FIELDS if k != "outcome_checked_at"):
        return
    appdb.run_query("INSERT INTO action_outcome_history (finding_id,target_version,recorded_at,outcome) VALUES (:f,:v,:at,:o)",
        {"f": row["finding_id"], "v": row["target_version"], "at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
         "o": json.dumps(payload)}, conn=conn)
    if row.get("case_id"):
        from backend.app.services import case_engine
        case_engine._log_history(row["case_id"], "outcome_measured", {
            "finding_id": row["finding_id"], "target_version": row["target_version"], **payload,
        }, conn=conn)


def store_outcome(action, values):
    """Publish the current projection and its versioned history atomically."""
    with appdb.get_engine().begin() as conn:
        row = appdb.run_query("SELECT * FROM finding_actions WHERE finding_id=:f", {"f": action["finding_id"]}, conn=conn)[0]
        if row["target_version"] != action["target_version"] or row["status"] != "completed":
            return False
        values = {**values, "outcome_target_version": row["target_version"]}
        if not values.keys() <= set(_OUTCOME_FIELDS):
            raise ValueError("Unknown outcome field")
        assignments = ", ".join(f"{key}=:{key}" for key in values)
        appdb.run_query(f"UPDATE finding_actions SET {assignments} WHERE finding_id=:f",
                        {**values, "f": action["finding_id"]}, conn=conn)
        _append_outcome({**row, **values}, conn)
        return True


def get_target_history(finding_id):
    _ensure_schema()
    versions = appdb.run_query("SELECT * FROM action_target_versions WHERE finding_id=:f ORDER BY version", {"f": finding_id})
    outcomes = appdb.run_query("SELECT * FROM action_outcome_history WHERE finding_id=:f ORDER BY id", {"f": finding_id})
    return [{**v, "target": json.loads(v["target"]),
             "outcomes": [json.loads(o["outcome"]) for o in outcomes if o["target_version"] == v["version"]]}
            for v in versions]


def _is_overdue(deadline: str | None, status: str) -> bool:
    return bool(deadline) and status != "completed" and deadline < date.today().isoformat()


def set_action(
    finding_id: str,
    relevant: bool | None,
    status: str,
    action_taken: str | None,
    owner: str | None = None,
    deadline: str | None = None,
    case_id: str | None = None,
    action_kind: str | None = None,
    intended_result: str | None = None,
    target_metric_id: str | None = None,
    target_value: float | None = None,
    target_comparison: str | None = None,
    target_baseline_relative: bool = False,
    target_measurement_period: str | None = None,
    evidence_verified: bool | None = None,
    evidence_version: str | None = None,
    conn=None,
):
    """Full-replace upsert for relevant/status/action_taken/owner/deadline —
    the frontend always sends the complete current selection for those, so
    there's no partial-merge case to handle there.

    case_id/action_kind/intended_result/target_* are all deliberately NOT
    full-replace: the existing "Track this" form has no fields for any of
    them, so a caller that omits them (None) must not wipe out a value a
    case-aware caller set earlier -- COALESCE keeps whatever's already
    stored when the new call doesn't provide one.

    target_metric_id/target_value/target_comparison are a group: setting
    one requires the other two, validated against the real metric
    definition -- target_unit is ALWAYS derived from
    intelligence.METRICS[target_metric_id].unit server-side, never
    trusted from a caller, so a target can't be recorded against the
    wrong unit. completed_at is stable within a completion cycle. Reopening
    clears the current completion and preserves it on the superseded target
    version. A changed target starts a new version and measurement window.

    conn is optional -- omitted, this opens/commits its own connection as
    always; passed (an active transaction from get_engine().begin()), this
    write participates in the CALLER's transaction instead, so it can be
    made atomic with another write (see case_engine.
    save_action_with_verification, which needs this row's write and its
    decision-snapshot history entry to succeed or fail together)."""
    _ensure_schema()
    if conn is None:
        arguments = locals().copy()
        with appdb.get_engine().begin() as transaction:
            arguments["conn"] = transaction
            return set_action(**arguments)
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    if action_kind is not None and action_kind not in ACTION_KINDS:
        raise ValueError(f"Unknown action_kind: {action_kind}")

    existing_rows = appdb.run_query(
        "SELECT * FROM finding_actions WHERE finding_id=:f", {"f": finding_id}, conn=conn,
    )
    existing = existing_rows[0] if existing_rows else {}
    existing_completed_at = existing.get("completed_at")
    reopened = existing.get("status") == "completed" and status != "completed"
    if status == "completed":
        completed_at = existing_completed_at or datetime.now(UTC).replace(tzinfo=None).isoformat()
    else:
        completed_at = None

    target_unit = None
    target_baseline_value = None
    measurement_period = None
    has_target = target_metric_id is not None
    if not has_target and (target_value is not None or target_comparison is not None):
        raise ValueError("A target value/comparison requires target_metric_id.")
    if has_target:
        if target_value is not None and not math.isfinite(target_value):
            raise ValueError("Target must be a finite number.")
        if target_value is None or target_comparison is None:
            raise ValueError("target_metric_id requires target_value and target_comparison.")
        if target_comparison not in COMPARISONS:
            raise ValueError(f"Unknown comparison: {target_comparison}")
        metric = intelligence.METRICS.get(target_metric_id)
        if metric is None:
            raise ValueError(f"Unknown metric: {target_metric_id}")
        target_unit = metric.unit
        measurement_period = target_measurement_period or "monthly"
        if measurement_period not in MEASUREMENT_PERIODS:
            raise ValueError(f"Unknown measurement period: {measurement_period}")
        if measurement_period == "weekly" and metric.short_term_trend_sql is None:
            raise ValueError(f"{target_metric_id} doesn't support weekly outcome measurement; use monthly.")
        if target_baseline_relative:
            same_target = all((existing.get(key) or False) == value for key, value in (
                ("target_metric_id", target_metric_id), ("target_value", target_value),
                ("target_comparison", target_comparison), ("target_baseline_relative", True),
                ("target_measurement_period", measurement_period),
            ))
            if same_target:
                target_baseline_value = existing.get("target_baseline_value")
            elif measurement_period == "weekly":
                weekly_trend, _as_of = intelligence.get_trailing_trend(metric)
                target_baseline_value = weekly_trend[-1]["value"] if weekly_trend else None
            else:
                non_partial = [r for r in intelligence.get_trend(metric) if not r.get("is_partial")]
                target_baseline_value = non_partial[-1]["value"] if non_partial else None
            if target_baseline_value is None:
                raise ValueError("A relative target requires a measured baseline.")

    target = dict(zip(_TARGET_FIELDS, (
        target_metric_id, target_value, target_comparison, target_unit,
        int(target_baseline_relative), target_baseline_value, measurement_period,
    ))) if has_target else {key: existing.get(key) for key in _TARGET_FIELDS}
    signature = [key for key in _TARGET_FIELDS if key != "target_baseline_value"]
    changed = has_target and any(target[key] != existing.get(key) for key in signature)
    if not changed and existing:
        target_baseline_value = existing.get("target_baseline_value")
        target["target_baseline_value"] = target_baseline_value
    new_version = bool(target.get("target_metric_id")) and (changed or reopened)
    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    if changed and existing and status == "completed":
        completed_at = now  # a newly agreed target starts a new measurement cycle
    if new_version:
        if existing.get("target_version"):
            appdb.run_query("UPDATE action_target_versions SET superseded_at=:at, completed_at=:c WHERE finding_id=:f AND version=:v",
                {"at": now, "c": existing_completed_at, "f": finding_id, "v": existing["target_version"]}, conn=conn)
        version = existing.get("target_version", 0) + 1
        _insert_target_version({"finding_id": finding_id, "completed_at": completed_at, **target}, version, now, conn)
    else:
        version = existing.get("target_version", 0)

    appdb.run_query(
        """
        INSERT INTO finding_actions
            (finding_id, relevant, status, action_taken, owner, deadline, case_id, action_kind, completed_at,
             intended_result, target_metric_id, target_value, target_comparison, target_unit,
             target_baseline_relative, target_baseline_value, target_measurement_period,
             evidence_verified, evidence_version, updated_at)
        VALUES
            (:finding_id, :relevant, :status, :action_taken, :owner, :deadline, :case_id, :action_kind, :completed_at,
             :intended_result, :target_metric_id, :target_value, :target_comparison, :target_unit,
             :target_baseline_relative, :target_baseline_value, :target_measurement_period,
             :evidence_verified, :evidence_version, :updated_at)
        ON CONFLICT(finding_id) DO UPDATE SET
            relevant = excluded.relevant,
            status = excluded.status,
            action_taken = excluded.action_taken,
            owner = excluded.owner,
            deadline = excluded.deadline,
            case_id = COALESCE(excluded.case_id, finding_actions.case_id),
            action_kind = COALESCE(excluded.action_kind, finding_actions.action_kind),
            completed_at = excluded.completed_at,
            intended_result = COALESCE(excluded.intended_result, finding_actions.intended_result),
            target_metric_id = COALESCE(excluded.target_metric_id, finding_actions.target_metric_id),
            target_value = COALESCE(excluded.target_value, finding_actions.target_value),
            target_comparison = COALESCE(excluded.target_comparison, finding_actions.target_comparison),
            target_unit = COALESCE(excluded.target_unit, finding_actions.target_unit),
            target_baseline_relative = COALESCE(excluded.target_baseline_relative, finding_actions.target_baseline_relative),
            target_baseline_value = COALESCE(excluded.target_baseline_value, finding_actions.target_baseline_value),
            target_measurement_period = COALESCE(excluded.target_measurement_period, finding_actions.target_measurement_period),
            evidence_verified = excluded.evidence_verified,
            evidence_version = excluded.evidence_version,
            updated_at = excluded.updated_at
        """,
        {
            "finding_id": finding_id,
            "relevant": relevant,
            "status": status,
            "action_taken": action_taken,
            "owner": owner,
            "deadline": deadline,
            "case_id": case_id,
            "action_kind": action_kind,
            "completed_at": completed_at,
            "intended_result": intended_result,
            "target_metric_id": target_metric_id,
            "target_value": target_value,
            "target_comparison": target_comparison,
            "target_unit": target_unit,
            "target_baseline_relative": (1 if target_baseline_relative else 0) if has_target else None,
            "target_baseline_value": target_baseline_value,
            "target_measurement_period": measurement_period,
            "evidence_verified": None if evidence_verified is None else (1 if evidence_verified else 0),
            "evidence_version": evidence_version,
            "updated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        },
        conn=conn,
    )
    appdb.run_query("UPDATE finding_actions SET target_version=:v WHERE finding_id=:f",
                    {"v": version, "f": finding_id}, conn=conn)
    if new_version:
        resets = ", ".join(f"{key}=NULL" for key in _OUTCOME_FIELDS)
        appdb.run_query(f"UPDATE finding_actions SET {resets} WHERE finding_id=:f", {"f": finding_id}, conn=conn)
        appdb.run_query("UPDATE finding_actions SET outcome_status='pending' WHERE finding_id=:f", {"f": finding_id}, conn=conn)
    if version and completed_at:
        appdb.run_query("UPDATE action_target_versions SET completed_at=:c WHERE finding_id=:f AND version=:v",
                        {"c": completed_at, "f": finding_id, "v": version}, conn=conn)
    if (new_version or reopened) and (case_id or existing.get("case_id")):
        from backend.app.services import case_engine
        case_engine._log_history(case_id or existing["case_id"], "action_reopened" if reopened else "target_changed",
            {"finding_id": finding_id, "target_version": version, "prior_completed_at": existing_completed_at}, conn=conn)


def _action_row_to_dict(row: dict, include_finding_id: bool = False) -> dict:
    d = {
        "relevant": bool(row["relevant"]) if row["relevant"] is not None else None,
        "status": row["status"],
        "action_taken": row["action_taken"],
        "owner": row.get("owner"),
        "deadline": row.get("deadline"),
        "case_id": row.get("case_id"),
        "action_kind": row.get("action_kind"),
        "completed_at": row.get("completed_at"),
        "intended_result": row.get("intended_result"),
        "target_metric_id": row.get("target_metric_id"),
        "target_version": row.get("target_version", 0),
        "outcome_target_version": row.get("outcome_target_version"),
        "target_value": row.get("target_value"),
        "target_comparison": row.get("target_comparison"),
        "target_unit": row.get("target_unit"),
        "target_baseline_relative": bool(row.get("target_baseline_relative")) if row.get("target_baseline_relative") is not None else None,
        "target_measurement_period": row.get("target_measurement_period"),
        "outcome_checked_at": row.get("outcome_checked_at"),
        "outcome_value": row.get("outcome_value"),
        "outcome_period": row.get("outcome_period"),
        "outcome_verdict": row.get("outcome_verdict"),
        "outcome_status": row.get("outcome_status"),
        "outcome_confidence": row.get("outcome_confidence"),
        "evidence_verified": bool(row.get("evidence_verified")) if row.get("evidence_verified") is not None else None,
        "evidence_version": row.get("evidence_version"),
        "overdue": _is_overdue(row.get("deadline"), row["status"]),
        "updated_at": row["updated_at"],
    }
    if include_finding_id:
        d["finding_id"] = row["finding_id"]
    return d


def get_actions_for(finding_ids: list[str]) -> dict[str, dict]:
    """Batch fetch for merging into a findings list. A finding id with no
    row yet simply isn't a key in the returned dict — callers fall back to
    DEFAULT_ACTION rather than this writing a row nobody's touched yet."""
    _ensure_schema()
    if not finding_ids:
        return {}
    rows = appdb.run_query(
        f"SELECT * FROM finding_actions WHERE finding_id IN ({', '.join(f':id{i}' for i in range(len(finding_ids)))})",
        {f"id{i}": fid for i, fid in enumerate(finding_ids)},
    )
    return {row["finding_id"]: _action_row_to_dict(row) for row in rows}


def get_actions_for_case(case_id: str) -> list[dict]:
    """Every action recorded against any finding that belongs to this case
    — a case can span several observations (e.g. a monthly case continuing
    across 3 months), and each observation's own finding_id can carry its
    own action row, so this is naturally a list, not a single action."""
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT * FROM finding_actions WHERE case_id = :case_id ORDER BY updated_at ASC",
        {"case_id": case_id},
    )
    return [{**_action_row_to_dict(row, include_finding_id=True),
             "target_history": get_target_history(row["finding_id"])} for row in rows]


def get_latest_action_for_cases(case_ids: list[str]) -> dict[str, dict]:
    """One action per case -- the most recently updated row -- for list
    views (Actions/Outcomes workspaces) that need a case's current status/
    owner/deadline/target/outcome without an N+1 fetch of every case's
    full action history. A case id with no action row yet simply isn't a
    key in the returned dict, same fallback contract as get_actions_for.

    Deliberately called from the API route layer (main.py), not from
    case_engine.list_cases() itself: finding_actions.py already imports
    COMPARISONS/MEASUREMENT_PERIODS from case_engine, so a call the other
    way would be a circular import. main.py's /cases/{id} route already
    composes case_engine + finding_actions the same way for case detail;
    this mirrors that for the list endpoint."""
    _ensure_schema()
    if not case_ids:
        return {}
    rows = appdb.run_query(
        f"""
        SELECT * FROM finding_actions
        WHERE case_id IN ({', '.join(f':id{i}' for i in range(len(case_ids)))})
        AND updated_at = (
            SELECT MAX(updated_at) FROM finding_actions fa2 WHERE fa2.case_id = finding_actions.case_id
        )
        """,
        {f"id{i}": cid for i, cid in enumerate(case_ids)},
    )
    # A tie on updated_at for the same case_id (two rows updated in the
    # same instant) is possible but rare; last-one-wins here is an
    # acceptable, harmless choice since both rows would be equally "most
    # recent" by the only timestamp this table tracks.
    return {row["case_id"]: _action_row_to_dict(row, include_finding_id=True) for row in rows}


def has_outstanding_action_for_cases(case_ids: list[str]) -> set[str]:
    """Case ids with at least one action -- not just the most recently
    updated one -- still not_started/in_progress. get_latest_action_for_cases
    picks a single row by MAX(updated_at), so an older still-open action
    hides behind a newer completed one there; this list-view indicator
    ("does this case have outstanding work") needs the real answer across
    every action, not just the latest. Read-only aggregate over the
    existing table, no new store. Same case_id-only matching as
    get_latest_action_for_cases (not the COALESCE-through-case_observations
    fallback management.list_actions() uses for legacy unlinked rows) --
    consistent with that existing precedent, not a separate fix."""
    _ensure_schema()
    if not case_ids:
        return set()
    rows = appdb.run_query(
        f"""
        SELECT DISTINCT case_id FROM finding_actions
        WHERE case_id IN ({', '.join(f':id{i}' for i in range(len(case_ids)))})
        AND status IN ('not_started', 'in_progress')
        """,
        {f"id{i}": cid for i, cid in enumerate(case_ids)},
    )
    return {row["case_id"] for row in rows}


_STATUS_RANK = {"not_started": 0, "in_progress": 1, "completed": 2}


def best_status_for_metric(metric_id: str, start_date: str, end_date: str) -> str | None:
    """The most-engaged status any finding_actions row for this metric_id
    picked up between start_date and end_date (inclusive) — 'completed' beats
    'in_progress' beats 'not_started'. Needed because state/short-term finding
    ids re-mint daily (f"{metric_id}-{date}", f"{metric_id}-trailing-{date}"),
    so a single exact-id lookup at outcome-check time would almost always show
    today's fresh, untouched row even if a manager worked every prior day's
    card under a different id. A LIKE prefix match is safe here: this
    codebase's metric_ids only ever use '_' internally, never '-', so
    "metric_id-%" can't accidentally match a different, longer metric_id
    (e.g. "revenue-%" cannot match "revenue_growth-...", since the character
    after "revenue" there is '_' not '-')."""
    _ensure_schema()
    rows = appdb.run_query(
        """
        SELECT status FROM finding_actions
        WHERE finding_id LIKE :prefix
          AND substr(updated_at, 1, 10) BETWEEN :start_date AND :end_date
        """,
        {"prefix": f"{metric_id}-%", "start_date": start_date, "end_date": end_date},
    )
    if not rows:
        return None
    return max((r["status"] for r in rows), key=lambda s: _STATUS_RANK.get(s, -1))
