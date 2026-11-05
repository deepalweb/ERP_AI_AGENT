"""Promotion discount-rate outliers, among promotions active right now.

Price Intelligence Plan, Phase 2, 2026-11-03. Reuses `loyalty.py`'s own
discount math (`price - promo_price` for value, `rate_percentage` for the
rate) rather than re-querying `store_promotion`/`store_promotion_line`
independently.

Checked before building: `store_promotion_line` is a static per-promotion
PRODUCT CONFIGURATION table (one row per product the promotion covers), not
a time-series of individual redemption events -- a single promotion has no
"history of itself" to compare today's value against, so the plan's
original framing ("deviates from that same promotion's own historical
distribution") doesn't hold against the real schema. What IS real: a
CROSS-SECTIONAL comparison -- how this promotion's average discount rate
compares to the population of every other approved promotion ever run.

Verified live (2026-11-03), scoped to promotions with at least
MIN_PRODUCTS lines (drops a 1-product 50%-off "Short Expire" clearance,
which is thin-sample noise, not a rate anomaly): 88 approved promotions,
median avg_discount_pct 16.7%, p95 18.85%. The one real outlier is
"September - Salary_Week 1" at 29% (25 products) -- 1.74x the population
median, clearly outside the 16-19% band every other sizeable promotion
sits in. RATIO_TO_MEDIAN below is set just under that ratio so it would
have caught that promotion, not a round guess.

`total_discount_value` is NOT compared across promotions directly (unlike
avg_discount_pct) -- it scales with how many products a promotion covers
(616-product promotions naturally total far more than 25-product ones),
so it's reported for context only, never thresholded on here.
"""

from datetime import date
from statistics import median

from backend.app.db import reporting
from mcp_server.tools import evidence_engine, impact_engine, measurement

MIN_PRODUCTS = 5  # excludes thin, single/few-item clearance promotions as noise (see module docstring)
RATIO_TO_MEDIAN = 1.5  # verified live: the one real outlier sits at 1.74x the population median; nothing legitimate exceeds 1.13x
ISSUE_METRIC_ID = "promotion_discount_outlier"

POPULATION_SQL = """
    SELECT AVG(spl.rate_percentage) FILTER (WHERE spl.rate_percentage > 0) AS avg_discount_pct
    FROM store_promotion sp
    JOIN store_promotion_line spl ON spl.promotion_id = sp.id
    WHERE sp.state = 'approved'
    GROUP BY sp.id
    HAVING COUNT(spl.id) >= :min_products
"""

ACTIVE_PROMOTIONS_SQL = """
    SELECT sp.id, sp.name, sp.applied_to, sp.start_date, sp.end_date,
           AVG(spl.rate_percentage) FILTER (WHERE spl.rate_percentage > 0) AS avg_discount_pct,
           SUM(spl.price - spl.promo_price) AS total_discount_value,
           COUNT(spl.id) AS n_products
    FROM store_promotion sp
    JOIN store_promotion_line spl ON spl.promotion_id = sp.id
    WHERE sp.state = 'approved' AND sp.start_date <= CURRENT_DATE AND sp.end_date >= CURRENT_DATE
    GROUP BY sp.id, sp.name, sp.applied_to, sp.start_date, sp.end_date
    HAVING COUNT(spl.id) >= :min_products
"""


def _promotion_discount_outlier_finding():
    population_rows = reporting.run_query(POPULATION_SQL, {"min_products": MIN_PRODUCTS})
    rates = [float(r["avg_discount_pct"]) for r in population_rows if r["avg_discount_pct"] is not None]
    # The population query and the active-promotions query both run live,
    # right now -- independent of whether the order feed's own recency is
    # stale, same reasoning as every other detector fixed this session.
    measurement.source_as_of(date.today().isoformat())
    if not rates:
        return None
    population_median = median(rates)

    active_rows = reporting.run_query(ACTIVE_PROMOTIONS_SQL, {"min_products": MIN_PRODUCTS})
    flagged = [
        {
            "id": r["id"], "name": r["name"], "applied_to": r["applied_to"],
            "start_date": r["start_date"].isoformat(), "end_date": r["end_date"].isoformat(),
            "avg_discount_pct": round(float(r["avg_discount_pct"]), 1),
            "total_discount_value": round(float(r["total_discount_value"] or 0), 2),
            "n_products": int(r["n_products"]),
            "ratio_to_median": round(float(r["avg_discount_pct"]) / population_median, 2),
        }
        for r in active_rows
        if r["avg_discount_pct"] is not None and float(r["avg_discount_pct"]) >= RATIO_TO_MEDIAN * population_median
    ]
    if not flagged:
        return None

    as_of = date.today().isoformat()
    checks = [
        {"source": "discount_rate_outlier", "verdict": "confirmed",
         "detail": f"{len(flagged)} currently-active promotion(s) run an average discount rate at least "
                   f"{RATIO_TO_MEDIAN}x the {round(population_median, 1)}% median across every approved "
                   f"promotion (n={len(rates)}, >= {MIN_PRODUCTS} products each)."},
        {"source": "cause", "verdict": "unknown",
         "detail": "Not established: whether this rate was deliberately set for a specific clearance/campaign "
                   "reason or is a configuration error. store_promotion carries no approval-reason field."},
    ]
    impact = {
        "kind": ISSUE_METRIC_ID, "financial_impact_rs": None, "count": len(flagged),
        "confidence": impact_engine.CONFIDENCE_ESTIMATED, "month": as_of,
        "detail": f"{len(flagged)} promotion(s) active today at >= {RATIO_TO_MEDIAN}x the normal discount rate.",
    }
    data_quality = evidence_engine.snapshot_data_quality_check(as_of)
    priority = "high" if any(f["ratio_to_median"] >= 2 * RATIO_TO_MEDIAN for f in flagged) else "medium"
    business_impact_score = impact_engine.business_impact_score(
        priority, {"consecutive_periods": 1}, impact, data_quality,
    )
    return {
        "id": f"{ISSUE_METRIC_ID}-{as_of}",
        "metric_id": ISSUE_METRIC_ID,
        "domain": "sales",
        "title": f"{len(flagged)} active promotion(s) running at an unusually high discount rate",
        "category": "critical",
        "severity": priority,
        "priority": priority,
        "business_impact_score": business_impact_score,
        "detected": as_of,
        "confirm": {"period": as_of, "current": len(flagged), "baseline": round(population_median, 1),
                    "pct_change": None, "unit": "count"},
        "breakdowns": [{
            "dimension": "promotion", "label": "Discount rate by promotion", "mode": "contribution",
            "baseline_month": None,
            "top_contributors": [{"name": f["name"], "current": f["avg_discount_pct"], "delta": f["avg_discount_pct"]}
                                  for f in flagged],
        }],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "data_quality": data_quality,
        "recommendations": [{
            "action": "Confirm each flagged promotion's discount rate was set intentionally, not misconfigured.",
            "owner": "Marketing/promotions team", "priority": priority,
            "expected_benefit": "Catches a promotion-setup error before it runs its full window, rather than "
                                 "after the discount has already been given out.",
        }],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "flagged_promotions": flagged,
    }


def build_promotion_intelligence_findings() -> measurement.FindingBatch:
    return measurement.combine([
        measurement.run_snapshot(ISSUE_METRIC_ID, _promotion_discount_outlier_finding),
    ])
