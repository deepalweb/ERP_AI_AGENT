"""Confirmed sales with no stock deduction at all -- clustered by episode.

User report, 2026-10-24: "outlet inventory mismatch, against the sale stock
are not match." Investigated directly against the live DB before building
anything (see this module's threshold comments for every verified number).

The naive version of this check -- "any sale line with no matching
stock_move" -- was tried first and rejected: 86% of the 1,166 initial hits
(1,007 of 1,166) were `service`-type products (delivery fees etc.), which
never get a stock move by Odoo design -- not a bug. Of the real 159
consu-type hits, 157 collapsed into just 23 distinct orders, all placed
within a single ~3-minute window on 2026-10-20 (08:52:36-08:54:55), with
EVERY affected order missing ALL of its lines' moves (never partial) --
the signature of a short batch/sync failure, not an ongoing per-line leak.
A flat "flag every missing line" detector would be wrong for this shape:
it would either never trip (too rare a background rate) or, once it did,
keep re-flagging the same old incident forever as if it were still
happening. So this detects CLUSTERS of orders in a short window with
missing deductions, not a flat rate.

A broader "does current stock reconcile with its full move history" check
was also attempted and explicitly NOT shipped: it produced an aggregate
gap too large to be real (Rs 46.5M across 13,811 outlet-product combos),
and after ruling out the two most likely causes (manual stock_quant
adjustments: negligible, 4 of 30,989 rows; stock-count corrections: the
same product's real count history showed variances of single-digit units,
nowhere near the few-hundred-unit gaps found) the true root cause was
still unexplained. Building a detector on an unexplained number would be
worse than not building one -- deliberately out of scope here.

Retail-product scoping, verified live: `sale_ok` alone doesn't separate
real retail products from internal packaging -- courier/carrier bags are
sale_ok=True and appear on ~160k-188k order lines each (auto-added
packaging, not a customer choice). Products categorised 'Spar' are SPAR's
co-located inventory, not Celeste's own business (same exclusion
store_scope.py already applies at the store level). 'Celeste Bulk'
products are raw-material BOM inputs consumed by MRP into a DIFFERENT
finished SKU (e.g. "Baby Potato - Celeste Bulk" feeds two separate
"Baby Potato ...g - Celeste Daily" finished products) -- they're not
meant to carry their own sellable on-hand balance, so a same-SKU check is
the wrong question for them.

'Convenience Fee' line items are the single biggest false-positive source,
and needed two separate fixes to actually exclude: most are correctly
type='service' (never get a stock move, by Odoo design -- same as every
other service-type product), but a meaningful minority of the identically-
named fee SKUs are miscatalogued type='consu' despite being a fee, not a
product. Verified live: before excluding 'convenience fee' by name
alongside type='service', they alone accounted for 1,941 of 2,098 (92.5%)
of all missing-move lines and inflated 68 episodes across a month down
from what should have been a small handful -- type='service' filtering
alone was NOT enough here.
"""

from collections import defaultdict
from datetime import date, timedelta

from backend.app.db import reporting
from backend.app.db.store_scope import CELESTE_STORE_IDS, SALE_ORDER_LIVE_STATES
from mcp_server.tools import evidence_engine, impact_engine, measurement

# Window and thresholds, all checked against the real incident before being
# picked:
#   - WINDOW_DAYS=30: wide enough to catch a rare batch failure without
#     scanning the whole company history (see module docstring for why an
#     unbounded window produced an unexplained number elsewhere).
#   - GAP_MINUTES=10: the real incident's 23 orders landed within ~3
#     minutes of each other; 10 minutes gives real headroom while still
#     separating genuinely distinct episodes.
#   - MIN_CLUSTER_ORDERS=5: well below the real incident's 23, but clearly
#     above background noise -- the one other hit in the 14-day sample
#     window was a single isolated order (not a cluster), correctly
#     excluded by this floor.
WINDOW_DAYS = 30
GAP_MINUTES = 10
MIN_CLUSTER_ORDERS = 5

MISSING_STOCK_MOVE_SQL = f"""
WITH bounds AS (
    SELECT MAX(date_order) AS as_of FROM sale_order
    WHERE state IN {SALE_ORDER_LIVE_STATES} AND store_id IN {CELESTE_STORE_IDS}
),
retail_lines AS (
    SELECT sol.id AS line_id, sol.order_id, sol.product_id, so.store_id, so.date_order, rs.name AS outlet
    FROM sale_order so
    CROSS JOIN bounds
    JOIN sale_order_line sol ON sol.order_id = so.id
    JOIN res_store rs ON rs.id = so.store_id
    JOIN product_product pp ON pp.id = sol.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    LEFT JOIN product_category pc ON pc.id = pt.categ_id
    WHERE so.state IN {SALE_ORDER_LIVE_STATES} AND so.store_id IN {CELESTE_STORE_IDS}
      AND sol.product_uom_qty > 0
      AND pt.sale_ok = true
      AND pt.type != 'service'
      AND pc.name::text NOT ILIKE '%spar%'
      AND pt.name::text NOT ILIKE '%bag%'
      AND pt.name::text NOT ILIKE '%packag%'
      AND pt.name::text NOT ILIKE '%thermal%'
      AND pt.name::text NOT ILIKE '%security seal%'
      AND pt.name::text NOT ILIKE '%celeste bulk%'
      AND pt.name::text NOT ILIKE '%convenience fee%'
      AND so.date_order >= bounds.as_of - INTERVAL '{WINDOW_DAYS} days'
),
move_presence AS (
    SELECT DISTINCT sale_line_id FROM stock_move WHERE sale_line_id IS NOT NULL
)
SELECT rl.line_id, rl.order_id, rl.product_id, rl.outlet, rl.date_order, pt.name AS product_name, bounds.as_of
FROM retail_lines rl
CROSS JOIN bounds
JOIN product_product pp ON pp.id = rl.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
LEFT JOIN move_presence mp ON mp.sale_line_id = rl.line_id
WHERE mp.sale_line_id IS NULL
ORDER BY rl.date_order
"""


