import json
import logging
import os

import anyio
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

from mcp_server.tools import current_period, intelligence, state_findings, transfer_accountability
from .core.access import AccessControlMiddleware, auth_mode
from .db import reporting
from .db.store_scope import CELESTE_STORE_NAMES
from .services import (
    agent,
    basket,
    brain,
    brand_mix,
    case_engine,
    channels,
    concentration,
    customer_lookup,
    customer_predictions,
    customers,
    dashboard,
    finding_actions,
    forecast,
    home_briefing,
    inventory,
    inventory_kpi_history,
    loss_and_damage,
    loyalty,
    management,
    notifications,
    operations,
    outcome_intelligence,
    stock_alerts,
    stock_counts,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="ERP AI Agent API",
    version="0.1.0",
)

app.add_middleware(AccessControlMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

scheduler = BackgroundScheduler(timezone=os.getenv("APP_TIMEZONE", "Asia/Colombo"))


@app.on_event("startup")
def start_scheduler():
    """Runs the same scan_all() + tracking pipeline /investigations runs on
    request, once a day, so the morning brief (see /brief) is precomputed
    instead of making whoever opens the dashboard first eat an 80-100s
    anomaly-detection pass. See backend/app/services/brain.py."""
    if os.getenv("ENABLE_SCHEDULER", "true").lower() == "true":
        scheduler.add_job(brain.run_daily_scan, "cron", hour=5, minute=0, id="daily_brief_scan")
        scheduler.start()


@app.on_event("shutdown")
def stop_scheduler():
    if scheduler.running:
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
    deadline: str | None = None
    # Optional and new (Phase 2b) -- the existing "Track this" form never
    # sends this, and set_action's COALESCE-on-conflict means omitting it
    # preserves whatever a case-aware caller set earlier rather than
    # wiping it back to null on every unrelated edit.
    action_kind: str | None = None
    # Phase 2d additions -- all optional/additive, same COALESCE posture.
    intended_result: str | None = None
    target_metric_id: str | None = None
    target_value: float | None = None
    target_comparison: str | None = None
    target_baseline_relative: bool = False
    # "monthly" (default) or "weekly" -- validated against the target
    # metric's own trend support server-side (finding_actions.set_action),
    # never trusted as-is (Phase 2e Task 4).
    target_measurement_period: str | None = None
    # The opaque version token (case_engine._observation_version --
    # "<observation row id>:<revision>", never a timestamp) of the
    # evidence this save is being made against -- omitted by any caller
    # that doesn't track evidence versions (skips the server-side
    # freshness check entirely); sent by CaseDetail.jsx after its own
    # client-side check already passed, as the authoritative reassertion
    # (see case_engine.verify_evidence_version). Not a timestamp because a
    # timestamp isn't a reliable version identifier -- see
    # case_engine._observation_version's docstring.
    evidence_version: str | None = None


class RecommendationAcceptanceRequest(BaseModel):
    """Celeste Investigate Phase 2, final correction: accepting a
    recommendation is deliberately NOT a FindingActionRequest -- it never
    touches relevant/status/action_taken/owner/deadline at all, so it
    can't accidentally create the "phantom" blank finding_actions row the
    earlier revision did (see case_engine.accept_recommendation's own
    docstring). recommendation_action is matched server-side against the
    finding's real, current recommendations -- never trusted as an
    arbitrary client string beyond that match."""
    recommendation_action: str
    evidence_version: str | None = None


class CaseObjectiveRequest(BaseModel):
    metric_id: str
    target_value: float
    comparison: str
    baseline_relative: bool = False


class CaseObjectiveFromActionRequest(BaseModel):
    finding_id: str


class CaseReasonRequest(BaseModel):
    reason: str


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "erp-ai-agent",
    }


@app.get("/auth/status")
async def access_status():
    return {"status": "ok", "mode": auth_mode()}


@app.get("/auth/session")
async def access_session():
    # Reaching this route means the access middleware accepted the request.
    return {"status": "ok"}


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


