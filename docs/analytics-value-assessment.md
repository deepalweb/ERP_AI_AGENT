# Analytics value assessment

Reviewed 18 September 2026 using the TimesFM forecasting skill, current application source, and upstream model documentation. This is a code-based opportunity assessment, not a measured forecast benchmark. No reporting records were queried and no models were downloaded or loaded.

Follow-up implementation: the scoped customer corrections and offline evaluation
pipeline are described in [the forecasting runbook](outlet-forecasting-runbook.md).
Measured results are in [the outlet evaluation report](outlet-forecast-evaluation.md).
The paragraph above describes the original assessment, before the subsequent
read-only data extraction and benchmark.

## Recommendation

Start with **seven-day daily order forecasts for each configured Celeste outlet**, evaluated offline before exposing predictions to managers. Use this to support workload planning. Inventory replenishment is a promising next application, but requires additional stock, allocation, and lead-time evidence.

## Existing capabilities and gaps

| Area | Current implementation | Opportunity |
|---|---|---|
| Revenue/orders | `backend/app/services/forecast.py` extrapolates the last three complete monthly growth rates. Its range is explicitly not a statistical prediction interval. | Compare genuine daily forecasts with simple seasonal baselines; report historical errors and uncertainty. |
| Customer reorder timing | `customer_predictions.py` uses individual purchase gaps and their variability. Aggregate predictions cover qualifying repeat customers, not all orders. | Preserve the customer-specific tool; evaluate predicted windows against subsequent orders. TimesFM should not automatically replace this method. |
| Outlet order forecasts | The existing service distributes aggregate repeat-customer forecasts using trailing 30-day outlet shares. | Forecast each outlet's own daily order history, allowing different outlet trajectories. |
| Inventory | `stock_alerts.py` uses trailing 30-day average units/day and a three-day cover threshold. | Test demand forecasts for high-volume products, then compare forecast consumption with usable stock and confirmed receipts. |
| Action outcomes | Persistent cases and action measurements already exist. | Surface forecast-driven risks through these workflows only after the forecast's data and quality gates pass. Never treat a forecast as a verified outcome. |

The runtime scope is four explicit outlet IDs `(1, 2, 45, 46)`. Some historical comments and CONTEXT.md still mention five. Use the runtime contract and ADR-0001; never substitute warehouse identity for order scope. Customer prediction scope needs auditing before comparisons: the module acknowledges a gap between aggregate prediction scope and scoped outlet shares. The outlet-share query also takes its latest date from all confirmed orders rather than scoped orders.

## First experiment

1. Extract daily confirmed-order counts for the configured outlets using read-only reporting access. Establish one business timezone, complete-day cutoff, and consistent cancellation/order definition. Profile actual available history; do not rely on old comments claiming six months.
2. Distinguish confirmed zero-trade days from missing ingestion and outlet closures. Keep source freshness and coverage in the dataset manifest.
3. Evaluate seven-day forecasts over multiple chronological rolling cutoffs. At each cutoff, use only information available then. Keep a final untouched holdout; do not tune on it.
4. Compare last-week-same-day and rolling weekday-average baselines against TimesFM 2.5. Do not assume the larger model wins. Reconstruct any existing-method benchmark from cutoff data rather than today's cached predictions.
5. Report MAE, aggregate WAPE where actual totals are nonzero, signed bias, and error by outlet/horizon. Evaluate quantile loss and empirical interval coverage. q10–q90 is an **80% nominal interval**, not 90%; calibration on Celeste data is not guaranteed.
6. Select a method based on stable holdout performance, runtime, and operational usefulness. Agree numerical promotion thresholds before examining the holdout. Retain a baseline fallback for inadequate history, failed inference, or stale data.
7. If accepted, run scheduled inference outside web requests and scan write locks. Persist forecast creation time, source cutoff, target dates, outlet, model/version, point estimate, quantiles, and later actuals in the application store. Do not write to Odoo.

## Product delivery

- Home: a compact “Next seven days” summary only when data is fresh and the method passed evaluation; link to outlet detail.
- Explore: daily expected orders by outlet, uncertainty bands, actual-versus-predicted history, and measured accuracy.
- Later inventory pilot: a small set of sufficiently dense product/outlet series. Observed sales can understate demand during stockouts; label forecasts as sales forecasts unless lost-demand adjustment is supported.
- Replenishment quantities require usable stock/reservations, confirmed inbound stock, supplier lead times, case-pack/minimum order quantities, and shelf-life constraints where applicable. Forecasting alone cannot supply these facts.
- Do not present attributed revenue recovery or causal campaign uplift from forecasting correlations. Use separate experiments or defensible causal analysis for those claims.

## Runtime and model choice

The packaged preflight ran successfully under Python 3.12. It reported 15.9 GB total RAM but only **2.6 GB available**, 68.4 GB free disk, and missing `torch`/`timesfm` packages. GPU capability is unknown because PyTorch is absent. Its overall CPU pass is not proof of comfortable runtime capacity; recheck available memory and start with a small batch in an isolated environment before loading weights.

The installed skill targets TimesFM 2.5. The current upstream repository says source and weights through 2.5 are Apache-2.0, while 3.0 pretrained weights have a separate non-commercial, non-production restriction. Evaluate 2.5 for this commercial application; do not silently upgrade checkpoints. Source: [TimesFM license notice](https://github.com/google-research/timesfm#license-notice-for-pretrained-weights).

## Suggested sequence

1. Correct analytical scope/date contracts and establish the daily-series dataset.
2. Produce the baseline-versus-TimesFM evaluation report.
3. Integrate only the winning forecast with accuracy monitoring.
4. Pilot inventory risk and quantify changes in stock availability, excess stock, and manager follow-through.

No claim of accuracy improvement, cost savings, or revenue uplift has been established yet.
