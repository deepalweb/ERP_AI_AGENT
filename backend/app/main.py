import logging

import anyio
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

from mcp_server.tools import intelligence, investigation, state_findings
from .db import reporting
from .db.store_scope import CELESTE_STORE_NAMES
from .services import (
    agent,
    basket,
    brain,
    brand_mix,
    channels,
    concentration,
    customer_predictions,
    customers,
    dashboard,
    finding_actions,
    forecast,
    inventory,
    loss_and_damage,
    loyalty,
    notifications,
    operations,
    stock_alerts,
    tracking,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="ERP AI Agent API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

scheduler = BackgroundScheduler()


@app.on_event("startup")
def start_scheduler():
    """Runs the same scan_all() + tracking pipeline /investigations runs on
    request, once a day, so the morning brief (see /brief) is precomputed
    instead of making whoever opens the dashboard first eat an 80-100s
    anomaly-detection pass. See backend/app/services/brain.py."""
    scheduler.add_job(brain.run_daily_scan, "cron", hour=5, minute=0, id="daily_brief_scan")
    scheduler.start()


@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown(wait=False)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class FindingActionRequest(BaseModel):
    relevant: bool | None
    status: str
    action_taken: str | None
    owner: str | None = None


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "erp-ai-agent",
    }


@app.get("/")
async def root():
    return {
        "message": "ERP AI Agent API",
        "docs": "/docs",
    }


@app.get("/reporting/health")
async def reporting_health():
    """Check the reporting DB by running a simple SELECT 1."""
    try:
        rows = await reporting.fetch_report("SELECT 1")
        return {"status": "ok", "rows": rows}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.get("/reporting/schema")
async def reporting_schema():
    """Return basic schema introspection from the reporting DB."""
    try:
        schema = await reporting.fetch_schema()
        return {"status": "ok", "schema": schema}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.get("/store-scope")
async def store_scope():
    """The fixed list of outlets every sales/operations number in this app is
    scoped to — see backend/app/db/store_scope.py for why."""
    return {"status": "ok", "stores": CELESTE_STORE_NAMES}


@app.get("/dashboard/summary")
async def dashboard_summary():
    """Aggregate the KPIs and chart data behind the business dashboard."""
    try:
        return {"status": "ok", **await dashboard.get_summary()}
    except Exception:
        logger.exception("dashboard summary failed")
        return {"status": "error", "detail": "Could not load dashboard data."}


@app.get("/dashboard/orders-by-hour")
async def dashboard_orders_by_hour():
    """All-time hour-of-day order distribution — see
    backend/app/services/dashboard.py's get_orders_by_hour()."""
    try:
        return {"status": "ok", **await dashboard.get_orders_by_hour()}
    except Exception:
        logger.exception("orders by hour failed")
        return {"status": "error", "detail": "Could not load hourly order data."}


@app.get("/channels/breakdown")
async def channels_breakdown(period: str | None = None):
    """Direct vs. Uber vs. PickMe order/revenue split — see
    backend/app/services/channels.py. Reused as-is from the chat agent's
    existing MCP tool; this just exposes it as a REST route too. Accepts
    the same "YYYY-MM" period strings dashboard.py's headline_month uses —
    without one, defaults to the current (often mostly-empty) partial
    month, which skews a "top channel" read."""
    try:
        result = await anyio.to_thread.run_sync(channels.get_channel_breakdown, period)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("channel breakdown failed")
        return {"status": "error", "detail": "Could not load channel breakdown."}


@app.get("/operations/summary")
async def operations_summary():
    """Fulfillment & delivery timing rollup — headline stats, monthly trend,
    and per-branch breakdown. See backend/app/services/operations.py."""
    try:
        return {"status": "ok", **await operations.get_fulfillment_summary()}
    except Exception:
        logger.exception("operations summary failed")
        return {"status": "error", "detail": "Could not load operations data."}


@app.get("/customers/intelligence")
async def customers_intelligence():
    """Repeat-purchase segmentation, at-risk customers, and the retention trend —
    see backend/app/services/customers.py."""
    try:
        return {"status": "ok", **await customers.get_customer_intelligence()}
    except Exception:
        logger.exception("customer intelligence failed")
        return {"status": "error", "detail": "Could not load customer intelligence."}


