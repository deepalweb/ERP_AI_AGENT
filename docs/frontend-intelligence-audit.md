# Celeste — complete frontend intelligence architecture

Audit date: 2026-09-18. Scope: the current working tree, including the existing
uncommitted Case System and frontend work. This is a source/contract audit and
implementation plan, not a claim that live reporting data was revalidated.

## Decision

Keep one product with three connected experiences:

- **Management:** Today, Investigate, Actions, Outcomes. The persistent Case Store
  is authoritative for case identity, investigation history, and resolution.
- **Business intelligence:** Explore and Customer Predictions. Analysis can be
  useful without becoming a case.
- **Conversational intelligence:** Ask Celeste. It remains a general business
  assistant; a title passed into chat is not a case-evidence context.

There are **22 registered chat tools**, not 22 required screens. Fourteen have
direct deterministic GET routes, one has its default result embedded in the
dashboard response, and seven have no equivalent business GET route. The chat
POST endpoint is not a substitute for these missing deterministic adapters.

Do not implement seven new Explore pages. Retain the existing Explore route and
organize its sections progressively. Do not introduce another alerting engine,
rewrite the Case Store, or add new predictive behavior.

## 1. Capability-to-screen mapping and current API coverage

Sources: [agent registration](../backend/app/services/agent.py),
[HTTP routes](../backend/app/main.py), [Explore](../frontend/src/routes/Dashboard.jsx),
[chat result rendering](../frontend/src/chat/AskCelesteOverlay.jsx), and
[Customer Predictions](../frontend/src/routes/CustomerPredictions.jsx).
“Chat card” below means a visualization already exists inside Ask Celeste; it
does not mean the corresponding deterministic HTTP route exists.

| Registered tool | Current visual access | Deterministic HTTP coverage today | Proposed home / reuse decision |
| --- | --- | --- | --- |
| `get_sales_summary` | Inline SalesSummaryCard in chat; Explore has a different monthly rollup | No exact route. `/dashboard/summary` differs in periods, states, and comparison logic | Explore → Sales; retain dashboard rollup and reconcile definitions before adding another headline |
| `scan_attention` | AttentionScanCard in chat | `GET /intelligence/attention`, no parameters | Retain as explicitly monthly analytical chat capability; management attention comes from existing cases/brief, not a second feed |
| `list_metrics` | Chat tool metadata; target forms use registry metadata | `GET /intelligence/metrics` | Shared metadata for existing target forms; no page |
| `investigate_metric` | InvestigateMetricCard in chat; enriched source of case findings | `GET /intelligence/investigate/{metric_id}` | Raw analytical detail in chat/Explore when needed; stored case detail remains the management investigation |
| `get_customer_intelligence` | CustomerIntelligence chat card; Explore segments and at-risk preview | `GET /customers/intelligence` | Explore → Customers; preserve existing view, separate historical segmentation from predictions |
| `get_inventory_depth` | InventoryDepth chat card; Explore inventory values | `GET /inventory/depth` | Explore → Inventory; reuse existing section, resolve scope issue before comparing with outlet-scoped stock |
| `get_operations_summary` | OperationsSummaryCard; Explore outlet scorecard | `GET /operations/summary` | Preserve in Explore Overview, with operational context linked from Channels; it is not channel-level delivery data |
| `get_forward_look` | ForwardLook chat card; Today forecast context | `GET /forecast/forward-look` | Explore → Sales, with limited Today context; retain “trend projection” caveat |
| `get_top_products` | TopProductsCard; Explore top products | Default all-time result embedded as `top_products` in `/dashboard/summary`; no standalone period-selectable route | Explore → Products; reuse current chart, later expose existing parameterized function if needed |
| `get_top_cancelled_products` | CancelledProductsCard | No exact route | Explore → Products, later; label cancelled-order line counts, not units lost |
| `get_gross_margin` | MarginCard | No exact route; registered `gross_margin` metric separately has a raw investigation GET | Explore → Profitability; expose total service, reuse existing metric for monthly trend/outlet contributors where appropriate |
| `get_channel_breakdown` | ChannelBreakdownCard; frontend fetch helper exists but no current route component uses it | `GET /channels/breakdown?period=...`; explicit date pair not forwarded | Explore → Channels; existing route can support first contribution view after date-boundary alignment |
| `get_uber_lost_sales` | UberLostSalesCard | No exact route; `uber_failure_rate` is a different metric already available via investigation GET | Explore → Channels, separate from Uber order failures |
| `get_inventory_by_outlet` | InventoryByOutletCard | No exact route | Explore → Inventory; pair with existing stock-alert/coverage data, not invented historical inventory trends |
| `get_product_lookup` | ProductLookupCard | No exact route | Explore → Products; reusable product drill-down, including Inventory links once an adapter exists |
| `get_stock_alerts` | StockAlertsCard; Customer Predictions and finding detail use the API | `GET /inventory/stock-alerts` | Reuse in Explore → Inventory; distinguish outlet-product incidences from distinct products |
| `get_product_listing` | ProductListingCard | No exact route | Explore → Products; product detail tab, distinct from stock availability |
| `get_basket_analysis` | BasketAnalysisCard; existing frontend API helper | `GET /products/basket-analysis`, default period only | Explore → Products, later; reuse deterministic result, fetch on demand |
| `get_loss_and_damage` | LossAndDamageCard; existing frontend API helper | `GET /operations/loss-and-damage`, default period only | Explore → Inventory; not Profitability until a real monetary valuation exists |
| `get_loyalty_and_promotions` | LoyaltyPromotionsCard; existing frontend API helper | `GET /customers/loyalty`, default period only | Explore → Customers, later; keep points, catalog discounts, and realized revenue distinct |
| `get_brand_and_department_mix` | BrandMixCard; Explore department chart | `GET /products/brand-mix`, default period only | Explore → Products; preserve department chart, optionally expose already-returned brand ranking |
| `get_revenue_concentration` | ConcentrationCard; existing frontend API helper | `GET /products/concentration`, default period only | Explore → Sales, later; reconcile revenue denominator before presenting shares |

