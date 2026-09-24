"""Current-state findings: stock-outs, low-stock, dead stock, and overstock.

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

Phase 5 of the evidence-based root-cause rollout: _stock_availability_finding
and _dead_overstock_finding attach evidence via evidence_engine.py's
build_snapshot_evidence — the same >=60% concentration rule as the trend
findings, adapted for a snapshot's "which segment holds most of the total"
instead of a trend's "which segment drove the move," plus a data-quality
(staleness) check. _top_seller_stock_finding and _outlet_activity_finding
are deliberately left out — see the comment next to each one's
"root_cause": None for why forcing the same check onto those two would
produce a hollow, trivially-true result rather than real evidence.
There is no LLM in this module (all four findings build their recommendations
directly from the evidence, no _generate_recommendations() call), so unlike
the trend metrics this isn't protecting an LLM from inventing a cause — it's
giving the human reader the same "confirmed: X% concentrated in Y" framing
already used elsewhere, instead of a bare, unranked breakdown list.
"""

from datetime import date

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS
from backend.app.services import data_controls
from backend.app.services import inventory as inventory_service
from backend.app.services.stock_alerts import get_stock_alerts, get_top_product_stock_coverage
from mcp_server.tools import evidence_engine, impact_engine

DEAD_STOCK_VALUE_SHARE_THRESHOLD_PCT = 5
OVERSTOCK_VALUE_SHARE_THRESHOLD_PCT = 25

# Business Data Controls, Wave 1 Step 1: tiers set from the real observed
# count (54 real INVENTORY products, company-wide, on 2026-09-22) -- a
# process-control finding like this is primarily about HOW WIDESPREAD the
# condition is, not its Rs value (real total value is only ~Rs 26.5k,
# small relative to other findings), so priority keys off count. Raw data
# comes from backend.app.services.data_controls -- shared unchanged with
# the get_data_controls Ask Celeste tool, never a second copy of the SQL.
NEGATIVE_STOCK_CRITICAL_COUNT = 100
NEGATIVE_STOCK_HIGH_COUNT = 20


def _priority_for_share(share_pct: float, threshold_pct: float) -> str:
    """Same posture as investigation._priority_for: a stated rule, not a
    judgment call. Twice the threshold share is 'critical', otherwise 'high'
    — mirrors the >=40%-off-baseline cutoff used for trend findings."""
    return "critical" if share_pct >= threshold_pct * 2 else "high"


def _stock_availability_finding():
    alerts = get_stock_alerts()
    if not alerts.get("as_of_date"):
        return None

    segments = alerts["segments_by_outlet"]
    total_out = sum(s["out_of_stock"] for s in segments)
    total_low = sum(s["low_stock"] for s in segments)
    if total_out == 0:
        return None  # matches health.py: out-of-stock is the trigger; low-stock is context

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
            f"{total_out} currently out-of-stock, actively-selling products.",
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
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": alerts["as_of_date"],
        "detail": f"{total_out} actively-selling products currently unavailable to sell.",
    }
    return {
        "id": f"stock_availability-{alerts['as_of_date']}",
        "metric_id": "stock_availability",
        "domain": "inventory",
        "title": f"{total_out} actively-selling product(s) are out of stock",
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
        return None
    as_of_date = rows[0]["as_of"]

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


def build_inventory_findings():
    """Runs both inventory state checks and returns whichever findings
    actually breached their threshold — 0, 1, or 2 results. Never fabricates
    a finding when the underlying data is empty (both helpers return None in
    that case) — same "don't fabricate" posture as the trend engine."""
    findings = [_stock_availability_finding(), _dead_overstock_finding()]
    return [f for f in findings if f is not None]


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
    breakdowns = [{
        "dimension": "product", "label": "Products with negative recorded stock",
        "mode": "contribution", "baseline_month": None,
        "total": round(total_value, 2),
        "top_contributors": [
            {"name": p["name"], "current": p["value_rs"] or 0, "delta": p["value_rs"] or 0} for p in products[:15]
        ],
    }]
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
                "action": f"Add a recorded cost (or a usable catalog list price) for the {no_cost} product"
                f"{'s' if plural else ''} listed — {', '.join(p['name'] or 'Unknown' for p in products[:5])}"
                f"{', and others' if len(products) > 5 else ''}",
                "owner": "Inventory team",
                "priority": priority,
                "expected_benefit": f"Closes the last gap in cost coverage ({coverage_pct}% today) so margin "
                f"and inventory-value figures involving these specific products stop excluding them.",
            }
        ],
        "products": [{"name": p["name"], "sku": p["sku"]} for p in products],
        "status": "completed",
    }


def build_controls_findings():
    """Business Data Controls, Wave 1 Step 1 -- a new, first-class domain
    alongside inventory/operational, for findings about the RELIABILITY of
    the data other findings depend on, not a business outcome itself."""
    findings = [_negative_stock_control_finding(), _cost_coverage_control_finding()]
    return [f for f in findings if f is not None]


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
    if not coverage.get("as_of_date"):
        return None

    out_of_stock = [p for p in coverage["products"] if p["current_stock"] <= 0]
    if not out_of_stock:
        return None

    negative = [p for p in out_of_stock if p["negative_stock"]]
    other_short = [p for p in coverage["products"] if p["short"] and p["current_stock"] > 0]

    top_contributors = [
        {"name": p["name"], "current": p["current_stock"], "delta": p["current_stock"]} for p in out_of_stock
    ]
    names = [p["name"] for p in out_of_stock[:3]]

    recommendations = [
        {
            "action": f"Urgently restock top sellers that are completely out: {', '.join(names)}",
            "owner": "Inventory team",
            "priority": "critical",
            "expected_benefit": f"Restores availability for {len(out_of_stock)} of your 15 best-selling "
            f"products by volume — these drive a disproportionate share of daily orders.",
        }
    ]
    if negative:
        recommendations.append(
            {
                "action": f"Investigate recorded stock for {', '.join(p['name'] for p in negative)} — "
                f"showing negative on-hand quantity",
                "owner": "Inventory team",
                "priority": "high",
                "expected_benefit": "Corrects a data-integrity issue (sold past what's recorded on hand) "
                "before it distorts reorder decisions.",
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
        "avg_order_value_used": None,
        "confidence": "measured",
        "month": coverage["as_of_date"],
        "detail": f"{len(out_of_stock)} of your 15 best-selling products are completely out of stock.",
    }
    return {
        "id": f"top_seller_stock-{coverage['as_of_date']}",
        "metric_id": "top_seller_stock",
        "domain": "inventory",
        "title": f"{len(out_of_stock)} of your top 15 best-selling products are completely out of stock",
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
        "breakdowns": [
            {
                "dimension": "product",
                "label": "Current stock (units)",
                "mode": "contribution",
                "baseline_month": None,
                "top_contributors": top_contributors,
            }
        ],
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
        WHERE state = 'sale' AND store_id IN {CELESTE_STORE_IDS}
    )
    SELECT rs.id, rs.name, b.as_of,
           COUNT(so.id) FILTER (WHERE so.date_order >= b.as_of - INTERVAL '30 days') AS orders_30d
    FROM res_store rs
    CROSS JOIN bounds b
    LEFT JOIN sale_order so ON so.store_id = rs.id AND so.state = 'sale'
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
        return None

    as_of = rows[0]["as_of"]
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
    findings = [_top_seller_stock_finding(), _outlet_activity_finding()]
    return [f for f in findings if f is not None]
