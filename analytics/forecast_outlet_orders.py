"""Offline CLI: python -m analytics.forecast_outlet_orders.

Production counterpart to evaluate_orders.py --timesfm: instead of holding
back weeks for backtesting, this feeds EVERY available day of each outlet's
history into the already-gated TimesFM 2.5 model (see
docs/outlet-forecast-evaluation.md for why it was selected) and asks for the
next 7 days. Run in the isolated .venv-forecast environment, same as the
evaluator -- no ML imports here reach the main app environment.

Reuses evaluate_orders.load_timesfm() so the checkpoint, config and output
clipping are identical to the gated evaluation, not a second, independently
configured copy of the model.

Writes artifacts/forecasting/outlet_forecast.json, read by
backend/app/services/forecast.py's get_outlet_order_forecast(). Deliberately
does NOT call backend.app.db (this process has no DB driver installed --
see analytics/requirements-forecasting.txt) and does NOT itself decide
whether the result is fit to publish -- it always writes `source_age_days`
and `is_stale` (> PROTOCOL["max_source_age_days"], the same one-day gate
evaluate_orders.py's promotion_decision() uses) so the serving layer and the
frontend can refuse to call it a live prediction when the source is old,
without re-deriving that threshold a second time.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from analytics.evaluate_orders import load_timesfm
from analytics.outlet_orders import MIN_HISTORY_DAYS, PROTOCOL

HORIZON = PROTOCOL["horizon"]


def main():
    data = json.loads(Path("artifacts/forecasting/orders.json").read_text(encoding="utf-8"))
    predict, model_meta = load_timesfm()

    outlets = {}
    for outlet, series in data["series"].items():
        values = series["values"]
        if len(values) < MIN_HISTORY_DAYS:
            continue  # same eligibility bar the evaluator requires, not a looser one for "just a forecast"
        last_date = datetime.fromisoformat(series["dates"][-1]).date()
        point, bands = predict(values)
        forecast_dates = [(last_date + timedelta(days=h + 1)).isoformat() for h in range(HORIZON)]
        outlets[outlet] = {
            "history_tail": {"dates": series["dates"][-14:], "values": values[-14:]},
            "forecast_dates": forecast_dates,
            "point": point,
            "q10": [b[0] for b in bands],
            "q90": [b[1] for b in bands],
        }

    source_date = datetime.fromisoformat(data["latest_source_local"]).date()
    today = (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date()
    source_age_days = (today - source_date).days

    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "horizon_days": HORIZON,
        "source": {
            "latest_source_local": data["latest_source_local"],
            "end_exclusive": data["end_exclusive"],
            "source_age_days": source_age_days,
            "is_stale": source_age_days > PROTOCOL["max_source_age_days"],
        },
        "model": model_meta,
        "outlets": outlets,
    }
    out_path = Path("artifacts/forecasting/outlet_forecast.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({**result, "outlets": {k: "..." for k in outlets}}, indent=2))


if __name__ == "__main__":
    main()
