# App logic and mathematics inventory

Reviewed against the working tree on 2026-09-29, with section 10 added 2026-10-08 for the warehouse-to-outlet transfer and dispatch detectors built after that date. This covers the main business analytics and decision paths, not every SQL expression or utility. It describes implemented calculations, not a validation that thresholds predict business outcomes accurately. Source files are linked for the exact eligibility, joins, and exclusions.

## 1. Data selection and time

- Celeste order analytics generally use storefront IDs `1, 2, 45, 46` and confirmed states `sale`, `done`, `received`. Cancellations are added only where required by a denominator.
- Customer and performance queries use the shared order relation, converting Odoo UTC timestamps to `Asia/Colombo` before calendar grouping. Do not assume every older analytics query uses this conversion: several registered trend SQL queries still group raw timestamps.
- Procurement is generally business-wide. Shared warehouse stock is not equivalent to a single storefront's inventory.
- Monthly anomaly detection excludes partial months. Current-period analysis separately includes the latest month with matched elapsed-time comparisons.
- Missing measurements and zero exposure are not evidence that a condition recovered.

Sources: [store_scope.py](../../backend/app/db/store_scope.py), [order_history.py](../../backend/app/services/order_history.py), [measurement.py](../../mcp_server/tools/measurement.py).

## 2. Registered business metrics

There are currently 15 registered metrics. Percentages multiply the indicated ratio by 100.

| Metric | Calculation / interpretation |
|---|---|
| Revenue | Sum of confirmed order totals |
| Order volume | Count of confirmed orders |
| Average order value | Confirmed revenue / confirmed orders |
| Cancellation rate | Cancelled / (confirmed + cancelled orders) |
| Out-of-stock cancellation rate | Orders cancelled with the specific out-of-stock `cancel_info` reason / (confirmed + cancelled orders) |
| Customer return rate | Distinct qualifying non-supplier return records / confirmed orders in the corresponding month; not a per-order linked return probability |
| Severely delayed delivery rate | Qualifying completed outgoing deliveries over 24 hours after scheduled time / qualifying deliveries; scheduled-to-done is not customer-promised delivery lateness |
| Dispatch wait | Packing-to-dispatch elapsed minutes, using the registered aggregate and eligible stage timestamps |
| Uber failure rate | Failed / orders in failed, completed, cancelled, accepted states |
| PickMe cancellation rate | Cancelled + failed + rejected / those states plus completed |
| Customer retention | Customers from the prior month's active cohort ordering again in the current month / prior cohort |
| Estimated gross margin | (Eligible line revenue − quantity × current catalog cost) / eligible line revenue; only cost-covered lines |
| Supplier on-time fill | Sum(on-time quantity) / sum(total quantity); source blends timeliness and completeness |
| Purchase price mismatch | Confirmed purchase orders with nonzero price mismatch / confirmed purchase orders; not average mismatch size |
| Supplier concentration | Largest supplier's spend / total supplier spend |

Source: [intelligence.py](../../mcp_server/tools/intelligence.py), `METRICS`. Returns have warehouse/store attribution limitations; margin applies current cost to historical sales, not historical booked COGS.

## 3. Detecting unusual changes

For eligible complete periods:

```
baseline = arithmetic mean of earlier complete-period values
relative change (%) = (latest − baseline) / baseline × 100
z-score = (latest − baseline) / population standard deviation of baseline values
flag = abs(relative change) >= 15% OR abs(z-score) >= 1.5
```

- At least two eligible complete periods are required: one current and one baseline. One baseline period cannot provide a useful standard deviation, but relative change can still be evaluated.
- Metric direction decides whether the change is adverse or favorable. Favorable significant changes are opportunities.
- Zero baseline needs a metric-specific activation threshold and exposure rule. Without that rule, the condition is not automatically labeled healthy.
- These are fixed detection thresholds, not p-values or calibrated probabilities.
- Priority: adverse relative changes at least 40% become critical; at least 25% high; otherwise medium. Where relative change is unavailable, z-score fallbacks are 3 and 2 respectively.
- Persistence classifies consecutive adverse movements/recovery across periods; trailing investigations exist for selected operational metrics.

Sources: [intelligence.py](../../mcp_server/tools/intelligence.py), [investigation.py](../../mcp_server/tools/investigation.py).

