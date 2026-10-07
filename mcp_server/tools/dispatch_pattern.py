"""Dispatcher x product pattern detector.

transfer_accountability.py's company-wide panel shows who dispatched and which
products carry the loss, but it is a dashboard, not an alert: a pattern like one
dispatcher's elevated rate on one specific product has to be found by reading the
data by hand, the way the Sandya/Potato/Coconut pattern was found this session.
This is that alert.

Concentration (one dispatcher accounts for most of a product's missing units) is
NOT the signal: dispatchers are tied to fixed warehouse-to-outlet routes, not
randomly assigned, so for 192 recurring products over 6 months the median
dispatcher concentration is already 66.7% and 41 products are at 90%+ -- that is
who is on the route, not a behavior problem.

The real signal is a RATE comparison: a dispatcher's miss rate on one specific
recurring product, against their own overall rate across everything else they
handle. Over the same 6 months, (dispatcher, recurring product) pairs with at
least 50 units handled have a median rate of 0.9% and a median ratio-to-own-
baseline of 1.0x -- most pairs are unremarkable. The cut here (rate >= 5% AND
>= 5x the dispatcher's own rate) sits at that data's ~p90, and finds the same
handful of real outlier pairs a manual review of this data already found,
company-wide, not just the ones with a recorded shortage.

One case rolls up every flagged pair in the window, with breakdowns by person
and by product. It does not repeat the individual lines behind each pair --
the company-wide accountability panel, shown on this finding's page too, lets
a manager filter to one person or product and see the lines there.
"""

from collections import defaultdict
from datetime import date

from mcp_server.tools import evidence_engine, impact_engine, measurement
from mcp_server.tools.transfer_accountability import WINDOW_DAYS, _missing, _product_key, _raw_rows, _recurring_keys

MIN_PAIR_UNITS = 50  # a dispatcher must have handled a meaningful volume of this product
PAIR_RATE_MIN = 5.0  # 6-month p90 of (dispatcher, recurring product) pairs with volume >= the floor above
RATIO_MIN = 5.0       # ...and at least this many times the dispatcher's own overall rate (same p90)
ISSUE_METRIC_ID = "dispatch_pattern"


def _number(value) -> float:
    return float(value or 0)


def _pairs(rows, recurring_keys):
    overall = defaultdict(lambda: [0.0, 0.0])  # dispatcher -> [expected, missing]
    pair = defaultdict(lambda: {"product": None, "sku": None, "expected": 0.0, "missing": 0.0,
                                "lines": 0, "outlets": set()})
    for row in rows:
        who = row["dispatch_completed_by"]
        if not who:
            continue
        expected = _number(row["expected"])
        missing = _missing(row)
        overall[who][0] += expected
        overall[who][1] += missing
        key = (who, _product_key(row))
        p = pair[key]
        p["product"] = row["product"] or "Unnamed product"
        p["sku"] = row["sku"]
        p["expected"] += expected
        p["missing"] += missing
        if missing > 0:
            p["lines"] += 1
            p["outlets"].add(row["store_id"])

    flagged = []
    for (who, prod_key), p in pair.items():
        if prod_key not in recurring_keys or p["expected"] < MIN_PAIR_UNITS or p["missing"] <= 0:
            continue
        pair_rate = p["missing"] / p["expected"] * 100
        if pair_rate < PAIR_RATE_MIN:
            continue
        own_expected, own_missing = overall[who]
        own_rate = own_missing / own_expected * 100 if own_expected else 0.0
        if own_rate > 0 and pair_rate / own_rate < RATIO_MIN:
            continue
        flagged.append({
            "dispatcher": who,
            "product": p["product"],
            "sku": p["sku"],
            "expected_units": round(p["expected"]),
            "missing_units": round(p["missing"]),
            "rate_pct": round(pair_rate, 1),
            "own_rate_pct": round(own_rate, 2),
            "ratio": round(pair_rate / own_rate, 1) if own_rate > 0 else None,
            "short_lines": p["lines"],
            "outlets": len(p["outlets"]),
        })
    return sorted(flagged, key=lambda r: -r["rate_pct"])


