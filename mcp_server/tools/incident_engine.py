"""Store x SKU x hour incident detector — a Business Brain sibling to
state_findings.py that catches a recurring, time-of-day-locked
cancellation pattern the rest of the pipeline structurally can't see:
intelligence.py has no hour-of-day dimension anywhere, evidence_engine.py's
stock_availability_check only matches a DECLINING product contribution
(built for revenue/order_volume, not a growing cancellation driver), and
state_findings.py's stock-out detector is a single daily snapshot that
misses a stock-out that clears before the next scan.

IncidentScore(s, k, h) = z_h x Lift(s) x sqrt(n_s,h) x m -- supplied by
the user after walking through a real incident (a store's fresh-milk SKU
stocking out every morning, driving cancellations, resolving by
afternoon, for 2+ weeks) that the existing pipeline couldn't confirm.
Validated directly against the live DB before being built in here: the
Lift term correctly rejected every noisy candidate the raw z_h alone
would have surfaced (packaging bags, eggs -- all Lift < 1, i.e. not
actually over-represented in cancellations).

Emits findings in the exact state_findings.py dict shape (id, metric_id,
domain, title, category, severity, priority, business_impact_score,
confirm, breakdowns, impact, root_cause, data_quality, recommendations,
status) so brain.py can fold this module's output straight into the same
`state` list state_findings.py already produces -- persistence tracking
(state_tracking.py), proactive-warning diffing (notifications.py), and
every frontend renderer already work generically off that shape, with
zero special-casing needed.
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from math import sqrt
from statistics import mean, pstdev

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from mcp_server.tools import (
    evidence_engine,
    impact_engine,
    intelligence,
    investigation,
    measurement,
)
from mcp_server.tools import recommendations as recommendation_schema

logger = logging.getLogger(__name__)

# Anchored to the data's own latest order, never wall-clock -- same
# principle intelligence.get_trailing_trend()'s as_of already uses.
INCIDENT_WINDOW_DAYS = 14
MIN_N = intelligence.MIN_SEGMENT_VOLUME  # 15 -- same volume floor used everywhere else in this app
Z_CONFIRM_THRESHOLD = 2.0
LIFT_CONFIRM_THRESHOLD = 3.0
LIFT_GATE = 1.0  # a candidate below this isn't over-represented in cancellations at all -- not a finding

SCORE_WATCHLIST = 5.0
SCORE_CONFIRMED = 15.0
MAX_LLM_CALLS_PER_SCAN = 10  # bounds recommendation-call cost if many incidents qualify on the same day


# Replayed over Mar-Oct 2026 (28 weekly dates): no score reached 50, so the
# high/critical bands have no historical evidence yet. Every incident is
# medium until a calibrated band is shown to mark a real jump in severity.
def _priority_for_score(score: float) -> str:
    return "medium"


def _as_of():
    rows = reporting.run_query(
        "SELECT MAX(date_order) AS as_of FROM sale_order WHERE store_id = ANY(:stores)",
        {"stores": list(CELESTE_STORE_IDS)},
    )
    return rows[0]["as_of"] if rows else None


def _hourly_rates(as_of, window_start):
    return reporting.run_query(
        f"""
        SELECT so.store_id, pp.id AS product_id, pt.name->>'en_US' AS product_name,
               EXTRACT(hour FROM so.date_order)::int AS hr,
               COUNT(DISTINCT so.id) FILTER (WHERE so.state = 'cancel') AS cancels,
               COUNT(DISTINCT so.id) AS orders
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        JOIN product_product pp ON pp.id = sol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE pt.type = 'consu'
          AND so.store_id = ANY(:stores) AND so.state IN {SALE_ORDER_LIVE_STATES + ("cancel",)}
          AND so.date_order >= :window_start AND so.date_order <= :as_of
        GROUP BY 1, 2, 3, 4
        """,
        {"stores": list(CELESTE_STORE_IDS), "as_of": as_of, "window_start": window_start},
    )


def _basket_lift(as_of, window_start):
    """P(cancel | sku in order) vs P(cancel | sku not in order), per
    (store, product), over the whole window -- no hour subscript, matching
    the formula's Lift(s). Laplace-smooths a zero "without" cancel count
    to a 0.5 floor rather than letting lift come back as literal infinity
    (which json.dumps would emit as a bare `Infinity` token the frontend's
    JSON.parse can't read) -- still produces a very large, honestly
    "this SKU dominates cancellations" lift, just a finite one."""
    per_product = reporting.run_query(
        f"""
        WITH order_products AS (
            SELECT DISTINCT so.id AS order_id, so.store_id, so.state, pp.id AS product_id
            FROM sale_order so
            JOIN sale_order_line sol ON sol.order_id = so.id
            JOIN product_product pp ON pp.id = sol.product_id
            WHERE so.store_id = ANY(:stores) AND so.state IN {SALE_ORDER_LIVE_STATES + ("cancel",)}
              AND so.date_order >= :window_start AND so.date_order <= :as_of
        )
        SELECT store_id, product_id,
               COUNT(*) FILTER (WHERE state = 'cancel') AS with_cancels,
               COUNT(*) AS with_total
        FROM order_products
        GROUP BY 1, 2
        """,
        {"stores": list(CELESTE_STORE_IDS), "as_of": as_of, "window_start": window_start},
    )
    store_totals = reporting.run_query(
        f"""
        SELECT store_id,
               COUNT(*) FILTER (WHERE state = 'cancel') AS cancels,
               COUNT(*) AS total
        FROM sale_order
        WHERE store_id = ANY(:stores) AND state IN {SALE_ORDER_LIVE_STATES + ("cancel",)}
          AND date_order >= :window_start AND date_order <= :as_of
        GROUP BY 1
        """,
        {"stores": list(CELESTE_STORE_IDS), "as_of": as_of, "window_start": window_start},
    )
    store_total_map = {r["store_id"]: r for r in store_totals}

    lift_map = {}
    for r in per_product:
        store_tot = store_total_map.get(r["store_id"])
        if not store_tot or r["with_total"] < MIN_N:
            continue
        without_total = store_tot["total"] - r["with_total"]
        without_cancels = store_tot["cancels"] - r["with_cancels"]
        if without_total <= 0:
            continue
        p_with = r["with_cancels"] / r["with_total"]
        p_without = (0.5 / without_total) if without_cancels == 0 else (without_cancels / without_total)
        lift = (p_with / p_without) if p_without else 0.0
        lift_map[(r["store_id"], r["product_id"])] = {"lift": lift, "with_total": r["with_total"]}
    return lift_map


def _persistence(store_id, product_id, hr, as_of, window_start):
    """m = (days this store/product/hour combo had >=1 cancelled order) /
    (days this store had any order activity at all) x 1.5, capped at 2.
    Only called for candidates that already cleared the volume/z/lift
    bars -- the candidate set is small by then, so one extra query each
    is cheap."""
    rows = reporting.run_query(
        f"""
        SELECT
            COUNT(DISTINCT date_trunc('day', so.date_order)) FILTER (
                WHERE EXTRACT(hour FROM so.date_order)::int = :hr AND so.state = 'cancel'
            ) AS days_appeared,
            COUNT(DISTINCT date_trunc('day', so.date_order)) AS days_observed
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        WHERE so.store_id = :store_id AND sol.product_id = :product_id
          AND so.state IN {SALE_ORDER_LIVE_STATES + ("cancel",)}
          AND so.date_order >= :window_start AND so.date_order <= :as_of
        """,
        {"store_id": store_id, "product_id": product_id, "hr": hr, "as_of": as_of, "window_start": window_start},
    )
    row = rows[0]
    days_observed = row["days_observed"] or 0
    days_appeared = row["days_appeared"] or 0
    if days_observed == 0:
        return 0, 0, 1.0
    m = min(2.0, (days_appeared / days_observed) * 1.5)
    return days_appeared, days_observed, m


def _avg_order_value(store_id, as_of, window_start):
    rows = reporting.run_query(
        f"""
        SELECT SUM(amount_total) AS revenue, COUNT(*) AS orders
        FROM sale_order
        WHERE store_id = :store_id AND state IN {SALE_ORDER_LIVE_STATES}
          AND date_order >= :window_start AND date_order <= :as_of
        """,
        {"store_id": store_id, "as_of": as_of, "window_start": window_start},
    )
    row = rows[0]
    if not row["orders"]:
        return None
    return float(row["revenue"] or 0) / row["orders"]


def _time_lock_check(z_h: float, hr: int) -> dict:
    if z_h > Z_CONFIRM_THRESHOLD:
        return {
            "source": "time_lock", "verdict": "confirmed",
            "detail": f"The cancellation rate at {hr:02d}:00 is {z_h:.1f} standard deviations above "
            f"this store/product's normal hourly rate.",
        }
    return {
        "source": "time_lock", "verdict": "possible_contributor",
        "detail": f"The cancellation rate at {hr:02d}:00 is somewhat above this store/product's normal "
        f"hourly rate ({z_h:.1f} standard deviations), but not sharply time-locked.",
    }


def _sku_cause_check(lift: float) -> dict:
    if lift > LIFT_CONFIRM_THRESHOLD:
        return {
            "source": "sku_cause", "verdict": "confirmed",
            "detail": f"Orders containing this product cancel {lift:.1f}x more often than orders without it.",
        }
    return {
        "source": "sku_cause", "verdict": "possible_contributor",
        "detail": f"Orders containing this product cancel {lift:.1f}x more often than orders without it "
        f"-- a real but modest overlap.",
    }


def _persistence_check(days_appeared: int, days_observed: int) -> dict:
    if days_observed and days_appeared / days_observed >= 0.5:
        return {
            "source": "persistence", "verdict": "confirmed",
            "detail": f"Appeared on {days_appeared} of the last {days_observed} days this store was trading.",
        }
    return {
        "source": "persistence", "verdict": "possible_contributor",
        "detail": f"Appeared on {days_appeared} of the last {days_observed} days this store was trading.",
    }


def _generate_incident_recommendations(evidence: dict) -> list[dict]:
    """Same evidence-constrained, non-agentic LLM posture as
    investigation._generate_recommendations -- reuses that module's NVIDIA
    client/model config directly rather than duplicating API-key handling."""
    prompt = (
        "You are a business analyst writing recommendations from evidence only. "
        "A recurring, time-of-day-locked incident was detected: a product is "
        "disproportionately represented in cancelled orders, concentrated in a "
        "specific hour of day, repeating across multiple days. Given this "
        "evidence as JSON, return a JSON array of 2-4 recommended actions, most "
        "important first. Each item must be exactly: "
        '{"action": string, "owner": string, "priority": "high"|"medium"|"low", '
        '"expected_benefit": string}. '
        "'owner' should be a plausible responsible team (e.g. 'Outlet operations', "
        "'Inventory team') given this is a store-level, product-specific, "
        "time-of-day pattern. Do not claim to know the exact root cause (e.g. do "
        "not assert 'out of stock' as an established fact) -- the evidence does "
        "not directly observe inventory levels, only order/cancellation records. "
        "State only what the evidence shows: which hour, which product, how "
        "concentrated (the Lift figure), how persistent (days appeared out of "
        "days observed). Recommend investigating and fixing this product's "
        "availability/fulfillment during this specific hour at this store, and "
        "recommend a substitution or proactive customer-communication process "
        "for when it recurs. 'expected_benefit' must be grounded in the "
        "evidence's own cancelled-order count and Rs impact -- never invent a "
        "number not present in the evidence. Return ONLY the JSON array, no "
        "prose, no markdown fences.\n\n"
        f"Evidence:\n{json.dumps(evidence, default=str)}"
    )
    try:
        client = investigation._get_client()
        response = client.chat.completions.create(
            model=investigation._get_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return recommendation_schema.parse(response.choices[0].message.content or "[]",
            allowed_refs={c["source"] for c in evidence.get("checks", [])})
    except Exception:
        logger.exception("incident recommendation generation failed")
        return recommendation_schema.Recommendations()


def build_incident_findings(as_of=None) -> list[dict]:
    as_of = as_of or _as_of()
    if as_of is None:
        return []
    window_start = as_of - timedelta(days=INCIDENT_WINDOW_DAYS)
    as_of_date = as_of.date().isoformat()

    hourly = _hourly_rates(as_of, window_start)
    lift_map = _basket_lift(as_of, window_start)
    store_names = {
        r["id"]: r["name"]
        for r in reporting.run_query(
            "SELECT id, name FROM res_store WHERE id = ANY(:stores)", {"stores": list(CELESTE_STORE_IDS)}
        )
    }

    by_store_product = {}
    for r in hourly:
        by_store_product.setdefault((r["store_id"], r["product_id"], r["product_name"]), []).append(r)

    # Pass 1: cheap in-memory scoring (z_h x Lift x sqrt(n)) across every
    # candidate, without the persistence term -- that needs one query per
    # candidate, so it's only worth paying for once everything else already
    # clears the bar.
    candidates = []
    for (store_id, product_id, product_name), rows in by_store_product.items():
        lift_info = lift_map.get((store_id, product_id))
        if not lift_info or lift_info["lift"] <= LIFT_GATE:
            continue
        vals = [(r["cancels"] / r["orders"]) if r["orders"] else 0.0 for r in rows]
        if len(vals) < 3:
            continue
        r_bar = mean(vals)
        sigma = pstdev(vals)
        if sigma == 0:
            continue
        for r in rows:
            if r["orders"] < MIN_N:
                continue
            r_h = r["cancels"] / r["orders"]
            z_h = (r_h - r_bar) / sigma
            if z_h <= 0:
                continue
            score_no_persistence = z_h * lift_info["lift"] * sqrt(r["orders"])
            if score_no_persistence < SCORE_WATCHLIST:
                continue
            candidates.append({
                "store_id": store_id, "product_id": product_id, "product_name": product_name,
                "hr": r["hr"], "z_h": z_h, "lift": lift_info["lift"], "n": r["orders"],
                "r_h": r_h, "r_bar": r_bar, "cancels": r["cancels"],
                "score_no_persistence": score_no_persistence, "hourly_rows": rows,
            })

    # Pass 2: persistence + full evidence/impact/recommendation build,
    # highest raw signal first so the LLM-call cap spends its budget on the
    # most severe candidates rather than an arbitrary dict order.
    candidates.sort(key=lambda c: -c["score_no_persistence"])

    findings = []
    llm_calls = 0
    for c in candidates:
        store_id, product_id, hr = c["store_id"], c["product_id"], c["hr"]
        days_appeared, days_observed, m = _persistence(store_id, product_id, hr, as_of, window_start)
        score = round(c["score_no_persistence"] * m, 2)
        if score < SCORE_WATCHLIST:
            continue
        if score < SCORE_CONFIRMED:
            logger.info(
                "incident watchlist: store=%s product=%s hr=%s score=%.1f (z=%.2f lift=%.2f n=%s m=%.2f)",
                store_id, c["product_name"], hr, score, c["z_h"], c["lift"], c["n"], m,
            )
            continue

        store_name = store_names.get(store_id, f"Store {store_id}")
        priority = _priority_for_score(score)

        checks = [
            _time_lock_check(c["z_h"], hr),
            _sku_cause_check(c["lift"]),
            _persistence_check(days_appeared, days_observed),
        ]
        data_quality = evidence_engine.snapshot_data_quality_check(as_of_date)

        avg_order_value = _avg_order_value(store_id, as_of, window_start)
        financial_impact = round(c["cancels"] * avg_order_value, 2) if avg_order_value else None
        impact = {
            "kind": "cancelled_orders",
            "financial_impact_rs": financial_impact,
            "count": c["cancels"],
            "avg_order_value_used": round(avg_order_value, 2) if avg_order_value else None,
            "confidence": impact_engine.CONFIDENCE_ESTIMATED,
            "month": as_of_date,
            "detail": (
                f"{c['cancels']} orders containing {c['product_name']} cancelled at {store_name} around "
                f"{hr:02d}:00 over the last {INCIDENT_WINDOW_DAYS} days"
                + (f" (~Rs. {financial_impact:,.0f})." if financial_impact else ".")
            ),
        }

        breakdowns = [{
            "dimension": "hour",
            "label": "Hour of day",
            "mode": "rate",
            "baseline_month": None,
            "top_contributors": sorted(
                [
                    {
                        "name": f"{r['hr']:02d}:00",
                        "current": round((r["cancels"] / r["orders"] * 100) if r["orders"] else 0, 2),
                        "baseline": round(c["r_bar"] * 100, 2),
                        "delta": round(((r["cancels"] / r["orders"]) - c["r_bar"]) * 100 if r["orders"] else -c["r_bar"] * 100, 2),
                        "current_volume": r["orders"],
                    }
                    for r in c["hourly_rows"]
                ],
                key=lambda x: -x["delta"],
            )[:8],
        }]

        business_impact_score = impact_engine.business_impact_score(
            priority, {"consecutive_periods": days_appeared}, impact, data_quality
        )

        evidence_payload = {
            "store": store_name, "product": c["product_name"], "hour": f"{hr:02d}:00",
            "checks": checks, "impact": impact,
            "persistence": {"days_appeared": days_appeared, "days_observed": days_observed},
            "incident_score": score,
        }
        recommendations = []
        if llm_calls < MAX_LLM_CALLS_PER_SCAN:
            recommendations = _generate_incident_recommendations(evidence_payload)
            llm_calls += 1

        metric_id = f"incident_s{store_id}_p{product_id}_h{hr}"
        pct_change = round((c["r_h"] - c["r_bar"]) / c["r_bar"] * 100, 2) if c["r_bar"] else None
        findings.append({
            "id": f"{metric_id}-{as_of_date}",
            "metric_id": metric_id,
            "domain": "order_customer",
            "title": f"{c['product_name']} cancellations spike at {store_name} around {hr:02d}:00",
            "category": "critical",
            "severity": "high",
            "priority": priority,
            "business_impact_score": business_impact_score,
            "detected": date.today().isoformat(),
            "confirm": {
                "period": as_of_date,
                "current": round(c["r_h"] * 100, 2),
                "baseline": round(c["r_bar"] * 100, 2),
                "pct_change": pct_change,
                "unit": "percent",
            },
            "breakdowns": breakdowns,
            "impact": impact,
            "how": None,
            "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
            "data_quality": data_quality,
            "recommendations": recommendations,
            "status": "completed",
            "incident_score": score,
            # Frontend Intelligence Integration: store/product/hour and
            # persistence were only ever baked into title/impact.detail's
            # prose and _persistence_check's own detail text -- a panel
            # shouldn't have to parse a sentence to get the store name or
            # how many days this has recurred.
            "store": store_name,
            "product": c["product_name"],
            "hour": f"{hr:02d}:00",
            "persistence": {"days_appeared": days_appeared, "days_observed": days_observed},
        })

    flagged = {f["metric_id"] for f in findings}
    records = []
    # Only observed, sufficiently exposed outlet/product/hour scopes are covered.
    # A disappeared product or sparse hour is not evidence of recovery.
    for row in hourly:
        if row["orders"] < MIN_N:
            continue
        key = f"incident_s{row['store_id']}_p{row['product_id']}_h{row['hr']}"
        records.append(measurement.observation(
            key, "snapshot", "measured_actionable" if key in flagged else "measured_healthy",
            scope=key, period=as_of_date,
            freshness="fresh" if (datetime.now(UTC).date() - as_of.date()).days <= 1 else "stale",
            value=row["cancels"] / row["orders"] * 100,
        ))
    for finding in findings:
        finding["measurement"] = next((m for m in records if m["metric_id"] == finding["metric_id"]), None)
        recs = finding["recommendations"]
        finding["recommendation_generation"] = {"status": getattr(recs, "generation_status", "not_requested"),
                                                  "rejected_count": getattr(recs, "rejected_count", 0)}
    return measurement.FindingBatch(findings, records)