## 4. Current-period comparisons

- Take the latest available source timestamp; use an exclusive endpoint just after it.
- Compare the current month's elapsed interval with the same elapsed interval in earlier months.
- Exclude earlier months too short to supply that interval.
- Rates use aggregated numerators divided by aggregated denominators, not an unweighted average of outlet percentages.
- Retention uses the prior full-month cohort, checking returns through the matched current-period cutoff.
- Source freshness and insufficient comparison history remain visible.
- `return_rate` currently reports a deliberate current-period limitation because the sources have no direct per-record link compatible with this calculation path.

Source: [current_period.py](../../mcp_server/tools/current_period.py).

## 5. Explaining findings

- Contributor analysis compares segment values/rates and ranks their movements. Contribution, composition, and rate breakdowns have different meanings.
- Rate segments are skipped when both current and baseline denominator counts are below 15.
- Concentration checks use a 60% threshold where applicable.
- Evidence includes stock availability, outlet activity, loss/damage overlap, history quality, and cross-metric corroboration.
- Independence groups prevent mechanically related metrics, such as total cancellation and its stockout subset, from being counted as independent support.
- Short history is marked lower confidence; corroboration is not a controlled causal experiment.

Source: [evidence_engine.py](../../mcp_server/tools/evidence_engine.py).

## 6. Incident detection

Store/product/hour cancellation incidents use a source-anchored 14-day window:

```
hourly rate = cancellations at that hour / eligible orders at that hour
hourly z = (hourly rate − mean of hourly rates) / population SD
lift = cancellation rate with the product / cancellation rate without it
raw score = hourly z × lift × sqrt(order count)
persistence factor = min(2, 1.5 × days appeared / days observed)
incident score = raw score × persistence factor
```

- Minimum eligible count: 15. At least three hourly rate values and nonzero variation are needed; candidates need lift > 1 and positive z.
- Score bands: watchlist 5; confirmed 15; escalation 50; auto-alert band 200. These are code labels/thresholds, not a promise that an external notification is sent.
- Evidence checks use z > 2 and lift > 3 for their stronger verdicts.
- Product/cancellation association does not itself prove the product caused a cancellation.

Source: [incident_engine.py](../../mcp_server/tools/incident_engine.py).

## 7. Business impact and ranking

```
Revenue deviation = abs(current revenue − baseline revenue)
Order-volume value estimate = abs(round(current orders − baseline orders)) × current AOV
Excess failed/cancelled orders = round((current rate − baseline rate) / 100 × current denominator)
Estimated value of excess orders = abs(excess orders) × AOV
```

The monthly impact engine only prices supported metrics. A baseline shortfall is not proven recoverable loss, and overlapping findings must not be summed into total loss.

```
ranking score = severity weight × persistence factor × impact factor × history-confidence factor
severity: critical 3; high 2; medium/opportunity 1
persistence: >=3 consecutive periods 1.5; >=2 periods 1.25; otherwise 1
impact: 1.25 if an impact object exists; otherwise 1
history: 0.85 for low-confidence data quality; otherwise 1
```

The score does not multiply by rupee magnitude. Also, the implementation boosts any truthy impact object, not only objects with a quantified financial value.

Observed duration shown in Investigate is `floor((last saved observation − first saved observation) / 1 day)`. Counts come from saved observations. This is neither the true onset nor proof of continuous occurrence.

Sources: [impact_engine.py](../../mcp_server/tools/impact_engine.py), [management.py](../../backend/app/services/management.py), [InvestigationImpact.jsx](../../frontend/src/design/InvestigationImpact.jsx).

## 8. Customer timing and lifecycle

Individual timing requires at least three orders and a lifetime mean gap of at least one day. Let `g` be the mean of the latest five gaps (or available gaps) and `s` their sample standard deviation:

```
half-width = max(1 day, s if positive else 0.35 × g)
window start = last purchase + max(1 day, g − half-width)
window end = last purchase + g + half-width
```

Before the window = on track; within = due; after = overdue. These are heuristics, not calibrated prediction intervals.

