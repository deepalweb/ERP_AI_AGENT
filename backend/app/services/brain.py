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
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anyio

from backend.app.db import appdb, reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from backend.app.services import (
    action_outcomes,
    case_engine,
    customer_predictions,
    finding_actions,
    forecast,
    forecast_outcomes,
    health,
    inventory,
    inventory_kpi_history,
    loss_and_damage,
    notifications,
    state_tracking,
    stock_alerts,
    stock_counts,
    tracking,
    weekly_streaks,
)
from mcp_server.tools import (
    delivery_partner_failures,
    dispatch_overage,
    dispatch_pattern,
    extreme_event,
    fresh_production,
    incident_engine,
    investigation,
    measurement,
    mwh_inventory_mismatch,
    operation_flow,
    price_intelligence,
    promotion_intelligence,
    returns_intelligence,
    state_findings,
    stock_reconciliation,
    transfer_discrepancy,
)

STALE_AFTER = timedelta(hours=24)

_SCHEMA_READY = False


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
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN state_tracking TEXT NOT NULL DEFAULT '[]'")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN streak_outcomes TEXT NOT NULL DEFAULT '[]'")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN data_freshness TEXT")
    except Exception:
        pass
    try:
        appdb.run_query("ALTER TABLE daily_brief ADD COLUMN synthesis TEXT")
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


def _record_inventory_kpi_snapshot():
    """Records today's Inventory page KPI strip (inventory_kpi_history.py) --
    reuses the exact same functions the /inventory/* routes call, not a
    second set of queries. get_inventory_depth() is async; same anyio.run()
    bridge _snapshot_health() above uses, and the same reason this is its
    own top-level function rather than an inline call inside run_daily_scan:
    a test that calls run_daily_scan() directly from an async test function
    (no anyio.to_thread.run_sync boundary) has a running event loop already,
    which anyio.run() can't nest inside -- tests stub this whole function
    out, the same way they already stub _snapshot_health, instead of hitting
    that nesting error."""
    inventory_kpi_history.record_inventory_kpi_snapshot(
        stock_alerts=stock_alerts.get_stock_alerts(),
        depth=anyio.run(inventory.get_inventory_depth),
        stuck_counts=stock_counts.get_stuck_stock_counts(),
        loss_and_damage=loss_and_damage.get_loss_and_damage(),
    )


def _data_freshness():
    """Whether the order data every finding is computed from is actually
    current — surfaced so the frontend can say so plainly rather than let
    the Business Brain look more certain than its data. Anchored to the
    data's own latest order (same as_of principle intelligence.py's
    get_trailing_trend and incident_engine already use), not wall-clock
    freshness assumptions. status == "stale" past 1 day is a deliberately
    tight bar: this is a quick-commerce grocery business where "yesterday"
    is already old."""
    rows = reporting.run_query(
        "SELECT MAX(date_order) AS latest FROM sale_order WHERE store_id = ANY(:stores)",
        {"stores": list(CELESTE_STORE_IDS)},
    )
    latest = rows[0]["latest"] if rows else None
    if latest is None:
        return {"latest_order_at": None, "days_stale": None, "status": "unavailable"}
    days_stale = (datetime.utcnow() - latest).days
    return {
        "latest_order_at": latest.isoformat(),
        "days_stale": days_stale,
        "status": "fresh" if days_stale <= 1 else "stale",
    }


def _apply_data_freshness(measurements, freshness):
    """Carry the broader order-data warning without attributing it to the
    wrong source. Transfer measurements have no ingest watermark, so their
    event timestamps cannot prove how current the mirror is."""
    if freshness.get("status") == "fresh":
        return
    transfer_metrics = ("transfer_discrepancy_", "extreme_event_", "operation_flow_")
    for record in measurements:
        metric_id = record.get("metric_id", "")
        transfer_source = (
            metric_id.startswith(transfer_metrics)
            or metric_id in {"dispatch_pattern", "dispatch_sent_above_plan"}
        )
        record.update(
            freshness="unknown",
            reason=(
                "Inventory-transfer source refresh time is unavailable; event timestamps alone cannot confirm the reporting mirror is current."
                if metric_id.startswith("operation_flow_")
                else "Inventory-transfer source refresh time is unavailable; event timestamps alone cannot confirm the reporting mirror is current."
                if transfer_source else "Order source freshness is not established."
            ),
        )


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


