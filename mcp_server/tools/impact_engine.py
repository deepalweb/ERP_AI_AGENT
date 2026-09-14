"""Business Impact Engine (Business Brain component #8): translates a
finding's own deviation into a real financial (Rs) or operational (order/
product count) figure wherever that's honestly derivable, and combines
severity/persistence/impact/confidence into one deterministic ranking
score — supersedes frontend/src/design/findingLifecycle.js's
businessImpactScore(), moved server-side so ranking logic lives in one
place, not split across two languages.

Independent of evidence_engine.py (component #5) on purpose — this module
only knows how to price a deviation, not how to explain it. investigation.py
and state_findings.py are the orchestrators that combine both engines'
output; neither engine imports the other.

compute_impact() only covers what's honestly priceable today: revenue's own
baseline deviation (already Rs, no estimation), order_volume's own
deviation (a raw count, priced via that period's avg order value — the
same math the existing lost_orders metrics already used), and the existing
lost_orders rate metrics (cancellation_rate, uber_failure_rate,
pickme_cancellation_rate) unchanged. Everything else returns None —
delayed_delivery_rate, gross_margin, customer_retention_rate, and the 3
supplier metrics have no verified, non-invented Rs/count translation yet.
Extending coverage further is a real future increment, not something to
guess at here.
"""

from mcp_server.tools import intelligence

CONFIDENCE_MEASURED = "measured"
CONFIDENCE_ESTIMATED = "estimated"

SEVERITY_WEIGHT = {"critical": 3, "high": 2, "medium": 1, "worth_pursuing": 1}


def _avg_order_value(latest_month: str, order_volume: float) -> float | None:
    """Rs per order for a given month, derived from the revenue and
    order_volume metrics' own trends — the same lookup the old
    investigation._estimate_impact used, extracted since both the
    order_volume and lost_orders branches below need it."""
    if not order_volume:
        return None
    rev_trend = {m["month"]: m["value"] for m in intelligence.get_trend(intelligence.METRICS["revenue"])}
    revenue = rev_trend.get(latest_month)
    if revenue is None:
        return None
    return revenue / order_volume


def compute_impact(metric_id: str, metric, anomaly: dict, trend: list[dict], horizon: str = "monthly"):
    """Returns the uniform impact shape below, or None when horizon isn't
    monthly (no non-monthly revenue/order_volume series exists to price a
    trailing-window finding against — same reasoning _estimate_impact
    always used) or when this metric has no verified translation.

    {
        "kind": str,                       # e.g. "revenue_lost", "orders_recovered"
        "financial_impact_rs": float | None,
        "count": int | None,               # orders affected, when applicable
        "avg_order_value_used": float | None,
        "confidence": "measured" | "estimated",
        "month": str,
        "detail": str,                     # pre-written human sentence
    }
    """
    if horizon != "monthly":
        return None
    latest_month = anomaly.get("latest_month")

    if metric_id == "revenue":
        delta = anomaly["latest_value"] - anomaly["baseline_mean"]
        if not delta:
            return None
        lost = delta < 0
        return {
            "kind": "revenue_lost" if lost else "revenue_gained",
            "financial_impact_rs": round(abs(delta), 2),
            "count": None,
            "avg_order_value_used": None,
            "confidence": CONFIDENCE_MEASURED,
            "month": latest_month,
            "detail": f"Rs. {abs(delta):,.0f} {'below' if lost else 'above'} the usual range this month.",
        }

    if metric_id == "order_volume":
        excess_orders = round(anomaly["latest_value"] - anomaly["baseline_mean"])
        if excess_orders == 0:
            return None
        avg_order_value = _avg_order_value(latest_month, anomaly["latest_value"])
        if avg_order_value is None:
            return None
        lost = excess_orders < 0
        financial_impact = round(abs(excess_orders) * avg_order_value, 2)
        return {
            "kind": "orders_lost" if lost else "orders_gained",
            "financial_impact_rs": financial_impact,
            "count": abs(excess_orders),
            "avg_order_value_used": round(avg_order_value, 2),
            "confidence": CONFIDENCE_ESTIMATED,
            "month": latest_month,
            "detail": f"An estimated {abs(excess_orders)} {'fewer' if lost else 'more'} orders than usual "
            f"(~Rs. {financial_impact:,.0f}) this month.",
        }

    if metric.impact_kind == "lost_orders":
        latest_row = next((m for m in trend if m["month"] == latest_month), None)
        if not latest_row or "denominator" not in latest_row:
            return None
        excess_rate_pts = anomaly["latest_value"] - anomaly["baseline_mean"]
        excess_orders = round(excess_rate_pts / 100 * latest_row["denominator"])
        if excess_orders == 0:
            return None
        vol_trend = {m["month"]: m["value"] for m in intelligence.get_trend(intelligence.METRICS["order_volume"])}
        avg_order_value = _avg_order_value(latest_month, vol_trend.get(latest_month))
        if avg_order_value is None:
            return None
        lost = excess_orders > 0
        financial_impact = round(abs(excess_orders) * avg_order_value, 2)
        return {
            "kind": "lost_orders" if lost else "orders_recovered",
            "financial_impact_rs": financial_impact,
            "count": abs(excess_orders),
            "avg_order_value_used": round(avg_order_value, 2),
            "confidence": CONFIDENCE_ESTIMATED,
            "month": latest_month,
            "detail": f"An estimated {abs(excess_orders)} orders {'likely lost' if lost else 'recovered'} "
            f"(~Rs. {financial_impact:,.0f}) this month.",
        }

    return None


def business_impact_score(priority: str, persistence: dict | None, impact, data_quality: dict | None) -> float:
    """Single deterministic 'how important is this' ranking number —
    supersedes the old client-side businessImpactScore()
    (frontend/src/design/findingLifecycle.js), moved server-side per the
    Business Brain architecture's principle that scoring belongs in the
    backend, not scattered client math. Same three factors that function
    already used (severity tier x persistence x whether a real quantified
    impact figure exists) plus one new one: a low_confidence data-quality
    verdict (evidence_engine.py) dampens the score slightly, since a
    finding built on thin history deserves less prominence than an
    equally-severe one backed by solid history."""
    severity_weight = SEVERITY_WEIGHT.get(priority, 1)
    consecutive = (persistence or {}).get("consecutive_periods", 0)
    persistence_multiplier = 1.5 if consecutive >= 3 else 1.25 if consecutive >= 2 else 1.0
    impact_multiplier = 1.25 if impact else 1.0
    confidence_multiplier = 0.85 if (data_quality or {}).get("verdict") == "low_confidence" else 1.0
    return round(severity_weight * persistence_multiplier * impact_multiplier * confidence_multiplier, 3)
