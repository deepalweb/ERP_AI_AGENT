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

from datetime import datetime

from backend.app.db import appdb

STATUSES = {"not_started", "in_progress", "completed"}
DEFAULT_ACTION = {"relevant": None, "status": "not_started", "action_taken": None, "owner": None, "updated_at": None}

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
    # above won't touch an existing table, so `owner` needs the same
    # best-effort ALTER pattern brain.py uses for its cache columns.
    try:
        appdb.run_query("ALTER TABLE finding_actions ADD COLUMN owner TEXT")
    except Exception:
        pass
    _SCHEMA_READY = True


def set_action(finding_id: str, relevant: bool | None, status: str, action_taken: str | None, owner: str | None = None):
    """Full-replace upsert — the frontend always sends the complete current
    selection, so there's no partial-merge case to handle."""
    _ensure_schema()
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    appdb.run_query(
        """
        INSERT INTO finding_actions (finding_id, relevant, status, action_taken, owner, updated_at)
        VALUES (:finding_id, :relevant, :status, :action_taken, :owner, :updated_at)
        ON CONFLICT(finding_id) DO UPDATE SET
            relevant = excluded.relevant,
            status = excluded.status,
            action_taken = excluded.action_taken,
            owner = excluded.owner,
            updated_at = excluded.updated_at
        """,
        {
            "finding_id": finding_id,
            "relevant": relevant,
            "status": status,
            "action_taken": action_taken,
            "owner": owner,
            "updated_at": datetime.utcnow().isoformat(),
        },
    )


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
    return {
        row["finding_id"]: {
            "relevant": bool(row["relevant"]) if row["relevant"] is not None else None,
            "status": row["status"],
            "action_taken": row["action_taken"],
            "owner": row.get("owner"),
            "updated_at": row["updated_at"],
        }
        for row in rows
    }
