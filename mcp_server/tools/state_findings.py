"""Current-state findings: stock-outs, low-stock, dead stock, overstock, and
stuck physical stock counts.

These are point-in-time conditions, not month-over-month trends, so they
don't fit intelligence.py's trend/anomaly shape (there's no "baseline month"
for "how many products are out of stock right now"). This is a second,
simpler finding shape — same Situation Card story (title, category, impact,
breakdowns, recommendations) but built from a snapshot instead of a trend.

Must stay fully synchronous: this runs inside investigation.scan_all()'s
ThreadPoolExecutor / brain.run_daily_scan()'s anyio.to_thread.run_sync,
where no event loop is available. Reuses backend.app.services.inventory's
SQL constant and pure helpers directly (run via the sync reporting.run_query
instead of that module's own async fetch_report) rather than duplicating the
dead-stock/overstock classification logic.

Thresholds are the same ones already driving the Business Health inventory
score (backend/app/services/health.py) — this must agree with what already
counts as "bad" there, not define a second, competing definition.

Phase 5 of the evidence-based root-cause rollout: _stock_availability_finding,
_dead_overstock_finding, and _stuck_stock_count_finding attach evidence via
evidence_engine.py's build_snapshot_evidence — the same >=60% concentration
rule as the trend findings, adapted for a snapshot's "which segment holds
most of the total" instead of a trend's "which segment drove the move," plus
a data-quality (staleness) check. _top_seller_stock_finding and
_outlet_activity_finding are deliberately left out — see the comment next to
each one's "root_cause": None for why forcing the same check onto those two
would produce a hollow, trivially-true result rather than real evidence.
There is no LLM in this module (all four findings build their recommendations
directly from the evidence, no _generate_recommendations() call), so unlike
the trend metrics this isn't protecting an LLM from inventing a cause — it's
giving the human reader the same "confirmed: X% concentrated in Y" framing
already used elsewhere, instead of a bare, unranked breakdown list.
"""

from datetime import date

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from backend.app.services import data_controls, loss_and_damage, stock_counts
from backend.app.services import inventory as inventory_service
from backend.app.services.stock_alerts import get_stock_alerts, get_top_product_stock_coverage
from mcp_server.tools import evidence_engine, impact_engine, measurement

DEAD_STOCK_VALUE_SHARE_THRESHOLD_PCT = 5
OVERSTOCK_VALUE_SHARE_THRESHOLD_PCT = 25
# Value tiers for _stuck_stock_count_finding's priority -- same posture as
# _priority_for_share: stated cutoffs, not a judgment call. Discovered live
# investigating one real case (Colombo 6, Rs ~150k+ of unscanned-line value)
# -- these bands aren't re-tuned from a larger sample yet, same caveat as
# evidence_engine.CONCENTRATION_THRESHOLD_PCT's own docstring.
STUCK_COUNT_CRITICAL_VALUE_RS = 100_000
STUCK_COUNT_HIGH_VALUE_RS = 20_000
# Completed Stock-Count Variance & Shrinkage Investigation: tiers set from
# the real observed distribution (last 30 days, 53 completed sessions,
# 2026-09-15): only 1 session exceeded Rs 500k (the Rs 7.1M Colombo 6
# outlier), 9 exceeded Rs 100k, the rest form a long tail below that --
# real gaps in the data, not round numbers picked blind.
COMPLETED_VARIANCE_CRITICAL_SESSION_RS = 500_000
COMPLETED_VARIANCE_HIGH_SESSION_RS = 100_000
# How far back a scan looks for a newly-completed session -- completed
# counts are sporadic (some months have only a handful), so "since
# yesterday" would miss most of them; 7 days balances catching a real
# spike promptly against re-flagging the same session's snapshot daily
# (case_engine's own same-day upsert already keeps a single day's re-scan
# from duplicating -- this window is about not going quiet between scans).
COMPLETED_VARIANCE_WINDOW_DAYS = 7

# Business Data Controls, Wave 1 Step 1: tiers set from the real observed
# count (54 real INVENTORY products, company-wide, on 2026-09-22) -- a
# process-control finding like this is primarily about HOW WIDESPREAD the
# condition is, not its Rs value (real total value is only ~Rs 26.5k,
# small relative to other findings), so priority keys off count. Raw data
# comes from backend.app.services.data_controls -- shared unchanged with
# the get_data_controls Ask Celeste tool, never a second copy of the SQL.
NEGATIVE_STOCK_CRITICAL_COUNT = 100
NEGATIVE_STOCK_HIGH_COUNT = 20

# Damage write-off: tiers set from the real observed distribution, not a
# guess -- 172 overlapping 14-day trailing windows across 6 months of
# confirmed-state damaged_order history (2026-04-04 to 2026-10-05), valued
# at catalog cost. This condition is chronic, not anomalous: even the
# LOWEST 14-day window in that history was Rs 73k, median Rs 388k -- so
# unlike completed_stock_count_variance's rare-spike framing, this always
# fires (same posture as _stock_availability_finding/_stuck_stock_count_
# finding) with priority reflecting whether the CURRENT window is worse
# than typical, not whether damage exists at all (it always does).
DAMAGE_WRITEOFF_CRITICAL_VALUE_RS = 570_000  # p90 of the 172 windows
DAMAGE_WRITEOFF_HIGH_VALUE_RS = 390_000  # p50 (median) of the 172 windows
DAMAGE_WRITEOFF_WINDOW_DAYS = 14


def _priority_for_share(share_pct: float, threshold_pct: float) -> str:
    """Same posture as investigation._priority_for: a stated rule, not a
    judgment call. Twice the threshold share is 'critical', otherwise 'high'
    — mirrors the >=40%-off-baseline cutoff used for trend findings."""
    return "critical" if share_pct >= threshold_pct * 2 else "high"