### Existing functionality outside the chat-tool registry

- Preserve Explore's business health, outlet performance, inventory segment values,
  customer segments/at-risk preview, department mix, top products, all-time hourly
  order distribution, and link to Customer Predictions.
- `/dashboard/summary` additionally returns monthly sales, top customers, stock
  counts, and purchase-spend data. Returning a field does not prove it is currently
  visualized or suitable for the same reporting scope.
- Customer Predictions already has summary/list/search, next-basket, lifecycle,
  actual-versus-predicted orders, outlet behavior/forecast, stock alerts, and
  top-product coverage APIs. Preserve their behavior and existing confidence
  explanations; do not infer a new prediction model from a redesigned chart.
- `/inventory/top-product-stock-coverage` already supplies product cover/demand;
  `/inventory/stuck-stock-counts` supplies incomplete count-session evidence.
  A stuck count's unscanned value is potential reporting distortion, not proven shrinkage.
- `/brief`, `/cases`, `/cases/{case_id}`, action-save/objective APIs, and
  notifications are management infrastructure, not new Explore tools.

## 2. Actual tool contracts

### Shared conventions and their limits

**P** means optional `period`, `start_date`, `end_date` as implemented in
[sales.py](../mcp_server/tools/sales.py). A valid date pair takes precedence;
otherwise `YYYY-MM`, a rolling `Nd` window, or last calendar month by wall clock.
Rolling windows end on the latest scoped successful order date. The helper
reverses a reversed date pair and silently falls back on missing/malformed pairs.
Explicit/rolling ranges are always marked non-partial; calendar-month partialness
uses the latest successful order date. These are current behaviors, not all
suitable for unvalidated browser input. The inclusive end-date defect is below.

**Live read** means computed on demand against the reporting connection. It does
not establish how recently Odoo data was synchronized. A returned `period` or an
HTTP retrieval timestamp is not a source-freshness timestamp.

Sales money is in Rs. `amount_total` and line `price_subtotal` are different bases;
do not combine them under an unqualified revenue/coverage label. Product quantities
can have different units of measure; summed quantities are not SKU counts.

The configured retail scope is `CELESTE_STORE_IDS=(1,2,45,46)`, from
[store_scope.py](../backend/app/db/store_scope.py). Several older comments/domain
docs still say five. Warehouse joins identify physical locations and do not prove
exclusive ownership of that stock by Celeste's retail storefronts.

