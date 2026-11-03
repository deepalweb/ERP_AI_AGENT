"""Per-product purchase price deviation.

The existing purchase_price_variance metric (intelligence.py) is company-wide
only: a monthly RATE of how often a PO's price differs from its original
amount, with no per-product magnitude. purchase_order_line.original_price_unit
looked like it could give per-line revision tracking directly, but verified
live (2026-10-22): only 5.8% of lines have it populated, and when populated
it is frequently a garbage 0.0 default -- not usable.

Instead this baselines each product against its OWN purchase history: the
log of the price ratio between consecutive purchases, baselined per product
(not one flat percentage for every product). Verified live over a 180-day
window: 2,367 products have enough history to baseline, and a z>=3 /
|pct|>=15% threshold flags exactly 5 genuine outliers -- all stable
packaged goods (Oats, Muesli, Vanilla Essence), never fresh produce, which
confirms the per-product baseline is doing its job: produce's own history is
volatile enough that normal seasonal swings don't cross z=3, while a real
step-change on an otherwise-stable product does. A flat percentage
threshold would have done the opposite -- flagging produce constantly and
missing quieter-but-real packaged-goods changes.

celeste_grn_price_updated is NOT a second detector here -- verified live
that it's 95.8% correlated with recent PO price mismatches on the same
product, i.e. a derived flag of this same event. It's folded in below as a
root_cause corroboration check instead.
"""

import math
import statistics
from collections import defaultdict
from datetime import date

from backend.app.db import reporting
from mcp_server.tools import evidence_engine, impact_engine, measurement

# Every threshold here was checked against the real 180-day purchase
# history before being picked, not guessed:
#   - MIN_BASELINE_POS=5: below this, a product's own stddev is too thin to
#     trust (a single outlier purchase would dominate it).
#   - Z_THRESHOLD=3.0 + MIN_ABS_PCT_CHANGE=0.15: together these flagged 5
#     products out of 2,367 with enough history -- an actionable number, not
#     noise. Z alone over-triggers on products with a naturally tiny
#     stddev; the 15% floor stops a statistically-"unusual" but trivial
#     blip (e.g. a 2% move on an extremely stable product) from qualifying.
WINDOW_DAYS = 180
MIN_BASELINE_POS = 5
Z_THRESHOLD = 3.0
MIN_ABS_PCT_CHANGE = 0.15

# purchase_order has no store_id -- purchasing is company-wide, not
# per-outlet (verified live: the whole table has a single company_id).
# Unlike sale_order, there is nothing here to scope out with
# CELESTE_STORE_IDS.
PO_PRICE_HISTORY_SQL = f"""
WITH bounds AS (
    SELECT MAX(date_order) AS as_of FROM purchase_order WHERE state = 'purchase'
)
SELECT pol.product_id, pt.name AS product_name, pol.price_unit, po.date_order,
       po.partner_id, rp.name AS supplier_name, bounds.as_of
FROM purchase_order po
CROSS JOIN bounds
JOIN purchase_order_line pol ON pol.order_id = po.id
LEFT JOIN res_partner rp ON rp.id = po.partner_id
JOIN product_product pp ON pp.id = pol.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE po.state = 'purchase' AND pol.price_unit > 0
  AND po.date_order >= bounds.as_of - INTERVAL '{WINDOW_DAYS} days'
ORDER BY pol.product_id, po.date_order
"""

GRN_FLAG_SQL = """
SELECT pp.id AS product_id, pt.celeste_grn_price_updated
FROM product_product pp
JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE pp.id = ANY(:product_ids)
"""


def _price_deviations(rows):
    """Pure/sync: groups PO lines by product, computes each product's own
    baseline of consecutive-purchase log-price-changes (excluding the most
    recent transition from its own baseline), and flags products whose
    latest transition is a genuine outlier against that baseline. Mirrors
    operation_flow.py's baseline/recent split -- the thing being tested
    never contributes to its own baseline."""
    by_product = defaultdict(list)
    for r in rows:
        by_product[r["product_id"]].append(r)

    flagged = []
    for product_id, product_rows in by_product.items():
        prices = [float(r["price_unit"]) for r in product_rows]
        if len(prices) < MIN_BASELINE_POS + 1:
            continue
        log_changes = [
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
            if prices[i - 1] > 0 and prices[i] > 0
        ]
        if len(log_changes) < MIN_BASELINE_POS + 1:
            continue
        baseline = log_changes[:-1]
        latest_change = log_changes[-1]
        mean = statistics.mean(baseline)
        stdev = statistics.pstdev(baseline)
        if stdev == 0:
            continue
        z = (latest_change - mean) / stdev
        pct_change = math.exp(latest_change) - 1
        if abs(z) < Z_THRESHOLD or abs(pct_change) < MIN_ABS_PCT_CHANGE:
            continue
        latest_row = product_rows[-1]
        prev_row = product_rows[-2]
        flagged.append({
            "product_id": product_id,
            "product_name": latest_row["product_name"].get("en_US") if isinstance(latest_row["product_name"], dict) else latest_row["product_name"],
            "supplier_name": latest_row["supplier_name"],
            "previous_price": round(prev_row["price_unit"], 2),
            "latest_price": round(latest_row["price_unit"], 2),
            "pct_change": round(pct_change * 100, 1),
            "z_score": round(z, 1),
            "baseline_purchases": len(baseline),
            "latest_date": str(latest_row["date_order"])[:10],
        })
    return flagged


