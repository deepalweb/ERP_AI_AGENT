"""Turns a metric's raw findings (intelligence.py) into the narrated Investigation
object the frontend renders as a card: confirm -> where/why -> impact -> recommend.

scan_all() is the "24/7 analyst" entry point — it runs every registered metric
through the same pipeline and returns only the ones worth a human's attention
(category != 'normal'). Nothing here is specific to any one metric; add a new
metric to intelligence.METRICS and it gets investigated the same way.
"""

import json
import os
from datetime import date

from openai import OpenAI

from mcp_server.tools import intelligence

CATEGORY_ORDER = {"critical": 0, "opportunity": 1}
SEVERITY_BY_CATEGORY = {"critical": "high", "opportunity": "positive"}

# Metrics with a verified fulfillment-stage mechanism behind their store
# breakdown (see backend/app/services/operations.py's
# get_stage_timing_by_store) — a HOW stage is only ever attached for these,
# never guessed for a metric with no confirmed mechanism.
HOW_CAPABLE_METRICS = {"delayed_delivery_rate"}


def _estimate_how(metric_id: str, anomaly: dict, breakdowns: list):
    if metric_id not in HOW_CAPABLE_METRICS:
        return None

    store_breakdown = next((b for b in breakdowns if b["dimension"] == "store"), None)
    if not store_breakdown:
        return None

    flagged_stores = [c["name"] for c in store_breakdown["top_contributors"] if c["delta"] > 0][:3]
    if not flagged_stores:
        return None

    # Imported lazily to avoid a module-load-time dependency from
    # mcp_server.tools onto backend.app.services for metrics that never need it.
    from backend.app.services.operations import get_stage_timing_by_store

    return get_stage_timing_by_store(flagged_stores, anomaly["latest_month"])


def _get_client() -> OpenAI:
    api_key = os.getenv("NVIDIA_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("NVIDIA_BASE_URL")
    return OpenAI(api_key=api_key, base_url=base_url)


def _get_model() -> str:
    return os.getenv("OPENAI_MODEL", "nvidia/nemotron-3-super-120b-a12b")


def _headline(metric: dict, anomaly: dict) -> str:
    direction_word = "increased" if anomaly["moved_up"] else "decreased"
    pct = anomaly.get("pct_change_vs_baseline")
    pct_str = f" {abs(pct):.0f}%" if pct is not None else ""
    return f"{metric['label']}{pct_str} {direction_word} vs its usual range"


def _estimate_impact(metric_id: str, trend: list[dict], anomaly: dict):
    """Only defined for rate metrics tagged impact_kind='lost_orders' — translates
    the excess/recovered rate into an order count and a rough revenue figure, priced
    using that same month's revenue/order_volume metrics. Returns None otherwise;
    callers must not fabricate an impact number when this returns None."""
    metric = intelligence.METRICS[metric_id]
    if metric.impact_kind != "lost_orders":
        return None

    latest_month = anomaly.get("latest_month")
    latest_row = next((m for m in trend if m["month"] == latest_month), None)
    if not latest_row or "denominator" not in latest_row:
        return None

    excess_rate_pts = anomaly["latest_value"] - anomaly["baseline_mean"]
    excess_orders = round(excess_rate_pts / 100 * latest_row["denominator"])
    if excess_orders == 0:
        return None

    rev_trend = {m["month"]: m["value"] for m in intelligence.get_trend(intelligence.METRICS["revenue"])}
    vol_trend = {m["month"]: m["value"] for m in intelligence.get_trend(intelligence.METRICS["order_volume"])}
    volume = vol_trend.get(latest_month)
    avg_order_value = (rev_trend.get(latest_month, 0) / volume) if volume else 0

    return {
        "kind": "lost_orders" if excess_orders > 0 else "orders_recovered",
        "orders": abs(excess_orders),
        "estimated_revenue_impact": round(abs(excess_orders) * avg_order_value, 2),
        "avg_order_value_used": round(avg_order_value, 2),
        "month": latest_month,
    }


def _generate_recommendations(metric: dict, anomaly: dict, breakdowns: list, impact):
    """One grounded, non-agentic LLM call: reason over the evidence already gathered
    and propose actions. The model is explicitly told not to introduce facts beyond
    the evidence payload, since these recommendations are shown as trustworthy,
    evidence-backed guidance, not free chat."""
    evidence = {"metric": metric, "anomaly": anomaly, "breakdowns": breakdowns, "impact": impact}
    prompt = (
        "You are a business analyst writing recommendations from evidence only. "
        "Given this investigation evidence as JSON, return a JSON array of 2-4 "
        "recommended actions, most important first. Each item must be exactly: "
        '{"action": string, "owner": string, "priority": "high"|"medium"|"low"}. '
        "'owner' is a plausible responsible team or role (e.g. 'Logistics team', "
        "'Inventory team', 'Operations manager') inferred from which dimension "
        "(store/product/reason/customer) the evidence points at. Name the specific "
        "segments that appear in the breakdowns rather than speaking generically. "
        "IMPORTANT: each entry in `breakdowns` is an INDEPENDENT aggregate over the "
        "whole business — the top store and the top reason were computed separately "
        "and do not mean that reason is what happened at that store. Never combine "
        "a segment from one breakdown with a segment from another (e.g. never say "
        "'address <reason> at <store>') unless the evidence explicitly ties them "
        "together. Write a recommendation about a breakdown's top segment using "
        "only that breakdown's own dimension. Do not invent facts not present in "
        "the evidence. Return ONLY the JSON array, no prose, no markdown fences.\n\n"
        f"Evidence:\n{json.dumps(evidence, default=str)}"
    )
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=_get_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        content = (response.choices[0].message.content or "[]").strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        recommendations = json.loads(content)
        if not isinstance(recommendations, list):
            return []
        return recommendations
    except Exception:
        return []


def build_investigation(metric_id: str):
    result = intelligence.investigate(metric_id)
    if "error" in result:
        return result

    metric, anomaly, trend = result["metric"], result["anomaly"], result["trend"]

    if anomaly["category"] not in ("critical", "opportunity"):
        return {"metric_id": metric_id, "category": anomaly["category"], "status": "no_finding"}

    breakdowns = result["breakdowns"]
    impact = _estimate_impact(metric_id, trend, anomaly)
    recommendations = _generate_recommendations(metric, anomaly, breakdowns, impact)
    how = _estimate_how(metric_id, anomaly, breakdowns)

    return {
        "id": f"{metric_id}-{anomaly['latest_month']}",
        "metric_id": metric_id,
        "title": _headline(metric, anomaly),
        "category": anomaly["category"],
        "severity": SEVERITY_BY_CATEGORY[anomaly["category"]],
        "detected": date.today().isoformat(),
        "confirm": {
            "period": anomaly["latest_month"],
            "current": anomaly["latest_value"],
            "baseline": anomaly["baseline_mean"],
            "pct_change": anomaly["pct_change_vs_baseline"],
            "unit": metric["unit"],
        },
        "breakdowns": breakdowns,
        "impact": impact,
        "how": how,
        "recommendations": recommendations,
        "status": "completed",
    }


def scan_all():
    investigations = []
    for metric_id in intelligence.METRICS:
        inv = build_investigation(metric_id)
        if inv.get("status") == "completed":
            investigations.append(inv)
    investigations.sort(key=lambda i: CATEGORY_ORDER.get(i["category"], 2))
    return investigations