| Tool / implementation | Calculation and source | Parameters / period | Returned fields and units | Freshness and limits |
| --- | --- | --- | --- | --- |
| `get_sales_summary` — [sales.py](../mcp_server/tools/sales.py) | Scoped `sale_order` states sale/done; fallback to posted `account_move` invoices | P | `period`, `revenue` Rs, `orders` count, `average_order_value` Rs/order, `growth_vs_previous_month_pct`, `is_partial_period`, `days_elapsed`, `days_in_period` | Live read; partial flag anchored to scoped order max date; invoice fallback/comparison inconsistency requires repair |
| `scan_attention` — [intelligence.py](../mcp_server/tools/intelligence.py) | All registered metric trends/anomalies; first available dimension's first contributor | None; latest complete month independently per metric | `flagged[]` with `metric_id,label,unit,category,latest_month,latest_value,pct_change_vs_baseline,persistence,top_driver{dimension,name,delta}`; `metrics_checked,metrics_normal` counts | Live monthly read; no case ID, evidence package, scan timestamp, or weekly/snapshot scan; sparse-history failure confirmed |
| `list_metrics` — same module | Registry metadata, no reporting query | None | `[{id,label,unit,direction,dimensions[{id,label}],notes,supports_weekly_measurement}]` | Static application configuration; units currency/percent/count; no freshness implication |
| `investigate_metric` — same module | Metric trend, common anomaly check, persistence, ranked dimensions | Required `metric_id`; no selectable period/horizon | `metric{id,label,unit,direction,notes}`, `trend[]`, `anomaly`, `persistence`, `breakdowns[]`, `notes`; invalid ID returns `error` | Live monthly read; trend points contain `month,value,is_partial` and rate numerator/denominator; not stored case evidence |
| `get_customer_intelligence` — [customers.py](../backend/app/services/customers.py) | `sale_order` sale + `res_partner`; lifetime repeat-customer segmentation and registry retention trend | None; lifetime recency using max order date; monthly retention | `as_of_date`, `repeat_purchase_rate_pct`, `segments[{id,label,count,revenue}]`, `at_risk_customers[{name,revenue,days_since_last_order}]`, `retention_trend[{month,retention_pct,is_partial}]` | Live; segmentation is currently unscoped while retention is outlet-scoped; thresholds 28/60 days, not individual reorder predictions |
| `get_inventory_depth` — [inventory.py](../backend/app/services/inventory.py) | Internal `stock_quant` with product/template data and trailing-30-day sale lines; dead/overstock/healthy | None; current stock plus latest-order-anchored 30-day velocity | `as_of_date`, `segments[{id,label,count,units,value,value_coverage}]`, `dead_stock_items[]`, `overstock_items[]` with `name,units,estimated_value,days_of_stock` | Live; as-of is sales anchor, not stock timestamp; >90 days cover is overstock; unscoped SQL; eight displayed items per excess-stock ranking |
| `get_operations_summary` — [operations.py](../backend/app/services/operations.py) | Registry trends plus `stock_picking`, picking type, scoped orders/outlets and picking/packing/dispatch timestamps | None; latest complete delivery month; some headline metrics choose their own latest month | `headline_month`, `headline_delayed_rate_pct`, delayed count/total/growth, `median_fulfillment_minutes`, revenue/orders/cancellation headlines and growth; `months[]`, `branches[]`, `funnel_stages[{name,count,pct}]`, `funnel_wait_times[{name,minutes}]` | Live; branches contain orders, Rs revenue, cancellation/delay %, median minutes; delay means >24h after scheduled picking time, not promised customer delivery |
| `get_forward_look` — [forecast.py](../backend/app/services/forecast.py) | Existing revenue/order metric trends; last three complete month-to-month growth rates | None | `revenue` / `orders`: nullable `{based_on_month,based_on_value,recent_growth_rates_pct,projected_low,projected_mid,projected_high}`; `stable_months_used,note` | Live; Rs or order counts; insufficient history gives null; simple projection, not a calibrated predictive interval |
| `get_top_products` — [dashboard.py](../backend/app/services/dashboard.py) | Sale-state order lines, products/templates; sum line subtotal and quantity | P, but omission uniquely means **all-time**, not last month | `{period,products:[{name,revenue,qty}]}`; top eight by Rs line revenue | Live; default `period=null`; no partial/source timestamp; product names are grouping keys |
| `get_top_cancelled_products` — same module | Lines belonging to cancelled sale orders, grouped by product name | None; all-time | `[{name,times_cancelled}]`, top eight; count of line rows | Live; no date, quantity, removed-item, or source timestamp; not the same as Uber removals |
| `get_gross_margin` — [margin.py](../backend/app/services/margin.py) | Sale/done lines with positive current `celeste_cost_price_ex_vat`; subtotal minus quantity × cost | P | `period,revenue,revenue_with_cost_data,cost_of_goods_sold,gross_margin_amount` Rs; `gross_margin_pct,cost_data_coverage_pct`; `is_partial_period,days_elapsed,days_in_period` | Live; no dimensions/history/comparison in this function; cost is current catalog cost, not historical COGS; missing covered revenue gives null margin |
| `get_channel_breakdown` — [channels.py](../backend/app/services/channels.py) | Sale/done `sale_order`; PickMe FK first, Uber FK second, else Direct | P in service; HTTP accepts only `period` | `period,is_partial_period,days_elapsed,days_in_period,channels:[{name,orders,revenue,revenue_share_pct}]` | Live; Rs order totals, counts, percentage shares; no failure, cancellation, or delivery fields and no comparison series |
| `get_uber_lost_sales` — [lost_sales.py](../backend/app/services/lost_sales.py) | Scoped order lines with `ue_item_removed`; removal reasons, product names; sum surviving `price_unit` | P | Period/partial/day fields; `removed_lines,total_lines,removed_rate_pct,estimated_lost_revenue,estimate_note,top_reasons[{reason,count}],top_products[{name,times_removed}]` | Live; total_lines is **all scoped lines**, not Uber-only lines; no order-state filter; estimate assumes one unit per removed line; eight reasons/products |
| `get_inventory_by_outlet` — [inventory.py](../backend/app/services/inventory.py) | Positive internal quant rows joined through warehouse to configured outlets; retail inventory products | None; point-in-time | `[{name,units,estimated_value}]`; physical quantity and estimated Rs cost value | Live; no source timestamp, SKU count, OOS count, cover, product list, or history; missing costs treated as zero; negative quants excluded |
| `get_product_lookup` — [product_lookup.py](../backend/app/services/product_lookup.py) | Resolve product template by SKU/name, then line sales/margin, channel split, warehouse stock | Required `query` + P | `query,status` = found/not_found/ambiguous; `matches[{name,sku}]` when ambiguous; found: `product{name,sku},period,is_partial_period,sales{revenue,qty_sold,orders},margin{gross_margin_pct,cost_data_coverage_pct},uber_removals,channel_mix[{name,revenue,qty}],stock_by_outlet[{name,qty}]` | Live mixed temporal scope: historical sales and current stock; no stable IDs in returned product/outlet objects; removals here limited to sale/done unlike lost-sales tool |
| `get_stock_alerts` — [stock_alerts.py](../backend/app/services/stock_alerts.py) | Scoped product/outlet trailing sales + current internal stock; only retail products with positive recent velocity | None; 30-day sales anchor; low stock <3 days cover | `as_of_date,segments_by_outlet[{outlet,out_of_stock,low_stock}],top_out_of_stock[],top_low_stock[],low_stock_threshold_days,distinct_out_of_stock_products`; items: `outlet,name,sku,qty,sold_30d`, plus low-stock `days_of_stock` | Live; counts are product-outlet rows, distinct headline is deduped by SKU; top lists limited to ten; distinct count omitted in empty response; as-of is not stock-sync time |
| `get_product_listing` — [listings.py](../backend/app/services/listings.py) | Product resolver plus `res_store_product`, products, configured outlets | Required `query`; current listing | Same lookup status variants; found: `product`, `listings[{outlet,available_in_uber,available_in_pickme,price}]`, `not_listed_at[]` | Live; booleans and nullable Rs listing price; no source timestamp; listing does not imply inventory availability |
| `get_basket_analysis` — [basket.py](../backend/app/services/basket.py) | Distinct retail product pairs within scoped sale/done orders | P; minimum 15 orders together | `period,is_partial_period,total_orders,min_pair_orders,pairs_considered,top_pairs_by_frequency[],top_pairs_by_affinity[]`; pair: `product_a,product_b,orders_together,product_a_order_count,product_b_order_count,confidence_a_to_b_pct,confidence_b_to_a_pct,lift` | Live; ten rows per ranking; lift is a ratio, not a percentage or causal effect; no source timestamp; no qualifying pairs currently reports total_orders=0 even if orders exist |
| `get_loss_and_damage` — [loss_and_damage.py](../backend/app/services/loss_and_damage.py) | `stock_move_shortage`, shortage reasons/pickings; damaged orders/lines; return orders/lines, via configured warehouses | P, applied to each source's event date | `period,shortage_by_reason[{reason,lines,qty}],shortage_top_products[{name,lines,qty}],shortage_by_store[{outlet,lines,qty}],damaged_top_products[{name,lines,qty}],damaged_by_store[{outlet,damaged_orders,qty}],returns_orders,returns_qty,returns_supplier_count` | Live; no partial or source-freshness field; counts/quantities, **no Rs loss value**; warehouse-side events not exclusive storefront attribution |
| `get_loyalty_and_promotions` — [loyalty.py](../backend/app/services/loyalty.py) | Loyalty history→card→order→customer; approved promotion/line records overlapping period | P | `period,points_issued,points_used,redemption_rate_pct,active_cards,top_customers_by_points[{customer,issued,used}],active_promotions[{name,applied_to,start_date,end_date,product_count,total_discount_value,avg_discount_pct}]` | Live; points are not money; promotion discount value is summed catalog price difference, not actual redeemed spend; promotions have no outlet dimension; no partial/freshness field |
| `get_brand_and_department_mix` — [brand_mix.py](../backend/app/services/brand_mix.py) | Scoped sale/done lines + `celeste_brand`, `celeste_department` FKs | P | `period,total_revenue,top_brands[{brand,revenue,qty}],departments[{department,revenue,revenue_share_pct}]` | Live; Rs line subtotals; top twelve linked brands; all departments including Unlinked; no partial/freshness field; brand top-N is not total revenue |
| `get_revenue_concentration` — [concentration.py](../backend/app/services/concentration.py) | Top-ten scoped product line revenue and customer order revenue, divided by sales-summary revenue | P | `period,is_partial_period,total_revenue,top_n,top_products_revenue_share_pct,top_customers_revenue_share_pct,top_products[{name,revenue,share_pct}],top_customers[...]` | Live; product subtotal/order-total denominator mismatch; customers/products grouped by name; no source timestamp |

