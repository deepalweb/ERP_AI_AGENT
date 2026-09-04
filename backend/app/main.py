import logging

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

from mcp_server.tools import intelligence, investigation
from .db import reporting
from .db.store_scope import CELESTE_STORE_NAMES
from .services import agent, customers, dashboard, forecast, inventory, operations, tracking
from .services import health as health_service

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


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


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


@app.get("/intelligence/health")
async def intelligence_health():
    """Explainable Business Health rollup across Sales, Operations, Inventory,
    and Customers — see backend/app/services/health.py for the scoring rule."""
    try:
        return {"status": "ok", **await health_service.get_business_health()}
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


@app.get("/investigations")
async def investigations():
    """Scan every registered metric and return a narrated Investigation for each
    one currently outside its normal range — the 24/7-analyst feed. Also records
    each finding for outcome tracking and checks whether older ones have
    resolved (see backend/app/services/tracking.py — the "AI Impact" loop)."""
    try:
        result = await anyio.to_thread.run_sync(investigation.scan_all)
        await anyio.to_thread.run_sync(tracking.record_new_findings, result)
        await anyio.to_thread.run_sync(tracking.check_outcomes)
        history = await anyio.to_thread.run_sync(tracking.get_tracking_history)
        return {"status": "ok", "investigations": result, "tracking": history}
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