def _stock_availability_finding():
    alerts = get_stock_alerts()
    measurement.source_as_of(alerts.get("as_of_date"), verifies_source=False)
    if not alerts.get("as_of_date"):
        return None

    segments = alerts["segments_by_outlet"]
    if not segments:
        measurement.source_unavailable("No outlet inventory coverage.")
    total_out = sum(s["out_of_stock"] for s in segments)
    total_low = sum(s["low_stock"] for s in segments)
    if total_out == 0:
        return None  # matches health.py: out-of-stock is the trigger; low-stock is context
    # total_out counts (outlet, product) rows -- the same product out of
    # stock at 3 outlets counts 3 times. distinct_products is the number a
    # reader actually means by "how many products are unavailable"; both
    # are real, neither is invented, they just answer different questions.
    distinct_products = alerts.get("distinct_out_of_stock_products", total_out)

    by_outlet = [
        {"name": s["outlet"], "current": s["out_of_stock"], "delta": s["out_of_stock"]}
        for s in segments
        if s["out_of_stock"] > 0
    ]
    by_outlet.sort(key=lambda c: c["delta"], reverse=True)

    # top_out_of_stock is per (outlet, product) — the same product can
    # appear multiple times for different outlets, which would otherwise
    # repeat a name in the recommendation text.
    seen_names = []
    for item in alerts["top_out_of_stock"]:
        if item["name"] not in seen_names:
            seen_names.append(item["name"])
        if len(seen_names) == 5:
            break
    recommendations = [
        {
            "action": f"Reorder or transfer stock for: {', '.join(seen_names[:3])}",
            "owner": "Inventory team",
            "priority": "high",
            "expected_benefit": f"Restores availability for {min(3, len(seen_names))} of the "
            f"{distinct_products} currently out-of-stock, actively-selling products.",
        }
    ]
    if total_low:
        recommendations.append(
            {
                "action": f"Cycle-count and replenish {total_low} product(s) below the "
                f"{alerts['low_stock_threshold_days']}-day stock cover threshold before they stock out",
                "owner": "Inventory team",
                "priority": "medium",
                "expected_benefit": "Prevents these from becoming tomorrow's stock-outs.",
            }
        )

    breakdowns = [
        {
            "dimension": "store",
            "label": "Store",
            "mode": "contribution",
            "baseline_month": None,
            "total": total_out,
            "top_contributors": by_outlet,
        }
    ]

    evidence = evidence_engine.build_snapshot_evidence(breakdowns, alerts["as_of_date"])
    priority = "critical" if total_out >= 10 else "high"
    impact = {
        "kind": "products_out_of_stock",
        "financial_impact_rs": None,
        "count": total_out,
        "distinct_products": distinct_products,
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": alerts["as_of_date"],
        "detail": f"{distinct_products} actively-selling products are out of stock somewhere, totalling {total_out} product-store stockouts across all outlets.",
    }
    return {
        "id": f"stock_availability-{alerts['as_of_date']}",
        "metric_id": "stock_availability",
        "domain": "inventory",
        "title": f"{distinct_products} product(s) out of stock at one or more stores ({total_out} product-store stockouts)",
        "category": "critical",
        "severity": "high",
        "priority": priority,
        "business_impact_score": impact_engine.business_impact_score(priority, None, impact, evidence["data_quality"]),
        "detected": date.today().isoformat(),
        "confirm": {
            "period": alerts["as_of_date"],
            "current": total_out,
            "baseline": None,
            "pct_change": None,
            "unit": "count",
        },
        "breakdowns": breakdowns,
        "impact": impact,
        "how": None,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]},
        "data_quality": evidence["data_quality"],
        "recommendations": recommendations,
        "status": "completed",
    }


def _dead_overstock_finding():
    rows = reporting.run_query(inventory_service.INVENTORY_DEPTH_SQL)
    if not rows:
        measurement.source_unavailable("No inventory depth records.")
        return None
    as_of_date = rows[0]["as_of"]
    measurement.source_as_of(as_of_date, verifies_source=False)

    segment_value = {"dead_stock": 0.0, "overstock": 0.0, "healthy": 0.0}
    dead_stock_candidates = []
    overstock_candidates = []

    for r in rows:
        total_qty = float(r["total_qty"] or 0)
        sold_30d = float(r["sold_30d"] or 0)
        days_of_stock = (total_qty / (sold_30d / 30.0)) if sold_30d > 0 else None
        segment = inventory_service._classify(sold_30d, days_of_stock)
        usable_cost = inventory_service._usable_cost(r["cost"], r["list_price"])
        estimated_value = total_qty * usable_cost if usable_cost is not None else None
        if estimated_value is not None:
            segment_value[segment] += estimated_value

        if estimated_value:
            item = {"name": r["product_name"] or "Unknown", "current": round(estimated_value, 2),
                     "delta": round(estimated_value, 2)}
            if segment == "dead_stock":
                dead_stock_candidates.append(item)
            elif segment == "overstock":
                overstock_candidates.append(item)

    total_value = sum(segment_value.values())
    if not total_value:
        measurement.source_unavailable("Inventory cannot be valued.")
        return None

    dead_share = segment_value["dead_stock"] / total_value * 100
    overstock_share = segment_value["overstock"] / total_value * 100

    dead_flagged = dead_share > DEAD_STOCK_VALUE_SHARE_THRESHOLD_PCT
    overstock_flagged = overstock_share > OVERSTOCK_VALUE_SHARE_THRESHOLD_PCT
    if not dead_flagged and not overstock_flagged:
        return None

    dead_stock_candidates.sort(key=lambda c: c["current"], reverse=True)
    overstock_candidates.sort(key=lambda c: c["current"], reverse=True)

    titles = []
    recommendations = []
    if dead_flagged:
        titles.append(f"dead stock is {dead_share:.1f}% of inventory value")
        top = dead_stock_candidates[:3]
        recommendations.append(
            {
                "action": f"Liquidate or discount slow movers: {', '.join(i['name'] for i in top)}"
                if top else "Review dead-stock SKUs for liquidation or discounting",
                "owner": "Merchandising team",
                "priority": "medium",
                "expected_benefit": f"Frees up roughly Rs. {segment_value['dead_stock']:,.0f} in tied-up inventory value.",
            }
        )
    if overstock_flagged:
        titles.append(f"overstock is {overstock_share:.1f}% of inventory value")
        top = overstock_candidates[:3]
        recommendations.append(
            {
                "action": f"Pause reordering and review demand forecasts for: {', '.join(i['name'] for i in top)}"
                if top else "Pause reordering for overstocked SKUs and review demand forecasts",
                "owner": "Inventory team",
                "priority": "medium",
                "expected_benefit": f"Reduces cash tied up (currently ~Rs. {segment_value['overstock']:,.0f}) as stock sells through.",
            }
        )

    worst_share = max(dead_share if dead_flagged else 0, overstock_share if overstock_flagged else 0)
    worst_threshold = DEAD_STOCK_VALUE_SHARE_THRESHOLD_PCT if dead_share >= overstock_share else OVERSTOCK_VALUE_SHARE_THRESHOLD_PCT

    breakdowns = [
        b
        for b in [
            {"dimension": "product", "label": "Dead stock by product", "mode": "contribution",
             "baseline_month": None, "total": round(segment_value["dead_stock"], 2),
             "top_contributors": dead_stock_candidates[:5]}
            if dead_flagged else None,
            {"dimension": "product", "label": "Overstock by product", "mode": "contribution",
             "baseline_month": None, "total": round(segment_value["overstock"], 2),
             "top_contributors": overstock_candidates[:5]}
            if overstock_flagged else None,
        ]
        if b is not None
    ]

    evidence = evidence_engine.build_snapshot_evidence(breakdowns, as_of_date) if breakdowns else None
    priority = _priority_for_share(worst_share, worst_threshold)
    at_risk_rs = segment_value["dead_stock"] + segment_value["overstock"]
    impact = {
        "kind": "inventory_value_at_risk",
        "financial_impact_rs": round(at_risk_rs, 2),
        "count": None,
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": as_of_date.date().isoformat(),
        "detail": f"Rs. {at_risk_rs:,.0f} tied up in dead and overstocked inventory.",
    }
    return {
        "id": f"dead_overstock_value-{as_of_date.date().isoformat()}",
        "metric_id": "dead_overstock_value",
        "domain": "inventory",
        "title": " and ".join(titles).capitalize(),
        "category": "critical",
        "severity": "high",
        "priority": priority,
        "business_impact_score": impact_engine.business_impact_score(
            priority, None, impact, evidence["data_quality"] if evidence else None
        ),
        "detected": date.today().isoformat(),
        "confirm": {
            "period": as_of_date.date().isoformat(),
            "current": round(dead_share + overstock_share, 1),
            "baseline": None,
            "pct_change": None,
            "unit": "percent",
        },
        "breakdowns": breakdowns,
        "impact": impact,
        "how": None,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]} if evidence else None,
        "data_quality": evidence["data_quality"] if evidence else None,
        "recommendations": recommendations,
        "status": "completed",
    }


