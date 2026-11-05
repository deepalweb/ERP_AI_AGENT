"""Internal return rate, as a trackable finding.

User ask, 2026-11-03: "Returns" as a new area to cover. Checked first:
`loss_and_damage.get_loss_and_damage()` already reports a flat returns
summary (order count, qty, supplier-vs-not split) with no value, no
breakdown, and no case -- never wired into the daily Investigate scan.

Checked the real shape of the data before deciding what to build: the RAW
return count grew ~47x over 6 months (11 orders in April to 520 in
October), but that alone could just be the business growing -- sale_order
volume over the same months only grew ~1.4x. The RATE (returns / orders)
is the real, disproportionate signal: ~0.018% in April to ~0.59% in
October, a ~33x increase. That's `intelligence.py`'s `return_rate`
MetricDef, built the same way as `uber_failure_rate`/`pickme_cancellation_
rate` (see delivery_partner_failures.py for the identical reuse pattern).

`return_order_line.price_subtotal`/`price_unit` are populated on only 353
of 6,324 lines (5.6%) -- not a usable value source, which is why this is a
RATE finding (order counts), not a Rs-value one, unlike damage_writeoff
(which has a real catalog-cost fallback via product_template).
"""

from datetime import date

from mcp_server.tools import intelligence, measurement


def _return_rate_finding():
    metric = intelligence.METRICS["return_rate"]
    trend = intelligence.get_trend(metric)
    anomaly = intelligence.detect_anomaly(trend, metric.direction, metric=metric)
    # Live query against the reporting DB, right now -- independent of
    # whether the order feed's own recency is stale, same reasoning as
    # every other detector fixed earlier this session for this exact bug.
    measurement.source_as_of(date.today().isoformat())
    if anomaly["category"] not in ("critical", "opportunity"):
        return None

    latest = next(m for m in trend if m["month"] == anomaly["latest_month"])
    moved = "risen" if anomaly["moved_up"] else "fallen"
    persistence = intelligence.classify_persistence(trend, metric.direction)
    breakdowns = [intelligence.get_breakdown(dim, anomaly["latest_month"], anomaly["moved_up"]) for dim in metric.dimensions]

    return {
        "id": f"return_rate_change-{anomaly['latest_month']}",
        "metric_id": "return_rate",
        "domain": "operations",
        "category": anomaly["category"],
        "priority": "high" if anomaly["category"] == "critical" else "medium",
        "title": f"{metric.label} has {moved} to {anomaly['latest_value']}% in {anomaly['latest_month']}",
        "detected": date.today().isoformat(),
        "metric": {"id": "return_rate", "label": metric.label, "unit": metric.unit,
                   "direction": metric.direction, "notes": metric.notes},
        "trend": trend,
        "persistence": persistence,
        "confirm": {
            "period": anomaly["latest_month"], "current": anomaly["latest_value"],
            "baseline": round(anomaly["baseline_mean"], 3), "pct_change": anomaly["pct_change_vs_baseline"],
            "unit": "percent",
        },
        "breakdowns": breakdowns,
        "root_cause": {
            "checks": [],
            "summary": f"{int(latest['numerator'])} of {int(latest['denominator'])} orders in "
                      f"{anomaly['latest_month']} were returned ({anomaly['latest_value']}%), vs a "
                      f"{round(anomaly['baseline_mean'], 3)}% prior average. No return reason is captured on "
                      "these records, so what's driving the change is not established.",
        },
        "impact": {
            "kind": "return_rate", "financial_impact_rs": None, "confidence": "measured",
            "detail": f"{int(latest['numerator'])} returned order(s) in {anomaly['latest_month']}.",
        },
        "recommendations": [],
        "status": "completed",
    }


def build_returns_intelligence_findings() -> measurement.FindingBatch:
    return measurement.combine([
        measurement.run_snapshot("return_rate", _return_rate_finding),
    ])