@app.get("/performance/summary")
async def performance_summary(month: str | None = None, store_id: int | None = None):
    from backend.app.services import performance
    try:
        performance.validate_filters(month, store_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return {"status": "ok", **await performance.get_performance(month, store_id)}
    except Exception:
        logger.exception("performance summary failed")
        return {"status": "error", "detail": "Could not load performance comparisons."}


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


@reporting.ttl_cache(seconds=300)
def _stock_alerts_cached():
    # Phase 5A: cached 5 minutes here, at the route layer -- Home's Inventory
    # panel is a pure supplementary display. state_findings.py and
    # evidence_engine.py call stock_alerts.get_stock_alerts() directly
    # (uncached) as part of scan_all()'s real detection pipeline.
    return stock_alerts.get_stock_alerts()


@app.get("/inventory/stock-alerts")
async def stock_alerts_route():
    """Which actively-selling products are out of stock or critically low at
    each of Celeste's 5 outlets right now — see
    backend/app/services/stock_alerts.py get_stock_alerts()."""
    try:
        result = await anyio.to_thread.run_sync(_stock_alerts_cached)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("stock alerts failed")
        return {"status": "error", "detail": "Could not load stock alerts."}


@app.get("/inventory/stuck-stock-counts")
async def stuck_stock_counts_route():
    """Physical stock counts started but never finished — any variance
    pulled off one of these is phantom (un-scanned lines still carry the
    system's on-hand qty), not real shrinkage. See
    backend/app/services/stock_counts.py."""
    try:
        result = await anyio.to_thread.run_sync(stock_counts.get_stuck_stock_counts)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("stuck stock counts failed")
        return {"status": "error", "detail": "Could not load stuck stock counts."}


@app.get("/inventory/stuck-stock-counts/{session_id}")
async def stuck_stock_count_session_detail(session_id: int):
    """Incident Investigation Phase 2/5: one stuck session's full record --
    header, every count line, and every real scan event. Read-only, scoped
    to Celeste's own stores by the same join stuck_stock_counts_route uses.
    See backend/app/services/stock_counts.py get_stock_count_session_detail()."""
    try:
        result = await anyio.to_thread.run_sync(stock_counts.get_stock_count_session_detail, session_id)
        if result is None:
            return {"status": "error", "detail": "Stock count session not found."}
        return {"status": "ok", "session": result}
    except Exception:
        logger.exception("stuck stock count session detail failed")
        return {"status": "error", "detail": "Could not load this stock count session's detail."}


@app.get("/inventory/completed-stock-count-variance")
async def completed_stock_count_variance_route(
    period: str | None = None, start_date: str | None = None, end_date: str | None = None, store_id: int | None = None,
):
    """Completed (state='done') stock counts' REAL, physically-verified
    variance -- distinct from stuck-stock-counts above, which is only ever
    a phantom estimate for sessions that never finished. See
    backend/app/services/stock_counts.py get_completed_stock_count_variance()."""
    try:
        result = await anyio.to_thread.run_sync(
            stock_counts.get_completed_stock_count_variance, period, start_date, end_date, store_id
        )
        return {"status": "ok", **result}
    except Exception:
        logger.exception("completed stock count variance failed")
        return {"status": "error", "detail": "Could not load completed stock-count variance."}


@app.post("/inventory/completed-stock-count-variance/save-as-case")
async def save_completed_stock_count_variance_case(
    period: str | None = None, start_date: str | None = None, end_date: str | None = None, store_id: int | None = None,
):
    """Turns an on-demand completed-stock-count-variance investigation into
    a persistent, trackable case -- the one "save as case" action this
    milestone adds. Deliberately takes the SAME scope parameters the
    investigation was run with and recomputes server-side (never a
    client-supplied finding/evidence/impact blob): a saved case must be
    exactly what the server actually measured, not whatever numbers a
    client claims it saw. metric_id is scoped to store_id + the resolved
    date range so re-investigating the same scope updates the same case,
    while a different scope opens a genuinely new one -- see
    case_engine.save_ad_hoc_case's own docstring."""
    try:
        result = await anyio.to_thread.run_sync(
            stock_counts.get_completed_stock_count_variance, period, start_date, end_date, store_id
        )
        if not result["sessions"]:
            return {"status": "error", "detail": "No completed stock counts found for that period/store — nothing to save."}
        metric_id = f"completed_stock_count_variance-{store_id or 'all'}-{result['start_date']}_{result['end_date']}"
        finding = state_findings.build_completed_stock_count_variance_finding(result, metric_id)
        case_id = await anyio.to_thread.run_sync(case_engine.save_ad_hoc_case, finding)
        return {"status": "ok", "case_id": case_id}
    except Exception:
        logger.exception("save completed stock count variance as case failed")
        return {"status": "error", "detail": "Could not save this investigation as a case."}


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
    file) but is still accepted for whoever tries it.

    Privacy correction: this used to return raw phone/email straight from
    search_customers() -- the same PII exposure the Customer Lookup Design
    doc's masking discipline was built to close on the Ask Celeste side.
    This route only ever needs to help a manager recognize the right
    person in a short result list (there's no click-to-call, no copy
    action, no other use of the raw number anywhere in CustomerPredictions.
    jsx) -- masked phone is enough for that, same _mask_phone() Ask Celeste
    uses, so the two surfaces can't drift onto two different masking
    rules. Raw contact detail stays deferred everywhere in this app until
    there's a real per-manager account to gate it behind."""
    try:
        results = await anyio.to_thread.run_sync(customer_predictions.search_customers, name, phone, email)
        masked = [
            {
                "partner_id": r["partner_id"],
                "name": r["name"],
                "phone_masked": customer_lookup._mask_phone(r["phone"]),
                "order_count": r["order_count"],
            }
            for r in results
        ]
        return {"status": "ok", "results": masked}
    except Exception:
        logger.exception("customer search failed")
        return {"status": "error", "detail": "Could not search customers."}