@app.get("/customers/predictions/summary")
async def customer_predictions_summary():
    """Rollup for the Customer Next-Order Intelligence page — counts and
    revenue on track / due / overdue, plus confidence-tier breakdown. Reads
    the once-daily cached snapshot (brain.get_latest_brief also runs
    customer_predictions.record_predictions as part of the same scan, self-
    healing if the cache is missing/stale) — see
    backend/app/services/customer_predictions.py."""
    try:
        await anyio.to_thread.run_sync(brain.get_latest_brief)
        result = await anyio.to_thread.run_sync(customer_predictions.get_summary)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("customer predictions summary failed")
        return {"status": "error", "detail": "Could not load customer predictions."}


@app.get("/customers/predictions")
async def customer_predictions_list(status: str | None = None, sort: str = "revenue", limit: int = 50, offset: int = 0):
    """Paginated, filterable, sortable list of per-customer next-order
    predictions — see backend/app/services/customer_predictions.py."""
    try:
        await anyio.to_thread.run_sync(brain.get_latest_brief)
        rows, total = await anyio.to_thread.run_sync(
            customer_predictions.list_predictions, status, sort, limit, offset
        )
        return {"status": "ok", "total": total, "results": rows}
    except Exception:
        logger.exception("customer predictions list failed")
        return {"status": "error", "detail": "Could not load customer predictions."}


@app.get("/customers/predictions/tomorrow")
async def customer_predictions_tomorrow():
    """Bottom-up order-count forecast for tomorrow, built from each
    predictable customer's own expected window (not a trend projection —
    see forecast.py for that) — see
    backend/app/services/customer_predictions.py get_tomorrow_forecast()."""
    try:
        result = await anyio.to_thread.run_sync(customer_predictions.get_tomorrow_forecast)
        if result is None:
            return {"status": "error", "detail": "No prediction data available yet."}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("customer predictions tomorrow forecast failed")
        return {"status": "error", "detail": "Could not compute tomorrow's forecast."}


@app.get("/customers/predictions/daily-forecast")
async def customer_predictions_daily_forecast(days: int = 14):
    """Day-by-day order-count + value forecast, built from each predictable
    customer's own expected window (not a trend projection) — see
    backend/app/services/customer_predictions.py get_daily_forecast()."""
    try:
        result = await anyio.to_thread.run_sync(customer_predictions.get_daily_forecast, days)
        return {"status": "ok", "days": result}
    except Exception:
        logger.exception("customer predictions daily forecast failed")
        return {"status": "error", "detail": "Could not compute the daily forecast."}


@app.get("/customers/predictions/order-forecast-chart")
async def customer_predictions_order_forecast_chart(actual_days: int = 5, forecast_days: int = 5):
    """Actual daily order counts transitioning into the Tier-2 forecast,
    plus an approximate stock-coverage overlay (current on-hand held flat,
    required = order count x trailing avg units/order) — see
    backend/app/services/customer_predictions.py get_order_forecast_chart()."""
    try:
        await anyio.to_thread.run_sync(brain.get_latest_brief)
        result = await anyio.to_thread.run_sync(
            customer_predictions.get_order_forecast_chart, actual_days, forecast_days
        )
        if result is None:
            return {"status": "error", "detail": "No prediction data available yet."}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("customer predictions order forecast chart failed")
        return {"status": "error", "detail": "Could not compute the order forecast chart."}


@app.get("/inventory/stock-alerts")
async def stock_alerts_route():
    """Which actively-selling products are out of stock or critically low at
    each of Celeste's 5 outlets right now — see
    backend/app/services/stock_alerts.py get_stock_alerts()."""
    try:
        result = await anyio.to_thread.run_sync(stock_alerts.get_stock_alerts)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("stock alerts failed")
        return {"status": "error", "detail": "Could not load stock alerts."}