### Registry coverage, not a new navigation list

The twelve metrics are cancellation rate, revenue, order volume, delayed delivery
rate, Uber failure rate, PickMe cancellation rate, customer retention, gross
margin, average order value, supplier fill rate, purchase price variance, and
supplier concentration. Only cancellation and delayed delivery support trailing
weekly measurement. Supplier metrics intentionally use business-wide
`vendor_delay_report` / `purchase_order` sources, not a four-outlet sales scope.
Retain their notes wherever these results appear; the sidebar cannot truthfully
say every number in the application uses the same outlet scope.

## 3. Detection/investigation duplication audit

### Actual call graph

```text
Ask Celeste.scan_attention ─── intelligence.METRICS + get_trend
                              + detect_anomaly + get_breakdown

Ask Celeste.investigate_metric ─── intelligence.investigate(metric_id)
                                             │
Daily scan ─── investigation.build_investigation ── same investigate()
           │       + evidence + impact + recommendations + corroboration
           ├── scan_short_term ── same metric engine, trailing 7-day windows
           ├── state_findings + incident_engine ── snapshot/operational checks
           └── case_engine.sync_cases ── persisted cases and observations
                        └── /cases/{case_id} ── stored evidence + all actions
```

Sources: [intelligence.py](../mcp_server/tools/intelligence.py),
[investigation.py](../mcp_server/tools/investigation.py),
[brain.py](../backend/app/services/brain.py),
[case_engine.py](../backend/app/services/case_engine.py).

