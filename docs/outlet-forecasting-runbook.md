# Outlet order forecasting pilot

The pilot is offline. It does not add an API route, schedule a model inside the
daily scan, or publish a Home forecast. Promotion requires passing both the
fixed evaluation protocol and the source freshness gate.

See [the measured evaluation](outlet-forecast-evaluation.md) for the current result.

## Run

From the repository root in PowerShell:

```powershell
# Existing application environment: read-only aggregate export, no app DB writes.
.\.venv\Scripts\python.exe -m scripts.export_outlet_orders

# Baselines need no ML packages.
.\.venv\Scripts\python.exe -m analytics.evaluate_orders --output artifacts/forecasting/baselines.json

# Separate environment; do not install these dependencies into the app environment.
.\.venv\Scripts\python.exe -m venv .venv-forecast
.\.venv-forecast\Scripts\python.exe -m pip install -r analytics/requirements-forecasting.txt --extra-index-url https://download.pytorch.org/whl/cpu

# Required before loading TimesFM. Use the installed skill's actual location.
.\.venv-forecast\Scripts\python.exe C:/Users/DeepalRupasinghe/.codex/skills/timesfm-forecasting/scripts/check_system.py --json
.\.venv-forecast\Scripts\python.exe -m analytics.evaluate_orders --timesfm
```

The machine must have sufficient **available** memory, not just installed RAM.
The observed evaluation setup used CPU, two threads, batch one and context 256.
Model weights download to the Hugging Face cache. Order histories stay local.
The selected checkpoint and its exact revision are recorded in the evaluation
artifact. TimesFM 2.5 is deliberately selected: upstream states that weights
through 2.5 are Apache-2.0; 3.0 weights have separate non-commercial,
non-production restrictions. [Upstream license notice](https://github.com/google-research/timesfm#license-notice-for-pretrained-weights).

`artifacts/forecasting/` is ignored by Git. `orders.json` contains only aggregate
outlet/day counts, source cutoff, and quality metadata. `evaluation.json` adds
every historical prediction, actual, origin, horizon, error breakdown, model
revision and promotion decision. Do not interpret missing artifacts as a passed
evaluation. Failed model loading or invalid numerical output stops the run.

## Data and evaluation contract

- Storefront IDs come from `CELESTE_STORE_IDS`; warehouses do not define scope.
- Confirmed `sale` orders only. Odoo UTC-naive timestamps become Asia/Colombo
  local timestamps before grouping. Forecasts describe observed orders, not
  unconstrained demand or causal uplift.
- Exclude the latest source day and today's day as potentially partial. Exclude
  the first observed day. Missing dates are unknown, not zeros; retain only the
  latest uninterrupted daily segment. Closed days and ingestion failures cannot
  be distinguished from order rows alone.
- Require 112 days: 56 initial training days, four seven-day validation windows,
  then four seven-day holdouts. Holdout origins advance weekly and may use actuals
  that had become observable before the new origin. No model sees future targets.
- Select the baseline on validation MAE. TimesFM must improve MAE by at least 5%
  on validation and holdout, avoid more than 5% holdout MAE regression at any
  outlet, and achieve 70–95% empirical coverage for its nominal 80% band.
- The selected candidate must have outlet holdout WAPE at most 25%, absolute bias
  at most 10%, all configured outlets represented, and source age at most one day.
  These are initial engineering gates fixed before the benchmark, not calibrated
  business promises. No method is automatically published by this CLI.
- Retrospective confirmed-order data may include later changes; historical
  ingestion snapshots are unavailable. A prospective shadow trial is still needed.

## Customer analytics correction

`order_history.py` supplies one scoped local-time relation for purchase cadence,
pattern counts, lifecycle/value thresholds, actual order charts, outlet shares,
and next-basket queries. This corrects partner storefront contamination and
midnight date boundaries.

Disposable customer caches use `_v2` tables. Existing tables remain intact but
are no longer read as scoped data. Old Home opportunity cards lack the new
contract marker and are hidden. The normal next daily scan rebuilds customer
snapshots; customer pages can be empty until that scan completes. No production
scan was forced by the pilot. Existing monthly projections and repeat-customer
heuristics are not replaced by the experimental outlet model.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q -m real_backend tests/test_customer_prediction_scope.py
```

The explicit database tests execute synthetic fixtures as CTEs in read-only
PostgreSQL transactions, plus a real scoped snapshot round trip into a temporary
SQLite app database. They do not insert fixtures or change anything in Odoo.

## Integration gate

When source freshness and model suitability pass, integration still needs a
scheduled forecast job outside scan locks, versioned app-store forecasts and
observed outcomes, baseline uncertainty calibration if the baseline wins, and
Home/Explore views with cutoff dates, measured errors and uncertainty. Do not
label an old-source historical forecast as a prediction for today.
