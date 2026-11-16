"""Offline CLI: python -m analytics.forecast_series --input <source.json> --output <forecast.json> --metric <label>.

Generic production counterpart to forecast_outlet_orders.py: that script
stays as the special-cased, already-proven entrypoint for outlet order
counts (untouched here, 2026-11-07); this is the same production-forecast
logic -- feed every available day into the already-gated TimesFM 2.5 model
and ask for the next 7 days -- made reusable for additional count-like
daily series that share the exact same shape (outlet revenue,
delivery-partner order volume, ...) instead of forking a near-identical
script per metric. Run in the isolated .venv-forecast environment, same as
forecast_outlet_orders.py -- no ML imports reach the main app environment.

Reuses evaluate_orders.load_timesfm() so the checkpoint, config and output
clipping are identical across every series this produces, never a second,
independently configured copy of the model.

Does NOT itself decide whether the result is fit to publish -- always writes
source_age_days/is_stale (PROTOCOL's one-day gate) so the serving layer and
frontend can refuse to call it a live prediction when the source is old.
"""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from analytics.evaluate_orders import load_timesfm
from analytics.outlet_orders import MIN_HISTORY_DAYS, PROTOCOL

HORIZON = PROTOCOL["horizon"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metric", required=True, help="Short label for what this series measures, e.g. 'revenue'")
    parser.add_argument("--horizon", type=int, default=HORIZON, help="Points ahead to forecast (default 7, the daily protocol's horizon)")
    parser.add_argument(
        "--step-hours", type=float, default=24.0,
        help="Hours between consecutive points in the series (24 for daily, 1 for hourly) -- "
        "only affects how forecast timestamps are formatted, not the model itself",
    )
    parser.add_argument(
        "--min-points", type=int, default=MIN_HISTORY_DAYS,
        help="Minimum history length to forecast a group at all (default: the daily protocol's own eligibility bar -- "
        "pass a different floor for a series with a different natural scale, e.g. hourly)",
    )
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    predict, model_meta = load_timesfm(horizon=args.horizon)

    groups = {}
    for key, series in data["series"].items():
        values = series["values"]
        if len(values) < args.min_points:
            continue
        last_ts = datetime.fromisoformat(series["dates"][-1])
        point, bands = predict(values)
        forecast_dates = []
        for h in range(args.horizon):
            ts = last_ts + timedelta(hours=args.step_hours * (h + 1))
            forecast_dates.append(ts.date().isoformat() if args.step_hours >= 24 else ts.isoformat())
        groups[key] = {
            # Any extra fields the source series carries (e.g. a per-SKU
            # export's "product"/"sku" labels) pass through unchanged --
            # this script never needs them, but the reader on the other
            # end usually does.
            **{k: v for k, v in series.items() if k not in ("dates", "values")},
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
        "metric": args.metric,
        "generated_at": datetime.now(UTC).isoformat(),
        "horizon_days": args.horizon if args.step_hours >= 24 else None,
        "horizon_points": args.horizon,
        "step_hours": args.step_hours,
        "source": {
            "latest_source_local": data["latest_source_local"],
            "end_exclusive": data["end_exclusive"],
            "source_age_days": source_age_days,
            "is_stale": source_age_days > PROTOCOL["max_source_age_days"],
        },
        "model": model_meta,
        "groups": groups,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({**result, "groups": {k: "..." for k in groups}}, indent=2))


if __name__ == "__main__":
    main()