def _attach_grn_corroboration(flagged):
    if not flagged:
        return flagged
    product_ids = [f["product_id"] for f in flagged]
    rows = reporting.run_query(GRN_FLAG_SQL, {"product_ids": product_ids})
    grn_by_product = {r["product_id"]: r["celeste_grn_price_updated"] for r in rows}
    for f in flagged:
        f["grn_price_updated"] = grn_by_product.get(f["product_id"])
    return flagged


def _po_price_deviation_finding():
    rows = reporting.run_query(PO_PRICE_HISTORY_SQL)
    if not rows:
        measurement.source_unavailable("No purchase order lines are available for the analysis window.")
        return None
    as_of = str(rows[0]["as_of"])[:10]
    measurement.source_as_of(as_of, verifies_source=False,
        unknown_reason="Purchase order dates do not confirm when the reporting mirror was refreshed.")

    flagged = _price_deviations(rows)
    if not flagged:
        return None
    flagged = _attach_grn_corroboration(flagged)
    flagged.sort(key=lambda f: -abs(f["pct_change"]))

    checks = [
        {
            "source": "purchase_order_line", "verdict": "confirmed",
            "detail": f"{len(flagged)} product(s) had a purchase price move statistically unusual "
                      f"against their own last {WINDOW_DAYS}-day purchase history "
                      f"(z>={Z_THRESHOLD}, at least {MIN_ABS_PCT_CHANGE*100:.0f}% change).",
        },
        {
            "source": "celeste_grn_price_updated",
            "verdict": "confirmed" if any(f.get("grn_price_updated") for f in flagged) else "no_evidence_found",
            "detail": "The product's catalog cost was also flagged as GRN-updated for "
                      f"{sum(1 for f in flagged if f.get('grn_price_updated'))} of {len(flagged)} flagged product(s), "
                      "consistent with the price change originating from a goods receipt.",
        },
        {
            "source": "cause", "verdict": "unknown",
            "detail": "Not established: whether each change is a genuine supplier price move, a pack-size/UOM "
                      "change, or a data-entry correction -- purchase_order_line alone cannot distinguish these.",
        },
    ]

    return {
        "id": f"po_price_deviation-{as_of}",
        "metric_id": "po_price_deviation",
        "domain": "supplier",
        "title": f"{len(flagged)} product(s) had an unusual purchase price change",
        "category": "critical",
        "severity": "medium",
        "priority": "medium",
        "business_impact_score": impact_engine.business_impact_score(
            "medium", {"consecutive_periods": 1}, {"count": len(flagged)}, None,
        ),
        "detected": date.today().isoformat(),
        "confirm": {"period": as_of, "current": len(flagged), "baseline": None,
                    "pct_change": None, "unit": "products flagged"},
        "breakdowns": [{
            "dimension": "product", "label": "Flagged product", "mode": "contribution",
            "baseline_month": None,
            "top_contributors": [
                {"name": f["product_name"], "current": f["pct_change"]} for f in flagged
            ],
        }],
        "impact": {"kind": "po_price_deviation", "financial_impact_rs": None, "count": len(flagged),
                   "detail": f"{len(flagged)} product(s) flagged over the trailing {WINDOW_DAYS} days.",
                   "confidence": "measured", "month": as_of},
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "recommendations": [{
            "action": "Review the flagged products' purchase history and confirm each change with the supplier "
                       "or against a pack-size/UOM change before treating it as a cost increase.",
            "owner": "Purchasing team", "priority": "medium",
            "expected_benefit": "Separates genuine supplier price moves from data or pack-size artifacts before they affect margin decisions.",
        }],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "po_price_deviation_rows": flagged,
    }


def build_price_intelligence_findings():
    """Per-product purchase price deviation against each product's own
    history -- see module docstring for why this replaces a flat threshold."""
    return measurement.combine([
        measurement.run_snapshot(
            "po_price_deviation", _po_price_deviation_finding,
            unknown_freshness_is_measured=True,
        ),
    ])
