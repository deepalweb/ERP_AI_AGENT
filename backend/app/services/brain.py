"""Orchestration layer: turns the on-demand "24/7 analyst" scan
(mcp_server.tools.investigation.scan_all) into a precomputed, cached morning
brief so opening the dashboard doesn't mean waiting 80-100s for the same
anomaly detection every other visit already ran.

Nothing here computes anything new — it schedules and caches the existing
scan_all() + tracking.py pipeline. Cache lives in appdb (the app's own state
DB, never the Odoo connection), following the same
CREATE TABLE IF NOT EXISTS pattern tracking.py already uses.
"""

import json
import threading
from datetime import datetime, timedelta

import anyio

from backend.app.db import appdb
from backend.app.services import customer_predictions, health, notifications, tracking, weekly_streaks
from mcp_server.tools import investigation, state_findings

STALE_AFTER = timedelta(hours=24)

_SCHEMA_READY = False
# Guards the stale-cache recompute path: without this, two requests arriving
# while the cache is empty/stale (e.g. two people opening the dashboard on a
# cold start) each launch their own 80-100s scan_all() against Postgres.
# Concurrent heavy aggregations against this DB have already been measured to
# actively slow each other down rather than run in parallel for free (see
# health.py), so this is a real cost to avoid, not just redundant work.
_scan_lock = threading.Lock()


def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS daily_brief (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            investigations TEXT NOT NULL,
            tracking TEXT NOT NULL
        )
    """)
    # daily_brief is a pure 24h cache (STALE_AFTER already discards old rows
    # on read) — no historical value in existing rows, so a best-effort
    # ALTER is enough. SQLite has no ADD COLUMN IF NOT EXISTS, so guard it;
    # a table created fresh by the CREATE above won't need it, one created
    # before this change will.
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN state_findings TEXT NOT NULL DEFAULT '[]'")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN short_term_findings TEXT NOT NULL DEFAULT '[]'")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN health TEXT NOT NULL DEFAULT '{}'")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN short_term_resolved TEXT NOT NULL DEFAULT '[]'")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN customer_finding TEXT")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN new_critical_findings TEXT NOT NULL DEFAULT '[]'")
    except Exception:
        pass
    appdb.run_query("""
        CREATE TABLE IF NOT EXISTS health_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            overall INTEGER NOT NULL,
            domains TEXT NOT NULL
        )
    """)
    _SCHEMA_READY = True


def _snapshot_health():
    """Computes today's Business Health rollup (health.py — Sales/
    Operations/Inventory/Customers, 0-100) and stores it in health_history,
    attaching a delta against the most recent PRIOR row for each domain and
    the overall score. The very first-ever run has no prior row — deltas
    come back None (rendered as "first analysis", not a fabricated 0) rather
    than inventing a baseline that was never measured.

    health.get_business_health() is async (it does real concurrent-ish I/O
    internally); run_daily_scan() is a plain sync function called from
    APScheduler's own worker thread or from inside anyio.to_thread.run_sync
    — both cases have no running event loop already, so anyio.run() here is
    the standard way to bridge sync -> async without restructuring either
    caller into an async job."""
    result = anyio.run(health.get_business_health)

    prior_rows = appdb.run_query("SELECT * FROM health_history ORDER BY generated_at DESC LIMIT 1")
    prior_overall = prior_rows[0]["overall"] if prior_rows else None
    prior_domains = json.loads(prior_rows[0]["domains"]) if prior_rows else []

    domains_with_delta = []
    for d in result["domains"]:
        prior_d = next((p for p in prior_domains if p["id"] == d["id"]), None)
        delta = round(d["score"] - prior_d["score"], 1) if prior_d else None
        domains_with_delta.append({**d, "delta": delta})

    overall_delta = round(result["overall"] - prior_overall, 1) if prior_overall is not None else None

    appdb.run_query(
        "INSERT INTO health_history (generated_at, overall, domains) VALUES (:generated_at, :overall, :domains)",
        {
            "generated_at": datetime.utcnow().isoformat(),
            "overall": result["overall"],
            "domains": json.dumps(result["domains"], default=str),
        },
    )

    return {**result, "overall_delta": overall_delta, "domains": domains_with_delta}


def _previous_brief_findings():
    """The immediately prior daily_brief row's three finding lists, for
    notifications.diff_new_critical_findings to compare THIS scan against —
    regardless of whether that prior row is now stale, since we're about to
    replace it either way. None (all three) on the very first-ever scan,
    when there's nothing to diff against yet."""
    rows = appdb.run_query("SELECT * FROM daily_brief ORDER BY generated_at DESC LIMIT 1")
    if not rows:
        return None, None, None
    row = rows[0]
    return (
        json.loads(row["investigations"]),
        json.loads(row["state_findings"]) if row.get("state_findings") else [],
        json.loads(row["short_term_findings"]) if row.get("short_term_findings") else [],
    )