def _stuck_stock_count_finding():
    """A physical stock count (Odoo's cycle-count feature) that was started
    but never finished -- state != 'done', no completed_datetime, open more
    than stock_counts.STUCK_AFTER_HOURS. Discovered live: a manager-reported
    "huge stock variance" at Colombo 6 traced to exactly this -- a 7,724-line
    full-store count where only 30 lines were ever scanned, left open for a
    month. An unfinished session's un-scanned lines still carry the system's
    onhand_qty with counted_qty defaulting to 0, so any variance/shrinkage
    report pulled from it is phantom, not real loss -- see
    backend/app/services/stock_counts.py's module docstring for the full
    investigation this came from.

    Fires whenever ANY session is stuck (no value threshold to clear) --
    same posture as _stock_availability_finding: a stuck count is a real,
    binary process failure (either a session completed or it didn't), not a
    fuzzy magnitude judgment call. Priority still scales with the phantom
    value at risk, so a 3-line forgotten count doesn't read as loudly as a
    full-store one."""
    result = stock_counts.get_stuck_stock_counts()
    # Live query against Odoo; result["as_of"] is utcnow() by construction
    # (stock_counts.get_stuck_stock_counts) -- see _cost_coverage_control_
    # finding's comment on why this is the fix for a case that can never
    # clear otherwise.
    measurement.source_as_of(result["as_of"])
    sessions = result["sessions"]
    if not sessions:
        return None

    by_outlet = {}
    for s in sessions:
        by_outlet[s["outlet"]] = by_outlet.get(s["outlet"], 0) + s["unscanned_value_rs"]
    top_contributors = [{"name": name, "current": round(v, 2), "delta": round(v, 2)} for name, v in by_outlet.items()]
    top_contributors.sort(key=lambda c: c["current"], reverse=True)

    worst = sessions[0]  # already sorted by unscanned_value_rs desc
    plural = len(sessions) != 1
    # Incident Investigation, Phase 6: the prior wording ("then either
    # complete it for a real count, or void it and schedule a recount")
    # still prescribed a specific fix -- this system has no basis to
    # recommend which disposition is correct (that depends on physical
    # reality this app can't observe). Replaced with "review the evidence,
    # then make an authorized disposition decision" -- doesn't presume
    # complete vs. void vs. something else. Activity clause is honest about
    # what's actually on record: some sessions have real scan activity
    # (worst['has_scan_activity']), most of the current real 8 don't.
    activity_clause = (
        f"last scan activity {worst['last_scan_at'][:10] if worst['last_scan_at'] else 'unknown'}"
        if worst["has_scan_activity"]
        else "no counting activity has been recorded"
    )
    notes_clause = f" (system note: {worst['notes']})" if worst.get("notes") else ""
    recommendations = [
        {
            "action": f"Review count session {worst['name']} at {worst['outlet']} — recorded owner "
            f"{worst['responsible']}, open {worst['days_stuck']:.0f} day(s), {worst['unscanned_lines']} of "
            f"{worst['total_lines']} lines never scanned, {activity_clause}{notes_clause} — then make an "
            f"authorized disposition decision based on the evidence",
            "owner": "Inventory team",
            "priority": "high",
            "expected_benefit": f"Establishes whether the recorded Rs. {worst['unscanned_value_rs']:,.0f} "
            f"reflects real inventory or is an artifact of the incomplete count, before it's treated as "
            f"either one.",
        }
    ]
    if len(sessions) > 1:
        recommendations.append(
            {
                "action": f"{len(sessions) - 1} more stock count(s) are stuck the same way — review count "
                f"completion as a recurring process gap, not a one-off",
                "owner": "Operations team",
                "priority": "medium",
                "expected_benefit": "Prevents future counts from silently going unfinished the same way.",
            }
        )

    breakdowns = [
        {
            "dimension": "store",
            "label": "Estimated value of unscanned lines by outlet",
            "mode": "contribution",
            "baseline_month": None,
            "total": round(result["total_value_at_risk_rs"], 2),
            "top_contributors": top_contributors,
        },
    ]
    top_products = result.get("top_products") or []
    if top_products:
        # Real product identity (added alongside the session-ranking chart
        # fix): fills what was previously a dead-end "product list is not
        # stored on the case" message under "What is driving it" -- product
        # rides along on the same per-line query stuck_stock_count already
        # runs, so this is free, not a new query.
        breakdowns.append({
            "dimension": "product",
            "label": "Estimated value of unscanned lines by product",
            "mode": "contribution",
            "baseline_month": None,
            "total": round(sum(p["value_rs"] for p in top_products), 2),
            "top_contributors": [{"name": p["name"], "current": p["value_rs"], "delta": p["value_rs"]} for p in top_products],
        })
    evidence = evidence_engine.build_snapshot_evidence(breakdowns, result["as_of"][:10])
    total_value = result["total_value_at_risk_rs"]
    priority = (
        "critical" if total_value >= STUCK_COUNT_CRITICAL_VALUE_RS
        else "high" if total_value >= STUCK_COUNT_HIGH_VALUE_RS
        else "medium"
    )
    # Final Stock-Count Financial Disclosure, items 1-2: one consistent
    # label ("estimated value of unscanned stock-count lines" -- never
    # "phantom variance," never implying confirmed loss or a fully valued
    # total) plus the coverage counts right beside the figure, not buried.
    # Deliberately states the excluded-line count and "value unknown" --
    # never characterizes it as small, since there's no basis to assume
    # that (see stock_counts.get_stuck_stock_counts's valuation_coverage
    # docstring for the exact tally this is built from).
    coverage = result["valuation_coverage"]
    valued_lines = coverage["source_cost_lines"] + coverage["catalog_estimate_lines"]
    impact = {
        "kind": "stuck_stock_count_value_at_risk",
        "financial_impact_rs": round(total_value, 2),
        "count": len(sessions),
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": result["as_of"][:10],
        "valuation_coverage": coverage,
        "detail": f"Rs. {total_value:,.0f} estimated value of unscanned lines across {len(sessions)} unfinished "
        f"stock count{'s' if plural else ''} -- valued {valued_lines} of {coverage['total_unscanned_lines']} "
        f"unscanned lines; {coverage['no_cost_data_lines']} have no recorded cost and are excluded "
        f"(their value is unknown).",
    }
    return {
        "id": f"stuck_stock_count-{result['as_of'][:10]}",
        "metric_id": "stuck_stock_count",
        "domain": "inventory",
        "title": f"{len(sessions)} stock count{'s' if plural else ''} started but never finished"
        + (f" -- Rs. {total_value:,.0f} estimated value of unscanned lines" if total_value else ""),
        "category": "critical",
        "severity": "high",
        "priority": priority,
        "business_impact_score": impact_engine.business_impact_score(priority, None, impact, evidence["data_quality"]),
        "detected": date.today().isoformat(),
        "confirm": {
            "period": result["as_of"][:10],
            "current": len(sessions),
            "baseline": None,
            "pct_change": None,
            "unit": "count",
        },
        "breakdowns": breakdowns,
        "impact": impact,
        "how": None,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]},
        "data_quality": evidence["data_quality"],
        "recommendations": recommendations,
        # Visual Investigation V2, Phase 2 (affected-session table): every
        # stuck session's own real record -- already fully computed by
        # get_stuck_stock_counts() above, just not previously carried past
        # this function. Not a new query, not scraped from recommendation
        # text -- the same `sessions` list already used for breakdowns/
        # recommendations, passed through whole so the frontend can render
        # a real per-session table instead of only the "worst" one's prose.
        "sessions": sessions,
        "status": "completed",
    }


