import inspect
import json
import os
from datetime import date

import anyio
from openai import OpenAI

from mcp_server.tools import intelligence, sales
from . import customers, forecast, inventory, operations


MAX_TOOL_ROUNDS = 6


def _system_prompt() -> str:
    metric_ids = ", ".join(f"'{m['id']}'" for m in intelligence.list_metrics())
    return (
        "You are an ERP business-intelligence assistant. "
        f"Today's date is {date.today().isoformat()}. "
        "Use the available tools to answer questions with real data instead of "
        "guessing. When a tool has a default for something like 'last month', "
        "omit that argument rather than computing it yourself. If a question "
        "needs data you don't have a tool for, say so plainly rather than "
        "making up an answer. If a tool result has is_partial_period: true, "
        "the period is still in progress (only days_elapsed of days_in_period "
        "days have happened) — explicitly say the figures are partial and that "
        "any growth/comparison number isn't a fair comparison against a full "
        "prior period, rather than stating it as a plain fact.\n\n"
        "For any question about *why* a metric moved, or whenever you'd otherwise "
        "just report a number that changed, don't stop at the number — call "
        f"investigate_metric with one of these ids: {metric_ids} (call list_metrics "
        "if a question doesn't map to one of those). Use the breakdown to name the "
        "specific products, stores, customers, or reasons driving the change, then "
        "suggest a concrete next action. You can call investigate_metric more than "
        "once in a row (e.g. on a related metric) before answering — you are not "
        "limited to one tool call. Only call out a move as a problem or an "
        "opportunity when anomaly.category is 'critical' or 'opportunity' — 'normal' "
        "means the move isn't outside its usual range, so say that plainly rather "
        "than manufacturing a story around it. In breakdowns, prefer segments with "
        "meaningful volume over ones with a big percentage but tiny sample size. "
        "investigate_metric's trend array includes the current, still-in-progress "
        "month — never quote that partial month's value as if it were a completed "
        "period; the anomaly/comparison is always about anomaly.latest_month and "
        "anomaly.latest_value, which is the most recent *complete* month.\n\n"
        "For customer loyalty/churn/at-risk questions, call "
        "get_customer_intelligence. For stock/dead-stock/overstock questions, "
        "call get_inventory_depth. For branch performance or fulfillment-"
        "bottleneck questions, call get_operations_summary. For 'what's expected "
        "next month' or projection questions, call get_forward_look — its result "
        "is a simple trend projection, not a statistical forecast, so always "
        "repeat its 'note' caveat when you quote it rather than stating the "
        "projection as a plain fact.\n\n"
        "The UI already renders a chart/table for get_sales_summary, "
        "get_customer_intelligence, get_inventory_depth, get_operations_summary, "
        "get_forward_look, and investigate_metric results right below your reply "
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
                        "description": "Month to summarize, formatted YYYY-MM. Defaults to last month if omitted.",
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
]

TOOL_FUNCTIONS = {
    "get_sales_summary": sales.get_sales_summary,
    "list_metrics": lambda: intelligence.list_metrics(),
    "investigate_metric": lambda metric_id: intelligence.investigate(metric_id),
    "get_customer_intelligence": customers.get_customer_intelligence,
    "get_inventory_depth": inventory.get_inventory_depth,
    "get_operations_summary": operations.get_fulfillment_summary,
    "get_forward_look": forecast.get_forward_look,
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
