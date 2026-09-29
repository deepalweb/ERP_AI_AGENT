# Customer module: insights and sales opportunities

Reviewed 2026-09-29. Evidence: customer backend services, CustomerPredictions frontend, shared order contract, scan integration, and read-only responses from the running application's summary and lifecycle endpoints. No customer outreach or application behavior changed. API figures below are served snapshots, not an independently refreshed database census. Historical counts in source-code comments were not used as current evidence.

## Conclusion

The module has a useful foundation for purchase timing, customer lifecycle, repeat-product suggestions, and customer lookup. Its biggest missing capability is a measurable sales workflow: identify an eligible audience, explain the opportunity, choose an action, and measure incremental purchases and profit. Improving the model alone will not close this gap.

Start with high-value lapsing customers and first-to-second purchase conversion. Treat these as hypotheses to test, not promised revenue.

## What it currently does

1. `order_history.py` selects orders from configured Celeste storefront IDs and converts UTC order timestamps to Asia/Colombo. Prediction history is not restricted to a selected month; September is included when available in the source and refreshed snapshot.
2. `customer_predictions.py:compute_predictions` requires three orders and a lifetime average gap of at least one day. It uses the last five purchase gaps to calculate an expected window, then labels customers on-track, due, or overdue relative to the source's latest order.
3. `brain.py` computes prediction rows, population statistics, and lifecycle summaries during the scan and stores them in application snapshot tables. Page reloads read these snapshots; they do not themselves recompute predictions from the reporting database.
4. Lifecycle assigns customers to eight stages, plus lifetime-revenue value tiers and primary channels. The response already contains a lifecycle-by-value cross-tab.
5. Individual profiles combine live lifecycle and recent-versus-prior spending measurements with next-basket suggestions. Reorder suggestions use products purchased at least three times.
6. The page shows lookup, forecasts, outlet allocations, stock information, summary, lifecycle, and a prediction list. Basket affinity and loyalty/promotion analysis exist in separate services.

These are purchase-history heuristics. The confidence label is based on order count, not a measured probability of a future purchase. Revenue is historical spend, not customer lifetime value or contribution profit.

## Observed snapshot

Both APIs report data through **2026-09-27**. Prediction generation timestamp: **2026-09-28T06:23:02.623497**, as returned by the API without a timezone suffix.

| Measure | Served value | Interpretation |
|---|---:|---|
| Customers analyzed | 86,706 | Identified customer records in the scoped population |
| Usable timing pattern | 36,926 | About 42.6% of customers |
| Only one or two orders | 49,640 | About 57.3%; individual timing is unavailable |
| Due in timing model | 17,824 | Within personal windows, not orders guaranteed today |
| Overdue in timing model | 12,534 | Missed heuristic window, not confirmed customer loss |
| High-value and lapsing | 2,213 | Candidate retention audience |
| High-value and churned | 2,755 | Candidate reactivation audience; heuristic classification |
| New lifecycle stage | 10,079 | Not equivalent to first-order-only customers |
| Dormant / one-time stage | 40,309 | Older low-history customers |
| Primary Uber / PickMe / direct | 61,375 / 25,306 / 25 | Platform-heavy population; direct channel comparisons are too small |

Timing and lifecycle due counts differ because lifecycle also assigns New and Win-back stages. Do not add the counts or describe them as identical audiences.

## Findings to address before operational use

### High: customer lookup does not consistently enforce outlet scope

`customer_predictions.py:CUSTOMER_SEARCH_SQL` searches all completed `sale_order` records without the shared storefront filter. Its order count is also unscoped. `customer_lookup.py:PARTNER_IDENTITY_SQL` has the same unscoped order count, while lifecycle and revenue use scoped orders.

Consequences: lookup can return customers outside the intended Celeste population, and a profile can show order counts that do not match its revenue/history. This conflicts with ADR 0001. Reuse the shared scoped relation for search eligibility and counts; verify with customers ordering only outside Celeste and customers ordering in both scopes.

### High: expected sales labels overstate what is calculated

`customer_predictions.py:get_summary` returns `expected_orders` as the number of due customers and `expected_revenue` as their summed historical average order values. The observed amount is **37,521,541.42**. It has no conversion weighting or explicit forecast horizon and must not be interpreted as incremental sales or today's revenue forecast.

Rename these to “Customers in reorder window” and “Typical basket value across due customers.” Reserve expected sales terminology for a horizon-specific, backtested estimate. `_confidence_for` should be presented as history strength until calibrated against held-out future orders.

### High: freshness is mixed across components

Summary and lifecycle read scan snapshots. Profiles and product history query live reporting data. `get_predicted_next_basket` anchors those live product histories to `_latest_as_of_date()` from the cached snapshot, without an upper bound on the product-history query. A newer purchase can therefore be compared with an older reference date and produce negative recency.

Use an explicit common cutoff per calculation, include source and generation timestamps, expose stale/error states, and provide an independent customer refresh. A refresh should publish a complete consistent snapshot rather than exposing intermediate generations.

### Medium: forecast actuals and predictions cover different populations

