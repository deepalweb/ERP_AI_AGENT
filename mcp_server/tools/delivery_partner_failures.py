"""Uber/PickMe delivery-partner failure and cancellation rate, as a trackable finding.

User ask, 2026-11-03: "delivery partner performance" as a new area to cover.
Checked live first: `intelligence.py` already defines `uber_failure_rate` and
`pickme_cancellation_rate` as registered MetricDefs (trend SQL, store
breakdown, anomaly thresholds, reviewed) -- they're just never turned into a
case. Today they're only reachable through `scan_attention()`, which powers
Ask Celeste's "what needs my attention" chat answers, not the daily
Investigate scan. This module reuses that existing metric/trend/anomaly
machinery directly rather than inventing a second threshold system -- same
pattern `customer_predictions.py`'s `_second_order_decline_finding()` already
uses for a different registered metric.

Checked which outlets/stores to scope to: `store_id` on both `uber_order` and
`pickme_order` also covers 22 non-Celeste partner-brand outlets (SPAR
co-branded, Budget Kade, Red Bull, Fonterra, Nestle, UStore) -- already
excluded by both MetricDefs' own trend/dimension SQL (see their notes), so
no extra scoping is needed here.

Checked whether there's a reason code for WHY an order failed/cancelled:
there isn't. Uber's `error_message` is a single generic string ("Order
failed in UberEats system") on every failed row; PickMe's status label is
null on every cancelled row. So this can only say "the rate moved", never
why -- same honesty limit as every other detector here.

Verified live, 2026-11-03: both metrics currently read "opportunity" (an
improving trend, not a problem) -- Uber's failure rate fell from a 24.97%
baseline to 1.56% (latest complete month), PickMe's cancellation rate fell
from 5.23% to 2.54%. This detector is not proving out a live incident today,
but the mechanism is real and will fire the same way if either rate rises.
"""

from datetime import date

from mcp_server.tools import intelligence, measurement

_PARTNER_METRICS = (
    ("uber_failure_rate", "Uber"),
    ("pickme_cancellation_rate", "PickMe"),
)


def _partner_rate_finding(metric_id: str, partner_label: str):
    metric = intelligence.METRICS[metric_id]
    trend = intelligence.get_trend(metric)
    anomaly = intelligence.detect_anomaly(trend, metric.direction, metric=metric)
    # The trend/anomaly query itself runs live, right now -- independent of
    # whether the order feed's OWN recency is stale, same reasoning as every
    # other detector fixed earlier this session for this exact bug class.
    measurement.source_as_of(date.today().isoformat())
    if anomaly["category"] not in ("critical", "opportunity"):
        return None

    latest = next(m for m in trend if m["month"] == anomaly["latest_month"])
    moved = "risen" if anomaly["moved_up"] else "fallen"
    persistence = intelligence.classify_persistence(trend, metric.direction)
    breakdowns = [intelligence.get_breakdown(dim, anomaly["latest_month"], anomaly["moved_up"]) for dim in metric.dimensions]

    return {
        "id": f"{metric_id}_change-{anomaly['latest_month']}",
        "metric_id": metric_id,
        "domain": "operations",
        "category": anomaly["category"],
        "priority": "high" if anomaly["category"] == "critical" else "medium",
        "title": f"{metric.label} has {moved} to {anomaly['latest_value']}% in {anomaly['latest_month']}",
        "detected": date.today().isoformat(),
        "metric": {"id": metric_id, "label": metric.label, "unit": metric.unit,
                   "direction": metric.direction, "notes": metric.notes},
        "trend": trend,
        "persistence": persistence,
        "confirm": {
            "period": anomaly["latest_month"], "current": anomaly["latest_value"],
            "baseline": round(anomaly["baseline_mean"], 2), "pct_change": anomaly["pct_change_vs_baseline"],
            "unit": "percent",
        },
        "breakdowns": breakdowns,
        "root_cause": {
            "checks": [],
            "summary": f"{int(latest['numerator'])} of {int(latest['denominator'])} {partner_label} orders "
                      f"{'failed' if partner_label == 'Uber' else 'were cancelled'} in {anomaly['latest_month']} "
                      f"({anomaly['latest_value']}%), vs a {round(anomaly['baseline_mean'], 1)}% prior average. "
                      "No reason code is captured on these orders, so what specifically changed is not established.",
        },
        "impact": {
            "kind": metric_id, "financial_impact_rs": None, "confidence": "measured",
            "detail": f"{int(latest['numerator'])} {partner_label} orders affected in {anomaly['latest_month']}.",
        },
        "recommendations": [],
        "status": "completed",
    }


def _uber_failure_finding():
    return _partner_rate_finding("uber_failure_rate", "Uber")


def _pickme_cancellation_finding():
    return _partner_rate_finding("pickme_cancellation_rate", "PickMe")


def build_delivery_partner_failure_findings() -> measurement.FindingBatch:
    return measurement.combine([
        measurement.run_snapshot("uber_failure_rate", _uber_failure_finding),
        measurement.run_snapshot("pickme_cancellation_rate", _pickme_cancellation_finding),
    ])