def _cluster_episodes(rows):
    """Pure/sync: groups affected ORDERS (not lines) into episodes where
    consecutive orders are within GAP_MINUTES of each other, then keeps
    only episodes with at least MIN_CLUSTER_ORDERS distinct orders. Mirrors
    operation_flow.py's "require repetition, not one blip" principle, just
    clustered by time-proximity instead of by calendar day."""
    by_order = {}
    for r in rows:
        order_id = r["order_id"]
        entry = by_order.setdefault(order_id, {
            "order_id": order_id, "outlet": r["outlet"], "date_order": r["date_order"],
            "products": [],
        })
        name = r["product_name"]
        entry["products"].append(name.get("en_US") if isinstance(name, dict) else name)

    orders = sorted(by_order.values(), key=lambda o: o["date_order"])
    episodes = []
    current = []
    for order in orders:
        if current and (order["date_order"] - current[-1]["date_order"]) > timedelta(minutes=GAP_MINUTES):
            episodes.append(current)
            current = []
        current.append(order)
    if current:
        episodes.append(current)

    return [ep for ep in episodes if len(ep) >= MIN_CLUSTER_ORDERS]


def _missing_stock_move_finding():
    rows = reporting.run_query(MISSING_STOCK_MOVE_SQL)
    if not rows:
        measurement.source_unavailable("No sale order lines are available for the analysis window.")
        return None
    as_of = str(rows[0]["as_of"])[:10]
    measurement.source_as_of(as_of, verifies_source=False,
        unknown_reason="Order dates do not confirm when the reporting mirror was refreshed.")

    episodes = _cluster_episodes(rows)
    if not episodes:
        return None

    outlet_totals = defaultdict(int)
    for ep in episodes:
        for order in ep:
            outlet_totals[order["outlet"]] += 1

    total_orders = sum(len(ep) for ep in episodes)
    detail = (
        f"{len(episodes)} episode(s) of {total_orders} confirmed order(s) total had zero stock "
        f"deduction for any of their retail product lines, each episode landing within "
        f"{GAP_MINUTES} minutes -- the signature of a short batch/sync failure, not an "
        f"ongoing per-order rate."
    )
    checks = [
        {
            "source": "stock_move", "verdict": "confirmed",
            "detail": detail,
        },
        {
            "source": "cause", "verdict": "unknown",
            "detail": "Not established: what specifically failed during each episode (sync job, "
                      "picking batch, a specific integration step) -- sale_order_line and stock_move "
                      "alone cannot identify the mechanism, only that the deduction never happened.",
        },
    ]

    return {
        "id": f"stock_move_missing-{as_of}",
        "metric_id": "stock_move_missing",
        "domain": "inventory",
        "title": f"{total_orders} order(s) across {len(episodes)} episode(s) had no stock deduction for confirmed sales",
        "category": "critical",
        "severity": "medium",
        "priority": "medium",
        "business_impact_score": impact_engine.business_impact_score(
            "medium", {"consecutive_periods": 1}, {"count": total_orders}, None,
        ),
        "detected": date.today().isoformat(),
        "confirm": {"period": as_of, "current": total_orders, "baseline": None,
                    "pct_change": None, "unit": "orders affected"},
        "breakdowns": [{
            "dimension": "outlet", "label": "Affected Celeste outlet", "mode": "contribution",
            "baseline_month": None,
            "top_contributors": sorted(
                ({"name": name, "current": value} for name, value in outlet_totals.items()),
                key=lambda item: -item["current"],
            ),
        }],
        "impact": {"kind": "stock_move_missing", "financial_impact_rs": None, "count": total_orders,
                   "detail": detail, "confidence": "measured", "month": as_of},
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "recommendations": [{
            "action": "Review what ran (or failed) around each episode's time window -- a sync job, "
                       "picking batch, or integration step most likely stopped partway through.",
            "owner": "Operations and systems teams", "priority": "medium",
            "expected_benefit": "Outlet stock for the affected products is currently overstated in the "
                                 "system relative to what was actually sold; fixing the cause prevents it recurring.",
        }],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        # outlets is a dict, not a single "outlet" string -- verified live
        # (2026-10-24) that the real Oct 20 episode spans all 4 Celeste
        # outlets at once (a shared root cause, not an outlet-specific
        # one); picking ep[0]'s outlet would have misreported it as
        # affecting only one.
        "stock_move_missing_episodes": [
            {
                "outlets": dict(sorted(
                    ((outlet, sum(1 for o in ep if o["outlet"] == outlet)) for outlet in {o["outlet"] for o in ep}),
                    key=lambda item: -item[1],
                )),
                "order_count": len(ep),
                "start": str(ep[0]["date_order"]),
                "end": str(ep[-1]["date_order"]),
                "distinct_products": len({p for order in ep for p in order["products"]}),
            }
            for ep in episodes
        ],
    }


def build_stock_reconciliation_findings():
    """Confirmed sales with no stock deduction at all, clustered into
    episodes -- see module docstring for why this is a cluster detector,
    not a flat per-line rate."""
    return measurement.combine([
        measurement.run_snapshot(
            "stock_move_missing", _missing_stock_move_finding,
            unknown_freshness_is_measured=True,
        ),
    ])