@app.get("/inventory/top-product-stock-coverage")
async def top_product_stock_coverage(top_n: int = 15, horizon_days: int = 10):
    """Top-selling products (by units sold, trailing 30 days) with each
    one's own stock checked against its own sales rate — a company-wide
    stock total can't answer "will we run out," since it pools every SKU
    together. See backend/app/services/stock_alerts.py
    get_top_product_stock_coverage()."""
    try:
        result = await anyio.to_thread.run_sync(
            stock_alerts.get_top_product_stock_coverage, top_n, horizon_days
        )
        return {"status": "ok", **result}
    except Exception:
        logger.exception("top product stock coverage failed")
        return {"status": "error", "detail": "Could not load top-product stock coverage."}


@app.get("/customers/predictions/outlet-forecast")
async def customer_predictions_outlet_forecast(forecast_days: int = 5):
    """Company-wide daily order forecast split across Celeste's 5 outlets
    by each outlet's real trailing-30-day share of order volume — see
    backend/app/services/customer_predictions.py get_outlet_order_forecast()."""
    try:
        await anyio.to_thread.run_sync(brain.get_latest_brief)
        result = await anyio.to_thread.run_sync(customer_predictions.get_outlet_order_forecast, forecast_days)
        if result is None:
            return {"status": "error", "detail": "No prediction data available yet."}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("customer predictions outlet forecast failed")
        return {"status": "error", "detail": "Could not compute the outlet-wise order forecast."}


@app.get("/customers/search")
async def customer_search(name: str | None = None, phone: str | None = None, email: str | None = None):
    """Find customers by any combination of name/phone/email — see
    backend/app/services/customer_predictions.py search_customers(). Email
    matches almost never find anything (17 of 84,896 customers have one on
    file) but is still accepted for whoever tries it."""
    try:
        results = await anyio.to_thread.run_sync(customer_predictions.search_customers, name, phone, email)
        return {"status": "ok", "results": results}
    except Exception:
        logger.exception("customer search failed")
        return {"status": "error", "detail": "Could not search customers."}


@app.get("/customers/{partner_id}/next-basket")
async def customer_next_basket(partner_id: int, top_n: int = 8):
    """Which of this customer's own repeat products are due for reorder,
    ranked most-overdue first — see
    backend/app/services/customer_predictions.py get_predicted_next_basket()."""
    try:
        result = await anyio.to_thread.run_sync(customer_predictions.get_predicted_next_basket, partner_id, top_n)
        if result is None:
            return {"status": "error", "detail": "No prediction data available yet."}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("customer next basket failed")
        return {"status": "error", "detail": "Could not compute the next-basket prediction."}


@app.get("/customers/lifecycle")
async def customer_lifecycle():
    """Every customer classified into one of 8 lifecycle stages (New,
    Active, Due/At Risk, Lapsing, Churned, Win-back, Dormant, Excluded)
    plus an independent High/Mid/Low value tier — see
    backend/app/services/customer_predictions.py compute_lifecycle_summary().
    Reads the once-daily cached snapshot, self-healing via brain.get_latest_brief
    if it's missing/stale, same as the rest of this module's routes."""
    try:
        await anyio.to_thread.run_sync(brain.get_latest_brief)
        result = await anyio.to_thread.run_sync(customer_predictions.get_lifecycle_summary)
        if result is None:
            return {"status": "error", "detail": "No lifecycle data available yet."}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("customer lifecycle summary failed")
        return {"status": "error", "detail": "Could not load customer lifecycle data."}


@app.get("/customers/outlet-behavior")
async def customer_outlet_behavior():
    """Per-outlet customer behavior (orders, cancellations, unique
    customers, orders per customer) for Celeste's 5 real retail outlets —
    computed live, not part of the daily prediction cache. See
    backend/app/services/customer_predictions.py."""
    try:
        result = await anyio.to_thread.run_sync(customer_predictions.get_outlet_behavior)
        return {"status": "ok", "outlets": result}
    except Exception:
        logger.exception("customer outlet behavior failed")
        return {"status": "error", "detail": "Could not load outlet-wise customer behavior."}


@app.get("/inventory/depth")
async def inventory_depth():
    """Days-of-stock, overstock, and dead-stock segmentation — see
    backend/app/services/inventory.py."""
    try:
        return {"status": "ok", **await inventory.get_inventory_depth()}
    except Exception:
        logger.exception("inventory depth failed")
        return {"status": "error", "detail": "Could not load inventory depth."}


