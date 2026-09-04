"""Explainable Business Health rollup.

Deliberately covers only domains backed by real, trustworthy signals today:
Sales and Operations (from the existing anomaly-detection engine in
intelligence.py), Inventory and Customers (simple, clearly-labeled
current-state heuristics). Financial (profit/margin) and Cash are excluded
outright — intelligence.py already documents that cost data is unreliable
before June 2026, and a health score that quietly folded in a shaky profit
number would be exactly the kind of confident-but-wrong figure this project
has avoided elsewhere (see the partial-month growth fix in sales.py).

Every domain score is a simple, stated rule over real numbers — not an LLM
judgment call — so a low score is always traceable to specific `drivers`.
"""

import anyio

from backend.app.db import reporting
from backend.app.services import dashboard, inventory as inventory_service
from mcp_server.tools import intelligence

SALES_METRICS = ["revenue", "order_volume"]
OPERATIONS_METRICS = ["cancellation_rate", "delayed_delivery_rate"]
CUSTOMER_ANOMALY_METRICS = ["customer_retention_rate"]
CUSTOMER_CONCENTRATION_THRESHOLD_PCT = 40
DEAD_STOCK_VALUE_SHARE_THRESHOLD_PCT = 5
OVERSTOCK_VALUE_SHARE_THRESHOLD_PCT = 25

ALL_TIME_REVENUE_SQL = "SELECT SUM(amount_total) AS revenue FROM sale_order WHERE state = 'sale'"


def _status_for(score: float) -> str:
    if score >= 80:
        return "good"
    if score >= 60:
        return "warning"
    return "critical"


def _anomaly_domain(metric_ids: list[str]):
    score = 100.0
    drivers = []
    for metric_id in metric_ids:
        metric_def = intelligence.METRICS[metric_id]
        result = intelligence.investigate(metric_id)
        anomaly = result["anomaly"]
        category = anomaly.get("category")
        pct = anomaly.get("pct_change_vs_baseline")
        direction = "up" if anomaly.get("moved_up") else "down"

        if category == "critical":
            penalty = min(40, abs(pct)) if pct is not None else 20
            score -= penalty
            pct_str = f" {abs(pct):.1f}%" if pct is not None else ""
            drivers.append(f"{metric_def.label} is{pct_str} {direction} vs its usual range")
        elif category == "opportunity" and pct is not None:
            drivers.append(f"{metric_def.label} is {abs(pct):.1f}% {direction} (opportunity, no penalty)")

    return max(0, round(score)), drivers


async def _inventory_domain():
    inv = await dashboard.get_inventory()
    score = 100.0
    drivers = []

    out_of_stock = inv["out_of_stock_count"]
    if out_of_stock:
        score -= min(50, out_of_stock * 5)
        drivers.append(f"{out_of_stock} product(s) are completely out of stock")

    low_stock = inv["low_stock_count"]
    if low_stock:
        score -= min(20, low_stock * 2)
        drivers.append(f"{low_stock} product(s) are at or below the low-stock threshold ({inv['low_stock_threshold']} units)")

    depth = await inventory_service.get_inventory_depth()
    segment_value = {s["id"]: s["value"] for s in depth["segments"]}
    total_value = sum(segment_value.values())

    if total_value:
        dead_share = segment_value.get("dead_stock", 0) / total_value * 100
        if dead_share > DEAD_STOCK_VALUE_SHARE_THRESHOLD_PCT:
            score -= 10
            drivers.append(f"Dead stock accounts for {dead_share:.1f}% of inventory value — not moving")

        overstock_share = segment_value.get("overstock", 0) / total_value * 100
        if overstock_share > OVERSTOCK_VALUE_SHARE_THRESHOLD_PCT:
            score -= 15
            drivers.append(f"Overstock accounts for {overstock_share:.1f}% of inventory value — cash tied up")

    return max(0, round(score)), drivers


async def _customers_domain():
    top_customers = await dashboard.get_top_customers()
    total_rows = await reporting.fetch_report(ALL_TIME_REVENUE_SQL)
    total_revenue = float((total_rows[0].get("revenue") if total_rows else 0) or 0)

    score = 100.0
    drivers = []

    if total_revenue and top_customers:
        top3_revenue = sum(c["revenue"] for c in top_customers[:3])
        concentration_pct = top3_revenue / total_revenue * 100
        if concentration_pct > CUSTOMER_CONCENTRATION_THRESHOLD_PCT:
            score -= 15
            drivers.append(
                f"Top 3 customers account for {concentration_pct:.0f}% of all-time revenue "
                f"— concentration risk"
            )

    return max(0, round(score)), drivers


async def get_business_health():
    sales_score, sales_drivers = await anyio.to_thread.run_sync(_anomaly_domain, SALES_METRICS)
    ops_score, ops_drivers = await anyio.to_thread.run_sync(_anomaly_domain, OPERATIONS_METRICS)
    inventory_score, inventory_drivers = await _inventory_domain()
    concentration_score, concentration_drivers = await _customers_domain()
    retention_score, retention_drivers = await anyio.to_thread.run_sync(_anomaly_domain, CUSTOMER_ANOMALY_METRICS)
    customers_score = round((concentration_score + retention_score) / 2)
    customers_drivers = concentration_drivers + retention_drivers

    domains = [
        {"id": "sales", "label": "Sales", "score": sales_score, "status": _status_for(sales_score), "drivers": sales_drivers},
        {"id": "operations", "label": "Operations", "score": ops_score, "status": _status_for(ops_score), "drivers": ops_drivers},
        {"id": "inventory", "label": "Inventory", "score": inventory_score, "status": _status_for(inventory_score), "drivers": inventory_drivers},
        {"id": "customers", "label": "Customers", "score": customers_score, "status": _status_for(customers_score), "drivers": customers_drivers},
    ]

    overall = round(sum(d["score"] for d in domains) / len(domains))

    return {
        "overall": overall,
        "status": _status_for(overall),
        "domains": domains,
        "excluded": [
            {
                "label": "Financial (profit/margin) & Cash",
                "reason": "Cost data is only ~66% populated and unreliable before June 2026 — "
                "shown once that data is verified complete, not before.",
            }
        ],
    }