def build_completed_stock_count_variance_finding(result: dict, metric_id: str):
    """Completed Stock-Count Variance & Shrinkage Investigation: builds a
    finding dict from an already-computed stock_counts.
    get_completed_stock_count_variance() result. Public (no leading
    underscore) and metric_id-parameterized on purpose -- shared by two
    callers: _completed_stock_count_variance_finding() below (the daily
    scan, fixed metric_id, gated by a threshold) and the Ask Celeste
    "save as case" route in main.py (an ad-hoc investigation scope, e.g.
    one store/period, always saved regardless of size since the manager
    already decided it's worth tracking -- see save_ad_hoc_case's own
    docstring on why metric_id must be scope-specific there). Returns None
    only when there's nothing to build a finding from at all (no completed
    sessions in scope) -- never a threshold decision, that's the caller's
    job.

    A physical stock count that actually FINISHED (state='done') carrying
    a real, physically-verified discrepancy -- distinct from
    _stuck_stock_count_finding above, which is about counts that never
    finished at all and only ever produces a phantom estimate. Discovered
    live: a single completed session at Colombo 6 (2026-09-08) carried
    Rs 7.1M gross variance, 44% of the entire trailing-3-month total across
    all outlets, invisible to every existing detector because none of them
    look at completed counts."""
    if not result["sessions"]:
        return None
    worst = result["sessions"][0]  # already sorted by gross_abs_variance_value_rs desc

    evidence = evidence_engine.build_snapshot_evidence(result["breakdowns"], date.today().isoformat())
    checks = list(evidence["checks"])
    checks.append(evidence_engine.shrinkage_loss_damage_check(
        result["breakdowns"], result["start_date"], result["end_date"],
    ))
    evidence = {"checks": checks, "summary": evidence_engine.summarize(checks), "data_quality": evidence["data_quality"]}

    product_type_breakdown = next((b for b in result["breakdowns"] if b["dimension"] == "product_type"), None)
    top_product_type = product_type_breakdown["top_contributors"][0]["name"] if product_type_breakdown and product_type_breakdown["top_contributors"] else None
    product_type_clause = (
        f" -- most of it in {top_product_type.replace('_', ' ').lower()} lines" if top_product_type else ""
    )

    plural = result["session_count"] != 1
    priority = (
        "critical" if worst["gross_abs_variance_value_rs"] >= COMPLETED_VARIANCE_CRITICAL_SESSION_RS
        else "high" if worst["gross_abs_variance_value_rs"] >= COMPLETED_VARIANCE_HIGH_SESSION_RS
        else "medium"
    )
    impact = {
        "kind": "completed_stock_count_variance",
        "financial_impact_rs": round(result["gross_abs_variance_value_rs"], 2),
        "count": result["session_count"],
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": date.today().isoformat(),
        "detail": f"Rs. {result['gross_abs_variance_value_rs']:,.0f} of variance across "
        f"{result['session_count']} completed stock count{'s' if plural else ''} "
        f"({result['period']}){product_type_clause}.",
    }
    recommendations = [
        {
            "action": f"Review count session {worst['name']} at {worst['outlet']} — Rs. "
            f"{worst['gross_abs_variance_value_rs']:,.0f} in confirmed variance across "
            f"{worst['lines']} lines, completed {worst['completed_at'][:10] if worst['completed_at'] else 'recently'} "
            f"— then investigate whether the affected product type suggests a tracking/UOM gap "
            f"(e.g. packing material) or real shrinkage (e.g. high-value inventory)",
            "owner": "Inventory team",
            "priority": priority,
            "expected_benefit": "Determines whether this specific count's variance reflects a real "
            "loss, a data-tracking gap, or a receiving-timing artifact, using the actual product "
            "and outlet breakdown rather than treating all variance as one undifferentiated problem.",
        }
    ]

    return {
        "id": f"{metric_id}-{date.today().isoformat()}",
        "metric_id": metric_id,
        "domain": "inventory",
        "title": f"Completed stock count{'s' if plural else ''} showed Rs. "
        f"{result['gross_abs_variance_value_rs']:,.0f} in variance ({result['period']})",
        "category": "critical",
        "severity": "high",
        "priority": priority,
        "business_impact_score": impact_engine.business_impact_score(priority, None, impact, evidence["data_quality"]),
        "detected": date.today().isoformat(),
        "confirm": {
            "period": result["period"],
            "current": result["session_count"],
            "baseline": None,
            "pct_change": None,
            "unit": "count",
        },
        "breakdowns": result["breakdowns"],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]},
        "data_quality": evidence["data_quality"],
        "recommendations": recommendations,
        # Same pattern as _stuck_stock_count_finding's "sessions" field --
        # detail-only, carried through so the case-detail page can render
        # a real per-session table instead of only the worst one's prose.
        "sessions": result["sessions"],
        "status": "completed",
    }