def _compute_daily_brief():
    """Run the full scan + outcome-tracking pipeline once and cache the
    result. Called by the scheduler on its daily cadence, and inline by
    get_latest_brief() when the cache is missing or stale."""
    _ensure_schema()
    prev_investigations, prev_state, prev_short_term = _previous_brief_findings()
    investigations = investigation.scan_all()
    # One extra LLM call, ONLY when 2+ of today's findings were independently
    # corroborated against each other (evidence_engine.
    # cross_metric_corroboration_checks, already run inside scan_all()) --
    # see generate_daily_synthesis's own docstring for why this is the one
    # place the pipeline reasons ACROSS findings instead of within one, and
    # why it stays silent (None) rather than force a connection on days
    # where nothing actually corroborates.
    synthesis = investigation.generate_daily_synthesis(investigations)
    # Current-state findings (stock-outs, dead/overstock value) — tracking.py
    # itself can't cover these (no baseline/pct_change, just a point-in-time
    # snapshot), so state_tracking.py is the sibling module built for this
    # shape: same record + check pattern, but comparing today's snapshot
    # against the value first observed, not a calendar period shift.
    #
    # incident_engine's store x SKU x hour findings are folded in here too —
    # they're emitted in the exact same dict shape (see incident_engine.py's
    # docstring), so this one line is all it takes for them to get
    # persistence tracking, proactive-warning diffing, and every frontend
    # renderer for free, with no special-casing anywhere else.
    state = measurement.combine([
        state_findings.build_inventory_findings(),
        state_findings.build_operational_findings(),
        state_findings.build_controls_findings(),
        incident_engine.build_incident_findings(),
        transfer_discrepancy.build_transfer_discrepancy_findings(),
        extreme_event.build_extreme_event_findings(),
        dispatch_overage.build_dispatch_overage_findings(),
        dispatch_pattern.build_dispatch_pattern_findings(),
        operation_flow.build_operation_flow_findings(),
        # Price Intelligence Phase 1, 2026-10-22: per-product purchase
        # price deviation against each product's own history (see
        # mcp_server/tools/price_intelligence.py's module docstring for
        # why this replaces a flat threshold).
        price_intelligence.build_price_intelligence_findings(),
        # Price Intelligence Phase 2, 2026-11-03: active promotions whose
        # discount rate is a cross-sectional outlier against every other
        # approved promotion's rate -- see
        # mcp_server/tools/promotion_intelligence.py's module docstring for
        # why this isn't a per-promotion time-series check.
        promotion_intelligence.build_promotion_intelligence_findings(),
        # Outlet inventory mismatch, 2026-10-24: confirmed sales with no
        # stock deduction at all, clustered by episode (see
        # mcp_server/tools/stock_reconciliation.py's module docstring for
        # why this is cluster-based rather than a flat per-line rate).
        stock_reconciliation.build_stock_reconciliation_findings(),
        # MWH inventory mismatch, 2026-11-03: active sellers with zero
        # on-hand quantity in BOTH MWH's and every outlet's own primary
        # stock location -- see mcp_server/tools/mwh_inventory_mismatch.py's
        # module docstring for the verified product/unit/value numbers and
        # why fresh-production items are excluded.
        mwh_inventory_mismatch.build_mwh_inventory_mismatch_findings(),
        # Fresh production (central warehouse), 2026-10-31: missed rounds
        # (scheduled, never confirmed) and output shortfall on rounds that
        # do complete -- see mcp_server/tools/fresh_production.py's module
        # docstring for the verified completion-rate and deviation numbers.
        fresh_production.build_fresh_production_findings(),
        # Delivery partner failures, 2026-11-03: reuses intelligence.py's
        # already-registered uber_failure_rate/pickme_cancellation_rate
        # MetricDefs and their existing trend/anomaly machinery -- those
        # were previously only reachable through scan_attention() (Ask
        # Celeste chat), never the daily Investigate scan. See
        # mcp_server/tools/delivery_partner_failures.py's module docstring.
        delivery_partner_failures.build_delivery_partner_failure_findings(),
        # Returns, 2026-11-03: reuses intelligence.py's return_rate MetricDef
        # (same trend/anomaly reuse pattern as delivery_partner_failures.py)
        # -- the raw return count grows with order volume, but the RATE has
        # climbed ~33x disproportionately; see returns_intelligence.py's
        # module docstring for the verified numbers.
        returns_intelligence.build_returns_intelligence_findings(),
        # Customer module review, 2026-10-13: the first customer-domain
        # signal wired into the case system -- a pure function of already-
        # historical cohort data, so (unlike lifecycle/predictions below)
        # it needs no reordering of this scan to run before sync_cases.
        customer_predictions.build_second_order_decline_finding(),
    ])
    data_freshness = _data_freshness()
    _apply_data_freshness(state.measurements, data_freshness)
    state_tracking.record_new_findings(state)
    state_tracking.check_outcomes(state, measurements=state.measurements)
    state_tracking_history = state_tracking.get_tracking_history()
    # Trailing 7-day findings (cancellation_rate, delayed_delivery_rate) —
    # tracking.py's check_outcomes() assumes a "YYYY-MM" period key, which a
    # trailing window's date-range label isn't. weekly_streaks.py covers
    # both streak continuity AND (as of Phase 4) a frozen verdict once a
    # sound comparison point exists (WINDOW_DAYS days, or the streak ending
    # early) — see weekly_streaks.get_verdicted_streaks().
    short_term = investigation.scan_short_term()
    short_measurements = getattr(short_term, "measurements", [])
    if data_freshness.get("status") != "fresh":
        for record in short_measurements:
            record.update(freshness="unknown", reason="Order source freshness is not established.")
    weekly_streaks.record_streaks(short_term, measurements=short_measurements)
    active_streaks = weekly_streaks.get_active_streaks()
    for f in short_term:
        f["streak"] = active_streaks.get(f["metric_id"])
    short_term_resolved = weekly_streaks.get_recently_ended_streaks()
    streak_outcomes = weekly_streaks.get_verdicted_streaks()
    # Proactive Warning (in-app): what's genuinely NEW in this scan versus
    # the immediately prior one — see notifications.py for why state
    # findings are diffed by metric_id, not by their own (daily-changing) id.
    new_critical = notifications.diff_new_critical_findings(
        investigations, state, short_term, prev_investigations, prev_state, prev_short_term
    )
    # Surfaced so the frontend can say plainly when the order data behind
    # every finding above isn't current — see _data_freshness's docstring.
    # Computed here (rather than at its old spot further down) because
    # case_engine.sync_cases needs it too: a weekly/snapshot case's
    # verification window must not advance on stale data.
    # Phase 2d: measure any completed action's declared target BEFORE this
    # cycle's case resolution runs, so a case whose verification window
    # closes this same cycle can already see a just-landed action outcome
    # rather than holding one extra cycle unnecessarily.
    action_outcomes.check_outcomes()
    # Phase 2a Case Store sync -- mutates each finding in place with a
    # 'case_id' (additive; every existing consumer of these lists is
    # unaffected). Runs after notifications' own diff above so that diff
    # sees the lists exactly as it always has. Protected against
    # concurrent scans by the _scan_lock() this whole function runs under.
    case_engine.sync_cases(
        investigations, state, short_term, data_fresh=data_freshness.get("status") == "fresh",
        measurements=[*getattr(investigations, "measurements", []), *state.measurements, *short_measurements],
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
    # Starts accumulating per-customer lifecycle history now, ahead of any
    # transition-rate feature -- see record_lifecycle_snapshot()'s own
    # docstring for why a frozen as_of_date doesn't fabricate a new day.
    customer_predictions.record_lifecycle_snapshot(customer_predictions.compute_lifecycle_snapshot_rows())

    # Inventory KPI strip history -- same reasoning as the customer lifecycle
    # snapshot above, just for the Inventory page's 6 headline numbers.
    _record_inventory_kpi_snapshot()

    # Outlet order-forecast outcome tracking, 2026-11-05: records today's
    # TimesFM predictions, then grades any earlier prediction whose date has
    # fully passed -- see forecast_outcomes.py's module docstring.
    forecast_outcomes.record_predictions(forecast.get_outlet_order_forecast())
    forecast_outcomes.record_actuals()

    generated_at = datetime.utcnow().isoformat()
    appdb.run_query(
        """
        INSERT INTO daily_brief (generated_at, investigations, tracking, state_findings, short_term_findings, health, short_term_resolved, customer_finding, new_critical_findings, state_tracking, streak_outcomes, data_freshness, synthesis)
        VALUES (:generated_at, :investigations, :tracking, :state_findings, :short_term_findings, :health, :short_term_resolved, :customer_finding, :new_critical_findings, :state_tracking, :streak_outcomes, :data_freshness, :synthesis)
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
            "state_tracking": json.dumps(state_tracking_history, default=str),
            "streak_outcomes": json.dumps(streak_outcomes, default=str),
            "data_freshness": json.dumps(data_freshness, default=str),
            "synthesis": json.dumps(synthesis, default=str) if synthesis else None,
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
        "state_tracking": state_tracking_history,
        "streak_outcomes": streak_outcomes,
        "data_freshness": data_freshness,
        "synthesis": synthesis,
    }


def _fresh_cached_brief(*, allow_stale=False):
    """Returns the cached brief if one exists and isn't stale, else None."""
    rows = appdb.run_query(
        "SELECT * FROM daily_brief ORDER BY generated_at DESC LIMIT 1"
    )
    if not rows:
        return None

    row = rows[0]
    generated_at = datetime.fromisoformat(row["generated_at"])
    age = datetime.utcnow() - generated_at
    if age >= STALE_AFTER and not allow_stale:
        return None

    customer_finding = json.loads(row["customer_finding"]) if row.get("customer_finding") else None
    if customer_finding and customer_finding.get("analytics_contract") != customer_predictions.ANALYTICS_CONTRACT:
        # Old briefs included partner storefronts. Hide just this stale card
        # until the next scan builds a scoped snapshot; preserve other evidence.
        customer_finding = None

    return {
        "cache_status": "stale" if age >= STALE_AFTER else "fresh",
        "cache_age_seconds": max(0, age.total_seconds()),
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
        "customer_finding": customer_finding,
        "new_critical_findings": json.loads(row["new_critical_findings"]) if row.get("new_critical_findings") else [],
        "state_tracking": json.loads(row["state_tracking"]) if row.get("state_tracking") else [],
        "streak_outcomes": json.loads(row["streak_outcomes"]) if row.get("streak_outcomes") else [],
        "data_freshness": json.loads(row["data_freshness"]) if row.get("data_freshness") else None,
        "synthesis": json.loads(row["synthesis"]) if row.get("synthesis") else None,
    }


def _prepare_scan_schemas():
    """Run app-state schema creation and migrations before opening the scan transaction."""
    for ensure in (
        _ensure_schema,
        state_tracking._ensure_schema,
        weekly_streaks._ensure_schema,
        case_engine._ensure_schema,
        finding_actions._ensure_schema,
        tracking._ensure_schema,
        notifications._ensure_schema,
        customer_predictions._ensure_schema,
        customer_predictions._ensure_lifecycle_schema,
        customer_predictions._ensure_lifecycle_snapshot_schema,
        inventory_kpi_history._ensure_schema,
    ):
        ensure()


def run_daily_scan(*, force: bool = True):
    """Serialize every scan, including scheduled jobs, across threads/processes.

    A forced request refreshes an existing cache, but reuses a scan that
    completed while it waited. Failed scans release the lock for retries.
    """
    requested_at = datetime.utcnow()
    with appdb.case_store_lock():
        _ensure_schema()
        brief = _fresh_cached_brief()
        if brief is not None and (
            not force or datetime.fromisoformat(brief["generated_at"]) >= requested_at
        ):
            return brief
        _prepare_scan_schemas()
        with appdb.transaction():
            return _compute_daily_brief()


def get_latest_brief():
    """Read the cached brief. Self-heals if the cache is missing or older
    than STALE_AFTER by computing fresh once (slow only on that request)
    rather than serving stale or empty data. Concurrent stale requests share
    one recompute via the shared scan lock instead of each running a scan."""
    # Once this process has initialized the schema, fresh reads need not
    # wait for a scheduled refresh. Schema creation itself stays locked.
    if _SCHEMA_READY:
        brief = _fresh_cached_brief()
        if brief is not None:
            return brief
    return run_daily_scan(force=False)


def catch_up_missed_scan():
    """The 5am cron (main.py's start_scheduler) only fires if this process
    is alive at that exact moment -- BackgroundScheduler doesn't queue or
    retry a fire it missed. A dev-environment backend that gets restarted
    through the morning (observed live, 2026-10-21->22: no scan ran at all
    on the 22nd, leaving Investigate showing the 21st's cases as if nothing
    had been checked) silently loses the whole day's scan otherwise, with
    no error anywhere to notice by.

    Runs the scan in the background (same non-blocking path as the manual
    /brief/refresh button) if today's calendar date (APP_TIMEZONE) has no
    daily_brief row yet -- intentionally calendar-day-based, not the 24h
    STALE_AFTER window _fresh_cached_brief() uses, since a scan from 11pm
    yesterday is "fresh" by that window but still isn't today's data.
    Call once, at process startup; cheap (one read) when already caught up."""
    tz = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Colombo"))
    today = datetime.now(tz).date()
    rows = appdb.run_query("SELECT generated_at FROM daily_brief ORDER BY generated_at DESC LIMIT 1")
    if rows:
        last_generated = datetime.fromisoformat(rows[0]["generated_at"]).replace(tzinfo=ZoneInfo("UTC"))
        if last_generated.astimezone(tz).date() >= today:
            return
    from backend.app.services import home_briefing
    home_briefing.request_refresh(retry=True)  # already backgrounds itself; non-blocking