| Concern | Chat analytical entry points | Case pipeline | Integration decision |
| --- | --- | --- | --- |
| Definitions/thresholds | Same registry; anomaly when absolute baseline change ≥15% OR absolute z-score ≥1.5; direction determines critical/opportunity | Same monthly definitions and detector | Reuse; no second frontend detector |
| Period | Latest complete month per metric; latest trend row treated as partial | Monthly plus selected trailing-seven-day and snapshot findings | Always label horizon and actual period; different horizons can correctly produce different cases |
| Baseline | Mean of earlier complete trend values; dimensional delta uses prior month/window | Same computation stored in findings | A baseline percentage change and prior-month contributor delta are not identical comparisons |
| Evidence | Attention scan has one contributor; raw investigation has trend/persistence/breakdowns | Adds root-cause checks, data quality, impact, memory where supported, recommendations, cross-metric checks | Do not call raw investigation “deeper case evidence” by default |
| Identity | `metric_id`, no persistent case identity | UUID case; active identity `(metric_id, domain, horizon)`; observations/finding IDs; recurrences can link to a new case | Never synthesize a case link from a title or metric alone |
| Time of read | Live request, no stored scan timestamp in payload | Brief cache up to 24h plus persisted observation/decision timestamps | Label live analysis versus stored evidence; a newer live result does not overwrite decision evidence |
| Resolution | Not represented | Verification window, objectives, action targets, dismissal/reopen/recurrence history | Use Case Store status; a live normal metric does not immediately prove case resolution |

**scan_attention recommendation:** no separate management page or badge count.
Keep its monthly analytical use in chat, explicitly distinguish it from “all
open cases,” and fix sparse-data behavior before exposing it more widely. For a
future case-aware conversational briefing, use an explicit read-only adapter to
existing case/brief data; do not silently reinterpret its current tool contract.

**investigate_metric recommendation:** use stored case detail first when reviewing
a case. A fresh raw metric read may be offered as separately labeled analytical
context, particularly for normal metrics that have no case. It is not additional
root-cause evidence and must not update the Case Store. Related-case navigation
may list `/cases?metric_id=...`; an exact case link requires a stored matching
observation and the correct period/horizon/recurrence, not a guessed association.

### Confirmed inconsistencies and integration prerequisites

1. **Inclusive final-day exclusion.** `_period_bounds` returns the final day as a
   date string; many tools compare timestamp columns with `<= :end`. This includes
   midnight but excludes later events on that final day. The metric engine uses
   half-open month bounds (`< next_month`). Margin, channels, sales, removals,
   product lookups, baskets, mix, concentration, and some loss/loyalty queries can
   therefore disagree with monthly case results. Repair the shared period/query
   boundary in backend services with regression tests; do not compensate in JS.

2. **Sales source mismatch.** `get_sales_summary` uses orders or a fallback to
   unscoped invoices for the current period, but its prior-period SQL UNION ALLs
   scoped orders and unscoped invoices. This can double-count prior revenue and
   change scope. The fallback also calls invoice counts “orders.” Preserve one
   source/basis in a comparison before promoting these numbers as shared KPIs.
   This conflicts with ADR-0001's requirement for outlet-scoped sales.

3. **Margin/coverage basis mismatch.** Margin uses line `price_subtotal` and current
   ex-VAT catalog cost; its coverage divides covered line revenue by sales-summary
   `amount_total`. Full line cost coverage can appear below 100% because the
   denominator is different. Product concentration has the same mixed revenue
   basis. Do not present either as reconciled accounting coverage. Correct the
   deterministic service once, then reuse it everywhere. Catalog cost can change
   a historical calculation; do not label the result booked historical COGS/net profit.

4. **Outlet scope is not uniform.** `inventory.INVENTORY_DEPTH_SQL`, dashboard
   inventory counts, `customers.CUSTOMER_STATS_SQL`, and several prediction/lifecycle
   base queries lack the retail scope filter. New outlet inventory and stock-alert
   queries are scoped. Customer segmentation also combines unscoped lifetime
   customers with a scoped retention series. Preserve features but correct or
   explicitly identify their actual population before presenting totals together.
   Unscoped sales/customer queries conflict with ADR-0001; intentionally
   business-wide supplier metrics should instead keep their explicit scope note.

5. **Different reporting clocks.** Explore's headline is latest complete data
   month; brand mix defaults to previous wall-clock month; top products and hourly
   orders are all-time. Operations headline metrics can also select different
   last-complete months. A single “Data through …” label currently obscures these
   differences. Give each section its actual period; forward supported parameters
   before offering any shared period control. Preserve explicit all-time views.