@app.get("/forecast/forward-look")
async def forward_look():
    """A simple, explicitly-caveated trend projection for revenue and orders —
    see backend/app/services/forecast.py for why this is a range, not a point
    estimate, and why it's not called a 'forecast'."""
    try:
        return {"status": "ok", **await forecast.get_forward_look()}
    except Exception:
        logger.exception("forward look failed")
        return {"status": "error", "detail": "Could not compute the forward look."}


@app.get("/operations/loss-and-damage")
async def loss_and_damage_summary():
    """Picking-shortage, damaged-goods, and stock-return signal for the last
    complete month — see backend/app/services/loss_and_damage.py."""
    try:
        result = await anyio.to_thread.run_sync(loss_and_damage.get_loss_and_damage)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("loss and damage summary failed")
        return {"status": "error", "detail": "Could not load loss and damage data."}


@app.get("/customers/loyalty")
async def loyalty_summary():
    """Loyalty-points activity and active store promotions for the last
    complete month — see backend/app/services/loyalty.py."""
    try:
        result = await anyio.to_thread.run_sync(loyalty.get_loyalty_and_promotions)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("loyalty summary failed")
        return {"status": "error", "detail": "Could not load loyalty data."}


@app.get("/products/brand-mix")
async def brand_mix_summary():
    """Revenue split by brand and department for the last complete month —
    see backend/app/services/brand_mix.py."""
    try:
        result = await anyio.to_thread.run_sync(brand_mix.get_brand_and_department_mix)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("brand mix summary failed")
        return {"status": "error", "detail": "Could not load brand mix data."}


@app.get("/products/basket-analysis")
async def basket_analysis_summary():
    """Market-basket co-purchase analysis for the last complete month — see
    backend/app/services/basket.py. Slower than the other endpoints (a
    heavier query, several seconds)."""
    try:
        result = await anyio.to_thread.run_sync(basket.get_basket_analysis)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("basket analysis summary failed")
        return {"status": "error", "detail": "Could not load basket analysis data."}


@app.get("/products/concentration")
async def concentration_summary():
    """Revenue concentration in the top 10 products/customers for the last
    complete month — see backend/app/services/concentration.py."""
    try:
        result = await anyio.to_thread.run_sync(concentration.get_revenue_concentration)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("concentration summary failed")
        return {"status": "error", "detail": "Could not load concentration data."}


@app.get("/intelligence/attention")
async def intelligence_attention():
    """Cross-metric anomaly scan — every registered metric's latest complete
    month, flagged if critical/opportunity — see intelligence.scan_attention."""
    try:
        result = await anyio.to_thread.run_sync(intelligence.scan_attention)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("attention scan failed")
        return {"status": "error", "detail": "Could not run the attention scan."}


@app.get("/intelligence/health")
async def intelligence_health():
    """Explainable Business Health rollup across Sales, Operations, Inventory,
    and Customers — see backend/app/services/health.py for the scoring rule.
    Reads the same once-daily cached snapshot /brief does (see brain.py)
    instead of computing live: this used to take 35s on every request, and
    now both routes read one consistent, delta-annotated "as of last night's
    scan" snapshot instead of paying that cost per page load."""
    try:
        result = await anyio.to_thread.run_sync(brain.get_latest_brief)
        return {"status": "ok", **result["health"]}
    except Exception:
        logger.exception("business health rollup failed")
        return {"status": "error", "detail": "Could not compute business health."}


@app.get("/intelligence/metrics")
async def intelligence_metrics():
    """List the metrics the intelligence engine can investigate."""
    return {"status": "ok", "metrics": intelligence.list_metrics()}


@app.get("/intelligence/investigate/{metric_id}")
async def intelligence_investigate(metric_id: str):
    """Run the full trend + anomaly + contributor-breakdown pipeline for one metric."""
    try:
        result = await anyio.to_thread.run_sync(intelligence.investigate, metric_id)
        if "error" in result:
            return {"status": "error", "detail": result["error"]}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("investigate_metric failed for %s", metric_id)
        return {"status": "error", "detail": "Could not run the investigation."}


