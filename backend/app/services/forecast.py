"""Forward Look: a simple, explicitly-caveated trend projection for revenue and
orders — not a forecast. No ML, no seasonality modeling (there isn't enough
history — only ~6 months — to detect a real seasonal pattern).

Projects a range, not a point estimate, from the last STABLE_MONTHS
month-over-month growth rates. A rolling window rather than a hardcoded
"skip the ramp-up months" rule: it naturally excludes this business's early
300-500%+ ramp-up growth today, and stays correct as new months arrive without
needing to be revisited.
"""

import anyio

from mcp_server.tools import intelligence

STABLE_MONTHS = 3


def _project(trend: list[dict]):
    complete = [row for row in trend if not row["is_partial"]]
    if len(complete) < STABLE_MONTHS + 1:
        return None  # not enough history for even a rough range

    recent = complete[-(STABLE_MONTHS + 1):]
    growths = []
    for prev, cur in zip(recent, recent[1:]):
        if prev["value"]:
            growths.append((cur["value"] - prev["value"]) / prev["value"] * 100)
    if not growths:
        return None

    based_on = complete[-1]
    avg_growth = sum(growths) / len(growths)
    return {
        "based_on_month": based_on["month"],
        "based_on_value": round(based_on["value"], 2),
        "recent_growth_rates_pct": [round(g, 1) for g in growths],
        "projected_low": round(based_on["value"] * (1 + min(growths) / 100), 2),
        "projected_mid": round(based_on["value"] * (1 + avg_growth / 100), 2),
        "projected_high": round(based_on["value"] * (1 + max(growths) / 100), 2),
    }


def _round_orders(projection):
    if not projection:
        return projection
    for key in ("based_on_value", "projected_low", "projected_mid", "projected_high"):
        projection[key] = round(projection[key])
    return projection


async def get_forward_look():
    revenue_trend = await anyio.to_thread.run_sync(intelligence.get_trend, intelligence.METRICS["revenue"])
    order_trend = await anyio.to_thread.run_sync(intelligence.get_trend, intelligence.METRICS["order_volume"])
    return {
        "revenue": _project(revenue_trend),
        "orders": _round_orders(_project(order_trend)),
        "stable_months_used": STABLE_MONTHS,
        "note": (
            "Simple trend projection from the most recent "
            f"{STABLE_MONTHS} month-over-month growth rates only — not a "
            "statistical forecast. Doesn't account for seasonality, "
            "promotions, or one-off events, and this business's growth rate "
            "has swung from single digits to 500%+ during its early ramp-up, "
            "so treat this as a rough guide, not a commitment."
        ),
    }