6. **Uber failure is not Uber item removal.** The registry's `uber_failure_rate`
   reads `uber_order.state='failed'` over failed/completed/cancelled/accepted orders.
   `get_uber_lost_sales` counts removed order lines and estimates one unit's price
   per line. Its percentage denominator includes all scoped order lines, not just
   Uber. Product lookup applies yet another sale/done filter. Never combine these
   as a single loss/failure metric, infer causation, or label line counts as orders.
   Keep the estimate note visible; the claimed lower bound needs live methodology
   validation for discounts/quantities before being presented as a guarantee.

7. **Inventory definitions differ.** Outlet totals sum positive quant rows and
   value missing costs at zero; inventory-depth values apply `_usable_cost`; stock
   alerts net on-hand stock and use <3 days cover; dashboard stock counts use a
   fixed ≤10-unit threshold across unscoped internal locations. These are neither
   one reconciled valuation nor one low-stock count. No outlet-total row can mean
   no positive stock, not necessarily zero known stock. Reservations/free stock,
   unit-of-measure normalization, source freshness, and complete product drill-down
   are not supplied by the outlet-total contract.

8. **scan_attention has a reproduced sparse-history failure.** `detect_anomaly`
   returns no `category` for fewer than two complete observations; scan_attention
   indexes `anomaly['category']` and raises `KeyError`. Its “strongest driver” is
   actually the first available dimension's first ranked contributor, not a
   cross-dimension maximum. Fix the guard and either qualify the wording or use an
   agreed existing ranking; insufficient data must not be counted as “normal.”

9. **Today still uses legacy outcome presentation.** Home reads `/brief` and
   `/outcomes/summary`, and uses `deriveLifecycle` for prior findings. The latter
   endpoint aggregates legacy tracking tables, not case resolution. Home/SignalCard
   do not currently use `case_id` to navigate to a case; the Home chat prompt is
   only `Tell me more about: <title>`. The redesign must make case navigation
   authoritative and avoid presenting legacy “improved” as case “resolved.”

10. **Latest action is not all actions.** `/cases` attaches only the most recently
    updated action; Actions filters on that one action's relevance/status, and
    Outcomes classifies using it. An older relevant/in-progress/blocked action can
    disappear behind a newer irrelevant action. `/cases/{id}` already returns all
    actions. Preserve individual rows and finding IDs using that existing detail
    contract, with lazy expansion; if a complete cross-case action queue needs a
    batch read adapter, add only a read projection over existing rows, not a new store.

11. **Workspace totals are truncated.** Actions and Outcomes fetch only the first
    200 cases and then filter/group locally. Outcomes says “Every business case”
    despite this limit. Use pagination and distinguish loaded-page counts from
    whole-dataset totals; do not show an empty filtered first page as “no actions.”

12. **UI/description drift.** The chat SalesSummaryCard renders `$`; the backend
    and other screens use Rs. The channel route's docstring says current month,
    but the implementation defaults to last month. Chat scan descriptions list
    five metrics while the registry has twelve; outlet comments still say five
    although four IDs are configured. Correct these labels as components migrate.

13. **Missing coverage must remain visible.** The empty stock-alert response omits
    its distinct-product field; basket analysis reports zero total orders when no
    pair clears the threshold; several totals turn null into zero. Add explicit
    contract fixtures before reusing these values in prominent executive cards.
    Stable IDs are absent in several product/outlet result objects; do not invent
    product identity from a display name. Use SKU lookup and its ambiguity handling.

## 4. First-wave capabilities and smallest adapters

All route names below marked “proposed” are absent today. Reuse the existing
functions unchanged at the transport boundary; any correctness repair belongs
inside the shared service and must also apply to chat. Do not add SQL or model
calls to adapters. Keep existing authentication and frontend error handling.

### Inventory by outlet — first integration candidate

- Proposed `GET /inventory/by-outlet`: directly await
  `inventory.get_inventory_by_outlet()`, return `{status:'ok', outlets:[...]}`.
- Reuse `/inventory/stock-alerts` for outlet-product OOS/low-stock counts and its
  returned top items; reuse `/inventory/top-product-stock-coverage` for the
  existing company-wide product-cover view. The latter is not per-outlet cover.
- Show separate panels for positive on-hand totals/value and active-selling
  product availability. Preserve units and thresholds. Do not average product
  cover into an invented outlet-wide cover score.
- Initial drill-down can use returned top items, explicitly marked top ten. A
  complete per-outlet product browser is not already in this contract. Later
  proposed `GET /products/lookup?query=...` can call the existing resolver; retain
  not-found and ambiguous states rather than treating them as server failures.
- Show “current snapshot; stock source timestamp unavailable” if no trustworthy
  inventory as-of exists. Retrieval time may be separate metadata; no fake trend.
- Resolve stock scope/value differences before presenting existing depth and new
  outlet totals as comparable. Missing costs must not imply complete valuation.

### Channels and operational failures — second candidate

