# Outlet forecast evaluation — 18 September 2026

**Result:** TimesFM 2.5 passes the predefined offline model gates and is the selected candidate. Live integration remains blocked by source freshness. No new forecast has been published to Home or Explore.

Compared with the validation-selected four-week weekday average, TimesFM reduced holdout MAE by **12.59%**, from **63.97 to 55.91 orders per outlet/day**. Holdout WAPE improved from **8.55% to 7.48%**. This measures retrospective prediction error, not revenue uplift or guaranteed future accuracy.

## Data and protocol

- Source: read-only confirmed Odoo orders for configured outlet IDs 1, 2, 45 and 46.
- Latest scoped source event: **15 September 2026, 10:56:59 Asia/Colombo**. Evaluation ran on 18 September; the source is three days old and fails the one-day freshness gate.
- Excluded the partial 15 September source day. Histories end on 14 September. Colombo 6, Attidiya and Ethul Kotte each retain 167 continuous days; Pannipitiya retains 174. Early Colombo 6 gaps are unknown and excluded, never replaced with zeros.
- Validation targets: **21 July–17 August 2026**. Untouched holdout targets: **18 August–14 September 2026**.
- Four weekly seven-day origins per split, four outlets: **112 outlet/day observations per method per split**. Later origins use only history observable before that origin.
- Baseline selection used validation MAE. The model configuration and promotion thresholds were fixed before its holdout was evaluated. No tuning followed these results.
- Forecasts target observed orders. No historic ingestion snapshots, closure calendar or lost-demand corrections were available. A prospective shadow trial is still required.

## Measured results

| Split | Method | MAE (orders/day) | WAPE | Signed bias | Nominal 80% coverage |
|---|---|---:|---:|---:|---:|
| Validation | Last week, same weekday | 48.83 | 6.95% | 0.07% | Not estimated |
| Validation | Four-week weekday average | 38.46 | 5.47% | -1.54% | Not estimated |
| Validation | TimesFM 2.5 | 36.40 | 5.18% | -1.04% | 85.71% |
| Holdout | Last week, same weekday | 70.24 | 9.39% | -2.64% | Not estimated |
| Holdout | Four-week weekday average | 63.97 | 8.55% | -5.16% | Not estimated |
| Holdout | TimesFM 2.5 | 55.91 | 7.48% | -4.05% | 76.79% |

WAPE is total absolute error divided by total observed orders. Signed bias below zero indicates underprediction. MAE averages outlet/day errors; it is not an error on the daily company total.

## Holdout by outlet

| Outlet | Baseline WAPE | TimesFM WAPE | TimesFM bias | 80% band coverage |
|---|---:|---:|---:|---:|
| Colombo 6 | 7.44% | 5.59% | -1.73% | 85.71% |
| Pannipitiya | 8.36% | 8.65% | -3.16% | 85.71% |
| Attidiya | 10.47% | 9.03% | -6.55% | 64.29% |
| Ethul Kotte | 7.75% | 7.20% | -4.39% | 71.43% |

Pannipitiya MAE is **3.37% worse** than its weekday baseline, within the predeclared 5% maximum regression. Improvements are not universal. Attidiya coverage is only **64.29%** (18 of 28 actuals inside the nominal 80% interval), despite acceptable pooled coverage of **76.79%**. Its uncertainty needs prospective monitoring and calibration; these bands must not be described as already calibrated for every outlet.

TimesFM holdout q10/q90 pinball losses are **17.10 / 13.89 orders**, with mean interval width **168.29 orders**. Full outlet/horizon breakdowns and all predictions are preserved in the local JSON artifact.

## Gate and delivery status

- Passed: at least 5% overall MAE improvement on validation (**5.34%**) and holdout (**12.59%**), no outlet MAE regression over 5%, pooled interval coverage between 70% and 95%, all four outlets represented, outlet WAPE below 25% and absolute bias below 10%.
- Failed: source freshness. A model pass is not an operational deployment approval or a claim that the data is current.
- Implemented: customer query scope and Colombo day boundaries; versioned customer caches and stale Home-card suppression; read-only daily extraction; reproducible rolling evaluation; isolated TimesFM environment; regression tests.
- Pending live integration: restore/verify daily reporting freshness, run a prospective shadow forecast, then add the scheduled job, app-store forecast/outcome history and Home/Explore views. The current CLI does not schedule or publish forecasts.
- Customer snapshot migration: legacy tables remain untouched. Scoped `_v2` snapshots are rebuilt by the normal next daily scan; affected customer views may be empty until then. The implementation test wrote only to temporary SQLite, not the real application store.

## Reproduction and validation

- Evaluation timestamp: `2026-09-18T07:25:54.238812+00:00`.
- Checkpoint: `google/timesfm-2.5-200m-pytorch`, revision `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`.
- Dataset SHA-256: `68b2186036f8f854db79875fb41246f5a61ab4ca37415e8ecc06c7ad794ff143`.
- Runtime: TimesFM package 2.0.2, PyTorch 2.8.0+cpu, eager inference, two CPU threads, batch one, maximum context 256. Thirty-two outlet/origin inferences plus scoring took **7.96 seconds**, excluding installation, weight download and model loading.
- The downloaded 925,181,104-byte checkpoint was checked against its published SHA-256. The first transfer stalled; a verified ranged download populated the standard local cache.
- Default backend suite: **281 passed** at the broad verification point. Two further evaluator tests were added afterward; the final focused customer-cache/evaluator run passed **10 tests**. Four additional explicit read-only PostgreSQL checks passed, including a real-data snapshot round trip into temporary SQLite.
- Ruff passes for all newly added Python files. Existing deprecation warnings remain in the backend.
- Local detailed artifacts: `artifacts/forecasting/orders.json`, `baselines.json`, `evaluation.json` (Git-ignored). Dependency snapshot: `analytics/requirements-forecasting.txt`.
- See [the runbook](outlet-forecasting-runbook.md) for commands, fixed thresholds and operational requirements.