`ACTUAL_DAILY_ORDERS_SQL` counts all qualifying scoped orders. `DAILY_FORECAST_SQL` includes only predictable repeat customers. The frontend explains that the prediction is smaller, but visually continuing the series can still suggest a forecasted business decline. Show a comparable repeat-customer actual series or clearly separate the two populations. Retain the latest-day partial marker.

### Medium: valuable segmentation exists but cannot drive work

`compute_lifecycle_summary` already returns `value_by_category`, but `CustomerLifecycleSection` renders category and channel cards rather than that cross-tab or a drill-down. The list filters by prediction status and defaults to lifetime revenue; it cannot directly retrieve “high-value lapsing customers at this outlet/channel.”

Add server-side audience filters and stable pagination. Show recent spend, change versus prior period, evidence dates, and relevant products. Historical spend alone should not determine current priority.

### Medium: related analytics need stronger interpretation

- `basket.py` computes pair frequency, directional confidence, and lift, with a 15-order minimum. It does not establish that recommending a product causes additional purchases. Join product IDs, availability, price and margin before turning pairs into suggestions. Product IDs are not currently included in its returned pair payload.
- If no pair passes the threshold, `get_basket_analysis` reports zero total orders because it derives the total from the first pair row. Count the audience independently so “no eligible pair” does not become “no sales.”
- `loyalty.py:LOYALTY_TOP_CUSTOMERS_SQL` groups by name, so different customers sharing a name are combined. Group by customer ID before using this to prioritize people.
- Promotion `total_discount_value` sums listed price differences, not discounts actually redeemed across purchased quantities. It cannot support campaign ROI without transaction-level attribution.

## Proposed sales opportunities

| Priority | Audience / question | Product capability | Success measure |
|---|---|---|---|
| 1 | Which valuable repeat customers are starting to lapse? | High-value lapsing queue with recent 30/60/90-day spend, personal cadence, last products, service issues and eligible channel | Incremental repeat-purchase rate and contribution per eligible customer |
| 1 | Which first-time customers fail to buy again? | First-order cohorts and 7/14/30-day second-order conversion, split by outlet and acquisition channel | Mature-cohort second-order conversion and time to second order |
| 2 | What is a customer likely to need next? | Existing reorder suggestions plus current outlet stock and product-level margin; separate replenishment from novel cross-sell | Incremental basket contribution, units and repeat purchase |
| 2 | Are service failures preceding customer inactivity? | Join cancellations and reliable removed-item evidence to customer histories; compare subsequent retention with appropriate comparison groups | Repeat purchase following service recovery; association alone is not causation |
| 3 | Which dormant customers can be reactivated profitably? | Segment by previous frequency, recent historical value, inactivity and reachable channel; test small audiences before expansion | Incremental reactivation and profit after incentive/contact costs |
| 3 | Which promotions actually work? | Campaign eligibility, assignment, exposure/redemption, costs and outcome records | Incremental revenue and contribution against an untreated holdout |

All-history data should remain available for customer history and cohorts. Operational comparisons should use explicit equivalent periods. For September cohorts without 30 days of observation, display “not yet mature” rather than count them as failed conversions.

Platform customer identifiers and phone availability do not establish that customers are reachable through an authorized marketing channel. Add reachability/eligibility as separate fields; do not merge customer identities solely by name or automatically contact/export these audiences.

## Recommended Customer page

1. **Opportunity overview:** high-value lapsing customers, recent first-order customers awaiting a second order, due repeat customers; each tile opens its exact audience. Show source cutoff and generation time.
2. **Customer growth charts:** monthly acquisition and repeat purchasers; first-order cohort retention heatmap; lifecycle-by-value matrix; recent spend versus purchase-frequency scatterplot. Include counts, denominator, scope, and period in chart details.
3. **Prioritized audience table:** reason, recent/prior spend, cadence, outlet/channel, suggested products, stock status, evidence date, and an action owner/status when tracking is implemented.
4. **Customer detail:** order/spend timeline, repeat products, category preferences, cancellations/removed-item history, and prior interventions with measured outcomes.
5. **Experiment results:** audience size, treatment/control assignment, mature observation window, incremental purchase rate and margin. Move detailed inventory/forecast sections lower or into their dedicated views so customer decisions appear first.

## Implementation sequence and validation

**First:** fix scoped lookup, snapshot consistency/freshness, and misleading sales/confidence labels. Verify source cutoffs and state eligibility with regression fixtures. Backtest timing with historical cutoffs that exclude future purchases; report coverage and accuracy by history strength/channel, not a single headline accuracy.

**Second:** expose lifecycle/value/channel/outlet audience filters and the existing value cross-tab. Build the high-value lapsing queue and first-to-second cohort chart using all available scoped history. Test partial September cohorts and zero/unknown denominators.

**Third:** connect replenishment and basket affinity to stocked, economically sensible products. Keep explanations inspectable. Test recommendations on a small eligible audience with a randomized untreated holdout where operationally feasible.

**Fourth:** add customer action and experiment records. Define assignment time, eligible audience, offer cost, observation horizon, and contribution calculation before evaluating an intervention. A before/after sales increase alone cannot establish uplift; natural reorders, seasonality and channel mix can explain it.

This review does not estimate guaranteed sales uplift. Current APIs describe historical behavior and candidate audiences; campaign exposure, costs, eligibility and controlled outcomes need additional implementation.
