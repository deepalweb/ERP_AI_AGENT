import inspect
import json
import os
import re
from datetime import date

import anyio
from openai import OpenAI

from mcp_server.tools import intelligence, sales
from . import (
    basket,
    brand_mix,
    channels,
    concentration,
    customer_lookup,
    customers,
    dashboard,
    forecast,
    inventory,
    listings,
    loss_and_damage,
    lost_sales,
    data_controls,
    loyalty,
    margin,
    operations,
    product_lookup,
    stock_alerts,
    stock_counts,
)


MAX_TOOL_ROUNDS = 6


def _system_prompt() -> str:
    metric_ids = ", ".join(f"'{m['id']}'" for m in intelligence.list_metrics())
    return (
        "You are an ERP business-intelligence assistant for a Sri Lankan retail "
        "business — all currency figures are Sri Lankan Rupees. Write them as "
        "'Rs' (e.g. 'Rs 197.2M'), never '$' or the '₨' glyph, and never say "
        "'dollars'. "
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
        "it's never partial the way a still-in-progress month is. For a "
        "specific custom range ('August 5 to August 20', 'from the 10th to "
        "yesterday'), pass start_date and end_date (YYYY-MM-DD, inclusive) "
        "instead of period — resolve relative dates against today's date "
        "given above before calling the tool. start_date/end_date take "
        "priority over period when both would apply. If a "
        "question needs data you don't have a tool for, say so plainly rather than "
        "making up an answer — but be precise about *why*: if the underlying data "
        "genuinely doesn't exist or isn't trustworthy, say that; if the data "
        "likely exists but there's just no tool built for that specific cut of "
        "it yet, say that instead (e.g. 'I don't have a tool that breaks that "
        "down that way yet') rather than implying the data itself is missing. "
        "Don't guess which case you're in — if you're not sure, say you're not "
        "sure rather than asserting either one. One specific, verified case: "
        "workforce/headcount cost has no reliable data (payroll records exist "
        "but are almost entirely unfinalized drafts) — that's a genuine data "
        "gap, say so plainly. Cash position is NOT a data gap — real, mostly-"
        "finalized payment records exist (account_payment) — there's just no "
        "tool built for it yet, so say that instead if asked. "
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
        "For broad, undirected questions — 'what needs my attention', 'give me "
        "an executive summary', 'biggest problems/opportunities right now', "
        "'what changed this month', 'today's business situation' — call "
        "scan_attention first rather than investigate_metric on each metric "
        "yourself; it does exactly that scan in one call. If it returns "
        "nothing flagged, say plainly that nothing crossed the anomaly "
        "threshold this month rather than manufacturing concerns. If the user "
        "then wants depth on one flagged item, follow up with "
        "investigate_metric on its metric_id.\n\n"
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
        "driver worth digging into, that's exactly what belongs in the FOLLOW_UPS "
        "trailer described near the end of these instructions — never write a "
        "continuation offer ('Want me to look into X?', 'let me know if you want "
        "more detail') as part of the narrative text itself; keep the narrative "
        "focused on what changed, why, and what to do, and put every next-question "
        "offer only in that trailer. "
        "investigate_metric's trend array includes the current, still-in-progress "
        "month — never quote that partial month's value as if it were a completed "
        "period; the anomaly/comparison is always about anomaly.latest_month and "
        "anomaly.latest_value, which is the most recent *complete* month.\n\n"
        "Both investigate_metric (in .persistence) and each item flagged by "
        "scan_attention carry a persistence read — use it, don't just report "
        "the latest month's number in isolation: 'single_period_move' means "
        "this looks like an isolated month, say so rather than implying a "
        "trend; 'consistent_declining'/'consistent_improving' with "
        "consecutive_periods >= 2 means say explicitly how many periods "
        "running (e.g. 'this is the third consecutive month of decline, not "
        "a one-off') — this is more informative than the anomaly number "
        "alone and is exactly the kind of context that turns a number into "
        "an insight; 'recovering' means the metric moved the right direction "
        "this month right after moving the wrong way before that — say it "
        "looks like a recovery, not a fresh problem, and don't call it "
        "critical/opportunity-worthy on its own if the anomaly category is "
        "'normal'. 'insufficient_data' or 'flat' need no comment. This "
        "business only has ~6 months of history (data starts Feb 2026) — "
        "never frame a pattern as seasonal or compare it with 'last year', "
        "there isn't one yet; say so plainly if asked.\n\n"
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
        "projection as a plain fact. For 'what is margin' / 'what is AOV' "
        "questions about one period, call get_gross_margin / get_sales_summary "
        "— get_gross_margin is an ESTIMATE on a current-cost basis (product "
        "cost is today's catalog price applied to historical sales, not a "
        "historical cost-of-goods-sold record), so always call it that "
        "('estimated gross margin, current-cost basis') rather than 'gross "
        "margin' alone, and mention cost_data_coverage_pct separately as what "
        "share of revenue had a cost value at all — coverage does NOT establish "
        "that the cost value is period-accurate, so never present a high "
        "coverage percentage as validating the figure's historical accuracy. "
        "For 'why did margin change', 'is margin trending', "
        "or 'is revenue growth volume-driven or price-driven', call "
        "investigate_metric with 'gross_margin' or 'average_order_value' — "
        "reading average_order_value next to order_volume is exactly how to "
        "answer the volume-vs-price question. gross_margin's breakdown is "
        "store-only (no per-product margin breakdown exists yet — say so if "
        "asked which product drove a margin change) and average_order_value "
        "has no breakdown at all (say so if asked which segment drove an AOV "
        "change) — both should be stated as a tool limitation, not implied to "
        "not exist as data. For 'how much comes through PickMe vs Uber' or "
        "sales-channel questions, call get_channel_breakdown. For Uber "
        "out-of-stock/item-removed/lost-sale questions, call get_uber_lost_sales "
        "and repeat its estimate_note caveat when quoting the revenue figure. For "
        "'which outlet has the most inventory' questions, call "
        "get_inventory_by_outlet rather than get_inventory_depth, which is "
        "company-wide only. For any question about one specific named product "
        "or SKU — sales, margin, stock, or how it's doing — call "
        "get_product_lookup rather than searching for it inside a Top-N tool's "
        "results; those only cover the top 5-8 items and will miss it. For "
        "'% of revenue from top products/customers' or 'are we too dependent "
        "on a few customers' questions, call get_revenue_concentration — "
        "don't approximate this from get_top_products, which isn't scoped to "
        "total revenue. For 'what gets bought together', cross-sell/bundle "
        "ideas, or basket-composition questions, call get_basket_analysis — "
        "prefer top_pairs_by_affinity (lift) for a genuinely surprising "
        "combination worth acting on (lift far above 1 means far more likely "
        "together than chance), and top_pairs_by_frequency for the most "
        "common combinations regardless of surprise. Both are already floored "
        "at min_pair_orders so a rare coincidence can't surface as a top "
        "result — don't add your own extra caveat about sample size on top of "
        "that. This tool is slower than the others (several seconds) — only "
        "call it when the question is actually about co-purchase patterns. "
        "For "
        "'what's out of stock' or restocking-priority questions, call "
        "get_stock_alerts. For whether a specific product is listed/enabled on "
        "Uber or PickMe at a given outlet, call get_product_listing — don't "
        "confuse this with get_product_lookup's stock numbers, they answer "
        "different questions (listed-for-sale vs. quantity-on-hand). Inventory "
        "expiry/freshness risk has no data behind it at all — on-hand stock "
        "isn't tracked by lot/batch (stock_quant.lot_id is unused), and "
        "shelf_life_days is 0% populated — say this is a genuine data gap, not "
        "a missing tool, if asked; a narrow forward-dated expiry field does "
        "exist for a few short-shelf-life SKUs (bread, produce, eggs) but "
        "isn't systematic enough to support a general expiry-risk question. "
        "For 'what are we losing/wasting', shrinkage, damaged-goods, or "
        "picking-shortage questions, call get_loss_and_damage — its "
        "shortage_by_reason and damaged_top_products/damaged_by_store are "
        "real operational data that didn't exist as a queryable signal "
        "before this tool.\n\n"
        "The UI already renders a chart/table for get_sales_summary, "
        "get_customer_intelligence, get_inventory_depth, get_operations_summary, "
        "get_forward_look, get_top_products, get_top_cancelled_products, "
        "get_gross_margin, get_channel_breakdown, get_uber_lost_sales, "
        "get_inventory_by_outlet, get_product_lookup, get_stock_alerts, "
        "get_product_listing, scan_attention, get_revenue_concentration, "
        "get_basket_analysis, get_loss_and_damage, "
        "get_loyalty_and_promotions, get_brand_and_department_mix, and "
        "investigate_metric results right below your reply "
        "— so don't re-type their numbers into a markdown table yourself. Lead "
        "with the narrative: what changed, why, and what to do about it. Still "
        "use a markdown table for any ad-hoc numbers that don't come from one of "
        "those tools.\n\n"
        "This is a hard rule, every single turn, no matter how long the "
        "conversation has gotten: after your narrative answer, add one final "
        "trailer line, on its own, with nothing after it, in exactly this "
        "form: 'FOLLOW_UPS: question one? | question two? | question three?' "
        "— 2 to 3 follow-up questions the user could click to dig deeper, each "
        "one a complete question naming the actual product, store, customer, "
        "metric, or month you just discussed (never a generic 'want to know "
        "more?'). The FOLLOW_UPS trailer is the ONLY place this content may "
        "appear — never also write a visible section in the narrative itself "
        "for this (no 'Follow-up questions you could explore:', 'Next steps', "
        "'What to ask next', or similar heading with its own bullet list); the "
        "UI renders the trailer as clickable chips right under your reply, so "
        "a matching prose section is pure duplication the user will see twice. "
        "A 'what to do about it' recommendations section (act on the finding) "
        "is fine and different from this — it's advice, not a list of "
        "questions to ask. Each follow-up must be answerable by one of your "
        "existing tools — "
        "don't suggest a drill-down you don't actually have data for. Favor "
        "the specific named driver from a breakdown's top_driver/"
        "top_contributors over the metric itself (e.g. if Colombo 6 drove a "
        "revenue drop, suggest asking about Colombo 6, not just 'why did "
        "revenue change again'), and a related metric that would confirm or "
        "explain the same story (e.g. average_order_value next to "
        "order_volume, or delayed_delivery_rate next to cancellation_rate). "
        "Omit the trailer entirely only when there's truly no real next "
        "question — a plain data-gap refusal, or a lookup that surfaced "
        "nothing anomalous — never because the narrative already mentioned a "
        "next step in passing; the trailer is the only place that offer may "
        "appear, so if there's a next question worth asking, the trailer is "
        "mandatory."
    )