def _completed_stock_count_variance_finding():
    """Daily-scan trigger: fires only when the WORST single session in the
    trailing window clears COMPLETED_VARIANCE_HIGH_SESSION_RS -- most
    completed counts land exactly right or close to it (verified live: 44
    of 53 sessions in a real 30-day sample were under Rs 100k), so this
    isn't a "some variance exists" trigger, only a "something unusually
    large just happened" one. See build_completed_stock_count_variance_
    finding for the finding itself, shared with the ad-hoc save path."""
    result = stock_counts.get_completed_stock_count_variance(period=f"{COMPLETED_VARIANCE_WINDOW_DAYS}d")
    # A live query against Odoo, run right now -- "today" is the correct
    # watermark for THIS freshness question ("did we just read current data,"
    # the same thing _stuck_stock_count_finding/_negative_stock_control_
    # finding/_cost_coverage_control_finding mark fresh). result["end_date"]
    # is deliberately NOT used here: it's the trailing window's end, anchored
    # to the latest SALES order (borrowed from sales.py's shared period-bounds
    # helper for window sizing) -- a stock count can complete on any day
    # regardless of whether orders are flowing, so sales recency doesn't
    # establish whether this specific query itself is current.
    measurement.source_as_of(date.today().isoformat())
    if not result["sessions"] or result["sessions"][0]["gross_abs_variance_value_rs"] < COMPLETED_VARIANCE_HIGH_SESSION_RS:
        return None
    return build_completed_stock_count_variance_finding(result, "completed_stock_count_variance")


def _damage_writeoff_finding():
    """Confirmed damage write-offs (Odoo's damaged_order document -- scrap/
    wastage records, distinct from the "product damage" shortage REASON on
    warehouse transfers that transfer_accountability.py already tracks as
    its own case) over the trailing window. Always fires when there's any
    confirmed write-off at all -- checked live: there always is, every
    single 14-day window across 6 months of history cleared Rs 73k, so
    gating this behind a "did something unusual happen" threshold the way
    completed_stock_count_variance does would mean it never fires despite
    being one of the largest standing inventory-loss signals in the whole
    app (see DAMAGE_WRITEOFF_* constants above for the real distribution
    behind the priority tiers)."""
    result = loss_and_damage.get_damage_writeoff(window_days=DAMAGE_WRITEOFF_WINDOW_DAYS)
    # Live query against Odoo, run right now -- same basis as the other
    # three snapshot detectors above.
    measurement.source_as_of(result["as_of"])
    if not result["line_count"]:
        return None

    priority = (
        "critical" if result["total_value_rs"] >= DAMAGE_WRITEOFF_CRITICAL_VALUE_RS
        else "high" if result["total_value_rs"] >= DAMAGE_WRITEOFF_HIGH_VALUE_RS
        else "medium"
    )
    breakdowns = [{
        "dimension": "store", "label": "Damage write-off value by outlet",
        "mode": "contribution", "baseline_month": None,
        "total": result["total_value_rs"],
        "top_contributors": [{"name": o["outlet"], "current": o["value_rs"], "delta": o["value_rs"]} for o in result["by_outlet"]],
    }, {
        "dimension": "product", "label": "Damage write-off value by product",
        "mode": "contribution", "baseline_month": None,
        "total": result["total_value_rs"],
        "top_contributors": [{"name": p["name"], "current": p["value_rs"], "delta": p["value_rs"]} for p in result["top_products"]],
    }]
    evidence = evidence_engine.build_snapshot_evidence(breakdowns, result["as_of"])

    coverage = result["valuation_coverage"]
    no_cost = coverage["no_cost_data_lines"]
    coverage_clause = (
        f" {coverage['catalog_estimate_lines']} of {coverage['total_lines']} lines have a usable catalog cost, "
        f"so this total is a lower bound."
        if no_cost else ""
    )
    impact = {
        "kind": "damage_writeoff",
        "financial_impact_rs": result["total_value_rs"],
        "count": result["order_count"],
        "valuation_coverage": coverage,
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": result["as_of"],
        "detail": f"Rs. {result['total_value_rs']:,.0f} of confirmed damage write-offs across "
        f"{result['order_count']} order(s), {result['total_qty']:,.0f} units, in the last "
        f"{result['window_days']} days.{coverage_clause}",
    }
    plural = result["order_count"] != 1
    worst_outlet = result["by_outlet"][0] if result["by_outlet"] else None
    recommendations = [{
        "action": f"Review damage write-offs at {worst_outlet['outlet']} — Rs. {worst_outlet['value_rs']:,.0f} "
        f"across {worst_outlet['orders']} order{'s' if worst_outlet['orders'] != 1 else ''} in the last "
        f"{result['window_days']} days, the largest of any outlet — then establish whether this reflects "
        f"real spoilage/breakage or a process gap (e.g. over-liberal write-off habits)"
        if worst_outlet else "Review confirmed damage write-offs for the trailing window",
        "owner": "Inventory team",
        "priority": priority,
        "expected_benefit": "Determines whether the recorded write-off value reflects real, unavoidable loss "
        "(spoilage, breakage in transit) or a controllable process gap, using the actual outlet and product "
        "breakdown rather than treating all write-offs as one undifferentiated cost.",
    }]

    return {
        "id": f"damage_writeoff-{result['as_of']}",
        "metric_id": "damage_writeoff",
        "domain": "inventory",
        "title": f"Rs. {result['total_value_rs']:,.0f} in confirmed damage write-offs — "
        f"{result['order_count']} order{'s' if plural else ''}, last {result['window_days']} days",
        "category": "critical",
        "severity": "high",
        "priority": priority,
        "business_impact_score": impact_engine.business_impact_score(priority, None, impact, evidence["data_quality"]),
        "detected": result["as_of"],
        "confirm": {"period": result["as_of"], "current": result["order_count"], "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": breakdowns,
        "impact": impact,
        "how": None,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]},
        "data_quality": evidence["data_quality"],
        "recommendations": recommendations,
        # Detail-only, same pattern as sessions/products on the sibling
        # stock-count findings -- the case-detail page can render a real
        # per-outlet/per-product table instead of only the breakdown.
        "by_outlet": result["by_outlet"],
        "top_products": result["top_products"],
        "status": "completed",
    }


def build_inventory_findings():
    """Runs all inventory state checks and returns whichever findings
    actually breached their threshold — 0 to 3 results. Never fabricates
    a finding when the underlying data is empty (each helper returns None in
    that case) — same "don't fabricate" posture as the trend engine."""
    return measurement.combine([
        measurement.run_snapshot("stock_availability", _stock_availability_finding),
        measurement.run_snapshot("dead_overstock_value", _dead_overstock_finding),
        measurement.run_snapshot("stuck_stock_count", _stuck_stock_count_finding),
        measurement.run_snapshot("completed_stock_count_variance", _completed_stock_count_variance_finding),
        measurement.run_snapshot("damage_writeoff", _damage_writeoff_finding),
    ])


# ---------------------------------------------------------------------------
# Business Data Controls, Wave 1 Step 1: the raw negative-stock and
# uncosted-product signals already existed as CAVEATS embedded inside other
# findings (top_seller_stock's negative_stock flag; stock_counts.py's
# valuation-basis classification) -- never their own company-wide detector.
# These two promote them to first-class findings using the exact same
# underlying data, not new methodology.