- Existing `GET /channels/breakdown?period=YYYY-MM` is sufficient for one period's
  channel revenue/orders/share. Extend only to forward the already-supported
  `start_date/end_date` pair when that UI is actually needed.
- Existing `GET /intelligence/investigate/uber_failure_rate` provides monthly
  failure-rate trend and outlet contribution breakdown through the shared engine.
  Reuse these numeric fields as analysis, with distinct live/monthly labeling;
  it must not generate a new management finding. The same pattern is possible
  for `pickme_cancellation_rate` but is not a new first-wave requirement.
- Proposed `GET /channels/uber-lost-sales` forwards P to
  `lost_sales.get_uber_lost_sales()` in a worker thread and wraps its existing dict.
- Show contributions, failed orders, and removed items as separate measures with
  their own denominators. Preserve the existing removal-value estimate note.
- Existing operation/fulfillment panels can remain linked context. They do not
  support claims about channel-specific promised delivery or a causal explanation
  for channel performance.
- Prerequisites: date-boundary correction, explicit period labels, failure/removal
  terminology, and source freshness not inferred from the briefing timestamp.

### Gross margin — third candidate, after reconciliation

- Proposed `GET /profitability/gross-margin` forwards P to
  `margin.get_gross_margin()` in a worker thread, wrapping its existing dict.
- First view: covered revenue, calculated cost, gross margin amount/percentage,
  meaningful cost coverage after denominator correction, and partial-period note.
- Existing `GET /intelligence/investigate/gross_margin` already supplies a monthly
  margin series and **outlet** contributors. Rate trend `numerator` is covered
  gross-profit amount; `denominator` is covered subtotal revenue. This is useful
  existing data, not a reason to recreate margin calculations in the browser.
- The raw investigation chooses its latest complete month; it cannot obey an
  arbitrary profitability date filter. Label it separately or defer it if the
  periods do not align. Contributor ranks are changes relative to prior month,
  not a full low-margin outlet ranking.
- Neither contract supplies category/brand/product margin rankings. Product
  lookup can inspect one resolved product later; it is not a ranking API.
- Gate on corrected bounds and revenue basis, zero/missing-cost behavior, and a
  visible “current catalog cost estimate” methodology. No net-profit claim.

### Later adapters, only for selected workflows

The other absent equivalents are proposed sales-summary, cancelled-products,
product-lookup, and product-listing GETs. A standalone top-products GET would
unlock its existing period arguments without reloading the entire dashboard.
Existing basket, loss/damage, loyalty, brand-mix, and concentration routes need
parameter forwarding, not duplicate routes, if date selection is introduced.
These are a backlog, not authorization to build every adapter in this stage.

For new P adapters validate dates, both-or-neither range parameters, positive
bounded rolling windows, and start≤end before invoking the service; invalid
browser input must not silently query another period. Keep response fields and
numeric units intact. Metadata may add requested/resolved period, retrieval time,
known source as-of or null, and methodology; never fabricate missing freshness.

## 5. Final navigation and workspace boundaries

```text
Celeste / Business Brain
  MANAGEMENT
    Today                 /
    Investigate           /investigate
      Case detail         /investigate/:caseId
    Actions               /actions
    Outcomes              /outcomes
  INTELLIGENCE
    Explore               /dashboard
    Customer Predictions  /customers/predictions
  Ask Celeste             global conversation panel
  Data freshness          brief checked time + source status, clearly distinguished
```

Use a grouped desktop sidebar and compact mobile navigation. Preserve URLs,
direct case links, authentication, notification behavior, and browser history.

Explore keeps one workspace with progressively available sections:

| Section | Content to preserve or introduce | Boundary |
| --- | --- | --- |
| Overview | Existing health and outlet performance; links into analysis | No duplicate case/alert feed; each source has its own freshness |
| Sales | Monthly sales, orders, all-time hourly distribution; later concentration and projection | Different comparison baselines/periods labeled |
| Products | Existing department mix and top products; later brands, cancellations, lookup/listings, baskets | Lookup/listing/stock are distinct questions |
| Inventory | Existing depth, new outlet snapshots and existing availability data; later losses/count-session context | Snapshot versus trend; physical units versus SKU/outlet incidences |
| Channels | Channel contribution, Uber failures, removed items | No unsupported delivery breakdown or causal explanation |
| Profitability | Margin totals and supported monthly/outlet analysis | Gross-margin estimate, not net profit or historical accounting COGS |
| Customers | Existing segments; later loyalty/promotions | Reorder predictions retain their separate workspace |

Render only sections with useful supported content. Do not create empty pages or
tabs solely to match this list. Introduce category navigation within Explore as
content warrants it, rather than expanding the main sidebar per tool.

Today is a decision briefing with links to authoritative case detail, not a second
investigation editor. Actions shows individual actions, their targets and evidence
versions, grouped by case if useful. Outcomes separates action measurement/status/
confidence from case resolution policy/verdict. Explore does not auto-create or
guess cases. Ask Celeste must not claim access to case evidence until an explicit
case-data tool/context contract actually exists.

## 6. Prioritized implementation stages and acceptance gates