@app.get("/customers/{partner_id}/profile")
async def customer_profile(partner_id: int):
    """Frontend Intelligence Integration: the Customers page's detail
    view for an already-resolved customer -- lifecycle category, value
    tier, lifetime activity, and recent-vs-prior deterioration signals.
    Same masking and access logging as Ask Celeste's get_customer_lookup
    tool -- see backend/app/services/customer_lookup.py."""
    try:
        result = await anyio.to_thread.run_sync(customer_lookup.get_customer_profile, partner_id)
        if result is None:
            return {"status": "not_found"}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("customer profile failed")
        return {"status": "error", "detail": "Could not load this customer's profile."}


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


@app.get("/customers/lifecycle/segment")
async def customer_lifecycle_segment(category: str, value_tier: str | None = None):
    """The named customers behind one lifecycle x value-tier cell on the
    Customer growth heatmap — a live query (compute_lifecycle_summary's
    own cached snapshot has no per-customer rows to filter), masked contact
    info, capped at SEGMENT_LIST_CAP (top by revenue) and paginated
    client-side — see customer_predictions.list_customers_by_segment()."""
    try:
        result = await anyio.to_thread.run_sync(
            customer_predictions.list_customers_by_segment, category, value_tier
        )
        return result
    except Exception:
        logger.exception("customer lifecycle segment lookup failed")
        return {"status": "error", "detail": "Could not load customers for this segment."}


@app.get("/customers/second-order-conversion")
async def customer_second_order_conversion():
    """Second-order conversion: of customers whose first order is old
    enough to judge, what share placed a second order within 30 days --
    plus the same calculation by acquisition month and 30/60/90-day
    window. A live, retrospective query (not part of the daily scan
    cache) — see customer_predictions.get_second_order_intelligence()."""
    try:
        result = await anyio.to_thread.run_sync(customer_predictions.get_second_order_intelligence)
        if result is None:
            return {"status": "error", "detail": "No order history available."}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("second order conversion failed")
        return {"status": "error", "detail": "Could not load second-order conversion."}


@app.get("/customers/outlet-behavior")
async def customer_outlet_behavior():
    """Per-outlet customer behavior (orders, cancellations, unique
    customers, orders per customer, repeat rate, and acquisition 2nd-order
    rate) for Celeste's 5 real retail outlets — computed live, not part of
    the daily prediction cache. See backend/app/services/customer_predictions.py."""
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


@app.get("/inventory/kpi-history")
async def inventory_kpi_history_route():
    """The Inventory page's KPI strip, plus a delta against the prior day's
    snapshot where one exists — see backend/app/services/inventory_kpi_history.py.
    None, not 0, for any field with no prior snapshot to compare against yet."""
    try:
        result = await anyio.to_thread.run_sync(inventory_kpi_history.get_inventory_kpi_deltas)
        return {"status": "ok", "deltas": result}
    except Exception:
        logger.exception("inventory kpi history failed")
        return {"status": "error", "detail": "Could not load inventory KPI history."}


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