def run_daily_scan():
    """Run the full scan + outcome-tracking pipeline once and cache the
    result. Called by the scheduler on its daily cadence, and inline by
    get_latest_brief() when the cache is missing or stale."""
    _ensure_schema()
    prev_investigations, prev_state, prev_short_term = _previous_brief_findings()
    investigations = investigation.scan_all()
    # Current-state findings (stock-outs, dead/overstock value) — not fed
    # into tracking.py: outcome tracking needs a baseline/pct_change concept
    # a point-in-time snapshot doesn't have, so these stay untracked rather
    # than faking a baseline.
    state = state_findings.build_inventory_findings() + state_findings.build_operational_findings() + state_findings.build_controls_findings()
    # Trailing 7-day findings (cancellation_rate, delayed_delivery_rate) —
    # same non-tracking exclusion as state findings: tracking.py's
    # check_outcomes() assumes a "YYYY-MM" period key, which a trailing
    # window's date-range label isn't. weekly_streaks.py is the honest
    # counterpart instead: it doesn't need a period key, just consecutive
    # scanned days, so it works where tracking.py's model doesn't.
    short_term = investigation.scan_short_term()
    weekly_streaks.record_streaks(short_term)
    active_streaks = weekly_streaks.get_active_streaks()
    for f in short_term:
        f["streak"] = active_streaks.get(f["metric_id"])
    short_term_resolved = weekly_streaks.get_recently_ended_streaks()
    # Proactive Warning (in-app): what's genuinely NEW in this scan versus
    # the immediately prior one — see notifications.py for why state
    # findings are diffed by metric_id, not by their own (daily-changing) id.
    new_critical = notifications.diff_new_critical_findings(
        investigations, state, short_term, prev_investigations, prev_state, prev_short_term
    )
    tracking.record_new_findings(investigations)
    tracking.check_outcomes()
    history = tracking.get_tracking_history()
    # Business Health used to be computed live on every /intelligence/health
    # request (35s, a known perf problem) — folded into this once-daily scan
    # instead so both /brief and /intelligence/health read the same cached,
    # delta-annotated snapshot (see _snapshot_health).
    health_snapshot = _snapshot_health()
    # Customer Next-Order Intelligence (Phase 1: expected window + overdue
    # detection) — its own appdb tables, not part of daily_brief, since the
    # /customers/predictions page queries them directly with its own
    # filter/sort/pagination rather than loading one big JSON blob. The one
    # exception is customer_finding below: a small, optional summary card
    # for the Business Briefing, decided by build_home_finding() from
    # today's aggregate numbers rather than duplicating any per-customer data.
    customer_preds = customer_predictions.compute_predictions()
    customer_predictions.record_predictions(customer_preds)
    customer_predictions.record_pattern_stats(customer_predictions.compute_pattern_stats(len(customer_preds)))
    customer_finding = customer_predictions.build_home_finding(customer_predictions.get_summary())
    customer_predictions.record_lifecycle_summary(customer_predictions.compute_lifecycle_summary())

    generated_at = datetime.utcnow().isoformat()
    appdb.run_query(
        """
        INSERT INTO daily_brief (generated_at, investigations, tracking, state_findings, short_term_findings, health, short_term_resolved, customer_finding, new_critical_findings)
        VALUES (:generated_at, :investigations, :tracking, :state_findings, :short_term_findings, :health, :short_term_resolved, :customer_finding, :new_critical_findings)
        """,
        {
            "generated_at": generated_at,
            "investigations": json.dumps(investigations, default=str),
            "tracking": json.dumps(history, default=str),
            "state_findings": json.dumps(state, default=str),
            "short_term_findings": json.dumps(short_term, default=str),
            "health": json.dumps(health_snapshot, default=str),
            "short_term_resolved": json.dumps(short_term_resolved, default=str),
            "customer_finding": json.dumps(customer_finding, default=str),
            "new_critical_findings": json.dumps(new_critical, default=str),
        },
    )
    return {
        "generated_at": generated_at,
        "investigations": investigations,
        "tracking": history,
        "state_findings": state,
        "short_term_findings": short_term,
        "health": health_snapshot,
        "short_term_resolved": short_term_resolved,
        "customer_finding": customer_finding,
        "new_critical_findings": new_critical,
    }


def _fresh_cached_brief():
    """Returns the cached brief if one exists and isn't stale, else None."""
    rows = appdb.run_query(
        "SELECT * FROM daily_brief ORDER BY generated_at DESC LIMIT 1"
    )
    if not rows:
        return None

    row = rows[0]
    generated_at = datetime.fromisoformat(row["generated_at"])
    if datetime.utcnow() - generated_at >= STALE_AFTER:
        return None

    return {
        "generated_at": row["generated_at"],
        "investigations": json.loads(row["investigations"]),
        "tracking": json.loads(row["tracking"]),
        # A row cached before this field existed won't have it — the ALTER's
        # DEFAULT '[]' covers rows written before the column existed too, so
        # this key access is always safe, but .get is cheap insurance against
        # an even older row shape from before that DEFAULT applied.
        "state_findings": json.loads(row["state_findings"]) if row.get("state_findings") else [],
        "short_term_findings": json.loads(row["short_term_findings"]) if row.get("short_term_findings") else [],
        "health": json.loads(row["health"]) if row.get("health") else {},
        "short_term_resolved": json.loads(row["short_term_resolved"]) if row.get("short_term_resolved") else [],
        "customer_finding": json.loads(row["customer_finding"]) if row.get("customer_finding") else None,
        "new_critical_findings": json.loads(row["new_critical_findings"]) if row.get("new_critical_findings") else [],
    }


def get_latest_brief():
    """Read the cached brief. Self-heals if the cache is missing or older
    than STALE_AFTER by computing fresh once (slow only on that request)
    rather than serving stale or empty data. Concurrent stale requests share
    one recompute via _scan_lock instead of each running their own scan."""
    _ensure_schema()

    brief = _fresh_cached_brief()
    if brief is not None:
        return brief

    with _scan_lock:
        # Another thread may have finished the recompute while this one
        # waited for the lock — check again before running a second scan.
        brief = _fresh_cached_brief()
        if brief is not None:
            return brief
        return run_daily_scan()