def _negative_stock_control_finding():
    """Company-wide: every real, active INVENTORY product currently
    recorded below zero on-hand. Fires whenever count > 0 -- same binary
    process-failure posture as _stuck_stock_count_finding (a product either
    has non-negative recorded stock or it doesn't, not a fuzzy magnitude
    call), with priority scaling by how widespread it is."""
    result = data_controls.get_negative_stock()
    # Live query against Odoo; result["as_of_date"] is "today" by construction
    # (data_controls.get_negative_stock) -- see _cost_coverage_control_finding's
    # comment on why this is the fix for a case that can never clear otherwise.
    measurement.source_as_of(result["as_of_date"])
    products = result["products"]
    if not products:
        return None
    total_value = result["total_value_rs"]

    priority = (
        "critical" if len(products) >= NEGATIVE_STOCK_CRITICAL_COUNT
        else "high" if len(products) >= NEGATIVE_STOCK_HIGH_COUNT
        else "medium"
    )
    plural = len(products) != 1
    by_outlet = result["by_outlet"]
    breakdowns = [
        {
            "dimension": "product", "label": "Products with negative recorded stock",
            "mode": "contribution", "baseline_month": None,
            "total": round(total_value, 2),
            "top_contributors": [
                {"name": p["name"], "current": p["value_rs"] or 0, "delta": p["value_rs"] or 0} for p in products[:15]
            ],
        },
    ]
    if by_outlet:
        # Store-level subtotal, not derived from the company-wide product
        # list -- see NEGATIVE_STOCK_BY_OUTLET_SQL's own docstring. Fed
        # into evidence_engine too, so an outlet-concentration check runs
        # for real instead of the panel only ever showing raw counts.
        breakdowns.append({
            "dimension": "store", "label": "Affected products by outlet",
            "mode": "contribution", "baseline_month": None,
            "total": sum(o["product_count"] for o in by_outlet),
            "top_contributors": [{"name": o["outlet"], "current": o["product_count"], "delta": o["product_count"]} for o in by_outlet],
        })
    evidence = evidence_engine.build_snapshot_evidence(breakdowns, date.today().isoformat())
    impact = {
        "kind": "negative_stock_control",
        "financial_impact_rs": round(total_value, 2) if total_value else None,
        "count": len(products),
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": date.today().isoformat(),
        # Deliberately does not say "loss" or "shrinkage" -- negative
        # recorded stock is a data-integrity condition (sold past what the
        # system thought was there); the cause (a late goods receipt, a
        # sync lag, a genuine unrecorded loss) has not been established.
        "detail": f"{len(products)} active product{'s' if plural else ''} currently show negative recorded "
        f"stock company-wide -- a data-integrity condition, not confirmed loss.",
    }
    return {
        "id": f"negative_stock_control-{date.today().isoformat()}",
        "metric_id": "negative_stock_control",
        "domain": "controls",
        "title": f"{len(products)} product{'s' if plural else ''} recorded at negative stock company-wide",
        "category": "critical",
        "severity": "high",
        "priority": priority,
        "business_impact_score": impact_engine.business_impact_score(priority, None, impact, evidence["data_quality"]),
        "detected": date.today().isoformat(),
        "confirm": {"period": date.today().isoformat(), "current": len(products), "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": breakdowns,
        "impact": impact,
        "how": None,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]},
        "data_quality": evidence["data_quality"],
        "recommendations": [
            {
                "action": f"Reconcile recorded stock for the {min(len(products), 15)} highest-value negative-stock "
                f"products — negative on-hand quantity requires investigation, not an assumed cause",
                "owner": "Inventory team",
                "priority": priority,
                "expected_benefit": "Determines the actual cause (a physical count, an unrecorded receipt, a "
                "POS/sync timing issue, or a genuine loss) for each affected product by reconciling physical "
                "stock, recorded inventory, and relevant transactions.",
            }
        ],
        # Detail-only, same pattern as sessions on the stock-count findings
        # -- the frontend can render a real per-product table.
        "products": products,
        "by_outlet": by_outlet,
        "status": "completed",
    }