@reporting.ttl_cache(seconds=300)
def _investigate_cached(metric_id: str):
    # Phase 5A: cached 5 minutes here, at the route layer, specifically
    # because THIS caller is always a pure supplementary display (Home's
    # Suppliers panel, RevenueInvestigationPanel's cross-checks) -- never
    # investigation.build_investigation(), which calls intelligence.investigate()
    # directly (uncached) as the core of scan_all()'s real detection pipeline
    # and must see fresh data on every rescan.
    return intelligence.investigate(metric_id)


@app.get("/intelligence/current-period")
async def current_period_investigations(refresh: bool = False):
    """Provisional source-anchored assessments; never mutates cases or outcomes."""
    from mcp_server.tools.investigation import METRIC_DOMAIN
    result = await anyio.to_thread.run_sync(lambda: current_period.assess_all(refresh=refresh))
    return {**result, "items": [{**item, "domain": METRIC_DOMAIN.get(item["metric_id"], "unclassified")} for item in result.get("items", [])]}


@reporting.ttl_cache(seconds=300)
def _month_to_date_store_breakdown_cached(metric_id: str):
    metric = intelligence.METRICS.get(metric_id)
    dim = next((d for d in metric.dimensions if d.id == "store"), None) if metric else None
    if dim is None:
        return None
    return intelligence.get_month_to_date_breakdown(dim)


@app.get("/intelligence/current-period/{metric_id}/store-breakdown")
async def current_period_store_breakdown(metric_id: str):
    """Outlet ranking for the in-progress month so far, elapsed-time-matched
    against the same portion of the prior month -- the per-store counterpart
    to /intelligence/current-period's single company-wide number. Built for
    Home's Outlet Performance panel, which previously could only show the
    last COMPLETE month (see get_month_to_date_breakdown's own docstring for
    why a raw, un-matched month-to-date ranking would be misleading)."""
    result = await anyio.to_thread.run_sync(_month_to_date_store_breakdown_cached, metric_id)
    if result is None:
        return {"status": "error", "detail": f"No store breakdown is available for '{metric_id}'."}
    return {"status": "ok", **result}


@app.get("/intelligence/investigate/{metric_id}")
async def intelligence_investigate(metric_id: str):
    """Run the full trend + anomaly + contributor-breakdown pipeline for one metric."""
    try:
        result = await anyio.to_thread.run_sync(_investigate_cached, metric_id)
        if "error" in result:
            return {"status": "error", "detail": result["error"]}
        return {"status": "ok", **result}
    except Exception:
        logger.exception("investigate_metric failed for %s", metric_id)
        return {"status": "error", "detail": "Could not run the investigation."}


@app.post("/intelligence/investigate/{metric_id}/save-as-case")
async def save_metric_investigation_case(metric_id: str):
    """Generalizes "save as case" -- previously wired only for completed-
    stock-count-variance (see save_completed_stock_count_variance_case
    above) -- to every metric investigate_metric/Ask Celeste can flag:
    revenue, supplier_fill_rate, purchase_price_variance, supplier_
    concentration, and any other entry in intelligence.METRICS. Recomputes
    via investigation.build_investigation(), the SAME finding builder the
    daily scan already uses for these metrics (sync_cases -> scan_all ->
    build_investigation), so a manually-saved case is identical to one the
    scan would have produced on its own -- never a client-supplied
    finding/evidence/impact blob. See case_engine.save_ad_hoc_case."""
    from mcp_server.tools.investigation import build_investigation

    try:
        finding = await anyio.to_thread.run_sync(build_investigation, metric_id)
        if "error" in finding:
            return {"status": "error", "detail": finding["error"]}
        if finding.get("status") != "completed":
            return {"status": "error", "detail": "Nothing to save — this metric isn't currently flagged."}
        case_id = await anyio.to_thread.run_sync(case_engine.save_ad_hoc_case, finding)
        return {"status": "ok", "case_id": case_id}
    except Exception:
        logger.exception("save metric investigation as case failed for %s", metric_id)
        return {"status": "error", "detail": "Could not save this investigation as a case."}


def _attach_actions(findings: list[dict], actions: dict[str, dict]) -> None:
    for f in findings:
        f["action"] = actions.get(f["id"], finding_actions.DEFAULT_ACTION)