def _attach_actions(findings: list[dict], actions: dict[str, dict]) -> None:
    for f in findings:
        f["action"] = actions.get(f["id"], finding_actions.DEFAULT_ACTION)


@app.get("/brief")
async def daily_brief():
    """The precomputed morning brief: the same finding + tracking data
    /investigations computes live, but read from the daily cache (see
    backend/app/services/brain.py) instead of re-running the 80-100s scan on
    every request. Self-heals (computes once, inline) if the cache is empty
    or older than 24h — e.g. the first request ever, or a missed scheduled
    run.

    Each finding also gets a manager-set `action` (relevant/status/
    action_taken — see finding_actions.py) merged in fresh on every request,
    never baked into the daily_brief cache: action state changes far more
    often than the once-a-day scan, so it has to be read live for an update
    to show up immediately instead of waiting for tomorrow's scan."""
    try:
        result = await anyio.to_thread.run_sync(brain.get_latest_brief)
        all_findings = [*result["investigations"], *result["state_findings"], *result["short_term_findings"]]
        actions = await anyio.to_thread.run_sync(finding_actions.get_actions_for, [f["id"] for f in all_findings])
        _attach_actions(all_findings, actions)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("daily brief fetch failed")
        return {"status": "error", "detail": "Could not load the daily brief."}


@app.get("/notifications/summary")
async def notifications_summary():
    """Proactive Warning: count + list of critical findings that are new
    since the last time someone acknowledged (opened Home) — see
    notifications.py. Cheap: reads the small notification_state row plus
    whichever daily_brief rows are newer than it, not a fresh scan."""
    try:
        result = await anyio.to_thread.run_sync(notifications.get_summary)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("notifications summary failed")
        return {"status": "error", "detail": "Could not load notifications."}


@app.post("/notifications/acknowledge")
async def notifications_acknowledge():
    """Marks everything seen up to now — called when Home loads."""
    try:
        acknowledged_at = await anyio.to_thread.run_sync(notifications.acknowledge)
        return {"status": "ok", "last_acknowledged_at": acknowledged_at}
    except Exception:
        logger.exception("notifications acknowledge failed")
        return {"status": "error", "detail": "Could not acknowledge notifications."}


@app.patch("/findings/{finding_id}/action")
async def update_finding_action(finding_id: str, request: FindingActionRequest):
    """Manager-facing action tracking, separate from tracking.py's automatic
    outcome verdicts — see finding_actions.py."""
    try:
        await anyio.to_thread.run_sync(
            finding_actions.set_action, finding_id, request.relevant, request.status, request.action_taken, request.owner
        )
        return {"status": "ok"}
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    except Exception:
        logger.exception("finding action update failed for %s", finding_id)
        return {"status": "error", "detail": "Could not save this action."}


@app.get("/investigations")
async def investigations():
    """Scan every registered metric and return a narrated Investigation for each
    one currently outside its normal range — the 24/7-analyst feed. Also records
    each finding for outcome tracking and checks whether older ones have
    resolved (see backend/app/services/tracking.py — the "AI Impact" loop)."""
    try:
        result = await anyio.to_thread.run_sync(investigation.scan_all)
        state = await anyio.to_thread.run_sync(state_findings.build_inventory_findings)
        short_term = await anyio.to_thread.run_sync(investigation.scan_short_term)
        await anyio.to_thread.run_sync(tracking.record_new_findings, result)
        await anyio.to_thread.run_sync(tracking.check_outcomes)
        history = await anyio.to_thread.run_sync(tracking.get_tracking_history)
        return {
            "status": "ok",
            "investigations": result,
            "tracking": history,
            "state_findings": state,
            "short_term_findings": short_term,
        }
    except Exception:
        logger.exception("investigation scan failed")
        return {"status": "error", "detail": "Could not run investigations."}


@app.post("/agent/chat")
async def agent_chat(request: ChatRequest):
    """Run one agent turn: send the conversation to the model, execute any
    requested ERP tools, and return the final reply."""
    try:
        messages = [m.model_dump() for m in request.messages]
        return await agent.run_agent_turn(messages)
    except Exception:
        logger.exception("agent chat turn failed")
        return {"status": "error", "detail": "The agent failed to process this request."}