def _cost_coverage_control_finding():
    """Company-wide catalog cost-coverage: how many active INVENTORY
    products have no usable cost anywhere. Fires only when the gap is
    real (count > 0) -- verified live that coverage is otherwise near-
    complete (7,065 of 7,218 have a direct cost, 146 more a usable
    catalog fallback), so the wording must never imply this affects
    products outside the specific uncosted list."""
    result = data_controls.get_cost_coverage()
    total = result["total_products"]
    no_cost = result["no_cost_at_all"]
    if not total:
        measurement.source_unavailable("No inventory catalog coverage.")
    else:
        # A live query against Odoo, not a cached snapshot -- result["as_of_date"]
        # is "today" by construction (see data_controls.get_cost_coverage), so a
        # successful query genuinely IS fresh, the same basis _outlet_activity_
        # finding already uses above. Without this, a cycle where the gap clears
        # can never be verified: run_snapshot() leaves freshness "unknown" by
        # default, which measurement.healthy() never accepts, so the case that
        # opened for this metric would stay in verification_pending forever,
        # showing its last-fired snapshot no matter how many clean days follow
        # (caught live on completed_stock_count_variance's own case -- 6+ days
        # stuck on "Source freshness is not established" purely because this
        # sibling function class never told the framework its data was current).
        measurement.source_as_of(result["as_of_date"])
    if not total or not no_cost:
        return None
    coverage_pct = result["coverage_pct"]
    products = result["uncosted_products"]
    plural = no_cost != 1
    priority = "high" if no_cost >= NEGATIVE_STOCK_HIGH_COUNT else "medium"

    breakdowns = [{
        "dimension": "product", "label": "Products with no recorded cost",
        "mode": "contribution", "baseline_month": None,
        "total": no_cost,
        "top_contributors": [{"name": p["name"] or "Unknown", "current": 1, "delta": 1} for p in products[:15]],
    }]
    evidence = evidence_engine.build_snapshot_evidence(breakdowns, date.today().isoformat())
    impact = {
        "kind": "cost_coverage_control",
        "financial_impact_rs": None,  # a count of affected products, not a priced Rs impact
        "count": no_cost,
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": date.today().isoformat(),
        # Explicit, deliberate scoping: says what's incomplete and, just as
        # deliberately, says what ISN'T affected -- the whole point of this
        # finding is that coverage is otherwise near-complete.
        "detail": f"{no_cost} of {total} active inventory products ({coverage_pct}% overall coverage) have no "
        f"recorded cost anywhere in the catalog -- any margin or inventory-value calculation involving "
        f"specifically these {no_cost} product{'s' if plural else ''} is incomplete. This does not affect the "
        f"other {total - no_cost} products' valuations.",
    }
    return {
        "id": f"cost_coverage_control-{date.today().isoformat()}",
        "metric_id": "cost_coverage_control",
        "domain": "controls",
        "title": f"{no_cost} inventory product{'s' if plural else ''} have no recorded cost anywhere",
        "category": "critical",
        "severity": "medium",
        "priority": priority,
        "business_impact_score": impact_engine.business_impact_score(priority, None, impact, evidence["data_quality"]),
        "detected": date.today().isoformat(),
        "confirm": {"period": date.today().isoformat(), "current": no_cost, "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": breakdowns,
        "impact": impact,
        "how": None,
        "root_cause": {"checks": evidence["checks"], "summary": evidence["summary"]},
        "data_quality": evidence["data_quality"],
        "recommendations": [
            {
                # Deliberately doesn't embed the product names -- with 7+
                # affected products the sentence became an unreadable list;
                # the affected-products table already shows every one of
                # them, so the recommendation only needs to name the count
                # and point at it.
                # "usable catalog list price" previously implied selling/
                # list price is interchangeable with cost -- get_cost_
                # coverage()'s own "catalog_fallback_only" is the real
                # concept meant here (a catalog cost field this business
                # has already approved as a stand-in when no direct cost
                # is recorded), so name it as that, not as "list price".
                "action": f"Review and add a recorded cost (or an approved catalog fallback cost) for the {no_cost} "
                f"affected product{'s' if plural else ''} listed below.",
                "owner": "Inventory team",
                "priority": priority,
                "expected_benefit": f"Closes the last gap in cost coverage ({coverage_pct}% today) so margin "
                f"and inventory-value figures involving these specific products stop excluding them.",
            }
        ],
        "products": [{"name": p["name"], "sku": p["sku"]} for p in products],
        # Structured, not just baked into impact.detail's prose -- so a
        # panel reads real numbers instead of parsing the sentence.
        "total_products": total,
        "coverage_pct": coverage_pct,
        "status": "completed",
    }


def build_controls_findings():
    """Business Data Controls, Wave 1 Step 1 -- a new, first-class domain
    alongside inventory/operational, for findings about the RELIABILITY of
    the data other findings depend on, not a business outcome itself."""
    return measurement.combine([
        measurement.run_snapshot("negative_stock_control", _negative_stock_control_finding),
        measurement.run_snapshot("cost_coverage_control", _cost_coverage_control_finding),
    ])


# ---------------------------------------------------------------------------
# Operational findings, built from the same real signals already computed
# for the Customer Next-Order Intelligence page (backend/app/services/
# stock_alerts.py get_top_product_stock_coverage(), and a direct outlet
# order-volume check) — those signals only ever showed up if someone opened
# that page. This surfaces them on the Business Briefing itself, same
# current-state shape as build_inventory_findings above (no trend/baseline,
# not fed into tracking.py's outcome loop for the same reason).


def _top_seller_stock_finding():
    """Distinct from _stock_availability_finding above on purpose: that one
    reports EVERY actively-selling out-of-stock product (300+ at a single
    outlet on a normal day — too big a number to act on directly). This
    narrows to just the 15 best-selling products company-wide.

    Fires only on a genuine stock-OUT among the top 15 (current_stock <= 0),
    not on get_top_product_stock_coverage()'s broader "short" flag (under
    LOW_STOCK_DAYS) — checked live before picking this: on a normal day, 12-
    13 of the top 15 already read as "short" under that flag, because most
    of them are fresh produce/dairy this business deliberately keeps thin
    and restocks daily. Firing "critical" on that count would mean this
    finding never stops being critical — noise, not news. An actual zero
    (or negative, a data-integrity problem on top of the stockout) is the
    real, comparatively rare signal; the rest of the "short" list is kept
    as supporting context on the finding, not what triggers it."""
    coverage = get_top_product_stock_coverage()
    measurement.source_as_of(coverage.get("as_of_date"), verifies_source=False)
    if not coverage.get("products"):
        measurement.source_unavailable("No top-product coverage.")
    if not coverage.get("as_of_date"):
        return None

    out_of_stock = [p for p in coverage["products"] if p["current_stock"] <= 0]
    if not out_of_stock:
        return None

    # The real count of top sellers this scan actually considered -- was
    # previously hardcoded as the literal "15" in the title/detail/
    # recommendation text below (TOP_PRODUCTS_TOP_N's usual value, but not
    # guaranteed if fewer than 15 products have recent sales). Deriving it
    # from the real list makes the copy match what was actually measured.
    total_top_sellers = len(coverage["products"])

    negative = [p for p in out_of_stock if p["negative_stock"]]
    other_short = [p for p in coverage["products"] if p["short"] and p["current_stock"] > 0]

    # sku/negative_stock/days_of_cover/qty_sold_30d already exist on each
    # product dict from get_top_product_stock_coverage() -- carried through
    # here rather than dropped, so the case-detail investigation panel has
    # a real SKU and stock-status flag per product instead of parsing them
    # back out of the "name" string or recommendation prose.
    top_contributors = [
        {
            "name": p["name"],
            "sku": p["sku"],
            "current": p["current_stock"],
            "delta": p["current_stock"],
            "negative_stock": p["negative_stock"],
            "qty_sold_30d": p["qty_sold_30d"],
        }
        for p in out_of_stock
    ]
    breakdowns = [
        {
            "dimension": "product",
            "label": "Current stock (units)",
            "mode": "contribution",
            "baseline_month": None,
            "top_contributors": top_contributors,
        }
    ]
    if other_short:
        breakdowns.append(
            {
                "dimension": "product",
                "label": f"Other top sellers under {coverage.get('low_stock_threshold_days', 3)} days of cover",
                "mode": "contribution",
                "baseline_month": None,
                "top_contributors": [
                    {
                        "name": p["name"],
                        "sku": p["sku"],
                        "current": p["days_of_cover"],
                        "delta": p["days_of_cover"],
                        "days_of_cover": p["days_of_cover"],
                        "qty_sold_30d": p["qty_sold_30d"],
                    }
                    for p in other_short
                ],
            }
        )
    names = [p["name"] for p in out_of_stock[:3]]

    recommendations = [
        {
            "action": f"Urgently verify and restock top sellers recorded at zero or negative stock: {', '.join(names)}",
            "owner": "Inventory team",
            "priority": "critical",
            "expected_benefit": f"Restores recorded stock for {len(out_of_stock)} of your {total_top_sellers} "
            f"best-selling products by volume — these drive a disproportionate share of daily orders.",
        }
    ]
    if negative:
        # Metric Truthfulness Correction, Task 2: the earlier wording ("Corrects
        # a data-integrity issue (sold past what's recorded on hand)") stated a
        # specific cause as settled fact. A negative recorded quantity is
        # evidence of a stock-record discrepancy, not proof of any one
        # explanation (a missed physical count, an unrecorded transaction, a
        # reservation mismatch, and a genuine oversell are all still open) --
        # matches negativeStockText()'s own already-correct hedging
        # (frontend/src/design/plainLanguage.js), which this recommendation
        # previously contradicted on the same page.
        recommendations.append(
            {
                "action": f"Reconcile recorded stock for {', '.join(p['name'] for p in negative)} — "
                f"negative on-hand quantity requires investigation",
                "owner": "Inventory team",
                "priority": "high",
                "expected_benefit": "Determines the actual cause (e.g. a physical count, an unrecorded "
                "transaction, or a reservation mismatch) by reconciling physical stock, recorded "
                "inventory, relevant transactions, and reservations where data is available -- before "
                "it distorts reorder decisions.",
            }
        )
    if other_short:
        recommendations.append(
            {
                "action": f"{len(other_short)} more top sellers are under {coverage.get('low_stock_threshold_days', 3)} "
                f"days of cover and worth watching, without being out yet",
                "owner": "Inventory team",
                "priority": "medium",
                "expected_benefit": "Gets ahead of these before they become the next stock-out.",
            }
        )

    impact = {
        "kind": "top_sellers_out_of_stock",
        "financial_impact_rs": None,
        "count": len(out_of_stock),
        # The denominator for "N of {total_top_sellers} top-selling products
        # out of stock" -- a real, already-computed field, not parsed back
        # out of the title/detail text. Lives on impact (already a
        # pass-through field on the case API) rather than a new top-level
        # key, so no case_engine.py schema/whitelist change is needed.
        "total_top_sellers": total_top_sellers,
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": coverage["as_of_date"],
        # Metric Truthfulness Correction, Task 1: "completely out of stock" claimed
        # verified unavailability. What's actually measured is recorded on-hand
        # quantity, company-wide across the monitored internal stock locations,
        # summed via get_top_product_stock_coverage() (TOP_PRODUCTS_STOCK_SQL) --
        # it neither confirms per-outlet availability nor subtracts reserved
        # quantity. See InventoryInvestigationPanel.jsx for the fuller scope
        # disclosure shown alongside this on the case page.
        "detail": f"{len(out_of_stock)} of your {total_top_sellers} best-selling products are recorded at zero "
        f"or negative stock across monitored locations, company-wide.",
    }
    return {
        "id": f"top_seller_stock-{coverage['as_of_date']}",
        "metric_id": "top_seller_stock",
        "domain": "inventory",
        "title": f"{len(out_of_stock)} of your top {total_top_sellers} best-selling products are recorded at "
        f"zero or negative stock, company-wide",
        "category": "critical",
        "severity": "high",
        "priority": "critical",
        "business_impact_score": impact_engine.business_impact_score("critical", None, impact, None),
        "detected": date.today().isoformat(),
        "confirm": {
            "period": coverage["as_of_date"],
            "current": len(out_of_stock),
            "baseline": None,
            "pct_change": None,
            "unit": "count",
        },
        "breakdowns": breakdowns,
        "impact": impact,
        "how": None,
        # Not run through _current_total_concentration_checks on purpose: the
        # "product" breakdown here already IS the complete list of the
        # out-of-stock top sellers by construction — every contributor is
        # already known to be out. A concentration check would report a
        # trivially-true "100%", which isn't evidence of anything.
        "root_cause": None,
        "recommendations": recommendations,
        "status": "completed",
    }


OUTLET_ACTIVITY_SQL = f"""
    WITH bounds AS (
        SELECT MAX(date_order) AS as_of FROM sale_order
        WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
    )
    SELECT rs.id, rs.name, b.as_of,
           COUNT(so.id) FILTER (WHERE so.date_order >= b.as_of - INTERVAL '30 days') AS orders_30d
    FROM res_store rs
    CROSS JOIN bounds b
    LEFT JOIN sale_order so ON so.store_id = rs.id AND so.state IN {SALE_ORDER_LIVE_STATES}
    WHERE rs.id IN {CELESTE_STORE_IDS}
    GROUP BY rs.id, rs.name, b.as_of
"""


def _outlet_activity_finding():
    """Flags any of Celeste's 5 configured outlets with zero orders in the
    trailing 30 days — a real, unambiguous rule (0 is 0, not a fuzzy "much
    lower than the others" judgment call). Verified against live data
    before building this: Ja-Ela currently shows exactly this — 25,838
    orders at Colombo 6 over the same window vs. 0 at Ja-Ela, not a
    borderline case."""
    rows = reporting.run_query(OUTLET_ACTIVITY_SQL)
    if not rows or rows[0]["as_of"] is None:
        measurement.source_unavailable("No outlet activity watermark.")
        return None

    as_of = rows[0]["as_of"]
    measurement.source_as_of(as_of)
    dark = [r for r in rows if int(r["orders_30d"] or 0) == 0]
    if not dark:
        return None

    names = [r["name"] for r in dark]
    plural = len(names) != 1
    impact = {
        "kind": "outlets_dark",
        "financial_impact_rs": None,
        "count": len(names),
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": as_of.date().isoformat(),
        "detail": f"{len(names)} outlet{'s' if plural else ''} {'have' if plural else 'has'} had zero orders "
        f"in the last 30 days.",
    }
    return {
        "id": f"outlet_no_orders-{as_of.date().isoformat()}",
        "metric_id": "outlet_no_orders",
        "domain": "order_customer",
        "title": f"{len(names)} outlet{'s' if plural else ''} {'have' if plural else 'has'} had zero orders "
        f"in the last 30 days",
        "category": "critical",
        "severity": "high",
        "priority": "critical",
        "business_impact_score": impact_engine.business_impact_score("critical", None, impact, None),
        "detected": date.today().isoformat(),
        "confirm": {
            "period": as_of.date().isoformat(),
            "current": len(names),
            "baseline": None,
            "pct_change": None,
            "unit": "count",
        },
        "breakdowns": [
            {
                "dimension": "store",
                "label": "Outlets with no orders",
                "mode": "contribution",
                "baseline_month": None,
                "top_contributors": [{"name": n, "current": 0, "delta": 0} for n in names],
            }
        ],
        "impact": impact,
        "how": None,
        # Not run through _current_total_concentration_checks: every listed
        # store already has a value of 0 (that's the trigger for being
        # listed at all), so there's no meaningful total to concentrate —
        # and the actual cause (closure, listing, system issue) isn't
        # discoverable from this data source; the recommendation below says
        # so directly rather than implying a root cause was checked.
        "root_cause": None,
        "recommendations": [
            {
                "action": f"Confirm whether {', '.join(names)} {'are' if plural else 'is'} actually "
                f"trading — check for a listing, system, or operational issue",
                "owner": "Operations team",
                "priority": "critical",
                "expected_benefit": "Distinguishes a real closure or outage from normal underperformance "
                "before it's mistaken for the latter.",
            }
        ],
        "status": "completed",
    }


def build_operational_findings():
    """Runs the newer operational state checks (top-seller stock coverage,
    outlet activity) — same never-fabricate posture as build_inventory_findings."""
    return measurement.combine([
        measurement.run_snapshot("top_seller_stock", _top_seller_stock_finding),
        measurement.run_snapshot("outlet_no_orders", _outlet_activity_finding),
    ])
