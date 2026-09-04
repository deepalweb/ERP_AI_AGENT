import inspect
import json
import os
from datetime import date

import anyio
from openai import OpenAI

from mcp_server.tools import intelligence, sales
from . import (
    channels,
    customers,
    dashboard,
    forecast,
    inventory,
    listings,
    lost_sales,
    margin,
    operations,
    product_lookup,
    stock_alerts,
)


MAX_TOOL_ROUNDS = 6


def _system_prompt() -> str:
    metric_ids = ", ".join(f"'{m['id']}'" for m in intelligence.list_metrics())
    return (
        "You are an ERP business-intelligence assistant. "
        f"Today's date is {date.today().isoformat()}. "
        "Use the available tools to answer questions with real data instead of "
        "guessing. When a tool has a default for something like 'last month', "
        "omit that argument rather than computing it yourself. For "
        "period-taking tools (get_sales_summary, get_gross_margin, "
        "get_channel_breakdown, get_uber_lost_sales, get_top_products, "
        "get_product_lookup), a 'last N days' or 'this week' style question "
        "should pass period as 'Nd' (e.g. '7d' for last 7 days, '30d' for "
        "last 30) — don't approximate it as a calendar month. A rolling "
        "window like this is anchored to the data's own latest order date, so "
        "it's never partial the way a still-in-progress month is. If a "
        "question needs data you don't have a tool for, say so plainly rather than "
        "needs data you don't have a tool for, say so plainly rather than "
        "making up an answer — but be precise about *why*: if the underlying data "
        "genuinely doesn't exist or isn't trustworthy, say that; if the data "
        "likely exists but there's just no tool built for that specific cut of "
        "it yet, say that instead (e.g. 'I don't have a tool that breaks that "
        "down that way yet') rather than implying the data itself is missing. "
        "Don't guess which case you're in — if you're not sure, say you're not "
        "sure rather than asserting either one. Two specific, verified cases: "
        "workforce/headcount cost has no reliable data (payroll records exist "
        "but are almost entirely unfinalized drafts) and cash position isn't "
        "tracked by any tool — these are genuine data gaps, say so plainly. "
        "Delivery speed measured against a promised time is also unmeasurable — "
        "the commitment_date field exists but is unpopulated. Gross margin *is* "
        "available now — call get_gross_margin rather than declining. "
        "When a question touches both available and unavailable data, answer the "
        "available part fully and name the gap — don't decline the whole question. "
        "If a tool result has is_partial_period: true, "
        "the period is still in progress (only days_elapsed of days_in_period "
        "days have happened) — explicitly say the figures are partial and that "
        "any growth/comparison number isn't a fair comparison against a full "
        "prior period, rather than stating it as a plain fact.\n\n"
        "For any question about *why* a metric moved, or whenever you'd otherwise "
        "just report a number that changed, don't stop at the number — call "
        f"investigate_metric with one of these ids: {metric_ids} (call list_metrics "
        "if a question doesn't map to one of those). Use the breakdown to name the "
        "specific products, stores, customers, or reasons driving the change. You "
        "can call investigate_metric more than once in a row (e.g. on a related "
        "metric) before answering — you are not limited to one tool call. Only "
        "call out a move as a problem or an opportunity when anomaly.category is "
        "'critical' or 'opportunity' — 'normal' means the move isn't outside its "
        "usual range, so say that plainly rather than manufacturing a story around "
        "it. In breakdowns, prefer segments with meaningful volume over ones with "
        "a big percentage but tiny sample size. When anomaly.category is "
        "'critical' or 'opportunity', or a breakdown surfaces one specific named "
        "driver worth digging into, end your reply by explicitly offering to "
        "continue — a direct question naming that driver, e.g. 'Want me to look "
        "into why Colombo 6 orders dropped?', not a vague 'let me know if you want "
        "more detail'. Don't add this offer to a plain lookup that found nothing "
        "anomalous — only offer to continue when there's a specific, named thread "
        "worth pulling on. "
        "investigate_metric's trend array includes the current, still-in-progress "
        "month — never quote that partial month's value as if it were a completed "
        "period; the anomaly/comparison is always about anomaly.latest_month and "
        "anomaly.latest_value, which is the most recent *complete* month.\n\n"
        "For customer loyalty/churn/at-risk questions, call "
        "get_customer_intelligence. For stock/dead-stock/overstock questions, "
        "call get_inventory_depth. For branch performance or fulfillment-"
        "bottleneck questions, call get_operations_summary. For 'best-selling "
        "product' / 'which product sold the most' questions, call "
        "get_top_products rather than inferring it from investigate_metric's "
        "revenue breakdown — it has real per-product unit counts, and its ranking "
        "is all-time, not a single month, so say that plainly if the question was "
        "about a specific month. For 'most common cancelled item' / 'what gets "
        "cancelled most' questions, call get_top_cancelled_products, not "
        "investigate_metric on cancellation_rate — that tool's product breakdown "
        "explains what changed in one specific month, it isn't an all-time "
        "ranking. For 'what's expected "
        "next month' or projection questions, call get_forward_look — its result "
        "is a simple trend projection, not a statistical forecast, so always "
        "repeat its 'note' caveat when you quote it rather than stating the "
        "projection as a plain fact. For gross margin / profitability questions, "
        "call get_gross_margin — always repeat its cost_data_coverage_pct caveat "
        "when quoting the figure. For 'how much comes through PickMe vs Uber' or "
        "sales-channel questions, call get_channel_breakdown. For Uber "
        "out-of-stock/item-removed/lost-sale questions, call get_uber_lost_sales "
        "and repeat its estimate_note caveat when quoting the revenue figure. For "
        "'which outlet has the most inventory' questions, call "
        "get_inventory_by_outlet rather than get_inventory_depth, which is "
        "company-wide only. For any question about one specific named product "
        "or SKU — sales, margin, stock, or how it's doing — call "
        "get_product_lookup rather than searching for it inside a Top-N tool's "
        "results; those only cover the top 5-8 items and will miss it. For "
        "'what's out of stock' or restocking-priority questions, call "
        "get_stock_alerts. For whether a specific product is listed/enabled on "
        "Uber or PickMe at a given outlet, call get_product_listing — don't "
        "confuse this with get_product_lookup's stock numbers, they answer "
        "different questions (listed-for-sale vs. quantity-on-hand). Inventory "
        "expiry/freshness risk has no data behind it at all — on-hand stock "
        "isn't tracked by lot/batch (stock_quant.lot_id is unused), and "
        "shelf_life_days is 0% populated — say this is a genuine data gap, not "
        "a missing tool, if asked.\n\n"
        "The UI already renders a chart/table for get_sales_summary, "
        "get_customer_intelligence, get_inventory_depth, get_operations_summary, "
        "get_forward_look, get_top_products, get_top_cancelled_products, "
        "get_gross_margin, get_channel_breakdown, get_uber_lost_sales, "
        "get_inventory_by_outlet, get_product_lookup, get_stock_alerts, "
        "get_product_listing, and investigate_metric results right below your reply "
        "— so don't re-type their numbers into a markdown table yourself. Lead "
        "with the narrative: what changed, why, and what to do about it. Still "
        "use a markdown table for any ad-hoc numbers that don't come from one of "
        "those tools."
    )

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_sales_summary",
            "description": (
                "Get revenue, order count, average order value, and month-over-month "
                "growth for a given month."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month if omitted.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_metrics",
            "description": (
                "List the business metrics available for investigation (e.g. cancellation "
                "rate, revenue, order volume), and which dimensions (product, store, "
                "customer, reason, ...) each can be broken down by."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "investigate_metric",
            "description": (
                "Run a full investigation on one metric: its monthly trend, whether the "
                "latest complete month is a critical decline or a notable opportunity, "
                "and which segments are most responsible for the change. Call "
                "list_metrics first if you don't already know the metric id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_id": {
                        "type": "string",
                        "description": "A metric id from list_metrics, e.g. 'cancellation_rate', 'revenue', 'order_volume'.",
                    }
                },
                "required": ["metric_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_intelligence",
            "description": (
                "Get customer segmentation (active/at-risk/churned/one-time), the "
                "repeat-purchase rate, at-risk customers ranked by revenue, and the "
                "customer retention trend."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inventory_depth",
            "description": (
                "Get inventory health: dead-stock and overstock segments (units and "
                "estimated value), and the specific dead-stock/overstock items ranked "
                "by value."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_operations_summary",
            "description": (
                "Get the operations rollup: headline delayed-delivery/cancellation/"
                "revenue/order figures and their trend, the per-branch scorecard "
                "(revenue, orders, cancellation rate, delayed rate, median fulfillment "
                "time), and the fulfillment-stage funnel showing where time is lost "
                "between picking and dispatch."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_forward_look",
            "description": (
                "Get a simple trend-based revenue/order projection range for next "
                "month, based on the last 3 months' growth rates. Not a statistical "
                "forecast — always repeat its 'note' caveat when quoting it."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_products",
            "description": (
                "Get the top-selling products, ranked by revenue, each with its "
                "revenue and units sold. Omit period for all-time sales; pass a "
                "YYYY-MM period to scope it to one month instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Omit for all-time.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_cancelled_products",
            "description": (
                "Get the products most frequently appearing on cancelled orders, "
                "ranked by how many times each was cancelled, all-time. Use this "
                "for 'most common cancelled item' / 'what gets cancelled most' "
                "questions. For 'why did the cancellation rate change and which "
                "products were involved', use investigate_metric on "
                "cancellation_rate instead — its product breakdown explains a "
                "specific month's change, not the all-time count this tool gives."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_gross_margin",
            "description": (
                "Get gross margin (revenue minus cost of goods sold) for a month: "
                "amount, percent, and what share of that month's revenue actually "
                "had cost data (cost_data_coverage_pct) — always mention that "
                "coverage figure when quoting margin, since it's not quite 100%. "
                "Defaults to last month if period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_channel_breakdown",
            "description": (
                "Get revenue and order count split by sales channel — PickMe, "
                "Uber, or Direct — for a month. Use for 'how much comes through "
                "PickMe vs Uber' style questions. Defaults to last month if "
                "period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_uber_lost_sales",
            "description": (
                "Get Uber's item-removed count for a month — items Uber removed "
                "from an order after it was placed (mostly out-of-stock at pick "
                "time), with the top reasons and most-affected products. "
                "estimated_lost_revenue is a lower-bound estimate (assumes 1 unit "
                "per removed line) — always repeat its caveat when quoting it. "
                "Defaults to last month if period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inventory_by_outlet",
            "description": (
                "Get current on-hand inventory units and estimated value at each "
                "of the 5 Celeste outlets. Company-wide totals/dead-stock/overstock "
                "come from get_inventory_depth instead — this tool is only for "
                "'which outlet is holding the most inventory' style questions."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_lookup",
            "description": (
                "Look up a single product by name or SKU and get its sales, gross "
                "margin, Uber removal count, channel mix, and current stock by "
                "outlet — all in one call. Use this for any question about one "
                "specific item ('how is X selling', 'margin on X', 'stock of X'), "
                "instead of scanning a Top-N tool's results for it. If the result's "
                "status is 'ambiguous', list the returned matches and ask which one "
                "the user meant rather than guessing. If 'not_found', say so plainly "
                "— don't infer a product that isn't in the matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Product name (or part of it) or exact SKU/default_code to search for.",
                    },
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Omit for all-time.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_alerts",
            "description": (
                "Get products that are out of stock or critically low (under "
                "3 days of cover) at each outlet right now, among products that "
                "are actually selling. This is the shortage counterpart to "
                "get_inventory_depth (which only covers excess: dead stock and "
                "overstock). Use this for 'what's out of stock', 'what needs "
                "restocking', or as the likely cause behind Uber lost-sale "
                "removals."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_listing",
            "description": (
                "Look up whether a specific product (by name or SKU) is listed "
                "for sale at each Celeste outlet, and whether it's enabled on "
                "Uber and PickMe there, with its store price. Use this for "
                "'why isn't X showing on Uber at outlet Y' or 'is X listed "
                "everywhere' questions — this is about catalog listing status, "
                "not stock quantity (use get_product_lookup for stock). Same "
                "ambiguous/not_found handling as get_product_lookup."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Product name (or part of it) or exact SKU/default_code to search for.",
                    }
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_sales_summary": sales.get_sales_summary,
    "list_metrics": lambda: intelligence.list_metrics(),
    "investigate_metric": lambda metric_id: intelligence.investigate(metric_id),
    "get_customer_intelligence": customers.get_customer_intelligence,
    "get_inventory_depth": inventory.get_inventory_depth,
    "get_operations_summary": operations.get_fulfillment_summary,
    "get_top_products": dashboard.get_top_products,
    "get_top_cancelled_products": dashboard.get_top_cancelled_products,
    "get_forward_look": forecast.get_forward_look,
    "get_gross_margin": margin.get_gross_margin,
    "get_channel_breakdown": channels.get_channel_breakdown,
    "get_uber_lost_sales": lost_sales.get_uber_lost_sales,
    "get_inventory_by_outlet": inventory.get_inventory_by_outlet,
    "get_product_lookup": product_lookup.get_product_lookup,
    "get_stock_alerts": stock_alerts.get_stock_alerts,
    "get_product_listing": listings.get_product_listing,
}


def _get_client() -> OpenAI:
    api_key = os.getenv("NVIDIA_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("NVIDIA_BASE_URL")
    return OpenAI(api_key=api_key, base_url=base_url)


def _get_model() -> str:
    return os.getenv("OPENAI_MODEL", "nvidia/nemotron-3-super-120b-a12b")


async def run_agent_turn(messages: list[dict]) -> dict:
    """Run one agent turn, letting the model call tools repeatedly (e.g. list_metrics
    then investigate_metric, or investigate a second related metric) until it settles
    on a final answer or hits MAX_TOOL_ROUNDS."""
    client = _get_client()
    model = _get_model()

    full_messages = [{"role": "system", "content": _system_prompt()}, *messages]
    executed_calls = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = await anyio.to_thread.run_sync(
            lambda: client.chat.completions.create(
                model=model,
                messages=full_messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        )
        choice_message = response.choices[0].message

        if not choice_message.tool_calls:
            return {"reply": choice_message.content, "tool_calls": executed_calls}

        full_messages.append(choice_message)

        for tool_call in choice_message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments or "{}")
            func = TOOL_FUNCTIONS.get(name)

            if func is None:
                result = {"error": f"Unknown tool '{name}'"}
            elif inspect.iscoroutinefunction(func):
                result = await func(**args)
            else:
                result = await anyio.to_thread.run_sync(lambda: func(**args))

            executed_calls.append({"name": name, "arguments": args, "result": result})
            full_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, default=str),
                }
            )

    # Hit the round cap still wanting to call tools — force a final text answer
    # from what's already been gathered rather than looping forever.
    final_response = await anyio.to_thread.run_sync(
        lambda: client.chat.completions.create(model=model, messages=full_messages)
    )
    return {
        "reply": final_response.choices[0].message.content,
        "tool_calls": executed_calls,
    }