@app.get("/brief")
async def daily_brief():
    """Return cached evidence promptly, with explicit refresh/freshness state."""
    try:
        result = await anyio.to_thread.run_sync(home_briefing.get_brief)
        if result["status"] != "ok":
            return JSONResponse(result, status_code=503 if result["status"] == "error" else 202)
        all_findings = [*result["investigations"], *result["state_findings"], *result["short_term_findings"]]
        actions = await anyio.to_thread.run_sync(finding_actions.get_actions_for, [f["id"] for f in all_findings])
        _attach_actions(all_findings, actions)
        return {**result, "home": home_briefing.presentation(result)}
    except Exception:
        logger.exception("daily brief fetch failed")
        return JSONResponse({"status": "error", "detail": "Could not load the daily brief."}, status_code=503)


@app.post("/brief/refresh")
async def refresh_home_brief():
    return JSONResponse({"status": "accepted", "refresh": home_briefing.request_refresh(retry=True)}, status_code=202)


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


@app.get("/outcomes/summary")
async def outcomes_summary():
    """Phase 5 v1: the cross-metric "is this working" scorecard — see
    backend/app/services/outcome_intelligence.py for why this is
    aggregate-only rather than per-metric pattern learning. Cheap (tens of
    rows across 3 small tables), computed fresh on every request, same
    posture as /notifications/summary."""
    try:
        result = await anyio.to_thread.run_sync(outcome_intelligence.get_summary)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("outcomes summary failed")
        return {"status": "error", "detail": "Could not load the outcomes summary."}


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
    outcome verdicts — see finding_actions.py. case_id is resolved inside
    case_engine.save_action_with_verification (not sent by the client)
    from case_observations, so every action save stays correctly linked to
    whichever case this finding_id belongs to without the frontend needing
    to know about cases at all yet.

    evidence_version, when sent, is verified server-side against the
    finding's actual current evidence — the authoritative reassertion of
    the same check CaseDetail.jsx already performs client-side. The
    version check, the write (in one real SQL transaction with its
    decision-snapshot record), all happen inside ONE call under ONE lock
    acquisition (see save_action_with_verification's docstring) rather
    than as separate steps, so a scan cannot land in the gap between
    "evidence verified" and "action saved" (Phase 2e Task 1) — the
    previous three-call version of this endpoint had exactly that gap. A
    version mismatch is rejected with a structured conflict carrying the
    fresh evidence; a scan holding the lock too long is rejected with a
    distinct, retryable conflict."""
    action_kwargs = dict(
        relevant=request.relevant,
        status=request.status,
        action_taken=request.action_taken,
        owner=request.owner,
        deadline=request.deadline,
        action_kind=request.action_kind,
        intended_result=request.intended_result,
        target_metric_id=request.target_metric_id,
        target_value=request.target_value,
        target_comparison=request.target_comparison,
        target_baseline_relative=request.target_baseline_relative,
        target_measurement_period=request.target_measurement_period,
    )
    try:
        result = await anyio.to_thread.run_sync(
            case_engine.save_action_with_verification, finding_id, action_kwargs, request.evidence_version
        )
        return {"status": "ok", "evidence_verified": result["evidence_verified"]}
    except case_engine.StaleEvidenceError as e:
        return {
            "status": "error",
            "error": "stale_evidence",
            "detail": "The evidence for this finding has changed since it was last reviewed.",
            "current_version": e.current_version,
            "current_observed_at": e.current_observed_at,
            "current_snapshot": e.current_snapshot,
        }
    except case_engine.ScanInProgressError as e:
        return {
            "status": "error",
            "error": "scan_in_progress",
            "detail": str(e) or "A scan is currently running; please retry in a moment.",
        }
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    except Exception:
        logger.exception("finding action update failed for %s", finding_id)
        return {"status": "error", "detail": "Could not save this action."}


@app.post("/findings/{finding_id}/recommendation-acceptance")
async def accept_finding_recommendation(finding_id: str, request: RecommendationAcceptanceRequest):
    """Celeste Investigate Phase 2, final correction: a separate route
    from PATCH /findings/{finding_id}/action on purpose -- see
    RecommendationAcceptanceRequest's own docstring and case_engine.
    accept_recommendation's. Same evidence-version-conflict/scan-lock
    error shapes as the action route, so CaseDetail.jsx's existing
    stale-evidence handling works unchanged against either."""
    try:
        result = await anyio.to_thread.run_sync(
            case_engine.accept_recommendation, finding_id, request.recommendation_action, request.evidence_version
        )
        return {"status": "ok", "evidence_verified": result["evidence_verified"], "recorded": result["recorded"]}
    except case_engine.StaleEvidenceError as e:
        return {
            "status": "error",
            "error": "stale_evidence",
            "detail": "The evidence for this finding has changed since it was last reviewed.",
            "current_version": e.current_version,
            "current_observed_at": e.current_observed_at,
            "current_snapshot": e.current_snapshot,
        }
    except case_engine.ScanInProgressError as e:
        return {
            "status": "error",
            "error": "scan_in_progress",
            "detail": str(e) or "A scan is currently running; please retry in a moment.",
        }
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    except Exception:
        logger.exception("recommendation acceptance failed for %s", finding_id)
        return {"status": "error", "detail": "Could not record this acceptance."}


@app.post("/cases/{case_id}/objective")
async def set_case_objective_endpoint(case_id: str, request: CaseObjectiveRequest):
    """Declares a case-level business objective directly — validated
    against the real metric registry (unit is always derived server-side,
    never trusted from the request). See case_engine.set_case_objective."""
    try:
        await anyio.to_thread.run_sync(
            case_engine.set_case_objective, case_id, request.metric_id, request.target_value,
            request.comparison, request.baseline_relative,
        )
        return {"status": "ok"}
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    except Exception:
        logger.exception("case objective set failed for %s", case_id)
        return {"status": "error", "detail": "Could not set this case's objective."}


@app.post("/cases/{case_id}/objective/from-action")
async def designate_case_objective_endpoint(case_id: str, request: CaseObjectiveFromActionRequest):
    """Designates an already-validated action target as this case's
    objective, instead of declaring a new one directly. See
    case_engine.designate_case_objective_from_action."""
    try:
        await anyio.to_thread.run_sync(
            case_engine.designate_case_objective_from_action, case_id, request.finding_id
        )
        return {"status": "ok"}
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    except Exception:
        logger.exception("case objective designation failed for %s", case_id)
        return {"status": "error", "detail": "Could not designate this case's objective."}


@app.get("/management/families")
async def management_families():
    try:
        return {"status": "ok", "families": await anyio.to_thread.run_sync(management.families)}
    except Exception:
        logger.exception("management families failed")
        return {"status": "error", "detail": "Could not load management families."}


@app.get("/management/transfer-accountability")
async def management_transfer_accountability(
    outlet: list[str] = Query(default=[]),
    product: list[str] = Query(default=[]),
    person: list[str] = Query(default=[]),
    date_from: str | None = None,
    date_to: str | None = None,
    recurring_only: bool = False,
):
    filters = transfer_accountability.make_filters(outlet, product, person, date_from, date_to, recurring_only)
    try:
        return {"status": "ok", **await anyio.to_thread.run_sync(transfer_accountability.build, filters)}
    except Exception:
        logger.exception("transfer accountability failed")
        return {"status": "error", "detail": "Could not load transfer accountability."}


@app.get("/management/summary")
async def management_summary():
    try:
        return {"status": "ok", **await anyio.to_thread.run_sync(management.summary)}
    except Exception:
        logger.exception("management summary failed")
        return {"status": "error", "detail": "Could not load management totals."}


@app.get("/management/actions")
async def management_actions(status: str | None = None, outcome_status: str | None = None,
                             relevant: bool | None = None, domain: str | None = None,
                             owner: str | None = None, limit: int = 20, offset: int = 0):
    try:
        result = await anyio.to_thread.run_sync(
            management.list_actions, status, outcome_status, relevant, domain, owner, limit, offset
        )
        return {"status": "ok", **result}
    except Exception:
        logger.exception("management actions failed")
        return {"status": "error", "detail": "Could not load actions."}


@app.get("/findings/{finding_id}/case")
async def finding_case(finding_id: str):
    return {"status": "ok", "case_id": await anyio.to_thread.run_sync(management.finding_case_id, finding_id)}


@app.get("/cases")
async def list_cases(status: str | None = None, metric_id: str | None = None, domain: str | None = None,
                      priority: str | None = None, sort: str | None = None,
                      limit: int = 50, offset: int = 0):
    """Persistent business cases (Phase 2a/2b Case Store) — filterable,
    stably ordered (last_seen_at desc, case_id as a tiebreaker), paginated.
    Read-only; see the POST endpoints below for the only two mutations
    this phase supports.

    Each item's "action" key is that case's most recently updated action
    (owner/status/deadline/target/outcome), the same composition /cases/
    {id} already does for its full action history — added for the Actions
    and Outcomes workspaces, which need a case's action state in the list
    view itself rather than one extra fetch per case (Frontend Experience
    v2, Stage 1). "has_outstanding_action" is a separate, correct answer to
    "does this case have ANY open action" — "action" alone can't answer
    that (an older still-open action can be masked by a newer completed
    one; see finding_actions.has_outstanding_action_for_cases)."""
    try:
        result = await anyio.to_thread.run_sync(
            case_engine.list_cases, status, metric_id, domain, priority, sort, limit, offset
        )
        case_ids = [item["case_id"] for item in result["items"]]
        actions_by_case = await anyio.to_thread.run_sync(finding_actions.get_latest_action_for_cases, case_ids)
        outstanding_case_ids = await anyio.to_thread.run_sync(finding_actions.has_outstanding_action_for_cases, case_ids)
        for item in result["items"]:
            item["action"] = actions_by_case.get(item["case_id"])
            # Distinct from item["action"].status: that's only the most
            # recently updated action, which can be "completed" while an
            # older action on the same case is still open. This flag
            # answers "any outstanding work on this case," not "is the
            # latest action outstanding."
            item["has_outstanding_action"] = item["case_id"] in outstanding_case_ids
        return {"status": "ok", **result}
    except Exception:
        logger.exception("case list failed")
        return {"status": "error", "detail": "Could not load cases."}


@app.get("/cases/{case_id}")
async def get_case_detail(case_id: str):
    """The case, its observations, a performance history derived from
    those observations' own stored current/baseline values, its
    relationships to other cases (e.g. recurrence_of), its audit history,
    and every action recorded against any of its findings."""
    try:
        detail = await anyio.to_thread.run_sync(case_engine.get_case_detail, case_id)
        if detail is None:
            return {"status": "error", "detail": "No such case."}
        actions = await anyio.to_thread.run_sync(finding_actions.get_actions_for_case, case_id)
        return {"status": "ok", **detail, "actions": actions}
    except Exception:
        logger.exception("case detail failed for %s", case_id)
        return {"status": "error", "detail": "Could not load this case."}


@app.post("/cases/{case_id}/dismiss")
async def dismiss_case_endpoint(case_id: str, request: CaseReasonRequest):
    """Closes the whole case without verification (distinct from
    finding_actions.relevant=False, which dismisses one action and never
    touches a case) — a real reason is required, see case_engine.dismiss_case."""
    try:
        await anyio.to_thread.run_sync(case_engine.dismiss_case, case_id, request.reason)
        return {"status": "ok"}
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    except Exception:
        logger.exception("case dismiss failed for %s", case_id)
        return {"status": "error", "detail": "Could not dismiss this case."}


@app.post("/cases/{case_id}/reopen")
async def reopen_case_endpoint(case_id: str, request: CaseReasonRequest):
    """Manually reopens a closed (resolved or dismissed) case — for
    correcting a closure that shouldn't have happened, with no fresh
    finding required. A real reason is required and logged to case_history."""
    try:
        await anyio.to_thread.run_sync(case_engine.reopen_case, case_id, request.reason)
        return {"status": "ok"}
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    except Exception:
        logger.exception("case reopen failed for %s", case_id)
        return {"status": "error", "detail": "Could not reopen this case."}


@app.get("/investigations")
async def investigations():
    """Refresh the complete briefing through the same coordinated pipeline
    as the scheduler. Outcome checks must receive every state-finding source;
    an inventory-only scan would falsely resolve operational incidents."""
    try:
        result = await anyio.to_thread.run_sync(brain.run_daily_scan)
        return {"status": "ok", **result}
    except Exception:
        logger.exception("investigation scan failed")
        return {"status": "error", "detail": "Could not run investigations."}


@app.post("/agent/chat")
async def agent_chat(request: ChatRequest):
    """Run one agent turn: send the conversation to the model, execute any
    requested ERP tools, and stream progress back as it happens -- token
    deltas for the answer, tool_start/tool_result for each ERP lookup along
    the way -- instead of one blocking JSON response after the whole
    tool-calling loop (up to 6 rounds, each a real reporting-DB query)
    finishes. See agent.stream_agent_turn for the event shapes. The one
    exception path that can't come from inside the generator (request
    parsing itself) still needs its own try/except, since nothing has
    started streaming yet at that point."""
    async def event_stream():
        try:
            messages = [m.model_dump() for m in request.messages]
            async for event in agent.stream_agent_turn(messages):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception:
            logger.exception("agent chat turn failed")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'The agent failed to process this request.'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
