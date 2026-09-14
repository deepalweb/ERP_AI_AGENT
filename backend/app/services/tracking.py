"""Closed-loop outcome tracking: record what the investigation engine
recommended, and later check whether the metric actually moved back
toward normal. This is the "AI Impact" measurement — the one dimension
that turns this from a reporting tool into evidence the system works.

Verdict is computed symmetrically (did the deviation from baseline shrink
or grow), not by guessing category-specific meaning — see the plan for
why. Nothing here touches the customer's Odoo database; app.db (or
APP_DATABASE_URL) is entirely separate app state.
"""

import json
from datetime import datetime

from backend.app.db import appdb
from mcp_server.tools import intelligence

IMPROVED_RATIO = 0.8  # deviation shrank to <=80% of its original size
WORSENED_RATIO = 1.2  # deviation grew to >=120% of its original size

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS tracked_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_id TEXT NOT NULL,
            period TEXT NOT NULL,
            category TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            baseline_value REAL,
            detected_value REAL,
            detected_pct_change REAL,
            recommendations TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            checked_at TEXT,
            outcome_period TEXT,
            outcome_value REAL,
            outcome_pct_change REAL,
            verdict TEXT,
            UNIQUE(metric_id, period)
        )
    """)
    _SCHEMA_READY = True


def _shift_month(month_key: str, delta: int) -> str:
    year, month = map(int, month_key.split("-"))
    idx = year * 12 + (month - 1) + delta
    y, m = divmod(idx, 12)
    return f"{y:04d}-{m + 1:02d}"


def record_new_findings(investigations: list[dict]):
    """Persist any new critical/opportunity findings from scan_all()'s output."""
    _ensure_schema()
    for inv in investigations:
        confirm = inv["confirm"]
        appdb.run_query(
            """
            INSERT OR IGNORE INTO tracked_findings
                (metric_id, period, category, detected_at, baseline_value,
                 detected_value, detected_pct_change, recommendations, status)
            VALUES (:metric_id, :period, :category, :detected_at, :baseline_value,
                    :detected_value, :detected_pct_change, :recommendations, 'open')
            """,
            {
                "metric_id": inv["metric_id"],
                "period": confirm["period"],
                "category": inv["category"],
                "detected_at": datetime.utcnow().isoformat(),
                "baseline_value": confirm["baseline"],
                "detected_value": confirm["current"],
                "detected_pct_change": confirm["pct_change"],
                "recommendations": json.dumps(inv.get("recommendations") or []),
            },
        )


def _verdict_for(detected_pct_change: float, outcome_pct_change: float) -> str:
    deviation_before = abs(detected_pct_change)
    deviation_after = abs(outcome_pct_change)
    if deviation_before == 0:
        return "unchanged"
    if deviation_after <= deviation_before * IMPROVED_RATIO:
        return "improved"
    if deviation_after >= deviation_before * WORSENED_RATIO:
        return "worsened"
    return "unchanged"


def check_outcomes():
    """For every open finding, check whether the following month (now complete)
    has landed, and if so compute the outcome verdict against the original baseline."""
    _ensure_schema()
    open_rows = appdb.run_query("SELECT * FROM tracked_findings WHERE status = 'open'")

    for row in open_rows:
        metric = intelligence.METRICS.get(row["metric_id"])
        if metric is None:
            continue

        trend = intelligence.get_trend(metric)
        next_period = _shift_month(row["period"], 1)
        outcome_row = next((m for m in trend if m["month"] == next_period and not m["is_partial"]), None)
        if outcome_row is None:
            continue  # not enough time has passed yet

        baseline = row["baseline_value"]
        outcome_value = outcome_row["value"]
        outcome_pct_change = ((outcome_value - baseline) / baseline * 100) if baseline else None
        if outcome_pct_change is None:
            continue

        verdict = _verdict_for(row["detected_pct_change"], outcome_pct_change)

        appdb.run_query(
            """
            UPDATE tracked_findings
            SET status = 'resolved', checked_at = :checked_at, outcome_period = :outcome_period,
                outcome_value = :outcome_value, outcome_pct_change = :outcome_pct_change, verdict = :verdict
            WHERE id = :id
            """,
            {
                "checked_at": datetime.utcnow().isoformat(),
                "outcome_period": next_period,
                "outcome_value": outcome_value,
                "outcome_pct_change": round(outcome_pct_change, 2),
                "verdict": verdict,
                "id": row["id"],
            },
        )


def get_most_recent_prior(metric_id: str, before_period: str):
    """The most recent tracked_findings row for this metric with a period
    strictly before `before_period` — resolved or still open. Mirrors
    Home.jsx's findPriorFinding() exactly (same "period < current" pick),
    so the Context+Memory fed to the LLM (see evidence_engine.py's
    get_finding_memory) matches what a manager already sees printed next
    to the recommendation on the card — never a different "last time" than
    the one on screen. Returns None when there's no prior period at all,
    the common case for a metric that's only ever been flagged once."""
    _ensure_schema()
    rows = appdb.run_query(
        """
        SELECT * FROM tracked_findings
        WHERE metric_id = :metric_id AND period < :before_period
        ORDER BY period DESC LIMIT 1
        """,
        {"metric_id": metric_id, "before_period": before_period},
    )
    if not rows:
        return None
    row = rows[0]
    row["recommendations"] = json.loads(row["recommendations"] or "[]")
    return row


def get_tracking_history(limit: int = 20):
    _ensure_schema()
    rows = appdb.run_query(
        "SELECT * FROM tracked_findings ORDER BY detected_at DESC LIMIT :limit",
        {"limit": limit},
    )
    for row in rows:
        row["recommendations"] = json.loads(row["recommendations"] or "[]")
        metric = intelligence.METRICS.get(row["metric_id"])
        row["metric_label"] = metric.label if metric else row["metric_id"]
        row["unit"] = metric.unit if metric else "count"
    return rows