def _breakdown(flagged, key_fn, label, dimension):
    totals = defaultdict(lambda: {"current": 0.0, "lines": 0})
    for row in flagged:
        bucket = totals[key_fn(row)]
        bucket["current"] += row["missing_units"]
        bucket["lines"] += 1
    return {
        "dimension": dimension, "label": label, "mode": "contribution", "baseline_month": None,
        "top_contributors": sorted(
            [{"name": name, "current": v["current"], "lines": v["lines"]} for name, v in totals.items()],
            key=lambda c: -c["current"],
        ),
    }


def build_dispatch_pattern_findings() -> list[dict]:
    raw = _raw_rows()
    if not raw:
        return []
    recurring_keys = _recurring_keys(raw)
    flagged = _pairs(raw, recurring_keys)
    if not flagged:
        return []

    as_of_date = date.today().isoformat()
    total_missing = sum(r["missing_units"] for r in flagged)
    dispatchers = {r["dispatcher"] for r in flagged}
    products = {r["sku"] or r["product"] for r in flagged}

    checks = [
        {"source": "pattern_magnitude", "verdict": "confirmed",
         "detail": f"{len(flagged)} dispatcher/product pair(s) in the last {WINDOW_DAYS} days have a miss rate of "
                   f"at least {PAIR_RATE_MIN:.0f}% on a product that is recurring overall, at least {RATIO_MIN:.0f}x "
                   "that dispatcher's own rate across everything else they handle -- not the routine spread."},
        {"source": "attribution", "verdict": "unknown",
         "detail": "Not established: whether the gap is at dispatch, in transit, or at receiving for these lines. "
                   "This flags a pattern tied to who marked the outbound note done, not a finding of fault."},
        {"source": "company_wide_context", "verdict": "possible_contributor",
         "detail": "See the company-wide panel below: filter to one of these people or products to check the "
                   "individual lines behind this pattern."},
    ]

    impact = {
        "kind": "dispatch_pattern",
        "financial_impact_rs": None,
        "count": len(flagged),
        "units_affected": round(total_missing),
        "distinct_products": len(products),
        "confidence": impact_engine.CONFIDENCE_ESTIMATED,
        "month": as_of_date,
        "detail": f"{len(flagged)} dispatcher/product pattern(s), {round(total_missing):g} missing units, "
                  f"across {len(dispatchers)} dispatcher(s) and {len(products)} product(s) in the last {WINDOW_DAYS} days.",
    }

    # Same style as extreme_event.py and dispatch_overage.py: cuts are multiples of
    # the detector's own floor, plus how many pairs fired at once.
    worst_rate = max(r["rate_pct"] for r in flagged)
    if worst_rate >= 3 * PAIR_RATE_MIN or len(flagged) >= 4:
        priority = "critical"
    elif worst_rate >= 2 * PAIR_RATE_MIN or len(flagged) >= 2:
        priority = "high"
    else:
        priority = "medium"

    data_quality = evidence_engine.snapshot_data_quality_check(as_of_date)
    business_impact_score = impact_engine.business_impact_score(
        priority, {"consecutive_periods": 1}, impact, data_quality,
    )
    finding = {
        "id": f"{ISSUE_METRIC_ID}-{as_of_date}",
        "metric_id": ISSUE_METRIC_ID,
        "domain": "inventory",
        "title": f"Dispatcher pattern on recurring products: {len(flagged)} pair(s), {len(dispatchers)} dispatcher(s), last {WINDOW_DAYS} days",
        "category": "critical",
        "severity": priority,
        "priority": priority,
        "business_impact_score": business_impact_score,
        "detected": date.today().isoformat(),
        "confirm": {"period": as_of_date, "current": len(flagged), "baseline": None, "pct_change": None, "unit": "count"},
        "breakdowns": [
            _breakdown(flagged, lambda r: r["dispatcher"], "Dispatcher", "person"),
            _breakdown(flagged, lambda r: r["product"], "Product", "product"),
        ],
        "impact": impact,
        "how": None,
        "root_cause": {"checks": checks, "summary": evidence_engine.summarize(checks)},
        "data_quality": data_quality,
        "recommendations": [],
        "recommendation_generation": {"status": "not_requested", "rejected_count": 0},
        "status": "completed",
        "store": None,
        "reason": "Dispatcher pattern",
        "pattern_rows": flagged,
    }
    finding["measurement"] = measurement.observation(
        ISSUE_METRIC_ID, "snapshot", "measured_actionable",
        scope=ISSUE_METRIC_ID, period=as_of_date, freshness="fresh", value=len(flagged),
    )
    return [finding]
