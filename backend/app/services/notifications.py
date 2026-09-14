"""Warning Engine (Business Brain component #10) — in-app only for now, no
external channel (no SMTP/Slack/WhatsApp credentials exist in this
codebase; see the delivery-channel decision in conversation). The goal:
tell someone a NEW critical finding showed up since they last looked, and
only if it's actually worth interrupting them for — not every new critical
finding, just the ones with a real reason behind them (see
_worth_warning_about below).

No auth exists in this app, so "seen" is a single global checkpoint
(last_acknowledged_at), not per-user state — same posture as every other
appdb cache table here (health_history, daily_brief, weekly_streaks): one
shared source of truth, not a multi-tenant model this app doesn't have.

The tricky part is defining "new" correctly per finding shape:
  - Monthly investigations key their own `id` by metric+period (e.g.
    "revenue-2026-08"). A new id there means a genuinely new period was
    flagged — that IS "new."
  - State findings (state_findings.py) AND short-term/trailing findings
    (investigation.py's build_trailing_investigation) both re-mint their
    `id` every single scan because it embeds today's as_of_date (e.g.
    "top_seller_stock-2026-09-11" becomes "...-09-12" tomorrow even if the
    SAME stock-out is still unresolved — a trailing window's as_of_date is
    the data's own latest point, which advances daily just like a state
    finding's snapshot date, NOT a stable period like a calendar month).
    Diffing those by `id` would flag them as "new" every day forever —
    caught live in this codebase: an earlier version of this diff used
    `id` for short-term findings by mistake, which would have re-fired a
    "new critical finding" notification every day a cancellation-rate
    spike stayed flagged instead of once. Both are diffed by `metric_id`
    instead: only a metric that wasn't already flagged critical in the
    immediately prior scan counts as new.
"""

import json
from datetime import datetime, timezone

from backend.app.db import appdb

_SCHEMA_READY = False

# A bare critical finding with nothing else behind it — single period, no
# quantified impact, full data-quality confidence — scores exactly 3.0
# under impact_engine.business_impact_score (severity weight 3 x
# persistence 1.0 x impact-presence 1.0 x confidence 1.0). Requiring
# STRICTLY more means a finding needs at least one real additional reason
# to interrupt someone: it's persisted 2+ periods, or it carries a real
# Rs/count impact figure — and a finding whose evidence is low_confidence
# can't clear this floor on bare severity alone either, since that
# dampens the score below 3.0. Same "stated rule, not a judgment call"
# posture as impact_engine.SEVERITY_WEIGHT / investigation._priority_for.
WARNING_IMPACT_THRESHOLD = 3.0


def _worth_warning_about(f: dict) -> bool:
    return f.get("business_impact_score", 0) > WARNING_IMPACT_THRESHOLD


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS notification_state (
            id INTEGER PRIMARY KEY,
            last_acknowledged_at TEXT
        )
    """)
    _SCHEMA_READY = True


def _new_by_id(current: list[dict], previous: list[dict]) -> list[dict]:
    previous_ids = {f["id"] for f in previous}
    return [
        f for f in current
        if f.get("category") == "critical" and f["id"] not in previous_ids and _worth_warning_about(f)
    ]


def _new_by_metric(current: list[dict], previous: list[dict]) -> list[dict]:
    previous_metrics = {f["metric_id"] for f in previous}
    return [
        f for f in current
        if f.get("category") == "critical" and f["metric_id"] not in previous_metrics and _worth_warning_about(f)
    ]


def diff_new_critical_findings(
    investigations: list[dict],
    state_findings: list[dict],
    short_term_findings: list[dict],
    prev_investigations: list[dict] | None,
    prev_state_findings: list[dict] | None,
    prev_short_term_findings: list[dict] | None,
) -> list[dict]:
    """Pure, no I/O — called from brain.py's run_daily_scan() with this
    scan's three finding lists and the previous daily_brief row's lists (or
    None on the very first-ever scan, which has nothing to diff against and
    so reports nothing new, same posture as _snapshot_health's first-run
    deltas: no fabricated baseline)."""
    if prev_investigations is None:
        return []
    return (
        _new_by_id(investigations, prev_investigations)
        + _new_by_metric(state_findings, prev_state_findings)
        + _new_by_metric(short_term_findings, prev_short_term_findings)
    )


def get_last_acknowledged_at() -> str | None:
    _ensure_schema()
    rows = appdb.run_query("SELECT last_acknowledged_at FROM notification_state WHERE id = 1")
    return rows[0]["last_acknowledged_at"] if rows else None


def acknowledge() -> str:
    """Marks everything seen up to now. Called when the Home page loads —
    opening the briefing IS seeing today's findings, no separate 'mark as
    read' click needed."""
    _ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    appdb.run_query(
        """
        INSERT INTO notification_state (id, last_acknowledged_at) VALUES (1, :now)
        ON CONFLICT(id) DO UPDATE SET last_acknowledged_at = excluded.last_acknowledged_at
        """,
        {"now": now},
    )
    return now


def get_summary() -> dict:
    """New critical findings across every daily_brief scan since the last
    acknowledgment — covers the case where nobody opened the app for a few
    days and more than one scan ran in between. A finding only ever appears
    once in this union: diff_new_critical_findings only flags it on the
    scan where it FIRST appears, so a still-open issue from two scans ago
    isn't re-flagged today.

    Never acknowledged yet (fresh install): establishes the checkpoint at
    now and reports zero, rather than dumping the entire pre-existing
    findings backlog on someone's very first visit as if it just happened."""
    _ensure_schema()
    last_ack = get_last_acknowledged_at()
    if last_ack is None:
        acknowledge()
        return {"new_critical_count": 0, "new_findings": []}

    rows = appdb.run_query(
        "SELECT new_critical_findings FROM daily_brief WHERE generated_at > :last_ack ORDER BY generated_at",
        {"last_ack": last_ack},
    )
    seen_ids = set()
    findings = []
    for row in rows:
        for f in json.loads(row["new_critical_findings"] or "[]"):
            if f["id"] not in seen_ids:
                seen_ids.add(f["id"])
                findings.append(f)

    return {"new_critical_count": len(findings), "new_findings": findings}