| Stage | Deliverable | Gate before continuing |
| --- | --- | --- |
| 0 — this audit | Capability mapping, contract inventory, inconsistency list, API gaps, navigation, tests | Complete; live data correctness remains a separate validation step |
| 1 — management/navigation alignment | Grouped shell, Today label/case links, first-class Customer Predictions navigation; preserve individual actions and distinguish outcome types | No guessed case associations, no disappearing older actions, honest pagination, stale-evidence save protection retained |
| 2 — contract prerequisites | Focused shared-service repairs for period boundaries, revenue basis, affected scoping and sparse-history handling; source/period labels | Regression fixtures prove chat and HTTP use the same corrected calculations; Case Store unchanged |
| 3 — inventory section | Reuse current depth/availability components; one outlet-total adapter and snapshot panel | Correct counts/units/scope, nullable valuation/freshness represented, no invented cover/history |
| 4 — channels section | Existing breakdown and metric-series reads; one removed-items adapter | Supported periods aligned or visibly separate; order failure and line removal never conflated |
| 5 — profitability section | One margin adapter; existing metric series/outlet analysis only where period-compatible | Covered revenue/COGS/margin reconcile, cost method disclosed, no unsupported dimensions |
| 6 — remaining frontend migration | Restyled AI cards, existing Explore/customer views, then selected product/customer analysis | Feature parity, Rs formatting, accessible/mobile layouts, no duplicate engine or unnecessary pages |

Stages 1 and 2 can progress independently, but no new chart should rely on a known
conflicting contract. Within the first wave, inventory is the simplest new adapter;
channels already has much of its HTTP coverage; profitability has the greatest
reconciliation burden. Reorder only if those readiness conditions change.

### Acceptance criteria and test coverage

| Area | Required tests / evidence |
| --- | --- |
| Service-to-HTTP parity | Patch deterministic functions; assert each adapter forwards parameters once and returns all original values, nulls, units, notes, and list shape; assert no model or case-write call |
| Period boundaries | Final day at 00:00/noon/23:59 included; next day excluded; February/leap year; rolling/custom/month defaults; partial/stale data; invalid dates rejected |
| Financial reconciliation | No invoice/order double count; tax-inclusive versus line-subtotal basis explicit; full/missing/invalid cost coverage; zero revenue; current-cost semantics; concentration denominator |
| Channel semantics | PickMe/Uber FK classification; failed versus cancelled/accepted state denominator; removal lines versus orders; non-Uber denominator; one-unit estimate note preserved |
| Inventory | Same product at two outlets counts two incidences and one distinct SKU under declared identity; missing/null SKU behavior; empty response; negative/reserved/absent rows; cost coverage; top-ten truncation; no fabricated history |
| Detection parity | Fixed reporting fixtures give the same monthly anomaly in scan_attention and build_investigation; sparse/one-period input degrades explicitly; weekly/snapshot exclusions documented; no new cases from read-only analysis |
| Case integrity | Multiple actions retained even if latest is irrelevant; all rows link by stored case/finding IDs; recurrence does not inherit prior actions; monthly and weekly cases kept distinct |
| Decision evidence | Correct observation version sent on save; stale version produces review flow; scan/save lock and transaction guarantees retained; saved decision evidence unchanged by a live analytical refresh |
| Outcomes | Completed action can coexist with open case; achieved target is not causal attribution; recovered/captured/expired/inconclusive remain backend verdicts; old legacy improvement never becomes case resolution |
| UI and pagination | Existing Explore/Customer Predictions/chat features retained; second/third case pages visible; summaries not mislabeled as all records; per-section loading/error/empty/partial/freshness states |
| Browser | Grouped navigation and deep links; keyboard/focus behavior; mobile overflow/charts; auth/sign-out; context prompt does not claim case evidence; case journey followed through action/outcome |
| Performance | No per-tool pages or duplicate SQL; lazy heavy sections; query keys include supported periods/queries; management pages do not trigger additional analytical scans just to restyle evidence |

### Existing coverage checked in this audit

- **102 tests passed** across `test_case_engine.py`, `test_case_api.py`,
  `test_resolution_policy.py`, `test_phase2e.py`, `test_stock_counts.py`, and
  `test_state_findings.py`. These used isolated app databases and mocked reporting.
- **20 frontend unit tests passed** with `npm test`, covering HTTP/auth handling,
  evidence versions, and target-form semantics.
- Inspected existing `frontend/e2e/core-workflow.spec.js`: three fixture-based
  browser scenarios cover the main case/action journey, stale-evidence conflict,
  and target validation. They were **not rerun** during this source audit.
- Reproduced `scan_attention`'s `KeyError('category')` using a patched empty trend.
  Probed margin and outlet-inventory response shapes with synthetic reporting rows.
  Synthetic values were not production measurements.
- Did not run live reporting/model calls, the real-backend integration suite,
  or a new visual browser review. Existing passing tests do not establish the
  missing adapter/financial/date/scoping acceptance criteria listed above.

No application code, Case Store schema, analytical calculation, or additional
Explore functionality was changed for this audit.