- History confidence: 10+ orders is high; 3–9 moderate. It is based on count, not measured prediction accuracy.
- Lifecycle precedence: new customers first; low-history older customers dormant; unsuitable very-fast histories excluded; remaining customers use personal timing.
- Churned versus lapsing uses overdue duration relative to window half-width; the configured churn multiplier is 2.
- Win-back checks whether the latest gap was unusually long relative to prior gaps and the customer has now returned.
- Value tiers use population lifetime-revenue percentiles: high at/above p80, mid at/above p50, otherwise low.
- Primary channel is the channel with the most orders.
- Reorder-product suggestions apply gap-based timing to products bought at least three times.
- Daily repeat-order estimates distribute one order uniformly across each customer's date window: contribution `1 / window days`; revenue contribution `historical AOV / window days`.
- Due-customer summary value sums their historical AOVs. It is not probability-weighted incremental revenue.
- Profile deterioration compares recent and prior windows, order count, revenue and AOV; cadence ratio is days since purchase / historical interval.

The older `customers.py` path still uses fixed 28/60-day segmentation. It should not be confused with the personal-cadence lifecycle model.

Sources: [customer_predictions.py](../../backend/app/services/customer_predictions.py), [customer_lookup.py](../../backend/app/services/customer_lookup.py), [customers.py](../../backend/app/services/customers.py).

## 9. Inventory, purchasing and baskets

- Sales velocity = trailing-30-day units / 30; days of cover = stock / daily sales velocity.
- Low cover threshold: 3 days in stock alerts. The older company-wide quantity panel uses a separate 10-unit threshold.
- Inventory depth: no sales in the trailing 30 days → dead stock; over 90 days of cover → overstock; otherwise healthy under this classification.
- Stock valuation uses quantity × usable cost. Cost must be positive and, where list price is present, not exceed list price.
- Stock/data controls also detect negative stock, absent costs, unfinished counts, count variance, top-seller stockouts, and inactive outlets. These are state rules, not all z-score detections.
- Basket pair confidence `A→B = orders with A and B / orders with A`; lift `= P(B|A) / P(B)`. Minimum pair frequency is 15; inventory products only. Affinity is not causal sales uplift.
- Loyalty redemption = points used / points issued. Promotion price differences describe listed discounts, not realized campaign ROI.

Sources: [inventory.py](../../backend/app/services/inventory.py), [stock_alerts.py](../../backend/app/services/stock_alerts.py), [state_findings.py](../../mcp_server/tools/state_findings.py), [basket.py](../../backend/app/services/basket.py), [loyalty.py](../../backend/app/services/loyalty.py).

## 10. Warehouse-to-outlet transfer and dispatch detection

Scope for all five: Celeste outlets (store ids `1, 2, 45, 46`), incoming receipts from `Transits/%` locations, matched to their outbound note via `receipt.origin == outbound.name`.