_DATE_RANGE_PROPS = {
    "start_date": {
        "type": "string",
        "description": (
            "Explicit range start, YYYY-MM-DD. Use together with end_date for a "
            "specific date range that isn't a calendar month or a 'last N days' "
            "window (e.g. 'August 5 to August 20', 'since the 3rd'). Takes "
            "priority over period when both are given."
        ),
    },
    "end_date": {
        "type": "string",
        "description": "Explicit range end, YYYY-MM-DD, inclusive. Must be given together with start_date.",
    },
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_sales_summary",
            "description": (
                "Get revenue, order count, average order value, and growth vs. "
                "the prior equal-length period for a given month, rolling "
                "window, or explicit date range."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month if omitted.",
                    },
                    **_DATE_RANGE_PROPS,
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_attention",
            "description": (
                "Scan every registered metric (revenue, order volume, "
                "cancellation rate, delayed-delivery rate, customer retention) "
                "for the latest complete month and return only the ones that "
                "are a critical decline or a notable opportunity, each with "
                "its single strongest driver. Call this FIRST for broad "
                "questions like 'what needs my attention', 'give me an "
                "executive summary', 'what are the biggest problems/"
                "opportunities right now', or 'what changed this month' — "
                "it's much cheaper than calling investigate_metric once per "
                "metric. Follow up with investigate_metric on a specific "
                "flagged metric_id only if the user wants more depth than "
                "the single top_driver this gives."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
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
            "name": "get_customer_lookup",
            "description": (
                "Look up one or more customers by name, phone, or email and get an "
                "analytical profile: lifecycle category, value tier (high/mid/low, based "
                "on company-wide revenue percentiles), order count, lifetime revenue, "
                "last order date, and next-basket prediction. Phone/email are always "
                "masked or omitted in the response — this tool never returns raw contact "
                "detail, by design, so never claim or imply you have a customer's full "
                "phone number or email. If status is 'multiple', list the returned "
                "matches (name + masked phone) and ask which one the user meant. If "
                "'not_found', say so plainly. Use for 'show me customer X', 'is customer "
                "X still ordering', or similar single-customer questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Customer name (partial match)."},
                    "phone": {"type": "string", "description": "Customer phone number, any format."},
                    "email": {"type": "string", "description": "Customer email address."},
                },
                "required": [],
            },
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
                    },
                    **_DATE_RANGE_PROPS,
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
                "Get ESTIMATED gross margin on a current-cost basis (revenue minus "
                "cost of goods sold, where cost is product_template's current "
                "catalog cost applied to historical sales quantities — not a "
                "historical cost-of-goods-sold record) for a month, rolling window, "
                "or explicit date range: amount, percent, and what share of revenue "
                "had a cost value at all (cost_data_coverage_pct). Coverage and "
                "point-in-time cost accuracy are separate questions — mention both "
                "the current-cost-basis caveat AND the coverage figure when quoting "
                "margin, never coverage alone. Defaults to last month if period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
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
                "Uber, or Direct — for a month, rolling window, or explicit date "
                "range. Use for 'how much comes through PickMe vs Uber' style "
                "questions. Defaults to last month if period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
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
                "Get Uber's item-removed count for a month, rolling window, or "
                "explicit date range — items Uber removed from an order after it "
                "was placed (mostly out-of-stock at pick time), with the top "
                "reasons and most-affected products. estimated_lost_revenue is a "
                "lower-bound estimate (assumes 1 unit per removed line) — always "
                "repeat its caveat when quoting it. Defaults to last month if "
                "period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
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
                "Look up a single product by name or SKU and get its sales, "
                "ESTIMATED gross margin (current-cost basis — same caveat as "
                "get_gross_margin, always mention it when quoting), Uber removal "
                "count, channel mix, and current stock by outlet — all in one call. "
                "Use this for any question about one specific item ('how is X "
                "selling', 'margin on X', 'stock of X'), instead of scanning a Top-N "
                "tool's results for it. If the result's status is 'ambiguous', list "
                "the returned matches and ask which one the user meant rather than "
                "guessing. If 'not_found', say so plainly — don't infer a product "
                "that isn't in the matches."
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
                    **_DATE_RANGE_PROPS,
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
            "name": "get_data_controls",
            "description": (
                "Get two data-reliability signals, company-wide, right now: "
                "(1) active products currently recorded at negative stock "
                "-- a data-integrity condition (sold past what the system "
                "recorded), never confirmed loss or theft, cause unknown "
                "unless separately investigated; (2) catalog cost coverage "
                "-- how many active inventory products have no recorded "
                "cost anywhere. Coverage is normally near-complete; state "
                "the coverage percentage and say plainly that any gap only "
                "affects the specific products listed, never imply other "
                "products' margin or inventory-value figures are wrong. "
                "Use for 'negative stock', 'data quality', 'missing cost', "
                "or 'what needs cleanup in the catalog' questions."
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
    {
        "type": "function",
        "function": {
            "name": "get_basket_analysis",
            "description": (
                "Get market-basket analysis for a month, rolling window, or "
                "explicit date range: which real inventory products (packaging "
                "and service-fee lines are excluded) get bought together most "
                "often (top_pairs_by_frequency) and which pairs have the "
                "strongest affinity — far more likely to co-occur than chance "
                "alone, via 'lift' — regardless of overall volume "
                "(top_pairs_by_affinity). Use for 'what gets bought together', "
                "'cross-sell' / 'bundle' suggestions, or basket-composition "
                "questions. This query is heavier than most (several seconds) "
                "— only call it when the question is actually about co-"
                "purchase patterns, not as a speculative extra call. Defaults "
                "to last month if period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_loss_and_damage",
            "description": (
                "Get wastage/shrinkage signal for a month, rolling window, or "
                "explicit date range: picking-shortage lines by reason "
                "(missing item, item declined, product damage, extra "
                "quantity) with top affected products and stores, physically "
                "damaged-goods lines with top affected products and stores, "
                "and stock-return counts/quantity. Use for 'what are we "
                "losing/wasting', 'shrinkage', 'damaged goods', or "
                "'shortage' questions. returns_orders/returns_qty are "
                "internal stock returns (store back to warehouse), not "
                "customer or supplier returns — say so if asked, don't "
                "imply a refund or supplier-credit process. Defaults to "
                "last month if period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_count_variance",
            "description": (
                "Get REAL, physically-verified variance from COMPLETED "
                "physical stock counts for a month, rolling window, or "
                "explicit date range — how much the physical count differed "
                "from what the system recorded, broken down by outlet and by "
                "product type (real inventory vs. packing material vs. raw "
                "items vs. services). This is DIFFERENT from a stuck/"
                "unfinished stock count (a phantom estimate for a count "
                "that never finished) — this tool is only for counts that "
                "actually completed. Use for 'stock variance', 'stock-take "
                "discrepancies', or 'why did a stock count come out wrong' "
                "questions. State plainly which outlet/product type the "
                "variance is concentrated in and whether it overlaps with "
                "picking-loss/damage data (a real correlation, never proof "
                "of cause) — never invent a reason beyond what the "
                "breakdown and evidence actually show. Defaults to last "
                "month if period is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_loyalty_and_promotions",
            "description": (
                "Get loyalty-points activity (issued/used/outstanding, top "
                "customers by points, note that issued and used are equal on "
                "~99% of activity since redemption here is near-instant, not "
                "a save-and-spend program) and store promotions active "
                "during a month, rolling window, or explicit date range "
                "(product count, total discount value, average discount %). "
                "Use for 'loyalty points', 'how many points did customers "
                "earn/redeem', or 'what promotions ran and how much did they "
                "discount' questions. Defaults to last month if period is "
                "omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_brand_and_department_mix",
            "description": (
                "Get revenue split by brand (top 12) and by department "
                "(Grocery, Beverage, Fresh, Household, Frozen, Liquor/"
                "Tobacco, Chilled, Bakery, Pharmacy, ...) for a month, "
                "rolling window, or explicit date range. Use for 'brand "
                "performance', 'which brands sell best', or 'category/"
                "department mix' questions. Celeste's private labels (e.g. "
                "Island Tide, Sterilex, Puracel) are real products in the "
                "catalog, but filed as product NAMES under the CELESTE brand "
                "tag, not as their own separate brand entries — this "
                "breakdown's brand list won't show them by name, they show "
                "under CELESTE. For a specific private-label product by "
                "name, use get_product_lookup instead, which searches "
                "product names directly. Defaults to last month if period "
                "is omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_revenue_concentration",
            "description": (
                "Get what share of revenue comes from the top 10 products and "
                "top 10 customers, for a month, rolling window, or explicit "
                "date range — a dependency-risk read ('are we too reliant on a "
                "handful of SKUs or customers'), not a ranking (use "
                "get_top_products for that). Use for '% of revenue from our "
                "top products/customers' or 'are we too dependent on a small "
                "number of customers' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month (YYYY-MM), or a rolling window like '7d'/'30d' for the last N days. Defaults to last month.",
                    },
                    **_DATE_RANGE_PROPS,
                },
                "required": [],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_sales_summary": sales.get_sales_summary,
    "list_metrics": lambda: intelligence.list_metrics(),
    "investigate_metric": lambda metric_id: intelligence.investigate(metric_id),
    "get_customer_intelligence": customers.get_customer_intelligence,
    "get_customer_lookup": customer_lookup.get_customer_lookup,
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
    "get_data_controls": data_controls.get_data_controls,
    "get_product_listing": listings.get_product_listing,
    "scan_attention": lambda: intelligence.scan_attention(),
    "get_revenue_concentration": concentration.get_revenue_concentration,
    "get_basket_analysis": basket.get_basket_analysis,
    "get_loss_and_damage": loss_and_damage.get_loss_and_damage,
    "get_stock_count_variance": stock_counts.get_completed_stock_count_variance,
    "get_loyalty_and_promotions": loyalty.get_loyalty_and_promotions,
    "get_brand_and_department_mix": brand_mix.get_brand_and_department_mix,
}


_FOLLOW_UPS_RE = re.compile(r"\n?\**FOLLOW_UPS:\**\s*(.+)", re.IGNORECASE | re.DOTALL)


def _extract_follow_ups(text: str | None) -> tuple[str | None, list[str]]:
    """Split the model's optional trailing 'FOLLOW_UPS: q1? | q2?' line off the
    reply. Tolerant of stray whitespace/newlines and markdown emphasis (the
    model sometimes bolds the marker as '**FOLLOW_UPS:**') inside the trailer —
    if the marker isn't present at all, the reply passes through unchanged."""
    if not text:
        return text, []
    stripped = text.strip()
    match = _FOLLOW_UPS_RE.search(stripped)
    if not match:
        return text, []
    questions = []
    for q in match.group(1).split("|"):
        q = " ".join(q.split()).strip("* ")
        if q:
            questions.append(q)
    clean = stripped[: match.start()].rstrip()
    return clean, questions[:3]


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
            reply, follow_ups = _extract_follow_ups(choice_message.content)
            return {"reply": reply, "tool_calls": executed_calls, "follow_ups": follow_ups}

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
    reply, follow_ups = _extract_follow_ups(final_response.choices[0].message.content)
    return {
        "reply": reply,
        "tool_calls": executed_calls,
        "follow_ups": follow_ups,
    }
