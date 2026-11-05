"""Forward Look: a simple, explicitly-caveated trend projection for revenue and
orders — not a forecast. No ML, no seasonality modeling (there isn't enough
history — only ~6 months — to detect a real seasonal pattern).

Projects a range, not a point estimate, from the last STABLE_MONTHS
month-over-month growth rates. A rolling window rather than a hardcoded
"skip the ramp-up months" rule: it naturally excludes this business's early
300-500%+ ramp-up growth today, and stays correct as new months arrive without
needing to be revisited.

get_outlet_order_forecast() below is the actual ML forecast: see
docs/outlet-forecast-evaluation.md for why TimesFM 2.5 was selected and
analytics/forecast_outlet_orders.py for how the artifact it reads is
produced (run in the isolated .venv-forecast environment -- this module
only ever reads a JSON file, never imports torch/timesfm itself).
"""

import calendar
import json
from pathlib import Path

import anyio

from backend.app.db import reporting
from backend.app.db.store_scope import (
    CELESTE_STORE_IDS,
    CELESTE_STORE_NAMES,
    SALE_ORDER_LIVE_STATES,
)
from backend.app.services import forecast_outcomes
from mcp_server.tools import intelligence

STABLE_MONTHS = 3
OUTLET_FORECAST_PATH = Path("artifacts/forecasting/outlet_forecast.json")
_OUTLET_NAMES = dict(zip((str(s) for s in CELESTE_STORE_IDS), CELESTE_STORE_NAMES, strict=True))

# Measured accuracy from docs/outlet-forecast-evaluation.md (holdout run,
# 2026-09-18) -- static numbers from that offline evaluation, not
# recomputed live. Surfaced so "not guaranteed accuracy" has a real figure
# behind it instead of only a link to the doc.
HOLDOUT_ACCURACY = {
    "evaluated_at": "2026-09-18",
    "overall": {"wape_pct": 7.48, "bias_pct": -4.05, "coverage_80_pct": 76.79},
    "by_store": {
        1: {"wape_pct": 5.59, "bias_pct": -1.73, "coverage_80_pct": 85.71},  # Colombo 6
        2: {"wape_pct": 8.65, "bias_pct": -3.16, "coverage_80_pct": 85.71},  # Pannipitiya
        45: {"wape_pct": 9.03, "bias_pct": -6.55, "coverage_80_pct": 64.29},  # Attidiya
        46: {"wape_pct": 7.20, "bias_pct": -4.39, "coverage_80_pct": 71.43},  # Ethul Kotte
    },
}


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


def _latest_order_date():
    rows = reporting.run_query(
        f"SELECT MAX(date_order) AS latest FROM sale_order WHERE store_id IN {CELESTE_STORE_IDS} "
        f"AND state IN {SALE_ORDER_LIVE_STATES}"
    )
    latest = rows[0]["latest"] if rows else None
    return latest.date() if latest else None


def _unpartial_month_end(trend, latest_order_date):
    """intelligence.get_trend() marks the newest row `is_partial` purely by
    its position (the last row), with no idea whether that calendar month's
    OWN last day was actually reached -- correct caution for a month cut off
    mid-month, wrong for one like October 2026, where the order feed covers
    all 31 days and simply stops a few hours short of midnight on the 31st.
    User report, 2026-11-04: "we have october month data" -- confirmed live,
    30 of 31 October days are fully captured (Oct 31 itself undercounts by
    about a third, since the feed stops at 17:34 local that day). That one
    partial day dilutes a ~31-day total by roughly 1% -- small enough to
    accept rather than build a same-elapsed-time pro-rating correction for.

    Only flips `is_partial` when the latest observed order's day-of-month IS
    the calendar month's last day -- a month genuinely missing its closing
    days (the feed stopped on the 20th, say) is never affected."""
    if not trend or latest_order_date is None:
        return trend
    last_day_of_month = calendar.monthrange(latest_order_date.year, latest_order_date.month)[1]
    if latest_order_date.day == last_day_of_month and trend[-1]["is_partial"]:
        trend[-1] = {**trend[-1], "is_partial": False}
    return trend


def _history_tail(trend, months=6):
    """The last `months` fully-observed months, for drawing a trend line
    behind the single projected number -- a bare "Rs 207.5M -> next month"
    figure has no sense of whether that's a climb, a plateau, or a dip."""
    complete = [row for row in trend if not row["is_partial"]]
    return [{"month": row["month"], "value": round(row["value"], 2)} for row in complete[-months:]]


def _round_orders(projection):
    if not projection:
        return projection
    for key in ("based_on_value", "projected_low", "projected_mid", "projected_high"):
        projection[key] = round(projection[key])
    return projection


async def get_forward_look():
    revenue_trend = await anyio.to_thread.run_sync(intelligence.get_trend, intelligence.METRICS["revenue"])
    order_trend = await anyio.to_thread.run_sync(intelligence.get_trend, intelligence.METRICS["order_volume"])
    latest_order_date = await anyio.to_thread.run_sync(_latest_order_date)
    revenue_trend = _unpartial_month_end(revenue_trend, latest_order_date)
    order_trend = _unpartial_month_end(order_trend, latest_order_date)
    return {
        "revenue": _project(revenue_trend),
        "orders": _round_orders(_project(order_trend)),
        "revenue_history": _history_tail(revenue_trend),
        "orders_history": [{**row, "value": round(row["value"])} for row in _history_tail(order_trend)],
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


def get_outlet_order_forecast():
    """Reads the latest TimesFM 2.5 outlet order-count forecast artifact --
    never runs the model inline (this process has no torch/timesfm
    installed, by design; see analytics/forecast_outlet_orders.py's module
    docstring). Returns None if no artifact has been generated yet.

    `is_stale` is carried through from the artifact unchanged, not
    re-derived here, so the one-day freshness gate is defined in exactly one
    place (analytics/outlet_orders.py's PROTOCOL) -- see
    docs/outlet-forecast-evaluation.md: "A model pass is not an operational
    deployment approval or a claim that the data is current." The frontend
    must show this as a historical forecast made from several-day-old data,
    never as today's prediction, whenever is_stale is true.
    """
    if not OUTLET_FORECAST_PATH.exists():
        return None
    raw = json.loads(OUTLET_FORECAST_PATH.read_text(encoding="utf-8"))
    return {
        "generated_at": raw["generated_at"],
        "horizon_days": raw["horizon_days"],
        "source": raw["source"],
        "model": raw["model"],
        "accuracy": {**HOLDOUT_ACCURACY, "live_track_record": forecast_outcomes.get_track_record()},
        "outlets": [
            {
                "store_id": int(outlet_id),
                "outlet": _OUTLET_NAMES.get(outlet_id, f"Store {outlet_id}"),
                "history_tail": f["history_tail"],
                "forecast_dates": f["forecast_dates"],
                "point": f["point"],
                "q10": f["q10"],
                "q90": f["q90"],
                "accuracy": HOLDOUT_ACCURACY["by_store"].get(int(outlet_id)),
            }
            for outlet_id, f in raw["outlets"].items()
        ],
    }