- **Missing item / extra quantity / product damage** (one case per shortage reason): 14-day window, recurrence gate of >=3 incidents across >=2 distinct days. Each case carries a line-level trace (plan, sent, expected, received, who closed each record). Shortage lines on cancelled or not-yet-received receipts are excluded from every headline figure and reported separately.
- **Large single shortages** (one case, 30-day window): a line qualifies at >=20 units and either >=10x its own product's median line (with >=5 lines of history) or >=60 units on thin history. Priority scales with the worst single line or the batch's catalog-cost value (>=200 units or Rs 50,000 = critical; >=100 units or Rs 15,000 = high; else medium) rather than a fixed tier.
- **Dispatch sent above plan** (one case, 14-day window): a line qualifies at >=5x its planned quantity and >=50 units over -- the top ~0.3% of 6 months of outbound lines, not routine rounding. "Sent with no plan line at all" was checked over the same 6 months and never occurs, so it is not a detector, only a count.
- **Dispatcher/product pattern** (one case, 14-day window): a dispatcher's miss rate on one recurring product, compared to their own overall rate everywhere else they handle. Qualifies at >=50 units handled on the pair, >=5% rate, and >=5x the dispatcher's own rate. Raw concentration (one dispatcher accounts for most of a product's missing units) was evaluated and rejected: dispatchers are tied to fixed warehouse-to-outlet routes, so median concentration across 192 recurring products over 6 months is already 66.7%, with 41 products at 90%+ -- that reflects route assignment, not behavior.
- **Accountability panel** (not a case; company-wide context shown on the finding pages above): every done receipt move in the window, not only lines with a recorded shortage, so each person's and product's rate has their full handled volume as the denominator. Filterable by outlet, product, person, date and recurring-only; raw rows cached 5 minutes.
- These five findings are grouped into management families (`warehouse_transfer_shortages`, `warehouse_transfer_overages`, `warehouse_dispatch_overage`, `transfer_damage`) alongside the pre-existing stock/opportunity families; a family's priority is its highest-priority child, and exposure is never summed across children, since they can reference the same underlying lines.

Sources: [transfer_discrepancy.py](../../mcp_server/tools/transfer_discrepancy.py), [extreme_event.py](../../mcp_server/tools/extreme_event.py), [dispatch_overage.py](../../mcp_server/tools/dispatch_overage.py), [dispatch_pattern.py](../../mcp_server/tools/dispatch_pattern.py), [transfer_accountability.py](../../mcp_server/tools/transfer_accountability.py), [management.py](../../backend/app/services/management.py).

## 11. Business health and forward projections

Health is a rule-based score, not a learned risk probability:

- Metric domains start at 100; adverse anomalies subtract `min(40, abs(relative change))`, or 20 when relative change is unavailable.
- Inventory penalties include 5 per company-wide out-of-stock product (cap 50), 2 per low-stock product (cap 20), 10 for dead-stock value share above 5%, and 15 for overstock value share above 25%.
- Top-three customer revenue concentration above 40% subtracts 15 from its component.
- Customer health averages concentration and retention components. Overall health averages Sales, Operations, Inventory and Customers scores.

Forward Look uses the most recent three complete-month growth rates (requires four complete months). Low/mid/high projections multiply the latest value by `1 + min/mean/max growth`. It does not model seasonality or provide statistical confidence bounds.

Sources: [health.py](../../backend/app/services/health.py), [forecast.py](../../backend/app/services/forecast.py). Separate forecasting experiments are not equivalent to this production Forward Look calculation.

## 12. Cases, verification and outcomes

- Case identity is metric + domain + horizon, with an active-case uniqueness rule.
- States include open, verification pending, reopened, resolved and dismissed.
- Auto-resolution requires two consecutive fresh clean cadence units under the relevant policy. Missing/stale/unmeasured sources cannot stand in for clean measurements; manual policies remain manual.
- Recently closed recurrence matching uses a 30-day window.
- Target comparisons are explicit `>=`, `<=`, `>`, `<`, `==`; saved evidence and target/objective versions preserve what was decided at the time.
- Action outcome evaluation waits for eligible post-completion windows and distinguishes blocked, pending and inconclusive states. Overlapping actions complicate attribution.
- Outcome intelligence requires at least 20 resolved findings for its sufficient-history label. That count threshold alone does not establish causal effectiveness.

Sources: [case_engine.py](../../backend/app/services/case_engine.py), [action_outcomes.py](../../backend/app/services/action_outcomes.py), [outcome_intelligence.py](../../backend/app/services/outcome_intelligence.py).

## 13. AI and evaluation rules

The model receives measured evidence and proposes structured explanations/recommendations; database mathematics and deterministic detection rules are not learned by the model during a scan.

The evaluation graders check recommendation fields/priority vocabulary, unsupported precise numerical claims, and near-verbatim reuse of advice whose prior outcome was unchanged or worse. The number grader compares normalized numbers from text with numbers in the evidence; it targets decimals and magnitudes at least 100, not every small integer. The repetition check normalizes text and compares equality, not semantic similarity.

These are evaluation checks, not a guarantee of factual correctness or proof that every grader runs on every production response.

Sources: [recommendations.py](../../mcp_server/tools/recommendations.py), [agent.py](../../backend/app/services/agent.py), [graders.py](../../evals/graders.py).

## Important interpretation limits

1. Detection thresholds and health weights are configured business heuristics; they are not calibrated probabilities.
2. Estimated margin uses current costs; recorded exposure is not confirmed loss.
3. Correlation/concentration and before-after improvement do not establish causation.
4. Stored observation spans do not prove continuous problems.
5. Old and new services have differing scope/time/segmentation conventions. Do not infer one global contract solely from a shared constant or frontend label.
6. Missing, unavailable, zero and insufficient-history results mean different things and should remain distinct.
